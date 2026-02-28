# addons/stock_3pl_mainfreight/models/product_hook.py
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)

SYNC_FIELDS = {'default_code', 'name', 'weight', 'volume', 'standard_price',
               'description_sale', 'tracking', 'packaging_ids'}


class ProductProductMF(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        result = super().write(vals)
        if SYNC_FIELDS.intersection(vals.keys()):
            for product in self:
                self._queue_mf_product_sync(product)
        return result

    def _queue_mf_product_sync(self, product):
        connectors = self.env['3pl.connector'].search([
            ('forwarder', '=', 'mainfreight'),
            ('active', '=', True),
        ])
        for connector in connectors:
            from odoo.addons.stock_3pl_mainfreight.document.product_spec import ProductSpecDocument
            doc = ProductSpecDocument(connector, self.env)
            if not product.default_code:
                continue
            idempotency_key = doc.make_idempotency_key(
                connector.id, 'product_spec', product.default_code
            )
            # Use update action if message already sent
            existing = self.env['3pl.message'].search([
                ('connector_id', '=', connector.id),
                ('document_type', '=', 'product_spec'),
                ('ref_id', '=', product.id),
                ('state', 'not in', ('dead',)),
            ], limit=1)
            if existing:
                continue
            payload = doc.build_outbound(product)
            self.env['3pl.message'].create({
                'connector_id': connector.id,
                'direction': 'outbound',
                'document_type': 'product_spec',
                'action': 'create',
                'payload_csv': payload,
                'ref_model': 'product.product',
                'ref_id': product.id,
                'idempotency_key': idempotency_key,
                'state': 'queued',
            })
