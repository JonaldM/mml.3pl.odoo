# addons/stock_3pl_mainfreight/tests/test_sales_order.py
from odoo.tests import TransactionCase, tagged
from lxml import etree


@tagged('post_install', '-at_install', 'mf_so')
class TestSalesOrderDocument(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'MF Test',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.partner = self.env['res.partner'].create({
            'name': 'Test Customer',
            'ref': 'CUST001',
            'street': '10 Demo Street',
            'city': 'Auckland',
            'zip': '1010',
            'country_id': self.env.ref('base.nz').id,
        })
        product = self.env['product.product'].create({
            'name': 'Widget',
            'default_code': 'WIDG001',
            'type': 'product',
        })
        self.order = self.env['sale.order'].create({
            'name': 'SO001',
            'partner_id': self.partner.id,
            'warehouse_id': warehouse.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 10,
                'price_unit': 15.00,
            })],
        })

    def _build(self):
        from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
        doc = SalesOrderDocument(self.connector, self.env)
        return doc.build_outbound(self.order)

    def test_xml_root_is_order(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.tag, 'Order')

    def test_client_order_number_maps_to_so_name(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('ClientOrderNumber'), 'SO001')

    def test_consignee_code_maps_to_partner_ref(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('ConsigneeCode'), 'CUST001')

    def test_warehouse_code_from_connector(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('WarehouseCode'), '99')

    def test_order_lines_present(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        lines = root.findall('Lines/Line')
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].findtext('ProductCode'), 'WIDG001')
        self.assertEqual(lines[0].findtext('Units'), '10')

    def test_idempotency_key_generated(self):
        from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
        doc = SalesOrderDocument(self.connector, self.env)
        key = doc.get_idempotency_key(self.order)
        self.assertIsNotNone(key)
        self.assertEqual(len(key), 64)  # SHA-256 hex
