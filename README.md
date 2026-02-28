# Mainfreight 3PL Integration for Odoo 15

A bidirectional integration layer between Mainfreight warehousing and Odoo 15 ERP, synchronising orders, products, stock confirmations, and inventory in both directions.

## Overview

Mainfreight manages the physical warehouse — receiving stock, picking and packing orders, and dispatching freight. Odoo manages the commercial side — products, sales orders, purchase orders, and inventory records. Without integration, these two systems operate in silos: warehouse staff must manually relay order information and inventory updates are reconciled by hand.

This integration closes that gap. When a sale order is confirmed in Odoo, the order is automatically transmitted to Mainfreight in their XML format. When Mainfreight confirms dispatch, a confirmation is received back into Odoo and the picking record is updated. Inventory reports from Mainfreight are parsed and applied to `stock.quant` records to keep stock levels in sync.

The codebase is structured as two Odoo addons. `stock_3pl_core` is a forwarder-agnostic platform layer providing the message queue, transport abstraction, and connector model — it contains no Mainfreight-specific logic. `stock_3pl_mainfreight` is the Mainfreight implementation: document builders, inbound parsers, the routing engine, and all `x_mf_*` custom fields. This separation makes it possible to add a second 3PL provider by writing a new addon against the core platform without modifying existing code.

## Module Structure

```
addons/
├── stock_3pl_core/              # Platform layer (forwarder-agnostic)
│   ├── models/
│   │   ├── connector.py         # 3pl.connector — warehouse/transport config
│   │   ├── message.py           # 3pl.message — queue, state machine, retry
│   │   ├── transport_base.py    # TransportBase abstract class
│   │   └── document_base.py     # AbstractDocument + FreightForwarderMixin
│   ├── transport/
│   │   ├── rest_api.py          # RestTransport
│   │   ├── sftp.py              # SFTPTransport (requires paramiko)
│   │   └── http_post.py         # HttpPostTransport
│   ├── views/                   # Connector form, message list, menus
│   └── data/cron.xml            # Outbound queue cron (5 min), inbound poll cron (15 min)
└── stock_3pl_mainfreight/       # Mainfreight implementation
    ├── models/
    │   ├── connector_mf.py      # MF API credentials and environment
    │   ├── warehouse_mf.py      # x_mf_warehouse_code, x_mf_customer_id, lat/lng
    │   ├── picking_mf.py        # x_mf_status (10-state lifecycle), tracking fields
    │   ├── sale_order_mf.py     # x_mf_sent, x_mf_filename, x_mf_split
    │   ├── sale_order_hook.py   # action_confirm → enqueue 3pl.message
    │   ├── product_hook.py      # write() on sync fields → enqueue product_spec
    │   ├── route_engine.py      # mf.route.engine — haversine warehouse selection
    │   ├── split_engine.py      # mf.split.engine — applies routing to stock.picking
    │   └── push_cron.py         # mf.push.cron — routes orders then fires outbound queue
    ├── document/
    │   ├── product_spec.py      # CSV builder (outbound)
    │   ├── sales_order.py       # XML builder (outbound)
    │   ├── so_confirmation.py   # XML parser (inbound)
    │   ├── so_acknowledgement.py # CSV parser (inbound, ACKH/ACKL)
    │   └── inventory_report.py  # CSV parser → stock.quant upsert (inbound)
    ├── transport/
    │   └── mainfreight_rest.py  # MainfreightRestTransport
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
| `mf.route.engine` | AbstractModel; selects warehouses using haversine distance and stock checks |
| `mf.split.engine` | AbstractModel; applies routing assignments to `stock.picking` records |
| `mf.push.cron` | AbstractModel; pre-processes routing then fires the outbound queue |

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

`mf.split.engine.apply_routing(order, assignments)` takes the assignment list and creates or updates `stock.picking` records accordingly, setting `x_mf_routed_by` and triggering cross-border detection.

### Cross-Border Detection

When the warehouse country differs from the delivery address country:
- `x_mf_cross_border` is set to `True` on the `stock.picking`.
- `x_mf_status` is set to `mf_held_review`.
- The picking is held until manually released via `action_approve_cross_border()`.

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

Go to **Inventory → Warehouses → [warehouse] → Mainfreight Routing tab**:

- Set `x_mf_enabled = True` to include this warehouse in routing decisions.
- Enter `x_mf_latitude` and `x_mf_longitude` (decimal degrees) so the routing engine can compute distances.
- Enter `x_mf_warehouse_code` — the warehouse code assigned by Mainfreight (e.g. `99`).
- Enter `x_mf_customer_id` — the Customer ID assigned by Mainfreight.

### Step 3 — Create a Connector

Go to **3PL Integration → Connectors → New**:

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

**For REST API transport**, fill:
- API URL
- API Secret (stored masked)

**For SFTP transport**, fill:
- SFTP Host, Port (default 22), Username, Password (stored masked)
- Inbound Path (default `/in`), Outbound Path (default `/out`)

**For HTTP POST transport**, fill:
- HTTP POST URL
- Transport Name (UniqueID)

### Step 4 — Verify Cron Jobs

Go to **Technical → Scheduled Actions** and confirm these are active:

| Cron Job | Model Method | Default Schedule |
|----------|--------------|------------------|
| 3PL: Process Outbound Queue | `3pl.message._process_outbound_queue()` | Every 5 minutes |
| 3PL: Poll Inbound Messages | `3pl.message._poll_inbound()` | Every 15 minutes |

The MF push cron (`mf.push.cron._run_mf_push()`) calls `_route_pending_orders()` before delegating to `_process_outbound_queue()`, so routing happens automatically on each push cycle.

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

Sprint 1 delivered 44 pure-Python tests covering the platform layer and all document builders. Sprint 2 targets 100 pure-Python tests adding coverage for haversine, route engine, split engine, cross-border detection, and push cron wiring.

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

- `api_secret` and `sftp_password` are stored with `password=True`, masking them in the UI. Both fields are restricted to `stock.group_stock_manager`.
- XML parsing (inbound SO Confirmation, Inward Confirmation) uses explicit XXE hardening with `resolve_entities=False` on the lxml parser.
- SFTP connections log a warning for unverified host keys. Configure a `known_hosts` file on the Odoo server for production deployments.
- All inbound 3PL data is validated against expected field names and types before any ORM lookup or write is attempted.
- The `source_hash` deduplication (SHA-256) prevents replayed inbound payloads from being applied twice.

## Sprint Status

| Sprint | Scope | Status |
|--------|-------|--------|
| Sprint 1, Tasks 1–9 | `stock_3pl_core` platform layer: connector, message queue, transport abstraction, document base, views, cron | Complete |
| Sprint 1, Tasks 10–17 | `stock_3pl_mainfreight`: document builders, event triggers, transport, custom fields | Complete |
| Sprint 2 | Warehouse routing engine (haversine, stock check, split logic), cross-border hold, full operational UX (dashboard, kanban, exception queues) | In progress |

## License

OPL-1. See individual `__manifest__.py` files for per-module licensing.

## Contributing

Raise a pull request against the `feature/3pl-platform-implementation` branch. Run the pure-Python test suite (`pytest -m "not odoo_integration" -q`) before submitting.
