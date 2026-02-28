from odoo import models, fields, api
from odoo.addons.stock_3pl_core.utils.credential_store import encrypt_credential

MF_ENVIRONMENTS = {
    'test': {
        'rest_api': 'https://warehouseapi-test.mainfreight.com/api/v1.1',
        'sftp': 'xftp.mainfreight.com',
        'http_post': 'https://securetest.mainfreight.com/crossfire/submit.aspx',
    },
    'production': {
        'rest_api': 'https://warehouseapi.mainfreight.com/api/v1.1',
        'sftp': 'xftp.mainfreight.com',
        'http_post': 'https://secure.mainfreight.co.nz/crossfire/submit.aspx',
    },
}


class ThreePlConnectorMF(models.Model):
    _inherit = '3pl.connector'

    _MF_CREDENTIAL_FIELDS = (
        'mf_warehousing_secret',
        'mf_label_secret',
        'mf_rating_secret',
        'mf_tracking_secret',
    )

    # MF REST API secrets (separate per API type per MF spec)
    mf_warehousing_secret = fields.Char('Warehousing API Secret', password=True, groups='stock.group_stock_manager')
    mf_label_secret = fields.Char('Label API Secret', password=True, groups='stock.group_stock_manager')
    mf_rating_secret = fields.Char('Rating API Secret', password=True, groups='stock.group_stock_manager')
    mf_tracking_secret = fields.Char('Tracking API Secret', password=True, groups='stock.group_stock_manager')

    @api.model
    def create(self, vals):
        for field in self._MF_CREDENTIAL_FIELDS:
            if field in vals and vals[field]:
                vals[field] = encrypt_credential(self.env, vals[field])
        return super().create(vals)

    def write(self, vals):
        for field in self._MF_CREDENTIAL_FIELDS:
            if field in vals and vals[field]:
                vals[field] = encrypt_credential(self.env, vals[field])
        return super().write(vals)  # super() calls ThreePlConnector.write() which handles _CREDENTIAL_FIELDS

    def action_test_connection(self):
        """Test REST API connectivity to MF."""
        self.ensure_one()
        transport = self.get_transport()
        result = transport.send('<ping/>', endpoint=self._mf_endpoint('order'))
        if result.get('success') or result.get('note') == 'already_exists':
            return self._notify('Connection to Mainfreight successful.')
        return self._notify(f"Connection failed: {result.get('error')}", error=True)

    def _mf_endpoint(self, resource):
        env = self.environment or 'test'
        base = MF_ENVIRONMENTS.get(env, MF_ENVIRONMENTS['test'])['rest_api']
        endpoints = {
            'order': f'{base}/Order',
            'inward': f'{base}/Inward',
            'soh': f'{base}/StockOnHand',
        }
        return endpoints.get(resource, base)

    def _notify(self, message, error=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'type': 'danger' if error else 'success',
                'sticky': False,
            },
        }
