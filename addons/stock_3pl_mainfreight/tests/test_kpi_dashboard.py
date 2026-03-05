"""
Pure-Python unit tests for mf.kpi.dashboard AbstractModel.

These tests run outside the Odoo test runner using plain unittest.TestCase.
They require the project root on PYTHONPATH so that Odoo addons are importable
(handled by conftest.py in the project root). Business logic is tested by
calling class methods on MagicMock instances.
"""
import ast
import os
import unittest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta


def _make_env(icp_params=None):
    """Build a mock env with configurable ir.config_parameter values."""
    env = MagicMock()
    icp = MagicMock()
    defaults = {
        'stock_3pl_mainfreight.kpi_difot_target': '95',
        'stock_3pl_mainfreight.kpi_ira_target': '98',
        'stock_3pl_mainfreight.kpi_exception_rate_target': '2',
        'stock_3pl_mainfreight.kpi_shrinkage_target': '0.5',
        'stock_3pl_mainfreight.kpi_difot_amber_offset': '5',
        'stock_3pl_mainfreight.kpi_ira_amber_offset': '3',
        'stock_3pl_mainfreight.difot_grace_days': '0',
        'stock_3pl_mainfreight.ira_tolerance': '0.005',
    }
    if icp_params:
        defaults.update(icp_params)
    icp.get_param = MagicMock(side_effect=lambda key, default=None: defaults.get(key, default))
    icp_model = MagicMock()
    icp_model.sudo.return_value = icp
    env.__getitem__ = MagicMock(side_effect=lambda key: {
        'ir.config_parameter': icp_model,
        'stock.picking': MagicMock(),
        'mf.soh.discrepancy': MagicMock(),
        'stock.quant': MagicMock(),
    }.get(key, MagicMock()))
    return env, icp


class TestKpiPureFunctions(unittest.TestCase):
    """Test the module-level pure functions."""

    def test_rag_green_at_target(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=95.0, target=95.0, lower_amber=90.0), 'green')

    def test_rag_green_above_target(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=97.0, target=95.0, lower_amber=90.0), 'green')

    def test_rag_amber_between_amber_and_target(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=92.0, target=95.0, lower_amber=90.0), 'amber')

    def test_rag_amber_at_lower_bound(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=90.0, target=95.0, lower_amber=90.0), 'amber')

    def test_rag_red_below_amber(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=88.0, target=95.0, lower_amber=90.0), 'red')

    def test_rag_lower_is_better_green(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status_lower_is_better
        self.assertEqual(_rag_status_lower_is_better(value=1.5, target=2.0, upper_amber=5.0), 'green')

    def test_rag_lower_is_better_amber(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status_lower_is_better
        self.assertEqual(_rag_status_lower_is_better(value=3.0, target=2.0, upper_amber=5.0), 'amber')

    def test_rag_lower_is_better_red(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status_lower_is_better
        self.assertEqual(_rag_status_lower_is_better(value=6.0, target=2.0, upper_amber=5.0), 'red')

    def test_compute_exception_rate_no_orders(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_exception_rate
        self.assertEqual(_compute_exception_rate(total=0, exceptions=0), 0.0)

    def test_compute_exception_rate_calculation(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_exception_rate
        self.assertAlmostEqual(_compute_exception_rate(total=100, exceptions=3), 3.0)

    def test_compute_difot_no_delivered(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_difot
        self.assertEqual(_compute_difot(on_time_in_full=0, total_delivered=0), 100.0)

    def test_compute_difot_calculation(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_difot
        self.assertAlmostEqual(_compute_difot(on_time_in_full=95, total_delivered=100), 95.0)

    def test_compute_difot_perfect(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_difot
        self.assertEqual(_compute_difot(on_time_in_full=50, total_delivered=50), 100.0)


class TestKpiDashboardModel(unittest.TestCase):
    """Test the MfKpiDashboard model methods."""

    def test_get_kpi_targets_reads_icp(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env, _ = _make_env()
        dashboard.env = env
        result = MfKpiDashboard.get_kpi_targets(dashboard)
        self.assertEqual(result['difot_target'], 95.0)
        self.assertEqual(result['ira_target'], 98.0)
        self.assertEqual(result['exception_rate_target'], 2.0)
        self.assertEqual(result['shrinkage_target'], 0.5)
        self.assertEqual(result['difot_amber_offset'], 5.0)
        self.assertEqual(result['ira_amber_offset'], 3.0)

    def test_get_kpi_targets_custom_values(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env, _ = _make_env({'stock_3pl_mainfreight.kpi_difot_target': '97'})
        dashboard.env = env
        result = MfKpiDashboard.get_kpi_targets(dashboard)
        self.assertEqual(result['difot_target'], 97.0)

    def test_get_kpi_summary_has_required_keys(self):
        """get_kpi_summary returns all required top-level keys."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env, _ = _make_env()
        dashboard.env = env
        # Patch private helpers to return simple values
        dashboard.get_kpi_targets = MagicMock(return_value={
            'difot_target': 95.0, 'ira_target': 98.0,
            'exception_rate_target': 2.0, 'shrinkage_target': 0.5,
            'difot_amber_offset': 5.0, 'ira_amber_offset': 3.0,
            'difot_grace_days': 0, 'ira_tolerance': 0.005,
        })
        dashboard._compute_difot_value = MagicMock(return_value=96.0)
        dashboard._compute_ira_value = MagicMock(return_value=99.0)
        dashboard._compute_exception_and_inflight = MagicMock(return_value=(1.0, 5))
        dashboard._compute_shrinkage_value = MagicMock(return_value=0.3)
        dashboard._compute_today_summary = MagicMock(return_value={'sent': 0, 'received': 0, 'delivered': 0, 'exceptions': 0})
        result = MfKpiDashboard.get_kpi_summary(dashboard)
        for key in ('difot', 'ira', 'exception_rate', 'shrinkage', 'in_flight', 'today', 'targets', 'data_available'):
            self.assertIn(key, result)

    def test_get_kpi_summary_difot_rag_green(self):
        """DIFOT value above target returns green RAG."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env, _ = _make_env()
        dashboard.env = env
        dashboard.get_kpi_targets = MagicMock(return_value={
            'difot_target': 95.0, 'ira_target': 98.0,
            'exception_rate_target': 2.0, 'shrinkage_target': 0.5,
            'difot_amber_offset': 5.0, 'ira_amber_offset': 3.0,
            'difot_grace_days': 0, 'ira_tolerance': 0.005,
        })
        dashboard._compute_difot_value = MagicMock(return_value=96.0)
        dashboard._compute_ira_value = MagicMock(return_value=99.0)
        dashboard._compute_exception_and_inflight = MagicMock(return_value=(1.0, 5))
        dashboard._compute_shrinkage_value = MagicMock(return_value=0.3)
        dashboard._compute_today_summary = MagicMock(return_value={'sent': 0, 'received': 0, 'delivered': 0, 'exceptions': 0})
        result = MfKpiDashboard.get_kpi_summary(dashboard)
        self.assertEqual(result['difot']['rag'], 'green')
        self.assertEqual(result['difot']['value'], 96.0)

    def test_data_available_false_on_fresh_install(self):
        """data_available is False when no MF-tracked pickings exist."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env, _ = _make_env()
        dashboard.env = env
        dashboard.get_kpi_targets = MagicMock(return_value={
            'difot_target': 95.0, 'ira_target': 98.0,
            'exception_rate_target': 2.0, 'shrinkage_target': 0.5,
            'difot_amber_offset': 5.0, 'ira_amber_offset': 3.0,
            'difot_grace_days': 0, 'ira_tolerance': 0.005,
        })
        dashboard._compute_difot_value = MagicMock(return_value=100.0)
        dashboard._compute_ira_value = MagicMock(return_value=100.0)
        dashboard._compute_exception_and_inflight = MagicMock(return_value=(0.0, 0))
        dashboard._compute_shrinkage_value = MagicMock(return_value=0.0)
        dashboard._compute_today_summary = MagicMock(return_value={'sent': 0, 'received': 0, 'delivered': 0, 'exceptions': 0})
        # _compute_data_available is the extracted helper — mock it to return False
        dashboard._compute_data_available = MagicMock(return_value=False)
        result = MfKpiDashboard.get_kpi_summary(dashboard)
        self.assertFalse(result['data_available'])


class TestDifotSqlPath(unittest.TestCase):
    """Test _compute_difot_value SQL branch for field-to-field date comparison."""

    def _make_dashboard(self, total, no_deadline, sql_on_time):
        """Return a dashboard mock with env wired for _compute_difot_value."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env = MagicMock()

        picking_mock = MagicMock()
        # search_count returns: total on first call, no_deadline on second call
        picking_mock.search_count = MagicMock(side_effect=[total, no_deadline])
        env.__getitem__ = MagicMock(side_effect=lambda key: {
            'stock.picking': picking_mock,
        }.get(key, MagicMock()))

        # Cursor mock
        cr = MagicMock()
        cr.fetchone.return_value = (sql_on_time,)
        env.cr = cr

        dashboard.env = env
        return dashboard

    def test_no_delivered_returns_100(self):
        """When total is 0, return 100.0 without hitting SQL."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env = MagicMock()
        picking_mock = MagicMock()
        picking_mock.search_count.return_value = 0
        env.__getitem__ = MagicMock(return_value=picking_mock)
        dashboard.env = env
        since = datetime(2026, 1, 1)
        result = MfKpiDashboard._compute_difot_value(dashboard, since, 0)
        self.assertEqual(result, 100.0)
        # SQL must not be called when there are no delivered orders
        env.cr.execute.assert_not_called()

    def test_all_on_time_with_no_deadline(self):
        """All delivered with no deadline → 100% DIFOT."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = self._make_dashboard(total=10, no_deadline=10, sql_on_time=0)
        since = datetime(2026, 1, 1)
        result = MfKpiDashboard._compute_difot_value(dashboard, since, 0)
        self.assertEqual(result, 100.0)

    def test_partial_on_time_via_sql(self):
        """Mixed: 5 no-deadline + 3 sql on-time out of 10 total → 80%."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = self._make_dashboard(total=10, no_deadline=5, sql_on_time=3)
        since = datetime(2026, 1, 1)
        result = MfKpiDashboard._compute_difot_value(dashboard, since, 0)
        self.assertAlmostEqual(result, 80.0)

    def test_grace_days_passed_to_sql(self):
        """grace_days value is forwarded to the SQL execute call."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        since = datetime(2026, 1, 1)
        dashboard = self._make_dashboard(total=5, no_deadline=0, sql_on_time=5)
        MfKpiDashboard._compute_difot_value(dashboard, since, grace_days=2)
        # Verify that the execute call was made and grace_days=2 was in the params
        call_args = dashboard.env.cr.execute.call_args
        self.assertIsNotNone(call_args)
        params = call_args[0][1]  # positional args: (sql, params)
        self.assertIn(2, params)


class TestShrinkageSqlPath(unittest.TestCase):
    """Test _compute_shrinkage_value SQL + read_group path."""

    def _make_dashboard(self, sql_total_lost, quant_read_group_result):
        """Return a dashboard mock with env wired for _compute_shrinkage_value.

        quant_read_group_result must be in _read_group format: list of tuples,
        e.g. [(1000.0,)] for a single aggregate result.
        """
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env = MagicMock()

        quant_mock = MagicMock()
        quant_mock._read_group.return_value = quant_read_group_result
        env.__getitem__ = MagicMock(side_effect=lambda key: {
            'stock.quant': quant_mock,
        }.get(key, MagicMock()))

        cr = MagicMock()
        cr.fetchone.return_value = (sql_total_lost,)
        env.cr = cr

        dashboard.env = env
        return dashboard

    def test_no_stock_returns_zero(self):
        """When total_stock is 0 (empty _read_group), denominator defaults to 1.0 → not division error."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = self._make_dashboard(sql_total_lost=0, quant_read_group_result=[])
        result = MfKpiDashboard._compute_shrinkage_value(dashboard)
        self.assertEqual(result, 0.0)

    def test_shrinkage_calculation(self):
        """50 units lost out of 1000 in stock → 5.0% shrinkage."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = self._make_dashboard(
            sql_total_lost=50,
            quant_read_group_result=[(1000.0,)],
        )
        result = MfKpiDashboard._compute_shrinkage_value(dashboard)
        self.assertAlmostEqual(result, 5.0)

    def test_sql_execute_called_once(self):
        """SQL execute is called exactly once for the loss sum."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = self._make_dashboard(
            sql_total_lost=10,
            quant_read_group_result=[(500.0,)],
        )
        MfKpiDashboard._compute_shrinkage_value(dashboard)
        dashboard.env.cr.execute.assert_called_once()


class TestIraDistinctProducts(unittest.TestCase):
    """Test _compute_ira_value uses _read_group for distinct-product counting."""

    def _make_dashboard(self, quant_groups, discrepancy_groups):
        """Return a dashboard mock wired for _compute_ira_value.

        quant_groups and discrepancy_groups must be in _read_group format:
        list of tuples — one tuple per group, e.g. [(prod_record, 1), ...].
        _compute_ira_value only calls len() on these, so any list of tuples works.
        """
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env = MagicMock()

        quant_mock = MagicMock()
        quant_mock._read_group.return_value = quant_groups
        discrepancy_mock = MagicMock()
        discrepancy_mock._read_group.return_value = discrepancy_groups
        env.__getitem__ = MagicMock(side_effect=lambda key: {
            'stock.quant': quant_mock,
            'mf.soh.discrepancy': discrepancy_mock,
        }.get(key, MagicMock()))

        dashboard.env = env
        return dashboard

    def test_no_stock_returns_100(self):
        """No tracked stock → IRA is 100.0 (nothing to be wrong about)."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = self._make_dashboard(quant_groups=[], discrepancy_groups=[])
        since = datetime(2026, 1, 1)
        result = MfKpiDashboard._compute_ira_value(dashboard, since, 0.005)
        self.assertEqual(result, 100.0)

    def test_no_discrepancies_returns_100(self):
        """10 distinct SKUs tracked, 0 discrepancies → 100.0%."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        # _read_group returns one tuple per group; content doesn't matter — only len() is used
        quant_groups = [(MagicMock(), 1) for _ in range(10)]
        dashboard = self._make_dashboard(quant_groups=quant_groups, discrepancy_groups=[])
        since = datetime(2026, 1, 1)
        result = MfKpiDashboard._compute_ira_value(dashboard, since, 0.005)
        self.assertAlmostEqual(result, 100.0)

    def test_distinct_product_ira_calculation(self):
        """10 distinct SKUs, 2 with open discrepancy → 80.0%."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        quant_groups = [(MagicMock(), 1) for _ in range(10)]
        discrepancy_groups = [(MagicMock(), 1), (MagicMock(), 1)]
        dashboard = self._make_dashboard(
            quant_groups=quant_groups,
            discrepancy_groups=discrepancy_groups,
        )
        since = datetime(2026, 1, 1)
        result = MfKpiDashboard._compute_ira_value(dashboard, since, 0.005)
        self.assertAlmostEqual(result, 80.0)

    def test_ira_clamped_at_zero(self):
        """More discrepancies than SKUs (edge case) → clamped to 0.0, not negative."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        quant_groups = [(MagicMock(), 1)]
        discrepancy_groups = [(MagicMock(), 1), (MagicMock(), 1), (MagicMock(), 1)]
        dashboard = self._make_dashboard(
            quant_groups=quant_groups,
            discrepancy_groups=discrepancy_groups,
        )
        since = datetime(2026, 1, 1)
        result = MfKpiDashboard._compute_ira_value(dashboard, since, 0.005)
        self.assertEqual(result, 0.0)


class TestKpiDashboardNoDeprecatedReadGroup(unittest.TestCase):
    """AST-based guard: kpi_dashboard.py must not use the deprecated read_group() API."""

    @classmethod
    def _src_path(cls):
        return os.path.join(
            os.path.dirname(__file__), '..', 'models', 'kpi_dashboard.py'
        )

    @classmethod
    def _parsed_source(cls):
        with open(cls._src_path(), encoding='utf-8') as f:
            return f.read(), ast.parse(f.read() if False else open(cls._src_path()).read())

    def test_kpi_dashboard_uses_no_deprecated_read_group(self):
        """kpi_dashboard.py must not use the deprecated .read_group() API."""
        source, tree = self._parsed_source()
        violations = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == 'read_group'
                and not isinstance(node.ctx, ast.Store)
            ):
                violations.append(f"Line {node.lineno}: .read_group() call found")

        self.assertFalse(
            violations,
            "kpi_dashboard.py still uses deprecated read_group():\n"
            + "\n".join(violations)
            + "\nMigrate to ._read_group(domain, groupby, aggregates)",
        )

    def test_kpi_dashboard_uses_private_read_group(self):
        """kpi_dashboard.py must use ._read_group() (private API, Odoo 19+)."""
        source, tree = self._parsed_source()
        private_calls = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == '_read_group'
            ):
                private_calls.append(node.lineno)

        self.assertGreaterEqual(
            len(private_calls),
            3,
            f"Expected at least 3 calls to ._read_group(), found {len(private_calls)}: lines {private_calls}",
        )
