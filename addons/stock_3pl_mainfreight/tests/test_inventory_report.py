import os
from odoo.tests import TransactionCase, tagged

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


@tagged('post_install', '-at_install', 'mf_inventory')
class TestInventoryReport(TransactionCase):

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
        self.product = self.env['product.product'].create({
            'name': 'Widget',
            'default_code': 'WIDG001',
            'type': 'product',
        })

    def _load_fixture(self):
        with open(os.path.join(FIXTURE_DIR, 'inventory_report.csv')) as f:
            return f.read()

    def _get_doc(self):
        from odoo.addons.stock_3pl_mainfreight.document.inventory_report import InventoryReportDocument
        return InventoryReportDocument(self.connector, self.env)

    def test_parse_returns_list_of_lines(self):
        doc = self._get_doc()
        lines = doc.parse_inbound(self._load_fixture())
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['product_code'], 'WIDG001')
        self.assertEqual(lines[0]['stock_on_hand'], 100)
        self.assertEqual(lines[0]['quantity_available'], 95)

    def test_apply_updates_stock_quant(self):
        doc = self._get_doc()
        csv_data = self._load_fixture()
        doc.apply_csv(csv_data, report_date=None)
        location = self.connector.warehouse_id.lot_stock_id
        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', location.id),
        ], limit=1)
        self.assertTrue(quant)
        self.assertEqual(quant.quantity, 100)
