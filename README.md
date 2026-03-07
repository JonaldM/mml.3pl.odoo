# Mainfreight 3PL Integration for Odoo 19

A bidirectional integration layer between Mainfreight warehousing and Odoo 19 ERP, synchronising orders, products, stock confirmations, and inventory in both directions.

## Overview

Mainfreight manages the physical warehouse — receiving stock, picking and packing orders, and dispatching freight. Odoo manages the commercial side — products, sales orders, purchase orders, and inventory records. Without integration, these two systems operate in silos: warehouse staff must manually relay order information and inventory updates are reconciled by hand.

This integration closes that gap. When a sale order is confirmed in Odoo, the order is automatically transmitted to Mainfreight in their XML format. When Mainfreight confirms dispatch, a confirmation is received back into Odoo and the picking record is updated. Inventory reports from Mainfreight are parsed and applied to `stock.quant` records to keep stock levels in sync.

The codebase is structured as two Odoo addons. `stock_3pl_core` is a forwarder-agnostic platform layer providing the message queue, transport abstraction, and connector model — it contains no Mainfreight-specific logic. `stock_3pl_mainfreight` is the Mainfreight implementation: document builders, inbound parsers, the routing engine, and all `x_mf_*` custom fields. This separation makes it possible to add a second 3PL provider by writing a new addon against the core platform without modifying existing code.

## Module Structure

```
addons/
├── stock_3pl_core/              # Platform layer (forwarder-agnostic)
│   ├── models/
│   │   ├── connector.py         # 3pl.connector — warehouse/transport config + Fernet credential helpers
│   │   ├── message.py           # 3pl.message — queue, state machine, retry
│   │   ├── transport_base.py    # AbstractTransport base class (send, poll, get_tracking_status)
│   │   └── document_base.py     # AbstractDocument + WarehousePartnerMixin
│   ├── transport/
│   │   ├── rest_api.py          # RestTransport (Bearer auth via _get_auth_secret hook)
│   │   ├── sftp.py              # SftpTransport (requires paramiko; strict host key when sftp_host_key set)
│   │   └── http_post.py         # HttpPostTransport
│   ├── utils/
│   │   └── credential_store.py  # Fernet symmetric encryption for connector secrets
│   ├── views/                   # Connector form, message list, menus
│   └── data/cron.xml            # Outbound queue cron (5 min), inbound poll cron (15 min)
└── stock_3pl_mainfreight/       # Mainfreight + Freightways implementation
    ├── models/
    │   ├── connector_mf.py      # MF API credentials (mf_*_secret fields, encrypted)
    │   ├── connector_freightways.py  # Freightways/Castle Parcels credentials (fw_api_key)
    │   ├── warehouse_mf.py      # x_mf_warehouse_code, x_mf_customer_id, lat/lng
    │   ├── picking_mf.py        # x_mf_status (10-state lifecycle), tracking fields
    │   ├── sale_order_mf.py     # x_mf_sent, x_mf_filename, x_mf_split
    │   ├── sale_order_hook.py   # action_confirm → enqueue 3pl.message
    │   ├── product_hook.py      # write() on sync fields → enqueue product_spec
    │   ├── route_engine.py      # mf.route.engine — haversine distance + optional MF SOH API cross-check
    │   ├── split_engine.py      # mf.split.engine — applies routing to stock.picking
    │   ├── push_cron.py         # mf.push.cron — routes orders then fires outbound queue
    │   ├── tracking_cron.py     # mf.tracking.cron — polls MF/Freightways tracking APIs (30 min)
    │   └── inbound_cron.py      # mf.inbound.cron — dispatches SOH/ACKH/ACKL files, processes received message queue, reconciles stale orders (60 min)
    ├── document/
    │   ├── product_spec.py      # CSV builder (outbound)
    │   ├── sales_order.py       # XML builder (outbound, action=CREATE|UPDATE, build_delete_ref)
    │   ├── inward_order.py      # XML builder (outbound, action=CREATE|UPDATE, build_delete_ref)
    │   ├── so_confirmation.py   # XML parser (inbound, dual-schema: SCH/SCL + webhook-style)
    │   ├── so_acknowledgement.py # CSV parser (inbound, ACKH/ACKL)
    │   └── inventory_report.py  # CSV parser → stock.quant upsert (inbound)
    ├── transport/
    │   ├── mainfreight_rest.py  # MainfreightRestTransport (warehousing + tracking APIs)
    │   └── freightways_rest.py  # FreightwaysRestTransport (Castle Parcels tracking API)
    ├── controllers/
    │   └── webhook.py           # Dormant MF webhook stub (3 POST routes, X-MF-Secret auth)
    └── utils/
        └── haversine.py         # Pure-Python great-circle distance
```

### stock_3pl_core

The platform layer. Knows nothing about Mainfreight-specific document formats or field names.

| Model | Description |
|-------|-------------|
| `3pl.connector` | Maps a warehouse to a 3PL provider with transport credentials and configuration |
| `3pl.message` | Outbound/inbound message queue with retry logic and dead-letter handling |
| `TransportBase` | Abstract base class for transport adapters (REST, SFTP, HTTP POST) |
| `AbstractDocument` | Abstract base class for document builders and parsers |

### stock_3pl_mainfreight

The Mainfreight implementation. All Mainfreight-specific logic lives here, including every `x_mf_*` field.

| Model | Description |
|-------|-------------|
| `mf.route.engine` | AbstractModel; selects warehouses using haversine distance + optional MF SOH API cross-check |
| `mf.split.engine` | AbstractModel; applies routing assignments to `stock.picking` records |
| `mf.push.cron` | AbstractModel; pre-processes routing then fires the outbound queue |
| `mf.tracking.cron` | AbstractModel; polls MF and Freightways tracking APIs; updates picking status fields |
| `mf.inbound.cron` | AbstractModel; dispatches SOH/ACKH/ACKL CSV files to the correct handler; processes `received` message queue records (SO Confirmations, etc.); reconciles stale `mf_sent` orders |

## Key Concepts

### Connector

`3pl.connector` is the central configuration record. Each connector links a single Odoo `stock.warehouse` to a 3PL provider (identified by the `warehouse_partner` selection field, e.g. `mainfreight`). It holds transport credentials, the MF Customer ID, the MF Warehouse Code, and alerting preferences.

A connector also aggregates the message queue: the `message_ids` one2many lists every `3pl.message` sent through this connector, and `get_transport()` returns the correct transport adapter based on the `transport` selection field (`rest_api`, `sftp`, or `http_post`).

### Message Queue

`3pl.message` is the core queue record. Each row represents one document transmitted to or received from a 3PL. Key design points:

- **Direction**: `outbound` (Odoo → MF) or `inbound` (MF → Odoo).
- **State machine**: `draft` → `queued` → `sending` → `sent` → `acknowledged` (outbound); `received` → `processing` → `applied` → `done` (inbound). Failed messages transition to `dead`.
- **Retry**: `action_fail()` increments `retry_count`. After `MAX_RETRIES` (3) attempts the message is dead-lettered and a Chatter activity is scheduled for the configured notify user.
- **Idempotency**: outbound messages deduplicate on `(connector_id, idempotency_key)`; inbound messages deduplicate on `(connector_id, source_hash)` (SHA-256 of raw payload).
- **Concurrency guard**: `_process_outbound_queue()` re-reads message state before acting to handle concurrent cron invocations.
- **Dead-letter recovery**: messages orphaned in `sending` state (from a crashed cron run) are picked up on the next cycle.

### Routing Engine

`mf.route.engine.route_order(order)` implements the V3 multi-warehouse dispatch algorithm:

1. Retrieve all `stock.warehouse` records where `x_mf_enabled = True`.
2. Compute haversine great-circle distance from each warehouse's `(x_mf_latitude, x_mf_longitude)` to the customer's delivery address `(partner_latitude, partner_longitude)`.
3. Sort warehouses by ascending distance (closest first).
4. **Single-line orders**: attempt complete fulfilment at the nearest warehouse; fall through to greedy split if no single warehouse can cover the quantity.
5. **Multi-line orders**: greedy assignment — for each warehouse in distance order, assign as many lines as available stock allows; continue until all lines are assigned.
6. Fall back to the first enabled warehouse if the partner has no geocoordinates.

When `x_mf_use_api_soh = True` is set on the connector, `_check_stock()` also calls the MF SOH API and uses the MF-reported `QuantityAvailable` when it differs from the Odoo quant, logging a drift warning. This is optional and off by default.

`mf.split.engine.apply_routing(order, assignments)` takes the assignment list and creates or updates `stock.picking` records accordingly, setting `x_mf_routed_by` and triggering cross-border detection.

### Cross-Border Detection

When the warehouse country differs from the delivery address country:
- `x_mf_cross_border` is set to `True` on the `stock.picking`.
- `x_mf_status` is set to `mf_held_review`.
- The picking is held until manually released via `action_approve_cross_border()`.

### Tracking

`mf.tracking.cron._run_mf_tracking()` runs every 30 minutes. It finds all `stock.picking` records in a non-terminal tracking state (`mf_sent`, `mf_received`, `mf_dispatched`, `mf_in_transit`, `mf_out_for_delivery`) that have a connote number set. For each picking it resolves the connector by warehouse, calls `connector.get_transport().get_tracking_status(connote)`, and writes back any updated status, POD URL, signed-by name, and delivery timestamp.

`get_transport()` dispatches to the correct transport subclass based on `connector.warehouse_partner`:
- `mainfreight` → `MainfreightRestTransport` — calls the MF Tracking API (`trackingapi.mainfreight.com`) with `mf_tracking_secret`
- `freightways` → `FreightwaysRestTransport` — calls the Freightways/Castle Parcels Tracking API (`api.freightways.co.nz`) with `fw_api_key` via `X-API-Key` header

Terminal statuses (`mf_delivered`, `mf_exception`) are never overwritten by the tracking cron.

### Inbound Polling and Order Reconciliation

`mf.inbound.cron._run_mf_inbound()` runs every 60 minutes and performs three tasks:

1. **CSV file dispatch** (`_poll_inventory_reports`): polls each active MF connector via its transport (`poll()`), detects SFTP `(filename, content)` tuples vs REST raw strings, and routes each file to the correct handler:
   - Files with an `ACKH_` or `ACKL_` filename prefix, or whose CSV header contains `ClientOrderNumber`, are dispatched to `SOAcknowledgementDocument.apply_csv()` — these update the associated `stock.picking` to `mf_received`.
   - All other CSV payloads are dispatched to `InventoryReportDocument.apply_csv()` which upserts `stock.quant` records and writes `mf.soh.discrepancy` records where drift exceeds the configured tolerance.
   - Files larger than 50 MB are skipped with a warning.

2. **Message queue processing** (`_process_inbound_messages`): scans `3pl.message` records in `received` state (created by the core `_poll_inbound` cron from XML deliveries) and dispatches each to the correct document handler by `document_type` (`so_confirmation` → `SOConfirmationDocument`, `so_acknowledgement` → `SOAcknowledgementDocument`, `inventory_report` → `InventoryReportDocument`). Success transitions the message to `applied`; unhandled exceptions dead-letter the message via `_dead_letter()` so operators are notified.

3. **Stale order reconciliation** (`_reconcile_sent_orders`): finds `stock.picking` records that have been in `mf_sent` status for longer than the configured threshold (default 48 hours, configurable via `ir.config_parameter` key `stock_3pl_mainfreight.reconcile_hours`) and have never received a connote. These are flagged as `mf_exception` for manual review.

### Document Types

| MF Document | Direction | Odoo Models |
|-------------|-----------|-------------|
| Product Specification | Outbound | `product.product`, `product.template`, `product.packaging` |
| SOH header / SOL lines | Outbound | `sale.order` / `sale.order.line` |
| INWH header / INWL lines | Outbound | `purchase.order` / `purchase.order.line` |
| SO Confirmation | Inbound | `stock.picking`, `stock.move`, `stock.move.line` |
| Inward Confirmation | Inbound | `stock.picking`, `stock.move`, `stock.move.line` |
| SO Acknowledgement (ACKH/ACKL) | Inbound | `sale.order` (status update) |
| Inventory Report | Inbound | `stock.quant` (upsert) |

### Critical Field Mappings

| Odoo Field | MF Field |
|------------|----------|
| `product.product.default_code` | Product Code |
| `sale.order.name` | Client Order Number |
| `purchase.order.name` | Inwards Reference |
| `purchase.order.date_planned` | Booking Date |
| `stock.picking.carrier_tracking_ref` | Tracking Reference |
| `stock.warehouse.code` | WarehouseID |
| `res.partner.ref` | Consignee Code |
| `3pl.connector.customer_id` | Customer ID (field 68) |

## Configuration

### Step 1 — Install

```bash
python odoo-bin -i stock_3pl_core,stock_3pl_mainfreight -d <database> --stop-after-init
```

`stock_3pl_mainfreight` depends on `stock_3pl_core`; install both together.

`stock_3pl_core` requires `paramiko` for SFTP transport:

```bash
pip install paramiko
```

### Step 2 — Enable a Warehouse for Routing

Go to **3PL Operations → Configuration → Connectors**, then navigate to **Inventory → Warehouses → [warehouse] → Mainfreight Routing tab** for routing settings:

- Set `x_mf_enabled = True` to include this warehouse in routing decisions.
- Enter `x_mf_latitude` and `x_mf_longitude` (decimal degrees) so the routing engine can compute distances.
- Enter `x_mf_warehouse_code` — the warehouse code assigned by Mainfreight (e.g. `99`).
- Enter `x_mf_customer_id` — the Customer ID assigned by Mainfreight.

### Step 3 — Create a Connector

Go to **3PL Operations → Configuration → Connectors → New**:

| Field | Description |
|-------|-------------|
| Name | Human-readable label for this connector |
| Warehouse | The Odoo warehouse this connector serves |
| Warehouse Partner | Select `Mainfreight` |
| Transport | `REST API`, `SFTP`, or `HTTP POST` |
| Environment | `Test` or `Production` |
| Region | Optional region code (e.g. `NZ`, `AU`) for international routing |
| Customer ID | MF-assigned Customer ID |
| Warehouse Code | MF-assigned warehouse code |
| Notify User | Odoo user to alert when a message is dead-lettered |

**For Mainfreight REST API transport**, fill:
- Warehousing API Secret (`mf_warehousing_secret`) — used for `/Order` and `/Inward` endpoints
- Tracking API Secret (`mf_tracking_secret`) — used for the MF Tracking API
- Label, Rating secrets as required for additional MF API surfaces
- **MF Region** (`mf_region`) — warehousing API `?region=` query parameter. Default `ANZ` (covers NZ and AU). Confirm exact value with Mainfreight before go-live; other known values: `EU`, `AMERICAS`.

**For Freightways / Castle Parcels REST API transport**, fill:
- Freightways API Key (`fw_api_key`) — sent as `X-API-Key` header
- Freightways Account Number

**For SFTP transport**, fill:
- SFTP Host, Port (default 22), Username, Password (stored masked)
- Inbound Path (default `/in`), Outbound Path (default `/out`)
- **SFTP Host Key** (optional but recommended for production): paste the server's public key in `known_hosts` format (output of `ssh-keyscan <host>`). When set, paramiko uses `RejectPolicy` and refuses connections from unexpected hosts. When blank, new host keys are accepted with a logged warning.

**For HTTP POST transport**, fill:
- HTTP POST URL
- Transport Name (UniqueID)

**SOH API cross-check** (optional): enable `Use MF SOH API for Routing` on the connector to cross-check Odoo quant stock against the live MF SOH API during routing decisions.

### Step 4 — Verify Cron Jobs

Go to **Technical → Scheduled Actions** and confirm these are active:

| Cron Job | Model Method | Default Schedule |
|----------|--------------|------------------|
| 3PL: Process Outbound Queue | `3pl.message._process_outbound_queue()` | Every 5 minutes |
| 3PL: Poll Inbound Messages | `3pl.message._poll_inbound()` | Every 15 minutes |
| MF: Poll Tracking Status | `mf.tracking.cron._run_mf_tracking()` | Every 30 minutes |
| MF: Poll Inbound Reports | `mf.inbound.cron._run_mf_inbound()` | Every 60 minutes |

The MF push cron (`mf.push.cron._run_mf_push()`) calls `_route_pending_orders()` before delegating to `_process_outbound_queue()`, so routing happens automatically on each push cycle.

The inbound cron also runs `_reconcile_sent_orders()` on each cycle to flag pickings stuck in `mf_sent` without a connote — configure the stale threshold with system parameter `stock_3pl_mainfreight.reconcile_hours` (default: 48).

## MF-Specific Fields

### stock.warehouse

| Field | Type | Description |
|-------|------|-------------|
| `x_mf_enabled` | Boolean | Include this warehouse in MF routing decisions |
| `x_mf_warehouse_code` | Char | MF warehouse code (e.g. `99`) |
| `x_mf_customer_id` | Char | MF Customer ID for this warehouse |
| `x_mf_latitude` | Float | Warehouse latitude for haversine routing |
| `x_mf_longitude` | Float | Warehouse longitude for haversine routing |

### stock.picking

| Field | Type | Description |
|-------|------|-------------|
| `x_mf_status` | Selection | Picking lifecycle (see states below) |
| `x_mf_connote` | Char | MF consignment note number |
| `x_mf_pick_id` | Char | MF internal pick ID |
| `x_mf_pod_url` | Char | Proof-of-delivery document URL |
| `x_mf_signed_by` | Char | Name of person who signed for delivery |
| `x_mf_dispatched_date` | Datetime | Dispatch timestamp from MF |
| `x_mf_delivered_date` | Datetime | Delivery timestamp from MF |
| `x_mf_routed_by` | Selection | `manual`, `auto_closest`, or `auto_split` |
| `x_mf_cross_border` | Boolean | Flagged for manual approval (warehouse country ≠ delivery country) |

**x_mf_status lifecycle:**

```
draft → mf_queued → mf_sent → mf_received → mf_dispatched
     → mf_in_transit → mf_out_for_delivery → mf_delivered
```

Cross-border holds insert `mf_held_review` before `mf_queued`. Failed transmissions move to `mf_failed`.

### sale.order

| Field | Type | Description |
|-------|------|-------------|
| `x_mf_sent` | Boolean | Order has been transmitted to MF |
| `x_mf_sent_date` | Datetime | Timestamp of first transmission |
| `x_mf_filename` | Char | Filename used for SFTP transmission |
| `x_mf_split` | Boolean | Order was split across multiple warehouses by the routing engine |

## Cron Jobs

| Cron Name | Method | Default Interval | Purpose |
|-----------|--------|-----------------|---------|
| 3PL: Process Outbound Queue | `3pl.message._process_outbound_queue()` | 5 minutes | Picks up queued outbound messages and sends via the connector's transport |
| 3PL: Poll Inbound Messages | `3pl.message._poll_inbound()` | 15 minutes | Polls all active connectors for inbound files; deduplicates and stores new messages |

Routing pre-processing (`mf.push.cron._route_pending_orders()`) runs inside the outbound cron cycle before messages are dispatched. It finds all `stock.picking` records where `x_mf_routed_by` is not set, groups them by sale order, and runs the routing engine for each unrouted order.

## Running Tests

The test suite has two categories:

**Pure-Python structural tests** — run without an Odoo instance; verify module structure, field definitions, document builders, CSV/XML parsing, routing logic, and haversine calculations.

```bash
python -m pytest addons/ -m "not odoo_integration" -v
```

**Odoo integration tests** — inherit from `odoo.tests.TransactionCase`; require `odoo-bin --test-enable`.

```bash
python odoo-bin -u stock_3pl_core,stock_3pl_mainfreight \
  --test-enable --stop-after-init -d testdb \
  --test-tags=routing,mf_inventory
```

The `conftest.py` at the repo root automatically marks any test class that imports from `odoo.tests` with the `odoo_integration` pytest marker, so the `-m "not odoo_integration"` filter works without decorating individual files.

Sprint 1 delivered 44 pure-Python tests. Sprint 2 extended to 100 tests adding haversine, route engine, split engine, cross-border detection, and push cron coverage. Sprint 3 brought the total to 228 tests covering tracking cron, inbound cron, SOH cross-check, credential encryption, SFTP host key verification, and the Freightways transport adapter. The inbound processing fixes extended the suite to 312 tests. The API gap sprint added dual-schema parser tests, dual tracking map tests, REST CRUD method tests, webhook secret validation tests, and delete-ref tests. Production-readiness hardening (SFTP `RejectPolicy`, inventory adjustment via `action_apply_inventory`, views cleanup) brings the total to **363 pure-Python tests**. 90 Odoo integration tests require `odoo-bin` and are tagged `odoo_integration`.

## Development

### Adding a New 3PL Provider

1. Create a new addon named `stock_3pl_<provider>`.
2. Add `('provider_key', 'Provider Name')` to `WAREHOUSE_PARTNER_SELECTION` in `addons/stock_3pl_core/models/connector.py`.
3. Implement document builders by inheriting `AbstractDocument` from `stock_3pl_core`.
4. Implement a transport adapter inheriting `TransportBase` if the provider requires a custom protocol.
5. Declare a dependency on `stock_3pl_core` in the new addon's `__manifest__.py`.

No changes to `stock_3pl_core` or `stock_3pl_mainfreight` are required.

### Architecture Notes

- `mf.route.engine` and `mf.split.engine` are `AbstractModel` instances — they have no database table and require no ACL entries.
- All MF-specific fields use the `x_mf_` prefix on standard Odoo models. They are defined in `stock_3pl_mainfreight`, not in core.
- The `warehouse_partner` field on `3pl.connector` (and `3pl.message`) identifies the 3PL provider. This is distinct from a freight forwarder — a freight forwarder manages the supplier-to-warehouse leg; Mainfreight here is the warehousing and last-mile partner.
- Ti-Hi fields (`x_mf_carton_per_layer`, `x_mf_layer_per_pallet`) and per-pack-level dimensions are stubbed in the XML builder: they are included when populated and skipped silently when `None`. This avoids blocking transmission while data is being collected.
- The `document_type` field on `3pl.message` is a `fields.Selection`, not a `fields.Char`. Extend `DOCUMENT_TYPE` in `message.py` when adding new document types.
- `action_fail` boundary: `retry_count + 1 >= MAX_RETRIES` (MAX_RETRIES = 3). On the third failure the message is dead-lettered and a Chatter activity is scheduled.
- SQL uniqueness constraints are scoped per `(connector_id, field)`, not globally. Messages without a key or hash are exempt from deduplication.

### Implementing a New Document Type

1. Add a `(key, label)` tuple to `DOCUMENT_TYPE` in `addons/stock_3pl_core/models/message.py`.
2. Create a builder/parser class in `addons/stock_3pl_mainfreight/document/` inheriting `AbstractDocument`.
3. Wire the trigger: either add a hook method on the relevant Odoo model (`sale_order_hook.py` is the pattern for outbound) or update `_detect_inbound_type()` in `message.py` for inbound.

## Security Notes

**Credential encryption at rest:** All API secrets (`api_secret`, `sftp_password`, `mf_*_secret`, `fw_api_key`) are encrypted with Fernet symmetric encryption before being written to the database. The master key is auto-generated on first use and stored in `ir.config_parameter` under `stock_3pl_core.credential_key`. Transport adapters read credentials via `connector.get_credential(field)` which decrypts on the fly. Legacy plaintext credentials are passed through with a logged warning prompting re-save. If decryption fails (e.g. after a key rotation), an empty string is returned and the error is logged — the ciphertext is never propagated to HTTP headers or external systems. **Note:** this protects against unprivileged SQL reads but does not protect against full database dumps — operators requiring stronger at-rest protection should use PostgreSQL full-disk encryption or a secrets manager for the master key.

**SFTP host key verification:** Setting the `SFTP Host Key` field on a connector (paste output of `ssh-keyscan <host>`) enables strict verification via `paramiko.RejectPolicy()`. Without it, new host keys are accepted with a logged warning — acceptable for development but not for production.

**XML XXE hardening:** All XML parsers use `etree.XMLParser(resolve_entities=False, no_network=True)` to prevent external entity injection.

**SSRF protection:** `RestTransport` validates `api_url` before every request — URLs must use `https://` and must not resolve to RFC-1918 private ranges, link-local (`169.254.x.x`), or loopback addresses. `HttpPostTransport` URL-encodes the `transport_name` query parameter using `urllib.parse.quote` to prevent query-string injection. Both transports truncate error response bodies to 500 characters before storing in `3pl.message.last_error` to prevent large or sensitive error payloads being written to the database.

**Input validation:** All inbound 3PL data (tracking API responses, SOH API quantities, CSV inventory reports) is validated before ORM writes. Tracking status values are checked against an allowlist; POD URLs must use `https://`; SOH quantities and all numeric CSV fields are parsed with overflow/NaN guards so a malformed file cannot abort processing of subsequent rows; CSV payloads over 50 MB are rejected. Carrier name lookups from inbound XML use exact match (`=`) rather than `ilike` to prevent wildcard expansion.

**XSS protection:** User display names interpolated into Odoo chatter `message_post` HTML bodies are escaped with `html.escape()`.

**Resource exhaustion:** Frontend-supplied numeric parameters (e.g. `weeks` in `get_weekly_trend`) are clamped server-side to prevent unbounded database query loops.

**Idempotency:** `source_hash` deduplication (SHA-256) prevents replayed inbound payloads from being applied twice. All credential fields carry `password=True` and `groups='stock.group_stock_manager'`.

## Sprint Status

| Sprint | Scope | Status |
|--------|-------|--------|
| Sprint 1, Tasks 1–9 | `stock_3pl_core` platform layer: connector, message queue, transport abstraction, document base, views, cron | Complete |
| Sprint 1, Tasks 10–17 | `stock_3pl_mainfreight`: document builders, event triggers, transport, custom fields | Complete |
| Sprint 2 | Warehouse routing engine (haversine, stock check, split logic), cross-border hold, operational UX (connector views, exception queues, picking status) | Complete |
| Sprint 3 | Tracking API (MF + Freightways), SOH API cross-check, inbound polling cron, stale order reconciliation, SFTP strict host key, Fernet credential encryption, integration test suite | Complete |
| Phase 2 | KPI dashboard (OWL), kanban pipeline, exception queue, inventory discrepancy screen | Complete |
| Inbound processing | CSV type detection (SOH vs ACKH/ACKL), ACK dispatch, received message queue processor | Complete |
| Bug hunt + security hardening | Full-codebase audit: SFTP inbound tuple unpacking (silent payload loss), Odoo 15 `move_ids` rename in split engine and SO confirmation, Freightways credential field scope fix, empty payload guard, fractional quantity rounding, `description_sale` sync field removal, SSRF URL validation, HTTP POST query-string encoding, credential store decryption safety, XML wildcard carrier lookup, CSV numeric overflow guards, XSS chatter escaping, KPI query bounds | Complete |
| **API gap sprint** | Corrected public API base URLs (`api[-test].mainfreight.com/warehousing/1.1/Customers`); `?region=` query param + `mf_region` connector field; `send_put`/`send_delete` on `RestTransport`; `update_order`, `delete_order`, `delete_inward` on MF transport; `action=CREATE\|UPDATE` on outbound documents + `build_delete_ref()`; dual tracking status map (flat `Status` + `eventCode` fallback); dual SO Confirmation parser (SCH/SCL + webhook-style); dormant webhook stub controller (3 POST routes, X-MF-Secret) | **Complete** |

## License

OPL-1. See individual `__manifest__.py` files for per-module licensing.

## Contributing

Raise a pull request against `master`. Run the pure-Python test suite (`pytest -m "not odoo_integration" -q`) before submitting — all 363 tests must pass.

### Known Limitations

- **SFTP MITM (when `sftp_host_key` is unset):** New host keys are accepted with a logged warning. Set the host key field in production.
- **Fernet master key co-located with ciphertext:** The encryption key lives in `ir.config_parameter` in the same database as encrypted secrets. A full database dump yields both. Use OS-level or external secret management for higher assurance.
- **`x_mf_connector_id` override is not yet wired:** The reassign-warehouse wizard writes this field, but the push cron does not yet read it to route a re-assigned picking to a different connector. Manual reassignment requires a direct re-queue.
- **Stale order detection uses `write_date`:** `_reconcile_sent_orders` uses `write_date` as a proxy for "time since last MF send". Any unrelated field write refreshes `write_date` and resets the timer. A dedicated `x_mf_sent_date` field would be more accurate; this is tracked for a future minor release.
- **Equatorial/meridian coordinate fallback:** Partners at exactly `latitude=0` or `longitude=0` are treated as having no geocoordinates and fall back to the first enabled warehouse. This is intentional (Odoo Float fields default to `0.0`) but may misroute addresses genuinely at the equator or prime meridian.
- **Webhook controller is dormant:** The three MF webhook routes (`/mf/webhook/*`) accept and log payloads but do not enqueue them for processing. Activate by wiring `_handle_webhook` to `3pl.message` create and setting `stock_3pl_mainfreight.webhook_secret` in system parameters. Requires cloud hosting with a public HTTPS endpoint.
- **Cancellation not yet wired to MF delete:** `delete_order` and `delete_inward` transport methods are implemented and tested, but SO/PO cancellation in Odoo does not yet automatically call them. Scope trigger wiring with the ops team (auto vs. manual approval).
- **`mf_region` value unconfirmed:** Default is `ANZ`. Confirm the exact string for NZ/AU with Mainfreight before go-live (known alternatives: `NZ`, `AU`, `NewZealand`).
