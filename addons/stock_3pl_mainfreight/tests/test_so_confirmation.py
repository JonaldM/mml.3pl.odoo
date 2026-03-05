# addons/stock_3pl_mainfreight/tests/test_so_confirmation.py
import os
from odoo.tests import TransactionCase, tagged

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


@tagged('post_install', '-at_install', 'mf_so_confirm')
class TestSOConfirmation(TransactionCase):

    def _load_fixture(self, name):
        with open(os.path.join(FIXTURE_DIR, name), encoding='utf-8') as f:
            return f.read()

    def test_parse_confirmation_extracts_reference(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import SOConfirmationDocument
        doc = SOConfirmationDocument(None, self.env)
        parsed = doc.parse_inbound(self._load_fixture('so_confirmation.xml'))
        self.assertEqual(parsed['reference'], 'SO001')
        self.assertEqual(parsed['consignment_no'], 'OTR000000134')
        self.assertEqual(parsed['carrier_name'], 'MAINFREIGHT')

    def test_parse_confirmation_extracts_lines(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import SOConfirmationDocument
        doc = SOConfirmationDocument(None, self.env)
        parsed = doc.parse_inbound(self._load_fixture('so_confirmation.xml'))
        self.assertEqual(len(parsed['lines']), 1)
        self.assertEqual(parsed['lines'][0]['product_code'], 'WIDG001')
        self.assertEqual(parsed['lines'][0]['qty_done'], 10.0)

    def test_parse_confirmation_extracts_dates(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import SOConfirmationDocument
        doc = SOConfirmationDocument(None, self.env)
        parsed = doc.parse_inbound(self._load_fixture('so_confirmation.xml'))
        self.assertIsNotNone(parsed['finalised_date'])
        self.assertIsNotNone(parsed['eta_date'])

    def test_parse_confirmation_extracts_lot_number(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import SOConfirmationDocument
        doc = SOConfirmationDocument(None, self.env)
        parsed = doc.parse_inbound(self._load_fixture('so_confirmation.xml'))
        self.assertEqual(parsed['lines'][0]['lot_number'], 'LOT001')


@tagged('post_install', '-at_install', 'mf_so_ack')
class TestSOAcknowledgement(TransactionCase):

    def _load_fixture(self, name):
        with open(os.path.join(FIXTURE_DIR, name), encoding='utf-8') as f:
            return f.read()

    def test_parse_ack_extracts_order_reference(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement import SOAcknowledgementDocument
        doc = SOAcknowledgementDocument(None, self.env)
        rows = doc.parse_inbound(self._load_fixture('so_acknowledgement.csv'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['client_order_number'], 'SO001')
        self.assertEqual(rows[0]['order_status'], 'ENTERED')

    def test_so_acknowledgement_document_type(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement import SOAcknowledgementDocument
        self.assertEqual(SOAcknowledgementDocument.document_type, 'so_acknowledgement')

    def test_so_acknowledgement_in_document_type_selection(self):
        """so_acknowledgement must be a valid value in 3pl.message.document_type."""
        doc_types = [v for v, _ in self.env['3pl.message'].fields_get(
            ['document_type'])['document_type']['selection']]
        self.assertIn('so_acknowledgement', doc_types)
