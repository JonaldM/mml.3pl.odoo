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

    def _get_auth_secret(self):
        """Return the credential field name used for Bearer token auth.

        Subclasses override this to use a transport-specific credential field.
        e.g. MainfreightRestTransport returns 'mf_warehousing_secret'.
        Returns an empty string (never None) so Bearer token headers are always safe.
        """
        return self.connector.get_credential('api_secret') or ''

    def send(self, payload, content_type='xml', filename=None, endpoint=None):
        url = endpoint or self.connector.api_url
        headers = {
            'Content-Type': CONTENT_TYPES.get(content_type, 'application/xml'),
            'Authorization': f'Bearer {self._get_auth_secret()}',
        }
        try:
            data = payload if isinstance(payload, bytes) else payload.encode('utf-8')
            resp = requests.post(url, data=data, headers=headers, timeout=30)
        except requests.Timeout:
            return self._retriable_error('Request timed out')
        except requests.ConnectionError as e:
            return self._retriable_error(f'Connection error: {e}')
        except requests.exceptions.RequestException as e:
            return self._retriable_error(f'Transport error: {str(e).split(chr(10))[0][:200]}')

        if resp.status_code in (200, 201):
            return self._success()
        elif resp.status_code == 409:
            return self._success(note='already_exists')
        elif resp.status_code == 422:
            error_body = resp.text[:500].replace('\n', ' ').replace('\r', '') if resp.text else ''
            return self._validation_error(error_body)
        else:
            error_body = resp.text[:500].replace('\n', ' ').replace('\r', '') if resp.text else ''
            return self._retriable_error(f'HTTP {resp.status_code}: {error_body}')

    def poll(self, path=None):
        """Poll the REST endpoint for inbound payloads.

        Returns a single-element list containing the full response body on HTTP 200.
        REST endpoints typically return a single payload (one document per response),
        unlike SFTP which returns one element per file.
        Returns [] on any error or non-200 response.
        """
        url = path or self.connector.api_url
        headers = {'Authorization': f'Bearer {self._get_auth_secret()}'}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return [resp.text]
        except requests.exceptions.RequestException as e:
            _logger.warning('REST poll failed: %s', e)
        return []
