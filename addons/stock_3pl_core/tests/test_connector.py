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

    def test_category_ids_default_empty(self):
        """product_category_ids defaults to empty (no linked records) on a new connector.
        An empty Many2many means the connector acts as a catch-all for any product category."""
        connector = self.env['3pl.connector'].create({
            'name': 'Catch-all Connector',
            'warehouse_id': self.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
        })
        self.assertFalse(connector.product_category_ids)


# ---------------------------------------------------------------------------
# Pure-Python structural tests — no Odoo runtime required
# Run with: python -m pytest addons/stock_3pl_core/tests/test_connector.py -v
# ---------------------------------------------------------------------------

import ast
import os
import pytest


def test_connector_create_accepts_vals_list():
    """Verify create() is decorated with @api.model_create_multi signature."""
    src_path = os.path.join(os.path.dirname(__file__), '..', 'models', 'connector.py')
    with open(src_path, encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'create':
            for dec in node.decorator_list:
                if isinstance(dec, ast.Attribute) and dec.attr == 'model_create_multi':
                    args = [a.arg for a in node.args.args]
                    assert 'vals_list' in args, f"create() should accept vals_list, got {args}"
                    return
            pytest.fail("create() does not have @api.model_create_multi decorator")
    pytest.fail("No create() method found in connector.py")


def test_connector_mf_create_accepts_vals_list():
    """Verify connector_mf create() is decorated with @api.model_create_multi."""
    src_path = os.path.join(os.path.dirname(__file__), '..', '..', 'stock_3pl_mainfreight', 'models', 'connector_mf.py')
    with open(src_path, encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'create':
            for dec in node.decorator_list:
                if isinstance(dec, ast.Attribute) and dec.attr == 'model_create_multi':
                    args = [a.arg for a in node.args.args]
                    assert 'vals_list' in args, f"create() should accept vals_list, got {args}"
                    return
            pytest.fail("connector_mf create() missing @api.model_create_multi")
    pytest.fail("No create() found in connector_mf.py")


def test_connector_freightways_create_accepts_vals_list():
    """Verify connector_freightways create() is decorated with @api.model_create_multi."""
    src_path = os.path.join(os.path.dirname(__file__), '..', '..', 'stock_3pl_mainfreight', 'models', 'connector_freightways.py')
    with open(src_path, encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'create':
            for dec in node.decorator_list:
                if isinstance(dec, ast.Attribute) and dec.attr == 'model_create_multi':
                    args = [a.arg for a in node.args.args]
                    assert 'vals_list' in args, f"create() should accept vals_list, got {args}"
                    return
            pytest.fail("connector_freightways create() missing @api.model_create_multi")
    pytest.fail("No create() found in connector_freightways.py")


def test_connector_create_loops_over_vals_list():
    """Verify create() body iterates vals_list (not a single vals dict)."""
    import os, ast
    src_path = os.path.join(os.path.dirname(__file__), '..', 'models', 'connector.py')
    with open(src_path, encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'create':
            # Find a For loop in the body
            for_loops = [n for n in ast.walk(node) if isinstance(n, ast.For)]
            assert for_loops, "create() must contain a for loop over vals_list"
            # The for loop target must be 'vals' iterating over 'vals_list'
            loop = for_loops[0]
            assert isinstance(loop.target, ast.Name) and loop.target.id == 'vals', \
                "Loop variable must be 'vals'"
            assert isinstance(loop.iter, ast.Name) and loop.iter.id == 'vals_list', \
                "Loop must iterate over 'vals_list'"
            return
    pytest.fail("No create() method found in connector.py")
