# addons/stock_3pl_mainfreight/transport/mainfreight_rest.py
import requests
import logging
from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport

_logger = logging.getLogger(__name__)

MF_ENDPOINTS = {
    'test': 'https://warehouseapi-test.mainfreight.com/api/v1.1',
    'production': 'https://warehouseapi.mainfreight.com/api/v1.1',
}


class MainfreightRestTransport(RestTransport):
    """MF-specific REST transport — handles MF auth and endpoint routing."""

    def _get_base_url(self):
        return MF_ENDPOINTS.get(self.connector.environment, MF_ENDPOINTS['test'])

    def send_order(self, payload):
        return self.send(payload, content_type='xml',
                         endpoint=f'{self._get_base_url()}/Order')

    def send_inward(self, payload):
        return self.send(payload, content_type='xml',
                         endpoint=f'{self._get_base_url()}/Inward')

    def get_stock_on_hand(self):
        url = f'{self._get_base_url()}/StockOnHand'
        headers = {'Authorization': f'Bearer {self.connector.api_secret}'}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            _logger.warning('MF SOH poll failed: %s', e)
        return None
