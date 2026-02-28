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


def _make_instance(default_code='PROD001', product_id=42):
    """
    Build a bare ProductProductMF instance with env wired up.
    Returns (instance, mock_connector, mock_connector_model, mock_message_model,
             mock_doc_instance, mock_spec_module).
    """
    instance = ProductProductMF.__new__(ProductProductMF)

    # Own scalar attributes (used by _queue_mf_product_sync via self.*)
    instance.default_code = default_code
    instance.id = product_id

    # ensure_one is an Odoo Model method not available in the bare __new__ instance
    instance.ensure_one = MagicMock()

    # Build a mock connector
    mock_connector = MagicMock()
    mock_connector.id = 1
    mock_connector.name = 'MF Test'

    mock_connector_model = MagicMock()
    mock_connector_model.search.return_value = [mock_connector]

    mock_message_model = MagicMock()

    def env_lookup(key):
        if key == '3pl.connector':
            return mock_connector_model
        if key == '3pl.message':
            return mock_message_model
        return MagicMock()

    mock_env = MagicMock()
    mock_env.__getitem__ = MagicMock(side_effect=env_lookup)
    instance.env = mock_env

    # Build the ProductSpecDocument stub
    mock_doc_instance = MagicMock()
    mock_doc_instance.make_idempotency_key.return_value = 'ikey-PROD001'
    mock_doc_instance.build_outbound.return_value = 'col1,col2\nv1,v2\n'

    mock_spec_module = types.ModuleType(
        'odoo.addons.stock_3pl_mainfreight.document.product_spec'
    )
    mock_spec_module.ProductSpecDocument = MagicMock(return_value=mock_doc_instance)

    return (
        instance,
        mock_connector,
        mock_connector_model,
        mock_message_model,
        mock_doc_instance,
        mock_spec_module,
    )


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
        (instance, mock_connector, mock_connector_model,
         mock_message_model, mock_doc_instance, mock_spec_module) = _make_instance(
            default_code=False, product_id=42
        )

        import unittest.mock as um
        with um.patch.dict(sys.modules, {
            'odoo.addons.stock_3pl_mainfreight.document.product_spec': mock_spec_module,
        }):
            instance._queue_mf_product_sync()

        # create should never have been called — skipped due to no default_code
        mock_message_model.create.assert_not_called()


class TestQueueMFProductSyncIdempotency(unittest.TestCase):
    """Two-phase idempotency tests for _queue_mf_product_sync."""

    def test_in_flight_message_skips_create(self):
        """Test 15: When an in-flight message exists, 3pl.message.create is NOT called."""
        (instance, mock_connector, mock_connector_model,
         mock_message_model, mock_doc_instance, mock_spec_module) = _make_instance()

        # First search (in_flight) returns a truthy result — skip path
        in_flight_msg = MagicMock()
        mock_message_model.search.return_value = in_flight_msg

        import unittest.mock as um
        with um.patch.dict(sys.modules, {
            'odoo.addons.stock_3pl_mainfreight.document.product_spec': mock_spec_module,
        }):
            instance._queue_mf_product_sync()

        mock_message_model.create.assert_not_called()

    def test_already_sent_queues_update_action(self):
        """Test 16: When no in-flight message but already_sent exists, create is called with action='update'."""
        (instance, mock_connector, mock_connector_model,
         mock_message_model, mock_doc_instance, mock_spec_module) = _make_instance()

        already_sent_msg = MagicMock()
        # search call sequence: 1st call (in_flight) → falsy, 2nd call (already_sent) → truthy
        mock_message_model.search.side_effect = [None, already_sent_msg]

        import unittest.mock as um
        with um.patch.dict(sys.modules, {
            'odoo.addons.stock_3pl_mainfreight.document.product_spec': mock_spec_module,
        }):
            instance._queue_mf_product_sync()

        mock_message_model.create.assert_called_once()
        call_kwargs = mock_message_model.create.call_args[0][0]
        self.assertEqual(call_kwargs['action'], 'update')

    def test_no_prior_message_queues_create_action(self):
        """Test 17: When both in_flight and already_sent are falsy, create is called with action='create'."""
        (instance, mock_connector, mock_connector_model,
         mock_message_model, mock_doc_instance, mock_spec_module) = _make_instance()

        # Both searches return falsy (None)
        mock_message_model.search.side_effect = [None, None]

        import unittest.mock as um
        with um.patch.dict(sys.modules, {
            'odoo.addons.stock_3pl_mainfreight.document.product_spec': mock_spec_module,
        }):
            instance._queue_mf_product_sync()

        mock_message_model.create.assert_called_once()
        call_kwargs = mock_message_model.create.call_args[0][0]
        self.assertEqual(call_kwargs['action'], 'create')


if __name__ == '__main__':
    unittest.main()
