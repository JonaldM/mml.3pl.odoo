import abc


class AbstractTransport(abc.ABC):
    def __init__(self, connector):
        self.connector = connector

    @abc.abstractmethod
    def send(self, payload, content_type='xml', endpoint=None):
        """Send an outbound payload. Returns dict: {success, note, error_type}."""

    def poll(self, path=None):
        """Poll for inbound messages. Returns list of raw payloads."""
        return []

    def _success(self, note=None):
        return {'success': True, 'note': note}

    def _retriable_error(self, msg):
        return {'success': False, 'error_type': 'retriable', 'error': msg}

    def _validation_error(self, msg):
        return {'success': False, 'error_type': 'validation', 'error': msg}
