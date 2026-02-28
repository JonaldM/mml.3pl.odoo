# addons/stock_3pl_mainfreight/tests/test_product_spec.py
import csv
import io
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'mf_product')
class TestProductSpec(TransactionCase):

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
        uom_kg = self.env.ref('uom.product_uom_kgm')
        self.product = self.env['product.product'].create({
            'name': 'Test Widget',
            'default_code': 'WIDGET001',
            'weight': 1.5,
            'volume': 0.002,
            'standard_price': 25.00,
            'type': 'product',
            'uom_id': uom_kg.id,
        })

    def _build(self):
        from odoo.addons.stock_3pl_mainfreight.document.product_spec import ProductSpecDocument
        doc = ProductSpecDocument(self.connector, self.env)
        return doc.build_outbound(self.product)

    def test_csv_has_header_row(self):
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        self.assertIn('Product Code', reader.fieldnames)
        self.assertIn('Product Description 1', reader.fieldnames)
        self.assertIn('Unit Weight', reader.fieldnames)

    def test_product_code_maps_to_default_code(self):
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        self.assertEqual(rows[0]['Product Code'], 'WIDGET001')

    def test_weight_and_volume_formatted(self):
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        row = list(reader)[0]
        self.assertEqual(float(row['Unit Weight']), 1.5)
        self.assertEqual(float(row['Unit Volume']), 0.002)

    def test_product_code_truncated_to_40_chars(self):
        self.product.default_code = 'A' * 50
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        row = list(reader)[0]
        self.assertEqual(len(row['Product Code']), 40)

    def test_missing_default_code_raises(self):
        self.product.default_code = False
        from odoo.exceptions import ValidationError
        from odoo.addons.stock_3pl_mainfreight.document.product_spec import ProductSpecDocument
        doc = ProductSpecDocument(self.connector, self.env)
        with self.assertRaises(ValidationError):
            doc.build_outbound(self.product)
