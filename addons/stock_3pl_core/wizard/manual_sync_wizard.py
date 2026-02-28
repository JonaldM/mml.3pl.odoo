# addons/stock_3pl_core/wizard/manual_sync_wizard.py
# TODO: Implement manual sync wizard in Phase 2 UX sprint.
# This wizard will allow users to manually trigger outbound sync for specific records.
from odoo import models, fields


class ManualSyncWizard(models.TransientModel):
    _name = 'stock_3pl.manual_sync_wizard'
    _description = 'Manual 3PL Sync Wizard'

    connector_id = fields.Many2one('3pl.connector', 'Connector', required=True)
    # Phase 2: add document type selector, record ref, dry_run toggle
