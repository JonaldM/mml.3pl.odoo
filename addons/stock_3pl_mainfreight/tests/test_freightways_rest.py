# addons/stock_3pl_mainfreight/tests/test_freightways_rest.py
"""
Pure-Python tests for freightways_rest.py.
No Odoo runtime required — tests verify URL routing, credential handling,
status mapping, and error paths.
"""
import sys
import types
import unittest
import importlib.util
import pathlib
import datetime
from unittest.mock import patch, MagicMock

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

    _requests_exc_stub = types.ModuleType('requests.exceptions')
    _requests_exc_stub.RequestException = _RequestException
    _requests_exc_stub.HTTPError = _HTTPError
    _requests_stub.exceptions = _requests_exc_stub

    sys.modules['requests'] = _requests_stub
    sys.modules['requests.exceptions'] = _requests_exc_stub
else:
    import requests as _requests_real
    if not hasattr(_requests_real, 'exceptions'):
        import importlib as _il
        _requests_real.exceptions = _il.import_module('requests.exceptions')

# ---------------------------------------------------------------------------
# Stub RestTransport parent so freightways_rest.py can be imported
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
# Load freightways_rest.py directly from disk
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_TRANSPORT_DIR = _HERE.parent / 'transport'


def _load_module(module_name, file_path):
    """Load a Python file as a sys.modules entry, idempotently."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_fw_rest_mod = _load_module(
    'stock_3pl_mainfreight.transport.freightways_rest',
    _TRANSPORT_DIR / 'freightways_rest.py',
)

FreightwaysRestTransport = _fw_rest_mod.FreightwaysRestTransport
FREIGHTWAYS_ENVIRONMENTS = _fw_rest_mod.FREIGHTWAYS_ENVIRONMENTS
FW_TRACKING_STATUS_MAP = _fw_rest_mod.FW_TRACKING_STATUS_MAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeConnector:
    """Minimal connector stub for transport tests."""

    def __init__(self, environment='test', fw_api_key='fw-test-key'):
        self.environment = environment
        self.fw_api_key = fw_api_key

    def get_credential(self, field_name):
        return getattr(self, field_name, None)


# ---------------------------------------------------------------------------
# Tests: FREIGHTWAYS_ENVIRONMENTS constant
# ---------------------------------------------------------------------------

class TestFreightwaysEnvironmentsConstant(unittest.TestCase):

    def test_environments_has_test_key(self):
        self.assertIn('test', FREIGHTWAYS_ENVIRONMENTS)

    def test_environments_has_production_key(self):
        self.assertIn('production', FREIGHTWAYS_ENVIRONMENTS)

    def test_test_url_points_to_sandbox_host(self):
        self.assertIn('api-sandbox.freightways.co.nz', FREIGHTWAYS_ENVIRONMENTS['test']['rest_api'])

    def test_production_url_points_to_prod_host(self):
        self.assertIn('api.freightways.co.nz', FREIGHTWAYS_ENVIRONMENTS['production']['rest_api'])
        self.assertNotIn('sandbox', FREIGHTWAYS_ENVIRONMENTS['production']['rest_api'])


# ---------------------------------------------------------------------------
# Tests: FW_TRACKING_STATUS_MAP constant
# ---------------------------------------------------------------------------

class TestFWTrackingStatusMap(unittest.TestCase):

    def test_booked_maps_to_mf_received(self):
        self.assertEqual(FW_TRACKING_STATUS_MAP['Booked'], 'mf_received')

    def test_in_transit_maps_to_mf_in_transit(self):
        self.assertEqual(FW_TRACKING_STATUS_MAP['InTransit'], 'mf_in_transit')

    def test_out_for_delivery_maps_to_mf_out_for_delivery(self):
        self.assertEqual(FW_TRACKING_STATUS_MAP['OutForDelivery'], 'mf_out_for_delivery')

    def test_delivered_maps_to_mf_delivered(self):
        self.assertEqual(FW_TRACKING_STATUS_MAP['Delivered'], 'mf_delivered')

    def test_exception_maps_to_mf_exception(self):
        self.assertEqual(FW_TRACKING_STATUS_MAP['Exception'], 'mf_exception')


# ---------------------------------------------------------------------------
# Tests: _get_base_url
# ---------------------------------------------------------------------------

class TestFreightwaysBaseUrl(unittest.TestCase):

    def test_freightways_base_url_test_environment(self):
        """_get_base_url returns the sandbox URL when environment == 'test'."""
        transport = FreightwaysRestTransport(_FakeConnector(environment='test'))
        self.assertEqual(transport._get_base_url(), FREIGHTWAYS_ENVIRONMENTS['test']['rest_api'])

    def test_freightways_base_url_production_environment(self):
        """_get_base_url returns the production URL when environment == 'production'."""
        transport = FreightwaysRestTransport(_FakeConnector(environment='production'))
        self.assertEqual(transport._get_base_url(), FREIGHTWAYS_ENVIRONMENTS['production']['rest_api'])

    def test_freightways_base_url_defaults_to_test_for_unknown_environment(self):
        """_get_base_url falls back to sandbox URL for an unknown environment string."""
        transport = FreightwaysRestTransport(_FakeConnector(environment='staging'))
        self.assertEqual(transport._get_base_url(), FREIGHTWAYS_ENVIRONMENTS['test']['rest_api'])

    def test_freightways_base_url_defaults_to_test_for_empty_environment(self):
        """_get_base_url falls back to sandbox URL for empty environment."""
        transport = FreightwaysRestTransport(_FakeConnector(environment=''))
        self.assertEqual(transport._get_base_url(), FREIGHTWAYS_ENVIRONMENTS['test']['rest_api'])


# ---------------------------------------------------------------------------
# Tests: _get_auth_secret
# ---------------------------------------------------------------------------

class TestGetAuthSecret(unittest.TestCase):

    def test_get_auth_secret_uses_fw_api_key(self):
        """_get_auth_secret delegates to connector.get_credential('fw_api_key')."""
        connector = _FakeConnector(fw_api_key='my-fw-key')
        transport = FreightwaysRestTransport(connector)
        self.assertEqual(transport._get_auth_secret(), 'my-fw-key')

    def test_get_auth_secret_returns_empty_string_when_key_is_none(self):
        """_get_auth_secret returns '' when fw_api_key is None (not None itself)."""
        connector = _FakeConnector(fw_api_key=None)
        transport = FreightwaysRestTransport(connector)
        self.assertEqual(transport._get_auth_secret(), '')


# ---------------------------------------------------------------------------
# Tests: get_tracking_status — success paths
# ---------------------------------------------------------------------------

class TestGetTrackingStatusSuccess(unittest.TestCase):

    def _make_response(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        return resp

    def test_get_tracking_status_success_in_transit(self):
        """'InTransit' status maps to 'mf_in_transit'."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = self._make_response({
            'Status': 'InTransit',
            'PODUrl': None,
            'SignedBy': None,
            'DeliveredDateTime': None,
        })
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW001')
        self.assertEqual(result['status'], 'mf_in_transit')

    def test_get_tracking_status_delivered(self):
        """'Delivered' status maps to 'mf_delivered' and DeliveredDateTime is parsed."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = self._make_response({
            'Status': 'Delivered',
            'PODUrl': 'https://fw.example.com/pod.pdf',
            'SignedBy': 'A. User',
            'DeliveredDateTime': '2026-02-28T14:30:00',
        })
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW002')
        self.assertEqual(result['status'], 'mf_delivered')
        self.assertEqual(result['pod_url'], 'https://fw.example.com/pod.pdf')
        self.assertEqual(result['signed_by'], 'A. User')
        self.assertIsInstance(result['delivered_at'], datetime.datetime)
        self.assertEqual(result['delivered_at'].year, 2026)
        self.assertEqual(result['delivered_at'].month, 2)
        self.assertEqual(result['delivered_at'].day, 28)

    def test_get_tracking_status_booked_maps_to_mf_received(self):
        """'Booked' status maps to 'mf_received'."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = self._make_response({'Status': 'Booked'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW003')
        self.assertEqual(result['status'], 'mf_received')

    def test_get_tracking_status_out_for_delivery(self):
        """'OutForDelivery' status maps to 'mf_out_for_delivery'."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = self._make_response({'Status': 'OutForDelivery'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW004')
        self.assertEqual(result['status'], 'mf_out_for_delivery')

    def test_get_tracking_status_exception_status(self):
        """'Exception' status maps to 'mf_exception'."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = self._make_response({'Status': 'Exception'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW005')
        self.assertEqual(result['status'], 'mf_exception')

    def test_pod_url_none_when_absent(self):
        """pod_url is None when PODUrl is absent or falsy in the response."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = self._make_response({'Status': 'InTransit'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW006')
        self.assertIsNone(result['pod_url'])
        self.assertIsNone(result['signed_by'])

    def test_delivered_at_none_when_absent(self):
        """delivered_at is None when DeliveredDateTime is absent."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = self._make_response({'Status': 'InTransit'})
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW007')
        self.assertIsNone(result['delivered_at'])


# ---------------------------------------------------------------------------
# Tests: X-API-Key header (not Authorization: Bearer)
# ---------------------------------------------------------------------------

class TestGetAuthSecretUsesXApiKeyHeader(unittest.TestCase):
    """Verify that get_tracking_status uses X-API-Key header, not Authorization: Bearer."""

    def test_get_auth_secret_uses_x_api_key_header(self):
        """get_tracking_status sends X-API-Key header with the fw_api_key value."""
        transport = FreightwaysRestTransport(_FakeConnector(fw_api_key='my-fw-api-key'))
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': 'InTransit'}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp) as mock_get:
            transport.get_tracking_status('FW010')
        _, kwargs = mock_get.call_args
        self.assertIn('X-API-Key', kwargs['headers'])
        self.assertEqual(kwargs['headers']['X-API-Key'], 'my-fw-api-key')

    def test_does_not_use_authorization_bearer_header(self):
        """get_tracking_status must NOT use the Authorization: Bearer pattern."""
        transport = FreightwaysRestTransport(_FakeConnector(fw_api_key='my-fw-api-key'))
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': 'InTransit'}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp) as mock_get:
            transport.get_tracking_status('FW010')
        _, kwargs = mock_get.call_args
        self.assertNotIn('Authorization', kwargs['headers'])

    def test_calls_correct_url(self):
        """get_tracking_status calls /Tracking/{connote} on the correct base URL."""
        transport = FreightwaysRestTransport(_FakeConnector(environment='test'))
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': 'Booked'}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp) as mock_get:
            transport.get_tracking_status('FW-CONNOTE-01')
        url = mock_get.call_args[0][0]
        self.assertIn('api-sandbox.freightways.co.nz', url)
        self.assertIn('/Tracking/FW-CONNOTE-01', url)

    def test_uses_production_url_for_production_environment(self):
        """get_tracking_status uses the production URL when environment == 'production'."""
        transport = FreightwaysRestTransport(_FakeConnector(environment='production'))
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': 'Booked'}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp) as mock_get:
            transport.get_tracking_status('FW-CONNOTE-02')
        url = mock_get.call_args[0][0]
        self.assertIn('api.freightways.co.nz', url)
        self.assertNotIn('sandbox', url)


# ---------------------------------------------------------------------------
# Tests: get_tracking_status — unknown/missing status
# ---------------------------------------------------------------------------

class TestGetTrackingStatusUnknownStatus(unittest.TestCase):

    def test_get_tracking_status_unknown_status(self):
        """An unrecognised FW status string → returns {}."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': 'SomeNewFutureStatus'}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW020')
        self.assertEqual(result, {})

    def test_missing_status_key_returns_empty_dict(self):
        """Missing Status key in response → returns {}."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'PODUrl': None}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW021')
        self.assertEqual(result, {})

    def test_null_status_returns_empty_dict(self):
        """Null Status value → returns {}."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'Status': None}
        mock_resp.raise_for_status.return_value = None
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW022')
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# Tests: get_tracking_status — network error paths
# ---------------------------------------------------------------------------

class TestGetTrackingStatusNetworkError(unittest.TestCase):

    def test_get_tracking_status_network_error(self):
        """requests.get raising RequestException → returns {}."""
        transport = FreightwaysRestTransport(_FakeConnector())
        exc_cls = sys.modules['requests'].exceptions.RequestException
        with patch.object(sys.modules['requests'], 'get', side_effect=exc_cls('connection refused')):
            result = transport.get_tracking_status('FW030')
        self.assertEqual(result, {})

    def test_http_error_from_raise_for_status_returns_empty_dict(self):
        """raise_for_status() raising RequestException → returns {}."""
        transport = FreightwaysRestTransport(_FakeConnector())
        exc_cls = sys.modules['requests'].exceptions.RequestException
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = exc_cls('404 Not Found')
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW031')
        self.assertEqual(result, {})

    def test_json_decode_error_returns_empty_dict(self):
        """JSON parse failure → returns {}."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError('invalid JSON')
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW032')
        self.assertEqual(result, {})

    def test_invalid_delivered_datetime_yields_none_not_error(self):
        """A malformed DeliveredDateTime string must not raise — delivered_at is None."""
        transport = FreightwaysRestTransport(_FakeConnector())
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'Status': 'Delivered',
            'DeliveredDateTime': 'not-a-date',
        }
        with patch.object(sys.modules['requests'], 'get', return_value=mock_resp):
            result = transport.get_tracking_status('FW033')
        self.assertEqual(result['status'], 'mf_delivered')
        self.assertIsNone(result['delivered_at'])


if __name__ == '__main__':
    unittest.main()
