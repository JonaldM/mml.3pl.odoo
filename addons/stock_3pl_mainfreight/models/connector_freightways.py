# addons/stock_3pl_mainfreight/models/connector_freightways.py
"""Freightways / Castle Parcels connector credential fields."""
from odoo import models, fields, api
from odoo.addons.stock_3pl_core.utils.credential_store import encrypt_credential

class ThreePlConnectorFreightways(models.Model):
    _inherit = '3pl.connector'

    _FW_CREDENTIAL_FIELDS = ('fw_api_key',)

    fw_api_key = fields.Char(
        'Freightways API Key',
        password=True,
        groups='stock.group_stock_manager',
    )
    fw_account_number = fields.Char('Freightways Account Number')

    @api.model_create_multi
    def create(self, vals_list):
        # Encrypts Freightways-specific credential fields before INSERT.
        # super().create() subsequently handles base _CREDENTIAL_FIELDS.
        # encrypt_credential() is idempotent on already-encrypted values.
        for vals in vals_list:
            for field in self._FW_CREDENTIAL_FIELDS:
                if field in vals and vals[field]:
                    vals[field] = encrypt_credential(self.env, vals[field])
        return super().create(vals_list)

    def write(self, vals):
        for field in self._FW_CREDENTIAL_FIELDS:
            if field in vals and vals[field]:
                vals[field] = encrypt_credential(self.env, vals[field])
        return super().write(vals)
