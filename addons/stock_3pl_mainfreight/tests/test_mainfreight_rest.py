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

        def poll(self, path=None):
            return []

    core_rest_mod.RestTransport = RestTransport

    sys.modules['odoo.addons.stock_3pl_core'] = core_pkg
    sys.modules['odoo.addons.stock_3pl_core.transport'] = core_transport_pkg
    sys.modules['odoo.addons.stock_3pl_core.transport.rest_api'] = core_rest_mod

    # Also stub the requests module reference used in mainfreight_rest.py
    if 'requests' not in sys.modules:
        import requests  # real requests — available in dev environment


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
    """Minimal connector stub for testing _get_base_url."""
    def __init__(self, environment, api_secret='secret'):
        self.environment = environment
        self.api_secret = api_secret


class TestMFEndpointsConstant(unittest.TestCase):

    def test_endpoints_has_test_key(self):
        """Test 4: MF_ENDPOINTS has a 'test' key."""
        self.assertIn('test', MF_ENDPOINTS)

    def test_endpoints_has_production_key(self):
        """Test 4 (extended): MF_ENDPOINTS has a 'production' key."""
        self.assertIn('production', MF_ENDPOINTS)

    def test_test_url_points_to_test_host(self):
        """Test 4 (extended): test URL contains 'warehouseapi-test'."""
        self.assertIn('warehouseapi-test.mainfreight.com', MF_ENDPOINTS['test'])

    def test_production_url_points_to_prod_host(self):
        """Test 4 (extended): production URL contains 'warehouseapi.mainfreight.com'."""
        self.assertIn('warehouseapi.mainfreight.com', MF_ENDPOINTS['production'])


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


if __name__ == '__main__':
    unittest.main()
