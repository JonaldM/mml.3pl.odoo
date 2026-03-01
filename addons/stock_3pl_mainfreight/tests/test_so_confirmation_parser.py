# addons/stock_3pl_mainfreight/tests/test_so_confirmation_parser.py
"""
Pure-Python tests for SOConfirmationDocument dual-schema parser.
No Odoo runtime required — tests parse_inbound() and its two sub-paths.
"""
import sys
import types
import unittest
import importlib.util
import pathlib


def _stub_odoo_for_document():
    """Minimal Odoo stubs so so_confirmation.py can be imported."""
    if 'odoo' not in sys.modules:
        sys.modules['odoo'] = types.ModuleType('odoo')

    # odoo.exceptions — only install stub if not already present (e.g. from conftest).
    # Unconditional assignment would replace conftest's stub (which includes UserError)
    # with this minimal one (ValidationError only), breaking other tests in a full suite run.
    if 'odoo.exceptions' not in sys.modules:
        exc_mod = types.ModuleType('odoo.exceptions')
        class ValidationError(Exception):
            pass
        exc_mod.ValidationError = ValidationError
        sys.modules['odoo.exceptions'] = exc_mod

    # odoo.addons.stock_3pl_core.models.document_base.AbstractDocument
    # Use setdefault so conftest's real module load takes precedence.
    class AbstractDocument:
        def __init__(self, connector, env):
            self.connector = connector
            self.env = env
        def truncate(self, value, max_len=None):
            if max_len and value:
                return str(value)[:max_len]
            return str(value) if value is not None else ''
        @staticmethod
        def make_idempotency_key(*args):
            return ':'.join(str(a) for a in args)

    core = types.ModuleType('odoo.addons.stock_3pl_core')
    core_models = types.ModuleType('odoo.addons.stock_3pl_core.models')
    core_doc = types.ModuleType('odoo.addons.stock_3pl_core.models.document_base')
    core_doc.AbstractDocument = AbstractDocument
    sys.modules.setdefault('odoo.addons', types.ModuleType('odoo.addons'))
    sys.modules.setdefault('odoo.addons.stock_3pl_core', core)
    sys.modules.setdefault('odoo.addons.stock_3pl_core.models', core_models)
    sys.modules.setdefault('odoo.addons.stock_3pl_core.models.document_base', core_doc)


_stub_odoo_for_document()

_DOC_DIR = pathlib.Path(__file__).parent.parent / 'document'


def _load_doc(name):
    spec = importlib.util.spec_from_file_location(name, _DOC_DIR / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_so_conf_mod = _load_doc('so_confirmation')
SOConfirmationDocument = _so_conf_mod.SOConfirmationDocument


# --- Fixture XML strings ---

SCH_SCL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<SOConfirmation>
  <SCH>
    <Reference>SO001</Reference>
    <ConsignmentNo>OTR000000134</ConsignmentNo>
    <CarrierName>MAINFREIGHT</CarrierName>
    <FinalisedDate>01/03/2026</FinalisedDate>
    <ETADate>03/03/2026</ETADate>
    <Lines>
      <SCL>
        <ProductCode>WIDG001</ProductCode>
        <UnitsFulfilled>10</UnitsFulfilled>
        <LotNumber>LOT001</LotNumber>
      </SCL>
    </Lines>
  </SCH>
</SOConfirmation>
"""

WEBHOOK_STYLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<orderConfirmation>
  <customerOrderReference>SO001</customerOrderReference>
  <orderReference>ORD-9999</orderReference>
  <dateDispatched>2026-03-01</dateDispatched>
  <etaDate>2026-03-03</etaDate>
  <serviceProvider>
    <name>MAINFREIGHT</name>
  </serviceProvider>
  <consignments>
    <consignment>
      <consignmentNumber>OTR000000134</consignmentNumber>
    </consignment>
  </consignments>
  <orderConfirmationLines>
    <orderConfirmationLine>
      <productCode>WIDG001</productCode>
      <unitsFulfilled>10</unitsFulfilled>
      <lotNumber>LOT001</lotNumber>
    </orderConfirmationLine>
  </orderConfirmationLines>
</orderConfirmation>
"""


class TestSOConfirmationSchSclParser(unittest.TestCase):
    """Original SCH/SCL XML path."""

    def setUp(self):
        self.doc = SOConfirmationDocument(connector=None, env=None)

    def test_parses_reference(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['reference'], 'SO001')

    def test_parses_consignment_no(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['consignment_no'], 'OTR000000134')

    def test_parses_carrier_name(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['carrier_name'], 'MAINFREIGHT')

    def test_parses_finalised_date(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertIsNotNone(result['finalised_date'])

    def test_parses_eta_date(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertIsNotNone(result['eta_date'])

    def test_parses_line_product_code(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['lines'][0]['product_code'], 'WIDG001')

    def test_parses_line_qty_done(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['lines'][0]['qty_done'], 10.0)

    def test_parses_line_lot_number(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['lines'][0]['lot_number'], 'LOT001')


class TestSOConfirmationWebhookStyleParser(unittest.TestCase):
    """Webhook/public-API schema path — camelCase elements, richer structure."""

    def setUp(self):
        self.doc = SOConfirmationDocument(connector=None, env=None)

    def test_parses_customer_order_reference_as_reference(self):
        """customerOrderReference (SO name) takes priority over orderReference."""
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['reference'], 'SO001')

    def test_parses_consignment_number(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['consignment_no'], 'OTR000000134')

    def test_parses_carrier_from_service_provider(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['carrier_name'], 'MAINFREIGHT')

    def test_parses_finalised_date_from_date_dispatched(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertIsNotNone(result['finalised_date'])

    def test_parses_eta_date(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertIsNotNone(result['eta_date'])

    def test_parses_line_product_code(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['lines'][0]['product_code'], 'WIDG001')

    def test_parses_line_qty_done(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['lines'][0]['qty_done'], 10.0)

    def test_parses_line_lot_number(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['lines'][0]['lot_number'], 'LOT001')

    def test_both_schemas_produce_same_keys(self):
        """Both parse paths must return the same dict shape for apply_inbound()."""
        sch_scl = self.doc.parse_inbound(SCH_SCL_XML)
        webhook = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(set(sch_scl.keys()), set(webhook.keys()))


if __name__ == '__main__':
    unittest.main()
