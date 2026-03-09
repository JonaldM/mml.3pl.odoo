"""Pure-Python tests for SO dispatch tracking fields and cron targeting.

No live Odoo instance required — uses Odoo stubs from conftest.py.
"""
import pytest


class TestPickingMFFields:

    def _get_model_class(self):
        from odoo.addons.stock_3pl_mainfreight.models.picking_mf import StockPickingMF
        return StockPickingMF

    def test_x_mf_outbound_ref_field_exists(self):
        cls = self._get_model_class()
        assert hasattr(cls, 'x_mf_outbound_ref'), "x_mf_outbound_ref not defined on StockPickingMF"

    def test_x_mf_tracking_url_field_on_picking_exists(self):
        cls = self._get_model_class()
        assert hasattr(cls, 'x_mf_tracking_url'), "x_mf_tracking_url not defined on StockPickingMF"
