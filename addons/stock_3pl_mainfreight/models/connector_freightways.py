# addons/stock_3pl_mainfreight/models/connector_freightways.py
"""Freightways / Castle Parcels connector credential fields."""
from odoo import models, fields
from odoo.addons.stock_3pl_core.utils.credential_store import encrypt_credential

FREIGHTWAYS_ENVIRONMENTS = {
    'test': {
        'rest_api': 'https://api-sandbox.freightways.co.nz/api/v1',
    },
    'production': {
        'rest_api': 'https://api.freightways.co.nz/api/v1',
    },
}

_FW_CREDENTIAL_FIELDS = ('fw_api_key',)


class ThreePlConnectorFreightways(models.Model):
    _inherit = '3pl.connector'

    fw_api_key = fields.Char(
        'Freightways API Key',
        password=True,
        groups='stock.group_stock_manager',
    )
    fw_account_number = fields.Char('Freightways Account Number')

    def write(self, vals):
        for field in _FW_CREDENTIAL_FIELDS:
            if field in vals and vals[field]:
                vals[field] = encrypt_credential(self.env, vals[field])
        return super().write(vals)
