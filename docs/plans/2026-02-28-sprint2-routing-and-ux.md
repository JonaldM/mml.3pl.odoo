# Sprint 2: Warehouse Routing Engine + Full UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Build the multi-warehouse routing engine (haversine distance, stock check, split logic, cross-border hold) and the full operational UX (picking status views, exception queues, connector dashboard, order pipeline).

**Architecture:** The routing engine runs as a pre-processing step in the MF push cron — all routing decisions in one place, easier to debug. Two service models (`mf.route.engine`, `mf.split.engine`) are AbstractModel (no stored state, no ACL needed). Custom fields extend existing Odoo models via `_inherit`. UX is split between `stock_3pl_mainfreight` (picking views, exception queues, pipeline) and `stock_3pl_core` (connector kanban enhancement). Note: V3 scope doc references Odoo 19 — this project is **Odoo 15**; use Odoo 15 APIs and `attrs` domain-list syntax throughout.

**Tech Stack:** Odoo 15, Python 3.8+, `math` stdlib (haversine — no external API needed), `stock.quant` ORM (availability checks), `stock.picking` ORM (split via `move_lines.write`), Odoo QWeb kanban

**Scope doc:** `mainfreight_integration_scopeV3.md` — Section 5 (Multi-Warehouse Dispatch Routing)
**Design doc:** `docs/plans/2026-02-28-3pl-integration-platform-design.md`

---

## Prerequisites

Complete before starting Sprint 2:
- Sprint 1 Tasks 10-19 complete (`stock_3pl_mainfreight` module built, `x_mf_status` already on `stock.picking`, push cron wired)
- At least two MF-enabled warehouses available for integration tests

---

## Routing Algorithm (from V3 Section 5.2)

```
For each confirmed sale.order:
1. Get customer delivery lat/lng (res.partner.partner_latitude / partner_longitude)
2. Get all x_mf_enabled warehouses sorted by haversine distance (closest first)
3. Greedy assignment: for each warehouse, check available stock for remaining lines
   - Single-line order: try to fulfill completely at closest warehouse; if not, move to next
   - Multi-line order: partial fulfillment allowed — assign what each warehouse can cover
4. Cross-border check: warehouse country ≠ delivery country → x_mf_cross_border=True, hold
5. Result: list of {warehouse, lines} dicts — one per warehouse needed
```

---

## PHASE 1 — Warehouse Routing Engine

---

### Task 1: Custom fields — stock.warehouse (lat/lng + MF-enabled flag)

**Context:** The routing engine sorts warehouses by haversine distance. Geographic coordinates must be stored on `stock.warehouse`. These are MF-specific fields so they go in `stock_3pl_mainfreight`, not core. Check if `warehouse_mf.py` already exists from Sprint 1 Task 10 — if so, ADD fields to the existing class rather than creating a duplicate `_inherit`.

**Files:**
- Create/Modify: `addons/stock_3pl_mainfreight/models/warehouse_mf.py`
- Modify: `addons/stock_3pl_mainfreight/models/__init__.py` (add import if not present)
- Test: `addons/stock_3pl_mainfreight/tests/test_routing_fields.py`

**Step 1: Write the failing test**

```python
# addons/stock_3pl_mainfreight/tests/test_routing_fields.py
from odoo.tests import tagged, TransactionCase


@tagged('post_install', '-at_install', 'routing')
class TestWarehouseFields(TransactionCase):

    def test_warehouse_has_lat_lng(self):
        wh = self.env['stock.warehouse'].search([], limit=1)
        self.assertIsNotNone(wh.x_mf_latitude)
        self.assertIsNotNone(wh.x_mf_longitude)
        self.assertIsNotNone(wh.x_mf_enabled)

    def test_warehouse_can_set_coordinates(self):
        wh = self.env['stock.warehouse'].search([], limit=1)
        wh.write({
            'x_mf_latitude': -37.7870,
            'x_mf_longitude': 175.2793,
            'x_mf_enabled': True,
        })
        self.assertAlmostEqual(wh.x_mf_latitude, -37.7870, places=4)
        self.assertAlmostEqual(wh.x_mf_longitude, 175.2793, places=4)
        self.assertTrue(wh.x_mf_enabled)
```

**Step 2: Run to confirm FAIL**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestWarehouseFields
```
Expected: `AttributeError` — field does not exist yet

**Step 3: Implement `warehouse_mf.py`**

If the file does not exist, create it. If it already exists (from Sprint 1 Task 10), add only the new fields to the existing class — do not duplicate `_inherit`.

```python
# addons/stock_3pl_mainfreight/models/warehouse_mf.py
from odoo import models, fields


class StockWarehouseMF(models.Model):
    _inherit = 'stock.warehouse'

    x_mf_enabled = fields.Boolean(
        'MF-Managed Warehouse',
        default=False,
        help='Include this warehouse in Mainfreight routing and push logic.',
    )
    x_mf_latitude = fields.Float('Latitude', digits=(9, 6))
    x_mf_longitude = fields.Float('Longitude', digits=(9, 6))
    # x_mf_warehouse_code and x_mf_customer_id are added in Sprint 1 Task 10 —
    # do NOT re-declare them here.
```

**Step 4: Update `__init__.py` if needed**

```python
# In addons/stock_3pl_mainfreight/models/__init__.py — add if not already present:
from . import warehouse_mf
```

**Step 5: Run tests — confirm PASS**

**Step 6: Commit**

```bash
git add addons/stock_3pl_mainfreight/models/warehouse_mf.py \
        addons/stock_3pl_mainfreight/models/__init__.py \
        addons/stock_3pl_mainfreight/tests/test_routing_fields.py
git commit -m "feat(mf): add x_mf_latitude/longitude/enabled to stock.warehouse for routing"
```

---

### Task 2: Custom fields — stock.picking (routing metadata)

**Context:** Each picking needs to record how it was routed (`x_mf_routed_by`) and whether it was flagged as cross-border (`x_mf_cross_border`). These extend the existing `picking_mf.py` from Sprint 1 Task 16 (which already has `x_mf_status`, `x_mf_sent`, etc.). Add fields to the existing class — do NOT create a second `_inherit` of `stock.picking`.

**Files:**
- Modify: `addons/stock_3pl_mainfreight/models/picking_mf.py`
- Test: `addons/stock_3pl_mainfreight/tests/test_routing_fields.py` (extend)

**Step 1: Add tests**

```python
# Add to test_routing_fields.py:
class TestPickingRoutingFields(TransactionCase):

    def test_picking_has_routed_by(self):
        picking = self.env['stock.picking'].search([], limit=1)
        # Field exists — default is False/None
        self.assertFalse(picking.x_mf_routed_by)

    def test_picking_cross_border_default_false(self):
        picking = self.env['stock.picking'].search([], limit=1)
        self.assertFalse(picking.x_mf_cross_border)

    def test_picking_routed_by_accepts_valid_values(self):
        picking = self.env['stock.picking'].search([], limit=1)
        for val in ('manual', 'auto_closest', 'auto_split'):
            picking.write({'x_mf_routed_by': val})
            self.assertEqual(picking.x_mf_routed_by, val)
```

**Step 2: Add fields to `picking_mf.py`**

Find the existing `StockPickingMF` class and add alongside the existing fields:

```python
# Add these constants at the top of picking_mf.py:
MF_ROUTED_BY = [
    ('manual', 'Manual'),
    ('auto_closest', 'Auto — Closest Warehouse'),
    ('auto_split', 'Auto — Split Order'),
]

# Add these fields to the existing StockPickingMF class:
x_mf_routed_by = fields.Selection(
    MF_ROUTED_BY,
    'Routing Method',
    readonly=True,
    help='How this picking was assigned to its warehouse.',
)
x_mf_cross_border = fields.Boolean(
    'Cross-Border',
    default=False,
    help='Warehouse country differs from delivery country — held for manual approval.',
)
```

**Step 3: Run tests — confirm PASS. Commit.**

```bash
git add addons/stock_3pl_mainfreight/models/picking_mf.py \
        addons/stock_3pl_mainfreight/tests/test_routing_fields.py
git commit -m "feat(mf): add x_mf_routed_by and x_mf_cross_border fields to stock.picking"
```

---

### Task 3: Custom fields — sale.order (split flag)

**Context:** When a sale order routes to multiple warehouses, this boolean records that fact. Used by UX to flag split orders.

**Files:**
- Create: `addons/stock_3pl_mainfreight/models/sale_order_mf.py`
- Modify: `addons/stock_3pl_mainfreight/models/__init__.py`
- Test: `addons/stock_3pl_mainfreight/tests/test_routing_fields.py` (extend)

**Step 1: Add test**

```python
class TestSaleOrderSplitField(TransactionCase):

    def test_sale_order_has_split_flag(self):
        order = self.env['sale.order'].search([], limit=1)
        self.assertFalse(order.x_mf_split)  # exists and defaults to False
```

**Step 2: Implement**

```python
# addons/stock_3pl_mainfreight/models/sale_order_mf.py
from odoo import models, fields


class SaleOrderMF(models.Model):
    _inherit = 'sale.order'

    x_mf_split = fields.Boolean(
        'Split Across Warehouses',
        default=False,
        readonly=True,
        help='Order was routed to multiple MF warehouses. Multiple pickings will be pushed to MF independently.',
    )
```

**Step 3: Add import**

```python
# In models/__init__.py:
from . import sale_order_mf
```

**Step 4: Run test — PASS. Commit.**

```bash
git add addons/stock_3pl_mainfreight/models/sale_order_mf.py \
        addons/stock_3pl_mainfreight/models/__init__.py \
        addons/stock_3pl_mainfreight/tests/test_routing_fields.py
git commit -m "feat(mf): add x_mf_split flag to sale.order"
```

---

### Task 4: Haversine distance utility

**Context:** Pure Python distance calculation — no external API, no Odoo ORM. Lives in a `utils/` package so it can be unit-tested without a running Odoo database. The formula is accurate to ~0.5% for distances below 10,000 km (more than adequate for NZ/AU routing).

**Files:**
- Create: `addons/stock_3pl_mainfreight/utils/__init__.py` (empty)
- Create: `addons/stock_3pl_mainfreight/utils/haversine.py`
- Test: `addons/stock_3pl_mainfreight/tests/test_haversine.py`

**Step 1: Write the failing test**

```python
# addons/stock_3pl_mainfreight/tests/test_haversine.py
from odoo.tests import tagged, TransactionCase
from odoo.addons.stock_3pl_mainfreight.utils.haversine import haversine_km, sort_warehouses_by_distance


@tagged('post_install', '-at_install', 'routing')
class TestHaversine(TransactionCase):

    def test_same_point_is_zero(self):
        self.assertAlmostEqual(
            haversine_km(-37.787, 175.279, -37.787, 175.279), 0.0, places=2
        )

    def test_hamilton_to_christchurch_approx_750km(self):
        # Hamilton NZ (-37.7870, 175.2793) → Christchurch NZ (-43.5321, 172.6362)
        km = haversine_km(-37.7870, 175.2793, -43.5321, 172.6362)
        self.assertGreater(km, 700)
        self.assertLess(km, 800)

    def test_sort_returns_closest_first(self):
        # Customer near Hamilton
        customer_lat, customer_lng = -37.0, 175.0
        warehouses = [
            {'id': 'chc', 'lat': -43.5321, 'lng': 172.6362},  # Christchurch ~760km
            {'id': 'ham', 'lat': -37.7870, 'lng': 175.2793},  # Hamilton ~100km
        ]
        sorted_wh = sort_warehouses_by_distance(customer_lat, customer_lng, warehouses)
        self.assertEqual(sorted_wh[0]['id'], 'ham')

    def test_sort_empty_list_returns_empty(self):
        result = sort_warehouses_by_distance(-37.0, 175.0, [])
        self.assertEqual(result, [])
```

**Step 2: Run to confirm FAIL**

Expected: `ImportError` — module does not exist

**Step 3: Implement**

```python
# addons/stock_3pl_mainfreight/utils/haversine.py
from math import radians, cos, sin, asin, sqrt


def haversine_km(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in kilometres between two points.

    Uses the haversine formula. Accurate to ~0.5% for distances < 10,000 km.
    All inputs in decimal degrees.
    """
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def sort_warehouses_by_distance(customer_lat, customer_lng, warehouses):
    """Sort a list of warehouse dicts by distance to the customer (closest first).

    Each dict must have 'lat' and 'lng' keys. Returns a NEW sorted list;
    does not modify the original.
    """
    return sorted(
        warehouses,
        key=lambda wh: haversine_km(customer_lat, customer_lng, wh['lat'], wh['lng']),
    )
```

**Step 4: Create empty `utils/__init__.py`**

**Step 5: Run tests — confirm all 4 PASS**

**Step 6: Commit**

```bash
git add addons/stock_3pl_mainfreight/utils/ \
        addons/stock_3pl_mainfreight/tests/test_haversine.py
git commit -m "feat(mf): haversine distance utility for warehouse routing"
```

---

### Task 5: Routing engine — warehouse selection and stock check

**Context:** The core routing model. Given a confirmed `sale.order`, returns a list of warehouse assignments: `[{'warehouse': wh, 'lines': [(product, qty)]}, ...]`. No pickings are created here — that's Task 6. Two edge-case rules from V3 Section 5.6:
- Single-line order: prefer complete fulfilment at one warehouse — try each warehouse in order before splitting
- Customer has no lat/lng: fall back to closest warehouse by `x_mf_warehouse_code` sort order (or first enabled), log warning

`mf.route.engine` is an `AbstractModel` — no stored fields, no database table, no ACL entry needed.

**Files:**
- Create: `addons/stock_3pl_mainfreight/models/route_engine.py`
- Modify: `addons/stock_3pl_mainfreight/models/__init__.py`
- Test: `addons/stock_3pl_mainfreight/tests/test_route_engine.py`

**Step 1: Write the failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_route_engine.py
from odoo.tests import tagged, TransactionCase
from odoo.exceptions import UserError


@tagged('post_install', '-at_install', 'routing')
class TestRouteEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.engine = self.env['mf.route.engine']
        self.wh = self.env['stock.warehouse'].search([], limit=1)
        self.wh.write({
            'x_mf_enabled': True,
            'x_mf_latitude': -37.7870,
            'x_mf_longitude': 175.2793,
        })

    def test_get_mf_warehouses_only_enabled(self):
        # Disable all then re-enable one
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})
        self.wh.x_mf_enabled = True
        result = self.engine._get_mf_warehouses()
        self.assertEqual(len(result), 1)
        self.assertTrue(result.x_mf_enabled)

    def test_no_mf_warehouses_raises_user_error(self):
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order:
            return  # No confirmed orders in test DB — skip
        with self.assertRaises(UserError):
            self.engine.route_order(order)

    def test_check_stock_returns_dict_keyed_by_product(self):
        product = self.env['product.product'].search(
            [('type', '=', 'product')], limit=1
        )
        if not product:
            return
        result = self.engine._check_stock(self.wh, [(product, 1.0)])
        self.assertIn(product, result)
        self.assertIsInstance(result[product], float)

    def test_order_lines_returns_only_storable(self):
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order:
            return
        lines = self.engine._order_lines(order)
        for product, qty in lines:
            self.assertEqual(product.type, 'product')
            self.assertGreater(qty, 0)
```

**Step 2: Run to confirm FAIL**

Expected: `KeyError` — model not in registry

**Step 3: Implement `route_engine.py`**

```python
# addons/stock_3pl_mainfreight/models/route_engine.py
import logging
from odoo import models, api
from odoo.exceptions import UserError
from odoo.addons.stock_3pl_mainfreight.utils.haversine import sort_warehouses_by_distance

_logger = logging.getLogger(__name__)


class MFRouteEngine(models.AbstractModel):
    """Service model for Mainfreight warehouse routing decisions.

    AbstractModel — no stored fields, no database table, no ACL needed.
    Call via: self.env['mf.route.engine'].route_order(order)

    Routing algorithm (V3 Section 5.2):
    - Sort MF-enabled warehouses by haversine distance to customer
    - Greedy assignment: for each warehouse, check available stock
    - Single-line orders: prefer complete fulfilment at one warehouse
    - Multi-line orders: partial assignment across warehouses is allowed
    """
    _name = 'mf.route.engine'
    _description = 'MF Warehouse Routing Engine'

    @api.model
    def route_order(self, order):
        """Assign sale.order lines to MF warehouses.

        Returns a list of assignment dicts:
            [{'warehouse': stock.warehouse, 'lines': [(product.product, qty)]}, ...]

        One dict per warehouse. If the full order can be fulfilled from one
        warehouse, returns a single-element list.

        Raises UserError if no MF warehouses are configured.
        """
        warehouses = self._get_mf_warehouses()
        if not warehouses:
            raise UserError(
                'No MF-enabled warehouses configured. '
                'Enable at least one warehouse under Inventory → Warehouses.'
            )

        partner = order.partner_shipping_id or order.partner_id
        cust_lat = partner.partner_latitude
        cust_lng = partner.partner_longitude

        if not cust_lat or not cust_lng:
            _logger.warning(
                'route_order: partner %s (order %s) has no lat/lng — '
                'assigning to first MF warehouse: %s',
                partner.name, order.name, warehouses[0].name,
            )
            return [{'warehouse': warehouses[0], 'lines': self._order_lines(order)}]

        # Sort warehouses by distance to customer
        wh_data = [
            {'warehouse': wh, 'lat': wh.x_mf_latitude, 'lng': wh.x_mf_longitude}
            for wh in warehouses
        ]
        sorted_wh = sort_warehouses_by_distance(cust_lat, cust_lng, wh_data)

        lines = self._order_lines(order)
        is_single_line = len(lines) == 1

        if is_single_line:
            # For single-line orders: prefer one warehouse that can fill completely
            product, qty = lines[0]
            for wh_entry in sorted_wh:
                wh = wh_entry['warehouse']
                stock = self._check_stock(wh, [(product, qty)])
                if stock.get(product, 0.0) >= qty:
                    return [{'warehouse': wh, 'lines': [(product, qty)]}]
            # No single warehouse can fill — split across closest warehouses
            _logger.warning(
                'route_order: single-line order %s cannot be fulfilled completely '
                'at any single warehouse — splitting across warehouses.',
                order.name,
            )

        # Multi-line (or single-line fallback): greedy assignment
        remaining = list(lines)
        assignments = []

        for wh_entry in sorted_wh:
            if not remaining:
                break
            wh = wh_entry['warehouse']
            stock = self._check_stock(wh, remaining)
            fulfillable = []
            still_remaining = []
            for product, qty in remaining:
                avail = stock.get(product, 0.0)
                if avail >= qty:
                    fulfillable.append((product, qty))
                elif avail > 0:
                    fulfillable.append((product, avail))
                    still_remaining.append((product, qty - avail))
                else:
                    still_remaining.append((product, qty))
            if fulfillable:
                assignments.append({'warehouse': wh, 'lines': fulfillable})
            remaining = still_remaining

        if remaining:
            _logger.warning(
                'route_order: order %s — %d product(s) could not be assigned '
                'to any MF warehouse (insufficient stock)',
                order.name, len(remaining),
            )

        return assignments

    @api.model
    def _get_mf_warehouses(self):
        """Return all MF-enabled warehouses, ordered by name for determinism."""
        return self.env['stock.warehouse'].search(
            [('x_mf_enabled', '=', True)],
            order='name',
        )

    @api.model
    def _check_stock(self, warehouse, lines):
        """Return {product: available_qty} at the warehouse stock location.

        Uses Odoo stock.quant (internal stock). Phase 2 will add MF SOH API
        cross-check if drift between Odoo and MF becomes a problem.
        """
        location = warehouse.lot_stock_id
        result = {}
        for product, _qty in lines:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', 'child_of', location.id),
            ])
            result[product] = sum(quants.mapped('quantity'))
        return result

    @api.model
    def _order_lines(self, order):
        """Extract (product, qty) tuples from a sale order.

        Returns only storable products (type='product'). Service/consumable
        lines are excluded — MF only manages physical stock.
        """
        return [
            (line.product_id, line.product_uom_qty)
            for line in order.order_line
            if line.product_id and line.product_id.type == 'product'
        ]
```

**Step 4: Add to `__init__.py`**

```python
from . import route_engine
```

**Step 5: Run tests — confirm all 4 PASS**

**Step 6: Commit**

```bash
git add addons/stock_3pl_mainfreight/models/route_engine.py \
        addons/stock_3pl_mainfreight/models/__init__.py \
        addons/stock_3pl_mainfreight/tests/test_route_engine.py
git commit -m "feat(mf): warehouse routing engine — haversine + greedy stock assignment"
```

---

### Task 6: Split order engine — create separate pickings per warehouse

**Context:** Takes the routing assignments from Task 5 and modifies Odoo pickings to match. The first assignment reuses the existing `stock.picking` (reassigning moves to it). Additional assignments get new pickings by moving `stock.move` records via `write({'picking_id': new_id})` — do NOT use backorder logic (that's for partial validation). Sets `x_mf_routed_by` and `x_mf_cross_border` on each picking.

Cross-border rule (V3 Section 5.7): compare `warehouse.partner_id.country_id` vs `picking.partner_id.country_id`. Simple country code comparison.

**Files:**
- Create: `addons/stock_3pl_mainfreight/models/split_engine.py`
- Modify: `addons/stock_3pl_mainfreight/models/__init__.py`
- Test: `addons/stock_3pl_mainfreight/tests/test_split_engine.py`

**Step 1: Write the failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_split_engine.py
from odoo.tests import tagged, TransactionCase


@tagged('post_install', '-at_install', 'routing')
class TestSplitEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.engine = self.env['mf.split.engine']
        self.wh = self.env['stock.warehouse'].search([], limit=1)

    def test_is_cross_border_same_country(self):
        picking = self.env['stock.picking'].search([], limit=1)
        if not picking or not self.wh:
            return
        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        if not nz:
            return
        self.wh.partner_id.country_id = nz
        picking.partner_id.country_id = nz
        self.assertFalse(self.engine._is_cross_border(self.wh, picking))

    def test_is_cross_border_different_country(self):
        picking = self.env['stock.picking'].search([], limit=1)
        if not picking or not self.wh:
            return
        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        au = self.env['res.country'].search([('code', '=', 'AU')], limit=1)
        if not nz or not au:
            return
        self.wh.partner_id.country_id = nz
        picking.partner_id.country_id = au
        self.assertTrue(self.engine._is_cross_border(self.wh, picking))

    def test_is_cross_border_missing_country_returns_false(self):
        picking = self.env['stock.picking'].search([], limit=1)
        if not picking or not self.wh:
            return
        self.wh.partner_id.country_id = False
        picking.partner_id.country_id = False
        self.assertFalse(self.engine._is_cross_border(self.wh, picking))
```

**Step 2: Run to confirm FAIL**

**Step 3: Implement `split_engine.py`**

```python
# addons/stock_3pl_mainfreight/models/split_engine.py
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class MFSplitEngine(models.AbstractModel):
    """Applies routing assignments to Odoo pickings.

    AbstractModel — no stored fields. Call via self.env['mf.split.engine'].
    """
    _name = 'mf.split.engine'
    _description = 'MF Split Order Engine'

    @api.model
    def apply_routing(self, order, assignments):
        """Apply routing assignments to an order by adjusting pickings.

        Takes the list of dicts from MFRouteEngine.route_order() and modifies
        Odoo pickings so each warehouse has its own picking with the correct moves.

        Sets x_mf_routed_by, x_mf_cross_border, and x_mf_status on each picking.
        Sets x_mf_split=True on the sale.order if more than one assignment.

        Returns a recordset of all affected pickings.
        """
        if not assignments:
            return self.env['stock.picking']

        is_split = len(assignments) > 1
        if is_split:
            order.write({'x_mf_split': True})

        routed_by = 'auto_split' if is_split else 'auto_closest'
        all_pickings = self.env['stock.picking']

        # Collect existing unrouted outbound pickings for this order
        unrouted = order.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel') and not p.x_mf_routed_by
        )

        for i, assignment in enumerate(assignments):
            wh = assignment['warehouse']
            line_products = {product for product, _ in assignment['lines']}

            if i == 0 and unrouted:
                # Reuse the first existing picking for the first assignment
                picking = unrouted[0]
                # Move any moves NOT in this assignment to a new picking
                moves_to_move = picking.move_lines.filtered(
                    lambda m: m.product_id not in line_products
                )
                if moves_to_move:
                    new_picking = picking.copy({'move_lines': []})
                    moves_to_move.write({'picking_id': new_picking.id})
                    _logger.info(
                        'split_engine: moved %d move(s) from picking %s to new picking %s',
                        len(moves_to_move), picking.name, new_picking.name,
                    )
            else:
                # Create a new picking for subsequent assignments
                picking_type = wh.out_type_id
                picking = self.env['stock.picking'].create({
                    'picking_type_id': picking_type.id,
                    'origin': order.name,
                    'partner_id': (order.partner_shipping_id or order.partner_id).id,
                    'location_id': wh.lot_stock_id.id,
                    'location_dest_id': (
                        order.partner_shipping_id or order.partner_id
                    ).property_stock_customer.id,
                })
                # Move the relevant moves to this picking
                moves = self.env['stock.picking'].search([
                    ('sale_id', '=', order.id),
                    ('state', 'not in', ('done', 'cancel')),
                ]).move_lines.filtered(
                    lambda m: m.product_id in line_products and not m.picking_id.x_mf_routed_by
                )
                moves.write({'picking_id': picking.id})

            is_cross_border = self._is_cross_border(wh, picking)
            mf_status = 'mf_held_review' if is_cross_border else 'mf_queued'
            picking.write({
                'x_mf_routed_by': routed_by,
                'x_mf_cross_border': is_cross_border,
                'x_mf_status': mf_status,
            })
            all_pickings |= picking
            _logger.info(
                'split_engine: picking %s → warehouse %s, cross_border=%s, status=%s',
                picking.name, wh.name, is_cross_border, mf_status,
            )

        return all_pickings

    @api.model
    def _is_cross_border(self, warehouse, picking):
        """Return True if warehouse country differs from delivery country.

        Uses warehouse.partner_id.country_id vs picking.partner_id.country_id.
        Returns False if either country is not set (fail-open: do not block).
        """
        wh_country = warehouse.partner_id.country_id
        dest_country = picking.partner_id.country_id
        if not wh_country or not dest_country:
            return False
        return wh_country.id != dest_country.id
```

**Step 4: Add to `__init__.py`**

```python
from . import split_engine
```

**Step 5: Run tests — confirm PASS. Commit.**

```bash
git add addons/stock_3pl_mainfreight/models/split_engine.py \
        addons/stock_3pl_mainfreight/models/__init__.py \
        addons/stock_3pl_mainfreight/tests/test_split_engine.py
git commit -m "feat(mf): split order engine — warehouse picking split + cross-border detection"
```

---

### Task 7: Cross-border manual release button

**Context:** Cross-border pickings sit in `mf_held_review`. An authorised user clicks "Approve Cross-Border Dispatch" to advance them to `mf_queued` (and the next push cron run includes them). The button and method go on `stock.picking`. The UX button is added to the view in Task 9.

**Files:**
- Modify: `addons/stock_3pl_mainfreight/models/picking_mf.py`
- Test: `addons/stock_3pl_mainfreight/tests/test_split_engine.py` (extend)

**Step 1: Add tests**

```python
def test_approve_cross_border_advances_to_queued(self):
    picking = self.env['stock.picking'].search([], limit=1)
    if not picking:
        return
    picking.write({'x_mf_status': 'mf_held_review', 'x_mf_cross_border': True})
    picking.action_approve_cross_border()
    self.assertEqual(picking.x_mf_status, 'mf_queued')

def test_approve_cross_border_rejects_wrong_state(self):
    picking = self.env['stock.picking'].search([], limit=1)
    if not picking:
        return
    picking.write({'x_mf_status': 'mf_sent'})
    from odoo.exceptions import UserError
    with self.assertRaises(UserError):
        picking.action_approve_cross_border()
```

**Step 2: Add method to `picking_mf.py`**

```python
# Add import at top:
from odoo.exceptions import UserError

# Add method to StockPickingMF class:
def action_approve_cross_border(self):
    """Release a cross-border held picking for MF push.

    Only valid from mf_held_review status. Advances to mf_queued
    so the next push cron run will include this picking.
    Can be called on a recordset — validates each picking individually.
    """
    for picking in self:
        if picking.x_mf_status != 'mf_held_review':
            raise UserError(
                f'{picking.name} is not in cross-border held status '
                f'(current: {picking.x_mf_status or "not set"}).'
            )
    self.write({'x_mf_status': 'mf_queued'})
```

**Step 3: Run tests — confirm PASS. Commit.**

```bash
git add addons/stock_3pl_mainfreight/models/picking_mf.py \
        addons/stock_3pl_mainfreight/tests/test_split_engine.py
git commit -m "feat(mf): action_approve_cross_border on stock.picking for manual cross-border release"
```

---

### Task 8: Wire routing into the MF push cron

**Context:** The routing pre-processor runs before the push cron collects eligible pickings. It finds confirmed sale orders without routing, routes them, and creates the correct picking assignments. Then the existing push cron picks up `x_mf_status='mf_queued'` pickings.

Add `_route_pending_orders()` to the push cron model from Sprint 1 Task 16. Call it at the START of the push cron method, before the SFTP push loop.

**Files:**
- Modify: `addons/stock_3pl_mainfreight/models/push_cron.py` (or wherever push cron lives from Sprint 1)
- Test: `addons/stock_3pl_mainfreight/tests/test_push_cron.py` (extend if exists)

**Step 1: Add test**

```python
def test_route_pending_skips_already_routed_pickings(self):
    """Already-routed pickings must not be re-processed."""
    picking = self.env['stock.picking'].search([], limit=1)
    if not picking:
        return
    picking.write({'x_mf_routed_by': 'auto_closest', 'x_mf_status': 'mf_queued'})
    # Run pre-processor — should leave this picking unchanged
    self.env['mf.push.cron']._route_pending_orders()
    picking.invalidate_recordset()
    self.assertEqual(picking.x_mf_routed_by, 'auto_closest')
```

**Step 2: Add `_route_pending_orders` to the push cron model**

```python
# In the push cron model (Sprint 1 Task 16 file):
import logging
_logger = logging.getLogger(__name__)

@api.model
def _route_pending_orders(self):
    """Route confirmed sale orders that have not yet been assigned to a warehouse.

    Runs as the first step of the MF push cron. Finds all unrouted pickings
    (x_mf_routed_by is not set), groups them by sale order, routes each order,
    and applies the routing (split + cross-border check).

    Skips orders that already have all pickings routed.
    Logs errors per-order and continues — one bad order must not block the batch.
    """
    route_engine = self.env['mf.route.engine']
    split_engine = self.env['mf.split.engine']

    unrouted_pickings = self.env['stock.picking'].search([
        ('x_mf_routed_by', '=', False),
        ('state', 'in', ('confirmed', 'assigned', 'waiting')),
    ])
    orders = unrouted_pickings.mapped('sale_id').filtered(
        lambda o: o.id and o.state == 'sale'
    )

    for order in orders:
        try:
            assignments = route_engine.route_order(order)
            split_engine.apply_routing(order, assignments)
        except Exception as e:
            _logger.error(
                '_route_pending_orders: routing failed for order %s: %s',
                order.name, e,
            )
```

**Step 3: Call `_route_pending_orders()` at the start of the push cron method**

In the existing push cron method (from Sprint 1), add as the very first line:
```python
self._route_pending_orders()
```

**Step 4: Run tests. Commit.**

```bash
git add addons/stock_3pl_mainfreight/models/push_cron.py \
        addons/stock_3pl_mainfreight/tests/test_push_cron.py
git commit -m "feat(mf): integrate routing pre-processor into MF push cron"
```

---

## PHASE 2 — Full UX

---

### Task 9: stock.picking view enhancements

**Context:** Add MF status, cross-border badge, and approval button to the existing `stock.picking` form and tree views using `inherit_id`. No new menu. Uses Odoo 15 `attrs` domain-list syntax.

**Files:**
- Create: `addons/stock_3pl_mainfreight/views/picking_mf_views.xml`
- Modify: `addons/stock_3pl_mainfreight/__manifest__.py` (add to data list)

**Step 1: Create `picking_mf_views.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!-- Extend stock.picking FORM view -->
    <record id="view_stock_picking_mf_form" model="ir.ui.view">
        <field name="name">stock.picking.mf.form</field>
        <field name="model">stock.picking</field>
        <field name="inherit_id" ref="stock.view_picking_form"/>
        <field name="arch" type="xml">

            <!-- Cross-border release button in header -->
            <xpath expr="//header" position="inside">
                <button name="action_approve_cross_border"
                        type="object"
                        string="Approve Cross-Border Dispatch"
                        class="btn-warning"
                        attrs="{'invisible': [('x_mf_status', '!=', 'mf_held_review')]}"/>
            </xpath>

            <!-- MF status statusbar (only shown when x_mf_status is set) -->
            <xpath expr="//field[@name='state']" position="after">
                <field name="x_mf_status" widget="statusbar"
                       statusbar_visible="mf_queued,mf_sent,mf_received,mf_dispatched,mf_delivered"
                       attrs="{'invisible': [('x_mf_status', '=', False)]}"/>
            </xpath>

            <!-- MF details group — only shown when this picking has MF routing -->
            <xpath expr="//page[@name='additional_info']" position="before">
                <page string="Mainfreight"
                      attrs="{'invisible': [('x_mf_status', '=', False)]}">
                    <group>
                        <group>
                            <field name="x_mf_status"/>
                            <field name="x_mf_routed_by"/>
                            <field name="x_mf_cross_border"/>
                        </group>
                        <group>
                            <field name="x_mf_connote"
                                   attrs="{'invisible': [('x_mf_connote', '=', False)]}"/>
                            <field name="x_mf_dispatched_date"
                                   attrs="{'invisible': [('x_mf_dispatched_date', '=', False)]}"/>
                            <field name="x_mf_delivered_date"
                                   attrs="{'invisible': [('x_mf_delivered_date', '=', False)]}"/>
                            <field name="x_mf_pod_url"
                                   attrs="{'invisible': [('x_mf_pod_url', '=', False)]}"/>
                        </group>
                    </group>
                </page>
            </xpath>

        </field>
    </record>

    <!-- Extend stock.picking TREE view -->
    <record id="view_stock_picking_mf_tree" model="ir.ui.view">
        <field name="name">stock.picking.mf.tree</field>
        <field name="model">stock.picking</field>
        <field name="inherit_id" ref="stock.vpicktree"/>
        <field name="arch" type="xml">
            <xpath expr="//field[@name='state']" position="after">
                <field name="x_mf_status"
                       optional="show"
                       decoration-danger="x_mf_status == 'mf_exception'"
                       decoration-warning="x_mf_status == 'mf_held_review'"
                       decoration-success="x_mf_status == 'mf_delivered'"/>
            </xpath>
        </field>
    </record>

</odoo>
```

**Step 2: Add to manifest**

In `addons/stock_3pl_mainfreight/__manifest__.py`, add to `'data'`:
```python
'views/picking_mf_views.xml',
```
Place it AFTER the `security/ir.model.access.csv` entry and BEFORE any menu files.

**Step 3: Install and verify**

```bash
python odoo-bin -u stock_3pl_mainfreight --stop-after-init -d testdb
```
Open any stock.picking form — confirm the Mainfreight tab appears and MF statusbar renders.

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/views/picking_mf_views.xml \
        addons/stock_3pl_mainfreight/__manifest__.py
git commit -m "feat(mf-ux): add MF status, cross-border, and approval button to stock.picking views"
```

---

### Task 10: Exception queue and order pipeline views

**Context:** Operations needs a dedicated place to see and action exceptional cases: cross-border held pickings, MF exceptions, and the full order pipeline. These are pre-filtered window actions on `stock.picking` — no new model needed.

**Files:**
- Create: `addons/stock_3pl_mainfreight/views/exception_views.xml`
- Create: `addons/stock_3pl_mainfreight/views/menu_mf.xml`
- Modify: `addons/stock_3pl_mainfreight/__manifest__.py`

**Step 1: Create `exception_views.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!-- Cross-border held -->
    <record id="action_mf_cross_border_held" model="ir.actions.act_window">
        <field name="name">Cross-Border — Awaiting Approval</field>
        <field name="res_model">stock.picking</field>
        <field name="view_mode">tree,form</field>
        <field name="domain">[('x_mf_status', '=', 'mf_held_review')]</field>
    </record>

    <!-- All MF exceptions (short ships, errors) -->
    <record id="action_mf_exceptions" model="ir.actions.act_window">
        <field name="name">MF Exceptions</field>
        <field name="res_model">stock.picking</field>
        <field name="view_mode">tree,form</field>
        <field name="domain">[('x_mf_status', '=', 'mf_exception')]</field>
    </record>

    <!-- Full MF order pipeline — all routed pickings -->
    <record id="action_mf_order_pipeline" model="ir.actions.act_window">
        <field name="name">MF Order Pipeline</field>
        <field name="res_model">stock.picking</field>
        <field name="view_mode">tree,form</field>
        <field name="domain">[('x_mf_status', '!=', False)]</field>
    </record>

</odoo>
```

**Step 2: Create `menu_mf.xml`**

The parent `menu_3pl_root` is defined in `stock_3pl_core.menu.xml`. Reference it with the module prefix.

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <menuitem id="menu_mf_root"
              name="Mainfreight"
              parent="stock_3pl_core.menu_3pl_root"
              sequence="50"/>
    <menuitem id="menu_mf_pipeline"
              name="Order Pipeline"
              parent="menu_mf_root"
              action="action_mf_order_pipeline"
              sequence="10"/>
    <menuitem id="menu_mf_cross_border"
              name="Cross-Border Held"
              parent="menu_mf_root"
              action="action_mf_cross_border_held"
              sequence="20"/>
    <menuitem id="menu_mf_exceptions"
              name="Exceptions"
              parent="menu_mf_root"
              action="action_mf_exceptions"
              sequence="30"/>
</odoo>
```

**Step 3: Add to manifest**

```python
'views/exception_views.xml',
'views/menu_mf.xml',
```
Place `exception_views.xml` before `menu_mf.xml` (actions must be defined before menus reference them).

**Step 4: Install and verify menus appear under 3PL Integration → Mainfreight**

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/views/exception_views.xml \
        addons/stock_3pl_mainfreight/views/menu_mf.xml \
        addons/stock_3pl_mainfreight/__manifest__.py
git commit -m "feat(mf-ux): exception queue, cross-border held, and order pipeline views with menus"
```

---

### Task 11: Connector health kanban view

**Context:** The `stock_3pl_core` connector list gets a kanban view showing each connector as a card with environment badge (PROD/TEST) and message count. Goes in `stock_3pl_core` since it's platform-level. Modify the existing `connector_views.xml` and update the action to include `kanban` mode.

**Files:**
- Modify: `addons/stock_3pl_core/views/connector_views.xml`

**Step 1: Add kanban view record**

Add after the existing `view_3pl_connector_tree` record:

```xml
<record id="view_3pl_connector_kanban" model="ir.ui.view">
    <field name="name">3pl.connector.kanban</field>
    <field name="model">3pl.connector</field>
    <field name="arch" type="xml">
        <kanban>
            <field name="name"/>
            <field name="forwarder"/>
            <field name="environment"/>
            <field name="transport"/>
            <field name="message_count"/>
            <field name="last_soh_applied_at"/>
            <templates>
                <t t-name="kanban-box">
                    <div class="oe_kanban_card oe_kanban_global_click">
                        <div class="o_kanban_record_top">
                            <div class="o_kanban_record_headings">
                                <strong class="o_kanban_record_title">
                                    <field name="name"/>
                                </strong>
                            </div>
                            <span t-if="record.environment.raw_value == 'production'"
                                  class="badge badge-pill badge-danger ml-1">PROD</span>
                            <span t-else=""
                                  class="badge badge-pill badge-secondary ml-1">TEST</span>
                        </div>
                        <div class="o_kanban_record_body mt-2">
                            <div>
                                <span class="text-muted">Forwarder: </span>
                                <field name="forwarder"/>
                            </div>
                            <div>
                                <span class="text-muted">Transport: </span>
                                <field name="transport"/>
                            </div>
                            <div>
                                <span class="text-muted">Messages: </span>
                                <field name="message_count"/>
                            </div>
                            <div t-if="record.last_soh_applied_at.raw_value">
                                <span class="text-muted">Last SOH: </span>
                                <field name="last_soh_applied_at"/>
                            </div>
                        </div>
                    </div>
                </t>
            </templates>
        </kanban>
    </field>
</record>
```

**Step 2: Update `action_3pl_connector` to include kanban as the default**

Find the existing record and update `view_mode`:
```xml
<field name="view_mode">kanban,tree,form</field>
```

**Step 3: Install and verify kanban renders**

**Step 4: Commit**

```bash
git add addons/stock_3pl_core/views/connector_views.xml
git commit -m "feat(core-ux): connector health kanban with environment badge and message count"
```

---

### Task 12: Warehouse lat/lng fields in connector/warehouse view

**Context:** The new `x_mf_latitude` and `x_mf_longitude` fields on `stock.warehouse` need to be editable somewhere. Add them to the MF connector extension view in `stock_3pl_mainfreight` (alongside the existing `x_mf_warehouse_code` field). This is the most natural place for ops to configure routing geography.

**Files:**
- Modify: `addons/stock_3pl_mainfreight/views/connector_mf_views.xml` (from Sprint 1 Task 10)

**Step 1: Find the existing `connector_mf_views.xml` and the warehouse section**

Look for the view that inherits `stock_3pl_core.view_3pl_connector_form` and adds MF-specific fields.

**Step 2: Add a "Routing" group to the connector form (or warehouse form)**

The natural place is on the `stock.warehouse` form, not the connector form. Add a new view inheritance:

```xml
<record id="view_stock_warehouse_mf_form" model="ir.ui.view">
    <field name="name">stock.warehouse.mf.form</field>
    <field name="model">stock.warehouse</field>
    <field name="inherit_id" ref="stock.view_warehouse"/>
    <field name="arch" type="xml">
        <xpath expr="//sheet" position="inside">
            <group string="Mainfreight Routing">
                <field name="x_mf_enabled"/>
                <field name="x_mf_latitude"
                       attrs="{'invisible': [('x_mf_enabled', '=', False)]}"/>
                <field name="x_mf_longitude"
                       attrs="{'invisible': [('x_mf_enabled', '=', False)]}"/>
            </group>
        </xpath>
    </field>
</record>
```

**Step 3: Add to manifest if `connector_mf_views.xml` already covers this, or add the warehouse view to the data list**

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/views/connector_mf_views.xml \
        addons/stock_3pl_mainfreight/__manifest__.py
git commit -m "feat(mf-ux): add MF routing fields (lat/lng/enabled) to stock.warehouse form"
```

---

### Task 13: End-to-end routing integration test

**Context:** A top-level test that exercises the full routing pipeline with two warehouses and an actual sale order. Gives confidence the engine, split, and cross-border check all work together.

**Files:**
- Create: `addons/stock_3pl_mainfreight/tests/test_routing_integration.py`
- Modify: `addons/stock_3pl_mainfreight/tests/__init__.py`

**Step 1: Write integration test**

```python
# addons/stock_3pl_mainfreight/tests/test_routing_integration.py
from odoo.tests import tagged, TransactionCase
from odoo.exceptions import UserError


@tagged('post_install', '-at_install', 'routing')
class TestRoutingIntegration(TransactionCase):

    def setUp(self):
        super().setUp()
        # Disable MF on all warehouses first for a clean slate
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})

        self.wh_ham = self.env['stock.warehouse'].search([], limit=1)
        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        self.wh_ham.write({
            'x_mf_enabled': True,
            'x_mf_latitude': -37.7870,
            'x_mf_longitude': 175.2793,
        })
        if nz:
            self.wh_ham.partner_id.country_id = nz

    def test_no_mf_warehouses_raises(self):
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order:
            return
        with self.assertRaises(UserError):
            self.env['mf.route.engine'].route_order(order)

    def test_order_with_no_lat_lng_falls_back_to_first_warehouse(self):
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order:
            return
        # Remove lat/lng from partner
        order.partner_shipping_id.write({'partner_latitude': 0, 'partner_longitude': 0})
        assignments = self.env['mf.route.engine'].route_order(order)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]['warehouse'], self.wh_ham)

    def test_cross_border_flag_is_set_on_split_apply(self):
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order or not order.picking_ids:
            return
        au = self.env['res.country'].search([('code', '=', 'AU')], limit=1)
        if not au:
            return
        # Make the order cross-border by setting partner to AU
        order.partner_shipping_id.country_id = au
        # Warehouse is NZ → AU customer = cross-border
        assignments = self.env['mf.route.engine'].route_order(order)
        pickings = self.env['mf.split.engine'].apply_routing(order, assignments)
        for picking in pickings:
            self.assertTrue(picking.x_mf_cross_border)
            self.assertEqual(picking.x_mf_status, 'mf_held_review')
```

**Step 2: Add to `tests/__init__.py`**

```python
from . import test_routing_integration
```

**Step 3: Run tests**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestRoutingIntegration
```
Expected: PASS (or SKIP for tests where test data is missing)

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/tests/test_routing_integration.py \
        addons/stock_3pl_mainfreight/tests/__init__.py
git commit -m "test(mf): end-to-end routing integration test — single warehouse, fallback, cross-border"
```

---

### Task 14: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

Add a "Sprint 2 Additions" section covering:
- New models: `mf.route.engine` (AbstractModel), `mf.split.engine` (AbstractModel) — no ACL
- New fields: `stock.warehouse.x_mf_latitude/longitude/enabled`, `stock.picking.x_mf_routed_by/cross_border`, `sale.order.x_mf_split`
- Cross-border logic: warehouse country ≠ delivery country → `mf_held_review`, manual release via `action_approve_cross_border()`
- Haversine util: `addons/stock_3pl_mainfreight/utils/haversine.py`
- UX views: `picking_mf_views.xml`, `exception_views.xml`, `menu_mf.xml`, connector kanban in `connector_views.xml`
- Routing pre-processor: called at start of push cron via `_route_pending_orders()`

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for sprint 2 — routing engine, split logic, UX"
```

---

## Task Checklist

| # | Task | Phase | Est. Complexity |
|---|---|---|---|
| 1 | stock.warehouse lat/lng fields | Routing | Low |
| 2 | stock.picking routing fields | Routing | Low |
| 3 | sale.order split flag | Routing | Low |
| 4 | Haversine utility | Routing | Low |
| 5 | Routing engine (haversine + stock check) | Routing | Medium |
| 6 | Split order engine | Routing | Medium-High |
| 7 | Cross-border release button | Routing | Low |
| 8 | Wire routing into push cron | Routing | Low |
| 9 | stock.picking view enhancements | UX | Low |
| 10 | Exception queue + pipeline views | UX | Low |
| 11 | Connector health kanban | UX | Low |
| 12 | Warehouse lat/lng in view | UX | Low |
| 13 | Integration test | Testing | Medium |
| 14 | Update CLAUDE.md | Docs | Low |

---

## V3 Scope Notes (not in this sprint)

These are explicitly deferred per `mainfreight_integration_scopeV3.md`:

- **Inventory reconciliation queue** (Section 4.9) — Phase 4
- **Tracking API integration** (Section 4.12) — Phase 5 (existing MF Transport API auth can be reused)
- **Order reconciliation polling** (Section 4.13) — Phase 5
- **MF SOH API cross-check for routing stock** (Section 5.5 note) — Phase 2 add-on after drift is confirmed
- **Dashboard with real-time metrics** — after data flows are stable (V3 Section 0, UX Phase 2)
- **Webhook/push notification from MF tracking** (Section 4.12) — requires MF to support it (open question #3)
