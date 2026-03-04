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
        Returns the message ID, or None if the PO does not exist or no connector is available.
        """
        po = self.env['purchase.order'].browse(purchase_order_id)
        if not po.exists():
            _logger.warning('TPLService.queue_inward_order: PO %s not found', purchase_order_id)
            return None
        if not connector_id:
            connector = self.env['3pl.connector'].search(
                [('active', '=', True)], limit=1
            )
            if not connector:
                _logger.warning(
                    'tpl_service.queue_inward_order: no active 3pl.connector found — '
                    'cannot queue PO %s', po.name if hasattr(po, 'name') else po,
                )
                return None
            connector_id = connector.id
        vals = {
            'document_type': 'inward_order',
            'ref_model': 'purchase.order',
            'ref_id': po.id,
            'connector_id': connector_id,
        }
        msg = self.env['3pl.message'].create(vals)
        return msg.id
