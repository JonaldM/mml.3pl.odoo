# addons/stock_3pl_mainfreight/models/sale_order_mf.py
from odoo import models, fields, api

_MF_STATUS_ORDER = [
    'draft',
    'mf_queued',
    'mf_sent',
    'mf_received',
    'mf_dispatched',
    'mf_in_transit',
    'mf_out_for_delivery',
    'mf_delivered',
]

_MF_STATUS_LABEL = {
    'draft': 'Draft',
    'mf_queued': 'Queued',
    'mf_sent': 'Sent to Warehouse',
    'mf_received': 'Received by Warehouse',
    'mf_dispatched': 'Dispatched',
    'mf_in_transit': 'In Transit',
    'mf_out_for_delivery': 'Out for Delivery',
    'mf_delivered': 'Delivered',
}


class SaleOrderMFFields(models.Model):
    _inherit = 'sale.order'

    x_mf_sent = fields.Boolean('Sent to MF', default=False, copy=False)
    x_mf_sent_date = fields.Datetime('MF Sent Date', copy=False)
    x_mf_filename = fields.Char('MF XML Filename', copy=False)
    x_mf_split = fields.Boolean('Split Order', default=False, copy=False,
                                 help='Order was split across multiple MF warehouses (Sprint 2)')

    x_mf_delivery_status = fields.Char(
        'MF Delivery Status',
        compute='_compute_mf_tracking_fields',
        store=False,
        help='Most advanced transport status across all outbound pickings for this order.',
    )
    x_mf_tracking_url = fields.Char(
        'MF Tracking URL',
        compute='_compute_mf_tracking_fields',
        store=False,
        help='Live tracking link from the most recently dispatched outbound picking.',
    )

    @api.depends('picking_ids.x_mf_status', 'picking_ids.x_mf_tracking_url',
                 'picking_ids.x_mf_dispatched_date')
    def _compute_mf_tracking_fields(self):
        for order in self:
            outbound = [
                p for p in order.picking_ids
                if getattr(p, 'picking_type_id', None)
                and getattr(p.picking_type_id, 'picking_type_code', None) == 'outgoing'
                and p.x_mf_status
            ]

            # Most advanced status
            best_status = None
            best_rank = -1
            for p in outbound:
                rank = _MF_STATUS_ORDER.index(p.x_mf_status) if p.x_mf_status in _MF_STATUS_ORDER else -1
                if rank > best_rank:
                    best_rank = rank
                    best_status = p.x_mf_status
            order.x_mf_delivery_status = _MF_STATUS_LABEL.get(best_status, '') if best_status else ''

            # Tracking URL from most recently dispatched picking
            url_pickings = [p for p in outbound if p.x_mf_tracking_url]
            if url_pickings:
                best = max(
                    url_pickings,
                    key=lambda p: (p.x_mf_dispatched_date or '', p.id if hasattr(p, 'id') else 0),
                )
                order.x_mf_tracking_url = best.x_mf_tracking_url
            else:
                order.x_mf_tracking_url = ''
