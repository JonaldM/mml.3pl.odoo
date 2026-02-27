# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project is an **integration layer between Mainfreight 3PL (Third-Party Logistics) warehousing system and Odoo ERP**. Mainfreight manages physical warehouse operations; Odoo is the ERP managing products, orders, and inventory records. The integration synchronises data in both directions.

Current state: **Planning/documentation phase** — no code exists yet. The repository contains reference documents and a model mapping checklist.

## Key Documents

- `docs/Mainfreight Warehousing Integration Specification.pdf` — the primary integration spec; defines all MF document types (SOH, SOL, INWH, INWL, etc.), field-level mappings, and communication protocols
- `mainfreight_odoo_model_checklist.xlsx` — prioritised list of Odoo models to be exported/integrated, grouped into Tiers 1–4 with field mappings to Mainfreight document fields
- `docs/*.pdf` — exported Odoo model schemas (field definitions) used as reference when building API payloads

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
