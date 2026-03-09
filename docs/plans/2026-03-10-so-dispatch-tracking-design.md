# SO Dispatch Tracking Design

**Goal:** Surface Mainfreight transport tracking status and a live tracking link on the Sale Order, automatically updated via the existing 3PL tracking cron, so customer care can see dispatch status at a glance and include the tracking link in customer communications.

**Architecture:** Extend `stock_3pl_mainfreight` only — two new fields on `stock.picking`, two computed fields on `sale.order`, and a Phase 0 pre-pass in the existing tracking cron using Mainfreight's chained reference API. No new modules, no new background jobs, no webhook infrastructure.

**Tech Stack:** Python, Odoo 19, Mainfreight Tracking API (chained references), `stock.picking`, `sale.order`, existing `tracking_cron.py`.

---

## Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| SO display | Status badge + single tracking link | Customer care clicks through for detail |
| Reference strategy | Chained reference (OutboundReference → transport tracking in one call) | No Transport Shipment Create step needed — we are not booking freight |
| Tracking update mechanism | Extend existing 30-min polling cron | No webhook infrastructure; cron already handles all in-flight pickings |

---

## Data Model

### `stock.picking` — two new fields (in `picking_mf.py`)

| Field | Type | Purpose |
|-------|------|---------|
| `x_mf_outbound_ref` | Char | OutboundReference Mainfreight assigns when they receive the outbound order. Used as the chained reference query key until the connote is known. Populated when parsing the SO confirmation. |
| `x_mf_tracking_url` | Char | Live transport tracking link returned by the Mainfreight Tracking API. Separate from `x_mf_pod_url` (post-delivery proof of delivery). |

### `sale.order` — two computed fields (in `sale_order_mf.py`)

| Field | Type | Purpose |
|-------|------|---------|
| `x_mf_delivery_status` | Char | Most advanced `x_mf_status` across all linked outbound pickings, human-readable (e.g. "In Transit", "Out for Delivery", "Delivered"). Computed, not stored. |
| `x_mf_tracking_url` | Char | Tracking URL from the most recently dispatched outbound picking. Computed, not stored. |

---

## Tracking Cron Extension (`tracking_cron.py`)

The existing cron runs every 30 minutes. Add **Phase 0** before the existing connote-based polling:

### Phase 0 — Chained reference query (new)

Target pickings:
- `x_mf_status = 'mf_sent'` (warehouse confirmed receipt, not yet dispatched)
- `x_mf_connote` is empty
- `x_mf_outbound_ref` is set

For each matching picking:
1. Query Mainfreight Tracking API using `OutboundReference` (chained mode)
2. If response includes a linked transport consignment:
   - Write `x_mf_connote` (enables Phase 1 to take over from next cycle)
   - Write `x_mf_tracking_url`
   - Advance `x_mf_status` to `'mf_dispatched'`
   - Post one chatter note on the linked SO: *"Order dispatched — Track your delivery: {url}"*

### Phase 1 — Poll by connote (existing, no changes)

Existing behavior: finds pickings with `x_mf_connote` set and in-flight status, polls by connote, writes `x_mf_status`, `x_mf_pod_url`, `x_mf_signed_by`, `x_mf_delivered_date`.

**Extension to Phase 1**: also write `x_mf_tracking_url` if returned in the tracking response (keeps URL fresh if Mainfreight rotates it).

---

## Sale Order View

Extend the existing `sale.order` form view (already inherited by `stock_3pl_mainfreight`):

Add to the SO header area, invisible when `x_mf_tracking_url` is not set:

```
Delivery Status: [In Transit]   [Track Shipment →]
```

- Status label: `x_mf_delivery_status` (plain text)
- "Track Shipment →": external URL button opening `x_mf_tracking_url` in a new tab
- Both invisible until Mainfreight dispatches (i.e. `x_mf_tracking_url` is empty before dispatch)

---

## Customer Communications

No new email template. The chatter note posted on dispatch appears in the SO chatter — customer care can copy the link directly. For formal comms, `x_mf_tracking_url` is available as a template variable on `sale.order` for use in any existing Odoo email template (e.g. delivery confirmation).

---

## File Changelist

| File | Change |
|------|--------|
| `stock_3pl_mainfreight/models/picking_mf.py` | Add `x_mf_outbound_ref`, `x_mf_tracking_url` fields |
| `stock_3pl_mainfreight/models/sale_order_mf.py` | Add `x_mf_delivery_status`, `x_mf_tracking_url` computed fields; post SO chatter on dispatch |
| `stock_3pl_mainfreight/models/tracking_cron.py` | Add Phase 0 chained reference query; extend Phase 1 to write `x_mf_tracking_url` |
| `stock_3pl_mainfreight/views/sale_order_views.xml` | Add status label + external tracking link to SO form header |
| `stock_3pl_mainfreight/tests/test_so_tracking.py` | Pure-Python tests for computed fields + cron targeting logic |

---

## Testing

All tests pure-Python (no live Odoo instance required):

| Test | Covers |
|------|--------|
| `x_mf_delivery_status` returns most advanced picking status | Computed field rollup logic |
| `x_mf_tracking_url` returns URL from dispatched picking | Computed field logic |
| Both fields empty when no outbound pickings | Edge case |
| Phase 0 targets correct pickings | Cron targeting: mf_sent + no connote + has outbound_ref |
| Phase 0 skips picking already with connote | Idempotency |
| Phase 0 skips picking with no outbound_ref | Missing reference guard |
| Phase 0 writes connote + tracking_url + advances status on API success | Happy path |
| Phase 0 silent no-op on API empty response | Graceful degradation |
| Phase 1 writes tracking_url if returned | Extended Phase 1 |
| SO chatter posted on first dispatch | Comms hook |
