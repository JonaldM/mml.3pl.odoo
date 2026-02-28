# addons/stock_3pl_mainfreight/tests/test_route_engine.py
"""Pure-Python mock-based tests for MFRouteEngine.

No Odoo runtime required. Odoo stubs are installed by the repo-level
conftest.py before pytest collects this module, so we only need to:
  1. Load the module under test directly from the filesystem.
  2. Grab UserError from the already-stubbed sys.modules so we catch
     exactly what route_engine raises.
"""
import sys
import types
import unittest
import importlib.util
import pathlib
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# UserError — taken from the stub installed by conftest.py
# ---------------------------------------------------------------------------
_UserError = sys.modules['odoo.exceptions'].UserError

# ---------------------------------------------------------------------------
# Ensure the haversine utility module is registered so route_engine can import it
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_HAV_PATH = _HERE.parent / 'utils' / 'haversine.py'

if 'odoo.addons.stock_3pl_mainfreight.utils.haversine' not in sys.modules:
    _hav_spec = importlib.util.spec_from_file_location(
        'odoo.addons.stock_3pl_mainfreight.utils.haversine', str(_HAV_PATH)
    )
    _hav_mod = importlib.util.module_from_spec(_hav_spec)
    _hav_spec.loader.exec_module(_hav_mod)
    sys.modules['odoo.addons.stock_3pl_mainfreight.utils.haversine'] = _hav_mod

    # Also ensure the 'utils' package stub exists
    if 'odoo.addons.stock_3pl_mainfreight.utils' not in sys.modules:
        _utils_pkg = types.ModuleType('odoo.addons.stock_3pl_mainfreight.utils')
        _utils_pkg.__path__ = [str(_HERE.parent / 'utils')]
        sys.modules['odoo.addons.stock_3pl_mainfreight.utils'] = _utils_pkg

# ---------------------------------------------------------------------------
# Load the module under test directly from disk
# ---------------------------------------------------------------------------
_ROUTE_PATH = _HERE.parent / 'models' / 'route_engine.py'

if 'stock_3pl_mainfreight.models.route_engine' not in sys.modules:
    _route_spec = importlib.util.spec_from_file_location(
        'stock_3pl_mainfreight.models.route_engine', str(_ROUTE_PATH)
    )
    _route_mod = importlib.util.module_from_spec(_route_spec)
    sys.modules['stock_3pl_mainfreight.models.route_engine'] = _route_mod
    _route_spec.loader.exec_module(_route_mod)
else:
    _route_mod = sys.modules['stock_3pl_mainfreight.models.route_engine']

MFRouteEngine = _route_mod.MFRouteEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(warehouses_result=None):
    """Construct an MFRouteEngine instance with a mocked self.env."""
    engine = object.__new__(MFRouteEngine)
    env = MagicMock()
    wh_search = MagicMock(
        return_value=warehouses_result if warehouses_result is not None else []
    )
    env.__getitem__.return_value.search = wh_search
    engine.env = env
    return engine


def _make_warehouse(name='WH1', lat=0.0, lng=0.0, enabled=True):
    wh = MagicMock()
    wh.name = name
    wh.x_mf_enabled = enabled
    wh.x_mf_latitude = lat
    wh.x_mf_longitude = lng
    wh.lot_stock_id = MagicMock()
    wh.lot_stock_id.id = abs(hash(name)) % 1000 + 1
    return wh


def _make_product(name='Prod A', prod_type='product'):
    p = MagicMock()
    p.name = name
    p.type = prod_type
    p.id = id(p)
    return p


def _make_order_line(product, qty):
    line = MagicMock()
    line.product_id = product
    line.product_uom_qty = qty
    return line


def _make_order(lines, shipping_partner=None):
    order = MagicMock()
    order.name = 'SO001'
    order.order_line = lines
    if shipping_partner is not None:
        order.partner_shipping_id = shipping_partner
    else:
        partner = MagicMock()
        partner.partner_latitude = 0.0
        partner.partner_longitude = 0.0
        partner.name = 'Test Partner'
        order.partner_shipping_id = partner
    order.partner_id = MagicMock()
    return order


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetMFWarehouses(unittest.TestCase):
    """_get_mf_warehouses delegates to stock.warehouse.search with correct args."""

    def test_search_called_with_correct_domain_and_order(self):
        engine = _make_engine()
        engine._get_mf_warehouses()
        engine.env['stock.warehouse'].search.assert_called_once_with(
            [('x_mf_enabled', '=', True)],
            order='name',
        )

    def test_returns_search_result(self):
        wh1 = _make_warehouse('Alpha')
        wh2 = _make_warehouse('Beta')
        engine = _make_engine(warehouses_result=[wh1, wh2])
        result = engine._get_mf_warehouses()
        self.assertEqual(result, [wh1, wh2])


class TestOrderLines(unittest.TestCase):
    """_order_lines filters to storable products only."""

    def test_returns_storable_products(self):
        prod = _make_product('Widget', 'product')
        line = _make_order_line(prod, 5.0)
        order = _make_order([line])
        engine = _make_engine()
        result = engine._order_lines(order)
        self.assertEqual(result, [(prod, 5.0)])

    def test_excludes_service_lines(self):
        storable = _make_product('Widget', 'product')
        service = _make_product('Delivery', 'service')
        lines = [_make_order_line(storable, 3.0), _make_order_line(service, 1.0)]
        order = _make_order(lines)
        engine = _make_engine()
        result = engine._order_lines(order)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], storable)

    def test_excludes_lines_with_no_product(self):
        line = MagicMock()
        line.product_id = None  # falsy
        line.product_uom_qty = 2.0
        order = _make_order([line])
        engine = _make_engine()
        result = engine._order_lines(order)
        self.assertEqual(result, [])

    def test_excludes_consu_lines(self):
        storable = _make_product('Box', 'product')
        consu = _make_product('Consumable', 'consu')
        lines = [_make_order_line(storable, 10.0), _make_order_line(consu, 2.0)]
        order = _make_order(lines)
        engine = _make_engine()
        result = engine._order_lines(order)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], (storable, 10.0))


class TestRouteOrderNoWarehouses(unittest.TestCase):
    """route_order raises UserError when no MF warehouses are configured."""

    def test_raises_user_error(self):
        engine = _make_engine()
        engine._get_mf_warehouses = lambda: []
        order = MagicMock()
        with self.assertRaises(_UserError):
            engine.route_order(order)

    def test_error_message_mentions_warehouses(self):
        engine = _make_engine()
        engine._get_mf_warehouses = lambda: []
        order = MagicMock()
        try:
            engine.route_order(order)
            self.fail('UserError not raised')
        except _UserError as exc:
            self.assertIn('warehouse', str(exc).lower())


class TestRouteOrderNoLatLng(unittest.TestCase):
    """route_order falls back to the first warehouse when partner has no lat/lng."""

    def _make_engine_with_wh(self, wh_list):
        engine = _make_engine()
        engine._get_mf_warehouses = lambda: wh_list
        return engine

    def test_fallback_to_first_warehouse_when_lat_is_zero(self):
        wh = _make_warehouse('WH-NZ', lat=0.0, lng=0.0)
        engine = self._make_engine_with_wh([wh])

        partner = MagicMock()
        partner.partner_latitude = 0.0   # falsy
        partner.partner_longitude = 0.0
        partner.name = 'NoCoords Partner'

        prod = _make_product()
        order = MagicMock()
        order.name = 'SO-NOLATLNG'
        order.partner_shipping_id = partner
        order.order_line = [_make_order_line(prod, 1.0)]
        engine._order_lines = lambda o: [(prod, 1.0)]

        result = engine.route_order(order)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['warehouse'], wh)

    def test_fallback_to_first_warehouse_when_lat_is_none(self):
        wh = _make_warehouse('WH-AU', lat=-33.8, lng=151.2)
        engine = self._make_engine_with_wh([wh])

        partner = MagicMock()
        partner.partner_latitude = None
        partner.partner_longitude = None
        partner.name = 'NullCoords Partner'

        prod = _make_product()
        order = MagicMock()
        order.name = 'SO-NULLLATLNG'
        order.partner_shipping_id = partner
        order.order_line = [_make_order_line(prod, 2.0)]
        engine._order_lines = lambda o: [(prod, 2.0)]

        result = engine.route_order(order)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['warehouse'], wh)

    def test_fallback_when_no_warehouses_have_coords(self):
        """All warehouses enabled but none have lat/lng set — fall back to first."""
        wh1 = _make_warehouse('Alpha', lat=0.0, lng=0.0)
        wh2 = _make_warehouse('Beta', lat=0.0, lng=0.0)
        engine = self._make_engine_with_wh([wh1, wh2])

        partner = MagicMock()
        partner.partner_latitude = -36.8
        partner.partner_longitude = 174.7
        partner.name = 'Auckland Customer'

        prod = _make_product()
        order = MagicMock()
        order.name = 'SO-NOWH-COORDS'
        order.partner_shipping_id = partner
        order.order_line = [_make_order_line(prod, 5.0)]
        engine._order_lines = lambda o: [(prod, 5.0)]

        result = engine.route_order(order)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['warehouse'], wh1)


class TestCheckStock(unittest.TestCase):
    """_check_stock sums quant quantities at the warehouse stock location."""

    def _make_quants(self, quantities):
        quants = MagicMock()
        quants.mapped.return_value = quantities
        return quants

    def test_returns_summed_quantity_for_product(self):
        wh = _make_warehouse('WH1', lat=-36.0, lng=174.0)
        quants = self._make_quants([10.0, 5.0])

        engine = object.__new__(MFRouteEngine)
        env = MagicMock()
        env['stock.quant'].search.return_value = quants
        engine.env = env

        prod = _make_product()
        result = engine._check_stock(wh, [(prod, 8.0)])

        self.assertIn(prod, result)
        self.assertEqual(result[prod], 15.0)

    def test_search_uses_child_of_lot_stock_id(self):
        wh = _make_warehouse()
        wh.lot_stock_id.id = 42
        quants = self._make_quants([])

        engine = object.__new__(MFRouteEngine)
        env = MagicMock()
        env['stock.quant'].search.return_value = quants
        engine.env = env

        prod = _make_product()
        engine._check_stock(wh, [(prod, 1.0)])

        call_args = env['stock.quant'].search.call_args[0][0]
        # Domain must contain ('location_id', 'child_of', 42)
        self.assertIn(('location_id', 'child_of', 42), call_args)

    def test_zero_quantity_when_no_quants(self):
        wh = _make_warehouse()
        quants = self._make_quants([])

        engine = object.__new__(MFRouteEngine)
        env = MagicMock()
        env['stock.quant'].search.return_value = quants
        engine.env = env

        prod = _make_product()
        result = engine._check_stock(wh, [(prod, 3.0)])
        self.assertEqual(result[prod], 0.0)

    def test_multiple_products_each_searched_separately(self):
        wh = _make_warehouse()
        call_count = {'n': 0}

        def side_effect(domain):
            q = MagicMock()
            q.mapped.return_value = [float(call_count['n'] + 1) * 10]
            call_count['n'] += 1
            return q

        engine = object.__new__(MFRouteEngine)
        env = MagicMock()
        env['stock.quant'].search.side_effect = side_effect
        engine.env = env

        prod_a = _make_product('A')
        prod_b = _make_product('B')
        result = engine._check_stock(wh, [(prod_a, 1.0), (prod_b, 1.0)])

        self.assertEqual(env['stock.quant'].search.call_count, 2)
        self.assertEqual(result[prod_a], 10.0)
        self.assertEqual(result[prod_b], 20.0)


class TestRouteOrderSingleLine(unittest.TestCase):
    """Single-line orders prefer complete fulfilment from one warehouse."""

    def _make_routable_engine(self, wh_list, stock_map):
        """stock_map: {product: {warehouse_name: qty}}"""
        engine = object.__new__(MFRouteEngine)
        engine.env = MagicMock()
        engine._get_mf_warehouses = lambda: wh_list

        def check_stock(wh, lines):
            result = {}
            for prod, _qty in lines:
                result[prod] = stock_map.get(prod, {}).get(wh.name, 0.0)
            return result

        engine._check_stock = check_stock
        return engine

    def test_assigns_to_closest_warehouse_with_full_stock(self):
        """WH-SYD is closer to Brisbane but has 0 stock; WH-MEL has enough."""
        wh_syd = _make_warehouse('WH-SYD', lat=-33.87, lng=151.21)
        wh_mel = _make_warehouse('WH-MEL', lat=-37.81, lng=144.96)

        prod = _make_product()
        # Customer in Brisbane (~lat -27.47, lng 153.03) — closer to SYD than MEL
        stock_map = {prod: {'WH-SYD': 0.0, 'WH-MEL': 10.0}}

        engine = self._make_routable_engine([wh_syd, wh_mel], stock_map)

        partner = MagicMock()
        partner.partner_latitude = -27.47
        partner.partner_longitude = 153.03
        partner.name = 'Brisbane Customer'

        order = MagicMock()
        order.name = 'SO-BRIS'
        order.partner_shipping_id = partner
        order.order_line = [_make_order_line(prod, 5.0)]
        engine._order_lines = lambda o: [(prod, 5.0)]

        result = engine.route_order(order)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['warehouse'].name, 'WH-MEL')

    def test_single_line_split_when_no_warehouse_has_enough(self):
        """Greedy split: WH-A fills 4, remainder 6 goes to WH-B (has 7 avail)."""
        wh1 = _make_warehouse('WH-A', lat=-33.0, lng=151.0)
        wh2 = _make_warehouse('WH-B', lat=-37.0, lng=145.0)

        prod = _make_product()
        # Neither warehouse can fill qty=10 alone
        stock_map = {prod: {'WH-A': 4.0, 'WH-B': 7.0}}

        engine = self._make_routable_engine([wh1, wh2], stock_map)

        partner = MagicMock()
        partner.partner_latitude = -34.0
        partner.partner_longitude = 151.0
        partner.name = 'Customer'

        order = MagicMock()
        order.name = 'SO-SPLIT'
        order.partner_shipping_id = partner
        order.order_line = [_make_order_line(prod, 10.0)]
        engine._order_lines = lambda o: [(prod, 10.0)]

        result = engine.route_order(order)
        # Greedy: WH-A contributes 4, WH-B fills the remaining 6 (has 7 avail)
        # Total fulfilled = 10 (not 11 — WH-B only fills what's still needed)
        self.assertGreater(len(result), 1)
        total = sum(qty for a in result for _p, qty in a['lines'])
        self.assertEqual(total, 10.0)


if __name__ == '__main__':
    unittest.main()
