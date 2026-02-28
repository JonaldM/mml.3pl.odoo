# addons/stock_3pl_mainfreight/transport/freightways_rest.py
"""Freightways / Castle Parcels REST transport adapter."""
import logging
import datetime
import requests
from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport

_logger = logging.getLogger(__name__)

FREIGHTWAYS_ENVIRONMENTS = {
    'test': {
        'rest_api': 'https://api-sandbox.freightways.co.nz/api/v1',
    },
    'production': {
        'rest_api': 'https://api.freightways.co.nz/api/v1',
    },
}

FW_TRACKING_STATUS_MAP = {
    'Booked': 'mf_received',
    'InTransit': 'mf_in_transit',
    'OutForDelivery': 'mf_out_for_delivery',
    'Delivered': 'mf_delivered',
    'Exception': 'mf_exception',
}


class FreightwaysRestTransport(RestTransport):
    """Freightways-specific REST transport — handles FW API Key auth and endpoint routing."""

    def _get_auth_secret(self):
        """Return the Freightways API key (used for X-API-Key header in tracking calls)."""
        return self.connector.get_credential('fw_api_key') or ''

    def _get_base_url(self):
        env = FREIGHTWAYS_ENVIRONMENTS.get(
            self.connector.environment, FREIGHTWAYS_ENVIRONMENTS['test']
        )
        return env['rest_api']

    def get_tracking_status(self, connote):
        """Poll the Freightways Tracking API for a given connote number.

        Freightways uses an X-API-Key header rather than Authorization: Bearer.
        Calls GET {base_url}/Tracking/{connote} directly (does NOT use self.send()).

        Returns a dict with keys: status, pod_url, signed_by, delivered_at.
        Returns {} on any error or if the FW status string is not recognised.
        """
        api_key = self.connector.get_credential('fw_api_key') or ''
        url = f'{self._get_base_url()}/Tracking/{connote}'
        try:
            response = requests.get(
                url,
                headers={'X-API-Key': api_key},
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            _logger.error(
                'FreightwaysRestTransport.get_tracking_status: HTTP request failed for %s: %s',
                connote, exc,
            )
            return {}

        try:
            data = response.json()
        except Exception as exc:
            _logger.warning(
                'FreightwaysRestTransport.get_tracking_status: could not parse JSON for %s: %s',
                connote, exc,
            )
            return {}

        fw_status = data.get('Status')
        mapped_status = FW_TRACKING_STATUS_MAP.get(fw_status)
        if mapped_status is None:
            _logger.warning(
                'FreightwaysRestTransport.get_tracking_status: unknown FW status %r for connote %s',
                fw_status, connote,
            )
            return {}

        delivered_at = None
        raw_delivered = data.get('DeliveredDateTime')
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
