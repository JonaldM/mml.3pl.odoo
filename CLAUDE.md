# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project is an **integration layer between Mainfreight 3PL (Third-Party Logistics) warehousing system and Odoo ERP**. Mainfreight manages physical warehouse operations; Odoo is the ERP managing products, orders, and inventory records. The integration synchronises data in both directions.

Current state:
- `stock_3pl_core` — platform layer (forwarder-agnostic): **Sprint 1 complete** (Tasks 1–9)
- `stock_3pl_mainfreight` — Mainfreight implementation: **Sprint 1 complete** (Tasks 10–17) + **Sprint 2 partially implemented** (routing engine, cross-border, SOH discrepancy, KPI dashboard, tracking/inbound crons, webhooks)

## Key Documents

- `docs/Mainfreight Warehousing Integration Specification.pdf` — the primary integration spec; defines all MF document types (SOH, SOL, INWH, INWL, etc.), field-level mappings, and communication protocols
- `mainfreight_odoo_model_checklist.xlsx` — prioritised list of Odoo models to be exported/integrated, grouped into Tiers 1–4 with field mappings to Mainfreight document fields
- `docs/*.pdf` — exported Odoo model schemas (field definitions) used as reference when building API payloads
- `docs/plans/2026-02-28-3pl-integration-platform-design.md` — architecture design doc
- `docs/plans/2026-02-28-3pl-integration-platform-implementation.md` — implementation plan

## Module Structure

```
addons/
├── stock_3pl_core/                    # Platform layer (forwarder-agnostic)
│   ├── models/
│   │   ├── connector.py               # 3pl.connector — warehouse/transport config + credential encryption
│   │   ├── message.py                 # 3pl.message — queue, state machine, cron
│   │   ├── transport_base.py
│   │   └── document_base.py           # AbstractDocument + FreightForwarderMixin
│   ├── transport/
│   │   ├── rest_api.py                # RestTransport
│   │   ├── sftp.py                    # SFTPTransport
│   │   └── http_post.py
│   ├── services/
│   │   └── tpl_service.py             # TPLService — cross-module public API (queue_inward_order)
│   ├── utils/
│   │   └── credential_store.py        # Fernet encryption for connector credential fields
│   ├── wizard/
│   │   ├── inbound_simulator.py       # Stub: inbound payload tester (Phase 2)
│   │   └── manual_sync_wizard.py      # Stub: manual outbound sync trigger (Phase 2)
│   └── views/                         # Connector form, message list, kanban, menus
│
└── stock_3pl_mainfreight/             # Mainfreight implementation
    ├── models/
    │   ├── connector_mf.py            # MF API credentials + environment
    │   ├── connector_freightways.py   # Freightways/Castle Parcels credentials on 3pl.connector
    │   ├── warehouse_mf.py            # x_mf_warehouse_code, x_mf_customer_id, lat/lng, x_mf_enabled
    │   ├── picking_mf.py              # x_mf_status (10-state), tracking fields, push cron entry
    │   ├── sale_order_mf.py           # x_mf_sent, x_mf_filename, x_mf_split
    │   ├── sale_order_hook.py         # action_confirm → queue 3pl.message
    │   ├── product_hook.py            # write() on SYNC_FIELDS → queue product_spec
    │   ├── route_engine.py            # mf.route.engine — haversine warehouse selection
    │   ├── split_engine.py            # mf.split.engine — routing assignment + cross-border flag
    │   ├── push_cron.py               # mf.push.cron — outbound push pipeline
    │   ├── inbound_cron.py            # mf.inbound.cron — SOH/ACK polling + stale order reconciliation
    │   ├── tracking_cron.py           # mf.tracking.cron — connote tracking poll
    │   ├── soh_discrepancy.py         # mf.soh.discrepancy — SOH variance records (open/investigated/accepted)
    │   └── kpi_dashboard.py           # mf.kpi.dashboard — KPI service (DIFOT, IRA, exception rate, shrinkage)
    ├── document/
    │   ├── product_spec.py            # CSV builder (outbound)
    │   ├── sales_order.py             # XML builder (outbound, SOH/SOL)
    │   ├── inward_order.py            # XML builder (outbound, INWH/INWL)
    │   ├── so_confirmation.py         # XML parser (inbound)
    │   ├── so_acknowledgement.py      # CSV parser (inbound, ACKH/ACKL V3)
    │   └── inventory_report.py        # CSV parser → stock.quant upsert (inbound)
    ├── transport/
    │   ├── mainfreight_rest.py        # MainfreightRestTransport
    │   └── freightways_rest.py        # FreightwaysRestTransport (Castle Parcels)
    ├── controllers/
    │   └── webhook.py                 # /mf/webhook/* endpoints; HMAC via X-MF-Secret header
    └── wizard/
        ├── accept_discrepancy_wizard.py   # mf.accept.discrepancy.wizard — accept SOH variance as shrinkage
        └── reassign_warehouse_wizard.py   # mf.reassign.warehouse.wizard — re-queue exception to new connector
```

## Custom Fields (x_mf_* prefix)

All custom fields live in `stock_3pl_mainfreight`, not `stock_3pl_core`.

```
stock.warehouse: x_mf_warehouse_code, x_mf_customer_id, x_mf_enabled,
                 x_mf_latitude, x_mf_longitude
stock.picking:   x_mf_status, x_mf_connote, x_mf_pick_id, x_mf_pod_url,
                 x_mf_signed_by, x_mf_dispatched_date, x_mf_delivered_date,
                 x_mf_routed_by (Selection: manual/auto_closest/auto_split),
                 x_mf_cross_border (Boolean), x_mf_connector_id
sale.order:      x_mf_sent, x_mf_sent_date, x_mf_filename, x_mf_split
```

## Key Service Models (AbstractModel — no DB table)

| Model | File | Purpose |
|-------|------|---------|
| `mf.route.engine` | `models/route_engine.py` | Haversine warehouse selection; raises `UserError` if no `x_mf_enabled` warehouse |
| `mf.split.engine` | `models/split_engine.py` | Applies routing to `stock.picking`; sets cross-border flags |
| `mf.push.cron` | `models/push_cron.py` | Outbound push pipeline; calls `_route_pending_orders()` before generating XML |
| `mf.inbound.cron` | `models/inbound_cron.py` | Polls SOH/ACK files; processes `3pl.message` queue; reconciles stale orders |
| `mf.tracking.cron` | `models/tracking_cron.py` | Polls MF tracking API for in-flight pickings; validates `pod_url` (https only) |
| `mf.kpi.dashboard` | `models/kpi_dashboard.py` | KPI computation called from OWL frontend via `orm.call()` |

## Credential Encryption

`stock_3pl_core/utils/credential_store.py` provides Fernet-based symmetric encryption for connector credential fields:

- Master key stored in `ir.config_parameter` as `stock_3pl_core.credential_key` (auto-generated on first use)
- Encrypted values are stored with an `enc:` prefix (idempotent — already-encrypted values pass through)
- Plaintext legacy values are handled transparently on read with a warning to re-save
- Requires `cryptography` Python package (in `requirements.txt`)
- Both `connector_mf.py` and `connector_freightways.py` call `encrypt_credential()` in `create()` and `write()`

## SOH Discrepancy Workflow

`mf.soh.discrepancy` records are populated by the inbound SOH cron when MF stock differs from Odoo:

- States: `open` → `investigated` → `accepted` (Accepted = Shrinkage)
- `action_accept_discrepancy(reason)` writes MF qty to `stock.quant` (MF is source of truth)
- Accepted discrepancies with `variance_qty < 0` feed the **shrinkage KPI**
- `mf.accept.discrepancy.wizard` provides the UI for acceptance with mandatory reason field

## KPI Dashboard

`mf.kpi.dashboard` is an AbstractModel called from OWL via `this.orm.call('mf.kpi.dashboard', 'get_kpi_summary', [])`:

- **DIFOT**: % delivered on time (within `date_deadline + grace_days`); uses raw SQL for field-to-field date comparison
- **IRA**: `(tracked SKUs - SKUs with open discrepancy > tolerance) / tracked SKUs × 100`
- **Exception rate**: `mf_exception` pickings / all MF pickings (30-day window)
- **Shrinkage**: accepted losses / total internal stock (rolling 12M)
- **In-flight**: pickings in `mf_sent`, `mf_received`, or `mf_dispatched`
- `data_available` flag prevents false all-green on fresh install

KPI targets are configurable via `ir.config_parameter` (`stock_3pl_mainfreight.kpi_*`).

## Inbound Pipeline (Two Paths)

**Path A — Queue path** (XML inbound via SFTP/REST poll):
1. `ThreePlMessage._poll_inbound()` creates `3pl.message` records with `state='received'`
2. `mf.inbound.cron._process_inbound_messages()` dispatches to the correct document handler
3. Dispatch table: `so_confirmation` → `SOConfirmationDocument`, `so_acknowledgement` → `SOAcknowledgementDocument`, `inventory_report` → `InventoryReportDocument`

**Path B — Direct path** (CSV files from SFTP poll):
1. `mf.inbound.cron._poll_inventory_reports()` polls connectors directly
2. ACK detection: filename prefix `ACKH_`/`ACKL_` or header sniff via `ThreePlMessage._detect_inbound_type()`
3. Dispatches directly to `SOAcknowledgementDocument.apply_csv()` or `InventoryReportDocument.apply_csv()`

Stale order reconciliation: pickings with `x_mf_status='mf_sent'`, no connote, older than `reconcile_hours` (default 48h) are flagged `mf_exception`.

## Webhook Controller

Routes at `/mf/webhook/{order-confirmation,inward-confirmation,tracking-update}`:
- Auth: none (public endpoint); validated by HMAC comparison of `X-MF-Secret` header
- Secret stored in `ir.config_parameter` as `stock_3pl_mainfreight.webhook_secret`
- Missing or mismatched secret returns 401; unconfigured system always denies
- Currently stubs returning `{"status": "received"}` — wire to message queue when on cloud hosting

## Cross-Module API (TPLService)

`stock_3pl_core/services/tpl_service.py` — retrieved via `self.env['mml.registry'].service('3pl')`:

```python
svc.queue_inward_order(purchase_order_id, connector_id=None)  # → message_id or None
```

Returns `NullService` (no-op) if `stock_3pl_core` is not installed.

## System Parameters Reference

| Parameter key | Default | Purpose |
|---------------|---------|---------|
| `stock_3pl_core.credential_key` | auto-generated | Fernet master key for credential encryption |
| `stock_3pl_mainfreight.webhook_secret` | — | HMAC secret for `/mf/webhook/*` |
| `stock_3pl_mainfreight.reconcile_hours` | `48` | Hours before stale `mf_sent` picking → `mf_exception` |
| `mml.cron_alert_email` | — | Email for cron failure alerts |
| `stock_3pl_mainfreight.kpi_difot_target` | `95` | DIFOT green threshold % |
| `stock_3pl_mainfreight.kpi_ira_target` | `98` | IRA green threshold % |
| `stock_3pl_mainfreight.kpi_exception_rate_target` | `2` | Exception rate green threshold % |
| `stock_3pl_mainfreight.kpi_shrinkage_target` | `0.5` | Shrinkage green threshold % |
| `stock_3pl_mainfreight.difot_grace_days` | `0` | Days of grace for DIFOT on-time calculation |
| `stock_3pl_mainfreight.ira_tolerance` | `0.005` | Fractional tolerance before a discrepancy counts against IRA |

## Odoo Model Tier Structure

The checklist (`mainfreight_odoo_model_checklist.xlsx`) organises integration work into four tiers:

| Tier | Focus |
|------|-------|
| **Tier 1** | Core: products, orders (SO/PO), stock movements, warehouse/location config, delivery carriers |
| **Tier 2** | Partners, addresses, countries, company identity |
| **Tier 3** | Attributes, routes, sequences, packages, UoM categories, scrap |
| **Tier 4** | Custom Odoo models (`x_pickhdrs`, `x_picklines`) — inspect for existing MF integration fields |

Priority numbers within tiers indicate implementation order (lower = higher priority).

## Critical Field Mappings (Tier 1)

Key Odoo → Mainfreight field translations that appear throughout the integration:

- `product.product.default_code` → MF **Product Code**
- `sale.order.name` → MF **Client Order Number**
- `purchase.order.name` → MF **Inwards Reference**
- `purchase.order.date_planned` → MF **Booking Date**
- `stock.picking.carrier_tracking_ref` → MF tracking reference
- `stock.warehouse.code` → MF **WarehouseID**
- `res.partner.ref` → MF **Consignee Code**
- `res.company` likely needs a custom `customer_id` field for MF **Customer ID** (field 68)

## MF Document Types

| Document | Odoo source | Direction |
|----------|-------------|-----------|
| Product Specification | `product.product` + `product.template` + `product.packaging` | Outbound |
| SOH header / SOL lines | `sale.order` / `sale.order.line` | Outbound |
| INWH header / INWL lines | `purchase.order` / `purchase.order.line` | Outbound |
| SO Confirmation / Inward Confirmation | `stock.picking` + moves | Inbound |
| ACKH / ACKL (SO Acknowledgement) | `stock.picking` status update | Inbound |
| Inventory Report | `stock.quant` upsert | Inbound |

## Development Commands

### Run pure-Python structural tests (no Odoo needed)
```bash
python -m pytest -m "not odoo_integration" -q
```

### Run a single test file
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_route_engine.py -q
```

### Run all tests (shows expected Odoo-integration failures)
```bash
python -m pytest -q
```

### Install modules in Odoo
```bash
python odoo-bin -i stock_3pl_core,stock_3pl_mainfreight -d testdb --stop-after-init
```

### Run Odoo integration tests
```bash
python odoo-bin -u stock_3pl_core,stock_3pl_mainfreight --test-enable --stop-after-init -d testdb
```

### Install dependencies
```bash
pip install -r requirements.txt   # includes paramiko (SFTP) and cryptography (Fernet)
```

## Test Suite Notes

The repository has two categories of tests:

- **Pure-Python structural tests** — run without Odoo; verify module structure, field definitions, document builders, CSV/XML parsing logic. Run with `pytest -m "not odoo_integration"`.
- **Odoo integration tests** — inherit from `odoo.tests.TransactionCase`; use `self.env.create()`, `self.env.ref()`, etc. These require `odoo-bin --test-enable` and will fail under plain pytest. They are auto-marked with the `odoo_integration` marker by `conftest.py`.

The `pytest.ini` at the repo root defines the `odoo_integration` marker and suppresses pytest warnings output.

Sprint 2 Odoo integration tests are in `test_routing_integration.py` and skipped by `pytest -m "not odoo_integration"`.

## Architecture Decisions

- `document_type = fields.Selection(DOCUMENT_TYPE, required=True)` — Selection, not Char
- `_inherit = ['mail.thread', 'mail.activity.mixin']` on `3pl.message` for activity scheduling
- `_dead_letter()` has `ensure_one()` guard
- `action_fail` boundary: `retry_count + 1 >= MAX_RETRIES` (MAX_RETRIES = 3)
- SQL constraints scoped per `(connector_id, field)` — not global
- Ti-Hi fields (`x_mf_carton_per_layer`, `x_mf_layer_per_pallet`): stub in XML builder, skip if None
- L×W×H per pack level: CBM exists, dimensions not yet available — same stub approach
- SO Acknowledgement (ACKH/ACKL): CSV inbound, maps to `mf_received` status
- `forwarder` field renamed to `warehouse_partner` on both `3pl.connector` and `3pl.message`
- Business logic lives in pure-Python service classes (no `self.env`) — Odoo models are thin adapters
- `_compute_difot_value()` uses raw SQL for field-to-field date comparison (ORM cannot do this)
- Tracking cron validates `pod_url` (https only) and sanitizes `x_mf_signed_by` (ASCII printable, max 128 chars)

## Known Issues / Backlog

- Webhook controller stubs are not yet wired to the message queue (pending cloud hosting decision)
- `stock_3pl.manual_sync_wizard` and `stock_3pl.inbound_simulator` are Phase 2 stubs with no implementation
