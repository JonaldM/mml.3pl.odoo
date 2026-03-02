import logging

_logger = logging.getLogger(__name__)


class TPLService:
    """
    Public API for stock_3pl_core. Retrieved via:
        svc = self.env['mml.registry'].service('3pl')
    Returns NullService if stock_3pl_core is not installed.
    """

    def __init__(self, env):
        self.env = env

    def queue_inward_order(self, purchase_order_id: int, connector_id: int | None = None) -> int | None:
        """
        Queue a 3pl.message of type inward_order for the given PO.
        Returns the message ID, or None if the PO does not exist.
        """
        po = self.env['purchase.order'].browse(purchase_order_id)
        if not po.exists():
            _logger.warning('TPLService.queue_inward_order: PO %s not found', purchase_order_id)
            return None
        vals = {
            'document_type': 'inward_order',
            'res_model': 'purchase.order',
            'res_id': po.id,
        }
        if connector_id:
            vals['connector_id'] = connector_id
        msg = self.env['3pl.message'].create(vals)
        return msg.id
