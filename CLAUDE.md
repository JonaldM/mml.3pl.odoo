# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project is an **integration layer between Mainfreight 3PL (Third-Party Logistics) warehousing system and Odoo ERP**. Mainfreight manages physical warehouse operations; Odoo is the ERP managing products, orders, and inventory records. The integration synchronises data in both directions.

Current state:
- `stock_3pl_core` — platform layer (forwarder-agnostic): **Sprint 1 complete** (Tasks 1–9)
- `stock_3pl_mainfreight` — Mainfreight implementation: **Sprint 1 complete** (Tasks 10–17)
- Sprint 2 (warehouse routing engine, full UX) — **not yet started**

## Key Documents

- `docs/Mainfreight Warehousing Integration Specification.pdf` — the primary integration spec; defines all MF document types (SOH, SOL, INWH, INWL, etc.), field-level mappings, and communication protocols
- `mainfreight_odoo_model_checklist.xlsx` — prioritised list of Odoo models to be exported/integrated, grouped into Tiers 1–4 with field mappings to Mainfreight document fields
- `docs/*.pdf` — exported Odoo model schemas (field definitions) used as reference when building API payloads
- `docs/plans/2026-02-28-3pl-integration-platform-design.md` — architecture design doc
- `docs/plans/2026-02-28-3pl-integration-platform-implementation.md` — implementation plan

## Module Structure

```
addons/
├── stock_3pl_core/          # Platform layer (forwarder-agnostic)
│   ├── models/
│   │   ├── connector.py     # 3pl.connector — warehouse/transport config
│   │   ├── message.py       # 3pl.message — queue, state machine, cron
│   │   ├── transport_base.py
│   │   └── document_base.py # AbstractDocument + FreightForwarderMixin
│   ├── transport/
│   │   ├── rest_api.py      # RestTransport
│   │   ├── sftp.py          # SFTPTransport
│   │   └── http_post.py
│   ├── views/               # Connector form, message list, menus
│   └── data/cron.xml        # Outbound queue cron (5 min) + inbound poll cron (15 min)
└── stock_3pl_mainfreight/   # Mainfreight implementation
    ├── models/
    │   ├── connector_mf.py      # MF API credentials + environment
    │   ├── warehouse_mf.py      # x_mf_warehouse_code, x_mf_customer_id, lat/lng
    │   ├── picking_mf.py        # x_mf_status (10-state), tracking fields
    │   ├── sale_order_mf.py     # x_mf_sent, x_mf_filename, x_mf_split
    │   ├── sale_order_hook.py   # action_confirm → queue 3pl.message
    │   └── product_hook.py      # write() on SYNC_FIELDS → queue product_spec
    ├── document/
    │   ├── product_spec.py      # CSV builder (outbound)
    │   ├── sales_order.py       # XML builder (outbound)
    │   ├── so_confirmation.py   # XML parser (inbound)
    │   ├── so_acknowledgement.py # CSV parser (inbound, V3)
    │   └── inventory_report.py  # CSV parser → stock.quant upsert (inbound)
    └── transport/
        └── mainfreight_rest.py  # MainfreightRestTransport
```

## Custom Fields (x_mf_* prefix)

All custom fields live in `stock_3pl_mainfreight`, not `stock_3pl_core`.

```
stock.warehouse: x_mf_warehouse_code, x_mf_customer_id, x_mf_enabled,
                 x_mf_latitude, x_mf_longitude
stock.picking:   x_mf_status, x_mf_connote, x_mf_pick_id, x_mf_pod_url,
                 x_mf_signed_by, x_mf_dispatched_date, x_mf_delivered_date,
                 x_mf_routed_by (Sprint 2), x_mf_cross_border (Sprint 2)
sale.order:      x_mf_sent, x_mf_sent_date, x_mf_filename, x_mf_split
```

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

Mainfreight uses named document types that map to Odoo objects:
- **Product Specification** → `product.product` + `product.template` + `product.packaging`
- **SOH header / SOL lines** → `sale.order` / `sale.order.line`
- **INWH header / INWL lines** → `purchase.order` / `purchase.order.line`
- **SO Confirmation / Inward Confirmation** → `stock.picking` + `stock.move` + `stock.move.line`
- **Inventory Report** → `stock.quant`

## Development Commands

### Install modules
```bash
python odoo-bin -i stock_3pl_core,stock_3pl_mainfreight -d testdb --stop-after-init
```

### Run all tests (requires live Odoo database)
```bash
python odoo-bin -u stock_3pl_core,stock_3pl_mainfreight --test-enable --stop-after-init -d testdb
```

### Run pure-Python structural tests (no Odoo needed)
```bash
python -m pytest -m "not odoo_integration" -q
```

### Run all tests (shows expected Odoo-integration failures)
```bash
python -m pytest -q
```

### Install paramiko (required for SFTP transport)
```bash
pip install paramiko
```

## Test Suite Notes

The repository has two categories of tests:

- **Pure-Python structural tests** — run without Odoo; verify module structure, field definitions, document builders, CSV/XML parsing logic. Run with `pytest -m "not odoo_integration"`.
- **Odoo integration tests** — inherit from `odoo.tests.TransactionCase`; use `self.env.create()`, `self.env.ref()`, etc. These require `odoo-bin --test-enable` and will fail under plain pytest. They are auto-marked with the `odoo_integration` marker by `conftest.py`.

The `pytest.ini` at the repo root defines the `odoo_integration` marker and suppresses pytest warnings output.

## Sprint Scope Summary

| Sprint | Tasks | Status |
|--------|-------|--------|
| Sprint 1 | Tasks 1–9: `stock_3pl_core` platform layer (connector, message queue, transport abstraction, document base, views, cron) | Complete |
| Sprint 1 | Tasks 10–17: `stock_3pl_mainfreight` (document builders, event triggers, transport, custom fields) | Complete |
| Sprint 2 | Warehouse routing engine (haversine distance, stock check, split logic), cross-border hold, full UX (dashboard, kanban, exception queues) | Not started |

## Architecture Decisions

- `document_type = fields.Selection(DOCUMENT_TYPE, required=True)` — Selection, not Char
- `_inherit = ['mail.thread', 'mail.activity.mixin']` on `3pl.message` for activity scheduling
- `mail` in manifest depends
- `_dead_letter()` has `ensure_one()` guard
- `action_fail` boundary: `retry_count + 1 >= MAX_RETRIES` (MAX_RETRIES = 3)
- SQL constraints scoped per `(connector_id, field)` — not global
- Ti-Hi fields (`x_mf_carton_per_layer`, `x_mf_layer_per_pallet`): stub in XML builder, skip if None
- L×W×H per pack level: CBM exists, dimensions not yet available — same stub approach
- SO Acknowledgement (ACKH/ACKL): CSV inbound, maps to `mf_received` status

## Sprint 2 Additions

### New Service Models

Both are `AbstractModel` instances (no database table, no ACL required):

- `mf.route.engine` (`addons/stock_3pl_mainfreight/models/route_engine.py`) — haversine-based warehouse selection; raises `UserError` if no `x_mf_enabled` warehouse exists
- `mf.split.engine` (`addons/stock_3pl_mainfreight/models/split_engine.py`) — applies routing assignments to `stock.picking` records; sets cross-border flags

### New Custom Fields

```
stock.warehouse:  x_mf_latitude (Float), x_mf_longitude (Float), x_mf_enabled (Boolean)
stock.picking:    x_mf_routed_by (Selection: manual/auto_closest/auto_split),
                  x_mf_cross_border (Boolean)
sale.order:       x_mf_split (Boolean)
```

### Cross-Border Logic

When `warehouse.partner_id.country_id != order.partner_shipping_id.country_id`:
- `x_mf_cross_border` is set to `True` on the `stock.picking`
- `x_mf_status` is set to `'mf_held_review'` (held pending manual release)
- Manual release via `action_approve_cross_border()` on `stock.picking`

### Haversine Utility

Pure-Python function at `addons/stock_3pl_mainfreight/utils/haversine.py`. Computes great-circle distance in km between two (lat, lng) coordinate pairs. Used by `mf.route.engine` to select the closest enabled warehouse to the delivery address.

### Routing Pre-Processor

`_route_pending_orders()` is called at the start of `_run_mf_push` (in `picking_mf.py`) to assign warehouse routing before outbound XML is generated. Orders without routing are assigned via haversine; orders with `partner_latitude == 0 and partner_longitude == 0` fall back to the first enabled warehouse.

### UX Views Added (Sprint 2)

| File | Purpose |
|------|---------|
| `stock_3pl_mainfreight/views/picking_mf_views.xml` | Inline `x_mf_status` on `stock.picking` tree and form |
| `stock_3pl_mainfreight/views/exception_views.xml` | Exception queue list view for held/failed pickings |
| `stock_3pl_mainfreight/views/menu_mf.xml` | Top-level Mainfreight menu and sub-menu items |
| `stock_3pl_mainfreight/views/warehouse_mf_views.xml` | Extends warehouse form with Mainfreight Routing group |
| `stock_3pl_core/views/connector_views.xml` | Kanban view for `3pl.connector` (status overview) |

### Field Rename

`forwarder` renamed to `warehouse_partner` everywhere — applies to both `3pl.connector` and `3pl.message` models. Update any domain filters or XML references that previously used `forwarder`.

### Test Suite

| Milestone | Count |
|-----------|-------|
| Sprint 1 end | 44 pure-Python tests |
| Sprint 2 end | 100 pure-Python tests |

Pure-Python tests cover: haversine util, route engine logic, split engine logic, cross-border detection, push cron wiring, all document builders, CSV/XML parsers, and field definitions. Odoo integration tests (requiring `odoo-bin --test-enable`) are in `test_routing_integration.py` and skipped by `pytest -m "not odoo_integration"`.
