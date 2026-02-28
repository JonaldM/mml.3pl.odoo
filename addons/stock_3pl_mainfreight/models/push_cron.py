# addons/stock_3pl_mainfreight/models/push_cron.py
"""MF push cron — routes unrouted orders then pushes mf_queued pickings to MF."""
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class MFPushCron(models.AbstractModel):
    """Cron service model for the Mainfreight outbound push pipeline.

    AbstractModel — no stored fields, no database table.
    Invoked via ir.cron as: self.env['mf.push.cron']._run_mf_push()
    """
    _name = 'mf.push.cron'
    _description = 'MF Push Cron'

    @api.model
    def _run_mf_push(self):
        """Main entry point called by the MF push ir.cron job.

        1. Routes any confirmed sale orders whose pickings are not yet assigned
           to a warehouse (x_mf_routed_by is False).
        2. Delegates to the core queue processor to push all mf_queued pickings
           (via 3pl.message._process_outbound_queue).
        """
        self._route_pending_orders()
        self.env['3pl.message']._process_outbound_queue()

    @api.model
    def _route_pending_orders(self):
        """Route confirmed sale orders that have not yet been assigned to a warehouse.

        Runs as the first step of the MF push cron. Finds all unrouted pickings
        (x_mf_routed_by is not set), groups them by sale order, routes each order,
        and applies the routing (split + cross-border check).

        Skips orders that already have all pickings routed.
        Logs errors per-order and continues — one bad order must not block the batch.
        """
        route_engine = self.env['mf.route.engine']
        split_engine = self.env['mf.split.engine']

        unrouted_pickings = self.env['stock.picking'].search([
            ('x_mf_routed_by', '=', False),
            ('state', 'in', ('confirmed', 'assigned', 'waiting')),
        ])
        orders = unrouted_pickings.mapped('sale_id').filtered(
            lambda o: o.id and o.state == 'sale'
        )

        for order in orders:
            try:
                assignments = route_engine.route_order(order)
                split_engine.apply_routing(order, assignments)
            except Exception as e:
                _logger.error(
                    '_route_pending_orders: routing failed for order %s: %s',
                    order.name, e,
                )
