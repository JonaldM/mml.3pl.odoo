# addons/stock_3pl_mainfreight/tests/test_push_cron.py
"""Pure-Python mock-based tests for MFPushCron.

No Odoo runtime required. Odoo stubs are installed by the repo-level
conftest.py before pytest collects this module, so we only need to:
  1. Load the module under test directly from the filesystem.
  2. Use MagicMock for all Odoo ORM calls.
"""
import sys
import unittest
import importlib.util
import pathlib
from unittest.mock import MagicMock, call

# ---------------------------------------------------------------------------
# Load the module under test directly from disk
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_PUSH_CRON_PATH = _HERE.parent / 'models' / 'push_cron.py'

if 'stock_3pl_mainfreight.models.push_cron' not in sys.modules:
    _push_spec = importlib.util.spec_from_file_location(
        'stock_3pl_mainfreight.models.push_cron', str(_PUSH_CRON_PATH)
    )
    _push_mod = importlib.util.module_from_spec(_push_spec)
    sys.modules['stock_3pl_mainfreight.models.push_cron'] = _push_mod
    _push_spec.loader.exec_module(_push_mod)
else:
    _push_mod = sys.modules['stock_3pl_mainfreight.models.push_cron']

MFPushCron = _push_mod.MFPushCron


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cron(env_lookup=None):
    """Construct an MFPushCron instance with a mocked self.env.

    env_lookup: optional callable used as side_effect for env.__getitem__.
    If not provided, env['anything'] returns a plain MagicMock.
    """
    cron = object.__new__(MFPushCron)
    env = MagicMock()
    if env_lookup is not None:
        env.__getitem__ = MagicMock(side_effect=env_lookup)
    cron.env = env
    return cron


# ---------------------------------------------------------------------------
# Tests: model metadata
# ---------------------------------------------------------------------------

class TestMFPushCronModel(unittest.TestCase):
    """Structural checks: correct _name and AbstractModel lineage."""

    def test_model_name(self):
        self.assertEqual(MFPushCron._name, 'mf.push.cron')

    def test_description(self):
        self.assertEqual(MFPushCron._description, 'MF Push Cron')


# ---------------------------------------------------------------------------
# Tests: _run_mf_push
# ---------------------------------------------------------------------------

class TestRunMFPush(unittest.TestCase):
    """_run_mf_push calls _route_pending_orders first, then the core queue processor."""

    def test_run_mf_push_calls_route_pending_orders_first(self):
        """_route_pending_orders must be invoked before _process_outbound_queue."""
        call_order = []

        cron = object.__new__(MFPushCron)
        env = MagicMock()

        mock_message_model = MagicMock()
        mock_message_model._process_outbound_queue.side_effect = (
            lambda: call_order.append('process_outbound_queue')
        )

        def env_lookup(key):
            if key == '3pl.message':
                return mock_message_model
            return MagicMock()

        env.__getitem__ = MagicMock(side_effect=env_lookup)
        cron.env = env

        # Track _route_pending_orders via a patch on the instance
        cron._route_pending_orders = MagicMock(
            side_effect=lambda: call_order.append('route_pending_orders')
        )

        cron._run_mf_push()

        self.assertEqual(call_order, ['route_pending_orders', 'process_outbound_queue'])

    def test_run_mf_push_calls_process_outbound_queue(self):
        """_run_mf_push must call 3pl.message._process_outbound_queue."""
        cron = object.__new__(MFPushCron)
        env = MagicMock()

        mock_message_model = MagicMock()

        def env_lookup(key):
            if key == '3pl.message':
                return mock_message_model
            return MagicMock()

        env.__getitem__ = MagicMock(side_effect=env_lookup)
        cron.env = env
        cron._route_pending_orders = MagicMock()

        cron._run_mf_push()

        mock_message_model._process_outbound_queue.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _route_pending_orders
# ---------------------------------------------------------------------------

class TestRoutePendingOrders(unittest.TestCase):
    """_route_pending_orders routes unrouted sale orders then applies the routing."""

    def test_route_pending_skips_already_routed_pickings(self):
        """Already-routed pickings are NOT re-processed by _route_pending_orders.

        The search domain filters on x_mf_routed_by=False, so already-routed
        pickings are excluded from the result. mapped('sale_id').filtered(...)
        returns an empty iterable, meaning route_engine.route_order is never called.
        """
        mock_route_engine = MagicMock()
        mock_split_engine = MagicMock()

        # Simulate stock.picking.search returning no unrouted pickings
        # (because already-routed pickings are filtered out by the domain)
        empty_picking_rs = MagicMock()
        # mapped('sale_id') returns a MagicMock whose .filtered() returns an empty list
        empty_sale_ids = MagicMock()
        empty_sale_ids.filtered.return_value = []
        empty_picking_rs.mapped.return_value = empty_sale_ids

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = empty_picking_rs

        def env_lookup(key):
            if key == 'mf.route.engine':
                return mock_route_engine
            if key == 'mf.split.engine':
                return mock_split_engine
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._route_pending_orders()

        # route_order must never be called — no unrouted orders found
        mock_route_engine.route_order.assert_not_called()

    def test_route_pending_searches_correct_domain(self):
        """search is called with x_mf_routed_by=False and relevant states."""
        mock_route_engine = MagicMock()
        mock_split_engine = MagicMock()

        empty_picking_rs = MagicMock()
        empty_sale_ids = MagicMock()
        empty_sale_ids.filtered.return_value = []
        empty_picking_rs.mapped.return_value = empty_sale_ids

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = empty_picking_rs

        def env_lookup(key):
            if key == 'mf.route.engine':
                return mock_route_engine
            if key == 'mf.split.engine':
                return mock_split_engine
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._route_pending_orders()

        mock_picking_model.search.assert_called_once()
        domain = mock_picking_model.search.call_args[0][0]
        self.assertIn(('x_mf_routed_by', '=', False), domain)
        # States must include confirmed, assigned, waiting
        state_tuple = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'state'), None
        )
        self.assertIsNotNone(state_tuple)
        for state in ('confirmed', 'assigned', 'waiting'):
            self.assertIn(state, state_tuple[2])

    def test_route_pending_calls_route_and_apply_for_each_order(self):
        """For each unrouted order, route_order and apply_routing are both called."""
        mock_route_engine = MagicMock()
        mock_split_engine = MagicMock()

        # Two confirmed sale orders to route
        order_a = MagicMock()
        order_a.id = 1
        order_a.name = 'SO001'
        order_a.state = 'sale'

        order_b = MagicMock()
        order_b.id = 2
        order_b.name = 'SO002'
        order_b.state = 'sale'

        mock_picking_rs = MagicMock()
        # filtered() is called on the mapped result — return both orders
        mock_orders_rs = MagicMock()
        mock_orders_rs.__iter__ = MagicMock(return_value=iter([order_a, order_b]))
        mock_picking_rs.mapped.return_value.filtered.return_value = mock_orders_rs

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = mock_picking_rs

        assignments_a = [{'warehouse': MagicMock(), 'lines': []}]
        assignments_b = [{'warehouse': MagicMock(), 'lines': []}]
        mock_route_engine.route_order.side_effect = [assignments_a, assignments_b]

        def env_lookup(key):
            if key == 'mf.route.engine':
                return mock_route_engine
            if key == 'mf.split.engine':
                return mock_split_engine
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._route_pending_orders()

        self.assertEqual(mock_route_engine.route_order.call_count, 2)
        self.assertEqual(mock_split_engine.apply_routing.call_count, 2)

        mock_route_engine.route_order.assert_any_call(order_a)
        mock_route_engine.route_order.assert_any_call(order_b)
        mock_split_engine.apply_routing.assert_any_call(order_a, assignments_a)
        mock_split_engine.apply_routing.assert_any_call(order_b, assignments_b)

    def test_route_pending_continues_after_order_error(self):
        """A routing failure on one order must not prevent subsequent orders being routed."""
        mock_route_engine = MagicMock()
        mock_split_engine = MagicMock()

        order_bad = MagicMock()
        order_bad.id = 10
        order_bad.name = 'SO010'
        order_bad.state = 'sale'

        order_good = MagicMock()
        order_good.id = 11
        order_good.name = 'SO011'
        order_good.state = 'sale'

        mock_picking_rs = MagicMock()
        mock_orders_rs = MagicMock()
        mock_orders_rs.__iter__ = MagicMock(return_value=iter([order_bad, order_good]))
        mock_picking_rs.mapped.return_value.filtered.return_value = mock_orders_rs

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = mock_picking_rs

        # First order raises, second succeeds
        mock_route_engine.route_order.side_effect = [RuntimeError('boom'), []]

        def env_lookup(key):
            if key == 'mf.route.engine':
                return mock_route_engine
            if key == 'mf.split.engine':
                return mock_split_engine
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        # Must not raise
        cron._route_pending_orders()

        # Both orders were attempted
        self.assertEqual(mock_route_engine.route_order.call_count, 2)
        # apply_routing called once (for the good order only)
        self.assertEqual(mock_split_engine.apply_routing.call_count, 1)
        mock_split_engine.apply_routing.assert_called_once_with(order_good, [])


if __name__ == '__main__':
    unittest.main()
