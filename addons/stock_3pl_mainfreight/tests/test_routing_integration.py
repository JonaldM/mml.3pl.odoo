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


# ---------------------------------------------------------------------------
# Route Engine — additional integration tests
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'odoo_integration')
class TestRouteEngineIntegration(TransactionCase):
    """Integration tests for mf.route.engine using live Odoo ORM.

    Requires odoo-bin --test-enable.  Deselected from the pure-Python pytest run
    via the 'odoo_integration' marker.
    """

    def setUp(self):
        super().setUp()
        # Start from a clean slate — disable all MF warehouses.
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})

        self.wh1 = self.env['stock.warehouse'].search([], limit=1)
        if not self.wh1:
            return

        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        self.wh1.write({
            'x_mf_enabled': True,
            'x_mf_latitude': -36.8485,   # Auckland (north of Hamilton)
            'x_mf_longitude': 174.7633,
        })
        if nz:
            self.wh1.partner_id.country_id = nz

    def _create_storable_product(self, name, default_code=None):
        """Helper: create a storable product for use in tests."""
        vals = {
            'name': name,
            'type': 'product',
        }
        if default_code:
            vals['default_code'] = default_code
        return self.env['product.product'].create(vals)

    def _create_second_warehouse(self, lat, lng, country_code='NZ'):
        """Helper: create a second MF-enabled warehouse."""
        country = self.env['res.country'].search([('code', '=', country_code)], limit=1)
        partner = self.env['res.partner'].create({
            'name': 'Test WH2 Partner',
            'country_id': country.id if country else False,
        })
        wh2 = self.env['stock.warehouse'].create({
            'name': 'Test Warehouse 2',
            'code': 'TW2',
            'partner_id': partner.id,
        })
        wh2.write({
            'x_mf_enabled': True,
            'x_mf_latitude': lat,
            'x_mf_longitude': lng,
        })
        return wh2

    def test_single_product_routes_to_nearest_warehouse(self):
        """Single storable product routes to the nearest MF warehouse.

        wh1 is set at Auckland coords (~370 km from Wellington).
        wh2 is created at Wellington coords (~2 km from the partner).
        The partner is placed at Wellington.  The engine should prefer wh2.
        """
        if not self.wh1:
            return

        # Create a second warehouse at Wellington (closer to the partner)
        wh2 = self._create_second_warehouse(lat=-41.2865, lng=174.7762)

        product = self._create_storable_product('Widget', default_code='WIDG-001')

        # Put stock of the product at wh2 only
        self.env['stock.quant'].create({
            'product_id': product.id,
            'location_id': wh2.lot_stock_id.id,
            'quantity': 100.0,
        })

        # Create a partner near Wellington
        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        partner = self.env['res.partner'].create({
            'name': 'Wellington Customer',
            'partner_latitude': -41.2865,
            'partner_longitude': 174.7762,
            'country_id': nz.id if nz else False,
        })

        # Confirmed sale order
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'partner_shipping_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 5.0,
                'price_unit': 10.0,
            })],
        })
        order.action_confirm()

        assignments = self.env['mf.route.engine'].route_order(order)

        self.assertEqual(len(assignments), 1, 'Expected a single warehouse assignment')
        self.assertEqual(
            assignments[0]['warehouse'].id, wh2.id,
            'Nearest warehouse (wh2 at Wellington) should be selected',
        )

    def test_multi_product_greedy_split(self):
        """Greedy split: product A only at wh1, product B only at wh2 → two assignments."""
        if not self.wh1:
            return

        wh2 = self._create_second_warehouse(lat=-41.2865, lng=174.7762)

        prod_a = self._create_storable_product('Product A', default_code='PROD-A')
        prod_b = self._create_storable_product('Product B', default_code='PROD-B')

        # Stock A at wh1 only, stock B at wh2 only
        self.env['stock.quant'].create({
            'product_id': prod_a.id,
            'location_id': self.wh1.lot_stock_id.id,
            'quantity': 50.0,
        })
        self.env['stock.quant'].create({
            'product_id': prod_b.id,
            'location_id': wh2.lot_stock_id.id,
            'quantity': 50.0,
        })

        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        partner = self.env['res.partner'].create({
            'name': 'Split Test Customer',
            'partner_latitude': -37.7870,
            'partner_longitude': 175.2793,
            'country_id': nz.id if nz else False,
        })

        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'partner_shipping_id': partner.id,
            'order_line': [
                (0, 0, {'product_id': prod_a.id, 'product_uom_qty': 10.0, 'price_unit': 5.0}),
                (0, 0, {'product_id': prod_b.id, 'product_uom_qty': 10.0, 'price_unit': 5.0}),
            ],
        })
        order.action_confirm()

        assignments = self.env['mf.route.engine'].route_order(order)

        self.assertGreaterEqual(
            len(assignments), 2,
            'Greedy split should produce at least two assignments when stock is at different warehouses',
        )
        assigned_warehouses = {a['warehouse'].id for a in assignments}
        self.assertIn(self.wh1.id, assigned_warehouses, 'wh1 should be in assignments (has prod_a)')
        self.assertIn(wh2.id, assigned_warehouses, 'wh2 should be in assignments (has prod_b)')

    def test_empty_order_returns_empty_list(self):
        """Sale order with only service lines → route_order returns []."""
        if not self.wh1:
            return

        service_product = self.env['product.product'].create({
            'name': 'Consulting Service',
            'type': 'service',
        })

        partner = self.env['res.partner'].create({
            'name': 'Service Customer',
            'partner_latitude': -37.7870,
            'partner_longitude': 175.2793,
        })

        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'partner_shipping_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': service_product.id,
                'product_uom_qty': 1.0,
                'price_unit': 100.0,
            })],
        })
        order.action_confirm()

        assignments = self.env['mf.route.engine'].route_order(order)
        self.assertEqual(assignments, [], 'Orders with no storable lines should return []')


# ---------------------------------------------------------------------------
# Split Engine — integration tests
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'odoo_integration')
class TestSplitEngineIntegration(TransactionCase):
    """Integration tests for mf.split.engine using live Odoo ORM.

    Requires odoo-bin --test-enable.  Deselected from the pure-Python pytest run.
    """

    def setUp(self):
        super().setUp()
        self.env['stock.warehouse'].search([]).write({'x_mf_enabled': False})

        self.wh1 = self.env['stock.warehouse'].search([], limit=1)
        if not self.wh1:
            return

        nz = self.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        self.wh1.write({
            'x_mf_enabled': True,
            'x_mf_latitude': -37.7870,
            'x_mf_longitude': 175.2793,
        })
        if nz:
            self.wh1.partner_id.country_id = nz
        self.nz = nz

    def _make_confirmed_order(self, partner, product, qty=5.0):
        """Create and confirm a sale order with one storable line."""
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'partner_shipping_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': qty,
                'price_unit': 10.0,
            })],
        })
        order.action_confirm()
        return order

    def test_single_assignment_sets_auto_closest(self):
        """Single-warehouse assignment → picking gets x_mf_routed_by='auto_closest'."""
        if not self.wh1:
            return

        product = self.env['product.product'].create({'name': 'Routed Product', 'type': 'product'})
        self.env['stock.quant'].create({
            'product_id': product.id,
            'location_id': self.wh1.lot_stock_id.id,
            'quantity': 100.0,
        })

        partner = self.env['res.partner'].create({
            'name': 'Routing Test Partner',
            'partner_latitude': -37.7870,
            'partner_longitude': 175.2793,
            'country_id': self.nz.id if self.nz else False,
        })

        order = self._make_confirmed_order(partner, product)
        if not order.picking_ids:
            return

        # Single assignment — one warehouse can fill the whole order
        assignment = [{'warehouse': self.wh1, 'lines': [(product, 5.0)]}]
        pickings = self.env['mf.split.engine'].apply_routing(order, assignment)

        for picking in pickings:
            self.assertEqual(
                picking.x_mf_routed_by, 'auto_closest',
                'Single-warehouse routing should set x_mf_routed_by to auto_closest',
            )

    def test_split_assignment_sets_x_mf_split_true(self):
        """Two-warehouse assignment → x_mf_split=True on the sale order."""
        if not self.wh1:
            return

        # Create a second MF-enabled warehouse
        country = self.nz
        partner2 = self.env['res.partner'].create({
            'name': 'WH2 Partner',
            'country_id': country.id if country else False,
        })
        wh2 = self.env['stock.warehouse'].create({
            'name': 'Split Test WH2',
            'code': 'ST2',
            'partner_id': partner2.id,
        })
        wh2.write({
            'x_mf_enabled': True,
            'x_mf_latitude': -41.2865,
            'x_mf_longitude': 174.7762,
        })

        prod_a = self.env['product.product'].create({'name': 'Split Prod A', 'type': 'product'})
        prod_b = self.env['product.product'].create({'name': 'Split Prod B', 'type': 'product'})

        customer = self.env['res.partner'].create({
            'name': 'Split Customer',
            'partner_latitude': -39.0,
            'partner_longitude': 176.0,
            'country_id': self.nz.id if self.nz else False,
        })

        order = self.env['sale.order'].create({
            'partner_id': customer.id,
            'partner_shipping_id': customer.id,
            'order_line': [
                (0, 0, {'product_id': prod_a.id, 'product_uom_qty': 2.0, 'price_unit': 1.0}),
                (0, 0, {'product_id': prod_b.id, 'product_uom_qty': 2.0, 'price_unit': 1.0}),
            ],
        })
        order.action_confirm()

        # Simulate two-warehouse assignment
        assignments = [
            {'warehouse': self.wh1, 'lines': [(prod_a, 2.0)]},
            {'warehouse': wh2, 'lines': [(prod_b, 2.0)]},
        ]
        self.env['mf.split.engine'].apply_routing(order, assignments)

        order.invalidate_cache(['x_mf_split'])
        self.assertTrue(order.x_mf_split, 'x_mf_split should be True after a split routing')

    def test_approve_cross_border_advances_status(self):
        """Picking in mf_held_review → action_approve_cross_border → status mf_queued."""
        if not self.wh1:
            return

        product = self.env['product.product'].create({
            'name': 'Cross Border Product',
            'type': 'product',
        })

        # Create a partner in a different country to trigger cross-border
        au = self.env['res.country'].search([('code', '=', 'AU')], limit=1)
        if not au:
            return

        au_partner = self.env['res.partner'].create({
            'name': 'AU Customer',
            'country_id': au.id,
            'partner_latitude': -33.8688,
            'partner_longitude': 151.2093,
        })

        order = self._make_confirmed_order(au_partner, product)
        if not order.picking_ids:
            return

        # Manually set the picking to mf_held_review (simulating cross-border detection)
        picking = order.picking_ids[0]
        picking.write({'x_mf_status': 'mf_held_review', 'x_mf_cross_border': True})

        picking.action_approve_cross_border()

        picking.invalidate_cache(['x_mf_status'])
        self.assertEqual(
            picking.x_mf_status, 'mf_queued',
            'action_approve_cross_border should advance status from mf_held_review to mf_queued',
        )


# ---------------------------------------------------------------------------
# Inbound Cron — ORM integration tests
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'odoo_integration')
class TestInboundCronIntegration(TransactionCase):
    """Integration tests for mf.inbound.cron._reconcile_sent_orders using live ORM.

    Requires odoo-bin --test-enable.  Deselected from the pure-Python pytest run.
    """

    def setUp(self):
        super().setUp()
        self.wh = self.env['stock.warehouse'].search([], limit=1)
        if not self.wh:
            return

        # A storable product for test pickings
        self.product = self.env['product.product'].create({
            'name': 'Reconcile Test Product',
            'type': 'product',
        })

        partner = self.env['res.partner'].create({'name': 'Reconcile Test Partner'})
        self.partner = partner

    def _create_picking(self):
        """Create a minimal outbound picking for the default warehouse."""
        if not self.wh:
            return self.env['stock.picking']
        picking_type = self.wh.out_type_id
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'partner_id': self.partner.id,
            'location_id': self.wh.lot_stock_id.id,
            'location_dest_id': self.partner.property_stock_customer.id,
        })
        return picking

    def test_reconcile_flags_stale_mf_sent_picking(self):
        """Picking with x_mf_status='mf_sent' + write_date 3 days ago + no connote
        → _reconcile_sent_orders() sets status to 'mf_exception'."""
        if not self.wh:
            return

        picking = self._create_picking()
        picking.write({'x_mf_status': 'mf_sent', 'x_mf_connote': False})

        # Backdate write_date to 3 days ago so it falls outside any threshold
        self.env.cr.execute(
            "UPDATE stock_picking SET write_date = NOW() - INTERVAL '3 days' WHERE id = %s",
            [picking.id],
        )
        picking.invalidate_cache(['write_date'])

        # Use a tight threshold (1 hour) so the 3-day-old picking is definitely stale
        self.env['ir.config_parameter'].sudo().set_param(
            'stock_3pl_mainfreight.reconcile_hours', '1'
        )

        self.env['mf.inbound.cron']._reconcile_sent_orders()
        picking.invalidate_cache(['x_mf_status'])

        self.assertEqual(
            picking.x_mf_status, 'mf_exception',
            'Stale mf_sent picking without connote should be flagged as mf_exception',
        )

    def test_reconcile_skips_picking_with_connote(self):
        """Picking with x_mf_status='mf_sent' + old write_date but x_mf_connote set
        → _reconcile_sent_orders() does NOT change the status."""
        if not self.wh:
            return

        picking = self._create_picking()
        picking.write({'x_mf_status': 'mf_sent', 'x_mf_connote': 'CON001'})

        # Backdate write_date to 3 days ago
        self.env.cr.execute(
            "UPDATE stock_picking SET write_date = NOW() - INTERVAL '3 days' WHERE id = %s",
            [picking.id],
        )
        picking.invalidate_cache(['write_date'])

        self.env['ir.config_parameter'].sudo().set_param(
            'stock_3pl_mainfreight.reconcile_hours', '1'
        )

        self.env['mf.inbound.cron']._reconcile_sent_orders()
        picking.invalidate_cache(['x_mf_status'])

        self.assertEqual(
            picking.x_mf_status, 'mf_sent',
            'Picking with a connote should not be flagged by reconciliation',
        )


# ---------------------------------------------------------------------------
# Credential Store — ORM integration tests
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'odoo_integration')
class TestCredentialStoreIntegration(TransactionCase):
    """Integration tests for Fernet encryption via 3pl.connector.

    Requires odoo-bin --test-enable.  Deselected from the pure-Python pytest run.
    """

    def setUp(self):
        super().setUp()
        self.wh = self.env['stock.warehouse'].search([], limit=1)
        if not self.wh:
            return

    def _create_connector(self, name='Test MF Connector'):
        """Create a minimal 3pl.connector record for credential testing."""
        if not self.wh:
            return self.env['3pl.connector']
        return self.env['3pl.connector'].create({
            'name': name,
            'warehouse_id': self.wh.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
        })

    def test_encrypt_decrypt_roundtrip_via_connector(self):
        """Writing api_secret through the ORM encrypts it; get_credential() returns plaintext."""
        if not self.wh:
            return

        connector = self._create_connector()
        if not connector:
            return

        plaintext = 'my-secret'
        connector.write({'api_secret': plaintext})

        # The raw stored value should be encrypted (starts with 'enc:')
        raw = connector.api_secret
        self.assertTrue(
            raw.startswith('enc:'),
            f'Stored api_secret should be encrypted (starts with "enc:"), got: {raw!r}',
        )

        # get_credential() should decrypt back to the original value
        decrypted = connector.get_credential('api_secret')
        self.assertEqual(
            decrypted, plaintext,
            'get_credential() must return the original plaintext after encrypt/decrypt roundtrip',
        )

    def test_legacy_plaintext_passthrough(self):
        """A plaintext value stored without encryption is returned unchanged by get_credential()."""
        if not self.wh:
            return

        connector = self._create_connector('Legacy Connector')
        if not connector:
            return

        # Bypass the write() override by writing directly via SQL so no encryption occurs
        plaintext = 'legacy-plain-secret'
        self.env.cr.execute(
            "UPDATE three_pl_connector SET api_secret = %s WHERE id = %s",
            [plaintext, connector.id],
        )
        connector.invalidate_cache(['api_secret'])

        # get_credential() must detect the absence of the 'enc:' prefix and
        # pass the value through unchanged (decrypt_credential legacy path)
        result = connector.get_credential('api_secret')
        self.assertEqual(
            result, plaintext,
            'Legacy plaintext api_secret (no enc: prefix) should be returned unchanged',
        )
