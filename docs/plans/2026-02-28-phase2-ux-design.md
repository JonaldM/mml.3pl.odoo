# Phase 2 UX Design — 3PL Operations Dashboard

**Date:** 2026-02-28
**Status:** Approved
**Scope:** `stock_3pl_mainfreight` — views, OWL components, KPI model, discrepancy model

---

## 1. Overview

Phase 2 adds an operational UX layer on top of the Phase 1 data pipeline. The primary users are:

- **Operations staff** — warehouse coordinators checking order statuses and actioning exceptions day-to-day
- **Management** — high-level KPI metrics and delivery performance trends

The design principle is **clean and functional** — this dashboard will not be checked constantly. Every screen should surface exactly what ops needs to act on, nothing more.

---

## 2. Menu Structure

A new top-level menu **"3PL Operations"** with four items:

| Menu Item | View Type | Model |
|---|---|---|
| Dashboard | OWL client action | Computed from `stock.picking` + `stock.quant` |
| Order Pipeline | Kanban | `stock.picking` |
| Exception Queue | Tree + Form | `stock.picking` |
| Inventory Discrepancy | Tree + Form | `mf.soh.discrepancy` (new) |

---

## 3. Order Pipeline (Kanban)

**View:** `stock.picking` kanban, filtered to MF-managed pickings (`x_mf_status != False`), grouped by `x_mf_status`.

**Columns (in order):**

```
Queued → Sent → Received → Dispatched → In Transit → Out for Delivery → Delivered → Exception
```

**Card content:**
- SO number
- Partner name
- Warehouse
- Number of order lines
- Days since last status change — amber >2 days, red >5 days

**Exception column:** cards always red regardless of age.

**Delivered column:** auto-folds after 7 days (configurable via `ir.config_parameter` key `stock_3pl_mainfreight.kanban_delivered_fold_days`, default `7`).

**Card actions:**
- **Retry** — visible on `mf_exception` cards only; re-queues picking to `mf_queued`
- **View** — opens picking form

**No drag-and-drop** — status transitions are driven by MF events only, not manual moves.

---

## 4. Exception Queue

### 4.1 Tree View

Filtered to `x_mf_status = mf_exception`. Columns:

| Column | Field |
|---|---|
| SO Number | `name` |
| Partner | `partner_id` |
| Warehouse | `picking_type_id.warehouse_id` |
| Connote | `x_mf_connote` |
| Exception Date | `write_date` |
| Assigned To | `x_mf_assigned_to` |
| Last Note | computed from chatter |

Default sort: exception date descending.

### 4.2 Form View

**Header action buttons:**

| Button | Action |
|---|---|
| Retry | Re-queues picking to `mf_queued`; logs chatter message |
| Reassign Warehouse | Wizard — select connector/warehouse, re-routes picking |
| Mark Resolved | Sets `x_mf_status = mf_resolved` (new terminal status); logs chatter |
| Escalate | Sends Odoo activity to configurable escalation user; tags record |

**Body:** Standard picking fields (SO, partner, lines, connote) — read-only in exception view.

**Chatter:** Full `mail.thread` — ops adds notes, system auto-logs all status transitions and retry attempts.

### 4.3 New Fields on `stock.picking`

| Field | Type | Purpose |
|---|---|---|
| `x_mf_assigned_to` | Many2one `res.users` | Exception ownership |

### 4.4 New Status Value

Add `mf_resolved` to `x_mf_status` selection — terminal, does not re-enter tracking pipeline.

---

## 5. KPI Dashboard

OWL client action (`mf_kpi_dashboard`). Two zones separated by a today-summary strip.

### 5.1 Top Zone — KPI Stat Cards (4 cards)

Each card: metric name, current value, target, RAG badge, period label.

| Card | Metric | Target Key | RAG Thresholds |
|---|---|---|---|
| **DIFOT** | % orders delivered in full on time (rolling 30 days) | `stock_3pl_mainfreight.kpi_difot_target` (default `95`) | Green ≥target, Amber target−5 to target, Red <target−5 |
| **IRA** | % SKUs where Odoo SOH = MF SOH ±tolerance (rolling 30 days) | `stock_3pl_mainfreight.kpi_ira_target` (default `98`) | Green ≥target, Amber target−3 to target, Red <target−3 |
| **Exception Rate** | % of MF orders hitting `mf_exception` (rolling 30 days) | `stock_3pl_mainfreight.kpi_exception_rate_target` (default `2`) | Green <target, Amber target to target×2.5, Red >target×2.5 |
| **In Flight** | Count of orders between `mf_sent` and `mf_dispatched` | — | No RAG — informational only |

**Edit Targets** button on dashboard → opens connector settings form directly via `ir.actions.act_window`.

### 5.2 Today Summary Strip

Four number badges (no RAG):

- Orders sent today
- Orders received today (ACK)
- Orders delivered today
- Exceptions today

### 5.3 Bottom Zone — Trend Graphs

Two Odoo native `<graph>` views embedded via OWL `<iframe>`-style action loader:

| Chart | Type | Period |
|---|---|---|
| Orders by status per week | Grouped bar | Rolling 90 days |
| DIFOT trend per week | Line | Rolling 90 days |

DIFOT trend includes a horizontal reference line at the configured target (rendered via OWL, not the graph view itself).

### 5.4 KPI Computation

All KPI values computed server-side in a new `mf.kpi.dashboard` AbstractModel with `@api.model` methods. OWL fetches via JSON-RPC `call_kw`. No stored fields — computed on demand.

**DIFOT formula:**
```
DIFOT = (orders with x_mf_status='mf_delivered' AND delivered on time AND no short-ship)
        / (all orders dispatched in period) × 100
```
"On time" = `x_mf_delivered_date` ≤ `commitment_date` (or within configurable grace days if `commitment_date` is null).

**IRA formula:**
```
IRA = (SKUs where |odoo_qty - mf_qty| / odoo_qty ≤ tolerance)
      / (total SKUs tracked) × 100
```
Tolerance configurable via `ir.config_parameter` (default `0.005` = 0.5%).

---

## 6. Inventory Discrepancy Screen

### 6.1 New Model: `mf.soh.discrepancy`

| Field | Type | Notes |
|---|---|---|
| `product_id` | Many2one `product.product` | |
| `warehouse_id` | Many2one `stock.warehouse` | |
| `odoo_qty` | Float | Snapshot at detection time |
| `mf_qty` | Float | From MF SOH API response |
| `variance_qty` | Float | `mf_qty - odoo_qty` |
| `variance_pct` | Float | `|variance_qty| / odoo_qty × 100` |
| `detected_date` | Datetime | Set by cron |
| `state` | Selection `open` / `investigated` | |
| `investigated_by` | Many2one `res.users` | Set on Mark Investigated |
| `investigated_date` | Datetime | |
| `active` | Boolean | Auto-archive after 90 days |

**Auto-archive:** scheduled action sets `active=False` on records where `detected_date < now - 90 days`. Configurable via `ir.config_parameter` (`stock_3pl_mainfreight.discrepancy_archive_days`, default `90`).

### 6.2 Tree View

Summary strip at top: total SKUs tracked, SKUs with discrepancy, current IRA %.

Columns: Product code, Product name, Warehouse, Odoo SOH, MF SOH, Variance (units), Variance %, Detected date, State.

### 6.3 Form View

Read-only fields. One action button: **Mark Investigated** — sets `state = investigated`, stamps `investigated_by` and `investigated_date`, logs a chatter note.

No quantity edits — ops adjusts Odoo quants directly in Inventory after investigation.

### 6.4 Population

The existing `MFInboundCron._poll_inventory_reports()` is extended to write `mf.soh.discrepancy` records when SOH drift exceeds tolerance. Replaces the current log-only behaviour.

---

## 7. New `ir.config_parameter` Keys

| Key | Default | Purpose |
|---|---|---|
| `stock_3pl_mainfreight.kpi_difot_target` | `95` | DIFOT target % |
| `stock_3pl_mainfreight.kpi_ira_target` | `98` | IRA target % |
| `stock_3pl_mainfreight.kpi_exception_rate_target` | `2` | Exception rate target % |
| `stock_3pl_mainfreight.kanban_delivered_fold_days` | `7` | Days before Delivered column folds |
| `stock_3pl_mainfreight.discrepancy_archive_days` | `90` | Days before discrepancy records archive |
| `stock_3pl_mainfreight.difot_grace_days` | `0` | Grace days for on-time calculation |
| `stock_3pl_mainfreight.ira_tolerance` | `0.005` | SOH match tolerance (0.5%) |

---

## 8. Frontend Design Review

Before implementation, invoke the `frontend-design` skill as a logic and UX review step. It should:
- Validate component structure and OWL patterns
- Flag any UX logic gaps (e.g. edge cases in RAG thresholds, empty states)
- Suggest improvements before code is written

---

## 9. Implementation Approach

**Option B selected:** Native Odoo kanban/tree/form views + custom OWL stat widget components for KPI cards. No external JS dependencies.

**New files required (indicative):**
- `models/kpi_dashboard.py` — AbstractModel, KPI computation methods
- `models/soh_discrepancy.py` — `mf.soh.discrepancy` model
- `static/src/js/mf_kpi_dashboard.js` — OWL dashboard component
- `static/src/xml/mf_kpi_dashboard.xml` — OWL template
- `views/pipeline_views.xml` — kanban view
- `views/exception_views.xml` — tree + form (extends existing)
- `views/discrepancy_views.xml` — tree + form
- `views/kpi_dashboard_action.xml` — client action
- `views/menu_phase2.xml` — "3PL Operations" menu

**Testing:**
- Pure-Python tests for all KPI computation methods
- OWL component tested via Odoo's JS test runner
- Integration tests for discrepancy population from SOH cron

---

## 10. Out of Scope (Phase 3+)

- Email/SMS alerts when KPI breaches threshold
- PDF export of KPI report
- Per-brand or per-carrier KPI breakdown
- Webhook/push notifications from MF
