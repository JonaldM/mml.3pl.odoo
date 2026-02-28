"""Wizard to reassign an exception picking to a different warehouse/connector."""
from odoo import models, fields
from odoo.exceptions import UserError


class MfReassignWarehouseWizard(models.TransientModel):
    _name = 'mf.reassign.warehouse.wizard'
    _description = 'Reassign MF Exception to Warehouse'

    picking_id = fields.Many2one('stock.picking', 'Picking', required=True, ondelete='cascade')
    connector_id = fields.Many2one(
        '3pl.connector', 'Target Connector / Warehouse',
        required=True,
        domain=[('active', '=', True)],
    )
    reason = fields.Text('Reason')

    def action_reassign(self):
        """Reassign the picking to the selected connector and re-queue."""
        self.ensure_one()
        picking = self.picking_id
        if picking.x_mf_status not in ('mf_exception', 'mf_held_review'):
            raise UserError(
                f'{picking.name} cannot be reassigned from status: {picking.x_mf_status}.'
            )
        picking.write({'x_mf_status': 'mf_queued'})
        note = (
            f'Reassigned to {self.connector_id.name} by {self.env.user.name}.'
            + (f' Reason: {self.reason}' if self.reason else '')
        )
        picking.message_post(body=note)
        return {'type': 'ir.actions.act_window_close'}
