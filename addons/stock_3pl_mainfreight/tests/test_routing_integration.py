from odoo.tests import tagged, TransactionCase
from odoo.exceptions import UserError


@tagged('post_install', '-at_install', 'routing')
class TestRoutingIntegration(TransactionCase):

    def setUp(self):
        super().setUp()
        # Disable MF on all warehouses first for a clean slate
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})

        self.wh_ham = self.env['stock.warehouse'].search([], limit=1)
        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        self.wh_ham.write({
            'x_mf_enabled': True,
            'x_mf_latitude': -37.7870,
            'x_mf_longitude': 175.2793,
        })
        if nz:
            self.wh_ham.partner_id.country_id = nz

    def test_no_mf_warehouses_raises(self):
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order:
            return
        with self.assertRaises(UserError):
            self.env['mf.route.engine'].route_order(order)

    def test_order_with_no_lat_lng_falls_back_to_first_warehouse(self):
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order:
            return
        order.partner_shipping_id.write({'partner_latitude': 0, 'partner_longitude': 0})
        assignments = self.env['mf.route.engine'].route_order(order)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]['warehouse'], self.wh_ham)

    def test_cross_border_flag_is_set_on_split_apply(self):
        order = self.env['sale.order'].search([('state', '=', 'sale')], limit=1)
        if not order or not order.picking_ids:
            return
        au = self.env['res.country'].search([('code', '=', 'AU')], limit=1)
        if not au:
            return
        order.partner_shipping_id.country_id = au
        assignments = self.env['mf.route.engine'].route_order(order)
        pickings = self.env['mf.split.engine'].apply_routing(order, assignments)
        for picking in pickings:
            self.assertTrue(picking.x_mf_cross_border)
            self.assertEqual(picking.x_mf_status, 'mf_held_review')
