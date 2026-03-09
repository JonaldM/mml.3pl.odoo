# addons/stock_3pl_mainfreight/tests/test_tracking_cron.py
"""Pure-Python mock-based tests for MFTrackingCron and MainfreightRestTransport.get_tracking_status().

No Odoo runtime required. Odoo stubs are installed by the repo-level
conftest.py before pytest collects this module.
"""
import sys
import types
import unittest
import importlib.util
import pathlib
import datetime
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Ensure requests is importable (stub it if not installed)
# ---------------------------------------------------------------------------
if 'requests' not in sys.modules:
    _requests_stub = types.ModuleType('requests')
    _requests_stub.get = MagicMock()

    class _RequestException(Exception):
        pass

    class _HTTPError(_RequestException):
        pass

    _requests_stub.HTTPError = _HTTPError
    _requests_stub.RequestException = _RequestException

    # requests.exceptions submodule — production code references it directly
    _requests_exc_stub = types.ModuleType('requests.exceptions')
    _requests_exc_stub.RequestException = _RequestException
    _requests_exc_stub.HTTPError = _HTTPError
    _requests_stub.exceptions = _requests_exc_stub

    sys.modules['requests'] = _requests_stub
    sys.modules['requests.exceptions'] = _requests_exc_stub
else:
    # Real requests is installed — ensure requests.exceptions is accessible
    import requests as _requests_real
    if not hasattr(_requests_real, 'exceptions'):
        import importlib as _il
        _requests_real.exceptions = _il.import_module('requests.exceptions')

# ---------------------------------------------------------------------------
# Stub the RestTransport parent so mainfreight_rest.py can be loaded
# ---------------------------------------------------------------------------
if 'odoo.addons.stock_3pl_core.transport.rest_api' not in sys.modules:
    _core_transport_pkg = sys.modules.get('odoo.addons.stock_3pl_core.transport') or types.ModuleType(
        'odoo.addons.stock_3pl_core.transport'
    )
    sys.modules.setdefault('odoo.addons.stock_3pl_core.transport', _core_transport_pkg)

    _rest_mod = types.ModuleType('odoo.addons.stock_3pl_core.transport.rest_api')

    class RestTransport:
        def __init__(self, connector):
            self.connector = connector

        def send(self, payload, content_type='xml', filename=None, endpoint=None):
            return {'success': True}

        def poll(self, path=None):
            return []

    _rest_mod.RestTransport = RestTransport
    sys.modules['odoo.addons.stock_3pl_core.transport.rest_api'] = _rest_mod

# ---------------------------------------------------------------------------
# Load modules under test directly from disk
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_TRANSPORT_DIR = _HERE.parent / 'transport'
_MODELS_DIR = _HERE.parent / 'models'


def _load_module(module_name, file_path):
    """Load a Python file as a sys.modules entry, idempotently."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_mf_rest_mod = _load_module(
    'stock_3pl_mainfreight.transport.mainfreight_rest',
    _TRANSPORT_DIR / 'mainfreight_rest.py',
)
_tracking_cron_mod = _load_module(
    'stock_3pl_mainfreight.models.tracking_cron',
    _MODELS_DIR / 'tracking_cron.py',
)

MainfreightRestTransport = _mf_rest_mod.MainfreightRestTransport
MF_TRACKING_ENDPOINTS = _mf_rest_mod.MF_TRACKING_ENDPOINTS
MF_TRACKING_STATUS_MAP = _mf_rest_mod.MF_TRACKING_STATUS_MAP
MFTrackingCron = _tracking_cron_mod.MFTrackingCron


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeConnector:
    """Minimal connector stub for transport tests."""
    def __init__(self, environment='test', mf_tracking_secret='test-secret'):
        self.environment = environment
        self.mf_tracking_secret = mf_tracking_secret

    def get_credential(self, field_name):
        """Return the credential field value directly (no encryption in tests)."""
        return getattr(self, field_name, None)


def _make_cron(env_lookup=None):
    """Construct an MFTrackingCron instance with a mocked self.env."""
    cron = object.__new__(MFTrackingCron)
    env = MagicMock()
    if env_lookup is not None:
        env.__getitem__ = MagicMock(side_effect=env_lookup)
    cron.env = env
    return cron


def _make_mock_picking(
    name='WH/OUT/00001',
    x_mf_status='mf_sent',
    x_mf_connote='CONNOTE123',
    warehouse_id=1,
):
    """Build a minimal mock stock.picking."""
    picking = MagicMock()
    picking.name = name
    picking.x_mf_status = x_mf_status
    picking.x_mf_connote = x_mf_connote
    picking.picking_type_id.warehouse_id.id = warehouse_id
    picking.picking_type_id.warehouse_id.name = 'Main Warehouse'
    return picking


# ---------------------------------------------------------------------------
# Tests: MF_TRACKING_ENDPOINTS constant
# ---------------------------------------------------------------------------

class TestMFTrackingEndpoints(unittest.TestCase):

    def test_tracking_endpoints_has_test_key(self):
        self.assertIn('test', MF_TRACKING_ENDPOINTS)

    def test_tracking_endpoints_has_production_key(self):
        self.assertIn('production', MF_TRACKING_ENDPOINTS)

    def test_test_url_contains_trackingapi_test(self):
        self.assertIn('trackingapi-test.mainfreight.com', MF_TRACKING_ENDPOINTS['test'])

    def test_production_url_contains_trackingapi(self):
        self.assertIn('trackingapi.mainfreight.com', MF_TRACKING_ENDPOINTS['production'])
        self.assertNotIn('trackingapi-test', MF_TRACKING_ENDPOINTS['production'])


# ---------------------------------------------------------------------------
# Tests: MF_TRACKING_STATUS_MAP constant
# ---------------------------------------------------------------------------

class TestMFTrackingStatusMap(unittest.TestCase):

    def test_received_maps_to_mf_received(self):
        self.assertEqual(MF_TRACKING_STATUS_MAP['RECEIVED'], 'mf_received')

    def test_dispatched_maps_to_mf_dispatched(self):
        self.assertEqual(MF_TRACKING_STATUS_MAP['DISPATCHED'], 'mf_dispatched')

    def test_in_transit_maps_to_mf_in_transit(self):
        self.assertEqual(MF_TRACKING_STATUS_MAP['IN_TRANSIT'], 'mf_in_transit')

    def test_out_for_delivery_maps_to_mf_out_for_delivery(self):
        self.assertEqual(MF_TRACKING_STATUS_MAP['OUT_FOR_DELIVERY'], 'mf_out_for_delivery')

    def test_delivered_maps_to_mf_delivered(self):
        self.assertEqual(MF_TRACKING_STATUS_MAP['DELIVERED'], 'mf_delivered')

    def test_exception_maps_to_mf_exception(self):
        self.assertEqual(MF_TRACKING_STATUS_MAP['EXCEPTION'], 'mf_exception')


# ---------------------------------------------------------------------------
# Tests: get_tracking_status — success path
# ---------------------------------------------------------------------------

class TestGetTrackingStatusSuccess(unittest.TestCase):
    """test_get_tracking_status_success: mock requests.get returning valid MF JSON."""

    def _make_response(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        return resp

    def test_returns_correct_status_mapping(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = self._make_response({
            'Status': 'IN_TRANSIT',
            'PODUrl': None,
            'SignedBy': None,
            'DeliveredAt': None,
        })
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CONNOTE123')
        self.assertEqual(result['status'], 'mf_in_transit')

    def test_returns_pod_url_when_present(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = self._make_response({
            'Status': 'DELIVERED',
            'PODUrl': 'https://example.com/pod.pdf',
            'SignedBy': 'J. Smith',
            'DeliveredAt': '2026-02-28T14:00:00',
        })
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CONNOTE999')
        self.assertEqual(result['pod_url'], 'https://example.com/pod.pdf')
        self.assertEqual(result['signed_by'], 'J. Smith')

    def test_parses_delivered_at_as_datetime(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = self._make_response({
            'Status': 'DELIVERED',
            'PODUrl': None,
            'SignedBy': None,
            'DeliveredAt': '2026-02-28T14:00:00',
        })
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CONNOTE999')
        self.assertIsInstance(result['delivered_at'], datetime.datetime)
        self.assertEqual(result['delivered_at'].year, 2026)
        self.assertEqual(result['delivered_at'].month, 2)
        self.assertEqual(result['delivered_at'].day, 28)

    def test_delivered_at_none_when_absent(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = self._make_response({
            'Status': 'IN_TRANSIT',
        })
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CONNOTE123')
        self.assertIsNone(result['delivered_at'])

    def test_pod_url_none_when_absent(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = self._make_response({'Status': 'DISPATCHED'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CONNOTE123')
        self.assertIsNone(result['pod_url'])
        self.assertIsNone(result['signed_by'])

    def test_uses_bearer_token_auth(self):
        transport = MainfreightRestTransport(_FakeConnector(mf_tracking_secret='my-secret'))
        mock_resp = self._make_response({'Status': 'RECEIVED'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp) as mock_get:
            transport.get_tracking_status('CN001')
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer my-secret')

    def test_calls_correct_url(self):
        transport = MainfreightRestTransport(_FakeConnector(environment='test'))
        mock_resp = self._make_response({'Status': 'DISPATCHED'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp) as mock_get:
            transport.get_tracking_status('CN001')
        url = mock_get.call_args[0][0]
        self.assertIn('trackingapi-test.mainfreight.com', url)
        self.assertIn('/Tracking/CN001', url)

    def test_uses_production_tracking_url_for_production(self):
        transport = MainfreightRestTransport(_FakeConnector(environment='production'))
        mock_resp = self._make_response({'Status': 'DISPATCHED'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp) as mock_get:
            transport.get_tracking_status('CN001')
        url = mock_get.call_args[0][0]
        self.assertIn('trackingapi.mainfreight.com', url)
        self.assertNotIn('trackingapi-test', url)


# ---------------------------------------------------------------------------
# Tests: get_tracking_status — unknown status
# ---------------------------------------------------------------------------

class TestGetTrackingStatusUnknownStatus(unittest.TestCase):
    """test_get_tracking_status_unknown_status: unknown MF status → returns {}."""

    def test_unknown_mf_status_returns_empty_dict(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': 'UNKNOWN_FUTURE_STATUS'}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CN001')
        self.assertEqual(result, {})

    def test_missing_status_key_returns_empty_dict(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'PODUrl': None}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CN001')
        self.assertEqual(result, {})

    def test_null_status_returns_empty_dict(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': None}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CN001')
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# Tests: get_tracking_status — network error
# ---------------------------------------------------------------------------

class TestGetTrackingStatusNetworkError(unittest.TestCase):
    """test_get_tracking_status_network_error: requests raises exception → returns {}."""

    def test_connection_error_returns_empty_dict(self):
        transport = MainfreightRestTransport(_FakeConnector())
        exc_cls = sys.modules['requests'].exceptions.RequestException
        with patch.object(sys.modules['requests'], 'get', side_effect=exc_cls('connection refused')):
            result = transport.get_tracking_status('CN001')
        self.assertEqual(result, {})

    def test_http_error_returns_empty_dict(self):
        transport = MainfreightRestTransport(_FakeConnector())
        exc_cls = sys.modules['requests'].exceptions.RequestException
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = exc_cls('404 Not Found')
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CN001')
        self.assertEqual(result, {})

    def test_json_decode_error_returns_empty_dict(self):
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError('invalid JSON')
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CN001')
        self.assertEqual(result, {})

    def test_invalid_delivered_at_yields_none_not_error(self):
        """A bad DeliveredAt string must not raise — return None for delivered_at."""
        transport = MainfreightRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'Status': 'DELIVERED',
            'DeliveredAt': 'not-a-date',
        }
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('CN001')
        self.assertEqual(result['status'], 'mf_delivered')
        self.assertIsNone(result['delivered_at'])


# ---------------------------------------------------------------------------
# Tests: MFTrackingCron model metadata
# ---------------------------------------------------------------------------

class TestMFTrackingCronModel(unittest.TestCase):

    def test_model_name(self):
        self.assertEqual(MFTrackingCron._name, 'mf.tracking.cron')

    def test_description(self):
        self.assertEqual(MFTrackingCron._description, 'MF Tracking Cron')


# ---------------------------------------------------------------------------
# Tests: _run_mf_tracking — updates picking
# ---------------------------------------------------------------------------

class TestRunMFTrackingUpdatesPicking(unittest.TestCase):
    """test_run_mf_tracking_updates_picking: assert picking.write() called with correct vals."""

    def test_write_called_with_status_and_metadata(self):
        """When tracking returns full data, picking.write() is called with all fields."""
        delivered = datetime.datetime(2026, 2, 28, 14, 0, 0)
        tracking_result = {
            'status': 'mf_delivered',
            'pod_url': 'https://example.com/pod.pdf',
            'signed_by': 'J. Smith',
            'delivered_at': delivered,
        }

        picking = _make_mock_picking(x_mf_status='mf_in_transit')
        mock_transport = MagicMock()
        mock_transport.get_tracking_status.return_value = tracking_result

        mock_connector = MagicMock()
        mock_connector.get_transport.return_value = mock_transport

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = mock_connector

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = [picking]

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._run_mf_tracking()

        picking.write.assert_called_once()
        write_vals = picking.write.call_args[0][0]
        self.assertEqual(write_vals['x_mf_status'], 'mf_delivered')
        self.assertEqual(write_vals['x_mf_pod_url'], 'https://example.com/pod.pdf')
        self.assertEqual(write_vals['x_mf_signed_by'], 'J. Smith')
        self.assertEqual(write_vals['x_mf_delivered_date'], delivered)

    def test_write_called_with_status_only_when_no_metadata(self):
        """When tracking returns only status (no pod/signed/delivered), only status is written."""
        tracking_result = {
            'status': 'mf_in_transit',
            'pod_url': None,
            'signed_by': None,
            'delivered_at': None,
        }

        picking = _make_mock_picking(x_mf_status='mf_sent')
        mock_transport = MagicMock()
        mock_transport.get_tracking_status.return_value = tracking_result

        mock_connector = MagicMock()
        mock_connector.get_transport.return_value = mock_transport

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = mock_connector

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = [picking]

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._run_mf_tracking()

        picking.write.assert_called_once()
        write_vals = picking.write.call_args[0][0]
        self.assertIn('x_mf_status', write_vals)
        self.assertEqual(write_vals['x_mf_status'], 'mf_in_transit')
        self.assertNotIn('x_mf_pod_url', write_vals)
        self.assertNotIn('x_mf_signed_by', write_vals)
        self.assertNotIn('x_mf_delivered_date', write_vals)

    def test_search_domain_includes_trackable_statuses_and_connote(self):
        """Phase 1 search is called with x_mf_connote != False and the correct status list.

        _run_mf_tracking() makes two search calls: Phase 0 (connote absent,
        outbound_ref present) then Phase 1 (connote present).  This test
        verifies the Phase 1 domain only.
        """
        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = []

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._run_mf_tracking()

        # Expect at least two search calls (Phase 0 + Phase 1).
        self.assertGreaterEqual(mock_picking_model.search.call_count, 2)

        # Find the Phase 1 call: domain contains ('x_mf_connote', '!=', False).
        phase1_domain = None
        for call in mock_picking_model.search.call_args_list:
            domain = call[0][0]
            connote_tuples = [t for t in domain if isinstance(t, tuple) and t[0] == 'x_mf_connote']
            if connote_tuples and connote_tuples[0][1] == '!=':
                phase1_domain = domain
                break

        self.assertIsNotNone(phase1_domain, "Phase 1 search call not found")

        status_tuple = next(
            (t for t in phase1_domain if isinstance(t, tuple) and t[0] == 'x_mf_status'), None
        )
        self.assertIsNotNone(status_tuple)
        for status in ('mf_sent', 'mf_received', 'mf_dispatched', 'mf_in_transit', 'mf_out_for_delivery'):
            self.assertIn(status, status_tuple[2])

        connote_tuple = next(
            (t for t in phase1_domain if isinstance(t, tuple) and t[0] == 'x_mf_connote'), None
        )
        self.assertIsNotNone(connote_tuple)
        self.assertEqual(connote_tuple[1], '!=')
        self.assertFalse(connote_tuple[2])

    def test_empty_tracking_response_does_not_call_write(self):
        """When get_tracking_status returns {}, picking.write is not called."""
        picking = _make_mock_picking(x_mf_status='mf_sent')
        mock_transport = MagicMock()
        mock_transport.get_tracking_status.return_value = {}

        mock_connector = MagicMock()
        mock_connector.get_transport.return_value = mock_transport

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = mock_connector

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = [picking]

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._run_mf_tracking()

        picking.write.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _run_mf_tracking — skips terminal statuses
# ---------------------------------------------------------------------------

class TestRunMFTrackingSkipsTerminalStatuses(unittest.TestCase):
    """test_run_mf_tracking_skips_terminal_statuses: terminal statuses not in search results."""

    def test_mf_delivered_picking_not_in_search_results(self):
        """mf_delivered pickings are excluded by the search domain — write not called."""
        # The search domain filters on ('x_mf_status', 'in', [trackable statuses]).
        # mf_delivered is NOT in that list, so search would never return it.
        # We verify by checking the search domain excludes terminal statuses.
        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = []

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._run_mf_tracking()

        domain = mock_picking_model.search.call_args[0][0]
        status_tuple = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'x_mf_status'), None
        )
        # Terminal statuses must NOT appear in the search domain's status list
        self.assertNotIn('mf_delivered', status_tuple[2])
        self.assertNotIn('mf_exception', status_tuple[2])

    def test_status_not_overwritten_if_already_terminal(self):
        """Even if a picking slips through with terminal status, x_mf_status is not overwritten."""
        # Simulate a picking that somehow arrives at _poll_and_update already in mf_delivered.
        # This guards against race conditions.
        picking = _make_mock_picking(x_mf_status='mf_delivered')
        mock_transport = MagicMock()
        mock_transport.get_tracking_status.return_value = {
            'status': 'mf_delivered',
            'pod_url': 'https://example.com/pod.pdf',
            'signed_by': 'J. Smith',
            'delivered_at': datetime.datetime(2026, 2, 28, 14, 0, 0),
        }

        mock_connector = MagicMock()
        mock_connector.get_transport.return_value = mock_transport

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = mock_connector

        cron = object.__new__(MFTrackingCron)
        cron.env = MagicMock()
        cron.env.__getitem__ = MagicMock(side_effect=lambda k: mock_connector_model if k == '3pl.connector' else MagicMock())

        cron._poll_and_update(picking)

        # x_mf_status must NOT be in write_vals since the picking is already terminal
        write_calls = picking.write.call_args_list
        if write_calls:
            for c in write_calls:
                vals = c[0][0]
                self.assertNotIn('x_mf_status', vals)

    def test_mf_exception_picking_not_in_search_results(self):
        """mf_exception pickings are excluded by the search domain."""
        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = []

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._run_mf_tracking()

        domain = mock_picking_model.search.call_args[0][0]
        status_tuple = next(
            (t for t in domain if isinstance(t, tuple) and t[0] == 'x_mf_status'), None
        )
        self.assertNotIn('mf_exception', status_tuple[2])


# ---------------------------------------------------------------------------
# Tests: _run_mf_tracking — no connector found
# ---------------------------------------------------------------------------

class TestRunMFTrackingNoConnector(unittest.TestCase):
    """test_run_mf_tracking_no_connector: no connector → skip picking, log warning."""

    def test_no_connector_skips_picking_does_not_raise(self):
        """When no connector is found for a picking's warehouse, write is not called."""
        picking = _make_mock_picking()

        # Connector search returns a falsy result (empty recordset / None-ish mock)
        mock_connector_model = MagicMock()
        # Make search return something falsy — use MagicMock with __bool__ = False
        empty_rs = MagicMock()
        empty_rs.__bool__ = lambda self: False
        mock_connector_model.search.return_value = empty_rs

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = [picking]

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        # Must not raise
        cron._run_mf_tracking()

        picking.write.assert_not_called()

    def test_no_connector_continues_to_next_picking(self):
        """A missing connector on one picking must not prevent subsequent pickings being updated."""
        delivered = datetime.datetime(2026, 2, 28, 14, 0, 0)

        picking_no_conn = _make_mock_picking(name='WH/OUT/00001', warehouse_id=1)
        picking_with_conn = _make_mock_picking(name='WH/OUT/00002', warehouse_id=2)

        mock_transport = MagicMock()
        mock_transport.get_tracking_status.return_value = {
            'status': 'mf_dispatched',
            'pod_url': None,
            'signed_by': None,
            'delivered_at': None,
        }

        good_connector = MagicMock()
        good_connector.get_transport.return_value = mock_transport

        # Connector search: return falsy for warehouse 1, good for warehouse 2
        empty_rs = MagicMock()
        empty_rs.__bool__ = lambda self: False

        def connector_search(domain, limit=1):
            wh_id = next((t[2] for t in domain if t[0] == 'warehouse_id'), None)
            if wh_id == 1:
                return empty_rs
            return good_connector

        mock_connector_model = MagicMock()
        mock_connector_model.search.side_effect = connector_search

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = [picking_no_conn, picking_with_conn]

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            if key == '3pl.connector':
                return mock_connector_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._run_mf_tracking()

        picking_no_conn.write.assert_not_called()
        picking_with_conn.write.assert_called_once()

    def test_per_picking_exception_does_not_abort_batch(self):
        """An unexpected exception on one picking must not stop the rest of the batch."""
        picking_bad = _make_mock_picking(name='WH/OUT/00001')
        picking_good = _make_mock_picking(name='WH/OUT/00002')

        call_count = {'n': 0}

        def _poll_and_update(picking):
            call_count['n'] += 1
            if picking.name == 'WH/OUT/00001':
                raise RuntimeError('simulated transport failure')

        mock_picking_model = MagicMock()
        mock_picking_model.search.return_value = [picking_bad, picking_good]

        def env_lookup(key):
            if key == 'stock.picking':
                return mock_picking_model
            return MagicMock()

        cron = _make_cron(env_lookup=env_lookup)
        cron._poll_and_update = _poll_and_update

        # Must not raise
        cron._run_mf_tracking()

        self.assertEqual(call_count['n'], 2)


# ---------------------------------------------------------------------------
# Tests: _send_cron_alert — rate-limiting and XSS escaping
# ---------------------------------------------------------------------------

class TestSendCronAlertRateLimiting(unittest.TestCase):
    """_send_cron_alert must suppress duplicate alerts within the cooldown window
    and escape HTML in the body."""

    def _make_cron_with_icp(self, icp_params):
        """Build a cron instance where ir.config_parameter returns values from icp_params dict."""
        cron = object.__new__(MFTrackingCron)
        env = MagicMock()

        icp = MagicMock()
        icp.get_param.side_effect = lambda key, default=False: icp_params.get(key, default)
        icp.set_param = MagicMock()

        mail_model = MagicMock()
        mail_instance = MagicMock()
        mail_model.create.return_value = mail_instance

        def env_getitem(key):
            if key == 'ir.config_parameter':
                mock_model = MagicMock()
                mock_model.sudo.return_value = icp
                return mock_model
            if key == 'mail.mail':
                mock_model = MagicMock()
                mock_model.sudo.return_value = mail_model
                return mock_model
            return MagicMock()

        env.__getitem__ = MagicMock(side_effect=env_getitem)
        cron.env = env
        return cron, icp, mail_model, mail_instance

    def test_alert_suppressed_within_cooldown(self):
        """Alert is not sent if the last alert was less than _ALERT_COOLDOWN_SECONDS ago."""
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        cron, icp, mail_model, _ = self._make_cron_with_icp({
            'mml.cron_alert_email': 'ops@example.com',
            'mml_3pl.last_alert.stock_3pl_mainfreight': recent,
        })

        cron._send_cron_alert('stock_3pl_mainfreight', 'Test subject', 'Test body')

        mail_model.create.assert_not_called()

    def test_alert_sent_after_cooldown_expires(self):
        """Alert is sent when the last alert was more than _ALERT_COOLDOWN_SECONDS ago."""
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
        cron, icp, mail_model, mail_instance = self._make_cron_with_icp({
            'mml.cron_alert_email': 'ops@example.com',
            'mml_3pl.last_alert.stock_3pl_mainfreight': old,
        })

        cron._send_cron_alert('stock_3pl_mainfreight', 'Test subject', 'Test body')

        mail_model.create.assert_called_once()
        mail_instance.send.assert_called_once()

    def test_alert_sent_when_no_prior_timestamp(self):
        """Alert is sent on the first call (no stored timestamp)."""
        cron, icp, mail_model, mail_instance = self._make_cron_with_icp({
            'mml.cron_alert_email': 'ops@example.com',
        })

        cron._send_cron_alert('stock_3pl_mainfreight', 'First alert', 'body')

        mail_model.create.assert_called_once()

    def test_timestamp_written_after_successful_send(self):
        """ir.config_parameter.set_param is called with the cooldown key after a successful send."""
        cron, icp, mail_model, _ = self._make_cron_with_icp({
            'mml.cron_alert_email': 'ops@example.com',
        })

        cron._send_cron_alert('stock_3pl_mainfreight', 'subj', 'body')

        set_calls = [c for c in icp.set_param.call_args_list
                     if c[0][0] == 'mml_3pl.last_alert.stock_3pl_mainfreight']
        self.assertEqual(len(set_calls), 1)

    def test_timestamp_not_written_when_send_raises(self):
        """set_param is NOT called if mail.mail.send() raises."""
        cron, icp, mail_model, mail_instance = self._make_cron_with_icp({
            'mml.cron_alert_email': 'ops@example.com',
        })
        mail_instance.send.side_effect = Exception('SMTP failure')

        cron._send_cron_alert('stock_3pl_mainfreight', 'subj', 'body')

        set_calls = [c for c in icp.set_param.call_args_list
                     if c[0][0] == 'mml_3pl.last_alert.stock_3pl_mainfreight']
        self.assertEqual(len(set_calls), 0)

    def test_body_is_html_escaped(self):
        """HTML special characters in body are escaped before insertion into <pre>."""
        cron, icp, mail_model, _ = self._make_cron_with_icp({
            'mml.cron_alert_email': 'ops@example.com',
        })

        cron._send_cron_alert('stock_3pl_mainfreight', 'subj', '<script>alert(1)</script>')

        create_kwargs = mail_model.create.call_args[0][0]
        self.assertIn('&lt;script&gt;', create_kwargs['body_html'])
        self.assertNotIn('<script>', create_kwargs['body_html'])

    def test_malformed_stored_timestamp_sends_alert(self):
        """A malformed stored timestamp does not suppress the alert (fail-open)."""
        cron, icp, mail_model, _ = self._make_cron_with_icp({
            'mml.cron_alert_email': 'ops@example.com',
            'mml_3pl.last_alert.stock_3pl_mainfreight': 'not-a-datetime',
        })

        cron._send_cron_alert('stock_3pl_mainfreight', 'subj', 'body')

        mail_model.create.assert_called_once()

    def test_no_alert_when_email_not_configured(self):
        """When mml.cron_alert_email is not set, _send_cron_alert returns without sending."""
        cron, icp, mail_model, _ = self._make_cron_with_icp({})

        cron._send_cron_alert('stock_3pl_mainfreight', 'subj', 'body')

        mail_model.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
