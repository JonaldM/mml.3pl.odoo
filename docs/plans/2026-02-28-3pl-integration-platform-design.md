# 3PL Integration Platform — Design Document

**Date:** 2026-02-28
**Status:** Approved
**Scope:** Odoo 15 (current), upgrade to Odoo 17+ deferred — diff on upgrade

---

## Overview

A forwarder-agnostic 3PL integration platform built as two Odoo addons, with Mainfreight Warehousing as the first implementation. Designed to support a central Odoo instance managing multiple warehouses internationally across multiple 3PL providers.

Integration spec reference: `docs/Mainfreight Warehousing Integration Specification.pdf` (v B05, Apr 2024)

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │         Central Odoo             │
                    │  SO/PO/Products/stock.quant      │
                    └────────────┬────────────────────-┘
                                 │ Odoo events
                    ┌────────────▼─────────────────────┐
                    │      Outbound Message Queue       │
                    │  (state machine + retry + log)    │
                    └────────────┬─────────────────────┘
                                 │
              ┌──────────────────┼───────────────────┐
              │                  │                   │
    ┌─────────▼──────┐  ┌────────▼───────┐  ┌───────▼────────┐
    │ Document Builder│  │Document Builder│  │Document Builder│
    │  (Mainfreight) │  │  (Future 3PL) │  │  (Future 3PL) │
    └─────────┬──────┘  └────────┬───────┘  └───────┬────────┘
              │                  │                   │
    ┌─────────▼──────┐  ┌────────▼───────┐  ┌───────▼────────┐
    │Transport Adapter│  │Transport Adapter│  │Transport Adapter│
    │REST/SFTP/HTTP  │  │    (SFTP)      │  │    (AS2)       │
    └─────────┬──────┘  └────────┬───────┘  └───────┬────────┘
              │                  │                   │
    ┌─────────▼──────┐  ┌────────▼───────┐  ┌───────▼────────┐
    │  MF NZ/AU/US   │  │   3PL (EU)    │  │   3PL (US)    │
    └─────────┬──────┘  └────────┬───────┘  └───────┬────────┘
              └──────────────────▼───────────────────┘
                    ┌────────────┴─────────────────────┐
                    │      Inbound Message Queue        │
                    │  (confirmations, SOH, adjustments)│
                    └────────────┬─────────────────────┘
                                 │
                    ┌────────────▼─────────────────────┐
                    │         Central Odoo             │
                    │  stock.quant / stock.picking     │
                    │  updated from 3PL confirmations  │
                    └──────────────────────────────────┘
```

---

## Module Structure

### `stock_3pl_core` — Forwarder-agnostic platform

```
stock_3pl_core/
  models/
    connector.py             # 3pl.connector: maps Odoo warehouse → 3PL config
    message.py               # 3pl.message: central message queue
    transport_base.py        # Abstract transport class
    document_base.py         # Abstract document builder/parser
  transport/
    rest_api.py              # RestTransport
    sftp.py                  # SftpTransport
    http_post.py             # HttpPostTransport
  wizard/
    manual_sync_wizard.py    # Manual trigger for testing/recovery
    inbound_simulator.py     # Paste raw payload, dry-run apply
  data/
    cron_outbound.xml        # Scheduled: process outbound queue
    cron_inbound.xml         # Scheduled: poll inbound (SFTP/HTTP)
```

### `stock_3pl_mainfreight` — Mainfreight implementation

```
stock_3pl_mainfreight/
  models/
    connector_mf.py          # Extends 3pl.connector with MF-specific fields
  document/
    product_spec.py          # product.product → MF Product Specification CSV
    sales_order.py           # sale.order → SOH/SOL XML
    inward_order.py          # purchase.order → INWH/INWL XML  [INACTIVE]
    so_confirmation.py       # Parse SCH/SCL → stock.picking update
    inventory_report.py      # Parse MF SOH CSV → stock.quant sync
    inventory_adjustment.py  # Parse MF adjustment → stock.move
  transport/
    mainfreight_rest.py      # MF Warehousing REST API v1.1
    mainfreight_sftp.py      # MF SFTP (xftp.mainfreight.com)
  data/
    connector_mf_demo.xml    # Demo connector pointing to MF test environment
```

---

## Core Models

### `3pl.connector` — One record per warehouse + 3PL combination

| Field | Type | Purpose |
|-------|------|---------|
| `warehouse_id` | Many2one → stock.warehouse | Odoo warehouse this connector serves |
| `forwarder` | Selection | `mainfreight` / future values |
| `transport` | Selection | `rest_api` / `sftp` / `http_post` |
| `environment` | Selection | `test` / `production` — routes to correct endpoint |
| `customer_id` | Char | MF CustomerID (mandatory, assigned by MF) |
| `warehouse_code` | Char | MF WarehouseID (e.g. 99) |
| `region` | Char | NZ / AU / US — for international routing |
| `api_url` | Char | REST API base URL |
| `sftp_host` | Char | SFTP hostname |
| `api_secret` | Char | API key / secret (stored in Odoo, consider Varlock) |
| `notify_user_id` | Many2one → res.users | Alert recipient on dead-letter events |
| `last_soh_applied_at` | Datetime | Guards against stale inventory report application |
| `active` | Boolean | Enable/disable per connector |

### `3pl.message` — Central message queue

| Field | Type | Purpose |
|-------|------|---------|
| `connector_id` | Many2one → 3pl.connector | Which warehouse/3PL |
| `direction` | Selection | `outbound` / `inbound` |
| `document_type` | Selection | `product_spec`, `sales_order`, `inward_order`, `so_confirmation`, `inventory_report`, `inventory_adjustment` |
| `action` | Selection | `create` / `update` / `delete` — maps to MF API action flags |
| `state` | Selection | State machine (see below) |
| `payload_xml` | Text | Raw XML payload |
| `payload_json` | Text | Raw JSON payload |
| `payload_csv` | Text | Raw CSV payload |
| `ref_model` | Char | Odoo source model (e.g. `sale.order`) |
| `ref_id` | Integer | Odoo source record ID |
| `forwarder_ref` | Char | 3PL's own reference for the document |
| `idempotency_key` | Char | Hash of (connector, doc_type, odoo_ref) — unique constraint on outbound |
| `source_hash` | Char | SHA-256 of raw inbound payload — deduplication on inbound |
| `report_date` | Date | For inventory reports — stale-report guard |
| `retry_count` | Integer | Auto-increments on failure, max 3 |
| `last_error` | Text | Last exception or error response |
| `sent_at` | Datetime | Timestamp of successful send |
| `acked_at` | Datetime | Timestamp of acknowledgement from 3PL |

---

## State Machines

**Outbound:**
```
draft → queued → sending → sent → acknowledged
               ↘ failed (retry_count < 3) → queued
               ↘ failed (retry_count = 3) → dead
```

**Inbound:**
```
received → processing → applied → done
         ↘ failed (retry_count < 3) → processing
         ↘ failed (retry_count = 3) → dead
```

Dead messages surface in an Odoo menu view with last error, raw payload, and a "Requeue" button.
Dead-letter transitions fire an Odoo activity to `connector.notify_user_id`.

---

## Retry Policy

| Attempt | Delay |
|---------|-------|
| 1 | Immediate |
| 2 | +5 minutes |
| 3 | +30 minutes |
| → dead | Manual intervention required |

---

## Idempotency & Deduplication

| Scenario | Solution |
|----------|---------|
| Outbound duplicate send (timeout + retry) | `idempotency_key` unique constraint; 409 from MF API treated as success |
| Inbound duplicate delivery (SFTP double-pickup) | `source_hash` unique constraint; SFTP files deleted immediately after pickup |
| Stale inventory report | `report_date` checked against `connector.last_soh_applied_at`; older reports silently rejected with log |
| Odoo event re-fires on same record | Check for existing non-dead message on `(ref_model, ref_id, document_type)` before queuing; auto-upgrade to Update action if appropriate |

---

## Document Data Flows

### Product Specification (Outbound — CSV)
Trigger: product published/updated, or manual sync wizard

| Odoo Field | MF Field | Mandatory |
|-----------|---------|-----------|
| `product.product.default_code` | Product Code | Yes |
| `product.product.name` | Product Description 1 | Yes |
| `product.product.weight` | Unit Weight | Yes |
| `product.product.volume` | Unit Volume | Yes |
| `product.product.standard_price` | Unit Price | No |
| Grade attrs from `stock.production.lot` | Grade1 / Grade2 / Grade3 (Y/N) | Yes |
| `product.packaging` (up to 4) | Pack Size / Desc / Barcode / Dims | Yes |
| `mrp.bom` + `mrp.bom.line` | Kitset (separate CSV) | If applicable |
| `stock.warehouse.code` via connector | Warehouse ID | No |
| `uom.uom.name` | Alt Unit Description | No |

### Sales Order (Outbound — XML) + Dispatch Confirmation (Inbound)
Trigger: `sale.order.state = 'sale'`
Idempotency key: `sale.order.name`

**Outbound (SOH header → SOL lines):**

| Odoo Field | MF Field | Mandatory |
|-----------|---------|-----------|
| `sale.order.name` | ClientOrderNumber | Yes |
| `res.partner.ref` (delivery) | ConsigneeCode | Yes |
| `res.partner` address fields | Delivery address (7 fields) | Yes |
| `res.partner` (invoice_address) | Invoice address fields | No |
| `connector.warehouse_code` | WarehouseCode | Yes |
| `delivery.carrier.name` | CarrierRef | No |
| `sale.order.commitment_date` | DateRequired | No |
| `sale.order.note` | DeliveryInstructions | No |
| `sale.order.line.product_id.default_code` | ProductCode | Yes |
| `sale.order.line.product_uom_qty` | Units | Yes |
| `sale.order.line.price_unit` | UnitPrice | No |

**Inbound (SCH/SCL → Odoo):**

| MF Field | Odoo Field |
|---------|-----------|
| `SCH.ConsignmentNo` | `stock.picking.carrier_tracking_ref` |
| `SCH.CarrierName` | `stock.picking.carrier_id` (matched by name) |
| `SCH.FinalisedDate` | `stock.picking.date_done` |
| `SCH.ETADate` | `stock.picking.scheduled_date` |
| SCL line quantities | `stock.move.line.qty_done` reconciliation |

### Inventory Report (Inbound — CSV)
Trigger: scheduled SFTP poll or REST API `/StockOnHand`
Stale guard: `report_date > connector.last_soh_applied_at`

| MF Field | Odoo Field |
|---------|-----------|
| `Product` | `stock.quant.product_id` (via `default_code`) |
| `WarehouseID` | `stock.quant.location_id` (via connector) |
| `StockOnHand` | `stock.quant.quantity` |
| `QuantityOnHold` | quant at Hold location |
| `QuantityDamaged` | quant at Damage location |
| `Grade1/2/3` | `stock.production.lot` matched/created |
| `ExpiryDate` | `lot.expiration_date` |
| `PackingDate` | `lot.use_date` |

### Inward Order (Outbound — XML) [BUILT, INACTIVE]
Trigger: `purchase.order` confirmed
Implemented via `FreightForwarderMixin` — field mappings registered per forwarder.
Designed to support non-MF freight forwarders with minimal additional code.

| Odoo Field | MF Field | Mandatory |
|-----------|---------|-----------|
| `purchase.order.name` | InwardsReference | Yes |
| `purchase.order.date_planned` | BookingDate | Yes |
| `res.partner.name` (supplier) | SupplierName | No |
| `product.supplierinfo.product_code` | VendorNo | No |
| PO lines | INWL lines | Yes |

---

## Error Handling

| Error Type | Behaviour |
|-----------|-----------|
| Network timeout / 5xx | Retry with backoff → dead after 3 attempts |
| 409 Conflict (already exists at MF) | Treat as success, mark `sent` |
| 422 Validation (bad payload) | Mark `dead` immediately — no retry |
| SFTP connection failure | Retry → dead, fire activity to notify user |
| Inbound parse failure | Mark `dead`, preserve raw payload |
| Stale SOH report | Silent reject, log on connector |
| Duplicate detected (idempotency/hash) | Block at queue entry, log |

---

## Transport Methods

Both built; selected per `3pl.connector.transport`:

| Transport | MF Endpoints |
|-----------|-------------|
| REST API v1.1 | `https://developer.mainfreight.com` (prod) / `https://securetest.mainfreight.com` (test) |
| SFTP | `xftp.mainfreight.com` port 22 |
| HTTP POST | `https://secure.mainfreight.co.nz/crossfire/submit.aspx?TransportName={UniqueID}` |

Format: XML preferred (per spec recommendation); CSV for product spec and inventory report (only format MF supports for those doc types).

---

## Testing Strategy

**Unit tests (per document builder):**
- Fixture Odoo records → assert correct XML/CSV output
- Field truncation at MF max lengths
- Mandatory field validation before send

**Integration tests (per transport):**
- Mocked HTTP/SFTP responses
- Retry logic and 409 handling
- Idempotency: queue same message twice, assert only one created
- Stale report rejection

**MF sandbox:**
- `connector.environment = 'test'` routes to MF test endpoints
- Test SFTP provided by MF

**Manual tooling in Odoo:**
- Wizard on `3pl.connector`: send test product spec, verify MF response
- Inbound simulator wizard: paste raw MF XML/CSV, dry-run apply without committing

---

## Active / Inactive at Launch

| Module | Status at Launch |
|--------|----------------|
| Product Specification sync | Active |
| Sales Order dispatch + confirmation | Active |
| Inventory Report SOH sync | Active |
| Inventory Adjustment sync | Active |
| Inward Order notification | Built, Inactive |

---

## Open Items

- [ ] Confirm MF CustomerID and WarehouseID(s) for NZ/AU/US with Mainfreight IT
- [ ] Confirm transport method preference (REST API vs SFTP) with Mainfreight IT
- [ ] Confirm MF sandbox credentials for development testing
- [ ] Determine if `delivery_mainfreight` (existing shipment carrier module) shares any credentials with warehousing API — may be able to reuse `mainfreight_customer_id` from `delivery.carrier`
- [ ] Upgrade diff: review model API changes between Odoo 15 and target version when upgrade commences
