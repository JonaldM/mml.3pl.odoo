import unittest
from unittest.mock import MagicMock


class TestReassignWizard(unittest.TestCase):

    def test_action_reassign_writes_mf_queued(self):
        """Wizard action_reassign should reset status to mf_queued."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_exception'
        wizard.picking_id.name = 'WH/OUT/00001'
        wizard.connector_id = MagicMock()
        wizard.connector_id.id = 99
        wizard.connector_id.name = 'Auckland'
        wizard.reason = ''
        wizard.env = MagicMock()
        wizard.env.user.name = 'Admin'
        MfReassignWarehouseWizard.action_reassign(wizard)
        wizard.picking_id.write.assert_called_once_with({
            'x_mf_status': 'mf_queued',
        })

    def test_action_reassign_posts_chatter(self):
        """Wizard action_reassign should log reassignment in chatter."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_exception'
        wizard.picking_id.name = 'WH/OUT/00001'
        wizard.connector_id = MagicMock()
        wizard.connector_id.name = 'Auckland'
        wizard.reason = 'Capacity issue at original warehouse'
        wizard.env = MagicMock()
        wizard.env.user.name = 'Admin'
        MfReassignWarehouseWizard.action_reassign(wizard)
        wizard.picking_id.message_post.assert_called_once()
        call_kwargs = wizard.picking_id.message_post.call_args[1]
        self.assertIn('Auckland', call_kwargs.get('body', ''))

    def test_action_reassign_includes_reason_in_chatter(self):
        """Chatter note includes reason when provided."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_exception'
        wizard.connector_id = MagicMock()
        wizard.connector_id.name = 'Wellington'
        wizard.reason = 'Capacity issue'
        wizard.env = MagicMock()
        wizard.env.user.name = 'Admin'
        MfReassignWarehouseWizard.action_reassign(wizard)
        call_kwargs = wizard.picking_id.message_post.call_args[1]
        self.assertIn('Capacity issue', call_kwargs.get('body', ''))

    def test_action_reassign_invalid_status_raises(self):
        """Cannot reassign a picking that is not in exception or held status."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        from odoo.exceptions import UserError
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_delivered'
        wizard.picking_id.name = 'WH/OUT/00001'
        wizard.connector_id = MagicMock()
        wizard.reason = ''
        with self.assertRaises(UserError):
            MfReassignWarehouseWizard.action_reassign(wizard)

    def test_action_reassign_held_status_allowed(self):
        """mf_held_review status is also valid for reassignment."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_held_review'
        wizard.picking_id.name = 'WH/OUT/00002'
        wizard.connector_id = MagicMock()
        wizard.connector_id.name = 'Christchurch'
        wizard.reason = ''
        wizard.env = MagicMock()
        wizard.env.user.name = 'Admin'
        # Should NOT raise
        MfReassignWarehouseWizard.action_reassign(wizard)
        wizard.picking_id.write.assert_called_once_with({'x_mf_status': 'mf_queued'})

    def test_action_reassign_returns_window_close(self):
        """action_reassign returns ir.actions.act_window_close."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_exception'
        wizard.connector_id = MagicMock()
        wizard.connector_id.name = 'Auckland'
        wizard.reason = ''
        wizard.env = MagicMock()
        wizard.env.user.name = 'Admin'
        result = MfReassignWarehouseWizard.action_reassign(wizard)
        self.assertEqual(result.get('type'), 'ir.actions.act_window_close')
