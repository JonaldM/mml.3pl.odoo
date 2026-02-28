# addons/stock_3pl_mainfreight/document/sales_order.py
import logging
from lxml import etree
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

_logger = logging.getLogger(__name__)


class SalesOrderDocument(AbstractDocument):
    document_type = 'sales_order'
    format = 'xml'

    def build_outbound(self, order):
        """Build MF Sales Order XML (SOH header + SOL lines) for a sale.order record."""
        root = etree.Element('Order')
        partner = order.partner_shipping_id or order.partner_id
        invoice_partner = order.partner_invoice_id or order.partner_id

        self._add(root, 'ClientOrderNumber', order.name, max_len=50)
        self._add(root, 'ClientReference', order.client_order_ref or '', max_len=50)
        self._add(root, 'ConsigneeCode', partner.ref or '', max_len=18)
        self._add(root, 'DeliveryName', partner.name, max_len=50)
        self._add(root, 'DeliveryAddress1', partner.street or '', max_len=50)
        self._add(root, 'DeliveryAddress2', partner.street2 or '', max_len=50)
        self._add(root, 'DeliverySuburb', '', max_len=50)
        self._add(root, 'DeliveryPostCode', partner.zip or '', max_len=50)
        self._add(root, 'DeliveryCity', partner.city or '', max_len=50)
        self._add(root, 'DeliveryState',
                  partner.state_id.name if partner.state_id else '', max_len=50)
        self._add(root, 'DeliveryCountry',
                  partner.country_id.name if partner.country_id else '', max_len=50)
        self._add(root, 'DeliveryInstructions', order.note or '', max_len=500)
        self._add(root, 'InvoiceName', invoice_partner.name, max_len=60)
        self._add(root, 'InvoiceAddress1', invoice_partner.street or '', max_len=50)
        self._add(root, 'InvoiceCity', invoice_partner.city or '', max_len=50)
        self._add(root, 'InvoicePostCode', invoice_partner.zip or '', max_len=50)
        self._add(root, 'InvoiceCountry',
                  invoice_partner.country_id.name if invoice_partner.country_id else '',
                  max_len=50)
        self._add(root, 'WarehouseCode', self.connector.warehouse_code or '', max_len=3)
        self._add(root, 'CustomerID', self.connector.customer_id or '', max_len=50)
        if order.commitment_date:
            self._add(root, 'DateRequired',
                      order.commitment_date.strftime('%d/%m/%Y'))

        # Order lines
        lines_el = etree.SubElement(root, 'Lines')
        for i, line in enumerate(order.order_line, start=1):
            line_el = etree.SubElement(lines_el, 'Line')
            self._add(line_el, 'LineNumber', str(i))
            self._add(line_el, 'ProductCode',
                      self.truncate(line.product_id.default_code or '', 40))
            self._add(line_el, 'Units', str(int(line.product_uom_qty)))
            self._add(line_el, 'UnitPrice', str(round(line.price_unit, 2)))

        return etree.tostring(root, pretty_print=True, xml_declaration=True,
                              encoding='UTF-8').decode('utf-8')

    def _add(self, parent, tag, value, max_len=None):
        """Add a child element with text content, optionally truncated."""
        el = etree.SubElement(parent, tag)
        el.text = self.truncate(value, max_len) if max_len else str(value)

    def get_filename(self, order):
        return f'{order.name}.xml'

    def get_idempotency_key(self, order):
        return self.make_idempotency_key(
            self.connector.id, self.document_type, order.name
        )
