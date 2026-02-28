# addons/stock_3pl_mainfreight/tests/test_sale_order_hook.py
"""
Pure-Python structural and logic tests for sale_order_hook.py.
No Odoo runtime required — Odoo dependencies are mocked.
"""
import sys
import types
import importlib.util
import pathlib
import unittest
from unittest.mock import MagicMock


_BASE = pathlib.Path(__file__).parent.parent / 'models'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _BASE / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_hook_mod = _load('sale_order_hook')
SaleOrderMF = _hook_mod.SaleOrderMF


class TestSaleOrderMFInherit(unittest.TestCase):

    def test_inherit_is_sale_order(self):
        """Test 10: SaleOrderMF._inherit == 'sale.order'."""
        self.assertEqual(SaleOrderMF._inherit, 'sale.order')


class TestQueueMFSalesOrderNoConnector(unittest.TestCase):

    def test_returns_early_when_no_connector(self):
        """Test 9: _queue_mf_sales_order returns early if no connector found."""
        instance = SaleOrderMF.__new__(SaleOrderMF)

        # Build a falsy connector search result (no connector found)
        empty_connector = MagicMock()
        empty_connector.__bool__ = MagicMock(return_value=False)

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = empty_connector

        mock_message_model = MagicMock()

        # Wire env['model'] lookups via side_effect
        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            if key == '3pl.message':
                return mock_message_model
            return MagicMock()

        mock_env = MagicMock()
        mock_env.__getitem__ = MagicMock(side_effect=env_lookup)
        instance.env = mock_env

        mock_order = MagicMock()
        mock_order.warehouse_id.id = 1
        mock_order.name = 'SO001'

        # Call the method under test
        instance._queue_mf_sales_order(mock_order)

        # create should never have been called — early return on no connector
        mock_message_model.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
