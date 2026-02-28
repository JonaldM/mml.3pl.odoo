# addons/stock_3pl_mainfreight/models/split_engine.py
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class MFSplitEngine(models.AbstractModel):
    """Applies routing assignments to Odoo pickings.

    AbstractModel — no stored fields. Call via self.env['mf.split.engine'].
    """
    _name = 'mf.split.engine'
    _description = 'MF Split Order Engine'

    @api.model
    def apply_routing(self, order, assignments):
        """Apply routing assignments to an order by adjusting pickings.

        Takes the list of dicts from MFRouteEngine.route_order() and modifies
        Odoo pickings so each warehouse has its own picking with the correct moves.

        Sets x_mf_routed_by, x_mf_cross_border, and x_mf_status on each picking.
        Sets x_mf_split=True on the sale.order if more than one assignment.

        Returns a recordset of all affected pickings.
        """
        if not assignments:
            return self.env['stock.picking']

        is_split = len(assignments) > 1
        if is_split:
            order.write({'x_mf_split': True})

        routed_by = 'auto_split' if is_split else 'auto_closest'
        all_pickings = self.env['stock.picking']

        # Collect existing unrouted outbound pickings for this order
        unrouted = order.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel') and not p.x_mf_routed_by
        )

        for i, assignment in enumerate(assignments):
            wh = assignment['warehouse']
            line_products = {product for product, _ in assignment['lines']}

            if i == 0 and unrouted:
                if len(unrouted) > 1:
                    _logger.warning(
                        'split_engine: order %s has %d unrouted pickings; '
                        'only the first will be reused — extras remain unrouted',
                        order.name, len(unrouted),
                    )
                picking = unrouted[0]
                # Move any moves NOT in this assignment to a new picking
                moves_to_move = picking.move_ids.filtered(
                    lambda m: m.product_id not in line_products
                )
                if moves_to_move:
                    # If copy() raises, the transaction rolls back entirely; the cron will retry.
                    new_picking = picking.copy({'move_ids': []})
                    moves_to_move.write({'picking_id': new_picking.id})
                    _logger.info(
                        'split_engine: moved %d move(s) from picking %s to new picking %s',
                        len(moves_to_move), picking.name, new_picking.name,
                    )
            else:
                # Create a new picking for subsequent assignments
                picking_type = wh.out_type_id
                picking = self.env['stock.picking'].create({
                    'picking_type_id': picking_type.id,
                    'origin': order.name,
                    'partner_id': (order.partner_shipping_id or order.partner_id).id,
                    'location_id': wh.lot_stock_id.id,
                    'location_dest_id': (
                        order.partner_shipping_id or order.partner_id
                    ).property_stock_customer.id,
                })
                # NOTE: The write() at the end of each loop iteration must run before this
                # search executes on the next pass — already-routed pickings are excluded
                # by the x_mf_routed_by filter. Do not defer writes outside the loop.
                moves = self.env['stock.picking'].search([
                    ('sale_id', '=', order.id),
                    ('state', 'not in', ('done', 'cancel')),
                ]).move_ids.filtered(
                    lambda m: m.product_id in line_products and not m.picking_id.x_mf_routed_by
                )
                moves.write({'picking_id': picking.id})

            is_cross_border = self._is_cross_border(wh, picking)
            mf_status = 'mf_held_review' if is_cross_border else 'mf_queued'
            picking.write({
                'x_mf_routed_by': routed_by,
                'x_mf_cross_border': is_cross_border,
                'x_mf_status': mf_status,
            })
            all_pickings |= picking
            _logger.info(
                'split_engine: picking %s → warehouse %s, cross_border=%s, status=%s',
                picking.name, wh.name, is_cross_border, mf_status,
            )

        return all_pickings

    @api.model
    def _is_cross_border(self, warehouse, picking):
        """Return True if warehouse country differs from delivery country.

        Uses warehouse.partner_id.country_id vs picking.partner_id.country_id.
        Returns False if either country is not set (fail-open: do not block).
        """
        wh_country = warehouse.partner_id.country_id
        dest_country = picking.partner_id.country_id
        if not wh_country or not dest_country:
            return False
        return wh_country.id != dest_country.id
