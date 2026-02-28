# addons/stock_3pl_core/tests/test_transport_rest.py
import requests
from unittest.mock import patch, MagicMock
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'transport')
class TestRestTransport(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test REST',
            'warehouse_id': warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
            'api_url': 'https://test.example.com',
            'api_secret': 'secret123',
        })

    def _make_transport(self):
        from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport
        return RestTransport(self.connector)

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_send_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text='OK')
        transport = self._make_transport()
        result = transport.send('<Order><Ref>SO001</Ref></Order>', content_type='xml')
        self.assertTrue(result['success'])

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_send_409_treated_as_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=409, text='Conflict')
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertTrue(result['success'])
        self.assertEqual(result['note'], 'already_exists')

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_send_422_raises_validation_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=422, text='Bad payload')
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_type'], 'validation')

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_send_500_raises_retriable_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text='Server Error')
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_type'], 'retriable')

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_send_timeout_returns_retriable(self, mock_post):
        mock_post.side_effect = requests.Timeout()
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_type'], 'retriable')

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.post')
    def test_send_connection_error_returns_retriable(self, mock_post):
        mock_post.side_effect = requests.ConnectionError('ECONNREFUSED')
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_type'], 'retriable')

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.get')
    def test_poll_success_returns_list(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, text='<response/>')
        transport = self._make_transport()
        results = transport.poll()
        self.assertEqual(results, ['<response/>'])

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.get')
    def test_poll_request_exception_returns_empty(self, mock_get):
        mock_get.side_effect = requests.exceptions.RequestException('timeout')
        transport = self._make_transport()
        self.assertEqual(transport.poll(), [])

    @patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.get')
    def test_poll_non_200_returns_empty(self, mock_get):
        mock_get.return_value = MagicMock(status_code=404, text='Not Found')
        transport = self._make_transport()
        self.assertEqual(transport.poll(), [])
