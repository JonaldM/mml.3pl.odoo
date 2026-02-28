from odoo import models, fields


class ThreePlMessage(models.Model):
    _name = '3pl.message'
    _description = '3PL Message Queue'
    _order = 'create_date desc'

    connector_id = fields.Many2one('3pl.connector', required=True, ondelete='cascade')
    direction = fields.Selection([('outbound', 'Outbound'), ('inbound', 'Inbound')], required=True)
    document_type = fields.Char()
    state = fields.Selection([
        ('draft', 'Draft'), ('queued', 'Queued'), ('sending', 'Sending'),
        ('sent', 'Sent'), ('acknowledged', 'Acknowledged'),
        ('received', 'Received'), ('processing', 'Processing'),
        ('applied', 'Applied'), ('done', 'Done'), ('dead', 'Dead'),
    ], default='draft', index=True)
    payload_xml = fields.Text('XML Payload')
    payload_csv = fields.Text('CSV Payload')
    payload_json = fields.Text('JSON Payload')
    last_error = fields.Text('Last Error')
    retry_count = fields.Integer(default=0)
    forwarder_ref = fields.Char('Forwarder Reference')
    ref_model = fields.Char('Source Model')
    ref_id = fields.Integer('Source Record ID')
    idempotency_key = fields.Char(index=True)
    source_hash = fields.Char(index=True)
    report_date = fields.Date('Report Date')
    action = fields.Selection([('create','Create'),('update','Update'),('delete','Delete')], default='create')
    sent_at = fields.Datetime('Sent At', readonly=True)
    acked_at = fields.Datetime('Acknowledged At', readonly=True)
