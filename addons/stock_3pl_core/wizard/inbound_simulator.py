# addons/stock_3pl_core/wizard/inbound_simulator.py
# Phase 2 UX sprint: implement inbound simulator to allow users to paste raw
# inbound XML/CSV payloads for testing without triggering a live SFTP poll.
from odoo import models, fields


class InboundSimulatorWizard(models.TransientModel):
    _name = 'stock_3pl.inbound_simulator'
    _description = '3PL Inbound Simulator Wizard'

    connector_id = fields.Many2one('3pl.connector', 'Connector', required=True)
    payload = fields.Text('Raw Payload')
    # Phase 2: add document type selector and parse result display fields
