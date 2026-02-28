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


def _make_routable_engine(wh_list, stock_map):
    """Construct an MFRouteEngine with mocked warehouses and stock.

    stock_map: {product: {warehouse_name: qty}}
    """
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
            order='x_mf_warehouse_code, name',
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


def _make_per_model_env(quant_mock, connector_mock=None):
    """Build a MagicMock env that returns different child mocks per model key.

    MagicMock.__getitem__ returns the SAME child mock for every key, which
    breaks tests that use both 'stock.quant' and '3pl.connector' lookups.
    This helper wires separate mocks per model name via a side_effect on
    __getitem__.
    """
    quant_model = MagicMock()
    quant_model.search = quant_mock

    connector_model = MagicMock()
    if connector_mock is None:
        # Default: connector with SOH API disabled so stock tests stay isolated
        disabled_connector = MagicMock()
        disabled_connector.x_mf_use_api_soh = False
        connector_model.search.return_value = disabled_connector
    else:
        connector_model.search = connector_mock

    _model_map = {
        'stock.quant': quant_model,
        '3pl.connector': connector_model,
    }

    env = MagicMock()
    env.__getitem__.side_effect = lambda key: _model_map.get(key, MagicMock())
    return env, quant_model, connector_model


class TestCheckStock(unittest.TestCase):
    """_check_stock sums quant quantities at the warehouse stock location."""

    def _make_quants(self, quantities):
        quants = MagicMock()
        quants.mapped.return_value = quantities
        return quants

    def test_returns_summed_quantity_for_product(self):
        wh = _make_warehouse('WH1', lat=-36.0, lng=174.0)
        quants = self._make_quants([10.0, 5.0])

        quant_search = MagicMock(return_value=quants)
        env, quant_model, _ = _make_per_model_env(quant_search)

        engine = object.__new__(MFRouteEngine)
        engine.env = env

        prod = _make_product()
        result = engine._check_stock(wh, [(prod, 8.0)])

        self.assertIn(prod, result)
        self.assertEqual(result[prod], 15.0)

    def test_search_uses_child_of_lot_stock_id(self):
        wh = _make_warehouse()
        wh.lot_stock_id.id = 42
        quants = self._make_quants([])

        quant_search = MagicMock(return_value=quants)
        env, quant_model, _ = _make_per_model_env(quant_search)

        engine = object.__new__(MFRouteEngine)
        engine.env = env

        prod = _make_product()
        engine._check_stock(wh, [(prod, 1.0)])

        call_args = quant_model.search.call_args[0][0]
        # Domain must contain ('location_id', 'child_of', 42)
        self.assertIn(('location_id', 'child_of', 42), call_args)

    def test_zero_quantity_when_no_quants(self):
        wh = _make_warehouse()
        quants = self._make_quants([])

        quant_search = MagicMock(return_value=quants)
        env, _, _ = _make_per_model_env(quant_search)

        engine = object.__new__(MFRouteEngine)
        engine.env = env

        prod = _make_product()
        result = engine._check_stock(wh, [(prod, 3.0)])
        self.assertEqual(result[prod], 0.0)

    def test_multiple_products_each_searched_separately(self):
        wh = _make_warehouse()
        call_count = {'n': 0}

        def quant_side_effect(domain):
            q = MagicMock()
            q.mapped.return_value = [float(call_count['n'] + 1) * 10]
            call_count['n'] += 1
            return q

        quant_search = MagicMock(side_effect=quant_side_effect)
        env, quant_model, _ = _make_per_model_env(quant_search)

        engine = object.__new__(MFRouteEngine)
        engine.env = env

        prod_a = _make_product('A')
        prod_b = _make_product('B')
        result = engine._check_stock(wh, [(prod_a, 1.0), (prod_b, 1.0)])

        self.assertEqual(quant_model.search.call_count, 2)
        self.assertEqual(result[prod_a], 10.0)
        self.assertEqual(result[prod_b], 20.0)


class TestRouteOrderSingleLine(unittest.TestCase):
    """Single-line orders prefer complete fulfilment from one warehouse."""

    def test_assigns_to_closest_warehouse_with_full_stock(self):
        """WH-SYD is closer to Brisbane but has 0 stock; WH-MEL has enough."""
        wh_syd = _make_warehouse('WH-SYD', lat=-33.87, lng=151.21)
        wh_mel = _make_warehouse('WH-MEL', lat=-37.81, lng=144.96)

        prod = _make_product()
        # Customer in Brisbane (~lat -27.47, lng 153.03) — closer to SYD than MEL
        stock_map = {prod: {'WH-SYD': 0.0, 'WH-MEL': 10.0}}

        engine = _make_routable_engine([wh_syd, wh_mel], stock_map)

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

        engine = _make_routable_engine([wh1, wh2], stock_map)

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


class TestRouteOrderMultiLine(unittest.TestCase):
    """Multi-product orders use greedy partial assignment across warehouses."""

    def test_two_products_split_across_two_warehouses(self):
        """WH-A has prod1 only; WH-B has prod2 only — each assigned to the right warehouse."""
        wh1 = _make_warehouse('WH-A', lat=-33.0, lng=151.0)
        wh2 = _make_warehouse('WH-B', lat=-37.0, lng=145.0)

        prod1 = _make_product('Prod1')
        prod2 = _make_product('Prod2')
        # WH-A (closer to customer) has prod1 but not prod2
        # WH-B has prod2 but not prod1
        stock_map = {
            prod1: {'WH-A': 10.0, 'WH-B': 0.0},
            prod2: {'WH-A': 0.0, 'WH-B': 8.0},
        }

        engine = _make_routable_engine([wh1, wh2], stock_map)

        partner = MagicMock()
        partner.partner_latitude = -34.0   # closer to WH-A
        partner.partner_longitude = 151.0
        partner.name = 'Customer'

        order = MagicMock()
        order.name = 'SO-MULTILINE'
        order.partner_shipping_id = partner
        order.order_line = [
            _make_order_line(prod1, 5.0),
            _make_order_line(prod2, 3.0),
        ]
        engine._order_lines = lambda o: [(prod1, 5.0), (prod2, 3.0)]

        result = engine.route_order(order)
        # Should produce 2 assignment dicts — one per warehouse
        self.assertEqual(len(result), 2)
        warehouses_used = {a['warehouse'].name for a in result}
        self.assertIn('WH-A', warehouses_used)
        self.assertIn('WH-B', warehouses_used)
        # Total lines across all assignments = 2 products
        all_lines = [line for a in result for line in a['lines']]
        self.assertEqual(len(all_lines), 2)
        total_qty = sum(qty for _p, qty in all_lines)
        self.assertEqual(total_qty, 8.0)  # 5 + 3

    def test_partial_fill_leaves_remainder_warning(self):
        """If stock is exhausted before all lines are covered, remaining is logged."""
        wh1 = _make_warehouse('WH-X', lat=-33.0, lng=151.0)

        prod1 = _make_product('ProdX')
        prod2 = _make_product('ProdY')
        # WH-X has prod1 but no prod2
        stock_map = {
            prod1: {'WH-X': 5.0},
            prod2: {'WH-X': 0.0},
        }

        engine = _make_routable_engine([wh1], stock_map)

        partner = MagicMock()
        partner.partner_latitude = -34.0
        partner.partner_longitude = 151.0
        partner.name = 'Customer2'

        order = MagicMock()
        order.name = 'SO-PARTIAL'
        order.partner_shipping_id = partner
        order.order_line = [
            _make_order_line(prod1, 5.0),
            _make_order_line(prod2, 3.0),
        ]
        engine._order_lines = lambda o: [(prod1, 5.0), (prod2, 3.0)]

        result = engine.route_order(order)
        # prod1 assigned to WH-X; prod2 has no stock — result has only 1 assignment
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['warehouse'].name, 'WH-X')
        # Only prod1 was assignable
        assigned_products = [p for p, _q in result[0]['lines']]
        self.assertIn(prod1, assigned_products)
        self.assertNotIn(prod2, assigned_products)


class TestRouteOrderEmptyLines(unittest.TestCase):
    """route_order returns [] when the order has no storable product lines."""

    def test_no_storable_lines_no_lat_lng_returns_empty(self):
        """Order with only service lines + partner has no lat/lng → []."""
        wh = _make_warehouse('WH-EMPTY', lat=-37.0, lng=175.0)
        engine = object.__new__(MFRouteEngine)
        engine.env = MagicMock()
        engine._get_mf_warehouses = lambda: [wh]
        engine._order_lines = lambda o: []   # no storable lines

        partner = MagicMock()
        partner.partner_latitude = 0.0
        partner.partner_longitude = 0.0
        partner.name = 'Service-only Customer'

        order = MagicMock()
        order.name = 'SO-SVCONLY'
        order.partner_shipping_id = partner
        order.order_line = []

        result = engine.route_order(order)
        self.assertEqual(result, [])

    def test_no_storable_lines_with_coords_returns_empty(self):
        """Order with only service lines + partner has valid coords → []."""
        wh = _make_warehouse('WH-COORDS', lat=-37.0, lng=175.0)
        engine = object.__new__(MFRouteEngine)
        engine.env = MagicMock()
        engine._get_mf_warehouses = lambda: [wh]
        engine._order_lines = lambda o: []   # no storable lines

        partner = MagicMock()
        partner.partner_latitude = -36.8
        partner.partner_longitude = 174.7
        partner.name = 'Auckland Service Customer'

        order = MagicMock()
        order.name = 'SO-SVCCOORDS'
        order.partner_shipping_id = partner
        order.order_line = []

        result = engine.route_order(order)
        self.assertEqual(result, [])


_ROUTE_ENGINE_LOGGER = 'stock_3pl_mainfreight.models.route_engine'


class TestSOHApiCrossCheck(unittest.TestCase):
    """_check_stock optionally cross-checks Odoo stock against the MF SOH API."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_engine_with_connector(self, connector=None):
        """Return an MFRouteEngine whose env models are properly separated.

        Uses _make_per_model_env so that 'stock.quant' and '3pl.connector'
        return independent mock objects — MagicMock.__getitem__ returns the
        same child for any key by default, which would break these tests.
        """
        engine = object.__new__(MFRouteEngine)

        # stock.quant always returns qty 10
        quants = MagicMock()
        quants.mapped.return_value = [10.0]
        quant_search = MagicMock(return_value=quants)

        # 3pl.connector search returns the provided connector
        connector_search = MagicMock(return_value=connector)

        env, _quant_model, _connector_model = _make_per_model_env(
            quant_search, connector_search
        )
        engine.env = env
        return engine

    def _make_connector(self, use_api_soh=False, soh_response=None, raise_on_call=False):
        """Return a mock connector whose transport.get_stock_on_hand() behaves as specified."""
        connector = MagicMock()
        connector.x_mf_use_api_soh = use_api_soh

        transport = MagicMock()
        if raise_on_call:
            transport.get_stock_on_hand.side_effect = RuntimeError('network error')
        else:
            transport.get_stock_on_hand.return_value = soh_response if soh_response is not None else []
        connector.get_transport.return_value = transport

        return connector

    def _make_product_with_code(self, name='Widget', code='WIDGET-01'):
        p = MagicMock()
        p.name = name
        p.default_code = code
        p.type = 'product'
        p.id = id(p)
        return p

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_soh_api_disabled_skips_api_call(self):
        """When x_mf_use_api_soh is False, get_stock_on_hand() is never called."""
        connector = self._make_connector(use_api_soh=False)
        engine = self._make_engine_with_connector(connector)
        wh = _make_warehouse('WH1')
        prod = self._make_product_with_code()

        engine._check_stock(wh, [(prod, 5.0)])

        connector.get_transport.assert_not_called()

    def test_soh_api_enabled_uses_mf_quantity_on_drift(self):
        """MF qty differs from Odoo qty → method returns MF figure, warning logged."""
        # Odoo quant = 10 (from _make_engine_with_connector helper)
        # MF SOH = 5  → drift = 5, use MF figure
        soh_response = [{'ProductCode': 'WIDGET-01', 'QuantityAvailable': 5}]
        connector = self._make_connector(use_api_soh=True, soh_response=soh_response)
        engine = self._make_engine_with_connector(connector)
        wh = _make_warehouse('WH1')
        prod = self._make_product_with_code(code='WIDGET-01')

        with self.assertLogs(_ROUTE_ENGINE_LOGGER, level='WARNING') as cm:
            result = engine._check_stock(wh, [(prod, 5.0)])

        self.assertEqual(result[prod], 5.0)
        self.assertTrue(any('drift' in line.lower() for line in cm.output))

    def test_soh_api_enabled_no_drift_uses_odoo_quantity(self):
        """When MF qty matches Odoo qty exactly, no warning is logged and Odoo figure is kept."""
        # Odoo quant = 10, MF SOH = 10 → no drift
        soh_response = [{'ProductCode': 'SKU-42', 'QuantityAvailable': 10}]
        connector = self._make_connector(use_api_soh=True, soh_response=soh_response)
        engine = self._make_engine_with_connector(connector)
        wh = _make_warehouse('WH1')
        prod = self._make_product_with_code(code='SKU-42')

        # assertNoLogs is Python 3.10+; use assertRaises on assertLogs for compat
        with self.assertRaises(AssertionError):
            with self.assertLogs(_ROUTE_ENGINE_LOGGER, level='WARNING'):
                engine._check_stock(wh, [(prod, 10.0)])

        # Re-run without assertLogs to capture the result
        result = engine._check_stock(wh, [(prod, 10.0)])
        self.assertEqual(result[prod], 10.0)

    def test_soh_api_returns_empty_falls_back_to_odoo(self):
        """API returns [] → warning logged, falls back to Odoo quantities, no exception raised."""
        connector = self._make_connector(use_api_soh=True, soh_response=[])
        engine = self._make_engine_with_connector(connector)
        wh = _make_warehouse('WH1')
        prod = self._make_product_with_code()

        with self.assertLogs(_ROUTE_ENGINE_LOGGER, level='WARNING') as cm:
            result = engine._check_stock(wh, [(prod, 5.0)])

        # Falls back to Odoo quantity (10.0)
        self.assertEqual(result[prod], 10.0)
        self.assertTrue(any('empty' in line.lower() for line in cm.output))

    def test_soh_api_call_raises_falls_back_to_odoo(self):
        """API raises an exception → warning logged, falls back to Odoo, routing continues."""
        connector = self._make_connector(use_api_soh=True, raise_on_call=True)
        engine = self._make_engine_with_connector(connector)
        wh = _make_warehouse('WH1')
        prod = self._make_product_with_code()

        with self.assertLogs(_ROUTE_ENGINE_LOGGER, level='WARNING') as cm:
            result = engine._check_stock(wh, [(prod, 5.0)])

        # Falls back to Odoo quantity (10.0)
        self.assertEqual(result[prod], 10.0)
        self.assertTrue(any('falling back' in line.lower() for line in cm.output))

    def test_no_connector_for_warehouse_skips_api(self):
        """No 3pl.connector found for warehouse → skip API, use Odoo quantities."""
        # Returning None (falsy) simulates search() finding no connector record
        engine = self._make_engine_with_connector(connector=None)
        wh = _make_warehouse('WH-NOCONN')
        prod = self._make_product_with_code()

        # Should not raise; no transport call is made
        result = engine._check_stock(wh, [(prod, 5.0)])

        # Result is from Odoo quants (10.0) — connector was falsy so API skipped
        self.assertEqual(result[prod], 10.0)


if __name__ == '__main__':
    unittest.main()
