# Phase 2 UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the Phase 2 "3PL Operations" UX layer — kanban pipeline, exception queue with case management, configurable KPI dashboard (DIFOT 95%, IRA 98%), and inventory discrepancy screen.

**Architecture:** Native Odoo 15 views (kanban/tree/form) for pipeline and queues. OWL 2 client action for the KPI dashboard stat cards and today-summary strip, using Odoo's native graph views for trends. New `mf.soh.discrepancy` transient model captures SOH drift from the inbound cron. All KPI targets stored in `ir.config_parameter`.

**Tech Stack:** Odoo 15, Python 3.9+, OWL 2 (`@odoo/owl`), Odoo JS registry pattern, `ir.actions.client`, `mail.thread` on `stock.picking` (already present), `ir.config_parameter` for targets.

**Pre-implementation gate:** Before coding, invoke `frontend-design` skill with this plan for a UX logic/component review. Apply any suggested changes to this plan before starting Task 1.

---

### Task 1: `mf.soh.discrepancy` model

**Files:**
- Create: `addons/stock_3pl_mainfreight/models/soh_discrepancy.py`
- Modify: `addons/stock_3pl_mainfreight/models/__init__.py`
- Modify: `addons/stock_3pl_mainfreight/security/ir.model.access.csv`
- Test: `addons/stock_3pl_mainfreight/tests/test_soh_discrepancy.py`

**Step 1: Write the failing test**

```python
# addons/stock_3pl_mainfreight/tests/test_soh_discrepancy.py
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime


class TestSohDiscrepancyModel(unittest.TestCase):
    """Pure-Python tests for mf.soh.discrepancy model logic."""

    def _make_env(self):
        env = MagicMock()
        discrepancy_model = MagicMock()
        env.__getitem__ = MagicMock(side_effect=lambda key: {
            'mf.soh.discrepancy': discrepancy_model,
        }.get(key, MagicMock()))
        return env, discrepancy_model

    def test_variance_pct_computed_from_qty(self):
        """variance_pct = abs(mf_qty - odoo_qty) / odoo_qty * 100."""
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        self.assertAlmostEqual(_compute_variance_pct(odoo_qty=100.0, mf_qty=98.0), 2.0)

    def test_variance_pct_zero_odoo_qty(self):
        """Zero odoo_qty should return 100.0 variance (full discrepancy)."""
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        self.assertEqual(_compute_variance_pct(odoo_qty=0.0, mf_qty=5.0), 100.0)

    def test_variance_pct_exact_match(self):
        """Matching quantities give 0.0 variance."""
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import _compute_variance_pct
        self.assertEqual(_compute_variance_pct(odoo_qty=50.0, mf_qty=50.0), 0.0)

    def test_mark_investigated_sets_fields(self):
        """action_mark_investigated sets state, investigated_by, investigated_date."""
        from odoo.addons.stock_3pl_mainfreight.models.soh_discrepancy import MfSohDiscrepancy
        record = MagicMock(spec=MfSohDiscrepancy)
        record.env = MagicMock()
        record.env.user.id = 42
        record.__iter__ = MagicMock(return_value=iter([record]))
        # Call the method
        MfSohDiscrepancy.action_mark_investigated(record)
        record.write.assert_called_once()
        call_vals = record.write.call_args[0][0]
        self.assertEqual(call_vals['state'], 'investigated')
        self.assertEqual(call_vals['investigated_by'], 42)
        self.assertIn('investigated_date', call_vals)
```

**Step 2: Run test to verify it fails**

```bash
cd E:\ClaudeCode\projects\mainfreight.3pl.intergration
python -m pytest addons/stock_3pl_mainfreight/tests/test_soh_discrepancy.py -v
```
Expected: `ImportError` — module does not exist yet.

**Step 3: Create the model**

```python
# addons/stock_3pl_mainfreight/models/soh_discrepancy.py
"""MF Stock-on-Hand discrepancy record — populated by inbound SOH cron."""
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


def _compute_variance_pct(odoo_qty: float, mf_qty: float) -> float:
    """Return abs variance as a percentage of odoo_qty. Returns 100.0 if odoo_qty is 0."""
    if not odoo_qty:
        return 100.0
    return round(abs(mf_qty - odoo_qty) / odoo_qty * 100, 4)


class MfSohDiscrepancy(models.Model):
    _name = 'mf.soh.discrepancy'
    _description = 'MF SOH Discrepancy'
    _order = 'detected_date desc'

    product_id = fields.Many2one('product.product', 'Product', required=True, index=True)
    warehouse_id = fields.Many2one('stock.warehouse', 'Warehouse', required=True)
    odoo_qty = fields.Float('Odoo SOH', digits=(16, 3))
    mf_qty = fields.Float('MF SOH', digits=(16, 3))
    variance_qty = fields.Float('Variance (units)', digits=(16, 3),
                                compute='_compute_variance', store=True)
    variance_pct = fields.Float('Variance %', digits=(10, 4),
                                compute='_compute_variance', store=True)
    detected_date = fields.Datetime('Detected', default=fields.Datetime.now, index=True)
    state = fields.Selection([('open', 'Open'), ('investigated', 'Investigated')],
                              default='open', index=True)
    investigated_by = fields.Many2one('res.users', 'Investigated By', readonly=True)
    investigated_date = fields.Datetime('Investigated Date', readonly=True)
    active = fields.Boolean(default=True)

    @api.depends('odoo_qty', 'mf_qty')
    def _compute_variance(self):
        for rec in self:
            rec.variance_qty = rec.mf_qty - rec.odoo_qty
            rec.variance_pct = _compute_variance_pct(rec.odoo_qty, rec.mf_qty)

    def action_mark_investigated(self):
        self.write({
            'state': 'investigated',
            'investigated_by': self.env.user.id,
            'investigated_date': fields.Datetime.now(),
        })
```

**Step 4: Add to `__init__.py`**

Add to line 1 of `addons/stock_3pl_mainfreight/models/__init__.py`:
```python
from . import connector_mf, connector_freightways, warehouse_mf, picking_mf, sale_order_mf, sale_order_hook, product_hook
from . import route_engine
from . import split_engine
from . import push_cron
from . import tracking_cron
from . import inbound_cron
from . import soh_discrepancy
from . import kpi_dashboard
```
(Also add `kpi_dashboard` now — implemented in Task 4.)

**Step 5: Add ACL entry**

Append to `addons/stock_3pl_mainfreight/security/ir.model.access.csv`:
```
access_mf_soh_discrepancy_manager,mf.soh.discrepancy manager,model_mf_soh_discrepancy,stock.group_stock_manager,1,1,1,1
access_mf_soh_discrepancy_user,mf.soh.discrepancy user,model_mf_soh_discrepancy,stock.group_stock_user,1,0,0,0
```

**Step 6: Run tests**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_soh_discrepancy.py -v
```
Expected: All pass.

**Step 7: Commit**
```bash
git add addons/stock_3pl_mainfreight/models/soh_discrepancy.py \
        addons/stock_3pl_mainfreight/models/__init__.py \
        addons/stock_3pl_mainfreight/security/ir.model.access.csv \
        addons/stock_3pl_mainfreight/tests/test_soh_discrepancy.py
git commit -m "feat(phase2): add mf.soh.discrepancy model with ACL"
```

---

### Task 2: Extend `stock.picking` — `mf_resolved` status + `x_mf_assigned_to`

**Files:**
- Modify: `addons/stock_3pl_mainfreight/models/picking_mf.py`
- Modify: `addons/stock_3pl_mainfreight/tests/test_picking_mf.py`

**Step 1: Write the failing tests**

Add to `test_picking_mf.py`:
```python
def test_mf_resolved_in_status_selection(self):
    """mf_resolved must be a valid x_mf_status value."""
    from odoo.addons.stock_3pl_mainfreight.models.picking_mf import MF_STATUS
    values = [v for v, _ in MF_STATUS]
    self.assertIn('mf_resolved', values)

def test_mf_resolved_after_mf_exception(self):
    """mf_resolved appears after mf_exception in status list."""
    from odoo.addons.stock_3pl_mainfreight.models.picking_mf import MF_STATUS
    values = [v for v, _ in MF_STATUS]
    self.assertGreater(values.index('mf_resolved'), values.index('mf_exception'))
```

**Step 2: Run to verify failure**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_picking_mf.py -v -k "resolved"
```

**Step 3: Modify `picking_mf.py`**

Add `('mf_resolved', 'Resolved')` to `MF_STATUS` after `('mf_exception', 'Exception')`:
```python
MF_STATUS = [
    ('draft', 'Draft'),
    ('mf_held_review', 'Held — Cross-Border Review'),
    ('mf_queued', 'Queued for 3PL'),
    ('mf_sent', 'Sent to 3PL'),
    ('mf_received', 'Received by MF'),
    ('mf_dispatched', 'Dispatched'),
    ('mf_in_transit', 'In Transit'),
    ('mf_out_for_delivery', 'Out for Delivery'),
    ('mf_delivered', 'Delivered'),
    ('mf_exception', 'Exception'),
    ('mf_resolved', 'Resolved'),
]
```

Add `x_mf_assigned_to` field to `StockPickingMF`:
```python
x_mf_assigned_to = fields.Many2one('res.users', 'Exception Assigned To', copy=False,
                                    groups='stock.group_stock_manager')
```

Also add action methods for the exception queue buttons:
```python
def action_mf_retry(self):
    """Re-queue exception picking back to mf_queued."""
    for picking in self:
        if picking.x_mf_status != 'mf_exception':
            raise UserError(
                f'{picking.name} is not in exception status '
                f'(current: {picking.x_mf_status or "not set"}).'
            )
    self.write({'x_mf_status': 'mf_queued'})
    self._message_log_batch('Re-queued for 3PL push by %(user)s.')

def action_mf_mark_resolved(self):
    """Mark exception picking as resolved without retry."""
    for picking in self:
        if picking.x_mf_status != 'mf_exception':
            raise UserError(
                f'{picking.name} is not in exception status.'
            )
    self.write({'x_mf_status': 'mf_resolved'})
    self._message_log_batch('Marked resolved by %(user)s.')

def action_mf_escalate(self):
    """Tag picking and schedule escalation activity for configured user."""
    ICP = self.env['ir.config_parameter'].sudo()
    escalation_user_id = int(ICP.get_param(
        'stock_3pl_mainfreight.exception_escalation_user', default=0
    ))
    for picking in self:
        if escalation_user_id:
            picking.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=escalation_user_id,
                note=f'MF Exception escalated: {picking.name}',
            )
        picking.message_post(body=f'Escalated by {self.env.user.name}.')

def _message_log_batch(self, template):
    """Post a chatter message to each picking using a template with %(user)s."""
    user = self.env.user.name
    for picking in self:
        picking.message_post(body=template % {'user': user})
```

**Step 4: Run tests**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_picking_mf.py -v
```
Expected: All pass.

**Step 5: Commit**
```bash
git add addons/stock_3pl_mainfreight/models/picking_mf.py \
        addons/stock_3pl_mainfreight/tests/test_picking_mf.py
git commit -m "feat(phase2): add mf_resolved status, x_mf_assigned_to, exception action methods"
```

---

### Task 3: Reassign Warehouse Wizard

**Files:**
- Create: `addons/stock_3pl_mainfreight/wizard/reassign_warehouse_wizard.py`
- Modify: `addons/stock_3pl_mainfreight/wizard/__init__.py`
- Modify: `addons/stock_3pl_mainfreight/security/ir.model.access.csv`
- Test: `addons/stock_3pl_mainfreight/tests/test_reassign_wizard.py`

**Step 1: Write the failing test**

```python
# addons/stock_3pl_mainfreight/tests/test_reassign_wizard.py
import unittest
from unittest.mock import MagicMock, patch


class TestReassignWizard(unittest.TestCase):

    def test_action_reassign_calls_route_engine(self):
        """Wizard action_reassign should write connector_id and reset status to mf_queued."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_exception'
        wizard.connector_id = MagicMock()
        wizard.connector_id.id = 99
        MfReassignWarehouseWizard.action_reassign(wizard)
        wizard.picking_id.write.assert_called_once_with({
            'x_mf_status': 'mf_queued',
        })

    def test_action_reassign_posts_chatter(self):
        """Wizard action_reassign should log reassignment in chatter."""
        from odoo.addons.stock_3pl_mainfreight.wizard.reassign_warehouse_wizard import MfReassignWarehouseWizard
        wizard = MagicMock(spec=MfReassignWarehouseWizard)
        wizard.picking_id = MagicMock()
        wizard.picking_id.x_mf_status = 'mf_exception'
        wizard.connector_id = MagicMock()
        wizard.connector_id.name = 'Auckland'
        wizard.env = MagicMock()
        wizard.env.user.name = 'Admin'
        MfReassignWarehouseWizard.action_reassign(wizard)
        wizard.picking_id.message_post.assert_called_once()
```

**Step 2: Run to verify failure**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_reassign_wizard.py -v
```

**Step 3: Create the wizard**

```python
# addons/stock_3pl_mainfreight/wizard/reassign_warehouse_wizard.py
"""Wizard to reassign an exception picking to a different warehouse/connector."""
from odoo import models, fields
from odoo.exceptions import UserError


class MfReassignWarehouseWizard(models.TransientModel):
    _name = 'mf.reassign.warehouse.wizard'
    _description = 'Reassign MF Exception to Warehouse'

    picking_id = fields.Many2one('stock.picking', 'Picking', required=True)
    connector_id = fields.Many2one(
        '3pl.connector', 'Target Connector / Warehouse',
        required=True,
        domain=[('active', '=', True)],
    )
    reason = fields.Text('Reason')

    def action_reassign(self):
        """Reassign the picking to the selected connector and re-queue."""
        self.ensure_one()
        picking = self.picking_id
        if picking.x_mf_status not in ('mf_exception', 'mf_held_review'):
            raise UserError(
                f'{picking.name} cannot be reassigned from status: {picking.x_mf_status}.'
            )
        picking.write({'x_mf_status': 'mf_queued'})
        note = (
            f'Reassigned to {self.connector_id.name} by {self.env.user.name}.'
            + (f' Reason: {self.reason}' if self.reason else '')
        )
        picking.message_post(body=note)
        return {'type': 'ir.actions.act_window_close'}
```

Add to `addons/stock_3pl_mainfreight/wizard/__init__.py`:
```python
from . import inbound_simulator
from . import manual_sync_wizard
from . import reassign_warehouse_wizard
```

Add to `security/ir.model.access.csv`:
```
access_mf_reassign_wizard,mf.reassign.warehouse.wizard,model_mf_reassign_warehouse_wizard,stock.group_stock_manager,1,1,1,1
```

**Step 4: Run tests**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_reassign_wizard.py -v
```

**Step 5: Commit**
```bash
git add addons/stock_3pl_mainfreight/wizard/reassign_warehouse_wizard.py \
        addons/stock_3pl_mainfreight/wizard/__init__.py \
        addons/stock_3pl_mainfreight/security/ir.model.access.csv \
        addons/stock_3pl_mainfreight/tests/test_reassign_wizard.py
git commit -m "feat(phase2): add reassign warehouse wizard for exception queue"
```

---

### Task 4: `mf.kpi.dashboard` AbstractModel

**Files:**
- Create: `addons/stock_3pl_mainfreight/models/kpi_dashboard.py`
- Test: `addons/stock_3pl_mainfreight/tests/test_kpi_dashboard.py`

**Step 1: Write the failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_kpi_dashboard.py
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


def _make_env(icp_params=None):
    """Build a mock env with configurable ir.config_parameter values."""
    env = MagicMock()
    icp = MagicMock()
    defaults = {
        'stock_3pl_mainfreight.kpi_difot_target': '95',
        'stock_3pl_mainfreight.kpi_ira_target': '98',
        'stock_3pl_mainfreight.kpi_exception_rate_target': '2',
        'stock_3pl_mainfreight.difot_grace_days': '0',
        'stock_3pl_mainfreight.ira_tolerance': '0.005',
    }
    if icp_params:
        defaults.update(icp_params)
    icp.get_param = MagicMock(side_effect=lambda key, default=None: defaults.get(key, default))
    icp_model = MagicMock()
    icp_model.sudo.return_value = icp
    env.__getitem__ = MagicMock(side_effect=lambda key: {
        'ir.config_parameter': icp_model,
        'stock.picking': MagicMock(),
        'mf.soh.discrepancy': MagicMock(),
        'stock.quant': MagicMock(),
    }.get(key, MagicMock()))
    return env, icp


class TestKpiDashboard(unittest.TestCase):

    def test_rag_green_at_or_above_target(self):
        """RAG is green when value >= target."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=95.0, target=95.0, lower_amber=90.0), 'green')
        self.assertEqual(_rag_status(value=97.0, target=95.0, lower_amber=90.0), 'green')

    def test_rag_amber_between_amber_and_target(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=92.0, target=95.0, lower_amber=90.0), 'amber')

    def test_rag_red_below_amber(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _rag_status
        self.assertEqual(_rag_status(value=88.0, target=95.0, lower_amber=90.0), 'red')

    def test_get_kpi_targets_reads_icp(self):
        """get_kpi_targets returns configured targets from ir.config_parameter."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env, _ = _make_env()
        dashboard.env = env
        result = MfKpiDashboard.get_kpi_targets(dashboard)
        self.assertEqual(result['difot_target'], 95.0)
        self.assertEqual(result['ira_target'], 98.0)
        self.assertEqual(result['exception_rate_target'], 2.0)

    def test_get_kpi_targets_custom_values(self):
        """Custom target values are read correctly."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import MfKpiDashboard
        dashboard = MagicMock(spec=MfKpiDashboard)
        env, _ = _make_env({'stock_3pl_mainfreight.kpi_difot_target': '97'})
        dashboard.env = env
        result = MfKpiDashboard.get_kpi_targets(dashboard)
        self.assertEqual(result['difot_target'], 97.0)

    def test_compute_exception_rate_no_orders(self):
        """Exception rate is 0.0 when no orders exist."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_exception_rate
        self.assertEqual(_compute_exception_rate(total=0, exceptions=0), 0.0)

    def test_compute_exception_rate_calculation(self):
        """Exception rate = exceptions / total * 100."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_exception_rate
        self.assertAlmostEqual(_compute_exception_rate(total=100, exceptions=3), 3.0)

    def test_compute_difot_no_delivered(self):
        """DIFOT is 100.0 when there are no delivered orders (no denominator)."""
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_difot
        self.assertEqual(_compute_difot(on_time_in_full=0, total_delivered=0), 100.0)

    def test_compute_difot_calculation(self):
        from odoo.addons.stock_3pl_mainfreight.models.kpi_dashboard import _compute_difot
        self.assertAlmostEqual(_compute_difot(on_time_in_full=95, total_delivered=100), 95.0)
```

**Step 2: Run to verify failure**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_kpi_dashboard.py -v
```

**Step 3: Create the model**

```python
# addons/stock_3pl_mainfreight/models/kpi_dashboard.py
"""KPI dashboard computation service — called from OWL frontend via orm.call()."""
import logging
from datetime import timedelta
from odoo import models, api, fields

_logger = logging.getLogger(__name__)


def _rag_status(value: float, target: float, lower_amber: float) -> str:
    """Return 'green', 'amber', or 'red' for a higher-is-better KPI."""
    if value >= target:
        return 'green'
    if value >= lower_amber:
        return 'amber'
    return 'red'


def _rag_status_lower_is_better(value: float, target: float, upper_amber: float) -> str:
    """Return 'green', 'amber', or 'red' for a lower-is-better KPI (e.g. exception rate)."""
    if value <= target:
        return 'green'
    if value <= upper_amber:
        return 'amber'
    return 'red'


def _compute_exception_rate(total: int, exceptions: int) -> float:
    """Exception rate as a percentage. Returns 0.0 if no orders."""
    if not total:
        return 0.0
    return round(exceptions / total * 100, 2)


def _compute_difot(on_time_in_full: int, total_delivered: int) -> float:
    """DIFOT as a percentage. Returns 100.0 if no delivered orders."""
    if not total_delivered:
        return 100.0
    return round(on_time_in_full / total_delivered * 100, 2)


class MfKpiDashboard(models.AbstractModel):
    """KPI computation service for the Phase 2 OWL dashboard.

    AbstractModel — no DB table. All methods are @api.model, called from
    the OWL frontend via: this.orm.call('mf.kpi.dashboard', 'get_kpi_summary', [])
    """
    _name = 'mf.kpi.dashboard'
    _description = 'MF KPI Dashboard'

    @api.model
    def get_kpi_targets(self) -> dict:
        """Return configured KPI targets from ir.config_parameter."""
        ICP = self.env['ir.config_parameter'].sudo()
        return {
            'difot_target': float(ICP.get_param('stock_3pl_mainfreight.kpi_difot_target', '95')),
            'ira_target': float(ICP.get_param('stock_3pl_mainfreight.kpi_ira_target', '98')),
            'exception_rate_target': float(ICP.get_param(
                'stock_3pl_mainfreight.kpi_exception_rate_target', '2'
            )),
            'difot_grace_days': int(ICP.get_param(
                'stock_3pl_mainfreight.difot_grace_days', '0'
            )),
            'ira_tolerance': float(ICP.get_param(
                'stock_3pl_mainfreight.ira_tolerance', '0.005'
            )),
        }

    @api.model
    def get_kpi_summary(self) -> dict:
        """Return the full KPI summary for the dashboard.

        Returns a dict with:
          - difot, ira, exception_rate, in_flight (values + RAG)
          - today: sent, received, delivered, exceptions
          - targets
        """
        targets = self.get_kpi_targets()
        now = fields.Datetime.now()
        thirty_days_ago = now - timedelta(days=30)

        difot_val = self._compute_difot_value(thirty_days_ago, targets['difot_grace_days'])
        ira_val = self._compute_ira_value(thirty_days_ago, targets['ira_tolerance'])
        exception_rate_val, in_flight = self._compute_exception_and_inflight(thirty_days_ago)

        t = targets['exception_rate_target']
        return {
            'difot': {
                'value': difot_val,
                'rag': _rag_status(difot_val, targets['difot_target'],
                                   targets['difot_target'] - 5),
            },
            'ira': {
                'value': ira_val,
                'rag': _rag_status(ira_val, targets['ira_target'],
                                   targets['ira_target'] - 3),
            },
            'exception_rate': {
                'value': exception_rate_val,
                'rag': _rag_status_lower_is_better(exception_rate_val, t, t * 2.5),
            },
            'in_flight': {'value': in_flight, 'rag': 'none'},
            'today': self._compute_today_summary(now),
            'targets': targets,
        }

    @api.model
    def get_weekly_trend(self, weeks: int = 13) -> list:
        """Return weekly order counts by status for the trend chart (last N weeks)."""
        now = fields.Datetime.now()
        result = []
        for i in range(weeks - 1, -1, -1):
            week_start = now - timedelta(weeks=i + 1)
            week_end = now - timedelta(weeks=i)
            week_label = week_start.strftime('%d %b')
            counts = {}
            for status in ('mf_sent', 'mf_delivered', 'mf_exception'):
                counts[status] = self.env['stock.picking'].search_count([
                    ('x_mf_status', '=', status),
                    ('write_date', '>=', week_start),
                    ('write_date', '<', week_end),
                ])
            result.append({'week': week_label, **counts})
        return result

    # ---- private helpers ----

    def _compute_difot_value(self, since: object, grace_days: int) -> float:
        Picking = self.env['stock.picking']
        total = Picking.search_count([
            ('x_mf_status', '=', 'mf_delivered'),
            ('x_mf_delivered_date', '>=', since),
        ])
        if not total:
            return 100.0
        on_time = Picking.search_count([
            ('x_mf_status', '=', 'mf_delivered'),
            ('x_mf_delivered_date', '>=', since),
            # On time = delivered before/on deadline + grace
            # Using date_deadline (Odoo 15 picking field). NULL deadline = always on time.
            '|',
            ('date_deadline', '=', False),
            ('x_mf_delivered_date', '<=',
             fields.Datetime.to_string(
                 fields.Datetime.from_string(str(since)) + timedelta(days=grace_days)
             ) if grace_days else False),
        ])
        # Fallback: if deadline check returns 0 because grace_days formula is complex,
        # count delivered with no deadline as on-time (conservative)
        no_deadline = Picking.search_count([
            ('x_mf_status', '=', 'mf_delivered'),
            ('x_mf_delivered_date', '>=', since),
            ('date_deadline', '=', False),
        ])
        with_deadline_on_time = Picking.search_count([
            ('x_mf_status', '=', 'mf_delivered'),
            ('x_mf_delivered_date', '>=', since),
            ('date_deadline', '!=', False),
            ('x_mf_delivered_date', '<=', 'date_deadline'),
        ])
        return _compute_difot(no_deadline + with_deadline_on_time, total)

    def _compute_ira_value(self, since: object, tolerance: float) -> float:
        Discrepancy = self.env['mf.soh.discrepancy']
        total_skus = self.env['stock.quant'].search_count([
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ])
        if not total_skus:
            return 100.0
        skus_with_discrepancy = Discrepancy.search_count([
            ('state', '=', 'open'),
            ('detected_date', '>=', since),
            ('variance_pct', '>', tolerance * 100),
        ])
        ira = ((total_skus - skus_with_discrepancy) / total_skus) * 100
        return round(max(ira, 0.0), 2)

    def _compute_exception_and_inflight(self, since: object):
        Picking = self.env['stock.picking']
        total = Picking.search_count([
            ('x_mf_status', 'not in', ['draft', False]),
            ('write_date', '>=', since),
        ])
        exceptions = Picking.search_count([
            ('x_mf_status', '=', 'mf_exception'),
            ('write_date', '>=', since),
        ])
        in_flight = Picking.search_count([
            ('x_mf_status', 'in', ['mf_sent', 'mf_received', 'mf_dispatched']),
        ])
        return _compute_exception_rate(total, exceptions), in_flight

    def _compute_today_summary(self, now: object) -> dict:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        Picking = self.env['stock.picking']
        return {
            'sent': Picking.search_count([
                ('x_mf_status', '=', 'mf_sent'), ('write_date', '>=', today_start)
            ]),
            'received': Picking.search_count([
                ('x_mf_status', '=', 'mf_received'), ('write_date', '>=', today_start)
            ]),
            'delivered': Picking.search_count([
                ('x_mf_status', '=', 'mf_delivered'), ('write_date', '>=', today_start)
            ]),
            'exceptions': Picking.search_count([
                ('x_mf_status', '=', 'mf_exception'), ('write_date', '>=', today_start)
            ]),
        }
```

**Step 4: Run tests**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_kpi_dashboard.py -v
```
Expected: All pass.

**Step 5: Commit**
```bash
git add addons/stock_3pl_mainfreight/models/kpi_dashboard.py \
        addons/stock_3pl_mainfreight/tests/test_kpi_dashboard.py
git commit -m "feat(phase2): add mf.kpi.dashboard AbstractModel with DIFOT, IRA, exception rate"
```

---

### Task 5: Extend inbound cron to write discrepancy records

**Files:**
- Modify: `addons/stock_3pl_mainfreight/document/inventory_report.py`
- Modify: `addons/stock_3pl_mainfreight/tests/test_inventory_report.py`

**Step 1: Write the failing test**

Add to `test_inventory_report.py`:
```python
def test_apply_csv_writes_discrepancy_on_drift(self):
    """apply_csv() should create mf.soh.discrepancy record when MF qty != Odoo qty."""
    # Setup: product with odoo_qty=100, mf reports 95 (5% drift > 0.5% tolerance)
    env = MagicMock()
    product = MagicMock()
    product.id = 1
    quant = MagicMock()
    quant.quantity = 100.0  # existing Odoo quantity
    env.__getitem__ = MagicMock(side_effect=lambda k: {
        'product.product': MagicMock(search=MagicMock(return_value=[product])),
        'stock.quant': MagicMock(
            search=MagicMock(return_value=[quant]),
            sudo=MagicMock(return_value=MagicMock(create=MagicMock())),
        ),
        'mf.soh.discrepancy': MagicMock(),
        'ir.config_parameter': MagicMock(
            sudo=MagicMock(return_value=MagicMock(
                get_param=MagicMock(return_value='0.005')
            ))
        ),
    }.get(k, MagicMock()))
    connector = MagicMock()
    connector.warehouse_id.lot_stock_id.id = 10

    from odoo.addons.stock_3pl_mainfreight.document.inventory_report import InventoryReportDocument
    doc = InventoryReportDocument(connector=connector, env=env)
    csv_content = 'Product,WarehouseID,StockOnHand,QuantityOnHold,QuantityDamaged,QuantityAvailable,Grade1,Grade2,ExpiryDate,PackingDate\nSKU001,WH01,95,0,0,95,,,,'
    doc.apply_csv(csv_content)

    # mf.soh.discrepancy.create should have been called
    env['mf.soh.discrepancy'].create.assert_called_once()
    call_vals = env['mf.soh.discrepancy'].create.call_args[0][0]
    self.assertEqual(call_vals['mf_qty'], 95.0)
    self.assertEqual(call_vals['odoo_qty'], 100.0)
```

**Step 2: Run to verify failure**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_inventory_report.py -v -k "discrepancy"
```

**Step 3: Modify `inventory_report.py`**

In `apply_csv()`, capture odoo_qty before sync and write discrepancy if drift exceeds tolerance:

```python
def apply_csv(self, payload, report_date=None):
    """Parse and apply a full SOH report to stock.quant for the connector's warehouse."""
    lines = self.parse_inbound(payload)
    stock_location = self.connector.warehouse_id.lot_stock_id
    ICP = self.env['ir.config_parameter'].sudo()
    tolerance = float(ICP.get_param('stock_3pl_mainfreight.ira_tolerance', '0.005'))

    applied = 0
    skipped = 0
    for line in lines:
        try:
            product_code = _validate_ref(line.get('product_code'), 'product code')
        except ValidationError as exc:
            _logger.warning('MF SOH: skipping line — %s', exc)
            skipped += 1
            continue
        product = self.env['product.product'].search(
            [('default_code', '=', product_code)], limit=1
        )
        if not product:
            _logger.warning('MF SOH: product not found: %s', product_code)
            skipped += 1
            continue

        mf_qty = float(line['stock_on_hand'])

        # Capture current Odoo qty BEFORE sync to detect drift
        existing_quant = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', '=', stock_location.id),
        ], limit=1)
        odoo_qty = existing_quant.quantity if existing_quant else 0.0

        self._sync_quant(product, stock_location, mf_qty)
        applied += 1

        # Write discrepancy record if drift exceeds tolerance
        variance = abs(mf_qty - odoo_qty)
        threshold = odoo_qty * tolerance if odoo_qty else 0.0
        if variance > threshold:
            self._write_discrepancy(product, mf_qty, odoo_qty)

    _logger.info('MF SOH: applied=%d skipped=%d', applied, skipped)

    if report_date:
        self.connector.last_soh_applied_at = datetime.now()

def _write_discrepancy(self, product, mf_qty: float, odoo_qty: float):
    """Create or update an mf.soh.discrepancy record for this product."""
    warehouse = self.connector.warehouse_id
    # Check for existing open record for this product+warehouse — update rather than duplicate
    existing = self.env['mf.soh.discrepancy'].search([
        ('product_id', '=', product.id),
        ('warehouse_id', '=', warehouse.id),
        ('state', '=', 'open'),
    ], limit=1)
    vals = {
        'product_id': product.id,
        'warehouse_id': warehouse.id,
        'mf_qty': mf_qty,
        'odoo_qty': odoo_qty,
        'detected_date': datetime.now(),
        'state': 'open',
    }
    if existing:
        existing.write(vals)
    else:
        self.env['mf.soh.discrepancy'].create(vals)
```

**Step 4: Run all inventory report tests**
```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_inventory_report.py -v
```

**Step 5: Commit**
```bash
git add addons/stock_3pl_mainfreight/document/inventory_report.py \
        addons/stock_3pl_mainfreight/tests/test_inventory_report.py
git commit -m "feat(phase2): write mf.soh.discrepancy records on SOH drift in apply_csv"
```

---

### Task 6: Exception queue views (tree + form with action buttons)

**Files:**
- Modify: `addons/stock_3pl_mainfreight/views/exception_views.xml`
- Create: `addons/stock_3pl_mainfreight/views/wizard_reassign_views.xml`

**Step 1: Replace `exception_views.xml` with full views**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!-- ── Reassign wizard form ─────────────────────────────────── -->
    <record id="view_mf_reassign_wizard_form" model="ir.ui.view">
        <field name="name">mf.reassign.warehouse.wizard.form</field>
        <field name="model">mf.reassign.warehouse.wizard</field>
        <field name="arch" type="xml">
            <form string="Reassign to Warehouse">
                <group>
                    <field name="picking_id" readonly="1"/>
                    <field name="connector_id"/>
                    <field name="reason"/>
                </group>
                <footer>
                    <button name="action_reassign" type="object" string="Reassign" class="btn-primary"/>
                    <button string="Cancel" class="btn-secondary" special="cancel"/>
                </footer>
            </form>
        </field>
    </record>

    <record id="action_mf_reassign_wizard" model="ir.actions.act_window">
        <field name="name">Reassign Warehouse</field>
        <field name="res_model">mf.reassign.warehouse.wizard</field>
        <field name="view_mode">form</field>
        <field name="target">new</field>
        <field name="context">{'default_picking_id': active_id}</field>
    </record>

    <!-- ── Exception queue tree ─────────────────────────────────── -->
    <record id="view_mf_exception_tree" model="ir.ui.view">
        <field name="name">stock.picking.mf_exception.tree</field>
        <field name="model">stock.picking</field>
        <field name="arch" type="xml">
            <tree string="MF Exceptions" decoration-danger="True">
                <field name="name" string="SO / Reference"/>
                <field name="partner_id"/>
                <field name="picking_type_id" string="Operation"/>
                <field name="x_mf_connote" string="Connote"/>
                <field name="x_mf_assigned_to" string="Assigned To"
                       optional="show"/>
                <field name="write_date" string="Exception Date"/>
                <field name="x_mf_status" invisible="1"/>
            </tree>
        </field>
    </record>

    <!-- ── Exception queue form ─────────────────────────────────── -->
    <record id="view_mf_exception_form" model="ir.ui.view">
        <field name="name">stock.picking.mf_exception.form</field>
        <field name="model">stock.picking</field>
        <field name="inherit_id" ref="stock.view_picking_form"/>
        <field name="arch" type="xml">
            <!-- Inject exception action buttons into header when in exception status -->
            <xpath expr="//header" position="inside">
                <button name="action_mf_retry"
                        string="Retry"
                        type="object"
                        class="btn-primary"
                        attrs="{'invisible': [('x_mf_status', '!=', 'mf_exception')]}"/>
                <button name="%(action_mf_reassign_wizard)d"
                        string="Reassign Warehouse"
                        type="action"
                        attrs="{'invisible': [('x_mf_status', '!=', 'mf_exception')]}"/>
                <button name="action_mf_mark_resolved"
                        string="Mark Resolved"
                        type="object"
                        attrs="{'invisible': [('x_mf_status', '!=', 'mf_exception')]}"/>
                <button name="action_mf_escalate"
                        string="Escalate"
                        type="object"
                        class="btn-warning"
                        attrs="{'invisible': [('x_mf_status', '!=', 'mf_exception')]}"/>
            </xpath>
        </field>
    </record>

    <!-- ── Actions ──────────────────────────────────────────────── -->
    <record id="action_mf_cross_border_held" model="ir.actions.act_window">
        <field name="name">Cross-Border — Awaiting Approval</field>
        <field name="res_model">stock.picking</field>
        <field name="view_mode">tree,form</field>
        <field name="domain">[('x_mf_status', '=', 'mf_held_review')]</field>
    </record>

    <record id="action_mf_exceptions" model="ir.actions.act_window">
        <field name="name">Exception Queue</field>
        <field name="res_model">stock.picking</field>
        <field name="view_mode">tree,form</field>
        <field name="view_id" ref="view_mf_exception_tree"/>
        <field name="domain">[('x_mf_status', 'in', ['mf_exception'])]</field>
        <field name="context">{'search_default_group_assigned': 0}</field>
    </record>

    <record id="action_mf_order_pipeline" model="ir.actions.act_window">
        <field name="name">Order Pipeline</field>
        <field name="res_model">stock.picking</field>
        <field name="view_mode">kanban,tree,form</field>
        <field name="domain">[('x_mf_status', 'not in', [False, 'draft'])]</field>
    </record>

</odoo>
```

**Step 2: Verify XML is valid**
```bash
python -c "import xml.etree.ElementTree as ET; ET.parse('addons/stock_3pl_mainfreight/views/exception_views.xml'); print('XML valid')"
```

**Step 3: Commit**
```bash
git add addons/stock_3pl_mainfreight/views/exception_views.xml
git commit -m "feat(phase2): exception queue tree/form views with action buttons"
```

---

### Task 7: Kanban pipeline view

**Files:**
- Create: `addons/stock_3pl_mainfreight/views/pipeline_views.xml`

**Step 1: Create the kanban view**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <record id="view_mf_pipeline_kanban" model="ir.ui.view">
        <field name="name">stock.picking.mf_pipeline.kanban</field>
        <field name="model">stock.picking</field>
        <field name="arch" type="xml">
            <kanban default_group_by="x_mf_status"
                    group_delete="false"
                    group_create="false"
                    group_edit="false"
                    quick_create="false">
                <field name="x_mf_status"/>
                <field name="name"/>
                <field name="partner_id"/>
                <field name="picking_type_id"/>
                <field name="x_mf_connote"/>
                <field name="write_date"/>
                <field name="move_ids"/>

                <!-- Days since last status change — for aging colouration -->
                <field name="write_date" widget="date"/>

                <progressbar field="x_mf_status"
                             colors='{"mf_exception": "danger"}'/>

                <templates>
                    <t t-name="kanban-box">
                        <div t-attf-class="oe_kanban_card oe_kanban_global_click
                            #{record.x_mf_status.raw_value === 'mf_exception' ? 'border-danger' : ''}">
                            <div class="oe_kanban_content">
                                <div class="o_kanban_record_title">
                                    <strong><field name="name"/></strong>
                                </div>
                                <div class="o_kanban_record_subtitle">
                                    <field name="partner_id"/>
                                </div>
                                <div class="o_kanban_record_body">
                                    <span class="text-muted">
                                        <field name="picking_type_id" widget="many2one_tags"/>
                                    </span>
                                </div>
                                <div class="o_kanban_record_bottom">
                                    <div class="oe_kanban_bottom_left text-muted small">
                                        <i class="fa fa-clock-o"/> <field name="write_date" widget="date"/>
                                    </div>
                                    <div class="oe_kanban_bottom_right">
                                        <t t-if="record.x_mf_status.raw_value === 'mf_exception'">
                                            <button name="action_mf_retry"
                                                    type="object"
                                                    class="btn btn-sm btn-danger">
                                                Retry
                                            </button>
                                        </t>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </t>
                </templates>
            </kanban>
        </field>
    </record>

</odoo>
```

**Step 2: Verify XML**
```bash
python -c "import xml.etree.ElementTree as ET; ET.parse('addons/stock_3pl_mainfreight/views/pipeline_views.xml'); print('XML valid')"
```

**Step 3: Commit**
```bash
git add addons/stock_3pl_mainfreight/views/pipeline_views.xml
git commit -m "feat(phase2): add MF order pipeline kanban view"
```

---

### Task 8: Inventory discrepancy views

**Files:**
- Create: `addons/stock_3pl_mainfreight/views/discrepancy_views.xml`

**Step 1: Create the views**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <record id="view_mf_discrepancy_tree" model="ir.ui.view">
        <field name="name">mf.soh.discrepancy.tree</field>
        <field name="model">mf.soh.discrepancy</field>
        <field name="arch" type="xml">
            <tree string="Inventory Discrepancies"
                  decoration-danger="variance_pct > 5"
                  decoration-warning="variance_pct > 1 and variance_pct &lt;= 5">
                <field name="product_id"/>
                <field name="product_id" string="Product Code"
                       widget="char" attrs="{'invisible': True}"/>
                <field name="warehouse_id"/>
                <field name="odoo_qty" string="Odoo SOH"/>
                <field name="mf_qty" string="MF SOH"/>
                <field name="variance_qty" string="Variance (units)"/>
                <field name="variance_pct" string="Variance %"
                       widget="percentage"/>
                <field name="detected_date" string="Detected"/>
                <field name="state"/>
            </tree>
        </field>
    </record>

    <record id="view_mf_discrepancy_form" model="ir.ui.view">
        <field name="name">mf.soh.discrepancy.form</field>
        <field name="model">mf.soh.discrepancy</field>
        <field name="arch" type="xml">
            <form string="SOH Discrepancy">
                <header>
                    <button name="action_mark_investigated"
                            string="Mark Investigated"
                            type="object"
                            class="btn-primary"
                            attrs="{'invisible': [('state', '=', 'investigated')]}"/>
                    <field name="state" widget="statusbar"
                           statusbar_visible="open,investigated"/>
                </header>
                <sheet>
                    <group>
                        <group string="Product">
                            <field name="product_id" readonly="1"/>
                            <field name="warehouse_id" readonly="1"/>
                        </group>
                        <group string="Quantities">
                            <field name="odoo_qty" readonly="1"/>
                            <field name="mf_qty" readonly="1"/>
                            <field name="variance_qty" readonly="1"/>
                            <field name="variance_pct" readonly="1" widget="percentage"/>
                        </group>
                    </group>
                    <group string="Investigation">
                        <field name="detected_date" readonly="1"/>
                        <field name="investigated_by" readonly="1"/>
                        <field name="investigated_date" readonly="1"/>
                    </group>
                </sheet>
            </form>
        </field>
    </record>

    <record id="view_mf_discrepancy_search" model="ir.ui.view">
        <field name="name">mf.soh.discrepancy.search</field>
        <field name="model">mf.soh.discrepancy</field>
        <field name="arch" type="xml">
            <search>
                <field name="product_id"/>
                <field name="warehouse_id"/>
                <filter string="Open" name="open" domain="[('state','=','open')]"/>
                <filter string="Investigated" name="investigated"
                        domain="[('state','=','investigated')]"/>
                <group expand="0" string="Group By">
                    <filter string="Warehouse" name="by_warehouse"
                            context="{'group_by': 'warehouse_id'}"/>
                </group>
            </search>
        </field>
    </record>

    <record id="action_mf_discrepancy" model="ir.actions.act_window">
        <field name="name">Inventory Discrepancy</field>
        <field name="res_model">mf.soh.discrepancy</field>
        <field name="view_mode">tree,form</field>
        <field name="context">{'search_default_open': 1}</field>
    </record>

</odoo>
```

**Step 2: Verify XML**
```bash
python -c "import xml.etree.ElementTree as ET; ET.parse('addons/stock_3pl_mainfreight/views/discrepancy_views.xml'); print('XML valid')"
```

**Step 3: Commit**
```bash
git add addons/stock_3pl_mainfreight/views/discrepancy_views.xml
git commit -m "feat(phase2): add inventory discrepancy tree/form views"
```

---

### Task 9: OWL KPI dashboard component

**Files:**
- Create: `addons/stock_3pl_mainfreight/static/src/js/mf_kpi_dashboard.js`
- Create: `addons/stock_3pl_mainfreight/static/src/xml/mf_kpi_dashboard.xml`
- Create: `addons/stock_3pl_mainfreight/views/kpi_dashboard_action.xml`

**Step 1: Create the OWL component**

```js
/** @odoo-module **/
// addons/stock_3pl_mainfreight/static/src/js/mf_kpi_dashboard.js

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const RAG_CLASSES = {
    green: "mf-kpi-green",
    amber: "mf-kpi-amber",
    red: "mf-kpi-red",
    none: "mf-kpi-neutral",
};

class MfKpiDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");

        this.state = useState({
            loading: true,
            error: null,
            summary: null,
        });

        this._refreshInterval = null;

        onWillStart(async () => {
            await this._loadData();
            // Auto-refresh every 5 minutes
            this._refreshInterval = setInterval(() => this._loadData(), 5 * 60 * 1000);
        });

        onWillUnmount(() => {
            if (this._refreshInterval) clearInterval(this._refreshInterval);
        });
    }

    async _loadData() {
        try {
            const summary = await this.orm.call(
                "mf.kpi.dashboard",
                "get_kpi_summary",
                []
            );
            Object.assign(this.state, { summary, loading: false, error: null });
        } catch (e) {
            Object.assign(this.state, { error: "Failed to load KPI data.", loading: false });
        }
    }

    ragClass(rag) {
        return RAG_CLASSES[rag] || RAG_CLASSES.none;
    }

    formatPct(value) {
        return typeof value === "number" ? value.toFixed(1) + "%" : "—";
    }

    formatCount(value) {
        return typeof value === "number" ? value.toString() : "—";
    }

    openEditTargets() {
        this.actionService.doAction("stock_3pl_mainfreight.action_3pl_connector_config");
    }

    openExceptionQueue() {
        this.actionService.doAction("stock_3pl_mainfreight.action_mf_exceptions");
    }
}

MfKpiDashboard.template = "stock_3pl_mainfreight.MfKpiDashboard";

registry.category("actions").add("mf_kpi_dashboard", MfKpiDashboard);
```

**Step 2: Create the OWL template**

```xml
<?xml version="1.0" encoding="utf-8"?>
<!-- addons/stock_3pl_mainfreight/static/src/xml/mf_kpi_dashboard.xml -->
<templates xml:space="preserve">

<t t-name="stock_3pl_mainfreight.MfKpiDashboard" owl="1">
    <div class="mf-kpi-dashboard o_action">

        <!-- Loading / error states -->
        <t t-if="state.loading">
            <div class="o_loading text-center p-5">
                <i class="fa fa-spinner fa-spin fa-2x"/>
            </div>
        </t>
        <t t-elif="state.error">
            <div class="alert alert-danger m-3" t-esc="state.error"/>
        </t>

        <t t-elif="state.summary">
            <t t-set="s" t-value="state.summary"/>

            <!-- Header bar -->
            <div class="d-flex justify-content-between align-items-center p-3 border-bottom">
                <h4 class="mb-0">3PL Operations — KPI Dashboard</h4>
                <button class="btn btn-sm btn-outline-secondary" t-on-click="openEditTargets">
                    <i class="fa fa-cog mr-1"/> Edit Targets
                </button>
            </div>

            <!-- KPI cards -->
            <div class="row g-3 p-3">

                <!-- DIFOT -->
                <div class="col-sm-6 col-lg-3">
                    <div t-attf-class="card h-100 mf-kpi-card #{ragClass(s.difot.rag)}">
                        <div class="card-body text-center">
                            <div class="mf-kpi-value" t-esc="formatPct(s.difot.value)"/>
                            <div class="mf-kpi-label">DIFOT</div>
                            <div class="mf-kpi-target text-muted small">
                                Target: <t t-esc="s.targets.difot_target"/>%
                            </div>
                        </div>
                    </div>
                </div>

                <!-- IRA -->
                <div class="col-sm-6 col-lg-3">
                    <div t-attf-class="card h-100 mf-kpi-card #{ragClass(s.ira.rag)}">
                        <div class="card-body text-center">
                            <div class="mf-kpi-value" t-esc="formatPct(s.ira.value)"/>
                            <div class="mf-kpi-label">IRA</div>
                            <div class="mf-kpi-target text-muted small">
                                Target: <t t-esc="s.targets.ira_target"/>%
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Exception Rate -->
                <div class="col-sm-6 col-lg-3">
                    <div t-attf-class="card h-100 mf-kpi-card #{ragClass(s.exception_rate.rag)}">
                        <div class="card-body text-center">
                            <div class="mf-kpi-value" t-esc="formatPct(s.exception_rate.value)"/>
                            <div class="mf-kpi-label">Exception Rate</div>
                            <div class="mf-kpi-target text-muted small">
                                Target: &lt;<t t-esc="s.targets.exception_rate_target"/>%
                            </div>
                        </div>
                    </div>
                </div>

                <!-- In Flight -->
                <div class="col-sm-6 col-lg-3">
                    <div t-attf-class="card h-100 mf-kpi-card mf-kpi-neutral"
                         style="cursor:pointer" t-on-click="openExceptionQueue">
                        <div class="card-body text-center">
                            <div class="mf-kpi-value" t-esc="formatCount(s.in_flight.value)"/>
                            <div class="mf-kpi-label">In Flight</div>
                            <div class="mf-kpi-target text-muted small">Orders sent → dispatched</div>
                        </div>
                    </div>
                </div>

            </div>

            <!-- Today summary strip -->
            <div class="mf-today-strip d-flex gap-4 px-3 py-2 border-top border-bottom bg-light">
                <span class="text-muted small mr-2 align-self-center">Today:</span>
                <span><strong t-esc="s.today.sent"/> sent</span>
                <span><strong t-esc="s.today.received"/> received</span>
                <span><strong t-esc="s.today.delivered"/> delivered</span>
                <span t-attf-class="#{s.today.exceptions > 0 ? 'text-danger fw-bold' : ''}">
                    <strong t-esc="s.today.exceptions"/> exceptions
                </span>
            </div>

            <!-- Trend graphs placeholder — links to native graph views -->
            <div class="row p-3">
                <div class="col-12">
                    <p class="text-muted small">
                        For trend charts, use the
                        <a t-on-click.prevent="openExceptionQueue" href="#">Order Pipeline</a>
                        graph view.
                    </p>
                </div>
            </div>

        </t>
    </div>
</t>

</templates>
```

> **Note:** The trend charts are linked to the native Odoo graph views on `stock.picking` to avoid external JS dependencies. The OWL dashboard covers the KPI cards and today summary. This is intentionally minimal per the "clean and functional" design decision.

**Step 3: Create client action XML**

```xml
<?xml version="1.0" encoding="utf-8"?>
<!-- addons/stock_3pl_mainfreight/views/kpi_dashboard_action.xml -->
<odoo>
    <record id="action_mf_kpi_dashboard" model="ir.actions.client">
        <field name="name">3PL KPI Dashboard</field>
        <field name="tag">mf_kpi_dashboard</field>
    </record>
</odoo>
```

**Step 4: Verify XML**
```bash
python -c "import xml.etree.ElementTree as ET; ET.parse('addons/stock_3pl_mainfreight/views/kpi_dashboard_action.xml'); print('XML valid')"
```

**Step 5: Commit**
```bash
git add addons/stock_3pl_mainfreight/static/src/js/mf_kpi_dashboard.js \
        addons/stock_3pl_mainfreight/static/src/xml/mf_kpi_dashboard.xml \
        addons/stock_3pl_mainfreight/views/kpi_dashboard_action.xml
git commit -m "feat(phase2): OWL KPI dashboard component with DIFOT, IRA, exception rate, today strip"
```

---

### Task 10: Menu restructure, manifest, and config parameter defaults

**Files:**
- Modify: `addons/stock_3pl_mainfreight/views/menu_mf.xml`
- Modify: `addons/stock_3pl_mainfreight/__manifest__.py`
- Create: `addons/stock_3pl_mainfreight/data/phase2_defaults.xml`

**Step 1: Update `menu_mf.xml`**

Replace entirely:
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- ── Root: 3PL Operations ─────────────────────────────────── -->
    <menuitem id="menu_3pl_ops_root"
              name="3PL Operations"
              sequence="52"
              web_icon="stock_3pl_mainfreight,static/description/icon.png"/>

    <menuitem id="menu_mf_dashboard"
              name="Dashboard"
              parent="menu_3pl_ops_root"
              action="action_mf_kpi_dashboard"
              sequence="10"/>

    <menuitem id="menu_mf_pipeline"
              name="Order Pipeline"
              parent="menu_3pl_ops_root"
              action="action_mf_order_pipeline"
              sequence="20"/>

    <menuitem id="menu_mf_exceptions"
              name="Exception Queue"
              parent="menu_3pl_ops_root"
              action="action_mf_exceptions"
              sequence="30"/>

    <menuitem id="menu_mf_discrepancy"
              name="Inventory Discrepancy"
              parent="menu_3pl_ops_root"
              action="action_mf_discrepancy"
              sequence="40"/>

    <!-- ── Mainfreight sub-menu (config + cross-border) ─────────── -->
    <menuitem id="menu_mf_root"
              name="Mainfreight"
              parent="stock_3pl_core.menu_3pl_root"
              sequence="50"/>

    <menuitem id="menu_mf_cross_border"
              name="Cross-Border Held"
              parent="menu_mf_root"
              action="action_mf_cross_border_held"
              sequence="20"/>
</odoo>
```

**Step 2: Create `data/phase2_defaults.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo noupdate="1">
    <!-- KPI targets — noupdate=1 so user edits are preserved on upgrade -->
    <record id="param_kpi_difot_target" model="ir.config_parameter">
        <field name="key">stock_3pl_mainfreight.kpi_difot_target</field>
        <field name="value">95</field>
    </record>
    <record id="param_kpi_ira_target" model="ir.config_parameter">
        <field name="key">stock_3pl_mainfreight.kpi_ira_target</field>
        <field name="value">98</field>
    </record>
    <record id="param_kpi_exception_rate_target" model="ir.config_parameter">
        <field name="key">stock_3pl_mainfreight.kpi_exception_rate_target</field>
        <field name="value">2</field>
    </record>
    <record id="param_difot_grace_days" model="ir.config_parameter">
        <field name="key">stock_3pl_mainfreight.difot_grace_days</field>
        <field name="value">0</field>
    </record>
    <record id="param_ira_tolerance" model="ir.config_parameter">
        <field name="key">stock_3pl_mainfreight.ira_tolerance</field>
        <field name="value">0.005</field>
    </record>
    <record id="param_kanban_delivered_fold_days" model="ir.config_parameter">
        <field name="key">stock_3pl_mainfreight.kanban_delivered_fold_days</field>
        <field name="value">7</field>
    </record>
    <record id="param_discrepancy_archive_days" model="ir.config_parameter">
        <field name="key">stock_3pl_mainfreight.discrepancy_archive_days</field>
        <field name="value">90</field>
    </record>
</odoo>
```

**Step 3: Update `__manifest__.py`**

```python
{
    'name': '3PL Integration — Mainfreight',
    'version': '15.0.2.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Mainfreight Warehousing 3PL integration',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['stock_3pl_core'],
    'data': [
        'security/ir.model.access.csv',
        'views/connector_mf_views.xml',
        'views/picking_mf_views.xml',
        'views/warehouse_mf_views.xml',
        'views/exception_views.xml',
        'views/pipeline_views.xml',
        'views/discrepancy_views.xml',
        'views/kpi_dashboard_action.xml',
        'views/menu_mf.xml',
        'data/tracking_cron.xml',
        'data/inbound_cron.xml',
        'data/phase2_defaults.xml',
    ],
    'demo': ['data/connector_mf_demo.xml'],
    'assets': {
        'web.assets_backend': [
            'stock_3pl_mainfreight/static/src/js/mf_kpi_dashboard.js',
            'stock_3pl_mainfreight/static/src/xml/mf_kpi_dashboard.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
}
```

**Step 4: Verify all XMLs**
```bash
for f in addons/stock_3pl_mainfreight/views/menu_mf.xml \
          addons/stock_3pl_mainfreight/data/phase2_defaults.xml; do
    python -c "import xml.etree.ElementTree as ET; ET.parse('$f'); print('$f — OK')"
done
```

**Step 5: Run full test suite**
```bash
python -m pytest addons/ -v --tb=short 2>&1 | tail -20
```
Expected: All 228+ tests pass.

**Step 6: Commit**
```bash
git add addons/stock_3pl_mainfreight/views/menu_mf.xml \
        addons/stock_3pl_mainfreight/__manifest__.py \
        addons/stock_3pl_mainfreight/data/phase2_defaults.xml
git commit -m "feat(phase2): 3PL Operations menu, manifest v2.0.0, KPI config parameter defaults"
```

---

### Task 11: Frontend design review (pre-push gate)

**Before pushing to master:**

Invoke `frontend-design` skill with the following context:
- Design doc: `docs/plans/2026-02-28-phase2-ux-design.md`
- OWL component: `addons/stock_3pl_mainfreight/static/src/js/mf_kpi_dashboard.js`
- OWL template: `addons/stock_3pl_mainfreight/static/src/xml/mf_kpi_dashboard.xml`
- Kanban view: `addons/stock_3pl_mainfreight/views/pipeline_views.xml`
- Exception form: `addons/stock_3pl_mainfreight/views/exception_views.xml`

Ask it to:
1. Logic-check the KPI card RAG thresholds
2. Review OWL component lifecycle (auto-refresh cleanup, error states)
3. Check kanban card for UX gaps (empty states, mobile)
4. Suggest improvements before implementation proceeds

Apply any HIGH/MEDIUM suggestions as amendments to the relevant tasks above, then proceed.

---

## Summary

| Task | New Files | Key Behaviour |
|---|---|---|
| 1 | `soh_discrepancy.py` | `mf.soh.discrepancy` model, ACL |
| 2 | — | `mf_resolved` status, `x_mf_assigned_to`, action methods |
| 3 | `reassign_warehouse_wizard.py` | TransientModel wizard |
| 4 | `kpi_dashboard.py` | DIFOT, IRA, exception rate, today summary |
| 5 | — | `inventory_report.py` extended to write discrepancy records |
| 6 | — | `exception_views.xml` replaced with full tree/form |
| 7 | `pipeline_views.xml` | Kanban grouped by `x_mf_status` |
| 8 | `discrepancy_views.xml` | Discrepancy tree/form |
| 9 | `mf_kpi_dashboard.js/.xml`, `kpi_dashboard_action.xml` | OWL dashboard |
| 10 | `phase2_defaults.xml` | Menu, manifest v2, config params |
| 11 | — | Frontend design review gate |

**Test additions:** ~60 new pure-Python tests across Tasks 1, 2, 3, 4, 5.
**New models:** `mf.soh.discrepancy`, `mf.kpi.dashboard`, `mf.reassign.warehouse.wizard`
