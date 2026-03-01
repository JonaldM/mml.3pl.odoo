# addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py
"""
Pure-Python structural tests for mainfreight_rest.py.
No Odoo runtime required — tests verify URL routing and endpoint constants.
"""
import sys
import types
import unittest
import importlib.util
import pathlib
from unittest.mock import patch, MagicMock


def _stub_odoo_for_transport():
    """Install minimal odoo stubs so the transport module can be imported."""
    if 'odoo' not in sys.modules:
        odoo = types.ModuleType('odoo')
        sys.modules['odoo'] = odoo

    # Stub odoo.addons namespace
    odoo_addons = sys.modules.get('odoo.addons') or types.ModuleType('odoo.addons')
    sys.modules['odoo.addons'] = odoo_addons

    # Stub stock_3pl_core.transport.rest_api with a minimal RestTransport
    core_pkg = types.ModuleType('odoo.addons.stock_3pl_core')
    core_transport_pkg = types.ModuleType('odoo.addons.stock_3pl_core.transport')
    core_rest_mod = types.ModuleType('odoo.addons.stock_3pl_core.transport.rest_api')

    class RestTransport:
        def __init__(self, connector):
            self.connector = connector

        def send(self, payload, content_type='xml', filename=None, endpoint=None):
            return {'success': True}

        def send_put(self, payload, content_type='xml', endpoint=None):
            return {'success': True}

        def send_delete(self, endpoint):
            return {'success': True}

        def poll(self, path=None):
            return []

    core_rest_mod.RestTransport = RestTransport

    sys.modules['odoo.addons.stock_3pl_core'] = core_pkg
    sys.modules['odoo.addons.stock_3pl_core.transport'] = core_transport_pkg
    sys.modules['odoo.addons.stock_3pl_core.transport.rest_api'] = core_rest_mod

    # requests is imported by mainfreight_rest.py (used in get_tracking_status);
    # tests mock at method level instead of patching the module.


_stub_odoo_for_transport()


# Load mainfreight_rest.py directly via importlib
_TRANSPORT_DIR = pathlib.Path(__file__).parent.parent / 'transport'


def _load_transport(name):
    spec = importlib.util.spec_from_file_location(
        name, _TRANSPORT_DIR / f'{name}.py'
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mf_rest_mod = _load_transport('mainfreight_rest')

MainfreightRestTransport = _mf_rest_mod.MainfreightRestTransport
MF_ENDPOINTS = _mf_rest_mod.MF_ENDPOINTS


class _FakeConnector:
    """Minimal connector stub for testing."""
    def __init__(self, environment, api_secret='secret', mf_region='ANZ'):
        self.environment = environment
        self.api_secret = api_secret
        self.mf_region = mf_region

    def get_credential(self, field_name):
        return self.api_secret


class TestMFEndpointsConstant(unittest.TestCase):

    def test_endpoints_has_test_key(self):
        self.assertIn('test', MF_ENDPOINTS)

    def test_endpoints_has_production_key(self):
        self.assertIn('production', MF_ENDPOINTS)

    def test_test_url_points_to_test_host(self):
        """Test URL must use the public API host, not the old warehouse-specific subdomain."""
        self.assertIn('api-test.mainfreight.com', MF_ENDPOINTS['test'])

    def test_production_url_points_to_prod_host(self):
        self.assertIn('api.mainfreight.com', MF_ENDPOINTS['production'])

    def test_urls_include_warehousing_path(self):
        self.assertIn('/warehousing/1.1/Customers', MF_ENDPOINTS['test'])
        self.assertIn('/warehousing/1.1/Customers', MF_ENDPOINTS['production'])


class TestGetBaseUrl(unittest.TestCase):

    def test_returns_test_url_for_test_environment(self):
        """Test 1: _get_base_url returns the test URL when environment == 'test'."""
        transport = MainfreightRestTransport(_FakeConnector('test'))
        self.assertEqual(transport._get_base_url(), MF_ENDPOINTS['test'])

    def test_returns_production_url_for_production_environment(self):
        """Test 2: _get_base_url returns the production URL when environment == 'production'."""
        transport = MainfreightRestTransport(_FakeConnector('production'))
        self.assertEqual(transport._get_base_url(), MF_ENDPOINTS['production'])

    def test_defaults_to_test_url_for_unknown_environment(self):
        """Test 3: _get_base_url defaults to test URL for an unknown environment string."""
        transport = MainfreightRestTransport(_FakeConnector('staging'))
        self.assertEqual(transport._get_base_url(), MF_ENDPOINTS['test'])

    def test_defaults_to_test_url_for_empty_environment(self):
        """Test 3 (extended): _get_base_url defaults to test URL for empty environment."""
        transport = MainfreightRestTransport(_FakeConnector(''))
        self.assertEqual(transport._get_base_url(), MF_ENDPOINTS['test'])


class TestSendOrderEndpoint(unittest.TestCase):

    def test_send_order_uses_order_endpoint(self):
        """send_order delegates to self.send with the /Order endpoint and xml content_type."""
        transport = MainfreightRestTransport(_FakeConnector('test'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_order('<Order/>')
        mock_send.assert_called_once_with(
            '<Order/>',
            content_type='xml',
            endpoint=f'{MF_ENDPOINTS["test"]}/Order?region=ANZ',
        )

    def test_send_order_uses_production_endpoint_for_production(self):
        """send_order uses the production base URL when environment == 'production'."""
        transport = MainfreightRestTransport(_FakeConnector('production'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_order('<Order/>')
        mock_send.assert_called_once_with(
            '<Order/>',
            content_type='xml',
            endpoint=f'{MF_ENDPOINTS["production"]}/Order?region=ANZ',
        )


class TestSendInwardEndpoint(unittest.TestCase):

    def test_send_inward_uses_inward_endpoint(self):
        """send_inward delegates to self.send with the /Inward endpoint and xml content_type."""
        transport = MainfreightRestTransport(_FakeConnector('test'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_inward('<Inward/>')
        mock_send.assert_called_once_with(
            '<Inward/>',
            content_type='xml',
            endpoint=f'{MF_ENDPOINTS["test"]}/Inward?region=ANZ',
        )

    def test_send_inward_uses_production_endpoint_for_production(self):
        """send_inward uses the production base URL when environment == 'production'."""
        transport = MainfreightRestTransport(_FakeConnector('production'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_inward('<Inward/>')
        mock_send.assert_called_once_with(
            '<Inward/>',
            content_type='xml',
            endpoint=f'{MF_ENDPOINTS["production"]}/Inward?region=ANZ',
        )


class TestGetStockOnHand(unittest.TestCase):

    def test_get_stock_on_hand_calls_poll_with_soh_path(self):
        """get_stock_on_hand delegates to self.poll with the /StockOnHand path."""
        transport = MainfreightRestTransport(_FakeConnector('test'))
        with patch.object(transport, 'poll', return_value=['csv-data']) as mock_poll:
            result = transport.get_stock_on_hand()
        mock_poll.assert_called_once_with(
            path=f'{MF_ENDPOINTS["test"]}/StockOnHand?region=ANZ',
        )
        self.assertEqual(result, ['csv-data'])

    def test_get_stock_on_hand_returns_empty_list_on_poll_failure(self):
        """get_stock_on_hand returns [] when poll returns empty (transport-level failure)."""
        transport = MainfreightRestTransport(_FakeConnector('test'))
        with patch.object(transport, 'poll', return_value=[]):
            result = transport.get_stock_on_hand()
        self.assertEqual(result, [])

    def test_get_stock_on_hand_uses_production_soh_path(self):
        """get_stock_on_hand uses the production base URL when environment == 'production'."""
        transport = MainfreightRestTransport(_FakeConnector('production'))
        with patch.object(transport, 'poll', return_value=[]) as mock_poll:
            transport.get_stock_on_hand()
        mock_poll.assert_called_once_with(
            path=f'{MF_ENDPOINTS["production"]}/StockOnHand?region=ANZ',
        )


class TestRegionParam(unittest.TestCase):

    def test_region_helper_returns_connector_mf_region(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='EU'))
        self.assertEqual(transport._region(), 'EU')

    def test_region_helper_defaults_to_anz_when_empty(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region=''))
        self.assertEqual(transport._region(), 'ANZ')

    def test_region_helper_defaults_to_anz_when_none(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region=None))
        self.assertEqual(transport._region(), 'ANZ')

    def test_send_order_url_includes_region_param(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_order('<Order/>')
        endpoint = mock_send.call_args[1]['endpoint']
        self.assertIn('?region=ANZ', endpoint)

    def test_send_inward_url_includes_region_param(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_inward('<Inward/>')
        endpoint = mock_send.call_args[1]['endpoint']
        self.assertIn('?region=ANZ', endpoint)

    def test_get_stock_on_hand_url_includes_region_param(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'poll', return_value=[]) as mock_poll:
            transport.get_stock_on_hand()
        path_arg = mock_poll.call_args[1]['path']
        self.assertIn('?region=ANZ', path_arg)

    def test_eu_region_used_in_url(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='EU'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_order('<Order/>')
        endpoint = mock_send.call_args[1]['endpoint']
        self.assertIn('?region=EU', endpoint)


class TestMFCrudMethods(unittest.TestCase):

    def test_update_order_uses_put_with_order_endpoint(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_put', return_value={'success': True}) as mock_put:
            transport.update_order('<Order action="UPDATE"/>')
        mock_put.assert_called_once()
        endpoint = mock_put.call_args[1].get('endpoint') or mock_put.call_args[0][2]
        self.assertIn('/Order', endpoint)
        self.assertIn('?region=ANZ', endpoint)

    def test_delete_order_uses_delete_with_ref_in_url(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_delete', return_value={'success': True}) as mock_del:
            transport.delete_order('SO-001')
        mock_del.assert_called_once()
        endpoint = mock_del.call_args[1].get('endpoint') or mock_del.call_args[0][0]
        self.assertIn('SO-001', endpoint)
        self.assertIn('/Order/', endpoint)
        self.assertIn('?region=ANZ', endpoint)

    def test_delete_order_url_encodes_ref_with_slash(self):
        """Order names containing '/' must be percent-encoded in the DELETE URL."""
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_delete', return_value={'success': True}) as mock_del:
            transport.delete_order('S/001')
        endpoint = mock_del.call_args[1].get('endpoint') or mock_del.call_args[0][0]
        self.assertNotIn('S/001', endpoint)   # raw slash must not appear
        self.assertIn('S%2F001', endpoint)

    def test_delete_inward_uses_delete_with_ref_in_url(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_delete', return_value={'success': True}) as mock_del:
            transport.delete_inward('PO-001')
        mock_del.assert_called_once()
        endpoint = mock_del.call_args[1].get('endpoint') or mock_del.call_args[0][0]
        self.assertIn('PO-001', endpoint)
        self.assertIn('/Inward/', endpoint)
        self.assertIn('?region=ANZ', endpoint)

    def test_update_order_production_uses_production_url(self):
        transport = MainfreightRestTransport(_FakeConnector('production', mf_region='ANZ'))
        with patch.object(transport, 'send_put', return_value={'success': True}) as mock_put:
            transport.update_order('<Order/>')
        endpoint = mock_put.call_args[1].get('endpoint') or mock_put.call_args[0][2]
        self.assertIn('api.mainfreight.com', endpoint)
        self.assertNotIn('api-test', endpoint)


class TestTrackingStatusMap(unittest.TestCase):

    def setUp(self):
        self.transport = MainfreightRestTransport(_FakeConnector('test'))

    def _call_with_response(self, response_data):
        """Patch requests.get to return response_data as JSON, call get_tracking_status."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = response_data
        # Patch at the module level where mainfreight_rest imports requests
        with patch.object(_mf_rest_mod.requests, 'get', return_value=mock_resp):
            return self.transport.get_tracking_status('OTR000001')

    # --- Existing flat-Status path (must still work) ---

    def test_flat_status_delivered_maps_correctly(self):
        result = self._call_with_response({'Status': 'DELIVERED'})
        self.assertEqual(result.get('status'), 'mf_delivered')

    def test_flat_status_dispatched_maps_correctly(self):
        result = self._call_with_response({'Status': 'DISPATCHED'})
        self.assertEqual(result.get('status'), 'mf_dispatched')

    def test_flat_status_unknown_returns_empty(self):
        result = self._call_with_response({'Status': 'UNKNOWN_CODE'})
        self.assertEqual(result, {})

    # --- New eventCode fallback path ---

    def test_event_code_goods_delivered_maps_to_mf_delivered(self):
        data = {'events': [{'sequence': 1, 'code': 'GoodsDelivered'}]}
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_delivered')

    def test_event_code_picked_up_maps_to_mf_dispatched(self):
        data = {'events': [{'sequence': 1, 'code': 'PickedUp'}]}
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_dispatched')

    def test_event_code_latest_event_used_when_multiple(self):
        """When multiple events exist, the one with the highest sequence wins."""
        data = {
            'events': [
                {'sequence': 1, 'code': 'PickedUp'},
                {'sequence': 3, 'code': 'GoodsDelivered'},
                {'sequence': 2, 'code': 'InTransit'},
            ]
        }
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_delivered')

    def test_event_code_unknown_returns_empty(self):
        data = {'events': [{'sequence': 1, 'code': 'SomeUnknownEvent'}]}
        result = self._call_with_response(data)
        self.assertEqual(result, {})

    def test_no_status_and_no_events_returns_empty(self):
        result = self._call_with_response({'trackingUrl': 'https://track.example.com'})
        self.assertEqual(result, {})

    def test_flat_status_takes_priority_over_events(self):
        """If both Status and events are present, flat Status wins."""
        data = {
            'Status': 'IN_TRANSIT',
            'events': [{'sequence': 1, 'code': 'GoodsDelivered'}],
        }
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_in_transit')


if __name__ == '__main__':
    unittest.main()
