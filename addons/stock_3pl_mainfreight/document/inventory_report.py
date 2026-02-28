import csv
import io
import logging
from datetime import datetime
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

# Discrepancy threshold: differences within this range are auto-corrected.
# Larger discrepancies are flagged for manual review (future enhancement).
DISCREPANCY_AUTO_CORRECT_THRESHOLD = 5


class InventoryReportDocument(AbstractDocument):
    """Parser for MF Stock On Hand (SOH) daily inventory report CSV.

    Parses the 31-field MF SOH CSV and syncs quantities to stock.quant
    for the connector's warehouse. Does NOT blindly overwrite — uses
    _sync_quant to upsert based on product + location.

    apply_csv() is the main entry point (not apply_inbound()) because
    this handler receives a CSV payload, not a 3pl.message instance.
    """
    document_type = 'inventory_report'
    format = 'csv'

    def build_outbound(self, record):
        raise NotImplementedError('Inventory Report is inbound-only')

    def parse_inbound(self, payload):
        """Parse MF SOH CSV. Returns list of line dicts."""
        reader = csv.DictReader(io.StringIO(payload))
        lines = []
        for row in reader:
            lines.append({
                'product_code': row.get('Product', '').strip(),
                'warehouse_id': row.get('WarehouseID', '').strip(),
                'stock_on_hand': int(float(row.get('StockOnHand', 0) or 0)),
                'qty_on_hold': int(float(row.get('QuantityOnHold', 0) or 0)),
                'qty_damaged': int(float(row.get('QuantityDamaged', 0) or 0)),
                'quantity_available': int(float(row.get('QuantityAvailable', 0) or 0)),
                'grade1': row.get('Grade1', '').strip(),
                'grade2': row.get('Grade2', '').strip(),
                'expiry_date': self._parse_date(row.get('ExpiryDate', '')),
                'packing_date': self._parse_date(row.get('PackingDate', '')),
            })
        return lines

    def apply_csv(self, payload, report_date=None):
        """Parse and apply a full SOH report to stock.quant for the connector's warehouse."""
        lines = self.parse_inbound(payload)
        stock_location = self.connector.warehouse_id.lot_stock_id
        ICP = self.env['ir.config_parameter'].sudo()
        tolerance = float(ICP.get_param('stock_3pl_mainfreight.ira_tolerance', '0.005'))

        applied = 0
        skipped = 0
        for line in lines:
            try:
                product_code = _validate_ref(line.get('product_code'), 'product code')
            except ValidationError as exc:
                _logger.warning('MF SOH: skipping line — %s', exc)
                skipped += 1
                continue
            product = self.env['product.product'].search(
                [('default_code', '=', product_code)], limit=1
            )
            if not product:
                _logger.warning('MF SOH: product not found: %s', product_code)
                skipped += 1
                continue

            mf_qty = float(line['stock_on_hand'])

            # Capture current Odoo qty BEFORE sync to detect drift
            existing_quant = self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', stock_location.id),
            ], limit=1)
            odoo_qty = existing_quant.quantity if existing_quant else 0.0

            self._sync_quant(product, stock_location, mf_qty)
            applied += 1

            # Write discrepancy record if drift exceeds tolerance
            variance = abs(mf_qty - odoo_qty)
            threshold = odoo_qty * tolerance
            if variance > threshold:
                self._write_discrepancy(product, mf_qty, odoo_qty)

        _logger.info('MF SOH: applied=%d skipped=%d', applied, skipped)

        if report_date:
            # Record when this report was applied (not the report's date).
            # last_soh_applied_at is used by is_stale() to reject older reports.
            self.connector.last_soh_applied_at = datetime.now()

    def apply_inbound(self, message):
        """Apply inbound inventory report from a 3pl.message record."""
        payload = message.payload_csv or message.payload_xml or ''
        if not payload:
            raise ValueError(f'No CSV payload on message {message.id}')
        self.apply_csv(payload, report_date=message.report_date)

    def _sync_quant(self, product, location, quantity):
        """Upsert a stock.quant record for the given product/location."""
        quant = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', '=', location.id),
        ], limit=1)
        if quant:
            quant.sudo().write({'quantity': quantity})
        else:
            self.env['stock.quant'].sudo().create({
                'product_id': product.id,
                'location_id': location.id,
                'quantity': quantity,
            })

    def _write_discrepancy(self, product, mf_qty: float, odoo_qty: float):
        """Create or update an mf.soh.discrepancy record for this product.

        Upsert pattern: if an open discrepancy already exists for the same
        product + warehouse, update it in place. Otherwise create a new one.
        This prevents duplicate open records accumulating across daily SOH runs.
        """
        warehouse = self.connector.warehouse_id
        existing = self.env['mf.soh.discrepancy'].search([
            ('product_id', '=', product.id),
            ('warehouse_id', '=', warehouse.id),
            ('state', '=', 'open'),
        ], limit=1)
        vals = {
            'product_id': product.id,
            'warehouse_id': warehouse.id,
            'mf_qty': mf_qty,
            'odoo_qty': odoo_qty,
            'detected_date': datetime.now(),
            'state': 'open',
        }
        if existing:
            existing.write(vals)
        else:
            self.env['mf.soh.discrepancy'].create(vals)

    @staticmethod
    def _parse_date(date_str):
        for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except (ValueError, AttributeError):
                continue
        return None
