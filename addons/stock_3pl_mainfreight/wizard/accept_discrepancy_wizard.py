# addons/stock_3pl_mainfreight/wizard/accept_discrepancy_wizard.py
"""Wizard to accept a SOH discrepancy as shrinkage."""
from odoo import models, fields


class MfAcceptDiscrepancyWizard(models.TransientModel):
    _name = 'mf.accept.discrepancy.wizard'
    _description = 'Accept SOH Discrepancy as Shrinkage'

    discrepancy_id = fields.Many2one(
        'mf.soh.discrepancy', 'Discrepancy', required=True
    )
    variance_qty = fields.Float(
        'Units to Accept', related='discrepancy_id.variance_qty', readonly=True
    )
    variance_pct = fields.Float(
        'Variance %', related='discrepancy_id.variance_pct', readonly=True
    )
    reason = fields.Text('Reason', required=True)

    def action_accept(self):
        """Delegate to the discrepancy model's accept method, then close."""
        self.ensure_one()
        self.discrepancy_id.action_accept_discrepancy(reason=self.reason)
        return {'type': 'ir.actions.act_window_close'}
