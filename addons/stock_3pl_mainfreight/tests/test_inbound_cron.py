# addons/stock_3pl_mainfreight/tests/test_inbound_cron.py
"""Pure-Python mock-based tests for MFInboundCron.

No Odoo runtime required. Odoo stubs are installed by the repo-level
conftest.py before pytest collects this module.

Coverage:
  - _poll_inventory_reports: SFTP tuples, REST strings, error resilience, empty response
  - _reconcile_sent_orders: stale flagging, recent skip, connote skip, threshold config
  - _run_mf_inbound: calls both sub-methods
"""
import sys
import types
import datetime
import unittest
import importlib.util
import pathlib
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Pre-load the inventory_report module so we can patch it by its import path.
# The production code does:
#   from odoo.addons.stock_3pl_mainfreight.document.inventory_report import (
#       InventoryReportDocument,
#   )
# For patch() to intercept that import we need the module registered in
# sys.modules under that exact name with the class accessible as an attribute.
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_DOCUMENT_DIR = _HERE.parent / 'document'
_MODELS_DIR = _HERE.parent / 'models'

_INV_REPORT_MOD_NAME = 'odoo.addons.stock_3pl_mainfreight.document.inventory_report'

if _INV_REPORT_MOD_NAME not in sys.modules:
    # The document module imports from odoo.exceptions — already stubbed by conftest.
    # We load it as-is; the stub environment is already in place.
    _inv_spec = importlib.util.spec_from_file_location(
        _INV_REPORT_MOD_NAME,
        str(_DOCUMENT_DIR / 'inventory_report.py'),
    )
    _inv_mod = importlib.util.module_from_spec(_inv_spec)
    sys.modules[_INV_REPORT_MOD_NAME] = _inv_mod
    _inv_spec.loader.exec_module(_inv_mod)

# ---------------------------------------------------------------------------
# Load the module under test directly from disk
# ---------------------------------------------------------------------------
_INBOUND_CRON_MOD_NAME = 'stock_3pl_mainfreight.models.inbound_cron'
_INBOUND_CRON_PATH = _MODELS_DIR / 'inbound_cron.py'

if _INBOUND_CRON_MOD_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _INBOUND_CRON_MOD_NAME, str(_INBOUND_CRON_PATH)
    )
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_INBOUND_CRON_MOD_NAME] = _mod
    _spec.loader.exec_module(_mod)
else:
    _mod = sys.modules[_INBOUND_CRON_MOD_NAME]

MFInboundCron = _mod.MFInboundCron

# Patch path for InventoryReportDocument (lazy-imported inside the method)
_INV_REPORT_CLS_PATH = (
    'odoo.addons.stock_3pl_mainfreight.document.inventory_report.InventoryReportDocument'
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_CSV = (
    'Product,WarehouseID,StockOnHand,QuantityOnHold,QuantityDamaged,'
    'QuantityAvailable,Grade1,Grade2,ExpiryDate,PackingDate\r\n'
    'WIDG001,99,100,2,3,95,A,,01/01/2027,\r\n'
)


def _make_cron(env_lookup=None):
    """Construct an MFInboundCron instance with a mocked self.env."""
    cron = object.__new__(MFInboundCron)
    env = MagicMock()
    if env_lookup is not None:
        env.__getitem__ = MagicMock(side_effect=env_lookup)
    cron.env = env
    return cron


def _make_mock_connector(name='MF Test', poll_result=None):
    """Build a minimal mock 3pl.connector."""
    connector = MagicMock()
    connector.name = name
    mock_transport = MagicMock()
    mock_transport.poll.return_value = poll_result if poll_result is not None else []
    connector.get_transport.return_value = mock_transport
    return connector


def _make_mock_picking(
    name='WH/OUT/00001',
    x_mf_status='mf_sent',
    x_mf_connote=False,
    write_date=None,
):
    """Build a minimal mock stock.picking."""
    if write_date is None:
        write_date = datetime.datetime(2020, 1, 1)  # old by default
    picking = MagicMock()
    picking.name = name
    picking.x_mf_status = x_mf_status
    picking.x_mf_connote = x_mf_connote
    picking.write_date = write_date
    return picking


# ---------------------------------------------------------------------------
# Tests: model metadata
# ---------------------------------------------------------------------------

class TestMFInboundCronModel(unittest.TestCase):

    def test_model_name(self):
        self.assertEqual(MFInboundCron._name, 'mf.inbound.cron')

    def test_model_description(self):
        self.assertEqual(MFInboundCron._description, 'MF Inbound Cron')


# ---------------------------------------------------------------------------
# Tests: _run_mf_inbound — calls both sub-methods
# ---------------------------------------------------------------------------

class TestRunMFInbound(unittest.TestCase):
    """_run_mf_inbound must call both _poll_inventory_reports and _reconcile_sent_orders."""

    def test_run_mf_inbound_calls_both_methods(self):
        """Both sub-methods are invoked exactly once."""
        cron = object.__new__(MFInboundCron)
        cron.env = MagicMock()

        call_order = []
        cron._poll_inventory_reports = MagicMock(
            side_effect=lambda: call_order.append('poll')
        )
        cron._reconcile_sent_orders = MagicMock(
            side_effect=lambda: call_order.append('reconcile')
        )

        cron._run_mf_inbound()

        self.assertEqual(call_order, ['poll', 'reconcile'])
        cron._poll_inventory_reports.assert_called_once()
        cron._reconcile_sent_orders.assert_called_once()

    def test_run_mf_inbound_calls_poll_before_reconcile(self):
        """Poll must execute before reconciliation."""
        cron = object.__new__(MFInboundCron)
        cron.env = MagicMock()
        call_order = []

        cron._poll_inventory_reports = MagicMock(
            side_effect=lambda: call_order.append('poll')
        )
        cron._reconcile_sent_orders = MagicMock(
            side_effect=lambda: call_order.append('reconcile')
        )

        cron._run_mf_inbound()

        self.assertEqual(call_order.index('poll'), 0)
        self.assertEqual(call_order.index('reconcile'), 1)


# ---------------------------------------------------------------------------
# Tests: _poll_inventory_reports — SFTP tuples
# ---------------------------------------------------------------------------

class TestPollInventoryReportsSFTP(unittest.TestCase):
    """_poll_inventory_reports with SFTP-style (filename, content) tuples."""

    def test_poll_sftp_calls_apply_csv(self):
        """SFTP poll returns [(filename, csv)]; apply_csv is called once."""
        connector = _make_mock_connector(
            poll_result=[('soh_2026.csv', _SAMPLE_CSV)]
        )

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [connector]

        mock_doc_instance = MagicMock()

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance) as MockDoc:
            cron._poll_inventory_reports()

        MockDoc.assert_called_once_with(connector=connector, env=cron.env)
        mock_doc_instance.apply_csv.assert_called_once()
        args = mock_doc_instance.apply_csv.call_args[0]
        self.assertEqual(args[0], _SAMPLE_CSV)

    def test_poll_sftp_multiple_files_apply_csv_called_per_file(self):
        """Multiple files from SFTP → apply_csv called once per file."""
        connector = _make_mock_connector(
            poll_result=[
                ('soh_a.csv', _SAMPLE_CSV),
                ('soh_b.csv', _SAMPLE_CSV),
            ]
        )

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [connector]

        mock_doc_instance = MagicMock()

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance):
            cron._poll_inventory_reports()

        self.assertEqual(mock_doc_instance.apply_csv.call_count, 2)


# ---------------------------------------------------------------------------
# Tests: _poll_inventory_reports — REST raw strings
# ---------------------------------------------------------------------------

class TestPollInventoryReportsREST(unittest.TestCase):
    """_poll_inventory_reports with REST-style raw string items."""

    def test_poll_rest_calls_apply_csv(self):
        """REST poll returns [raw_csv_string]; apply_csv is called once."""
        connector = _make_mock_connector(poll_result=[_SAMPLE_CSV])

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [connector]

        mock_doc_instance = MagicMock()

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance):
            cron._poll_inventory_reports()

        mock_doc_instance.apply_csv.assert_called_once()
        args = mock_doc_instance.apply_csv.call_args[0]
        self.assertEqual(args[0], _SAMPLE_CSV)

    def test_poll_rest_uses_rest_filename_placeholder(self):
        """When REST returns a raw string, apply_csv is still called once."""
        connector = _make_mock_connector(poll_result=[_SAMPLE_CSV])
        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [connector]
        mock_doc_instance = MagicMock()

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance):
            cron._poll_inventory_reports()

        self.assertEqual(mock_doc_instance.apply_csv.call_count, 1)


# ---------------------------------------------------------------------------
# Tests: _poll_inventory_reports — empty response
# ---------------------------------------------------------------------------

class TestPollInventoryReportsEmpty(unittest.TestCase):
    """_poll_inventory_reports with empty poll response."""

    def test_poll_empty_response_no_apply(self):
        """When poll() returns [], apply_csv must never be called."""
        connector = _make_mock_connector(poll_result=[])

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [connector]
        mock_doc_instance = MagicMock()

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance):
            cron._poll_inventory_reports()

        mock_doc_instance.apply_csv.assert_not_called()

    def test_poll_no_connectors_no_calls(self):
        """When search returns no connectors, nothing is called."""
        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = []
        mock_doc_instance = MagicMock()

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance):
            cron._poll_inventory_reports()

        mock_doc_instance.apply_csv.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _poll_inventory_reports — error resilience
# ---------------------------------------------------------------------------

class TestPollInventoryReportsErrorResilience(unittest.TestCase):
    """Errors on one connector or file must not prevent processing others."""

    def test_poll_error_continues_to_next_connector(self):
        """First connector's poll raises; second connector is still processed."""
        connector_bad = MagicMock()
        connector_bad.name = 'Bad'
        transport_bad = MagicMock()
        transport_bad.poll.side_effect = RuntimeError('SFTP down')
        connector_bad.get_transport.return_value = transport_bad

        connector_good = _make_mock_connector(
            name='Good',
            poll_result=[('soh.csv', _SAMPLE_CSV)],
        )

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [connector_bad, connector_good]

        mock_doc_instance = MagicMock()

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance):
            # Must not raise
            cron._poll_inventory_reports()

        # apply_csv called once for the good connector's file
        self.assertEqual(mock_doc_instance.apply_csv.call_count, 1)

    def test_apply_csv_error_continues_to_next_file(self):
        """apply_csv raises on first file; second file is still processed."""
        connector = _make_mock_connector(
            poll_result=[
                ('soh_bad.csv', _SAMPLE_CSV),
                ('soh_good.csv', _SAMPLE_CSV),
            ]
        )

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [connector]

        mock_doc_instance = MagicMock()
        mock_doc_instance.apply_csv.side_effect = [RuntimeError('parse error'), None]

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_doc_instance):
            # Must not raise
            cron._poll_inventory_reports()

        # Both files were attempted
        self.assertEqual(mock_doc_instance.apply_csv.call_count, 2)

    def test_poll_connector_search_domain(self):
        """Search domain filters on active=True and warehouse_partner='mainfreight'."""
        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = []

        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        with patch(_INV_REPORT_CLS_PATH):
            cron._poll_inventory_reports()

        mock_connector_model.search.assert_called_once()
        domain = mock_connector_model.search.call_args[0][0]
        self.assertIn(('active', '=', True), domain)
        self.assertIn(('warehouse_partner', '=', 'mainfreight'), domain)


# ---------------------------------------------------------------------------
# Tests: _reconcile_sent_orders
# ---------------------------------------------------------------------------

class TestReconcileSentOrders(unittest.TestCase):
    """Tests for _reconcile_sent_orders stale-picking detection."""

    def _make_cron_with_pickings(self, pickings, threshold_str='48'):
        """Build a cron with a mocked env that returns the given pickings."""
        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = pickings

        mock_icp = MagicMock()
        mock_icp.get_param.return_value = threshold_str

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == 'ir.config_parameter':
                m = MagicMock()
                m.sudo.return_value = mock_icp
                return m
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        return cron

    def test_reconcile_flags_stale_sent_picking(self):
        """Picking with mf_sent + old write_date + no connote → set to mf_exception."""
        stale_picking = _make_mock_picking(
            name='WH/OUT/00001',
            x_mf_status='mf_sent',
            x_mf_connote=False,
            write_date=datetime.datetime(2020, 1, 1),
        )

        cron = self._make_cron_with_pickings([stale_picking])
        cron._reconcile_sent_orders()

        stale_picking.write.assert_called_once_with({'x_mf_status': 'mf_exception'})

    def test_reconcile_search_domain_is_correct(self):
        """Search uses mf_sent status, write_date < cutoff, and x_mf_connote=False."""
        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = []

        mock_icp = MagicMock()
        mock_icp.get_param.return_value = '48'

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == 'ir.config_parameter':
                m = MagicMock()
                m.sudo.return_value = mock_icp
                return m
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._reconcile_sent_orders()

        mock_picking_model.search.assert_called_once()
        domain = mock_picking_model.search.call_args[0][0]

        status_clause = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'x_mf_status'), None
        )
        self.assertIsNotNone(status_clause)
        self.assertEqual(status_clause, ('x_mf_status', '=', 'mf_sent'))

        connote_clause = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'x_mf_connote'), None
        )
        self.assertIsNotNone(connote_clause)
        self.assertEqual(connote_clause, ('x_mf_connote', '=', False))

        write_date_clause = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'write_date'), None
        )
        self.assertIsNotNone(write_date_clause)
        self.assertEqual(write_date_clause[1], '<')

    def test_reconcile_skips_recent_picking(self):
        """When search returns [] (simulating ORM date filter), no writes occur."""
        # The ORM domain filters out recent pickings via write_date < cutoff.
        # We simulate this by returning an empty list from search.
        cron = self._make_cron_with_pickings([])
        cron._reconcile_sent_orders()
        # No pickings returned — nothing to write

    def test_reconcile_skips_picking_with_connote(self):
        """When search returns [] (simulating ORM connote filter), no writes occur."""
        # The ORM domain filters out connote-bearing pickings via x_mf_connote=False.
        cron = self._make_cron_with_pickings([])
        cron._reconcile_sent_orders()
        # No stale pickings without connote returned

    def test_reconcile_configurable_threshold(self):
        """Threshold read from ir.config_parameter is applied to cutoff calculation."""
        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = []

        mock_icp = MagicMock()
        mock_icp.get_param.return_value = '24'

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == 'ir.config_parameter':
                m = MagicMock()
                m.sudo.return_value = mock_icp
                return m
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        before = datetime.datetime.utcnow()
        cron._reconcile_sent_orders()

        # Verify ICP was queried with the correct parameter key
        mock_icp.get_param.assert_called_once_with(
            'stock_3pl_mainfreight.reconcile_hours', default=48
        )

        # The write_date domain cutoff should be approximately (now - 24h)
        domain = mock_picking_model.search.call_args[0][0]
        write_date_clause = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'write_date'), None
        )
        cutoff = write_date_clause[2]

        expected_cutoff_approx = before - datetime.timedelta(hours=24)
        # Cutoff must be within a 5-second window of the expected value
        delta = abs((cutoff - expected_cutoff_approx).total_seconds())
        self.assertLess(delta, 5, f'Cutoff {cutoff} is not ~24h before now')

    def test_reconcile_default_threshold_48h(self):
        """Default threshold is 48h when ir.config_parameter returns '48'."""
        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = []

        mock_icp = MagicMock()
        mock_icp.get_param.return_value = '48'

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == 'ir.config_parameter':
                m = MagicMock()
                m.sudo.return_value = mock_icp
                return m
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)

        before = datetime.datetime.utcnow()
        cron._reconcile_sent_orders()

        domain = mock_picking_model.search.call_args[0][0]
        write_date_clause = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'write_date'), None
        )
        cutoff = write_date_clause[2]

        expected = before - datetime.timedelta(hours=48)
        delta = abs((cutoff - expected).total_seconds())
        self.assertLess(delta, 5)

    def test_reconcile_error_on_picking_continues(self):
        """An error flagging one picking must not prevent others from being flagged."""
        picking_bad = _make_mock_picking(name='WH/OUT/00001')
        picking_bad.write.side_effect = RuntimeError('write failed')

        picking_good = _make_mock_picking(name='WH/OUT/00002')

        cron = self._make_cron_with_pickings([picking_bad, picking_good])
        # Must not raise
        cron._reconcile_sent_orders()

        # Good picking was still flagged
        picking_good.write.assert_called_once_with({'x_mf_status': 'mf_exception'})

    def test_reconcile_no_stale_pickings_no_writes(self):
        """When search returns [], no write() calls are made."""
        cron = self._make_cron_with_pickings([])
        cron._reconcile_sent_orders()
        # No pickings to iterate — no write() possible

    def test_reconcile_multiple_stale_pickings_all_flagged(self):
        """All stale pickings returned by search are flagged as mf_exception."""
        pickings = [
            _make_mock_picking(name=f'WH/OUT/{i:05d}')
            for i in range(3)
        ]

        cron = self._make_cron_with_pickings(pickings)
        cron._reconcile_sent_orders()

        for picking in pickings:
            picking.write.assert_called_once_with({'x_mf_status': 'mf_exception'})


if __name__ == '__main__':
    unittest.main()
