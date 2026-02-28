from odoo import models, fields
from odoo.addons.stock_3pl_core.utils.credential_store import encrypt_credential, decrypt_credential

WAREHOUSE_PARTNER_SELECTION = [
    ('mainfreight', 'Mainfreight'),
]

TRANSPORT_SELECTION = [
    ('rest_api', 'REST API'),
    ('sftp', 'SFTP'),
    ('http_post', 'HTTP POST'),
]

ENVIRONMENT_SELECTION = [
    ('test', 'Test'),
    ('production', 'Production'),
]


class ThreePlConnector(models.Model):
    _name = '3pl.connector'
    _description = '3PL Warehouse Connector'

    _CREDENTIAL_FIELDS = ('api_secret', 'sftp_password')

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    warehouse_id = fields.Many2one('stock.warehouse', required=True, ondelete='restrict')
    warehouse_partner = fields.Selection(WAREHOUSE_PARTNER_SELECTION, string='Warehouse Partner', required=True)
    transport = fields.Selection(TRANSPORT_SELECTION, required=True)
    environment = fields.Selection(ENVIRONMENT_SELECTION, required=True, default='test')
    region = fields.Char(help='e.g. NZ, AU, US — used for international routing')

    # 3PL identity
    customer_id = fields.Char('Customer ID', help='Unique ID assigned by the 3PL')
    warehouse_code = fields.Char('Warehouse Code', help='3PL warehouse identifier e.g. 99')

    # REST API credentials
    api_url = fields.Char('API URL')
    api_secret = fields.Char('API Secret', password=True, groups='stock.group_stock_manager')

    # SFTP credentials
    sftp_host = fields.Char('SFTP Host')
    sftp_port = fields.Integer('SFTP Port', default=22)
    sftp_username = fields.Char('SFTP Username')
    sftp_password = fields.Char('SFTP Password', password=True, groups='stock.group_stock_manager')
    sftp_inbound_path = fields.Char('SFTP Inbound Path', default='/in')
    sftp_outbound_path = fields.Char('SFTP Outbound Path', default='/out')
    sftp_host_key = fields.Text(
        'SFTP Host Key',
        help='Paste the server public key in known_hosts format: '
             '"hostname key-type base64-key" (e.g. from ssh-keyscan). '
             'When set, strict host key verification is enforced (RejectPolicy). '
             'Leave blank to allow new keys with a logged warning.',
    )

    # HTTP POST
    http_post_url = fields.Char('HTTP POST URL')
    http_transport_name = fields.Char('Transport Name (UniqueID)')

    # Alerting
    notify_user_id = fields.Many2one('res.users', 'Notify User on Dead Letter')

    # SOH guard
    last_soh_applied_at = fields.Datetime('Last SOH Applied At', readonly=True)
    x_mf_use_api_soh = fields.Boolean(
        'Use MF SOH API for Routing',
        default=False,
        help='When enabled, the routing engine cross-checks Odoo stock against the MF SOH API. '
             'MF figures are used if drift exceeds the threshold.',
    )

    message_ids = fields.One2many('3pl.message', 'connector_id', 'Messages')
    message_count = fields.Integer(compute='_compute_message_count')

    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    def write(self, vals):
        self._encrypt_credential_vals(vals)
        return super().write(vals)

    def _encrypt_credential_vals(self, vals):
        """Encrypt any credential fields present in vals, in place."""
        for field in self._CREDENTIAL_FIELDS:
            if field in vals and vals[field]:
                vals[field] = encrypt_credential(self.env, vals[field])

    def get_credential(self, field_name):
        """Return the decrypted value of a credential field.

        Usage: connector.get_credential('api_secret')
        instead of: connector.api_secret
        """
        self.ensure_one()
        raw = getattr(self, field_name, None)
        if not raw:
            return raw
        return decrypt_credential(self.env, raw)

    def get_transport(self):
        """Return the appropriate transport adapter for this connector."""
        self.ensure_one()
        if self.transport == 'rest_api':
            from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport
            return RestTransport(self)
        elif self.transport == 'sftp':
            from odoo.addons.stock_3pl_core.transport.sftp import SftpTransport
            return SftpTransport(self)
        elif self.transport == 'http_post':
            from odoo.addons.stock_3pl_core.transport.http_post import HttpPostTransport
            return HttpPostTransport(self)
        raise NotImplementedError(f'No transport implemented for: {self.transport}')
