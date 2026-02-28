# addons/stock_3pl_core/tests/test_message.py
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'message')
class TestMessage(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test Connector',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
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
