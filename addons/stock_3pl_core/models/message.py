# addons/stock_3pl_core/models/message.py
from odoo import models, fields, api
import hashlib
import logging

_logger = logging.getLogger(__name__)

MAX_RETRIES = 3

DIRECTION = [('outbound', 'Outbound'), ('inbound', 'Inbound')]

DOCUMENT_TYPE = [
    ('product_spec', 'Product Specification'),
    ('sales_order', 'Sales Order'),
    ('so_acknowledgement', 'SO Acknowledgement'),
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

    # PostgreSQL treats each NULL as distinct, so these constraints only enforce
    # uniqueness for non-null keys. Messages without a key/hash are intentionally
    # exempt — they are not subject to deduplication.
    _sql_constraints = [
        (
            'unique_idempotency_key',
            'UNIQUE(connector_id, idempotency_key)',
            'An outbound message with this idempotency key already exists for this connector.',
        ),
        (
            'unique_source_hash',
            'UNIQUE(connector_id, source_hash)',
            'An inbound message with this payload hash already exists for this connector.',
        ),
    ]

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
            if msg.retry_count + 1 >= MAX_RETRIES:
                msg._dead_letter(error_msg)
            else:
                _logger.warning(
                    '3PL message %s retry %s/%s: %s',
                    msg.id, msg.retry_count + 1, MAX_RETRIES, error_msg,
                )
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
        self.ensure_one()
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

    # --- Idempotency helpers ---

    def is_stale(self):
        """Return True if this inventory report is not newer than the last applied SOH date.

        Same-day reports are also considered stale — the snapshot has already been processed.
        """
        self.ensure_one()
        if not self.connector_id.last_soh_applied_at or not self.report_date:
            return False
        last = self.connector_id.last_soh_applied_at.date()
        return self.report_date <= last

    # ---- Cron-driven queue processor ----

    @api.model
    def _process_outbound_queue(self):
        """Called by cron. Processes all queued outbound messages.

        Also recovers messages orphaned in 'sending' state from a prior crashed run
        by treating them as if they had just been queued.

        For each message:
        - Re-checks state before acting (handles concurrent cron runs)
        - Marks it as 'sending'
        - Calls the connector's transport adapter
        - On success: marks sent
        - On validation failure: dead-letters immediately (no retry)
        - On retriable failure: increments retry_count or dead-letters at MAX_RETRIES
        """
        candidates = self.search([
            ('direction', '=', 'outbound'),
            ('state', 'in', ['queued', 'sending']),
        ])
        for msg in candidates:
            # Re-check state to guard against concurrent cron runs picking up the
            # same record. A fresh read ensures we see the committed DB state.
            msg.invalidate_recordset()
            if msg.state not in ('queued', 'sending'):
                continue
            try:
                msg.action_sending()
                transport = msg.connector_id.get_transport()
                payload = msg.payload_xml or msg.payload_json or msg.payload_csv
                result = transport.send(
                    payload,
                    content_type=msg._detect_content_type(),
                )
                if result['success']:
                    msg.action_sent()
                elif result.get('error_type') == 'validation':
                    msg.action_validation_fail(result.get('error', 'Validation error'))
                else:
                    msg.action_fail(result.get('error', 'Unknown error'))
            except Exception as e:
                _logger.error('Error processing 3PL message %s: %s', msg.id, e)
                try:
                    msg.action_fail(str(e))
                except Exception as inner_e:
                    _logger.error(
                        'Failed to dead-letter message %s after error: %s',
                        msg.id, inner_e,
                    )

    def _detect_content_type(self):
        """Detect payload content type from which payload field is populated.

        Raises ValidationError if no payload is set — sending an empty payload
        would produce a confusing error at the 3PL endpoint.
        """
        self.ensure_one()
        if self.payload_xml:
            return 'xml'
        if self.payload_json:
            return 'json'
        if self.payload_csv:
            return 'csv'
        from odoo.exceptions import ValidationError
        raise ValidationError(
            f'3PL message {self.id} has no payload to send (payload_xml, '
            f'payload_json, and payload_csv are all empty).'
        )

    # ---- Cron-driven inbound poller ----

    @api.model
    def _poll_inbound(self):
        """Called by cron. Poll all active connectors for inbound messages."""
        connectors = self.env['3pl.connector'].search([('active', '=', True)])
        for connector in connectors:
            try:
                transport = connector.get_transport()
                payloads = transport.poll()
                for raw in payloads:
                    source_hash = hashlib.sha256(raw.encode()).hexdigest()
                    existing = self.search([
                        ('connector_id', '=', connector.id),
                        ('source_hash', '=', source_hash),
                    ], limit=1)
                    if existing:
                        continue  # Deduplicate
                    self.create({
                        'connector_id': connector.id,
                        'direction': 'inbound',
                        'document_type': self._detect_inbound_type(raw),
                        'payload_xml': raw if raw.strip().startswith('<') else False,
                        'payload_csv': raw if not raw.strip().startswith('<') else False,
                        'source_hash': source_hash,
                        'state': 'received',
                    })
            except Exception as e:
                _logger.error('Inbound poll failed for %s: %s', connector.name, e)

    @staticmethod
    def _detect_inbound_type(raw):
        """Detect document type from inbound payload content."""
        raw = raw.strip()
        if '<OrderConfirmation' in raw or '<SCH' in raw:
            return 'so_confirmation'
        if '<InwardConfirmation' in raw:
            return 'inward_confirmation'
        return 'inventory_report'  # Default: assume CSV = SOH report
