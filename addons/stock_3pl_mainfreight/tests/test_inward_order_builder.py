from lxml import etree
from odoo.tests.common import TransactionCase
from odoo.addons.stock_3pl_mainfreight.document.inward_order import InwardOrderDocument


class TestInwardOrderBuilder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        connector_vals = {
            'name': 'IO Test Connector',
            'warehouse_id': cls.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
        }
        if hasattr(cls.env['3pl.connector'], 'warehouse_code'):
            connector_vals['warehouse_code'] = 'AKL'
        cls.connector = cls.env['3pl.connector'].create(connector_vals)

        cls.supplier = cls.env['res.partner'].create({
            'name': 'CN Supplier', 'street': '1 Main', 'city': 'Shanghai',
            'country_id': cls.env.ref('base.cn').id,
        })
        cls.wh_partner = cls.env['res.partner'].create({
            'name': 'MF Auckland', 'street': '5 Mainfreight Dr',
            'city': 'Auckland', 'country_id': cls.env.ref('base.nz').id,
        })
        cls.warehouse.partner_id = cls.wh_partner

        cls.product = cls.env['product.product'].create({
            'name': 'Widget', 'default_code': 'WGT001', 'type': 'product',
            'x_freight_weight': 1.5,
        })
        po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.env['purchase.order.line'].create({
            'order_id': po.id, 'product_id': cls.product.id,
            'product_qty': 100, 'price_unit': 5.0,
        })
        cls.po = po

        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) \
              or cls.env.company.currency_id
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.env['delivery.carrier'].search([], limit=1).id,
            'currency_id': nzd.id,
            'carrier_booking_id': 'DSVBK_IO_001',
            'vessel_name': 'MSC Oscar',
            'voyage_number': 'VOY42',
            'container_number': 'CONT001',
            'transport_mode': 'sea_lcl',
            'purchase_order_id': po.id,
        })

    def _doc(self):
        return InwardOrderDocument(self.connector, self.env)

    def test_build_create_returns_xml_string(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        self.assertIsInstance(xml, str)
        self.assertIn('<?xml', xml)

    def test_create_action_attribute(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.get('action'), 'CREATE')

    def test_update_action_attribute(self):
        xml = self._doc().build_outbound(self.booking, action='update')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.get('action'), 'UPDATE')

    def test_order_ref_is_po_name(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('OrderRef'), self.po.name)

    def test_booking_ref(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('BookingRef'), 'DSVBK_IO_001')

    def test_vessel_name_in_transport(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        transport = root.find('Transport')
        self.assertEqual(transport.findtext('Vessel'), 'MSC Oscar')

    def test_tba_vessel_when_empty(self):
        self.booking.vessel_name = ''
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.find('Transport').findtext('Vessel'), 'TBA')
        self.booking.vessel_name = 'MSC Oscar'  # restore

    def test_po_lines_in_xml(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        lines = root.findall('.//Line')
        self.assertGreater(len(lines), 0)
        self.assertEqual(lines[0].findtext('ProductCode'), 'WGT001')

    def test_xml_is_valid(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        # Must parse without error
        etree.fromstring(xml.encode())
