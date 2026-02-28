# Mainfreight 3PL Integration — Scope Document

**MML Consumer Products Ltd → Mainfreight MIMS**
**Version**: Draft 1.0
**Date**: February 2026

---

## 0. Claude Code Implementation Notes

**This document is the specification for building the Mainfreight integration module in Odoo 19.**

### What's ready now
- Odoo model schemas: exported as PDFs in this session (check against the model checklist spreadsheet — anything missing, export from Odoo Settings → Technical → Models)
- MF Transport API: already integrated — reuse the existing auth/client code for Tracking API calls
- Product barcodes and CBM: populated in Odoo
- Address data: already MF-compliant

### What's blocked on dev (do not build yet — stub these)
- **Ti-Hi fields**: `x_mf_carton_per_layer`, `x_mf_layer_per_pallet` — dev is creating fields and populating data. Product sync XML builder should include these fields but handle `None`/empty gracefully (skip the field or use MF defaults)
- **L×W×H per pack level**: CBM exists but individual dimensions don't yet. Same approach — stub in the XML builder, skip if empty

### UX — Phase 2, not now
The dashboard, order pipeline views, exception queues, and inventory discrepancy screens are a separate phase. For now, build only the minimum:
- **Settings/config form**: SFTP credentials, MF Customer ID, warehouse codes, cron toggles
- **Integration log list view**: basic tree view of `mf.integration.log` so we can debug
- **Status fields on existing views**: add `x_mf_status` to `stock.picking` tree/form views (inline, not a new menu)
- **No custom dashboard, no kanban pipeline, no stats widgets** — these come later once the data flows are stable and we know what ops actually needs to see

### Build order
Follow the phased rollout in Section 9. Start with:
1. Custom Odoo module scaffold (`mf_integration`)
2. Configuration model (MF customer ID, warehouse codes, SFTP creds, cron settings)
3. SFTP client (paramiko — push/poll/archive)
4. XML builder/parser engine
5. Product sync flow
6. Sales order push flow
7. Acknowledgement + Confirmation parsers
8. Then work through remaining flows

### Architecture decisions
- One Odoo module: `mf_integration`
- XML format for all EDI (not CSV) — MF recommends XML
- SFTP via paramiko (not ftplib)
- Cron-driven, not event-driven (simpler, easier to debug, Phase 1)
- Dedicated `mf.integration.log` model for all activity logging
- All MF-specific fields prefixed `x_mf_` on existing models

---

## 1. Executive Summary

Full bidirectional integration between Odoo 19 and Mainfreight MIMS covering product master sync, outbound order fulfilment, inbound receipt management, inventory reconciliation, and shipment tracking.

**Transfer method**: XML via SFTP (primary), REST API (tracking + stock queries)
**Target**: All 5 brands, ~400 SKUs, all retail and direct-to-consumer orders

---

## 2. Integration Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ODOO 19 (Self-hosted)                        │
│                                                                     │
│  EDI In ──→ sale.order ──→ stock.picking ──→ Outbound XML ──────┐  │
│  (Retailers)                                                     │  │
│                                                                  ▼  │
│  ┌──────────────────────────┐      ┌─────────────────────────┐      │
│  │   MF Integration Module  │◄────►│  SFTP Client (Cron)     │──────┤
│  │   • XML Builder/Parser   │      │  • Push orders/products  │      │
│  │   • Status State Machine │      │  • Poll confirmations    │      │
│  │   • Field Mapping Engine │      │  • Poll inventory        │      │
│  └──────────────────────────┘      └─────────────────────────┘      │
│                                                                     │
│  ┌──────────────────────────┐      ┌─────────────────────────┐      │
│  │   Tracking Poller        │◄────►│  MF REST API Client     │──────┤
│  │   • Connote → events     │      │  • Tracking API         │      │
│  │   • POD link capture     │      │  • Stock On Hand API    │      │
│  └──────────────────────────┘      └─────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
                            ▲            │
                            │            ▼
                    ┌───────────────────────────┐
                    │   Mainfreight MIMS / SFTP  │
                    │   xftp.mainfreight.com     │
                    │                             │
                    │   Warehousing API:          │
                    │   developer.mainfreight.com │
                    └───────────────────────────┘
```

---

## 3. Order Lifecycle & Status Mapping

### 3.1 What Mainfreight Actually Gives You

This is the critical part. MF's EDI provides **two** warehouse states, then the Tracking API picks up for transport. There is a gap in the middle.

```
Odoo sends SO ──→ MF Acknowledges ──→ [WMS Black Box] ──→ MF Confirms Dispatch ──→ Tracking API
     │                  │                     │                      │                      │
  "Sent to 3PL"   "Received by MF"    No visibility         "Dispatched"          "In Transit"
                   Order Status:       (picking/packing                            "Delivered"
                   "ENTERED"           is internal)                                "POD"
```

**Status chain available from MF**:

| # | Status | Source | MF Document | Trigger |
|---|---|---|---|---|
| 1 | Sent to 3PL | Odoo internal | (outbound XML pushed) | Cron pushes SO XML to SFTP |
| 2 | Received by MF | MF → Odoo | SO Acknowledgement (ACKH/ACKL) | MF WMS accepts order, auto-generates ACK |
| 3 | Dispatched | MF → Odoo | SO Confirmation (SCH/SCL) | MF finalises pick, generates connote |
| 4 | In Transit | MF → Odoo | Tracking API (References with Events) | Carrier scan events |
| 5 | Out for Delivery | MF → Odoo | Tracking API (References with Events) | Carrier scan events |
| 6 | Delivered / POD | MF → Odoo | Tracking API (POD link + Signed By) | Final delivery scan |

### 3.2 The Gap: Picked / Packed / Waiting Uplift

MF's standard EDI does **not** expose intermediate WMS states (picking started, packing complete, staged for carrier). The SO Confirmation fires when the order is "finalised" in MIMS — which typically means picked and packed, sometimes before physical dispatch.

**Options to close the gap**:

| Option | Approach | Effort | Recommendation |
|---|---|---|---|
| A | Accept the gap — 2 warehouse states + 3 transport states = 5 total | None | Good enough for most operations |
| B | Ask MF to configure confirmation at physical dispatch only (not finalise) | MF config request | Do this — ensures "Dispatched" means actually on a truck |
| C | Use Order Reconciliation polling to check status of open orders | Medium | Can detect if order moves from ENTERED → PICKING → CONFIRM SENT |
| D | Ask MF about WMS webhook/event streaming options | Discovery | Worth asking — some 3PLs offer this now |

**Recommendation**: Start with Option A + B. Request MF hold the SO Confirmation until physical dispatch. Then add C (reconciliation polling) in Phase 2 to catch edge cases and stuck orders.

### 3.3 Proposed Odoo Status Model

New selection field on `stock.picking` (or dedicated `mf.order.status` model):

| Status Value | Label | Set By |
|---|---|---|
| `draft` | Draft | Odoo default |
| `mf_queued` | Queued for 3PL | Cron picks up order |
| `mf_sent` | Sent to 3PL | SFTP push confirmed |
| `mf_received` | Received by MF | SO Acknowledgement parsed |
| `mf_dispatched` | Dispatched | SO Confirmation parsed |
| `mf_in_transit` | In Transit | Tracking API event |
| `mf_out_for_delivery` | Out for Delivery | Tracking API event |
| `mf_delivered` | Delivered | Tracking API POD |
| `mf_exception` | Exception | Error/short-ship/held |

---

## 4. Integration Flows — Detailed Spec

### 4.1 Product Master Sync (Odoo → MF)

**Direction**: Odoo → MF
**Format**: XML via SFTP
**Frequency**: On product create/update (event-driven) or nightly batch
**MF Document**: Product Specification (77 fields)

| Data Point | Odoo Source | MF Field | Notes |
|---|---|---|---|
| Product Code | `product.product.default_code` | Field 1 (40 char) | Must be unique in MF |
| Description 1 | `product.template.name` | Field 2 (40 char) | Truncate if needed |
| Description 2 | `product.template.description_sale` | Field 3 (40 char) | Optional |
| Unit Weight | `product.product.weight` | Field 4 | kg, 4dp |
| Unit Volume | `product.product.volume` | Field 5 | m³, 4dp |
| Grade1 (lot tracking) | `product.template.tracking != 'none'` | Field 7 | Y/N — enable if lot tracked |
| Expiry Date tracking | Custom field or `use_expiration_date` | Field 10 | Y/N |
| Pack Size 1 (default) | `product.packaging[0].qty` | Field 22 | Mandatory |
| Pack Description 1 | `product.packaging[0].name` | Field 23 | e.g., EACH, UNIT |
| Pack Barcode 1 | `product.packaging[0].barcode` | Field 24 | Mandatory — ✅ Have barcodes |
| Pack Dimensions 1 | `product.packaging[0].length/width/height` | Fields 26-28 | **TODO: Dev** — have CBM, need L×W×H per pack level |
| Pack Size 2-4 | Additional packaging records | Fields 29-49 | INNER, OUTER, PALLET |
| Carton Per Layer (Ti) | Custom field on template | Field 20 | **TODO: Dev** |
| Layer Per Pallet (Hi) | Custom field on template | Field 21 | **TODO: Dev** |
| Shipping Conditions | Custom field | Field 53 | AMBIENT / CHILLED / FROZEN |

**TODO (Dev)**: Ti-Hi (fields 20-21) and individual product dimensions (L×W×H) are mandatory in MF but not yet in Odoo. Dev is adding custom fields and populating data. CBM exists for all products but needs decomposing into L×W×H per pack level. Integration code should handle these fields but they will be empty initially — product sync must gracefully skip or default these until populated.

**Custom fields required on `product.template`**:
- `x_mf_carton_per_layer` (integer) — **TODO: Dev populating**
- `x_mf_layer_per_pallet` (integer) — **TODO: Dev populating**
- `x_mf_shipping_condition` (selection: ambient/chilled/frozen)
- `x_mf_product_synced` (boolean — sync tracking)
- `x_mf_last_sync_date` (datetime)

### 4.2 Kitset / BoM Sync (Odoo → MF)

**Direction**: Odoo → MF
**Format**: CSV via SFTP
**Frequency**: On BoM create/update
**MF Document**: Kitsets (10 fields)

Maps `mrp.bom` → Kitset Code, `mrp.bom.line` → Component lines. Straightforward mapping.

### 4.3 Sales Order Push (Odoo → MF)

**Direction**: Odoo → MF
**Format**: XML via SFTP
**Frequency**: Twice daily (configurable cron — e.g., 10:00 and 15:00)
**MF Document**: Sales Order (SOH header + SOL lines, 68 + 30 fields)

**Trigger criteria** — push orders that are:
- Confirmed (`sale.order.state = 'sale'`)
- Delivery picking created and in `confirmed` or `assigned` state
- Warehouse matches MF warehouse(s)
- Not already sent (`x_mf_sent = False`)

**Batching approach**: One XML file per order (MF requirement — unique filenames). Cron collects all qualifying orders, generates XML per order, pushes batch to SFTP.

**Cadence discussion — twice daily vs real-time**:

| Cadence | Pro | Con |
|---|---|---|
| Twice daily (10am, 3pm) | Simple, predictable, easy to monitor/debug | Max 4-5hr delay from order to MF |
| Hourly | Good balance | More SFTP connections, more to monitor |
| Near real-time (every 15 min) | Fastest fulfilment | Highest complexity, SFTP rate concerns |
| Event-driven (on confirm) | Zero delay | Needs robust retry/queue, can overwhelm MF |

**Recommendation**: Start at twice daily. Move to hourly once stable. Near real-time only if retailer SLAs demand same-day dispatch for afternoon orders. MF won't pick faster just because you send faster.

**Key field mappings**:

| MF SOH Field | Odoo Source |
|---|---|
| OrderID (unique) | `sale.order.name` |
| ClientRef | `sale.order.client_order_ref` |
| CustRef | Partner's PO reference |
| DebtorID | `res.partner.ref` (consignee code) |
| DebtorName | `res.partner.name` |
| Delivery Address 1-8 | `res.partner` delivery address fields |
| Invoice Address 1-8 | `res.partner` invoice address fields |
| WarehouseNo | `stock.warehouse.code` → MF warehouse ID mapping |
| DateRequired | `sale.order.commitment_date` or picking scheduled date |
| CarrierDescription | `delivery.carrier.name` |
| CustomerID | MF customer ID (config parameter) |

| MF SOL Field | Odoo Source |
|---|---|
| OrderID | Same as header |
| LineNo | Line sequence number |
| ProductCode | `product.product.default_code` |
| Quantity | `sale.order.line.product_uom_qty` |
| PackTypeOrdered | Derive from UoM → MF pack description |
| UnitPrice | `sale.order.line.price_unit` (if priced packing slip needed) |

### 4.4 Sales Order Acknowledgement (MF → Odoo)

**Direction**: MF → Odoo
**Format**: CSV via SFTP (MF only sends ACK as CSV)
**Frequency**: Poll SFTP every 30 minutes
**MF Document**: SO Acknowledgement (ACKH + ACKL)

**Action on receipt**:
1. Match `Client Order Number` → `sale.order.name`
2. Update picking status → `mf_received`
3. Capture MF Order Status field (value: "ENTERED")
4. Log acknowledgement timestamp
5. Flag any qty mismatches between ACK and original order

### 4.5 Sales Order Confirmation / Dispatch (MF → Odoo)

**Direction**: MF → Odoo
**Format**: XML or CSV via SFTP
**Frequency**: Poll SFTP every 30 minutes
**MF Document**: SO Confirmation (SCH + SCL + SCP)

This is the most critical inbound document. It means the order is picked, packed, and (if configured correctly) on a truck.

**Action on receipt**:
1. Match `Reference` → `sale.order.name`
2. Update picking status → `mf_dispatched`
3. Capture connote number (`CarrierRef` / `ConsignmentNo`) → `stock.picking.carrier_tracking_ref`
4. Capture carrier name → verify against expected carrier
5. Process line-level confirmation:
   - Match `ProductCode` → `product.product.default_code`
   - Compare `ShippedQty` vs `OrderedQty` — flag short ships
   - Capture lot/grade/expiry from grade fields
6. Capture pack-level detail from SCP section (SSCC, weights)
7. Validate and confirm the Odoo delivery picking (`stock.picking.button_validate()` or equivalent)
8. Trigger tracking poller for this connote

**Short ship handling**: If MF ships less than ordered, create backorder in Odoo automatically. Log discrepancy for review.

### 4.6 Purchase Order / Inward Push (Odoo → MF)

**Direction**: Odoo → MF
**Format**: XML via SFTP
**Frequency**: On PO confirmation (event-driven or twice daily batch)
**MF Document**: Inward Order (INWH header + INWL lines)

**Trigger**: `purchase.order.state = 'purchase'` and warehouse = MF warehouse.

| MF INWH Field | Odoo Source |
|---|---|
| InwardsReference (unique) | `purchase.order.name` |
| BookingDate | `purchase.order.date_planned` |
| SupplierName | `purchase.order.partner_id.name` |
| TotalUnits | Sum of line qtys |
| InwardsType | G (general goods) — default |
| WarehouseCode | MF warehouse ID |

| MF INWL Field | Odoo Source |
|---|---|
| ProductCode | `product.product.default_code` |
| Quantity | `purchase.order.line.product_qty` |
| PackType | Derive from UoM |

### 4.7 Inward Acknowledgement (MF → Odoo)

**Direction**: MF → Odoo
**Format**: CSV via SFTP
**Frequency**: Poll every 30 minutes
**Action**: Update PO receipt picking → `mf_received`. Log timestamp.

### 4.8 Inward Confirmation / Receipt (MF → Odoo)

**Direction**: MF → Odoo
**Format**: XML or CSV via SFTP
**Frequency**: Poll every 30 minutes

**Action on receipt**:
1. Match to purchase order
2. Process received quantities per line
3. Capture lot/grade/expiry assignments from MF
4. Validate receipt picking in Odoo
5. Handle over/under receipt (create backorder or flag)
6. Update stock.quant via picking validation

### 4.9 Inventory Report / Stock On Hand (MF → Odoo)

**Direction**: MF → Odoo
**Format**: CSV via SFTP (31 fields per line) OR REST API (Stock On Hand endpoint)
**Frequency**: Daily (overnight batch from MF) + on-demand via API

**Fields captured**:

| MF Field | Odoo Target |
|---|---|
| ProductCode | `stock.quant.product_id` (via default_code) |
| Stock On Hand | Total qty |
| Quantity Available | Available for fulfilment |
| Quantity on Hold | Held stock |
| Quantity Committed | Committed to open orders |
| Quantity Damaged | Damaged stock |
| Grade1/2/3 | Lot attributes |
| Expiry/Packing Date | Lot dates |

**Reconciliation approach**: Compare MF SOH report against Odoo `stock.quant` for MF warehouse location. Flag discrepancies above threshold (e.g., >2% or >5 units).

**Do NOT blindly overwrite Odoo quants** — use a reconciliation queue that flags mismatches for review. Auto-correct only for small discrepancies.

### 4.10 Inventory Adjustments (MF → Odoo)

**Direction**: MF → Odoo (can also be Odoo → MF)
**Format**: CSV or XML via SFTP
**Frequency**: Poll every 30 minutes

Captures when MF team makes physical adjustments (damage, write-off, corrections). Creates `stock.scrap` or inventory adjustment in Odoo.

### 4.11 Inventory Status Changes (Bidirectional)

**Direction**: Both
**Format**: CSV or XML via SFTP
**Frequency**: Poll every 30 minutes (inbound), event-driven (outbound)

Handles stock status transitions (e.g., Hold → Available, Available → Damaged). Maps to Odoo stock location transfers between status-specific locations.

### 4.12 Shipment Tracking (MF REST API → Odoo)

**Direction**: MF → Odoo
**Format**: REST API (JSON)
**Frequency**: Poll every 2 hours for active shipments, or webhook if MF supports it
**Note**: MF Transport API is already integrated in Odoo — reuse existing auth client and connection logic. The Tracking API (References / References with Events) uses the same developer.mainfreight.com platform.

**API endpoints**:
- `References` — current status
- `References with Events` — full event chain

**Implementation**:
1. After SO Confirmation, register connote for tracking
2. Poll tracking API on schedule
3. Parse events → map to Odoo status:
   - Picked up / In transit → `mf_in_transit`
   - Out for delivery → `mf_out_for_delivery`
   - Delivered → `mf_delivered`
4. Capture POD link and Signed By → store on picking
5. Stop polling once delivered

### 4.13 Order Reconciliation (Bidirectional)

**Direction**: Odoo → MF request, MF → Odoo response
**Format**: CSV via SFTP
**Frequency**: Weekly batch or on-demand

Sends list of open order references to MF, receives back current status of each. Use to catch:
- Orders stuck in MF WMS
- Confirmations lost in transit
- Status sync drift

### 4.14 Inventory Reconciliation (Bidirectional)

**Direction**: Odoo → MF request, MF → Odoo response
**Format**: CSV or XML via SFTP
**Frequency**: Weekly or monthly

Full stock count reconciliation between Odoo and MF.

---

## 5. SFTP Folder Structure

```
/outbound/                  (Odoo → MF)
  /products/                Product master XML files
  /orders/                  Sales order XML files
  /inwards/                 Inward order XML files
  /inventory_status/        Inventory status change requests
  /reconciliation/          Reconciliation requests

/inbound/                   (MF → Odoo)
  /ack_orders/              SO Acknowledgements
  /confirm_orders/          SO Confirmations (dispatch)
  /ack_inwards/             Inward Acknowledgements
  /confirm_inwards/         Inward Confirmations (receipt)
  /inventory_reports/       Daily stock on hand
  /inventory_adjustments/   Ad-hoc adjustments
  /inventory_status/        Status change confirmations
  /reconciliation/          Reconciliation responses

/processed/                 (Moved after processing — retain 90 days)
/error/                     (Failed to parse — requires manual review)
```

---

## 6. Cron Schedule

| Cron Job | Frequency | Window | Description |
|---|---|---|---|
| Push Sales Orders | Twice daily | 10:00, 15:00 NZST | Batch eligible SOs → XML → SFTP |
| Push Inward Orders | Twice daily | 10:00, 15:00 NZST | Batch eligible POs → XML → SFTP |
| Push Product Updates | Nightly | 22:00 NZST | Changed products → XML → SFTP |
| Poll Acknowledgements | Every 30 min | 06:00–22:00 | Check SFTP for ACK files |
| Poll Confirmations | Every 30 min | 06:00–22:00 | Check SFTP for dispatch/receipt confirms |
| Poll Inventory Reports | Daily | 06:00 NZST | Process overnight SOH report |
| Poll Adjustments | Every 30 min | 06:00–22:00 | Check for ad-hoc adjustments |
| Track Shipments | Every 2 hours | 06:00–22:00 | REST API poll for active connotes |
| Order Reconciliation | Weekly | Sunday 02:00 | Reconcile open orders |
| Inventory Reconciliation | Monthly | 1st of month 02:00 | Full stock recon |

---

## 7. Error Handling & Monitoring

### 7.1 Error Categories

| Category | Example | Response |
|---|---|---|
| SFTP connection failure | Network/auth issue | Retry 3x with backoff, then alert |
| XML validation failure | Missing mandatory field | Log error, skip file, alert |
| Order not found | ACK references unknown SO | Log to exception queue |
| Product not found | Confirmation references unknown SKU | Log, don't process line, alert |
| Quantity mismatch | Short ship or over-receipt | Create backorder, flag for review |
| Address validation | MF rejects suburb/city | ✅ Already compliant — MF Transport API integrated, addresses validated |
| Duplicate file | Same filename already processed | Skip, log warning |

### 7.2 Alerting

- Email/Slack notification for any file in `/error/`
- Daily digest of processing stats (files sent/received/errors)
- Alert if no acknowledgement received within 4 hours of push
- Alert if tracking shows no movement after 48 hours

### 7.3 Logging

Dedicated `mf.integration.log` model:
- Direction (in/out)
- Document type
- Reference number
- Filename
- Status (success/error)
- Error detail
- Processing timestamp
- Raw file stored as attachment

---

## 8. Custom Fields Required

### On `product.template`:
- `x_mf_carton_per_layer` — integer, Ti value
- `x_mf_layer_per_pallet` — integer, Hi value
- `x_mf_shipping_condition` — selection (ambient/chilled/frozen)
- `x_mf_synced` — boolean
- `x_mf_last_sync` — datetime

### On `stock.picking`:
- `x_mf_status` — selection (the status chain from section 3.3)
- `x_mf_connote` — char (redundant with carrier_tracking_ref but explicit)
- `x_mf_pick_id` — char (MF internal pick ID from confirmation)
- `x_mf_pod_url` — char (POD link from tracking API)
- `x_mf_signed_by` — char
- `x_mf_dispatched_date` — datetime
- `x_mf_delivered_date` — datetime

### On `stock.warehouse`:
- `x_mf_warehouse_code` — char (MF warehouse ID, e.g., "99")
- `x_mf_customer_id` — char (MF customer ID, e.g., "123456")
- `x_mf_enabled` — boolean (is this a MF-managed warehouse?)

### On `sale.order`:
- `x_mf_sent` — boolean
- `x_mf_sent_date` — datetime
- `x_mf_filename` — char (XML filename sent)

### On `purchase.order`:
- `x_mf_sent` — boolean
- `x_mf_sent_date` — datetime

---

## 9. Phased Rollout

### Phase 1: Foundation (Weeks 1-3)
- SFTP connection + auth
- Product master sync (all 400 SKUs)
- MF confirms products loaded correctly
- Custom fields deployed
- Integration logging model

### Phase 2: Outbound Orders (Weeks 4-6)
- Sales order XML generation
- SO push cron (twice daily)
- SO Acknowledgement parsing
- SO Confirmation parsing (dispatch)
- Short ship handling
- Test with 10-20 orders, then go live

### Phase 3: Inbound Orders (Weeks 7-8)
- Inward order XML generation
- PO push to MF
- Inward Acknowledgement parsing
- Inward Confirmation parsing (receipt)
- Lot/grade/expiry capture on receipt

### Phase 4: Inventory (Weeks 9-10)
- Daily inventory report ingestion
- Reconciliation queue + alerting
- Inventory adjustment processing
- Inventory status change handling

### Phase 5: Tracking & Polish (Weeks 11-12)
- Tracking API integration
- POD capture
- Order reconciliation (weekly)
- Full inventory reconciliation (monthly)
- Monitoring dashboard
- Documentation

---

## 10. Open Questions for Mainfreight

1. Can SO Confirmation be held until physical dispatch (not WMS finalise)?
2. What intermediate WMS statuses are available via API or custom EDI?
3. Is webhook/push notification available for tracking events?
4. What is our MF Customer ID and Warehouse Code(s)?
5. SFTP credentials and folder structure — do they match our proposed layout or do they have a standard?
6. XML schema files — can they provide XSD for validation?
7. ~~Address validation reference data~~ — ✅ Already compliant via MF Transport API integration
8. What's their SLA for processing EDI files after SFTP drop?
9. Do they support pack-level detail (SCP) for our account?
10. API key for Warehousing API — request (Tracking API already integrated)

---

## 11. Dependencies & Prerequisites

- [ ] MF account setup with EDI enabled
- [ ] SFTP credentials issued
- [ ] MF Customer ID confirmed
- [ ] MF Warehouse Code(s) confirmed
- [ ] API key issued (Warehousing API — Tracking API ✅ already integrated)
- [x] ~~MF suburb/city/postcode reference data~~ — addresses already compliant via Transport API
- [ ] All 400 SKUs have `default_code` populated in Odoo
- [x] Product barcodes populated for all SKUs
- [x] Product CBM (volume) populated for all SKUs
- [ ] **TODO (Dev)**: Product L×W×H dimensions per pack level (have CBM, need to decompose)
- [ ] **TODO (Dev)**: Ti-Hi data collected and populated for all SKUs
- [ ] **TODO (Dev)**: Custom fields deployed (`x_mf_carton_per_layer`, `x_mf_layer_per_pallet`, etc.)
- [ ] Shipping conditions assigned to all SKUs
- [ ] Test SFTP folder structure agreed with MF
- [ ] Test order set prepared (10-20 representative orders)
