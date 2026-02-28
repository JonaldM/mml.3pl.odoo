# addons/stock_3pl_core/transport/rest_api.py
import requests
import logging
from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport

_logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    'xml': 'application/xml',
    'json': 'application/json',
    'csv': 'text/csv',
}


class RestTransport(AbstractTransport):

    def send(self, payload, content_type='xml', endpoint=None):
        url = endpoint or self.connector.api_url
        headers = {
            'Content-Type': CONTENT_TYPES.get(content_type, 'application/xml'),
            'Authorization': f'Bearer {self.connector.api_secret}',
        }
        try:
            resp = requests.post(url, data=payload.encode('utf-8'), headers=headers, timeout=30)
        except requests.Timeout:
            return self._retriable_error('Request timed out')
        except requests.ConnectionError as e:
            return self._retriable_error(f'Connection error: {e}')

        if resp.status_code in (200, 201):
            return self._success()
        elif resp.status_code == 409:
            return self._success(note='already_exists')
        elif resp.status_code == 422:
            return self._validation_error(resp.text)
        else:
            return self._retriable_error(f'HTTP {resp.status_code}: {resp.text}')

    def poll(self, path=None):
        url = path or self.connector.api_url
        headers = {'Authorization': f'Bearer {self.connector.api_secret}'}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return [resp.text]
        except Exception as e:
            _logger.warning('REST poll failed: %s', e)
        return []
