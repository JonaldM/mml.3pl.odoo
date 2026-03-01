# Design: Mainfreight Public API Gap Close Sprint

**Date:** 2026-03-02
**Status:** Approved
**Scope:** `stock_3pl_mainfreight` only — core platform layer untouched
**Trigger:** Pre-deployment gap analysis against https://developer.mainfreight.com

---

## Background

The integration was built against a PDF spec provided by the MF account manager. A
comparison against the current public developer portal (March 2026) identified seven
discrepancies before any live API testing. Two are deployment blockers; the rest are
medium-severity issues best fixed pre-go-live.

The polling (cron pull) architecture is retained. Cloud hosting is coming shortly at
which point the webhook receiver stub can be activated.

---

## Gaps Identified

| # | Area | Severity | Current | Public API |
|---|------|----------|---------|-----------|
| 1 | Base endpoint URL structure | 🔴 Critical | `warehouseapi[-test].mainfreight.com/api/v1.1` | `api[-test].mainfreight.com/warehousing/1.1/Customers/{Resource}` |
| 2 | `region` query parameter | 🔴 Critical | Not sent | Required since Nov 2022 |
| 3 | Webhook / subscription API | 🟠 High | Not implemented | Push webhooks for Order Confirm, Inward Confirm, Tracking Update |
| 4 | Order Update + Delete | 🟡 Medium | Not implemented | PUT + DELETE on `/Order` |
| 5 | Inward Delete | 🟡 Medium | Not implemented | DELETE on `/Inward` |
| 6 | Tracking status codes | 🟡 Medium | Flat `Status` strings only | `eventCode` values (`GoodsDelivered`, `PickedUp`, etc.) |
| 7 | Confirmation parser schema | 🟡 Medium | SCH/SCL XML (EDI spec) only | Richer webhook-style schema with parties, financials, consignments |
| 8 | Transport APIs | ⚪ Out of scope | Not implemented | Rate API + Shipment Create/Delete/Label |

Gap 8 (Transport) is out of scope: domestic transport is handled via a separate
`mml_freight_forwarder` module, and MML operates on MF's rate card so dynamic rate
queries are not required.

---

## Architecture

### What stays

- `stock_3pl_core` — entirely untouched (message queue, transport abstraction, document
  base, cron wiring, SFTP transport, REST transport base)
- Polling (cron pull) as the primary inbound path for confirmations and tracking
- All existing `x_mf_*` Odoo fields — no schema migrations required
- All existing document builders and parsers — changes are additive only

### What changes (all in `stock_3pl_mainfreight`)

- `transport/mainfreight_rest.py` — corrected URLs, region param, PUT/DELETE methods,
  dual-aware tracking status map
- `models/connector_mf.py` — new `mf_region` field (Char, default `ANZ`)
- `document/sales_order.py` — `action` param on `build_outbound`, new `build_delete_ref()`
- `document/inward_order.py` — new `build_delete_ref()`
- `document/so_confirmation.py` — dual-schema parser (SCH/SCL + webhook-style)
- `controllers/webhook.py` — new file: dormant stub HTTP controller
- `tests/` — ~25–35 new pure-Python tests across the above

---

## Detailed Design

### Gap 1+2 — Endpoint URLs and `region` Parameter

**New URL pattern:**
```
test:       https://api-test.mainfreight.com/warehousing/1.1/Customers/{Resource}?region={region}
production: https://api.mainfreight.com/warehousing/1.1/Customers/{Resource}?region={region}
```

`MF_ENDPOINTS` dict in `mainfreight_rest.py` updated to the new base URLs.

**`mf_region` field** added to `ThreePlConnectorMF` (connector_mf.py):
- Type: `fields.Char`, string `'MF Region'`, default `'ANZ'`
- Visible in connector form under Mainfreight credentials group
- Valid values per MF docs: `ANZ`, `EU`, `AMERICAS` (not enforced as Selection — leave
  as Char to avoid breakage if MF adds regions)

Every method in `MainfreightRestTransport` that calls a warehousing endpoint appends
`?region={connector.mf_region or 'ANZ'}` to the URL.

The tracking API base URL (`trackingapi[-test].mainfreight.com`) is not affected by
the region update per the public docs and is left as-is pending MF confirmation.

### Gap 3 — Webhook Receiver Stub

New file: `stock_3pl_mainfreight/controllers/webhook.py`

Three routes:
```
POST /mf/webhook/order-confirmation
POST /mf/webhook/inward-confirmation
POST /mf/webhook/tracking-update
```

Each handler:
1. Reads `X-MF-Secret` header; compares against `ir.config_parameter`
   `stock_3pl_mainfreight.webhook_secret` — returns 401 JSON if missing or wrong
2. Logs raw payload at INFO: `_logger.info('MF webhook %s received: %s', event_type, body[:500])`
3. Returns HTTP 200 `{"status": "received"}`
4. Does not process the payload
5. Marked with `# TODO: wire to inbound message queue when on cloud hosting`

Registered via `__manifest__.py` `controllers` path. No views, menus, or models.

### Gap 4+5 — Order Update+Delete, Inward Delete

**`rest_api.py` (core)** — two new methods on `RestTransport`:
- `send_put(payload, content_type, endpoint)` — PUT with same auth/error pattern as `send()`
- `send_delete(endpoint)` — DELETE with no body, same auth/error pattern

**`mainfreight_rest.py`** — three new methods on `MainfreightRestTransport`:
```python
def update_order(self, payload):
    return self.send_put(payload, content_type='xml',
                         endpoint=f'{self._get_base_url()}/Order?region={self._region()}')

def delete_order(self, order_ref):
    return self.send_delete(
        endpoint=f'{self._get_base_url()}/Order/{quote(order_ref, safe="")}?region={self._region()}')

def delete_inward(self, order_ref):
    return self.send_delete(
        endpoint=f'{self._get_base_url()}/Inward/{quote(order_ref, safe="")}?region={self._region()}')
```

**`sales_order.py`**:
- `build_outbound(order, action='create')` — adds `action=CREATE|UPDATE` attribute to
  XML root (mirrors existing InwardOrderDocument pattern)
- `build_delete_ref(order)` — returns `order.name` (the reference sent in DELETE URL)

**`inward_order.py`**:
- `build_delete_ref(booking)` — returns `booking.purchase_order_id.name or booking.name`

Cancellation trigger wiring (SO/PO cancel → delete call) is **not** in this sprint.
The transport and document methods are built and tested; the hook wiring is a follow-on
task to be scoped with the ops team (confirm whether MF deletes should be automatic or
require manual approval).

### Gap 6 — Tracking Status Code Alignment

`MF_TRACKING_STATUS_MAP` in `mainfreight_rest.py` retains existing flat `Status` entries.
A second dict `MF_EVENT_CODE_MAP` is added:

```python
MF_EVENT_CODE_MAP = {
    'GoodsDelivered':      'mf_delivered',
    'PickedUp':            'mf_dispatched',
    'InTransit':           'mf_in_transit',
    'OutForDelivery':      'mf_out_for_delivery',
    'GoodsReceived':       'mf_received',
    'DeliveryException':   'mf_exception',
}
```

`get_tracking_status()` updated:
1. Try `data.get('Status')` → `MF_TRACKING_STATUS_MAP` (existing path, no change)
2. If no match, try latest event's `code` field from `data.get('events', [])` sorted by
   `sequence` → `MF_EVENT_CODE_MAP`
3. Still returns `{}` on no match (existing behaviour preserved)

### Gap 7 — Confirmation Parser Hardening

`SOConfirmationDocument.parse_inbound()` refactored to delegate to one of two paths:

**Path A — SCH/SCL XML (existing):** detected by presence of `<SCH>` element or root
tag `<SOConfirmation>`. Existing logic unchanged.

**Path B — Webhook-style schema:** detected by presence of `orderReference` or
`customerOrderReference` at root level. Maps:
- `orderReference` or `customerOrderReference` → `reference`
- `consignments[0].consignmentNumber` → `consignment_no`
- `serviceProvider.name` → `carrier_name`
- `dateDispatched` → `finalised_date`
- `etaDate` → `eta_date`
- `orderConfirmationLines[].productCode` + `unitsFulfilled` → `lines`

Both paths produce the identical normalised dict consumed by `apply_inbound()` —
no changes to `apply_inbound()`.

For inward confirmation: a stub `parse_inbound()` Path B is added to
`SOAcknowledgementDocument` (or a new `InwardConfirmationDocument` if the schemas
diverge enough) extracting `inwardReference` and `arrivalDate`, marked
`# TODO: expand when webhook activated`.

---

## Testing

All tests are pure-Python (no Odoo), runnable via `pytest -m "not odoo_integration"`.

| Test file | Coverage added |
|-----------|---------------|
| `test_mainfreight_rest.py` | Correct URL shape; `region` param appended; PUT/DELETE methods; dual tracking status map (flat + eventCode paths) |
| `test_connector_mf.py` | `mf_region` field present; `_mf_endpoint()` includes `?region=ANZ` |
| `test_sales_order.py` | `build_outbound(action='update')` → `action=UPDATE`; `build_delete_ref()` returns order name |
| `test_inward_order_builder.py` | `build_delete_ref()` returns correct reference |
| `test_so_confirmation.py` | SCH/SCL path; webhook-schema path; both normalise to same dict |
| `test_webhook_controller.py` | Valid secret → 200; wrong/missing secret → 401 (mocked HTTP request) |

Estimated net new tests: **25–35**. No existing tests require modification.

---

## Out of Scope

- Transport Rate API, Shipment Create/Delete/Label (separate module)
- Cancellation hook wiring (SO/PO cancel → MF delete call) — methods built, triggers deferred
- Full webhook activation — stub only; activate when on cloud hosting
- Tracking API: References/Events multi-reference method — not needed for current flow

---

## Open Questions (to resolve with MF before go-live)

1. **Test environment base URL** — confirm whether test base is `api-test.mainfreight.com`
   or another hostname; validate with a real credential against `/StockOnHand`
2. **`region` values** — confirm exact string for NZ (`ANZ`? `NZ`? `NewZealand`?)
3. **Tracking poll response schema** — confirm whether `/Tracking/{connote}` still returns
   flat `{Status, PODUrl, SignedBy, DeliveredAt}` or the richer `events[]` schema
4. **SO Confirmation inbound schema** — confirm whether REST polling returns SCH/SCL XML
   or the richer webhook-style JSON/XML
