# addons/stock_3pl_mainfreight/models/sale_order_mf.py
from odoo import models, fields


class SaleOrderMFFields(models.Model):
    _inherit = 'sale.order'

    x_mf_sent = fields.Boolean('Sent to MF', default=False, copy=False)
    x_mf_sent_date = fields.Datetime('MF Sent Date', copy=False)
    x_mf_filename = fields.Char('MF XML Filename', copy=False)
    x_mf_split = fields.Boolean('Split Order', default=False,
                                 help='Order was split across multiple MF warehouses (Sprint 2)')
