# addons/stock_3pl_mainfreight/tests/test_split_engine.py
"""Pure-Python mock-based tests for MFSplitEngine.

No Odoo runtime required. Odoo stubs are installed by the repo-level
conftest.py before pytest collects this module, so we only need to:
  1. Load the module under test directly from the filesystem.
  2. Use MagicMock for all Odoo ORM calls.
"""
import sys
import unittest
import importlib.util
import pathlib
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Load the module under test directly from disk
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_SPLIT_PATH = _HERE.parent / 'models' / 'split_engine.py'

if 'stock_3pl_mainfreight.models.split_engine' not in sys.modules:
    _split_spec = importlib.util.spec_from_file_location(
        'stock_3pl_mainfreight.models.split_engine', str(_SPLIT_PATH)
    )
    _split_mod = importlib.util.module_from_spec(_split_spec)
    sys.modules['stock_3pl_mainfreight.models.split_engine'] = _split_mod
    _split_spec.loader.exec_module(_split_mod)
else:
    _split_mod = sys.modules['stock_3pl_mainfreight.models.split_engine']

MFSplitEngine = _split_mod.MFSplitEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine():
    """Construct an MFSplitEngine instance with a mocked self.env."""
    engine = object.__new__(MFSplitEngine)
    env = MagicMock()
    engine.env = env
    return engine


def _make_country(country_id, name='Country'):
    country = MagicMock()
    country.id = country_id
    country.name = name
    # Ensure truthiness: MagicMock is truthy by default, which is what we want.
    return country


def _make_warehouse(name='WH1', country=None):
    wh = MagicMock()
    wh.name = name
    if country is not None:
        wh.partner_id.country_id = country
    else:
        # No country set — falsy
        wh.partner_id.country_id = None
    return wh


def _make_picking(partner_country=None, state='confirmed', routed_by=''):
    picking = MagicMock()
    picking.state = state
    picking.x_mf_routed_by = routed_by
    if partner_country is not None:
        picking.partner_id.country_id = partner_country
    else:
        picking.partner_id.country_id = None
    return picking


def _make_product(name='Prod A'):
    p = MagicMock()
    p.name = name
    p.id = id(p)
    return p


def _make_assignment(warehouse, products_and_qtys):
    """Build a single routing assignment dict as route_engine would produce."""
    return {
        'warehouse': warehouse,
        'lines': [(prod, qty) for prod, qty in products_and_qtys],
    }


def _make_order_with_picking(picking, name='SO001'):
    """Build a mock order whose picking_ids.filtered() returns [picking]."""
    order = MagicMock()
    order.name = name
    order.picking_ids.filtered.return_value = [picking]
    return order


# ---------------------------------------------------------------------------
# Tests: _is_cross_border
# ---------------------------------------------------------------------------

class TestIsCrossBorder(unittest.TestCase):
    """_is_cross_border returns True only when both countries are set and differ."""

    def test_same_country_returns_false(self):
        engine = _make_engine()
        country_nz = _make_country(1, 'New Zealand')
        wh = _make_warehouse('WH-NZ', country=country_nz)
        picking = _make_picking(partner_country=country_nz)
        result = engine._is_cross_border(wh, picking)
        self.assertFalse(result)

    def test_different_countries_returns_true(self):
        engine = _make_engine()
        country_nz = _make_country(1, 'New Zealand')
        country_au = _make_country(2, 'Australia')
        wh = _make_warehouse('WH-NZ', country=country_nz)
        picking = _make_picking(partner_country=country_au)
        result = engine._is_cross_border(wh, picking)
        self.assertTrue(result)

    def test_missing_warehouse_country_returns_false(self):
        """Fail-open: if warehouse has no country, do not block."""
        engine = _make_engine()
        country_nz = _make_country(1, 'New Zealand')
        wh = _make_warehouse('WH-NOCC', country=None)
        picking = _make_picking(partner_country=country_nz)
        result = engine._is_cross_border(wh, picking)
        self.assertFalse(result)

    def test_missing_destination_country_returns_false(self):
        """Fail-open: if picking partner has no country, do not block."""
        engine = _make_engine()
        country_nz = _make_country(1, 'New Zealand')
        wh = _make_warehouse('WH-NZ', country=country_nz)
        picking = _make_picking(partner_country=None)
        result = engine._is_cross_border(wh, picking)
        self.assertFalse(result)

    def test_both_countries_missing_returns_false(self):
        engine = _make_engine()
        wh = _make_warehouse('WH-X', country=None)
        picking = _make_picking(partner_country=None)
        result = engine._is_cross_border(wh, picking)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Tests: apply_routing — empty assignments
# ---------------------------------------------------------------------------

class TestApplyRoutingEmpty(unittest.TestCase):
    """apply_routing with empty assignments returns an empty recordset."""

    def test_empty_assignments_returns_empty_recordset(self):
        engine = _make_engine()
        empty_rs = MagicMock()
        engine.env.__getitem__.return_value = empty_rs

        order = MagicMock()
        result = engine.apply_routing(order, [])

        # Should return env['stock.picking'] — the empty recordset sentinel
        engine.env.__getitem__.assert_called_with('stock.picking')
        self.assertEqual(result, empty_rs)

    def test_empty_assignments_does_not_write_order(self):
        engine = _make_engine()
        engine.env.__getitem__.return_value = MagicMock()
        order = MagicMock()
        engine.apply_routing(order, [])
        order.write.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: apply_routing — single assignment (auto_closest)
# ---------------------------------------------------------------------------

class TestApplyRoutingSingle(unittest.TestCase):
    """Single assignment: reuses first existing picking, sets auto_closest."""

    def _make_engine_with_mock_cross_border(self, cross_border=False):
        engine = _make_engine()
        engine._is_cross_border = MagicMock(return_value=cross_border)
        return engine

    def test_single_assignment_sets_auto_closest(self):
        engine = self._make_engine_with_mock_cross_border(cross_border=False)

        prod = _make_product()
        wh = _make_warehouse('WH-NZ')
        picking = _make_picking()
        # No moves to relocate — filtered returns empty list
        picking.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking)
        assignments = [_make_assignment(wh, [(prod, 5.0)])]

        # env['stock.picking'] returns a mock recordset for the accumulator start
        picking_recordset = MagicMock()
        engine.env.__getitem__.return_value = picking_recordset

        engine.apply_routing(order, assignments)

        # The picking should be written with auto_closest
        write_calls = picking.write.call_args_list
        self.assertTrue(any(
            c == call({'x_mf_routed_by': 'auto_closest', 'x_mf_cross_border': False, 'x_mf_status': 'mf_queued'})
            for c in write_calls
        ))

    def test_single_assignment_does_not_set_x_mf_split(self):
        engine = self._make_engine_with_mock_cross_border(cross_border=False)

        prod = _make_product()
        wh = _make_warehouse('WH-NZ')
        picking = _make_picking()
        picking.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking)
        assignments = [_make_assignment(wh, [(prod, 5.0)])]

        engine.env.__getitem__.return_value = MagicMock()
        engine.apply_routing(order, assignments)

        # x_mf_split should NOT be written when there is only one assignment
        for c in order.write.call_args_list:
            args = c[0][0] if c[0] else {}
            self.assertNotIn('x_mf_split', args)

    def test_multiple_unrouted_pickings_uses_first_only(self):
        """When order has 2 unrouted pickings, apply_routing succeeds and uses only the first."""
        engine = self._make_engine_with_mock_cross_border(cross_border=False)

        prod = _make_product()
        wh = _make_warehouse('WH-NZ')

        picking_a = _make_picking()
        picking_a.move_ids.filtered.return_value = []
        picking_b = _make_picking()

        # Mock order with 2 unrouted pickings
        order = MagicMock()
        order.name = 'SO010'
        order.picking_ids.filtered.return_value = [picking_a, picking_b]

        assignments = [_make_assignment(wh, [(prod, 5.0)])]
        engine.env.__getitem__.return_value = MagicMock()

        result_pickings_mock = MagicMock()
        # Capture accumulated pickings via |= by making the env mock return a consistent object
        engine.env.__getitem__.return_value = result_pickings_mock

        # Should not raise
        engine.apply_routing(order, assignments)

        # picking_a (index 0) must have been written — picking_b must not
        self.assertTrue(picking_a.write.called)
        self.assertFalse(picking_b.write.called)


# ---------------------------------------------------------------------------
# Tests: apply_routing — two assignments (auto_split)
# ---------------------------------------------------------------------------

class TestApplyRoutingSplit(unittest.TestCase):
    """Two assignments: sets auto_split on pickings, sets x_mf_split on order."""

    def _make_engine_no_cross_border(self):
        engine = _make_engine()
        engine._is_cross_border = MagicMock(return_value=False)
        return engine

    def _make_split_env_mock(self, picking2):
        """Return an env['stock.picking'] mock suitable for split (2-assignment) tests.

        create() returns picking2.
        search().move_ids.filtered() returns a MagicMock (not a list) so that
        moves.write(...) succeeds — MagicMock has .write() by default.
        """
        picking_class_mock = MagicMock()
        picking_class_mock.create.return_value = picking2
        # filtered must return a MagicMock so that .write() is available on it
        picking_class_mock.search.return_value.move_ids.filtered.return_value = MagicMock()
        return picking_class_mock

    def test_two_assignments_sets_x_mf_split_on_order(self):
        engine = self._make_engine_no_cross_border()

        prod1 = _make_product('ProdA')
        prod2 = _make_product('ProdB')
        wh1 = _make_warehouse('WH-A')
        wh2 = _make_warehouse('WH-B')

        picking1 = _make_picking()
        picking1.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking1, name='SO002')
        order.partner_shipping_id = MagicMock()
        order.partner_id = MagicMock()

        picking2 = _make_picking()
        engine.env.__getitem__.return_value = self._make_split_env_mock(picking2)

        assignments = [
            _make_assignment(wh1, [(prod1, 3.0)]),
            _make_assignment(wh2, [(prod2, 2.0)]),
        ]

        engine.apply_routing(order, assignments)

        # Order must have x_mf_split=True written
        order.write.assert_called_with({'x_mf_split': True})

    def test_two_assignments_sets_auto_split_on_first_picking(self):
        engine = self._make_engine_no_cross_border()

        prod1 = _make_product('ProdA')
        prod2 = _make_product('ProdB')
        wh1 = _make_warehouse('WH-A')
        wh2 = _make_warehouse('WH-B')

        picking1 = _make_picking()
        picking1.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking1, name='SO002')
        order.partner_shipping_id = MagicMock()
        order.partner_id = MagicMock()

        picking2 = _make_picking()
        engine.env.__getitem__.return_value = self._make_split_env_mock(picking2)

        assignments = [
            _make_assignment(wh1, [(prod1, 3.0)]),
            _make_assignment(wh2, [(prod2, 2.0)]),
        ]

        engine.apply_routing(order, assignments)

        # First picking should be written with auto_split
        write_calls = picking1.write.call_args_list
        self.assertTrue(any(
            c[0][0].get('x_mf_routed_by') == 'auto_split'
            for c in write_calls if c[0]
        ))

    def test_two_assignments_sets_auto_split_on_second_picking(self):
        engine = self._make_engine_no_cross_border()

        prod1 = _make_product('ProdA')
        prod2 = _make_product('ProdB')
        wh1 = _make_warehouse('WH-A')
        wh2 = _make_warehouse('WH-B')

        picking1 = _make_picking()
        picking1.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking1, name='SO002')
        order.partner_shipping_id = MagicMock()
        order.partner_id = MagicMock()

        picking2 = _make_picking()
        engine.env.__getitem__.return_value = self._make_split_env_mock(picking2)

        assignments = [
            _make_assignment(wh1, [(prod1, 3.0)]),
            _make_assignment(wh2, [(prod2, 2.0)]),
        ]

        engine.apply_routing(order, assignments)

        # Second picking should also be written with auto_split
        write_calls = picking2.write.call_args_list
        self.assertTrue(any(
            c[0][0].get('x_mf_routed_by') == 'auto_split'
            for c in write_calls if c[0]
        ))


# ---------------------------------------------------------------------------
# Tests: apply_routing — cross-border detection
# ---------------------------------------------------------------------------

class TestApplyRoutingCrossBorder(unittest.TestCase):
    """Cross-border assignment: sets x_mf_cross_border=True, status mf_held_review."""

    def test_cross_border_sets_held_review_status(self):
        engine = _make_engine()
        # Monkeypatch _is_cross_border to return True
        engine._is_cross_border = MagicMock(return_value=True)

        prod = _make_product()
        wh = _make_warehouse('WH-NZ')
        picking = _make_picking()
        picking.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking, name='SO003')
        assignments = [_make_assignment(wh, [(prod, 1.0)])]

        engine.env.__getitem__.return_value = MagicMock()
        engine.apply_routing(order, assignments)

        write_calls = picking.write.call_args_list
        self.assertTrue(any(
            c[0][0].get('x_mf_cross_border') is True and
            c[0][0].get('x_mf_status') == 'mf_held_review'
            for c in write_calls if c[0]
        ))

    def test_cross_border_is_cross_border_called_with_warehouse_and_picking(self):
        engine = _make_engine()
        engine._is_cross_border = MagicMock(return_value=True)

        prod = _make_product()
        wh = _make_warehouse('WH-NZ')
        picking = _make_picking()
        picking.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking, name='SO003')
        assignments = [_make_assignment(wh, [(prod, 1.0)])]

        engine.env.__getitem__.return_value = MagicMock()
        engine.apply_routing(order, assignments)

        engine._is_cross_border.assert_called_once_with(wh, picking)


# ---------------------------------------------------------------------------
# Tests: apply_routing — non-cross-border
# ---------------------------------------------------------------------------

class TestApplyRoutingNonCrossBorder(unittest.TestCase):
    """Non-cross-border assignment: sets x_mf_status='mf_queued'."""

    def test_non_cross_border_sets_queued_status(self):
        engine = _make_engine()
        engine._is_cross_border = MagicMock(return_value=False)

        prod = _make_product()
        wh = _make_warehouse('WH-AU')
        picking = _make_picking()
        picking.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking, name='SO004')
        assignments = [_make_assignment(wh, [(prod, 2.0)])]

        engine.env.__getitem__.return_value = MagicMock()
        engine.apply_routing(order, assignments)

        write_calls = picking.write.call_args_list
        self.assertTrue(any(
            c[0][0].get('x_mf_cross_border') is False and
            c[0][0].get('x_mf_status') == 'mf_queued'
            for c in write_calls if c[0]
        ))

    def test_non_cross_border_x_mf_cross_border_is_false(self):
        engine = _make_engine()
        engine._is_cross_border = MagicMock(return_value=False)

        prod = _make_product()
        wh = _make_warehouse('WH-AU')
        picking = _make_picking()
        picking.move_ids.filtered.return_value = []

        order = _make_order_with_picking(picking, name='SO004')
        assignments = [_make_assignment(wh, [(prod, 2.0)])]

        engine.env.__getitem__.return_value = MagicMock()
        engine.apply_routing(order, assignments)

        write_calls = picking.write.call_args_list
        self.assertTrue(any(
            c[0][0].get('x_mf_cross_border') is False
            for c in write_calls if c[0]
        ))


# ---------------------------------------------------------------------------
# Load picking_mf module under test
# ---------------------------------------------------------------------------

_PICKING_MF_PATH = _HERE.parent / 'models' / 'picking_mf.py'

if 'stock_3pl_mainfreight.models.picking_mf' not in sys.modules:
    _picking_spec = importlib.util.spec_from_file_location(
        'stock_3pl_mainfreight.models.picking_mf', str(_PICKING_MF_PATH)
    )
    _picking_mod = importlib.util.module_from_spec(_picking_spec)
    sys.modules['stock_3pl_mainfreight.models.picking_mf'] = _picking_mod
    _picking_spec.loader.exec_module(_picking_mod)
else:
    _picking_mod = sys.modules['stock_3pl_mainfreight.models.picking_mf']

StockPickingMF = _picking_mod.StockPickingMF
# Retrieve the stubbed UserError so we can assert on the correct class.
_UserError = sys.modules['odoo.exceptions'].UserError


# ---------------------------------------------------------------------------
# Tests: action_approve_cross_border
# ---------------------------------------------------------------------------

class TestApproveCrossBorder(unittest.TestCase):
    """action_approve_cross_border: validates status and advances to mf_queued."""

    def test_approve_advances_to_queued(self):
        """A picking with mf_held_review status is advanced to mf_queued."""
        # Build a mock picking that is in the correct held status.
        mock_picking = MagicMock()
        mock_picking.x_mf_status = 'mf_held_review'
        mock_picking.name = 'WH/OUT/00001'

        # Build a mock 'self' recordset that iterates over [mock_picking]
        # and has a write() method we can assert on.
        mock_self = MagicMock()
        mock_self.__iter__ = MagicMock(return_value=iter([mock_picking]))

        # Call the method as an unbound function, passing mock_self as self.
        StockPickingMF.action_approve_cross_border(mock_self)

        # write must have been called with the queued status.
        mock_self.write.assert_called_once_with({'x_mf_status': 'mf_queued'})

    def test_approve_raises_for_wrong_status(self):
        """A picking NOT in mf_held_review raises UserError; write is never called."""
        mock_picking = MagicMock()
        mock_picking.x_mf_status = 'mf_sent'
        mock_picking.name = 'WH/OUT/00002'

        mock_self = MagicMock()
        mock_self.__iter__ = MagicMock(return_value=iter([mock_picking]))

        with self.assertRaises(_UserError):
            StockPickingMF.action_approve_cross_border(mock_self)

        # write should never be reached when validation fails.
        mock_self.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
