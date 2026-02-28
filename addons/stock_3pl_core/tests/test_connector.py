from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'connector')
class TestConnector(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)

    def test_connector_create(self):
        connector = self.env['3pl.connector'].create({
            'name': 'MF NZ Test',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.assertEqual(connector.warehouse_partner, 'mainfreight')
        self.assertEqual(connector.environment, 'test')
        self.assertTrue(connector.active)

    def test_connector_requires_warehouse(self):
        with self.assertRaises(Exception):
            self.env['3pl.connector'].create({
                'name': 'Bad Connector',
                'warehouse_partner': 'mainfreight',
                'transport': 'rest_api',
            })

    def test_connector_last_soh_applied_at_default_none(self):
        connector = self.env['3pl.connector'].create({
            'name': 'MF NZ Test',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'sftp',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.assertFalse(connector.last_soh_applied_at)

    def test_get_transport_rest(self):
        connector = self.env['3pl.connector'].create({
            'name': 'MF REST Test',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
            'api_url': 'https://test.example.com',
            'api_secret': 'secret',
        })
        from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport
        transport = connector.get_transport()
        self.assertIsInstance(transport, RestTransport)

    def test_priority_default(self):
        connector = self.env['3pl.connector'].create({
            'name': 'Priority Default Test',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
        })
        self.assertEqual(connector.priority, 10)

    def test_priority_ordering(self):
        """Lower priority integer should sort first."""
        low = self.env['3pl.connector'].create({
            'name': 'Low Priority',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'priority': 5,
        })
        high = self.env['3pl.connector'].create({
            'name': 'High Priority',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'freightways',
            'transport': 'rest_api',
            'environment': 'test',
            'priority': 20,
        })
        result = self.env['3pl.connector'].search([
            ('warehouse_id', '=', self.warehouse.id),
            ('id', 'in', [low.id, high.id]),
        ], order='priority asc', limit=1)
        self.assertEqual(result.id, low.id)

    def test_category_catch_all_empty(self):
        """Connector with no categories configured is a catch-all."""
        connector = self.env['3pl.connector'].create({
            'name': 'Catch-all Connector',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
        })
        self.assertFalse(connector.product_category_ids)
