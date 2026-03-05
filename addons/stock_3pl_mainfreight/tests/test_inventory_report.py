import os
import unittest
from unittest.mock import MagicMock
from odoo.tests import TransactionCase, tagged

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


@tagged('post_install', '-at_install', 'mf_inventory')
class TestInventoryReport(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'MF Test',
            'warehouse_id': warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.product = self.env['product.product'].create({
            'name': 'Widget',
            'default_code': 'WIDG001',
            'type': 'product',
        })

    def _load_fixture(self):
        with open(os.path.join(FIXTURE_DIR, 'inventory_report.csv'), encoding='utf-8') as f:
            return f.read()

    def _get_doc(self):
        from odoo.addons.stock_3pl_mainfreight.document.inventory_report import InventoryReportDocument
        return InventoryReportDocument(self.connector, self.env)

    def test_parse_returns_list_of_lines(self):
        doc = self._get_doc()
        lines = doc.parse_inbound(self._load_fixture())
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['product_code'], 'WIDG001')
        self.assertEqual(lines[0]['stock_on_hand'], 100)
        self.assertEqual(lines[0]['quantity_available'], 95)

    def test_apply_updates_stock_quant(self):
        doc = self._get_doc()
        csv_data = self._load_fixture()
        doc.apply_csv(csv_data, report_date=None)
        location = self.connector.warehouse_id.lot_stock_id
        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', location.id),
        ], limit=1)
        self.assertTrue(quant)
        self.assertEqual(quant.quantity, 100)


# ---------------------------------------------------------------------------
# Unit tests for discrepancy recording in apply_csv() — no Odoo runtime
# ---------------------------------------------------------------------------

class TestInventoryReportDiscrepancy(unittest.TestCase):
    """Mock-based tests verifying that apply_csv() writes mf.soh.discrepancy
    records when the MF SOH qty differs from the Odoo quant beyond tolerance.

    No Odoo runtime is required. All ORM access is replaced with MagicMock.
    The InventoryReportDocument class is imported directly from the filesystem.
    """

    def _get_doc_class(self):
        from odoo.addons.stock_3pl_mainfreight.document.inventory_report import InventoryReportDocument
        return InventoryReportDocument

    def _make_env_with_quant(self, odoo_qty, mf_qty=0.0):
        """Build a minimal mock env that satisfies all ORM calls in apply_csv().

        Returns a tuple of (env, discrepancy_model) so tests can assert on the
        stable discrepancy model mock directly, bypassing the env.__getitem__
        indirection which would return a new object each call.
        """
        product = MagicMock()
        product.id = 1

        quant = MagicMock()
        quant.quantity = odoo_qty

        # 'mf.soh.discrepancy' search returns a falsy mock (no existing record)
        no_existing_discrepancy = MagicMock()
        no_existing_discrepancy.__bool__ = MagicMock(return_value=False)

        # Build stable model mocks — the same object is returned every time
        # env[key] is called for that key.
        product_model = MagicMock(search=MagicMock(return_value=product))
        quant_model = MagicMock(
            search=MagicMock(return_value=quant),
            sudo=MagicMock(return_value=MagicMock(
                write=MagicMock(),
                create=MagicMock(),
            )),
        )
        discrepancy_model = MagicMock(
            search=MagicMock(return_value=no_existing_discrepancy),
            create=MagicMock(),
        )
        icp_model = MagicMock(
            sudo=MagicMock(return_value=MagicMock(
                get_param=MagicMock(return_value='0.005')
            ))
        )

        model_registry = {
            'product.product': product_model,
            'stock.quant': quant_model,
            'mf.soh.discrepancy': discrepancy_model,
            'ir.config_parameter': icp_model,
        }

        env = MagicMock()
        env.__getitem__ = MagicMock(
            side_effect=lambda k: model_registry.get(k, MagicMock())
        )
        return env, discrepancy_model

    def _make_connector(self, env):
        connector = MagicMock()
        connector.warehouse_id.lot_stock_id.id = 10
        connector.warehouse_id.id = 5
        return connector

    def _make_soh_csv(self, sku, qty):
        return (
            'Product,WarehouseID,StockOnHand,QuantityOnHold,QuantityDamaged,'
            'QuantityAvailable,Grade1,Grade2,ExpiryDate,PackingDate\n'
            f'{sku},WH01,{qty},0,0,{qty},,,,'
        )

    def test_apply_csv_writes_discrepancy_on_drift(self):
        """apply_csv() creates mf.soh.discrepancy when MF qty differs beyond tolerance.

        Odoo has 100 units, MF reports 95 (5% drift, well above the 0.5% default
        tolerance). Expect exactly one create() call on mf.soh.discrepancy with
        the correct mf_qty and odoo_qty values.
        """
        env, discrepancy_model = self._make_env_with_quant(odoo_qty=100.0, mf_qty=95.0)
        doc = self._get_doc_class()(connector=self._make_connector(env), env=env)
        csv_content = self._make_soh_csv('SKU001', 95)
        doc.apply_csv(csv_content)
        discrepancy_model.create.assert_called_once()
        call_vals = discrepancy_model.create.call_args[0][0]
        self.assertEqual(call_vals['mf_qty'], 95.0)
        self.assertEqual(call_vals['odoo_qty'], 100.0)
        self.assertEqual(call_vals.get('state'), 'open')

    def test_apply_csv_no_discrepancy_within_tolerance(self):
        """apply_csv() does NOT create discrepancy when drift is within 0.5% tolerance.

        Odoo has 100 units, MF reports 100 (zero drift). No discrepancy record
        should be created.
        """
        env, discrepancy_model = self._make_env_with_quant(odoo_qty=100.0, mf_qty=100.0)
        doc = self._get_doc_class()(connector=self._make_connector(env), env=env)
        csv_content = self._make_soh_csv('SKU001', 100)
        doc.apply_csv(csv_content)
        discrepancy_model.create.assert_not_called()

    def test_apply_csv_updates_existing_open_discrepancy(self):
        """apply_csv() updates an existing open discrepancy instead of creating duplicate.

        When a discrepancy record already exists in 'open' state for the same
        product+warehouse, write() must be called on that existing record and
        create() must NOT be called.
        """
        env, discrepancy_model = self._make_env_with_quant(odoo_qty=100.0, mf_qty=90.0)
        existing = MagicMock()
        # Replace the default falsy search result with a truthy existing record
        discrepancy_model.search = MagicMock(return_value=existing)
        doc = self._get_doc_class()(connector=self._make_connector(env), env=env)
        csv_content = self._make_soh_csv('SKU001', 90)
        doc.apply_csv(csv_content)
        existing.write.assert_called_once()
        discrepancy_model.create.assert_not_called()

    def test_apply_csv_zero_odoo_qty_triggers_discrepancy(self):
        """Zero odoo_qty + nonzero MF qty must always trigger a discrepancy (threshold=0)."""
        env, discrepancy_model = self._make_env_with_quant(odoo_qty=0.0)
        # Override quant search to return falsy (no existing quant record)
        env['stock.quant'].search = MagicMock(return_value=MagicMock(
            __bool__=MagicMock(return_value=False),
            quantity=0.0,
        ))
        doc = self._get_doc_class()(connector=self._make_connector(env), env=env)
        csv_content = self._make_soh_csv('SKU001', 50)
        doc.apply_csv(csv_content)
        discrepancy_model.create.assert_called_once()
        call_vals = discrepancy_model.create.call_args[0][0]
        self.assertEqual(call_vals['mf_qty'], 50.0)
        self.assertEqual(call_vals['odoo_qty'], 0.0)
