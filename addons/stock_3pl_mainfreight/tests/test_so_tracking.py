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


class FakePicking:
    def __init__(self, picking_type_code, x_mf_status, x_mf_tracking_url='', x_mf_dispatched_date=None, id=0):
        self.picking_type_code = picking_type_code
        self.picking_type_id = self  # picking_type_id.picking_type_code
        self.x_mf_status = x_mf_status
        self.x_mf_tracking_url = x_mf_tracking_url
        self.x_mf_dispatched_date = x_mf_dispatched_date
        self.id = id


class FakeSaleOrder:
    def __init__(self, pickings):
        self.picking_ids = pickings
        self.x_mf_delivery_status = False
        self.x_mf_tracking_url = False

    def __iter__(self):
        yield self


def _run_compute(order):
    """Invoke the compute method on a single FakeSaleOrder instance."""
    from odoo.addons.stock_3pl_mainfreight.models.sale_order_mf import SaleOrderMFFields
    SaleOrderMFFields._compute_mf_tracking_fields(order)


class TestSaleOrderComputedFields:

    def test_delivery_status_empty_when_no_pickings(self):
        order = FakeSaleOrder([])
        _run_compute(order)
        assert order.x_mf_delivery_status == ''

    def test_tracking_url_empty_when_no_pickings(self):
        order = FakeSaleOrder([])
        _run_compute(order)
        assert order.x_mf_tracking_url == ''

    def test_delivery_status_most_advanced(self):
        pickings = [
            FakePicking('outgoing', 'mf_dispatched', 'https://track.mf.com/1', id=1),
            FakePicking('outgoing', 'mf_in_transit', 'https://track.mf.com/2', id=2),
            FakePicking('outgoing', 'mf_sent', '', id=3),
        ]
        order = FakeSaleOrder(pickings)
        _run_compute(order)
        assert order.x_mf_delivery_status == 'In Transit'

    def test_tracking_url_from_most_recently_dispatched(self):
        import datetime
        pickings = [
            FakePicking('outgoing', 'mf_dispatched',
                        'https://track.mf.com/1',
                        datetime.datetime(2026, 3, 1), id=1),
            FakePicking('outgoing', 'mf_dispatched',
                        'https://track.mf.com/2',
                        datetime.datetime(2026, 3, 5), id=2),
        ]
        order = FakeSaleOrder(pickings)
        _run_compute(order)
        assert order.x_mf_tracking_url == 'https://track.mf.com/2'

    def test_ignores_non_outgoing_pickings(self):
        pickings = [
            FakePicking('incoming', 'mf_delivered', 'https://track.mf.com/in', id=1),
            FakePicking('outgoing', 'mf_sent', '', id=2),
        ]
        order = FakeSaleOrder(pickings)
        _run_compute(order)
        assert order.x_mf_delivery_status == 'Sent to Warehouse'


class TestPhaseZeroCronTargeting:

    def test_phase0_method_exists(self):
        from odoo.addons.stock_3pl_mainfreight.models.tracking_cron import MFTrackingCron
        assert hasattr(MFTrackingCron, '_run_mf_tracking_phase0')

    def test_phase0_targets_mf_sent_with_outbound_ref_no_connote(self):
        from odoo.addons.stock_3pl_mainfreight.models.tracking_cron import _phase0_should_target
        picking = type('P', (), {
            'x_mf_status': 'mf_sent',
            'x_mf_connote': False,
            'x_mf_outbound_ref': 'OUT-001',
        })()
        assert _phase0_should_target(picking) is True

    def test_phase0_skips_picking_with_connote(self):
        from odoo.addons.stock_3pl_mainfreight.models.tracking_cron import _phase0_should_target
        picking = type('P', (), {
            'x_mf_status': 'mf_sent',
            'x_mf_connote': 'MF123',
            'x_mf_outbound_ref': 'OUT-001',
        })()
        assert _phase0_should_target(picking) is False

    def test_phase0_skips_picking_with_no_outbound_ref(self):
        from odoo.addons.stock_3pl_mainfreight.models.tracking_cron import _phase0_should_target
        picking = type('P', (), {
            'x_mf_status': 'mf_sent',
            'x_mf_connote': False,
            'x_mf_outbound_ref': False,
        })()
        assert _phase0_should_target(picking) is False

    def test_phase0_skips_non_mf_sent(self):
        from odoo.addons.stock_3pl_mainfreight.models.tracking_cron import _phase0_should_target
        picking = type('P', (), {
            'x_mf_status': 'mf_dispatched',
            'x_mf_connote': False,
            'x_mf_outbound_ref': 'OUT-001',
        })()
        assert _phase0_should_target(picking) is False
