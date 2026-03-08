# addons/stock_3pl_mainfreight/models/route_engine.py
import logging
import math
from odoo import models, api
from odoo.exceptions import UserError
from odoo.addons.stock_3pl_mainfreight.utils.haversine import sort_warehouses_by_distance

_logger = logging.getLogger(__name__)


class MFRouteEngine(models.AbstractModel):
    """Service model for Mainfreight warehouse routing decisions.

    AbstractModel — no stored fields, no database table, no ACL needed.
    Call via: self.env['mf.route.engine'].route_order(order)

    Routing algorithm (V3 Section 5.2):
    - Sort MF-enabled warehouses by haversine distance to customer
    - Greedy assignment: for each warehouse, check available stock
    - Single-line orders: prefer complete fulfilment at one warehouse
    - Multi-line orders: partial assignment across warehouses is allowed
    """
    _name = 'mf.route.engine'
    _description = 'MF Warehouse Routing Engine'

    @api.model
    def route_order(self, order):
        """Assign sale.order lines to MF warehouses.

        Returns a list of assignment dicts:
            [{'warehouse': stock.warehouse, 'lines': [(product.product, qty)]}, ...]

        One dict per warehouse. If the full order can be fulfilled from one
        warehouse, returns a single-element list.

        Raises UserError if no MF warehouses are configured.
        """
        warehouses = self._get_mf_warehouses()
        if not warehouses:
            raise UserError(
                'No MF-enabled warehouses configured. '
                'Enable at least one warehouse under Inventory \u2192 Warehouses.'
            )

        lines = self._order_lines(order)
        if not lines:
            return []

        partner = order.partner_shipping_id or order.partner_id
        cust_lat = partner.partner_latitude
        cust_lng = partner.partner_longitude

        if not cust_lat or not cust_lng:
            _logger.warning(
                'route_order: partner %s (order %s) has no lat/lng — '
                'assigning to first MF warehouse: %s',
                partner.name, order.name, warehouses[0].name,
            )
            return [{'warehouse': warehouses[0], 'lines': lines}]

        # Sort warehouses by distance to customer
        # Filter out warehouses without coordinates before calling haversine
        wh_data = [
            {'warehouse': wh, 'lat': wh.x_mf_latitude, 'lng': wh.x_mf_longitude}
            for wh in warehouses
            if wh.x_mf_latitude and wh.x_mf_longitude
        ]
        if not wh_data:
            _logger.warning(
                'route_order: no MF warehouses have lat/lng configured for order %s '
                '— assigning to first MF warehouse: %s',
                order.name, warehouses[0].name,
            )
            return [{'warehouse': warehouses[0], 'lines': lines}]

        sorted_wh = sort_warehouses_by_distance(cust_lat, cust_lng, wh_data)
        is_single_line = len(lines) == 1

        if is_single_line:
            # For single-line orders: prefer one warehouse that can fill completely
            product, qty = lines[0]
            for wh_entry in sorted_wh:
                wh = wh_entry['warehouse']
                stock = self._check_stock(wh, [(product, qty)])
                if stock.get(product, 0.0) >= qty:
                    return [{'warehouse': wh, 'lines': [(product, qty)]}]
            # No single warehouse can fill — fall through to greedy split
            _logger.warning(
                'route_order: single-line order %s cannot be fulfilled completely '
                'at any single warehouse — splitting across warehouses.',
                order.name,
            )

        # Multi-line (or single-line fallback): greedy assignment
        remaining = list(lines)
        assignments = []

        for wh_entry in sorted_wh:
            if not remaining:
                break
            wh = wh_entry['warehouse']
            stock = self._check_stock(wh, remaining)
            fulfillable = []
            still_remaining = []
            for product, qty in remaining:
                avail = stock.get(product, 0.0)
                if avail >= qty:
                    fulfillable.append((product, qty))
                elif avail > 0:
                    fulfillable.append((product, avail))
                    still_remaining.append((product, qty - avail))
                else:
                    still_remaining.append((product, qty))
            if fulfillable:
                assignments.append({'warehouse': wh, 'lines': fulfillable})
            remaining = still_remaining

        if remaining:
            _logger.warning(
                'route_order: order %s — %d product(s) could not be assigned '
                'to any MF warehouse (insufficient stock)',
                order.name, len(remaining),
            )

        return assignments

    @api.model
    def _get_mf_warehouses(self):
        """Return all MF-enabled warehouses, ordered by x_mf_warehouse_code then name for determinism."""
        return self.env['stock.warehouse'].search(
            [('x_mf_enabled', '=', True)],
            order='x_mf_warehouse_code, name',
        )

    @api.model
    def _check_stock(self, warehouse, lines):
        """Return {product: available_qty} at the warehouse stock location.

        Always computes Odoo stock.quant quantities first. If the connector
        for this warehouse has ``x_mf_use_api_soh = True``, also calls the
        MF SOH API and uses its figures when drift is detected (any non-zero
        difference). Falls back to Odoo quantities on any API error or when
        no connector is configured — routing is never blocked by the API.
        """
        location = warehouse.lot_stock_id
        result = {}
        for product, _qty in lines:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', 'child_of', location.id),
            ])
            result[product] = sum(quants.mapped('quantity'))

        # Optional MF SOH API cross-check ----------------------------------
        connector = self.env['3pl.connector'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1
        )
        if not connector:
            return result
        if not connector.x_mf_use_api_soh:
            return result

        try:
            transport = connector.get_transport()
            soh_records = transport.get_stock_on_hand()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                '_check_stock: SOH API call failed for warehouse %s — '
                'falling back to Odoo quantities. Error: %s',
                warehouse.name, exc,
            )
            return result

        if not soh_records:
            _logger.warning(
                '_check_stock: SOH API returned empty response for warehouse %s — '
                'falling back to Odoo quantities.',
                warehouse.name,
            )
            return result

        # Build a lookup: ProductCode → QuantityAvailable
        soh_by_code = {
            rec['ProductCode']: rec.get('QuantityAvailable', 0.0)
            for rec in soh_records
            if isinstance(rec, dict) and rec.get('ProductCode')
        }

        for product in list(result.keys()):
            code = product.default_code
            if not code or code not in soh_by_code:
                continue
            mf_qty = float(soh_by_code[code])
            if math.isnan(mf_qty) or math.isinf(mf_qty) or mf_qty < 0.0 or mf_qty > 1e9:
                _logger.warning(
                    '_check_stock: SOH API returned out-of-bounds quantity %.2f '
                    'for product %s — skipping MF override, using Odoo qty.',
                    mf_qty, code,
                )
                continue
            odoo_qty = result[product]
            drift = abs(mf_qty - odoo_qty)
            if drift > self._get_soh_drift_threshold():
                _logger.warning(
                    '_check_stock: SOH drift detected for product %s (code=%s) '
                    'at warehouse %s — Odoo qty=%.2f, MF qty=%.2f. Using MF figure.',
                    product.name, code, warehouse.name, odoo_qty, mf_qty,
                )
                result[product] = mf_qty

        return result

    @api.model
    def _get_soh_drift_threshold(self):
        """
        Minimum absolute qty difference that triggers a SOH warning log.
        Default 0 = log all diffs. Increase post-go-live to reduce noise.
        Configure via Settings > Technical > Parameters > System Parameters:
          key: mml_3pl.soh_drift_threshold
          value: 1.0  (or whatever threshold makes sense)
        """
        try:
            val = self.env['ir.config_parameter'].sudo().get_param(
                'mml_3pl.soh_drift_threshold', '0'
            )
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @api.model
    def _order_lines(self, order):
        """Extract (product, qty) tuples from a sale order.

        Returns only storable products (type='product'). Service/consumable
        lines are excluded — MF only manages physical stock.
        """
        return [
            (line.product_id, line.product_uom_qty)
            for line in order.order_line
            if line.product_id and line.product_id.type == 'product'
        ]
