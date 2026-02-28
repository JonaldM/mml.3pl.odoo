# addons/stock_3pl_core/transport/http_post.py
import requests
import logging
from urllib.parse import quote

from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport

_logger = logging.getLogger(__name__)


class HttpPostTransport(AbstractTransport):
    """HTTP POST transport adapter for Mainfreight's CrossFire submission endpoint.

    URL format: https://secure.mainfreight.co.nz/crossfire/submit.aspx?TransportName={UniqueID}
    This is a push-only transport — no inbound polling.
    """

    def send(self, payload, content_type='xml', filename=None, endpoint=None):
        url = self.connector.http_post_url
        transport_name = self.connector.http_transport_name
        full_url = f'{url}?TransportName={quote(transport_name, safe="")}'
        data = payload if isinstance(payload, bytes) else payload.encode('utf-8')
        try:
            resp = requests.post(
                full_url,
                data=data,
                headers={'Content-Type': 'application/xml'},
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return self._success()
            elif resp.status_code == 409:
                return self._success(note='already_exists')
            elif 400 <= resp.status_code < 500 and resp.status_code != 429:
                _logger.error('HTTP POST validation failure %s: %s', resp.status_code, resp.text[:200])
                return self._validation_error(f'HTTP {resp.status_code}: {resp.text[:500]}')
            _logger.warning('HTTP POST server error %s: %s', resp.status_code, resp.text[:200])
            return self._retriable_error(f'HTTP {resp.status_code}: {resp.text[:500]}')
        except requests.Timeout:
            _logger.warning('HTTP POST timed out: %s', full_url)
            return self._retriable_error('Request timed out')
        except requests.ConnectionError as e:
            _logger.warning('HTTP POST connection error: %s', e)
            return self._retriable_error(f'Connection error: {e}')
        except requests.exceptions.RequestException as e:
            _logger.error('HTTP POST transport error: %s', e)
            return self._retriable_error(f'Transport error: {e}')

    def poll(self, path=None):
        """HTTP POST is push-only — no inbound polling supported."""
        return []
