# addons/stock_3pl_mainfreight/models/sale_order_hook.py
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class SaleOrderMF(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        result = super().action_confirm()
        for order in self:
            self._queue_mf_sales_order(order)
        return result

    def _queue_mf_sales_order(self, order):
        """Find the active MF connector for this order's warehouse and queue."""
        connector = self.env['3pl.connector'].search([
            ('warehouse_id', '=', order.warehouse_id.id),
            ('forwarder', '=', 'mainfreight'),
            ('active', '=', True),
        ], limit=1)
        if not connector:
            return

        from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
        doc = SalesOrderDocument(connector, self.env)
        idempotency_key = doc.get_idempotency_key(order)

        # Block if already queued for this order
        existing = self.env['3pl.message'].search([
            ('connector_id', '=', connector.id),
            ('document_type', '=', 'sales_order'),
            ('idempotency_key', '=', idempotency_key),
            ('state', 'not in', ('dead',)),
        ], limit=1)
        if existing:
            _logger.info('MF: SO %s already queued (msg %s), skipping.', order.name, existing.id)
            return

        payload = doc.build_outbound(order)
        self.env['3pl.message'].create({
            'connector_id': connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'payload_xml': payload,
            'ref_model': 'sale.order',
            'ref_id': order.id,
            'idempotency_key': idempotency_key,
            'state': 'queued',
        })
        _logger.info('MF: Queued sales order %s for connector %s', order.name, connector.name)
