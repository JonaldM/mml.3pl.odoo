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
from unittest.mock import MagicMock, patch


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
        instance.ensure_one = MagicMock()
        instance.warehouse_id = MagicMock()
        instance.warehouse_id.id = 1
        instance.name = 'SO001'
        instance.id = 1

        # Call the method under test (no argument — operates on self)
        instance._queue_mf_sales_order()

        # create should never have been called — early return on no connector
        mock_message_model.create.assert_not_called()


def _make_so_module_stub(mock_doc_instance):
    """Return a stub sys.modules entry for the sales_order document module."""
    import unittest.mock as um
    mock_so_module = types.ModuleType(
        'odoo.addons.stock_3pl_mainfreight.document.sales_order'
    )
    mock_so_module.SalesOrderDocument = um.MagicMock(return_value=mock_doc_instance)
    return mock_so_module


class TestQueueMFSalesOrderHappyPath(unittest.TestCase):

    def test_queue_mf_sales_order_creates_message_when_connector_found(self):
        """Happy path: connector found, no existing message → 3pl.message.create called."""
        # Return a single MagicMock (truthy, has .id) to simulate limit=1 recordset
        mock_connector = MagicMock()
        mock_connector.id = 10
        mock_connector.name = 'MF Test'

        mock_doc = MagicMock()
        mock_doc.get_idempotency_key.return_value = 'ikey123'
        mock_doc.build_outbound.return_value = '<Order/>'

        mock_message_model = MagicMock()
        # idempotency search returns falsy (no existing message)
        empty = MagicMock()
        empty.__bool__ = MagicMock(return_value=False)
        mock_message_model.search.return_value = empty

        def env_lookup(key):
            if key == '3pl.connector':
                mock_connector_model = MagicMock()
                mock_connector_model.search.return_value = mock_connector
                return mock_connector_model
            if key == '3pl.message':
                return mock_message_model
            return MagicMock()

        mock_env = MagicMock()
        mock_env.__getitem__ = MagicMock(side_effect=env_lookup)

        instance = SaleOrderMF.__new__(SaleOrderMF)
        instance.env = mock_env
        instance.ensure_one = MagicMock()
        instance.warehouse_id = MagicMock()
        instance.warehouse_id.id = 1
        instance.name = 'S00001'
        instance.id = 42

        mock_so_module = _make_so_module_stub(mock_doc)
        import unittest.mock as um
        with um.patch.dict(sys.modules, {
            'odoo.addons.stock_3pl_mainfreight.document.sales_order': mock_so_module,
        }):
            instance._queue_mf_sales_order()

        mock_message_model.create.assert_called_once()
        call_kwargs = mock_message_model.create.call_args[0][0]
        self.assertEqual(call_kwargs['state'], 'queued')
        self.assertEqual(call_kwargs['document_type'], 'sales_order')

    def test_queue_mf_sales_order_idempotency_skips_if_message_exists(self):
        """Idempotency: existing non-dead message → create NOT called."""
        # Return a single MagicMock (truthy, has .id) to simulate limit=1 recordset
        mock_connector = MagicMock()
        mock_connector.id = 10

        mock_doc = MagicMock()
        mock_doc.get_idempotency_key.return_value = 'ikey123'

        mock_message_model = MagicMock()
        # existing message found — truthy recordset
        existing = MagicMock()
        existing.__bool__ = MagicMock(return_value=True)
        mock_message_model.search.return_value = existing

        def env_lookup(key):
            if key == '3pl.connector':
                mock_connector_model = MagicMock()
                mock_connector_model.search.return_value = mock_connector
                return mock_connector_model
            if key == '3pl.message':
                return mock_message_model
            return MagicMock()

        mock_env = MagicMock()
        mock_env.__getitem__ = MagicMock(side_effect=env_lookup)

        instance = SaleOrderMF.__new__(SaleOrderMF)
        instance.env = mock_env
        instance.ensure_one = MagicMock()
        instance.warehouse_id = MagicMock()
        instance.warehouse_id.id = 1
        instance.name = 'S00001'
        instance.id = 42

        mock_so_module = _make_so_module_stub(mock_doc)
        import unittest.mock as um
        with um.patch.dict(sys.modules, {
            'odoo.addons.stock_3pl_mainfreight.document.sales_order': mock_so_module,
        }):
            instance._queue_mf_sales_order()

        mock_message_model.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
