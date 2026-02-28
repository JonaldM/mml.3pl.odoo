# addons/stock_3pl_mainfreight/tests/test_picking_mf.py
"""
Pure-Python structural tests for picking_mf.py and sale_order_mf.py.
No Odoo runtime required — tests verify field definitions and model metadata.
"""
import sys
import types
import unittest
from unittest.mock import MagicMock


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
            if '_fields_meta' not in owner.__dict__:
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

    def test_mf_status_has_11_entries(self):
        """Test 1: MF_STATUS list contains all 11 expected keys."""
        self.assertEqual(len(MF_STATUS), 11)

    def test_mf_status_keys(self):
        """Test 1 (extended): MF_STATUS contains all expected keys."""
        keys = [k for k, _ in MF_STATUS]
        expected = [
            'draft', 'mf_held_review', 'mf_queued', 'mf_sent',
            'mf_received', 'mf_dispatched', 'mf_in_transit',
            'mf_out_for_delivery', 'mf_delivered', 'mf_exception',
            'mf_resolved',
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

    # --- Task 2: mf_resolved status ---

    def test_mf_resolved_in_status_selection(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import MF_STATUS
        values = [v for v, _ in MF_STATUS]
        self.assertIn('mf_resolved', values)

    def test_mf_resolved_after_mf_exception(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import MF_STATUS
        values = [v for v, _ in MF_STATUS]
        self.assertGreater(values.index('mf_resolved'), values.index('mf_exception'))

    # --- Task 2: x_mf_assigned_to field ---

    def test_x_mf_assigned_to_field_exists(self):
        field = StockPickingMF._fields_meta.get('x_mf_assigned_to')
        self.assertIsNotNone(field, 'x_mf_assigned_to field not found on StockPickingMF')

    def test_x_mf_assigned_to_copy_false(self):
        field = StockPickingMF._fields_meta.get('x_mf_assigned_to')
        self.assertIsNotNone(field)
        self.assertFalse(field._kwargs.get('copy', True))

    def test_x_mf_assigned_to_groups_set(self):
        field = StockPickingMF._fields_meta.get('x_mf_assigned_to')
        self.assertIsNotNone(field)
        self.assertEqual(field._kwargs.get('groups'), 'stock.group_stock_manager')


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


class TestActionMfRetry(unittest.TestCase):
    """Tests for StockPickingMF.action_mf_retry."""

    def test_action_mf_retry_raises_if_not_exception(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import StockPickingMF
        from odoo.exceptions import UserError
        picking = MagicMock()
        picking.x_mf_status = 'mf_sent'
        picking.name = 'WH/OUT/001'
        record = MagicMock()
        record.__iter__ = MagicMock(return_value=iter([picking]))
        with self.assertRaises(UserError):
            StockPickingMF.action_mf_retry(record)

    def test_action_mf_retry_sets_queued_status(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import StockPickingMF
        picking = MagicMock()
        picking.x_mf_status = 'mf_exception'
        picking.name = 'WH/OUT/001'
        record = MagicMock()
        record.__iter__ = MagicMock(return_value=iter([picking]))
        record.env = MagicMock()
        record.env.user.name = 'Admin'
        StockPickingMF.action_mf_retry(record)
        record.write.assert_called_once_with({'x_mf_status': 'mf_queued'})


class TestActionMfMarkResolved(unittest.TestCase):
    """Tests for StockPickingMF.action_mf_mark_resolved."""

    def test_action_mf_mark_resolved_raises_if_not_exception(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import StockPickingMF
        from odoo.exceptions import UserError
        picking = MagicMock()
        picking.x_mf_status = 'mf_delivered'
        picking.name = 'WH/OUT/001'
        record = MagicMock()
        record.__iter__ = MagicMock(return_value=iter([picking]))
        with self.assertRaises(UserError):
            StockPickingMF.action_mf_mark_resolved(record)

    def test_action_mf_mark_resolved_sets_resolved_status(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import StockPickingMF
        picking = MagicMock()
        picking.x_mf_status = 'mf_exception'
        picking.name = 'WH/OUT/001'
        record = MagicMock()
        record.__iter__ = MagicMock(return_value=iter([picking]))
        record.env = MagicMock()
        record.env.user.name = 'Admin'
        StockPickingMF.action_mf_mark_resolved(record)
        record.write.assert_called_once_with({'x_mf_status': 'mf_resolved'})


class TestActionMfEscalate(unittest.TestCase):
    """Tests for StockPickingMF.action_mf_escalate."""

    def test_action_mf_escalate_raises_if_no_user_configured(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import StockPickingMF
        from odoo.exceptions import UserError
        record = MagicMock()
        icp = MagicMock()
        icp.get_param.return_value = '0'
        record.env = MagicMock()
        record.env.__getitem__ = MagicMock(return_value=MagicMock(sudo=MagicMock(return_value=icp)))
        with self.assertRaises(UserError):
            StockPickingMF.action_mf_escalate(record)

    def test_action_mf_escalate_schedules_activity(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import StockPickingMF
        picking = MagicMock()
        picking.name = 'WH/OUT/001'
        record = MagicMock()
        record.__iter__ = MagicMock(return_value=iter([picking]))
        icp = MagicMock()
        icp.get_param.return_value = '5'
        record.env = MagicMock()
        record.env.__getitem__ = MagicMock(return_value=MagicMock(sudo=MagicMock(return_value=icp)))
        record.env.user.name = 'Admin'
        StockPickingMF.action_mf_escalate(record)
        picking.activity_schedule.assert_called_once()
        picking.message_post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
