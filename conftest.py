# conftest.py — root-level: install Odoo stubs before pytest collects any modules
"""
Installs minimal odoo stubs and wires up the addons path so that pure-Python
structural tests can be collected and run without a live Odoo runtime.
"""
import sys
import types
import pathlib
import importlib.util
import pytest

# Ensure the addons directory is on sys.path so direct imports work
_ROOT = pathlib.Path(__file__).parent
_ADDONS = _ROOT / 'addons'
if str(_ADDONS) not in sys.path:
    sys.path.insert(0, str(_ADDONS))


def _install_odoo_stubs():
    """Build and register lightweight odoo stubs in sys.modules (idempotent)."""
    if 'odoo' in sys.modules and hasattr(sys.modules['odoo'], '_stubbed'):
        return

    # ---- odoo.fields ----
    odoo_fields = types.ModuleType('odoo.fields')

    class _BaseField:
        """Minimal field descriptor that captures kwargs for introspection."""
        def __init__(self, *args, **kwargs):
            self._kwargs = kwargs
            self.default = kwargs.get('default')
            self.string = args[0] if args else kwargs.get('string', '')

        def __set_name__(self, owner, name):
            self._attr_name = name
            if '_fields_meta' not in owner.__dict__:
                owner._fields_meta = {}
            owner._fields_meta[name] = self

    class Selection(_BaseField):
        def __init__(self, selection=None, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.selection = selection or []

    class Boolean(_BaseField):
        pass

    class Char(_BaseField):
        pass

    class Datetime(_BaseField):
        @classmethod
        def now(cls):
            import datetime
            return datetime.datetime.utcnow()

    class Date(_BaseField):
        pass

    class Many2one(_BaseField):
        pass

    class One2many(_BaseField):
        pass

    class Many2many(_BaseField):
        pass

    class Float(_BaseField):
        pass

    class Integer(_BaseField):
        pass

    class Text(_BaseField):
        pass

    odoo_fields.Selection = Selection
    odoo_fields.Boolean = Boolean
    odoo_fields.Char = Char
    odoo_fields.Datetime = Datetime
    odoo_fields.Date = Date
    odoo_fields.Many2one = Many2one
    odoo_fields.One2many = One2many
    odoo_fields.Many2many = Many2many
    odoo_fields.Float = Float
    odoo_fields.Integer = Integer
    odoo_fields.Text = Text

    # ---- odoo.models ----
    odoo_models = types.ModuleType('odoo.models')

    class Model:
        _inherit = None
        _name = None
        _fields_meta = {}

        def write(self, vals):
            pass

        def ensure_one(self):
            pass

        def search(self, domain, **kwargs):
            return []

        def sudo(self):
            return self

        def create(self, vals):
            pass

    class AbstractModel(Model):
        pass

    class TransientModel(Model):
        pass

    odoo_models.Model = Model
    odoo_models.AbstractModel = AbstractModel
    odoo_models.TransientModel = TransientModel

    # ---- odoo.api ----
    odoo_api = types.ModuleType('odoo.api')
    odoo_api.model = lambda f: f
    odoo_api.depends = lambda *args: (lambda f: f)
    odoo_api.constrains = lambda *args: (lambda f: f)
    odoo_api.onchange = lambda *args: (lambda f: f)

    # ---- odoo.exceptions ----
    odoo_exceptions = types.ModuleType('odoo.exceptions')

    class ValidationError(Exception):
        pass

    class UserError(Exception):
        pass

    odoo_exceptions.ValidationError = ValidationError
    odoo_exceptions.UserError = UserError

    # ---- odoo.tests ----
    # TransactionCase inherits unittest.TestCase so pytest recognises test methods.
    # Tests that call self.env will still fail — they require odoo-bin --test-enable.
    import unittest
    odoo_tests = types.ModuleType('odoo.tests')

    class TransactionCase(unittest.TestCase):
        """Stub: provides assertion methods. self.env is NOT available without Odoo."""

    def tagged(*args):
        def decorator(cls):
            return cls
        return decorator

    odoo_tests.TransactionCase = TransactionCase
    odoo_tests.tagged = tagged

    # ---- odoo.tests.common (alias — some test files import from here) ----
    odoo_tests_common = types.ModuleType('odoo.tests.common')
    odoo_tests_common.TransactionCase = TransactionCase

    # ---- odoo.http ----
    # Stub for the Odoo HTTP controller framework.  Required so that
    # controllers/webhook.py (and any future controllers) can be imported
    # without a live Odoo runtime.
    odoo_http = types.ModuleType('odoo.http')

    class _StubController:
        pass

    def _stub_route(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

    odoo_http.Controller = _StubController
    odoo_http.route = _stub_route
    odoo_http.request = None  # placeholder; not safe to use outside Odoo runtime

    # ---- odoo (root) ----
    odoo = types.ModuleType('odoo')
    odoo._stubbed = True
    odoo.models = odoo_models
    odoo.fields = odoo_fields
    odoo.api = odoo_api
    odoo.exceptions = odoo_exceptions
    odoo.tests = odoo_tests
    odoo.http = odoo_http

    sys.modules['odoo'] = odoo
    sys.modules['odoo.models'] = odoo_models
    sys.modules['odoo.fields'] = odoo_fields
    sys.modules['odoo.api'] = odoo_api
    sys.modules['odoo.exceptions'] = odoo_exceptions
    sys.modules['odoo.tests'] = odoo_tests
    sys.modules['odoo.tests.common'] = odoo_tests_common
    sys.modules['odoo.http'] = odoo_http

    # ---- odoo.addons namespace ----
    # Wire odoo.addons.* to the real addon directories on sys.path.
    # This lets document modules import AbstractDocument etc. without Odoo.
    odoo_addons = types.ModuleType('odoo.addons')
    sys.modules['odoo.addons'] = odoo_addons
    odoo.addons = odoo_addons

    # Pre-load stock_3pl_core's pure-Python document_base so that
    # stock_3pl_mainfreight document builders can import AbstractDocument.
    def _load_real_module(full_name, file_path):
        """Load a real Python file as a sys.modules entry under the given name."""
        if full_name in sys.modules:
            return sys.modules[full_name]
        spec = importlib.util.spec_from_file_location(full_name, file_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = mod
        spec.loader.exec_module(mod)
        return mod

    _core = _ADDONS / 'stock_3pl_core'

    # Register stub package entries for the addon namespaces.
    # Set __path__ on each so Python can resolve real submodule imports from disk
    # (e.g. odoo.addons.stock_3pl_mainfreight.document.product_spec).
    _mf = _ADDONS / 'stock_3pl_mainfreight'
    for pkg_name, real_path in (
        ('odoo.addons.stock_3pl_core', _core),
        ('odoo.addons.stock_3pl_core.models', _core / 'models'),
        ('odoo.addons.stock_3pl_core.utils', _core / 'utils'),
        ('odoo.addons.stock_3pl_mainfreight', _mf),
        ('odoo.addons.stock_3pl_mainfreight.document', _mf / 'document'),
        ('odoo.addons.stock_3pl_mainfreight.models', _mf / 'models'),
        ('odoo.addons.stock_3pl_mainfreight.transport', _mf / 'transport'),
    ):
        if pkg_name not in sys.modules:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = [str(real_path)]
            pkg.__package__ = pkg_name
            sys.modules[pkg_name] = pkg

    # Load document_base directly so AbstractDocument is available
    _load_real_module(
        'odoo.addons.stock_3pl_core.models.document_base',
        _core / 'models' / 'document_base.py',
    )


_install_odoo_stubs()


def pytest_collection_modifyitems(config, items):
    """Auto-mark TransactionCase tests as odoo_integration (requires odoo-bin)."""
    from odoo.tests import TransactionCase  # already stubbed in sys.modules by _install_odoo_stubs()
    for item in items:
        if isinstance(item, pytest.Class):
            continue
        cls = getattr(item, 'cls', None)
        if cls is not None and issubclass(cls, TransactionCase):
            item.add_marker(pytest.mark.odoo_integration)
