"""
Pure-Python unit tests for mf.kpi.dashboard AbstractModel.

These tests run outside the Odoo test runner using plain unittest.TestCase.
They require the project root on PYTHONPATH so that Odoo addons are importable
(handled by conftest.py in the project root). Business logic is tested by
calling class methods on MagicMock instances.
"""
import unittest
from unittest.mock import MagicMock, patch
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
