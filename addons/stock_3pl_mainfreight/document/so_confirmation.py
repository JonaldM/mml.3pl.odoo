# addons/stock_3pl_mainfreight/document/so_confirmation.py
import logging
from datetime import datetime
from lxml import etree
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

_logger = logging.getLogger(__name__)


class SOConfirmationDocument(AbstractDocument):
    document_type = 'so_confirmation'
    format = 'xml'

    def build_outbound(self, record):
        raise NotImplementedError('SO Confirmation is inbound-only')

    def parse_inbound(self, payload):
        """Parse MF SO Confirmation XML (SCH + SCL) into a structured dict."""
        root = etree.fromstring(payload.encode('utf-8'))
        sch = root.find('SCH') or root
        lines = []
        for scl in sch.findall('Lines/SCL'):
            lines.append({
                'product_code': scl.findtext('ProductCode', '').strip(),
                'qty_done': float(scl.findtext('UnitsFulfilled', '0') or 0),
                'lot_number': scl.findtext('LotNumber', '').strip(),
            })
        return {
            'reference': sch.findtext('Reference', '').strip(),
            'consignment_no': sch.findtext('ConsignmentNo', '').strip(),
            'carrier_name': sch.findtext('CarrierName', '').strip(),
            'finalised_date': self._parse_date(sch.findtext('FinalisedDate', '')),
            'eta_date': self._parse_date(sch.findtext('ETADate', '')),
            'lines': lines,
        }

    def apply_inbound(self, message):
        """Apply parsed SO Confirmation to Odoo: update picking status, connote, move qtys."""
        if not message.payload_xml:
            raise ValueError(f'No XML payload on message {message.id} — cannot apply SO Confirmation')
        parsed = self.parse_inbound(message.payload_xml)
        order = self.env['sale.order'].search(
            [('name', '=', parsed['reference'])], limit=1
        )
        if not order:
            raise ValueError(f"Sale order not found: {parsed['reference']}")

        picking = order.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel')
        )[:1]
        if not picking:
            _logger.warning('MF: No open picking found for order %s', parsed['reference'])
            return

        write_vals = {'carrier_tracking_ref': parsed['consignment_no']}
        if parsed['finalised_date']:
            write_vals['date_done'] = parsed['finalised_date']
        if parsed['eta_date']:
            write_vals['scheduled_date'] = parsed['eta_date']
        picking.write(write_vals)

        # Set x_mf_status if field exists (added in Task 16)
        if hasattr(picking, 'x_mf_status'):
            picking.x_mf_status = 'mf_dispatched'
        if hasattr(picking, 'x_mf_connote'):
            picking.x_mf_connote = parsed['consignment_no']

        # Match carrier by name
        if parsed['carrier_name']:
            carrier = self.env['delivery.carrier'].search(
                [('name', 'ilike', parsed['carrier_name'])], limit=1
            )
            if carrier:
                picking.carrier_id = carrier

        # Reconcile move line quantities
        for line_data in parsed['lines']:
            product = self.env['product.product'].search(
                [('default_code', '=', line_data['product_code'])], limit=1
            )
            if product:
                move = picking.move_lines.filtered(
                    lambda m: m.product_id == product
                )[:1]
                if move and move.move_line_ids:
                    move.move_line_ids[0].qty_done = line_data['qty_done']

    @staticmethod
    def _parse_date(date_str):
        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, AttributeError):
                continue
        return None
