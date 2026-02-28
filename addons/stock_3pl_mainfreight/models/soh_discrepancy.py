# addons/stock_3pl_mainfreight/models/soh_discrepancy.py
"""MF Stock-on-Hand discrepancy record — populated by the inbound SOH cron."""
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _compute_variance_pct(odoo_qty: float, mf_qty: float) -> float:
    """Return abs variance as a percentage of odoo_qty. Returns 100.0 if odoo_qty is 0."""
    if not odoo_qty:
        return 100.0
    return round(abs(mf_qty - odoo_qty) / odoo_qty * 100, 4)


class MfSohDiscrepancy(models.Model):
    _name = 'mf.soh.discrepancy'
    _description = 'MF SOH Discrepancy'
    _order = 'detected_date desc'

    product_id = fields.Many2one('product.product', 'Product', required=True, index=True)
    warehouse_id = fields.Many2one('stock.warehouse', 'Warehouse', required=True)
    odoo_qty = fields.Float('Odoo SOH', digits=(16, 3))
    mf_qty = fields.Float('MF SOH', digits=(16, 3))
    variance_qty = fields.Float('Variance (units)', digits=(16, 3),
                                compute='_compute_variance', store=True)
    variance_pct = fields.Float('Variance %', digits=(10, 4),
                                compute='_compute_variance', store=True)
    detected_date = fields.Datetime('Detected', default=fields.Datetime.now, index=True)
    state = fields.Selection([
        ('open', 'Open'),
        ('investigated', 'Investigated'),
        ('accepted', 'Accepted — Shrinkage'),
    ], default='open', index=True)
    investigated_by = fields.Many2one('res.users', 'Investigated By', readonly=True)
    investigated_date = fields.Datetime('Investigated Date', readonly=True)
    accepted_by = fields.Many2one('res.users', 'Accepted By', readonly=True)
    accepted_date = fields.Datetime('Accepted Date', readonly=True)
    accept_reason = fields.Text('Acceptance Reason', readonly=True)
    active = fields.Boolean(default=True)

    @api.depends('odoo_qty', 'mf_qty')
    def _compute_variance(self):
        for rec in self:
            rec.variance_qty = rec.mf_qty - rec.odoo_qty
            rec.variance_pct = _compute_variance_pct(rec.odoo_qty, rec.mf_qty)

    def action_mark_investigated(self):
        self.write({
            'state': 'investigated',
            'investigated_by': self.env.user.id,
            'investigated_date': fields.Datetime.now(),
        })

    def action_accept_discrepancy(self, reason=''):
        """Accept the discrepancy as shrinkage and update Odoo quant to MF figure.

        - Validates that a non-empty reason is provided (audit trail requirement).
        - Writes MF qty to stock.quant (MF is source of truth for physical stock).
        - Logs acceptance with user, date, reason.
        - Sets state = 'accepted' so shrinkage KPI can accumulate it.
        """
        self.ensure_one()
        if not reason or not reason.strip():
            raise UserError('A reason is required to accept a discrepancy as shrinkage.')
        if self.state == 'accepted':
            raise UserError(
                f'{self.product_id.display_name} discrepancy is already accepted as shrinkage.'
            )
        # Update Odoo quant to match MF (MF is source of truth)
        stock_location = self.warehouse_id.lot_stock_id
        quants = self.env['stock.quant'].search([
            ('product_id', '=', self.product_id.id),
            ('location_id', '=', stock_location.id),
        ], limit=1)
        if quants:
            quants[0].sudo().write({'quantity': self.mf_qty})
        else:
            self.env['stock.quant'].sudo().create({
                'product_id': self.product_id.id,
                'location_id': stock_location.id,
                'quantity': self.mf_qty,
            })
        self.write({
            'state': 'accepted',
            'accepted_by': self.env.user.id,
            'accepted_date': fields.Datetime.now(),
            'accept_reason': reason.strip(),
        })
