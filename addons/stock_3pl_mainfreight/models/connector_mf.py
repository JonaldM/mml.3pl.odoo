from odoo import models, fields

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

    # MF REST API secrets (separate per API type per MF spec)
    mf_warehousing_secret = fields.Char('Warehousing API Secret')
    mf_label_secret = fields.Char('Label API Secret')
    mf_rating_secret = fields.Char('Rating API Secret')
    mf_tracking_secret = fields.Char('Tracking API Secret')

    def action_test_connection(self):
        """Test REST API connectivity to MF."""
        self.ensure_one()
        transport = self.get_transport()
        result = transport.send('<ping/>', endpoint=self._mf_endpoint('order'))
        if result['success'] or result.get('note') == 'already_exists':
            return self._notify('Connection to Mainfreight successful.')
        return self._notify(f"Connection failed: {result.get('error')}", error=True)

    def _mf_endpoint(self, resource):
        env = self.environment or 'test'
        base = MF_ENVIRONMENTS[env]['rest_api']
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
