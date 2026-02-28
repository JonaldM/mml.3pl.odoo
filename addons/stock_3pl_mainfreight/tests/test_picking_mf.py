# addons/stock_3pl_mainfreight/tests/test_picking_mf.py
"""
Pure-Python structural tests for picking_mf.py and sale_order_mf.py.
No Odoo runtime required — tests verify field definitions and model metadata.
"""
import sys
import types
import unittest


def _stub_odoo():
    """Install minimal odoo stubs so the model modules can be imported."""
    if 'odoo' in sys.modules:
        return

    odoo = types.ModuleType('odoo')
    odoo_models = types.ModuleType('odoo.models')
    odoo_fields = types.ModuleType('odoo.fields')

    class _Field:
        def __init__(self, *args, **kwargs):
            self._kwargs = kwargs

        def __set_name__(self, owner, name):
            self._name = name
            if not hasattr(owner, '_fields_meta'):
                owner._fields_meta = {}
            owner._fields_meta[name] = self

    class Selection(_Field):
        def __init__(self, selection=None, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.selection = selection
            self.default = kwargs.get('default')

    class Boolean(_Field):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.default = kwargs.get('default')

    class Char(_Field):
        pass

    class Datetime(_Field):
        pass

    odoo_fields.Selection = Selection
    odoo_fields.Boolean = Boolean
    odoo_fields.Char = Char
    odoo_fields.Datetime = Datetime

    class Model:
        _inherit = None
        _fields_meta = {}

    odoo_models.Model = Model

    odoo.models = odoo_models
    odoo.fields = odoo_fields

    sys.modules['odoo'] = odoo
    sys.modules['odoo.models'] = odoo_models
    sys.modules['odoo.fields'] = odoo_fields


_stub_odoo()


# Import the modules under test after stubs are in place
import importlib.util, pathlib

_BASE = pathlib.Path(__file__).parent.parent / 'models'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _BASE / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_picking_mod = _load('picking_mf')
_so_mod = _load('sale_order_mf')

MF_STATUS = _picking_mod.MF_STATUS
MF_ROUTED_BY = _picking_mod.MF_ROUTED_BY
StockPickingMF = _picking_mod.StockPickingMF
SaleOrderMFFields = _so_mod.SaleOrderMFFields


class TestMFStatusList(unittest.TestCase):

    def test_mf_status_has_10_entries(self):
        """Test 1: MF_STATUS list contains all 10 expected keys."""
        self.assertEqual(len(MF_STATUS), 10)

    def test_mf_status_keys(self):
        """Test 1 (extended): MF_STATUS contains all expected keys."""
        keys = [k for k, _ in MF_STATUS]
        expected = [
            'draft', 'mf_held_review', 'mf_queued', 'mf_sent',
            'mf_received', 'mf_dispatched', 'mf_in_transit',
            'mf_out_for_delivery', 'mf_delivered', 'mf_exception',
        ]
        self.assertEqual(keys, expected)


class TestMFRoutedByList(unittest.TestCase):

    def test_mf_routed_by_has_3_entries(self):
        """Test 2: MF_ROUTED_BY list contains 3 expected keys."""
        self.assertEqual(len(MF_ROUTED_BY), 3)

    def test_mf_routed_by_keys(self):
        """Test 2 (extended): MF_ROUTED_BY contains manual/auto_closest/auto_split."""
        keys = [k for k, _ in MF_ROUTED_BY]
        self.assertIn('manual', keys)
        self.assertIn('auto_closest', keys)
        self.assertIn('auto_split', keys)


class TestStockPickingMF(unittest.TestCase):

    def test_inherit_is_stock_picking(self):
        """Test 3: StockPickingMF._inherit == 'stock.picking'."""
        self.assertEqual(StockPickingMF._inherit, 'stock.picking')

    def test_x_mf_status_default_is_draft(self):
        """Test 4: x_mf_status field has default='draft'."""
        field = StockPickingMF._fields_meta.get('x_mf_status')
        self.assertIsNotNone(field, 'x_mf_status field not found on StockPickingMF')
        self.assertEqual(field.default, 'draft')

    def test_x_mf_cross_border_default_is_false(self):
        """Test 5: x_mf_cross_border field has default=False."""
        field = StockPickingMF._fields_meta.get('x_mf_cross_border')
        self.assertIsNotNone(field, 'x_mf_cross_border field not found on StockPickingMF')
        self.assertIs(field.default, False)


class TestSaleOrderMFFields(unittest.TestCase):

    def test_inherit_is_sale_order(self):
        """Test 6: SaleOrderMFFields._inherit == 'sale.order'."""
        self.assertEqual(SaleOrderMFFields._inherit, 'sale.order')

    def test_x_mf_sent_default_is_false(self):
        """Test 7: x_mf_sent field has default=False."""
        field = SaleOrderMFFields._fields_meta.get('x_mf_sent')
        self.assertIsNotNone(field, 'x_mf_sent field not found on SaleOrderMFFields')
        self.assertIs(field.default, False)

    def test_x_mf_split_default_is_false(self):
        """Test 8: x_mf_split field has default=False."""
        field = SaleOrderMFFields._fields_meta.get('x_mf_split')
        self.assertIsNotNone(field, 'x_mf_split field not found on SaleOrderMFFields')
        self.assertIs(field.default, False)


if __name__ == '__main__':
    unittest.main()
