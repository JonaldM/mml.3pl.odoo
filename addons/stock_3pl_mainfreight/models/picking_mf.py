# addons/stock_3pl_mainfreight/models/picking_mf.py
from odoo import models, fields
from odoo.exceptions import UserError

MF_STATUS = [
    ('draft', 'Draft'),
    ('mf_held_review', 'Held — Cross-Border Review'),  # V3: manual release required
    ('mf_queued', 'Queued for 3PL'),
    ('mf_sent', 'Sent to 3PL'),
    ('mf_received', 'Received by MF'),
    ('mf_dispatched', 'Dispatched'),
    ('mf_in_transit', 'In Transit'),
    ('mf_out_for_delivery', 'Out for Delivery'),
    ('mf_delivered', 'Delivered'),
    ('mf_exception', 'Exception'),
    ('mf_resolved', 'Resolved'),
]

MF_ROUTED_BY = [
    ('manual', 'Manual'),
    ('auto_closest', 'Auto — Closest Warehouse'),
    ('auto_split', 'Auto — Split Order'),
]


class StockPickingMF(models.Model):
    _inherit = 'stock.picking'

    x_mf_status = fields.Selection(MF_STATUS, 'MF Status', default='draft', index=True, copy=False)
    x_mf_connote = fields.Char('Connote No.', copy=False)
    x_mf_pick_id = fields.Char('MF Pick ID', copy=False)
    x_mf_pod_url = fields.Char('POD URL', copy=False)
    x_mf_signed_by = fields.Char('Signed By', copy=False)
    x_mf_dispatched_date = fields.Datetime('Dispatched Date', copy=False)
    x_mf_delivered_date = fields.Datetime('Delivered Date', copy=False)
    # Sprint 2 routing fields — declared here to avoid migration on routing engine rollout
    x_mf_routed_by = fields.Selection(MF_ROUTED_BY, 'Routing Method', readonly=True, copy=False)
    x_mf_cross_border = fields.Boolean('Cross-Border', default=False)
    # Phase 2: exception ownership
    x_mf_assigned_to = fields.Many2one('res.users', 'Exception Assigned To', copy=False,
                                        groups='stock.group_stock_manager')
    # Phase 2: connector override for re-routed exceptions
    x_mf_connector_id = fields.Many2one(
        '3pl.connector', 'Override Connector',
        copy=False,
        help='Set by the Reassign Wizard when an exception is manually re-routed to a '
             'different 3PL connector. The push cron will use this connector when '
             're-creating the 3pl.message for this picking.',
    )

    def action_approve_cross_border(self):
        """Release cross-border held pickings for MF push.

        Only valid from mf_held_review status. Advances to mf_queued
        so the next push cron run will include this picking.
        Can be called on a recordset — validates each picking individually.
        """
        for picking in self:
            if picking.x_mf_status != 'mf_held_review':
                raise UserError(
                    f'{picking.name} is not in cross-border held status '
                    f'(current: {picking.x_mf_status or "not set"}).'
                )
        self.write({'x_mf_status': 'mf_queued'})

    def action_mf_retry(self):
        """Re-queue exception picking back to mf_queued."""
        for picking in self:
            if picking.x_mf_status != 'mf_exception':
                raise UserError(
                    f'{picking.name} is not in exception status '
                    f'(current: {picking.x_mf_status or "not set"}).'
                )
        self.write({'x_mf_status': 'mf_queued'})
        self._message_log_batch('Re-queued for 3PL push by %(user)s.')

    def action_mf_mark_resolved(self):
        """Mark exception picking as resolved without retry."""
        for picking in self:
            if picking.x_mf_status != 'mf_exception':
                raise UserError(
                    f'{picking.name} is not in exception status.'
                )
        self.write({'x_mf_status': 'mf_resolved'})
        self._message_log_batch('Marked resolved by %(user)s.')

    def action_mf_escalate(self):
        """Schedule escalation activity for configured user. Raises UserError if no user configured."""
        ICP = self.env['ir.config_parameter'].sudo()
        escalation_user_id = int(ICP.get_param(
            'stock_3pl_mainfreight.exception_escalation_user', default=0
        ) or 0)
        if not escalation_user_id:
            raise UserError(
                'No escalation user is configured. '
                'Go to 3PL connector settings and set the Exception Escalation User.'
            )
        for picking in self:
            picking.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=escalation_user_id,
                note=f'MF Exception escalated: {picking.name}',
            )
            picking.message_post(body=f'Escalated by {self.env.user.name}.')

    def _message_log_batch(self, template):
        """Post a chatter message to each picking using a template with %(user)s."""
        user = self.env.user.name
        for picking in self:
            picking.message_post(body=template % {'user': user})
