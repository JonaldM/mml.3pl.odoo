# addons/stock_3pl_core/models/document_base.py
import abc
import hashlib


class AbstractDocument(abc.ABC):
    """Base class for all 3PL document builders and parsers.

    Subclasses implement build_outbound() for outbound document generation
    or parse_inbound() + apply_inbound() for inbound document processing.

    Class attributes:
        document_type: str matching a value in 3pl.message.document_type selection
        format: 'xml', 'csv', or 'json'
    """

    document_type = None  # Must be set by subclass
    format = 'xml'  # 'xml', 'csv', or 'json'

    def __init__(self, connector, env):
        self.connector = connector
        self.env = env

    @abc.abstractmethod
    def build_outbound(self, record):
        """Build a payload string from an Odoo record. Returns str."""

    def parse_inbound(self, payload):
        """Parse raw inbound payload and return structured dict."""
        raise NotImplementedError

    def apply_inbound(self, message):
        """Apply a parsed inbound message to Odoo records."""
        raise NotImplementedError

    def get_filename(self, record):
        """Return a unique filename for SFTP/file transfer."""
        raise NotImplementedError

    @staticmethod
    def hash_payload(payload):
        """Compute SHA-256 hex digest of a payload string."""
        if isinstance(payload, bytes):
            return hashlib.sha256(payload).hexdigest()
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    @staticmethod
    def make_idempotency_key(connector_id, document_type, odoo_ref):
        """Compute a deterministic idempotency key for an outbound message."""
        raw = f'{connector_id}:{document_type}:{odoo_ref}'
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def truncate(self, value, max_len):
        """Truncate a value to MF field max length. Returns '' for falsy values."""
        if not value:
            return ''
        return str(value)[:max_len]


class FreightForwarderMixin:
    """Mixin for document builders that support multiple freight forwarders.

    Register field mappings per forwarder via the FIELD_MAP class attribute:
        FIELD_MAP = {'mainfreight': {'odoo_field': 'mf_field', ...}}

    Use get_field_map(forwarder) to retrieve the mapping for the active forwarder.
    """

    FIELD_MAP = {}

    def get_field_map(self, forwarder):
        """Return the field mapping dict for the given forwarder, or {} if not registered."""
        return self.FIELD_MAP.get(forwarder, {})
