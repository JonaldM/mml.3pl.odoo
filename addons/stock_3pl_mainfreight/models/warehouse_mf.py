from odoo import models, fields


class StockWarehouseMF(models.Model):
    _inherit = 'stock.warehouse'

    x_mf_warehouse_code = fields.Char('MF Warehouse Code', help='MF warehouse identifier, e.g. "99"')
    x_mf_customer_id = fields.Char('MF Customer ID', help='MF account customer ID, e.g. "123456"')
    x_mf_enabled = fields.Boolean(
        'MF-Managed Warehouse',
        default=False,
        help='Include this warehouse in Mainfreight routing and push logic.',
    )
    # Routing fields (used by Sprint 2 routing engine -- declared here to avoid migrations)
    x_mf_latitude = fields.Float('Latitude', digits=(9, 6))
    x_mf_longitude = fields.Float('Longitude', digits=(9, 6))
