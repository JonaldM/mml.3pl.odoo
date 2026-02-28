# addons/stock_3pl_core/tests/test_message.py
import importlib.util
import os
import sys
import unittest

from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'message')
class TestMessage(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test Connector',
            'warehouse_id': warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })

    def test_outbound_message_create(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'ref_model': 'sale.order',
            'ref_id': 1,
        })
        self.assertEqual(msg.state, 'draft')
        self.assertEqual(msg.retry_count, 0)

    def test_outbound_state_transitions(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
        })
        msg.action_queue()
        self.assertEqual(msg.state, 'queued')
        msg.action_sending()
        self.assertEqual(msg.state, 'sending')
        msg.action_sent()
        self.assertEqual(msg.state, 'sent')
        self.assertTrue(msg.sent_at)
        msg.action_acknowledged()
        self.assertEqual(msg.state, 'acknowledged')
        self.assertTrue(msg.acked_at)

    def test_message_fail_and_retry(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
        })
        msg.action_queue()
        msg.action_sending()
        msg.action_fail('Timeout')
        self.assertEqual(msg.state, 'queued')
        self.assertEqual(msg.retry_count, 1)
        self.assertEqual(msg.last_error, 'Timeout')

    def test_message_dead_after_max_retries(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'retry_count': 2,
        })
        msg.action_queue()
        msg.action_sending()
        msg.action_fail('Final failure')
        self.assertEqual(msg.state, 'dead')
        self.assertEqual(msg.last_error, 'Final failure')

    def test_action_requeue_resets_retry_state(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'state': 'dead',
            'retry_count': 3,
            'last_error': 'previous error',
        })
        msg.action_requeue()
        self.assertEqual(msg.state, 'queued')
        self.assertEqual(msg.retry_count, 0)
        self.assertFalse(msg.last_error)

    def test_validation_error_goes_straight_to_dead(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
        })
        msg.action_queue()
        msg.action_sending()
        msg.action_validation_fail('Bad payload: missing ProductCode')
        self.assertEqual(msg.state, 'dead')
        self.assertEqual(msg.retry_count, 0)  # no retry consumed


# ---------------------------------------------------------------------------
# Pure-Python tests for _detect_inbound_type — no Odoo runtime required.
#
# The static method has zero Odoo dependencies.  conftest.py (at repo root)
# installs lightweight odoo stubs into sys.modules before collection, so the
# module-level ``from odoo import models, fields, api`` in message.py resolves
# without a live Odoo instance.  We load message.py via importlib to extract
# the static method directly from the class, keeping these tests completely
# independent of the Odoo test runner.
#
# These tests are skipped when running under a real Odoo instance (odoo has
# a real __file__ and no _stubbed marker).
# ---------------------------------------------------------------------------

def _load_detect_inbound_type():
    """Load ThreePlMessage._detect_inbound_type from message.py via importlib.

    conftest.py has already installed odoo stubs into sys.modules so the
    top-level ``from odoo import models, fields, api`` in message.py will
    resolve without error.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    message_path = os.path.normpath(
        os.path.join(here, '..', 'models', 'message.py')
    )
    spec = importlib.util.spec_from_file_location('_message_module_pure', message_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ThreePlMessage._detect_inbound_type


def _running_under_real_odoo():
    """Return True only when odoo is a real installed package, not a stub."""
    odoo_mod = sys.modules.get('odoo')
    if odoo_mod is None:
        return False
    # conftest stubs set _stubbed = True; real odoo has __file__
    if getattr(odoo_mod, '_stubbed', False):
        return False
    return hasattr(odoo_mod, '__file__') and odoo_mod.__file__ is not None


@unittest.skipIf(_running_under_real_odoo(), 'Requires stub environment; skip under real Odoo')
class TestDetectInboundTypePure(unittest.TestCase):
    """Pure-Python unit tests for ThreePlMessage._detect_inbound_type."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Wrap in staticmethod so that Python's descriptor protocol does not
        # rebind the function as an instance method when accessed via self.
        cls.detect = staticmethod(_load_detect_inbound_type())

    # 1. SOH CSV — header contains 'Product,WarehouseID,StockOnHand'
    def test_soh_csv_returns_inventory_report(self):
        raw = 'Product,WarehouseID,StockOnHand,Reserved,Available\n' \
              'SKU001,NZ01,100,0,100\n'
        self.assertEqual(self.detect(raw), 'inventory_report')

    # 2. ACKH CSV — header contains 'ClientOrderNumber'
    def test_ackh_csv_returns_so_acknowledgement(self):
        raw = 'ClientOrderNumber,OrderStatus,WarehouseID,ReceivedDate\n' \
              'SO/2026/0001,Received,NZ01,2026-02-28\n'
        self.assertEqual(self.detect(raw), 'so_acknowledgement')

    # 3. ACKL CSV — header uses lowercase 'clientordernumber' (case-insensitive)
    def test_ackl_csv_lowercase_header_returns_so_acknowledgement(self):
        raw = 'clientordernumber,linestatus,sku,qty\n' \
              'SO/2026/0001,Received,SKU001,5\n'
        self.assertEqual(self.detect(raw), 'so_acknowledgement')

    # 4. SO Confirmation XML — contains <SCH
    def test_so_confirmation_xml_returns_so_confirmation(self):
        raw = '<?xml version="1.0"?>\n<SCH>\n  <Order>123</Order>\n</SCH>\n'
        self.assertEqual(self.detect(raw), 'so_confirmation')

    # 5. Unrecognised XML — starts with '<' but no known root element
    def test_unrecognised_xml_returns_none(self):
        raw = '<?xml version="1.0"?>\n<UnknownDocument>\n</UnknownDocument>\n'
        self.assertIsNone(self.detect(raw))

    # 6. Empty string — non-XML, no header line → falls through to inventory_report
    def test_empty_string_returns_inventory_report(self):
        self.assertEqual(self.detect(''), 'inventory_report')
