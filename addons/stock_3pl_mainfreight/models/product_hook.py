# addons/stock_3pl_mainfreight/models/product_hook.py
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)

SYNC_FIELDS = {
    'default_code', 'name', 'weight', 'volume', 'standard_price',
    'description_sale', 'tracking',
    # packaging_ids lives on product.template — hook that model separately if needed
}


class ProductProductMF(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        result = super().write(vals)
        if SYNC_FIELDS.intersection(vals.keys()):
            for product in self:
                product._queue_mf_product_sync()
        return result

    def _queue_mf_product_sync(self):
        """Queue a product_spec message for all active MF connectors for this product."""
        self.ensure_one()
        connectors = self.env['3pl.connector'].search([
            ('warehouse_partner', '=', 'mainfreight'),
            ('active', '=', True),
        ])
        for connector in connectors:
            from odoo.addons.stock_3pl_mainfreight.document.product_spec import ProductSpecDocument
            doc = ProductSpecDocument(connector, self.env)
            if not self.default_code:
                continue
            idempotency_key = doc.make_idempotency_key(
                connector.id, 'product_spec', self.default_code
            )
            # Skip if an identical message is already in-flight (queued or sending)
            in_flight = self.env['3pl.message'].search([
                ('connector_id', '=', connector.id),
                ('document_type', '=', 'product_spec'),
                ('idempotency_key', '=', idempotency_key),
                ('state', 'in', ('queued', 'sending')),
            ], limit=1)
            if in_flight:
                continue
            payload = doc.build_outbound(self)
            # If product was previously sent, queue as update; otherwise create
            already_sent = self.env['3pl.message'].search([
                ('connector_id', '=', connector.id),
                ('document_type', '=', 'product_spec'),
                ('idempotency_key', '=', idempotency_key),
                ('state', 'not in', ('dead',)),
            ], limit=1)
            action = 'update' if already_sent else 'create'
            self.env['3pl.message'].create({
                'connector_id': connector.id,
                'direction': 'outbound',
                'document_type': 'product_spec',
                'action': action,
                'payload_csv': payload,
                'ref_model': 'product.product',
                'ref_id': self.id,
                'idempotency_key': idempotency_key,
                'state': 'queued',
            })
            _logger.info('MF: Queued product_spec %s (action=%s) for connector %s',
                         self.default_code, action, connector.name)
