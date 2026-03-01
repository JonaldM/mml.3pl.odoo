# addons/stock_3pl_mainfreight/document/inward_order.py
import logging
from lxml import etree
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

_logger = logging.getLogger(__name__)


class InwardOrderDocument(AbstractDocument):
    document_type = 'inward_order'
    format = 'xml'

    def build_outbound(self, booking, action='create'):
        """Build Mainfreight InwardOrder XML for a freight.booking record.

        action: 'create' or 'update'
        """
        if action not in ('create', 'update'):
            raise ValueError(f"InwardOrderDocument.build_outbound: invalid action {action!r}")
        _logger.debug('Building InwardOrder XML for booking %s (action=%s)', booking.name, action)

        po        = booking.purchase_order_id
        warehouse = (po.picking_type_id.warehouse_id if po and po.picking_type_id else None)
        wh_partner = warehouse.partner_id if warehouse else None
        wh_code    = getattr(self.connector, 'warehouse_code', '') or ''

        root = etree.Element('InwardOrder', action=action.upper())

        self._add(root, 'OrderRef',   po.name if po else '', max_len=50)
        self._add(root, 'BookingRef', booking.carrier_booking_id or '', max_len=50)

        # Supplier
        sup_el = etree.SubElement(root, 'Supplier')
        supplier = po.partner_id if po else None
        if supplier:
            self._add(sup_el, 'Name',    supplier.name or '',  max_len=100)
            self._add(sup_el, 'Address', supplier.street or '', max_len=100)
            self._add(sup_el, 'Country',
                      supplier.country_id.code if supplier.country_id else '', max_len=3)

        # Consignee
        con_el = etree.SubElement(root, 'Consignee')
        if wh_partner:
            self._add(con_el, 'Name',    wh_partner.name or '',  max_len=100)
            self._add(con_el, 'Address', wh_partner.street or '', max_len=100)
            self._add(con_el, 'Country',
                      wh_partner.country_id.code if wh_partner.country_id else '', max_len=3)
        self._add(con_el, 'WarehouseCode', wh_code, max_len=10)

        # ETA
        if booking.eta:
            self._add(root, 'ExpectedArrival', booking.eta.strftime('%Y-%m-%d'))
            # NOTE: booking.eta is stored as UTC. Mainfreight interprets ExpectedArrival
            # as a local date; for NZ warehouses (UTC+12/13) the UTC date may be one day
            # early. Accepted as-is; adjust if Mainfreight reports date mismatches.

        # Transport
        tr_el = etree.SubElement(root, 'Transport')
        self._add(tr_el, 'Mode',        booking.transport_mode or '',        max_len=20)
        self._add(tr_el, 'Vessel',      booking.vessel_name or 'TBA',        max_len=100)
        self._add(tr_el, 'VoyageNo',    booking.voyage_number or 'TBA',      max_len=50)
        self._add(tr_el, 'ContainerNo', booking.container_number or '',      max_len=50)

        # Lines
        lines_el = etree.SubElement(root, 'Lines')
        for line in (po.order_line if po else []):
            product = line.product_id
            if not product:
                _logger.warning('inward_order: PO line %s has no product — skipping', line.id)
                continue
            line_el = etree.SubElement(lines_el, 'Line')
            self._add(line_el, 'ProductCode', product.default_code or '', max_len=40)
            self._add(line_el, 'Description', product.name or '',         max_len=100)
            self._add(line_el, 'Quantity',    str(round(line.product_qty)))
            self._add(line_el, 'UOM',         line.product_uom.name if line.product_uom else '')
            weight = getattr(product.product_tmpl_id, 'x_freight_weight', 0.0) or 0.0
            self._add(line_el, 'WeightKg', f'{weight * line.product_qty:.3f}')

        return etree.tostring(root, pretty_print=True, xml_declaration=True,
                              encoding='UTF-8').decode('utf-8')

    def _add(self, parent, tag, value, max_len=None):
        el = etree.SubElement(parent, tag)
        el.text = self.truncate(value, max_len) if max_len else (str(value) if value is not None else '')

    def get_filename(self, booking):
        po_name = booking.purchase_order_id.name if booking.purchase_order_id else booking.name
        return f'inward_order_{po_name.replace("/", "_")}.xml'

    def get_idempotency_key(self, booking):
        po_name = booking.purchase_order_id.name if booking.purchase_order_id else str(booking.id)
        return self.make_idempotency_key(self.connector.id, self.document_type, po_name)
