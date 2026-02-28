# addons/stock_3pl_mainfreight/document/so_acknowledgement.py
import csv
import io
import logging
from odoo.exceptions import ValidationError
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

_logger = logging.getLogger(__name__)


def _validate_ref(value, field_name, max_len=256):
    """Validate a reference string from external data before using in ORM search."""
    if not value or not isinstance(value, str):
        raise ValidationError(f'Invalid {field_name}: empty or non-string value received from 3PL')
    if len(value) > max_len:
        raise ValidationError(f'Invalid {field_name}: value too long ({len(value)} chars, max {max_len})')
    return value.strip()


class SOAcknowledgementDocument(AbstractDocument):
    """Parser for MF SO Acknowledgement (ACKH/ACKL) — CSV inbound.

    V3 scope addition: maps ACKH/ACKL CSV → mf_received status on stock.picking.
    MF sends this after the WMS accepts the order (status: ENTERED).
    """
    document_type = 'so_acknowledgement'
    format = 'csv'

    def build_outbound(self, record):
        raise NotImplementedError('SO Acknowledgement is inbound-only')

    def parse_inbound(self, payload):
        """Parse MF SO Acknowledgement CSV. Returns list of dicts per ACK line."""
        reader = csv.DictReader(io.StringIO(payload))
        rows = []
        for row in reader:
            rows.append({
                'client_order_number': row.get('ClientOrderNumber', '').strip(),
                'order_status': row.get('OrderStatus', '').strip(),
                'warehouse_id': row.get('WarehouseID', '').strip(),
                'received_date': row.get('ReceivedDate', '').strip(),
            })
        return rows

    def apply_inbound(self, message):
        """Apply SO Acknowledgements to Odoo: set picking status to mf_received."""
        rows = self.parse_inbound(message.payload_csv or message.payload_xml or '')
        for ack in rows:
            order_ref = ack.get('client_order_number')
            if not order_ref:
                continue
            try:
                order_ref = _validate_ref(order_ref, 'client order number')
            except ValidationError as exc:
                _logger.warning('MF ACK: skipping row — %s', exc)
                continue
            order = self.env['sale.order'].search([('name', '=', order_ref)], limit=1)
            if not order:
                _logger.warning('MF ACK: sale order not found for reference %s', order_ref)
                continue
            picking = order.picking_ids.filtered(
                lambda p: p.state not in ('done', 'cancel')
            )[:1]
            if picking and hasattr(picking, 'x_mf_status'):
                picking.x_mf_status = 'mf_received'
            _logger.info('MF ACK: order %s acknowledged by MF (status: %s)',
                         order_ref, ack.get('order_status'))
