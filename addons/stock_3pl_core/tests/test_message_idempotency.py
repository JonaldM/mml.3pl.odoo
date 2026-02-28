# addons/stock_3pl_core/tests/test_message_idempotency.py
import hashlib
from odoo.tests import TransactionCase, tagged
from odoo import fields as odoo_fields

@tagged('post_install', '-at_install', 'idempotency')
class TestMessageIdempotency(TransactionCase):

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

    def _make_key(self, connector_id, doc_type, ref):
        raw = f'{connector_id}:{doc_type}:{ref}'
        return hashlib.sha256(raw.encode()).hexdigest()

    def test_duplicate_outbound_blocked(self):
        key = self._make_key(self.connector.id, 'sales_order', 'SO001')
        self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'idempotency_key': key,
        })
        with self.assertRaises(Exception):
            self.env['3pl.message'].create({
                'connector_id': self.connector.id,
                'direction': 'outbound',
                'document_type': 'sales_order',
                'action': 'create',
                'idempotency_key': key,
            })

    def test_duplicate_inbound_blocked_by_source_hash(self):
        raw = '<SCH>test</SCH>'
        h = hashlib.sha256(raw.encode()).hexdigest()
        self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'inbound',
            'document_type': 'so_confirmation',
            'source_hash': h,
        })
        with self.assertRaises(Exception):
            self.env['3pl.message'].create({
                'connector_id': self.connector.id,
                'direction': 'inbound',
                'document_type': 'so_confirmation',
                'source_hash': h,
            })

    def test_stale_soh_report_rejected(self):
        from datetime import date, timedelta
        self.connector.last_soh_applied_at = odoo_fields.Datetime.now()
        yesterday = date.today() - timedelta(days=1)
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'inbound',
            'document_type': 'inventory_report',
            'report_date': yesterday,
        })
        result = msg.is_stale()
        self.assertTrue(result)

    def test_fresh_soh_report_not_stale(self):
        from datetime import date, timedelta
        self.connector.last_soh_applied_at = odoo_fields.Datetime.now() - timedelta(days=2)
        today = date.today()
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'inbound',
            'document_type': 'inventory_report',
            'report_date': today,
        })
        result = msg.is_stale()
        self.assertFalse(result)
