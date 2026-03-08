# addons/stock_3pl_core/wizard/manual_sync_wizard.py
# Phase 2 UX sprint: implement manual sync wizard to allow operators to trigger
# outbound sync for a specific record without waiting for the scheduled cron.
from odoo import models, fields


class ManualSyncWizard(models.TransientModel):
    _name = 'stock_3pl.manual_sync_wizard'
    _description = 'Manual 3PL Sync Wizard'

    connector_id = fields.Many2one('3pl.connector', 'Connector', required=True)
    # Phase 2: add document type selector, record ref field, and dry_run toggle
