# addons/stock_3pl_mainfreight/tests/test_process_inbound_messages.py
"""Pure-Python mock-based tests for MFInboundCron._process_inbound_messages().

No Odoo runtime required.  Odoo stubs are installed by the repo-level
conftest.py before pytest collects this module.

Coverage:
  - so_confirmation message dispatched to SOConfirmationDocument.apply_inbound
  - so_acknowledgement message dispatched to SOAcknowledgementDocument.apply_inbound
  - inventory_report message dispatched to InventoryReportDocument.apply_inbound
  - unknown document_type skipped (no handler called, no write)
  - exception dead-letters the message (state='dead', last_error set)
  - one failure does not prevent subsequent messages from being processed
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
# The production code (_process_inbound_messages) does lazy imports:
#
#   from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import ...
#   from odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement import ...
#   from odoo.addons.stock_3pl_mainfreight.document.inventory_report import ...
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


_SO_CONF_MOD_NAME = 'odoo.addons.stock_3pl_mainfreight.document.so_confirmation'
_SO_ACK_MOD_NAME = 'odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement'
_INV_REPORT_MOD_NAME = 'odoo.addons.stock_3pl_mainfreight.document.inventory_report'

_ensure_module(_SO_CONF_MOD_NAME, _DOCUMENT_DIR / 'so_confirmation.py')
_ensure_module(_SO_ACK_MOD_NAME, _DOCUMENT_DIR / 'so_acknowledgement.py')
_ensure_module(_INV_REPORT_MOD_NAME, _DOCUMENT_DIR / 'inventory_report.py')

# ---------------------------------------------------------------------------
# Load the module under test (inbound_cron) directly from disk.
# Use a unique module name to avoid collisions with other test modules that
# may have already loaded inbound_cron under a different key.
# ---------------------------------------------------------------------------
_INBOUND_CRON_MOD_NAME = 'stock_3pl_mainfreight.models.inbound_cron_process_test'
_INBOUND_CRON_PATH = _MODELS_DIR / 'inbound_cron.py'

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
# Patch target strings (lazy imports inside _process_inbound_messages)
# ---------------------------------------------------------------------------
_SO_CONF_CLS_PATH = (
    'odoo.addons.stock_3pl_mainfreight.document.so_confirmation.SOConfirmationDocument'
)
_SO_ACK_CLS_PATH = (
    'odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement.SOAcknowledgementDocument'
)
_INV_REPORT_CLS_PATH = (
    'odoo.addons.stock_3pl_mainfreight.document.inventory_report.InventoryReportDocument'
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(document_type, connector_name='MF Test'):
    """Build a mock 3pl.message with the given document_type."""
    connector = MagicMock()
    connector.name = connector_name
    connector.warehouse_partner = 'mainfreight'

    msg = MagicMock()
    msg.id = 1
    msg.document_type = document_type
    msg.connector_id = connector
    return msg


def _make_cron(messages):
    """Build an MFInboundCron instance whose env['3pl.message'].search returns *messages*."""
    cron = object.__new__(MFInboundCron)

    mock_message_model = MagicMock()
    mock_message_model.search.return_value = messages

    env = MagicMock()
    env.__getitem__ = MagicMock(side_effect=lambda key: (
        mock_message_model if key == '3pl.message' else MagicMock()
    ))
    cron.env = env
    return cron


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestSOConfirmationDispatchedAndApplied(unittest.TestCase):
    """so_confirmation message → SOConfirmationDocument.apply_inbound called, state='applied'."""

    def test_so_confirmation_dispatched_and_applied(self):
        msg = _make_message('so_confirmation')
        cron = _make_cron([msg])

        mock_conf_doc = MagicMock()

        with patch(_SO_CONF_CLS_PATH, return_value=mock_conf_doc) as MockConf, \
             patch(_SO_ACK_CLS_PATH) as MockAck, \
             patch(_INV_REPORT_CLS_PATH) as MockInv:
            cron._process_inbound_messages()

        mock_conf_doc.apply_inbound.assert_called_once_with(msg)
        msg.write.assert_called_once_with({'state': 'applied'})
        MockAck.return_value.apply_inbound.assert_not_called()
        MockInv.return_value.apply_inbound.assert_not_called()


class TestSOAcknowledgementDispatchedAndApplied(unittest.TestCase):
    """so_acknowledgement message → SOAcknowledgementDocument.apply_inbound called, state='applied'."""

    def test_so_acknowledgement_dispatched_and_applied(self):
        msg = _make_message('so_acknowledgement')
        cron = _make_cron([msg])

        mock_ack_doc = MagicMock()

        with patch(_SO_CONF_CLS_PATH) as MockConf, \
             patch(_SO_ACK_CLS_PATH, return_value=mock_ack_doc) as MockAck, \
             patch(_INV_REPORT_CLS_PATH) as MockInv:
            cron._process_inbound_messages()

        mock_ack_doc.apply_inbound.assert_called_once_with(msg)
        msg.write.assert_called_once_with({'state': 'applied'})
        MockConf.return_value.apply_inbound.assert_not_called()
        MockInv.return_value.apply_inbound.assert_not_called()


class TestInventoryReportDispatchedAndApplied(unittest.TestCase):
    """inventory_report message → InventoryReportDocument.apply_inbound called, state='applied'."""

    def test_inventory_report_dispatched_and_applied(self):
        msg = _make_message('inventory_report')
        cron = _make_cron([msg])

        mock_inv_doc = MagicMock()

        with patch(_SO_CONF_CLS_PATH) as MockConf, \
             patch(_SO_ACK_CLS_PATH) as MockAck, \
             patch(_INV_REPORT_CLS_PATH, return_value=mock_inv_doc) as MockInv:
            cron._process_inbound_messages()

        mock_inv_doc.apply_inbound.assert_called_once_with(msg)
        msg.write.assert_called_once_with({'state': 'applied'})
        MockConf.return_value.apply_inbound.assert_not_called()
        MockAck.return_value.apply_inbound.assert_not_called()


class TestUnknownDocumentTypeSkipped(unittest.TestCase):
    """Unrecognised document_type must be skipped — no handler called, no write."""

    def test_unknown_document_type_skipped(self):
        msg = _make_message('product_spec')
        cron = _make_cron([msg])

        with patch(_SO_CONF_CLS_PATH) as MockConf, \
             patch(_SO_ACK_CLS_PATH) as MockAck, \
             patch(_INV_REPORT_CLS_PATH) as MockInv:
            cron._process_inbound_messages()

        MockConf.return_value.apply_inbound.assert_not_called()
        MockAck.return_value.apply_inbound.assert_not_called()
        MockInv.return_value.apply_inbound.assert_not_called()

        # write must NOT have been called with 'applied'
        for written_call in msg.write.call_args_list:
            args, kwargs = written_call
            if args:
                self.assertNotEqual(args[0].get('state'), 'applied',
                                    'write(state=applied) must not be called for unknown types')


class TestExceptionDeadLettersMessage(unittest.TestCase):
    """apply_inbound raising an exception must dead-letter the message."""

    def test_exception_dead_letters_message(self):
        msg = _make_message('so_confirmation')
        msg.id = 42
        cron = _make_cron([msg])

        mock_conf_doc = MagicMock()
        mock_conf_doc.apply_inbound.side_effect = ValueError('Bad XML')

        with patch(_SO_CONF_CLS_PATH, return_value=mock_conf_doc), \
             patch(_SO_ACK_CLS_PATH), \
             patch(_INV_REPORT_CLS_PATH):
            cron._process_inbound_messages()

        # Must be dead-lettered
        dead_write_calls = [
            c for c in msg.write.call_args_list
            if c.args and c.args[0].get('state') == 'dead'
        ]
        self.assertEqual(len(dead_write_calls), 1,
                         'Expected exactly one write(state=dead) call')

        dead_vals = dead_write_calls[0].args[0]
        self.assertIn('last_error', dead_vals)
        self.assertIn('Bad XML', dead_vals['last_error'])
        self.assertLessEqual(len(dead_vals['last_error']), 500)

        # Must NOT have been written as 'applied'
        applied_calls = [
            c for c in msg.write.call_args_list
            if c.args and c.args[0].get('state') == 'applied'
        ]
        self.assertEqual(len(applied_calls), 0,
                         'write(state=applied) must not be called when apply_inbound raises')


class TestOneFailureDoesNotStopOthers(unittest.TestCase):
    """A failure on message 1 must not prevent message 2 from being processed."""

    def test_one_failure_does_not_stop_others(self):
        msg1 = _make_message('so_confirmation')
        msg1.id = 10
        msg2 = _make_message('so_confirmation')
        msg2.id = 11

        cron = _make_cron([msg1, msg2])

        # First call raises; second call succeeds
        mock_conf_doc_1 = MagicMock()
        mock_conf_doc_1.apply_inbound.side_effect = ValueError('First fails')

        mock_conf_doc_2 = MagicMock()

        call_count = {'n': 0}
        docs = [mock_conf_doc_1, mock_conf_doc_2]

        def _make_doc(*args, **kwargs):
            doc = docs[call_count['n']]
            call_count['n'] += 1
            return doc

        with patch(_SO_CONF_CLS_PATH, side_effect=_make_doc), \
             patch(_SO_ACK_CLS_PATH), \
             patch(_INV_REPORT_CLS_PATH):
            cron._process_inbound_messages()

        # msg1 must be dead-lettered
        dead_calls_1 = [
            c for c in msg1.write.call_args_list
            if c.args and c.args[0].get('state') == 'dead'
        ]
        self.assertEqual(len(dead_calls_1), 1, 'msg1 must be dead-lettered')

        # msg2 must be applied
        applied_calls_2 = [
            c for c in msg2.write.call_args_list
            if c.args and c.args[0].get('state') == 'applied'
        ]
        self.assertEqual(len(applied_calls_2), 1, 'msg2 must be applied')


if __name__ == '__main__':
    unittest.main()
