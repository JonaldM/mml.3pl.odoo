# addons/stock_3pl_core/transport/http_post.py
import requests
import logging

from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport

_logger = logging.getLogger(__name__)


class HttpPostTransport(AbstractTransport):
    """HTTP POST transport adapter for Mainfreight's CrossFire submission endpoint.

    URL format: https://secure.mainfreight.co.nz/crossfire/submit.aspx?TransportName={UniqueID}
    This is a push-only transport — no inbound polling.
    """

    def send(self, payload, content_type='xml', endpoint=None):
        url = self.connector.http_post_url
        transport_name = self.connector.http_transport_name
        full_url = f'{url}?TransportName={transport_name}'
        data = payload if isinstance(payload, bytes) else payload.encode('utf-8')
        try:
            resp = requests.post(
                full_url,
                data=data,
                headers={'Content-Type': 'multipart/form-data'},
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return self._success()
            return self._retriable_error(f'HTTP {resp.status_code}: {resp.text}')
        except requests.Timeout:
            return self._retriable_error('Request timed out')
        except requests.ConnectionError as e:
            return self._retriable_error(f'Connection error: {e}')
        except requests.exceptions.RequestException as e:
            return self._retriable_error(f'Transport error: {e}')

    def poll(self, path=None):
        """HTTP POST is push-only — no inbound polling supported."""
        return []
