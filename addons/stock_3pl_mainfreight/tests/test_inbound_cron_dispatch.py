# addons/stock_3pl_mainfreight/tests/test_inbound_cron_dispatch.py
"""Pure-Python mock-based tests for MFInboundCron dispatch logic.

No Odoo runtime required.  Odoo stubs are installed by the repo-level
conftest.py before pytest collects this module.

Coverage:
  - SOH filename → InventoryReportDocument.apply_csv called
  - ACKH_ filename prefix → SOAcknowledgementDocument.apply_csv called
  - ACKL_ filename prefix → SOAcknowledgementDocument.apply_csv called
  - Generic filename but ACK CSV header → SOAcknowledgementDocument.apply_csv called
  - REST raw-string SOH (no filename) → InventoryReportDocument.apply_csv called
"""
import sys
import types
import unittest
import importlib.util
import pathlib
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Resolve key directories
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
_DOCUMENT_DIR = _HERE.parent / 'document'
_MODELS_DIR = _HERE.parent / 'models'
_CORE_MODELS_DIR = (
    _HERE.parent.parent / 'stock_3pl_core' / 'models'
)

# ---------------------------------------------------------------------------
# Pre-load document modules so patch() can intercept them by import path.
#
# The production code (inbound_cron.py) does lazy imports inside the method:
#
#   from odoo.addons.stock_3pl_mainfreight.document.inventory_report import ...
#   from odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement import ...
#   from odoo.addons.stock_3pl_core.models.message import ThreePlMessage
#
# For patch() to intercept those we must have the modules registered in
# sys.modules under those exact dotted names before the method is called.
# ---------------------------------------------------------------------------

def _ensure_module(full_name, file_path):
    """Load a real Python source file into sys.modules under *full_name*."""
    if full_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(full_name, str(file_path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[full_name]


_INV_REPORT_MOD_NAME = 'odoo.addons.stock_3pl_mainfreight.document.inventory_report'
_SO_ACK_MOD_NAME = 'odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement'
_MESSAGE_MOD_NAME = 'odoo.addons.stock_3pl_core.models.message'

_ensure_module(_INV_REPORT_MOD_NAME, _DOCUMENT_DIR / 'inventory_report.py')
_ensure_module(_SO_ACK_MOD_NAME, _DOCUMENT_DIR / 'so_acknowledgement.py')
_ensure_module(_MESSAGE_MOD_NAME, _CORE_MODELS_DIR / 'message.py')

# ---------------------------------------------------------------------------
# Load the module under test (inbound_cron) directly from disk
# ---------------------------------------------------------------------------
_INBOUND_CRON_MOD_NAME = 'stock_3pl_mainfreight.models.inbound_cron_dispatch_test'
_INBOUND_CRON_PATH = _MODELS_DIR / 'inbound_cron.py'

# Use a unique module name to avoid collision with the existing test module
# that already loaded inbound_cron under a different key.
if _INBOUND_CRON_MOD_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _INBOUND_CRON_MOD_NAME, str(_INBOUND_CRON_PATH)
    )
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_INBOUND_CRON_MOD_NAME] = _mod
    _spec.loader.exec_module(_mod)
else:
    _mod = sys.modules[_INBOUND_CRON_MOD_NAME]

MFInboundCron = _mod.MFInboundCron

# ---------------------------------------------------------------------------
# Patch target strings (lazy imports inside _poll_inventory_reports)
# ---------------------------------------------------------------------------
_INV_REPORT_CLS_PATH = (
    'odoo.addons.stock_3pl_mainfreight.document.inventory_report.InventoryReportDocument'
)
_SO_ACK_CLS_PATH = (
    'odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement.SOAcknowledgementDocument'
)
_DETECT_TYPE_PATH = (
    'odoo.addons.stock_3pl_core.models.message.ThreePlMessage._detect_inbound_type'
)

# ---------------------------------------------------------------------------
# Sample CSV fixtures
# ---------------------------------------------------------------------------
_SOH_CSV = (
    'Product,WarehouseID,StockOnHand,QuantityOnHold,QuantityDamaged,'
    'QuantityAvailable,Grade1,Grade2,ExpiryDate,PackingDate\r\n'
    'WIDG001,99,100,2,3,95,A,,01/01/2027,\r\n'
)

_ACK_CSV = (
    'ClientOrderNumber,OrderStatus,WarehouseID,ReceivedDate\r\n'
    'SO/2026/00001,ENTERED,99,2026-02-28\r\n'
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cron(poll_result, connector_name='MF Test'):
    """Build an MFInboundCron instance with a single mock connector."""
    cron = object.__new__(MFInboundCron)

    mock_transport = MagicMock()
    mock_transport.poll.return_value = poll_result

    connector = MagicMock()
    connector.name = connector_name
    connector.get_transport.return_value = mock_transport

    mock_connector_model = MagicMock()
    mock_connector_model.search.return_value = [connector]

    env = MagicMock()
    env.__getitem__ = MagicMock(side_effect=lambda key: (
        mock_connector_model if key == '3pl.connector' else MagicMock()
    ))
    cron.env = env
    cron._connector = connector  # kept for assertion access
    return cron


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestDispatchSOHToInventoryReport(unittest.TestCase):
    """SOH CSV files must go to InventoryReportDocument.apply_csv."""

    def test_soh_file_dispatched_to_inventory_report(self):
        """filename=SOH_99_2026.csv, SOH content → InventoryReportDocument.apply_csv called;
        SOAcknowledgementDocument.apply_csv NOT called."""
        cron = _make_cron([('SOH_99_2026.csv', _SOH_CSV)])

        mock_inv_doc = MagicMock()
        mock_ack_doc = MagicMock()

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_inv_doc) as MockInv, \
             patch(_SO_ACK_CLS_PATH, return_value=mock_ack_doc) as MockAck:
            cron._poll_inventory_reports()

        mock_inv_doc.apply_csv.assert_called_once()
        args = mock_inv_doc.apply_csv.call_args[0]
        self.assertEqual(args[0], _SOH_CSV)

        mock_ack_doc.apply_csv.assert_not_called()


class TestDispatchACKHFilenameToAcknowledgement(unittest.TestCase):
    """ACKH_ prefix in filename → SOAcknowledgementDocument.apply_csv."""

    def test_ackh_filename_dispatched_to_acknowledgement(self):
        """filename=ACKH_99_2026.csv → SOAcknowledgementDocument.apply_csv called;
        InventoryReportDocument.apply_csv NOT called."""
        cron = _make_cron([('ACKH_99_2026.csv', _ACK_CSV)])

        mock_inv_doc = MagicMock()
        mock_ack_doc = MagicMock()

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_inv_doc), \
             patch(_SO_ACK_CLS_PATH, return_value=mock_ack_doc):
            cron._poll_inventory_reports()

        mock_ack_doc.apply_csv.assert_called_once()
        args = mock_ack_doc.apply_csv.call_args[0]
        self.assertEqual(args[0], _ACK_CSV)

        mock_inv_doc.apply_csv.assert_not_called()


class TestDispatchACKLFilenameToAcknowledgement(unittest.TestCase):
    """ACKL_ prefix in filename → SOAcknowledgementDocument.apply_csv."""

    def test_ackl_filename_dispatched_to_acknowledgement(self):
        """filename=ACKL_99_2026.csv → SOAcknowledgementDocument.apply_csv called;
        InventoryReportDocument.apply_csv NOT called."""
        cron = _make_cron([('ACKL_99_2026.csv', _ACK_CSV)])

        mock_inv_doc = MagicMock()
        mock_ack_doc = MagicMock()

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_inv_doc), \
             patch(_SO_ACK_CLS_PATH, return_value=mock_ack_doc):
            cron._poll_inventory_reports()

        mock_ack_doc.apply_csv.assert_called_once()
        args = mock_ack_doc.apply_csv.call_args[0]
        self.assertEqual(args[0], _ACK_CSV)

        mock_inv_doc.apply_csv.assert_not_called()


class TestDispatchACKContentHeaderToAcknowledgement(unittest.TestCase):
    """Generic filename but ACK CSV header → SOAcknowledgementDocument.apply_csv."""

    def test_ackh_content_header_dispatched_to_acknowledgement(self):
        """filename=data.csv (no ACKH prefix) but content has ClientOrderNumber header
        → _detect_inbound_type returns 'so_acknowledgement'
        → SOAcknowledgementDocument.apply_csv called; InventoryReportDocument NOT called."""
        cron = _make_cron([('data.csv', _ACK_CSV)])

        mock_inv_doc = MagicMock()
        mock_ack_doc = MagicMock()

        # _detect_inbound_type is a @staticmethod — patch it on the class in
        # the message module so the lazy import inside _poll_inventory_reports
        # picks up our mock.
        with patch(_INV_REPORT_CLS_PATH, return_value=mock_inv_doc), \
             patch(_SO_ACK_CLS_PATH, return_value=mock_ack_doc), \
             patch(_DETECT_TYPE_PATH, return_value='so_acknowledgement'):
            cron._poll_inventory_reports()

        mock_ack_doc.apply_csv.assert_called_once()
        args = mock_ack_doc.apply_csv.call_args[0]
        self.assertEqual(args[0], _ACK_CSV)

        mock_inv_doc.apply_csv.assert_not_called()


class TestDispatchRESTSOHStringToInventoryReport(unittest.TestCase):
    """REST poll returns raw string with SOH header → InventoryReportDocument.apply_csv."""

    def test_rest_soh_raw_string_dispatched_to_inventory_report(self):
        """REST poll item is a raw CSV string (no filename tuple) with a SOH header.
        Filename falls back to '<rest>' which does not start with ACKH_/ACKL_.
        _detect_inbound_type returns 'inventory_report'.
        InventoryReportDocument.apply_csv is called; SOAcknowledgementDocument is NOT."""
        # REST returns a plain string, not a (filename, content) tuple
        cron = _make_cron([_SOH_CSV])

        mock_inv_doc = MagicMock()
        mock_ack_doc = MagicMock()

        with patch(_INV_REPORT_CLS_PATH, return_value=mock_inv_doc), \
             patch(_SO_ACK_CLS_PATH, return_value=mock_ack_doc), \
             patch(_DETECT_TYPE_PATH, return_value='inventory_report'):
            cron._poll_inventory_reports()

        mock_inv_doc.apply_csv.assert_called_once()
        args = mock_inv_doc.apply_csv.call_args[0]
        self.assertEqual(args[0], _SOH_CSV)

        mock_ack_doc.apply_csv.assert_not_called()


if __name__ == '__main__':
    unittest.main()
