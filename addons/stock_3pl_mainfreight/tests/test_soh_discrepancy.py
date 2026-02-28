import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime


class TestSohDiscrepancyModel(unittest.TestCase):

    def test_variance_pct_computed_from_qty(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        self.assertAlmostEqual(_compute_variance_pct(odoo_qty=100.0, mf_qty=98.0), 2.0)

    def test_variance_pct_zero_odoo_qty(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        self.assertEqual(_compute_variance_pct(odoo_qty=0.0, mf_qty=5.0), 100.0)

    def test_variance_pct_exact_match(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        self.assertEqual(_compute_variance_pct(odoo_qty=50.0, mf_qty=50.0), 0.0)

    def test_variance_pct_loss(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        # MF shows less than Odoo — loss
        self.assertAlmostEqual(_compute_variance_pct(odoo_qty=100.0, mf_qty=95.0), 5.0)

    def test_variance_pct_gain(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        # MF shows more than Odoo — gain (still a discrepancy)
        self.assertAlmostEqual(_compute_variance_pct(odoo_qty=100.0, mf_qty=105.0), 5.0)

    def test_mark_investigated_sets_fields(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        record = MagicMock()
        record.state = 'open'
        record.__iter__ = MagicMock(return_value=iter([record]))
        record.env = MagicMock()
        record.env.user.id = 42
        MfSohDiscrepancy.action_mark_investigated(record)
        record.write.assert_called_once()
        call_vals = record.write.call_args[0][0]
        self.assertEqual(call_vals['state'], 'investigated')
        self.assertEqual(call_vals['investigated_by'], 42)
        self.assertIn('investigated_date', call_vals)

    def test_accept_discrepancy_requires_reason(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        from odoo.exceptions import UserError
        record = MagicMock(spec=MfSohDiscrepancy)
        record.state = 'open'
        record.ensure_one = MagicMock()
        record.env = MagicMock()
        with self.assertRaises(UserError):
            MfSohDiscrepancy.action_accept_discrepancy(record, reason='')

    def test_accept_discrepancy_raises_if_already_accepted(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        from odoo.exceptions import UserError
        record = MagicMock(spec=MfSohDiscrepancy)
        record.state = 'accepted'
        record.ensure_one = MagicMock()
        record.product_id.display_name = 'Test Product'
        record.env = MagicMock()
        with self.assertRaises(UserError):
            MfSohDiscrepancy.action_accept_discrepancy(record, reason='valid reason')

    def test_accept_discrepancy_writes_quant_and_logs(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        record = MagicMock(spec=MfSohDiscrepancy)
        record.state = 'open'
        record.ensure_one = MagicMock()
        record.product_id.id = 1
        record.product_id.display_name = 'Test Product'
        record.warehouse_id.lot_stock_id.id = 10
        record.mf_qty = 95.0
        record.odoo_qty = 100.0
        record.env = MagicMock()
        record.env.user.id = 7
        quant_mock = MagicMock()
        quant_list = MagicMock()
        quant_list.__bool__ = MagicMock(return_value=True)
        quant_model = MagicMock()
        quant_model.search.return_value = quant_list
        record.env.__getitem__ = MagicMock(side_effect=lambda k: {
            'stock.quant': quant_model,
        }.get(k, MagicMock()))
        MfSohDiscrepancy.action_accept_discrepancy(record, reason='Confirmed shrinkage')
        # Quant list should have sudo().write() called on it (not quant[0])
        quant_list.sudo.return_value.write.assert_called_once_with({'quantity': 95.0})
        # Record state should be updated
        record.write.assert_called_once()
        write_vals = record.write.call_args[0][0]
        self.assertEqual(write_vals['state'], 'accepted')
        self.assertEqual(write_vals['accepted_by'], 7)
        self.assertEqual(write_vals['accept_reason'], 'Confirmed shrinkage')

    def test_accept_discrepancy_creates_quant_if_missing(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        record = MagicMock(spec=MfSohDiscrepancy)
        record.state = 'investigated'
        record.ensure_one = MagicMock()
        record.product_id.id = 2
        record.product_id.display_name = 'New Product'
        record.warehouse_id.lot_stock_id.id = 10
        record.mf_qty = 50.0
        record.odoo_qty = 0.0
        record.env = MagicMock()
        record.env.user.id = 3
        quant_model = MagicMock()
        quant_model.search.return_value = []  # no existing quant
        record.env.__getitem__ = MagicMock(side_effect=lambda k: {
            'stock.quant': quant_model,
        }.get(k, MagicMock()))
        MfSohDiscrepancy.action_accept_discrepancy(record, reason='New stock found')
        quant_model.sudo.return_value.create.assert_called_once()

    def test_variance_pct_both_zero(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        self.assertEqual(_compute_variance_pct(odoo_qty=0.0, mf_qty=0.0), 0.0)

    def test_mark_investigated_raises_if_accepted(self):
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        from odoo.exceptions import UserError
        record = MagicMock()
        record.state = 'accepted'
        record.product_id.display_name = 'Test Product'
        # Make iteration over self yield the record
        record.__iter__ = MagicMock(return_value=iter([record]))
        with self.assertRaises(UserError):
            MfSohDiscrepancy.action_mark_investigated(record)

    def test_action_open_accept_wizard_returns_window_action(self):
        """action_open_accept_wizard returns an ir.actions.act_window dict with correct context."""
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        record = MagicMock(spec=MfSohDiscrepancy)
        record.id = 42
        result = MfSohDiscrepancy.action_open_accept_wizard(record)
        self.assertEqual(result.get('type'), 'ir.actions.act_window')
        self.assertEqual(result.get('res_model'), 'mf.accept.discrepancy.wizard')
        self.assertEqual(result.get('context', {}).get('default_discrepancy_id'), 42)
