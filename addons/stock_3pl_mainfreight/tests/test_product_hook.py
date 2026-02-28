# addons/stock_3pl_mainfreight/tests/test_product_hook.py
"""
Pure-Python structural and logic tests for product_hook.py.
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


_hook_mod = _load('product_hook')
SYNC_FIELDS = _hook_mod.SYNC_FIELDS
ProductProductMF = _hook_mod.ProductProductMF


class TestSyncFields(unittest.TestCase):

    def test_sync_fields_contains_default_code(self):
        """Test 11: SYNC_FIELDS contains 'default_code'."""
        self.assertIn('default_code', SYNC_FIELDS)

    def test_sync_fields_contains_weight(self):
        """Test 12: SYNC_FIELDS contains 'weight'."""
        self.assertIn('weight', SYNC_FIELDS)

    def test_sync_fields_is_set(self):
        """SYNC_FIELDS should be a set for O(1) intersection checks."""
        self.assertIsInstance(SYNC_FIELDS, set)


class TestProductProductMFInherit(unittest.TestCase):

    def test_inherit_is_product_product(self):
        """Test 13: ProductProductMF._inherit == 'product.product'."""
        self.assertEqual(ProductProductMF._inherit, 'product.product')


class TestQueueMFProductSyncNoDefaultCode(unittest.TestCase):

    def test_skips_product_with_no_default_code(self):
        """Test 14: _queue_mf_product_sync skips product with no default_code."""
        instance = ProductProductMF.__new__(ProductProductMF)

        # Build a mock connector
        mock_connector = MagicMock()
        mock_connector.id = 1
        mock_connector.name = 'MF Test'

        mock_connector_model = MagicMock()
        mock_connector_model.search.return_value = [mock_connector]

        mock_message_model = MagicMock()

        # Wire env['model'] lookups via side_effect (correct pattern for MagicMock)
        def env_lookup(key):
            if key == '3pl.connector':
                return mock_connector_model
            if key == '3pl.message':
                return mock_message_model
            return MagicMock()

        mock_env = MagicMock()
        mock_env.__getitem__ = MagicMock(side_effect=env_lookup)
        instance.env = mock_env

        # Product with no default_code
        mock_product = MagicMock()
        mock_product.default_code = False
        mock_product.id = 42

        # Stub out the deferred import in _queue_mf_product_sync
        mock_spec_module = types.ModuleType(
            'odoo.addons.stock_3pl_mainfreight.document.product_spec'
        )
        mock_doc_instance = MagicMock()
        mock_spec_module.ProductSpecDocument = MagicMock(return_value=mock_doc_instance)

        import unittest.mock as um
        with um.patch.dict(sys.modules, {
            'odoo.addons.stock_3pl_mainfreight.document.product_spec': mock_spec_module,
        }):
            instance._queue_mf_product_sync(mock_product)

        # create should never have been called — skipped due to no default_code
        mock_message_model.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
