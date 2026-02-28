# addons/stock_3pl_mainfreight/transport/mainfreight_rest.py
import logging
import datetime
import requests
from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport

_logger = logging.getLogger(__name__)

MF_ENDPOINTS = {
    'test': 'https://warehouseapi-test.mainfreight.com/api/v1.1',
    'production': 'https://warehouseapi.mainfreight.com/api/v1.1',
}

MF_TRACKING_ENDPOINTS = {
    'test': 'https://trackingapi-test.mainfreight.com/api/v1',
    'production': 'https://trackingapi.mainfreight.com/api/v1',
}

MF_TRACKING_STATUS_MAP = {
    'RECEIVED': 'mf_received',
    'DISPATCHED': 'mf_dispatched',
    'IN_TRANSIT': 'mf_in_transit',
    'OUT_FOR_DELIVERY': 'mf_out_for_delivery',
    'DELIVERED': 'mf_delivered',
    'EXCEPTION': 'mf_exception',
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

    def _get_tracking_base_url(self):
        return MF_TRACKING_ENDPOINTS.get(self.connector.environment, MF_TRACKING_ENDPOINTS['test'])

    def get_tracking_status(self, connote):
        """Poll the MF Tracking API for a given connote number.

        Returns a dict with keys: status, pod_url, signed_by, delivered_at.
        Returns {} on any error or if the MF status string is not recognised.
        """
        secret = self.connector.get_credential('mf_tracking_secret') or ''
        url = f'{self._get_tracking_base_url()}/Tracking/{connote}'
        try:
            response = requests.get(
                url,
                headers={'Authorization': f'Bearer {secret}'},
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            _logger.error('get_tracking_status: HTTP request failed for %s: %s', connote, exc)
            return {}

        try:
            data = response.json()
        except Exception as exc:
            _logger.warning('get_tracking_status: could not parse JSON response for %s: %s', connote, exc)
            return {}

        mf_status = data.get('Status')
        mapped_status = MF_TRACKING_STATUS_MAP.get(mf_status)
        if mapped_status is None:
            _logger.warning(
                'get_tracking_status: unknown MF status %r for connote %s', mf_status, connote
            )
            return {}

        delivered_at = None
        raw_delivered = data.get('DeliveredAt')
        if raw_delivered:
            try:
                delivered_at = datetime.datetime.fromisoformat(raw_delivered)
            except (ValueError, TypeError):
                delivered_at = None

        return {
            'status': mapped_status,
            'pod_url': data.get('PODUrl') or None,
            'signed_by': data.get('SignedBy') or None,
            'delivered_at': delivered_at,
        }
