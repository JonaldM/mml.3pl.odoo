# addons/stock_3pl_core/models/message.py
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

MAX_RETRIES = 3

DIRECTION = [('outbound', 'Outbound'), ('inbound', 'Inbound')]

DOCUMENT_TYPE = [
    ('product_spec', 'Product Specification'),
    ('sales_order', 'Sales Order'),
    ('inward_order', 'Inward Order'),
    ('so_confirmation', 'SO Confirmation'),
    ('inward_confirmation', 'Inward Confirmation'),
    ('inventory_report', 'Inventory Report'),
    ('inventory_adjustment', 'Inventory Adjustment'),
]

ACTION = [
    ('create', 'Create'),
    ('update', 'Update'),
    ('delete', 'Delete'),
]

ALL_STATES = [
    ('draft', 'Draft'),
    ('queued', 'Queued'),
    ('sending', 'Sending'),
    ('sent', 'Sent'),
    ('acknowledged', 'Acknowledged'),
    ('received', 'Received'),
    ('processing', 'Processing'),
    ('applied', 'Applied'),
    ('done', 'Done'),
    ('dead', 'Dead'),
]


class ThreePlMessage(models.Model):
    _name = '3pl.message'
    _description = '3PL Message Queue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    connector_id = fields.Many2one('3pl.connector', required=True, ondelete='cascade')
    direction = fields.Selection(DIRECTION, required=True)
    document_type = fields.Selection(DOCUMENT_TYPE, required=True)
    action = fields.Selection(ACTION, default='create')
    state = fields.Selection(ALL_STATES, default='draft', index=True)

    # Payloads
    payload_xml = fields.Text('XML Payload')
    payload_json = fields.Text('JSON Payload')
    payload_csv = fields.Text('CSV Payload')

    # Source record
    ref_model = fields.Char('Source Model')
    ref_id = fields.Integer('Source Record ID')

    # 3PL references
    forwarder_ref = fields.Char('Forwarder Reference')

    # Idempotency / deduplication
    idempotency_key = fields.Char(index=True)
    source_hash = fields.Char(index=True)
    report_date = fields.Date('Report Date')

    # Retry
    retry_count = fields.Integer(default=0)
    last_error = fields.Text('Last Error')

    # Timestamps
    sent_at = fields.Datetime('Sent At', readonly=True)
    acked_at = fields.Datetime('Acknowledged At', readonly=True)

    # --- Outbound state transitions ---

    def action_queue(self):
        self.write({'state': 'queued'})

    def action_sending(self):
        self.write({'state': 'sending'})

    def action_sent(self):
        self.write({'state': 'sent', 'sent_at': fields.Datetime.now()})

    def action_acknowledged(self):
        self.write({'state': 'acknowledged', 'acked_at': fields.Datetime.now()})

    def action_fail(self, error_msg):
        """Retry if under MAX_RETRIES, otherwise dead-letter."""
        for msg in self:
            if msg.retry_count >= MAX_RETRIES - 1:
                msg._dead_letter(error_msg)
            else:
                msg.write({
                    'state': 'queued',
                    'retry_count': msg.retry_count + 1,
                    'last_error': error_msg,
                })

    def action_validation_fail(self, error_msg):
        """Validation failures go straight to dead — retrying won't fix a bad payload."""
        for msg in self:
            msg._dead_letter(error_msg)

    def action_requeue(self):
        """Manual requeue from dead letter."""
        self.write({'state': 'queued', 'retry_count': 0, 'last_error': False})

    def _dead_letter(self, error_msg):
        self.write({'state': 'dead', 'last_error': error_msg})
        if self.connector_id.notify_user_id:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=self.connector_id.notify_user_id.id,
                note=f'3PL message dead-lettered: {self.document_type} — {error_msg}',
            )
        _logger.error('3PL message %s dead-lettered: %s', self.id, error_msg)

    # --- Inbound state transitions ---

    def action_processing(self):
        self.write({'state': 'processing'})

    def action_applied(self):
        self.write({'state': 'applied'})

    def action_done(self):
        self.write({'state': 'done'})
