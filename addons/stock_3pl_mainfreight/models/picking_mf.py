# addons/stock_3pl_mainfreight/models/picking_mf.py
from odoo import models, fields

MF_STATUS = [
    ('draft', 'Draft'),
    ('mf_held_review', 'Held — Cross-Border Review'),  # V3: manual release required
    ('mf_queued', 'Queued for 3PL'),
    ('mf_sent', 'Sent to 3PL'),
    ('mf_received', 'Received by MF'),
    ('mf_dispatched', 'Dispatched'),
    ('mf_in_transit', 'In Transit'),
    ('mf_out_for_delivery', 'Out for Delivery'),
    ('mf_delivered', 'Delivered'),
    ('mf_exception', 'Exception'),
]

MF_ROUTED_BY = [
    ('manual', 'Manual'),
    ('auto_closest', 'Auto — Closest Warehouse'),
    ('auto_split', 'Auto — Split Order'),
]


class StockPickingMF(models.Model):
    _inherit = 'stock.picking'

    x_mf_status = fields.Selection(MF_STATUS, 'MF Status', default='draft', index=True, copy=False)
    x_mf_connote = fields.Char('Connote No.', copy=False)
    x_mf_pick_id = fields.Char('MF Pick ID', copy=False)
    x_mf_pod_url = fields.Char('POD URL', copy=False)
    x_mf_signed_by = fields.Char('Signed By', copy=False)
    x_mf_dispatched_date = fields.Datetime('Dispatched Date', copy=False)
    x_mf_delivered_date = fields.Datetime('Delivered Date', copy=False)
    # Sprint 2 routing fields — declared here to avoid migration on routing engine rollout
    x_mf_routed_by = fields.Selection(MF_ROUTED_BY, 'Routing Method', readonly=True, copy=False)
    x_mf_cross_border = fields.Boolean('Cross-Border', default=False)
