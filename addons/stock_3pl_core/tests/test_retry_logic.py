# addons/stock_3pl_core/tests/test_retry_logic.py
from unittest.mock import patch, MagicMock
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'retry')
class TestRetryLogic(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test',
            'warehouse_id': warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
            'api_url': 'https://test.example.com',
            'api_secret': 'secret',
        })

    def _make_queued_message(self, payload='<test/>'):
        return self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'payload_xml': payload,
            'state': 'queued',
        })

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_process_queued_sends_and_marks_sent(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text='OK')
        msg = self._make_queued_message()
        self.env['3pl.message']._process_outbound_queue()
        self.assertEqual(msg.state, 'sent')
        self.assertTrue(msg.sent_at)

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_process_queued_on_500_increments_retry(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text='Error')
        msg = self._make_queued_message()
        self.env['3pl.message']._process_outbound_queue()
        self.assertEqual(msg.state, 'queued')
        self.assertEqual(msg.retry_count, 1)

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_process_queued_on_422_dead_letters(self, mock_post):
        mock_post.return_value = MagicMock(status_code=422, text='Bad data')
        msg = self._make_queued_message()
        self.env['3pl.message']._process_outbound_queue()
        self.assertEqual(msg.state, 'dead')

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_process_queued_at_max_retry_dead_letters(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text='Error')
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'payload_xml': '<test/>',
            'state': 'queued',
            'retry_count': 2,  # Already at MAX_RETRIES - 1
        })
        self.env['3pl.message']._process_outbound_queue()
        self.assertEqual(msg.state, 'dead')
