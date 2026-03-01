# addons/stock_3pl_mainfreight/tests/test_webhook_controller.py
"""Pure-Python tests for the MF webhook stub controller.

Tests only _validate_webhook_secret — no Odoo runtime required.
The controller's HTTP route methods are dormant stubs and are not exercised here.

Run with:
    python -m pytest addons/stock_3pl_mainfreight/tests/test_webhook_controller.py -v
"""
import sys
import types
import importlib.util
import pathlib
import unittest

# ---------------------------------------------------------------------------
# Stub odoo.http before loading the controller module
# ---------------------------------------------------------------------------
# The conftest.py stubs odoo root, odoo.models, etc., but not odoo.http.
# We add that stub here so controllers/webhook.py can be imported without
# a live Odoo runtime.

_odoo_mod = sys.modules.get('odoo') or types.ModuleType('odoo')


class _FakeController:
    pass


class _FakeHttp:
    Controller = _FakeController

    @staticmethod
    def route(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

    @staticmethod
    def make_json_response(data, status=200):
        return (data, status)


_odoo_http = types.ModuleType('odoo.http')
_odoo_http.Controller = _FakeController
_odoo_http.route = _FakeHttp.route
_odoo_http.make_json_response = _FakeHttp.make_json_response
_odoo_http.request = None   # used in _handle_webhook, not in _validate_webhook_secret

_odoo_mod.http = _odoo_http
sys.modules.setdefault('odoo', _odoo_mod)
sys.modules['odoo.http'] = _odoo_http

# ---------------------------------------------------------------------------
# Load controllers/webhook.py directly from disk
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_WEBHOOK_PATH = _HERE.parent / 'controllers' / 'webhook.py'

_key = 'stock_3pl_mainfreight.controllers.webhook'
if _key not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_key, str(_WEBHOOK_PATH))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_key] = _mod
    _spec.loader.exec_module(_mod)
else:
    _mod = sys.modules[_key]

_validate_webhook_secret = _mod._validate_webhook_secret


# ---------------------------------------------------------------------------
# Fake Odoo env helpers
# ---------------------------------------------------------------------------

class _FakeConfigParam:
    """Minimal stand-in for ir.config_parameter."""

    def __init__(self, value):
        self._value = value

    def sudo(self):
        return self

    def get_param(self, key, default=''):
        return self._value if self._value is not None else default


class _FakeEnv:
    """Minimal stand-in for Odoo environment."""

    def __init__(self, stored_secret):
        self._stored = stored_secret

    def __getitem__(self, key):
        if key == 'ir.config_parameter':
            return _FakeConfigParam(self._stored)
        raise KeyError(key)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidateWebhookSecret(unittest.TestCase):
    """Unit tests for _validate_webhook_secret."""

    def test_valid_secret_returns_true(self):
        """Matching stored and request secrets must return True."""
        env = _FakeEnv('mysecret')
        self.assertTrue(_validate_webhook_secret(env, 'mysecret'))

    def test_wrong_secret_returns_false(self):
        """Mismatched request secret must return False."""
        env = _FakeEnv('mysecret')
        self.assertFalse(_validate_webhook_secret(env, 'wrong'))

    def test_empty_request_secret_returns_false(self):
        """Empty string request secret must be rejected."""
        env = _FakeEnv('mysecret')
        self.assertFalse(_validate_webhook_secret(env, ''))

    def test_none_request_secret_returns_false(self):
        """None request secret must be rejected immediately (no env lookup)."""
        env = _FakeEnv('mysecret')
        self.assertFalse(_validate_webhook_secret(env, None))

    def test_empty_stored_secret_returns_false(self):
        """Unconfigured system (empty stored secret) must never grant access."""
        env = _FakeEnv('')
        self.assertFalse(_validate_webhook_secret(env, 'mysecret'))

    def test_none_stored_secret_returns_false(self):
        """None from get_param (falls back to '') must never grant access."""
        env = _FakeEnv(None)  # get_param will return default=''
        self.assertFalse(_validate_webhook_secret(env, 'mysecret'))
