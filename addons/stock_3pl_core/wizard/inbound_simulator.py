# addons/stock_3pl_core/wizard/inbound_simulator.py
# TODO: Implement inbound simulator in Phase 2 UX sprint.
# This wizard will allow users to paste raw inbound XML/CSV payloads for testing.
from odoo import models, fields


class InboundSimulatorWizard(models.TransientModel):
    _name = 'threePL.inbound_simulator'
    _description = '3PL Inbound Simulator Wizard (stub)'

    connector_id = fields.Many2one('3pl.connector', 'Connector', required=True)
    payload = fields.Text('Raw Payload')
    # Phase 2: add document type selector, parse result display
