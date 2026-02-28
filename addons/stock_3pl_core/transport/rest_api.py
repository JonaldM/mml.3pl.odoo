from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport


class RestTransport(AbstractTransport):
    def send(self, payload, content_type='xml', endpoint=None):
        raise NotImplementedError('RestTransport.send() implemented in Task 5')
