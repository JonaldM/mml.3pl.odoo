# addons/stock_3pl_mainfreight/document/so_confirmation.py
import logging
from datetime import datetime
from lxml import etree
from odoo.exceptions import ValidationError
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

_logger = logging.getLogger(__name__)

_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _validate_ref(value, field_name, max_len=256):
    """Validate a reference string from external data before using in ORM search."""
    if not value or not isinstance(value, str):
        raise ValidationError(f'Invalid {field_name}: empty or non-string value received from 3PL')
    if len(value) > max_len:
        raise ValidationError(f'Invalid {field_name}: value too long ({len(value)} chars, max {max_len})')
    return value.strip()


class SOConfirmationDocument(AbstractDocument):
    document_type = 'so_confirmation'
    format = 'xml'

    def build_outbound(self, record):
        raise NotImplementedError('SO Confirmation is inbound-only')

    def parse_inbound(self, payload):
        """Parse MF SO Confirmation into a normalised dict.

        Detects schema automatically:
        - SCH/SCL XML (PDF spec): root tag SOConfirmation with nested SCH element
        - Webhook-style XML (public API): root tag orderConfirmation with camelCase elements
        """
        root = etree.fromstring(payload.encode('utf-8'), _XML_PARSER)
        if root.find('SCH') is not None or root.tag in ('SOConfirmation', 'SCH'):
            return self._parse_sch_scl(root)
        return self._parse_webhook_style(root)

    def _parse_sch_scl(self, root):
        """Original PDF-spec parser: SCH header + SCL lines."""
        sch = root.find('SCH') or root
        lines = []
        for scl in sch.findall('Lines/SCL'):
            lines.append({
                'product_code': scl.findtext('ProductCode', '').strip(),
                'qty_done': float(scl.findtext('UnitsFulfilled', '0').strip() or '0'),
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

    def _parse_webhook_style(self, root):
        """Webhook/public-API schema: camelCase elements, richer structure.

        customerOrderReference is the Odoo SO name (maps to 'reference').
        orderReference is the MF internal reference — used as fallback only.
        TODO: expand field coverage when webhook is activated on cloud hosting.
        """
        lines = []
        for line_el in root.findall('.//orderConfirmationLine'):
            lines.append({
                'product_code': line_el.findtext('productCode', '').strip(),
                'qty_done': float(line_el.findtext('unitsFulfilled', '0').strip() or '0'),
                'lot_number': line_el.findtext('lotNumber', '').strip(),
            })
        reference = (
            root.findtext('customerOrderReference', '').strip()
            or root.findtext('orderReference', '').strip()
        )
        consignment_no = ''
        consignment_el = root.find('.//consignment')
        if consignment_el is not None:
            consignment_no = consignment_el.findtext('consignmentNumber', '').strip()
        carrier_name = ''
        sp_el = root.find('serviceProvider')
        if sp_el is not None:
            carrier_name = sp_el.findtext('name', '').strip()
        return {
            'reference': reference,
            'consignment_no': consignment_no,
            'carrier_name': carrier_name,
            'finalised_date': self._parse_date(root.findtext('dateDispatched', '')),
            'eta_date': self._parse_date(root.findtext('etaDate', '')),
            'lines': lines,
        }

    def apply_inbound(self, message):
        """Apply parsed SO Confirmation to Odoo: update picking status, connote, move qtys."""
        if not message.payload_xml:
            raise ValueError(f'No XML payload on message {message.id} — cannot apply SO Confirmation')
        parsed = self.parse_inbound(message.payload_xml)
        ref = _validate_ref(parsed.get('reference'), 'order reference')
        order = self.env['sale.order'].search(
            [('name', '=', ref)], limit=1
        )
        if not order:
            raise ValueError(f"Sale order not found: {ref}")

        picking = order.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel')
        )[:1]
        if not picking:
            _logger.warning('MF: No open picking found for order %s', parsed['reference'])
            return

        write_vals = {
            'carrier_tracking_ref': parsed['consignment_no'],
            'x_mf_status': 'mf_dispatched',
        }
        if parsed.get('consignment_no'):
            write_vals['x_mf_connote'] = parsed['consignment_no']
        if parsed['finalised_date']:
            write_vals['date_done'] = parsed['finalised_date']
        if parsed['eta_date']:
            write_vals['scheduled_date'] = parsed['eta_date']
        picking.write(write_vals)

        # Match carrier by name
        if parsed['carrier_name']:
            try:
                carrier_name = _validate_ref(parsed['carrier_name'], 'carrier name')
                carrier = self.env['delivery.carrier'].search(
                    [('name', '=', carrier_name)], limit=1
                )
                if carrier:
                    picking.carrier_id = carrier
            except ValidationError:
                _logger.warning('MF: invalid carrier name in SO Confirmation — skipping carrier match')

        # Reconcile move line quantities
        for line_data in parsed['lines']:
            if not line_data.get('product_code'):
                continue
            try:
                product_code = _validate_ref(line_data['product_code'], 'product code')
            except ValidationError:
                _logger.warning('MF: invalid product code in SO Confirmation line — skipping line')
                continue
            product = self.env['product.product'].search(
                [('default_code', '=', product_code)], limit=1
            )
            if product:
                move = picking.move_ids.filtered(
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
