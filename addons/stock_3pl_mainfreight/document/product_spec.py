# addons/stock_3pl_mainfreight/document/product_spec.py
import csv
import io
from odoo.exceptions import ValidationError
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

HEADERS = [
    'Product Code', 'Product Description 1', 'Product Description 2',
    'Unit Weight', 'Unit Volume', 'Unit Price',
    'Grade1', 'Grade2', 'Grade3', 'Expiry Date', 'Packing Date',
    'Carton Per Layer', 'Layer Per Pallet',
    'Default Pack Size', 'Default Pack Description', 'Default Barcode',
    'Default Length', 'Default Width', 'Default Height',
    'Pack Size 2', 'Pack Description 2', 'Pack Barcode 2',
    'Pack Size 3', 'Pack Description 3', 'Pack Barcode 3',
    'Pack Size 4', 'Pack Description 4', 'Pack Barcode 4',
    'Warehouse ID',
]

MAX_PACK_TYPES = 4


class ProductSpecDocument(AbstractDocument):
    document_type = 'product_spec'
    format = 'csv'

    def build_outbound(self, product):
        """Build MF Product Specification CSV for a single product.product record."""
        if not product.default_code:
            raise ValidationError(
                f'Product "{product.name}" has no Internal Reference (default_code). '
                f'This is mandatory for Mainfreight product sync.'
            )

        row = self._build_row(product)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=HEADERS, extrasaction='ignore')
        writer.writeheader()
        writer.writerow(row)
        return output.getvalue()

    def build_outbound_batch(self, products):
        """Build MF Product Specification CSV for multiple products."""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=HEADERS, extrasaction='ignore')
        writer.writeheader()
        for product in products:
            if not product.default_code:
                continue  # Skip silently in batch
            writer.writerow(self._build_row(product))
        return output.getvalue()

    def _build_row(self, product):
        row = {
            'Product Code': self.truncate(product.default_code, 40),
            'Product Description 1': self.truncate(product.name, 40),
            'Product Description 2': self.truncate(product.description_sale or '', 40),
            'Unit Weight': round(product.weight or 0, 4),
            'Unit Volume': round(product.volume or 0, 4),
            'Unit Price': round(product.standard_price or 0, 2),
            'Grade1': 'N',
            'Grade2': 'N',
            'Grade3': 'N',
            'Expiry Date': 'N',
            'Packing Date': 'N',
            'Carton Per Layer': getattr(product, 'x_mf_carton_per_layer', None) or '',
            'Layer Per Pallet': getattr(product, 'x_mf_layer_per_pallet', None) or '',
            'Default Pack Size': '',
            'Default Pack Description': '',
            'Default Barcode': '',
            'Default Length': '',
            'Default Width': '',
            'Default Height': '',
            'Pack Size 2': '',
            'Pack Description 2': '',
            'Pack Barcode 2': '',
            'Pack Size 3': '',
            'Pack Description 3': '',
            'Pack Barcode 3': '',
            'Pack Size 4': '',
            'Pack Description 4': '',
            'Pack Barcode 4': '',
            'Warehouse ID': self.connector.warehouse_code or '',
        }

        # Grade1 = lot tracking enabled
        if product.tracking == 'lot':
            row['Grade1'] = 'Y'

        # Packaging (up to 4 pack types from product.packaging_ids)
        packagings = product.packaging_ids[:MAX_PACK_TYPES]
        for i, pkg in enumerate(packagings, start=1):
            if i == 1:
                row['Default Pack Size'] = int(pkg.qty or 1)
                row['Default Pack Description'] = self.truncate(pkg.name, 20)
                row['Default Barcode'] = self.truncate(pkg.barcode or '', 40)
                if hasattr(pkg, 'length'):
                    row['Default Length'] = round(pkg.length or 0, 4)
                    row['Default Width'] = round(pkg.width or 0, 4)
                    row['Default Height'] = round(pkg.height or 0, 4)
            else:
                row[f'Pack Size {i}'] = int(pkg.qty or 1)
                row[f'Pack Description {i}'] = self.truncate(pkg.name, 20)
                row[f'Pack Barcode {i}'] = self.truncate(pkg.barcode or '', 40)

        return row

    def get_filename(self, record):
        return f'product_spec_{record.default_code}.csv'
