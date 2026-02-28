"""Pure-Python unit tests for mf.accept.discrepancy.wizard."""
import unittest
from unittest.mock import MagicMock


class TestAcceptDiscrepancyWizard(unittest.TestCase):

    def _make_wizard(self):
        from odoo.addons.stock_3pl_mainfreight.wizard.accept_discrepancy_wizard import MfAcceptDiscrepancyWizard
        wizard = MagicMock(spec=MfAcceptDiscrepancyWizard)
        wizard.discrepancy_id = MagicMock()
        wizard.reason = 'Confirmed shrinkage'
        return wizard

    def test_action_accept_delegates_to_discrepancy(self):
        """action_accept calls discrepancy.action_accept_discrepancy with the reason."""
        from odoo.addons.stock_3pl_mainfreight.wizard.accept_discrepancy_wizard import MfAcceptDiscrepancyWizard
        wizard = self._make_wizard()
        MfAcceptDiscrepancyWizard.action_accept(wizard)
        wizard.discrepancy_id.action_accept_discrepancy.assert_called_once_with(
            reason='Confirmed shrinkage'
        )

    def test_action_accept_returns_window_close(self):
        """action_accept returns ir.actions.act_window_close."""
        from odoo.addons.stock_3pl_mainfreight.wizard.accept_discrepancy_wizard import MfAcceptDiscrepancyWizard
        wizard = self._make_wizard()
        result = MfAcceptDiscrepancyWizard.action_accept(wizard)
        self.assertEqual(result.get('type'), 'ir.actions.act_window_close')
