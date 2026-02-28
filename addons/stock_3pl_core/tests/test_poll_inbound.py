# addons/stock_3pl_core/tests/test_poll_inbound.py
"""
Pure-Python tests for ThreePlMessage._detect_inbound_type (staticmethod).
No Odoo runtime required — the method is pure string inspection logic.

The conftest.py at the repository root installs all required odoo stubs before
collection, so this module loads message.py directly via importlib to access
the ThreePlMessage class without going through the Odoo registry.
"""
import importlib.util
import pathlib
import unittest

# Load message.py as a standalone module (bypasses the Odoo registry).
# conftest.py has already installed odoo stubs so class-body execution succeeds.
_MODELS_DIR = pathlib.Path(__file__).parent.parent / 'models'


def _load_model(name):
    spec = importlib.util.spec_from_file_location(
        f'_test_standalone_{name}', _MODELS_DIR / f'{name}.py'
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_message_mod = _load_model('message')
ThreePlMessage = _message_mod.ThreePlMessage


class TestDetectInboundType(unittest.TestCase):

    def test_sch_xml_returns_so_confirmation(self):
        """Test 5: _detect_inbound_type returns 'so_confirmation' for SCH XML payload."""
        raw = '<SCH><OrderRef>SO001</OrderRef></SCH>'
        result = ThreePlMessage._detect_inbound_type(raw)
        self.assertEqual(result, 'so_confirmation')

    def test_order_confirmation_xml_returns_so_confirmation(self):
        """Test 6: _detect_inbound_type returns 'so_confirmation' for OrderConfirmation XML."""
        raw = '<OrderConfirmation><Ref>SO002</Ref></OrderConfirmation>'
        result = ThreePlMessage._detect_inbound_type(raw)
        self.assertEqual(result, 'so_confirmation')

    def test_inward_confirmation_xml_returns_inward_confirmation(self):
        """Test 7: _detect_inbound_type returns 'inward_confirmation' for InwardConfirmation XML."""
        raw = '<InwardConfirmation><InwardRef>PO001</InwardRef></InwardConfirmation>'
        result = ThreePlMessage._detect_inbound_type(raw)
        self.assertEqual(result, 'inward_confirmation')

    def test_csv_payload_returns_inventory_report(self):
        """Test 8: _detect_inbound_type returns 'inventory_report' for a CSV payload."""
        raw = 'Product Code,Quantity On Hand\nWIDGET001,50\n'
        result = ThreePlMessage._detect_inbound_type(raw)
        self.assertEqual(result, 'inventory_report')

    def test_leading_whitespace_stripped_before_detection(self):
        """Extra: leading whitespace does not prevent XML tag detection."""
        raw = '  \n<OrderConfirmation><Ref>SO003</Ref></OrderConfirmation>'
        result = ThreePlMessage._detect_inbound_type(raw)
        self.assertEqual(result, 'so_confirmation')

    def test_unrecognised_xml_returns_none(self):
        """Extra: unrecognised XML root element returns None (not 'inventory_report')."""
        raw = '<UnknownDocument><Data/></UnknownDocument>'
        result = ThreePlMessage._detect_inbound_type(raw)
        self.assertIsNone(result)

    def test_empty_string_returns_inventory_report(self):
        """Extra: empty string does not start with '<', so it is treated as empty CSV."""
        result = ThreePlMessage._detect_inbound_type('')
        self.assertEqual(result, 'inventory_report')


if __name__ == '__main__':
    unittest.main()
