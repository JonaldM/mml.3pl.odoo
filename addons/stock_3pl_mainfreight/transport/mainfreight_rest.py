# addons/stock_3pl_mainfreight/transport/mainfreight_rest.py
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
        """Poll the MF StockOnHand endpoint.

        Returns a list containing the response body on success, or [] on failure.
        Delegates to RestTransport.poll() to keep auth and retry logic in one place.
        """
        return self.poll(path=f'{self._get_base_url()}/StockOnHand')
