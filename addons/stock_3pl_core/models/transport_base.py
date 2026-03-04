import abc
import ipaddress
from urllib.parse import urlparse


class AbstractTransport(abc.ABC):
    def __init__(self, connector):
        self.connector = connector

    def _validate_url(self, url: str) -> None:
        """Raise ValueError if URL is not a safe HTTPS endpoint."""
        parsed = urlparse(url)
        if parsed.scheme != 'https':
            raise ValueError(f'Transport: URL must use HTTPS, got: {parsed.scheme}://')
        # Block RFC-1918 and link-local ranges
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            if ip.is_private or ip.is_link_local or ip.is_loopback:
                raise ValueError(
                    f'Transport: URL resolves to private/internal address: {parsed.hostname}'
                )
        except ValueError as e:
            if 'Transport:' in str(e):
                raise
            # hostname is a domain name, not an IP — allow it
            pass

    @abc.abstractmethod
    def send(self, payload, content_type='xml', filename=None, endpoint=None):
        """Send an outbound payload. Returns dict: {success, note, error_type}.

        Args:
            payload: str or bytes — the document to send
            content_type: 'xml', 'json', or 'csv'
            filename: required for SFTP transport; ignored by REST/HTTP POST
            endpoint: override the default URL/path from the connector config
        """

    def poll(self, path=None):
        """Poll for inbound messages. Returns list of raw payloads."""
        return []

    def get_tracking_status(self, connote):
        """Poll tracking API for a given connote number.

        Returns a dict with keys: status (str|None), pod_url (str|None),
        signed_by (str|None), delivered_at (datetime|None).
        Default implementation returns {} — subclasses override for real tracking.
        """
        return {}

    def _success(self, note=None):
        return {'success': True, 'note': note}

    def _retriable_error(self, msg):
        return {'success': False, 'error_type': 'retriable', 'error': msg}

    def _validation_error(self, msg):
        return {'success': False, 'error_type': 'validation', 'error': msg}
