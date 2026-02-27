# 3PL Integration Platform — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a forwarder-agnostic 3PL integration platform as two Odoo 15 addons, with Mainfreight Warehousing as the first implementation.

**Architecture:** `stock_3pl_core` provides the message queue, transport abstraction, and connector model. `stock_3pl_mainfreight` provides Mainfreight-specific document builders and transports. All documents pass through `3pl.message` — the single source of truth for every outbound and inbound exchange.

**Tech Stack:** Odoo 15, Python 3.8+, `requests` (REST), `paramiko` (SFTP), `lxml` (XML), `hashlib` (idempotency), `csv` (product/inventory formats)

**Design doc:** `docs/plans/2026-02-28-3pl-integration-platform-design.md`
**MF spec:** `docs/Mainfreight Warehousing Integration Specification.pdf`

---

## Repo Structure

```
addons/
  stock_3pl_core/          # Platform — forwarder-agnostic
  stock_3pl_mainfreight/   # Mainfreight implementation
docs/
CLAUDE.md
```

## Running Tests (Odoo 15)

```bash
# All tests for a module
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb

# Single test class
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestConnector

# With log output
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestConnector --log-level=test
```

---

## PHASE 1 — `stock_3pl_core` Module

---

### Task 1: Core module scaffold

**Files:**
- Create: `addons/stock_3pl_core/__manifest__.py`
- Create: `addons/stock_3pl_core/__init__.py`
- Create: `addons/stock_3pl_core/models/__init__.py`
- Create: `addons/stock_3pl_core/transport/__init__.py`
- Create: `addons/stock_3pl_core/wizard/__init__.py`
- Create: `addons/stock_3pl_core/tests/__init__.py`

**Step 1: Create `__manifest__.py`**

```python
# addons/stock_3pl_core/__manifest__.py
{
    'name': '3PL Integration Core',
    'version': '15.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Forwarder-agnostic 3PL warehousing integration platform',
    'depends': ['stock', 'sale_management', 'purchase'],
    'external_dependencies': {'python': ['paramiko']},
    'data': [
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/connector_views.xml',
        'views/message_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
}
```

**Step 2: Create `__init__.py` files**

```python
# addons/stock_3pl_core/__init__.py
from . import models, transport, wizard

# addons/stock_3pl_core/models/__init__.py
from . import connector, message

# addons/stock_3pl_core/transport/__init__.py
from . import rest_api, sftp, http_post

# addons/stock_3pl_core/wizard/__init__.py
from . import manual_sync_wizard, inbound_simulator

# addons/stock_3pl_core/tests/__init__.py
from . import test_connector, test_message, test_message_idempotency, test_retry_logic
```

**Step 3: Install module to verify scaffold**

```bash
python odoo-bin -i stock_3pl_core -d testdb --stop-after-init
```
Expected: Module installs without error.

**Step 4: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): scaffold stock_3pl_core module"
```

---

### Task 2: `3pl.connector` model

**Files:**
- Create: `addons/stock_3pl_core/models/connector.py`
- Create: `addons/stock_3pl_core/tests/test_connector.py`
- Create: `addons/stock_3pl_core/security/ir.model.access.csv`

**Step 1: Write failing tests**

```python
# addons/stock_3pl_core/tests/test_connector.py
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'connector')
class TestConnector(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)

    def test_connector_create(self):
        connector = self.env['3pl.connector'].create({
            'name': 'MF NZ Test',
            'warehouse_id': self.warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.assertEqual(connector.forwarder, 'mainfreight')
        self.assertEqual(connector.environment, 'test')
        self.assertTrue(connector.active)

    def test_connector_requires_warehouse(self):
        with self.assertRaises(Exception):
            self.env['3pl.connector'].create({
                'name': 'Bad Connector',
                'forwarder': 'mainfreight',
                'transport': 'rest_api',
            })

    def test_connector_last_soh_applied_at_default_none(self):
        connector = self.env['3pl.connector'].create({
            'name': 'MF NZ Test',
            'warehouse_id': self.warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'sftp',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.assertFalse(connector.last_soh_applied_at)
```

**Step 2: Run tests — verify they fail**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestConnector
```
Expected: FAIL — `3pl.connector` model not found.

**Step 3: Implement `connector.py`**

```python
# addons/stock_3pl_core/models/connector.py
from odoo import models, fields

FORWARDER_SELECTION = [
    ('mainfreight', 'Mainfreight'),
]

TRANSPORT_SELECTION = [
    ('rest_api', 'REST API'),
    ('sftp', 'SFTP'),
    ('http_post', 'HTTP POST'),
]

ENVIRONMENT_SELECTION = [
    ('test', 'Test'),
    ('production', 'Production'),
]


class ThreePlConnector(models.Model):
    _name = '3pl.connector'
    _description = '3PL Warehouse Connector'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    warehouse_id = fields.Many2one('stock.warehouse', required=True, ondelete='restrict')
    forwarder = fields.Selection(FORWARDER_SELECTION, required=True)
    transport = fields.Selection(TRANSPORT_SELECTION, required=True)
    environment = fields.Selection(ENVIRONMENT_SELECTION, required=True, default='test')
    region = fields.Char(help='e.g. NZ, AU, US — used for international routing')

    # 3PL identity
    customer_id = fields.Char('Customer ID', help='Unique ID assigned by the 3PL')
    warehouse_code = fields.Char('Warehouse Code', help='3PL warehouse identifier (e.g. 99)')

    # REST API credentials
    api_url = fields.Char('API URL')
    api_secret = fields.Char('API Secret')

    # SFTP credentials
    sftp_host = fields.Char('SFTP Host')
    sftp_port = fields.Integer('SFTP Port', default=22)
    sftp_username = fields.Char('SFTP Username')
    sftp_password = fields.Char('SFTP Password')
    sftp_inbound_path = fields.Char('SFTP Inbound Path', default='/in')
    sftp_outbound_path = fields.Char('SFTP Outbound Path', default='/out')

    # HTTP POST
    http_post_url = fields.Char('HTTP POST URL')
    http_transport_name = fields.Char('Transport Name (UniqueID)')

    # Alerting
    notify_user_id = fields.Many2one('res.users', 'Notify User on Dead Letter')

    # SOH guard
    last_soh_applied_at = fields.Datetime('Last SOH Applied At', readonly=True)

    message_ids = fields.One2many('3pl.message', 'connector_id', 'Messages')
    message_count = fields.Integer(compute='_compute_message_count')

    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)
```

**Step 4: Create `security/ir.model.access.csv`**

```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_3pl_connector_manager,3pl.connector manager,model_3pl_connector,stock.group_stock_manager,1,1,1,1
access_3pl_connector_user,3pl.connector user,model_3pl_connector,stock.group_stock_user,1,0,0,0
```

**Step 5: Run tests — verify they pass**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestConnector
```
Expected: 3 tests PASS.

**Step 6: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): add 3pl.connector model with warehouse mapping"
```

---

### Task 3: `3pl.message` model — fields and state machine

**Files:**
- Create: `addons/stock_3pl_core/models/message.py`
- Create: `addons/stock_3pl_core/tests/test_message.py`

**Step 1: Write failing tests**

```python
# addons/stock_3pl_core/tests/test_message.py
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'message')
class TestMessage(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test Connector',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })

    def test_outbound_message_create(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'ref_model': 'sale.order',
            'ref_id': 1,
        })
        self.assertEqual(msg.state, 'draft')
        self.assertEqual(msg.retry_count, 0)

    def test_outbound_state_transitions(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
        })
        msg.action_queue()
        self.assertEqual(msg.state, 'queued')
        msg.action_sending()
        self.assertEqual(msg.state, 'sending')
        msg.action_sent()
        self.assertEqual(msg.state, 'sent')
        msg.action_acknowledged()
        self.assertEqual(msg.state, 'acknowledged')

    def test_message_fail_and_retry(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
        })
        msg.action_queue()
        msg.action_sending()
        msg.action_fail('Timeout')
        self.assertEqual(msg.state, 'queued')
        self.assertEqual(msg.retry_count, 1)
        self.assertEqual(msg.last_error, 'Timeout')

    def test_message_dead_after_max_retries(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'retry_count': 2,
        })
        msg.action_queue()
        msg.action_sending()
        msg.action_fail('Final failure')
        self.assertEqual(msg.state, 'dead')

    def test_validation_error_goes_straight_to_dead(self):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
        })
        msg.action_queue()
        msg.action_sending()
        msg.action_validation_fail('Bad payload: missing ProductCode')
        self.assertEqual(msg.state, 'dead')
        self.assertEqual(msg.retry_count, 0)  # no retry consumed
```

**Step 2: Run — verify fail**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestMessage
```
Expected: FAIL — `3pl.message` model not found.

**Step 3: Implement `message.py`**

```python
# addons/stock_3pl_core/models/message.py
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

MAX_RETRIES = 3

DIRECTION = [('outbound', 'Outbound'), ('inbound', 'Inbound')]

DOCUMENT_TYPE = [
    ('product_spec', 'Product Specification'),
    ('sales_order', 'Sales Order'),
    ('inward_order', 'Inward Order'),
    ('so_confirmation', 'SO Confirmation'),
    ('inward_confirmation', 'Inward Confirmation'),
    ('inventory_report', 'Inventory Report'),
    ('inventory_adjustment', 'Inventory Adjustment'),
]

ACTION = [
    ('create', 'Create'),
    ('update', 'Update'),
    ('delete', 'Delete'),
]

OUTBOUND_STATES = [
    ('draft', 'Draft'),
    ('queued', 'Queued'),
    ('sending', 'Sending'),
    ('sent', 'Sent'),
    ('acknowledged', 'Acknowledged'),
    ('dead', 'Dead'),
]

INBOUND_STATES = [
    ('received', 'Received'),
    ('processing', 'Processing'),
    ('applied', 'Applied'),
    ('done', 'Done'),
    ('dead', 'Dead'),
]

ALL_STATES = list({s[0]: s for s in OUTBOUND_STATES + INBOUND_STATES}.items())


class ThreePlMessage(models.Model):
    _name = '3pl.message'
    _description = '3PL Message Queue'
    _order = 'create_date desc'

    connector_id = fields.Many2one('3pl.connector', required=True, ondelete='cascade')
    direction = fields.Selection(DIRECTION, required=True)
    document_type = fields.Selection(DOCUMENT_TYPE, required=True)
    action = fields.Selection(ACTION, default='create')
    state = fields.Selection(ALL_STATES, default='draft', index=True)

    # Payloads
    payload_xml = fields.Text('XML Payload')
    payload_json = fields.Text('JSON Payload')
    payload_csv = fields.Text('CSV Payload')

    # Source record
    ref_model = fields.Char('Source Model')
    ref_id = fields.Integer('Source Record ID')

    # 3PL references
    forwarder_ref = fields.Char('Forwarder Reference')

    # Idempotency / deduplication
    idempotency_key = fields.Char(index=True)
    source_hash = fields.Char(index=True)
    report_date = fields.Date('Report Date')

    # Retry
    retry_count = fields.Integer(default=0)
    last_error = fields.Text('Last Error')

    # Timestamps
    sent_at = fields.Datetime('Sent At', readonly=True)
    acked_at = fields.Datetime('Acknowledged At', readonly=True)

    # --- State transitions ---

    def action_queue(self):
        self.write({'state': 'queued'})

    def action_sending(self):
        self.write({'state': 'sending'})

    def action_sent(self):
        self.write({'state': 'sent', 'sent_at': fields.Datetime.now()})

    def action_acknowledged(self):
        self.write({'state': 'acknowledged', 'acked_at': fields.Datetime.now()})

    def action_fail(self, error_msg):
        """Retry if under MAX_RETRIES, otherwise dead-letter."""
        for msg in self:
            if msg.retry_count >= MAX_RETRIES - 1:
                msg._dead_letter(error_msg)
            else:
                msg.write({
                    'state': 'queued',
                    'retry_count': msg.retry_count + 1,
                    'last_error': error_msg,
                })

    def action_validation_fail(self, error_msg):
        """Validation failures go straight to dead — retrying won't fix a bad payload."""
        for msg in self:
            msg._dead_letter(error_msg)

    def action_requeue(self):
        """Manual requeue from dead letter."""
        self.write({'state': 'queued', 'retry_count': 0, 'last_error': False})

    def _dead_letter(self, error_msg):
        self.write({'state': 'dead', 'last_error': error_msg})
        if self.connector_id.notify_user_id:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=self.connector_id.notify_user_id.id,
                note=f'3PL message dead-lettered: {self.document_type} — {error_msg}',
            )
        _logger.error('3PL message %s dead-lettered: %s', self.id, error_msg)

    # Inbound state transitions
    def action_processing(self):
        self.write({'state': 'processing'})

    def action_applied(self):
        self.write({'state': 'applied'})

    def action_done(self):
        self.write({'state': 'done'})
```

**Step 4: Add access rules to CSV**

```csv
access_3pl_message_manager,3pl.message manager,model_3pl_message,stock.group_stock_manager,1,1,1,1
access_3pl_message_user,3pl.message user,model_3pl_message,stock.group_stock_user,1,0,0,0
```

**Step 5: Run tests — verify pass**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestMessage
```
Expected: 5 tests PASS.

**Step 6: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): add 3pl.message model with state machine and retry logic"
```

---

### Task 4: Idempotency and deduplication on `3pl.message`

**Files:**
- Modify: `addons/stock_3pl_core/models/message.py`
- Create: `addons/stock_3pl_core/tests/test_message_idempotency.py`

**Step 1: Write failing tests**

```python
# addons/stock_3pl_core/tests/test_message_idempotency.py
import hashlib
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError

@tagged('post_install', '-at_install', 'idempotency')
class TestMessageIdempotency(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test Connector',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })

    def _make_key(self, connector_id, doc_type, ref):
        raw = f'{connector_id}:{doc_type}:{ref}'
        return hashlib.sha256(raw.encode()).hexdigest()

    def test_duplicate_outbound_blocked(self):
        key = self._make_key(self.connector.id, 'sales_order', 'SO001')
        self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'idempotency_key': key,
        })
        with self.assertRaises(Exception):
            self.env['3pl.message'].create({
                'connector_id': self.connector.id,
                'direction': 'outbound',
                'document_type': 'sales_order',
                'action': 'create',
                'idempotency_key': key,
            })

    def test_duplicate_inbound_blocked_by_source_hash(self):
        raw = '<SCH>test</SCH>'
        h = hashlib.sha256(raw.encode()).hexdigest()
        self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'inbound',
            'document_type': 'so_confirmation',
            'source_hash': h,
        })
        with self.assertRaises(Exception):
            self.env['3pl.message'].create({
                'connector_id': self.connector.id,
                'direction': 'inbound',
                'document_type': 'so_confirmation',
                'source_hash': h,
            })

    def test_stale_soh_report_rejected(self):
        from datetime import date, timedelta
        self.connector.last_soh_applied_at = fields.Datetime.now()
        yesterday = date.today() - timedelta(days=1)
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'inbound',
            'document_type': 'inventory_report',
            'report_date': yesterday,
        })
        result = msg.is_stale()
        self.assertTrue(result)

    def test_fresh_soh_report_not_stale(self):
        from datetime import date, timedelta
        import pytz
        self.connector.last_soh_applied_at = fields.Datetime.now() - timedelta(days=2)
        today = date.today()
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'inbound',
            'document_type': 'inventory_report',
            'report_date': today,
        })
        result = msg.is_stale()
        self.assertFalse(result)
```

**Step 2: Run — verify fail**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestMessageIdempotency
```

**Step 3: Add SQL constraints and `is_stale()` to `message.py`**

```python
# Add to ThreePlMessage class in message.py:

_sql_constraints = [
    (
        'unique_idempotency_key',
        'UNIQUE(connector_id, idempotency_key)',
        'An outbound message with this idempotency key already exists for this connector.',
    ),
    (
        'unique_source_hash',
        'UNIQUE(connector_id, source_hash)',
        'An inbound message with this payload hash already exists for this connector.',
    ),
]

def is_stale(self):
    """Return True if this inbound inventory report is older than the last applied."""
    self.ensure_one()
    if not self.connector_id.last_soh_applied_at or not self.report_date:
        return False
    from datetime import datetime
    last = self.connector_id.last_soh_applied_at.date()
    return self.report_date <= last
```

**Step 4: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestMessageIdempotency
```
Expected: 4 tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): add idempotency key and source hash deduplication constraints"
```

---

### Task 5: Transport base class and REST transport

**Files:**
- Create: `addons/stock_3pl_core/models/transport_base.py`
- Create: `addons/stock_3pl_core/transport/rest_api.py`
- Create: `addons/stock_3pl_core/tests/test_transport_rest.py`

**Step 1: Write failing tests**

```python
# addons/stock_3pl_core/tests/test_transport_rest.py
from unittest.mock import patch, MagicMock
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'transport')
class TestRestTransport(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test REST',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
            'api_url': 'https://test.example.com',
            'api_secret': 'secret123',
        })

    def _make_transport(self):
        from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport
        return RestTransport(self.connector)

    @patch('requests.post')
    def test_send_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text='OK')
        transport = self._make_transport()
        result = transport.send('<Order><Ref>SO001</Ref></Order>', content_type='xml')
        self.assertTrue(result['success'])

    @patch('requests.post')
    def test_send_409_treated_as_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=409, text='Conflict')
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertTrue(result['success'])
        self.assertEqual(result['note'], 'already_exists')

    @patch('requests.post')
    def test_send_422_raises_validation_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=422, text='Bad payload')
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_type'], 'validation')

    @patch('requests.post')
    def test_send_500_raises_retriable_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text='Server Error')
        transport = self._make_transport()
        result = transport.send('<Order/>', content_type='xml')
        self.assertFalse(result['success'])
        self.assertEqual(result['error_type'], 'retriable')
```

**Step 2: Run — verify fail**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestRestTransport
```

**Step 3: Implement `transport_base.py`**

```python
# addons/stock_3pl_core/models/transport_base.py

class AbstractTransport:
    """Base class for all 3PL transport adapters."""

    def __init__(self, connector):
        self.connector = connector

    def send(self, payload, content_type='xml', endpoint=None):
        """Send an outbound payload. Returns dict: {success, note, error_type}."""
        raise NotImplementedError

    def poll(self, path=None):
        """Poll for inbound files/responses. Returns list of raw payloads."""
        raise NotImplementedError

    def _success(self, note=None):
        return {'success': True, 'note': note}

    def _retriable_error(self, msg):
        return {'success': False, 'error_type': 'retriable', 'error': msg}

    def _validation_error(self, msg):
        return {'success': False, 'error_type': 'validation', 'error': msg}
```

**Step 4: Implement `transport/rest_api.py`**

```python
# addons/stock_3pl_core/transport/rest_api.py
import requests
import logging
from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport

_logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    'xml': 'application/xml',
    'json': 'application/json',
    'csv': 'text/csv',
}


class RestTransport(AbstractTransport):

    def send(self, payload, content_type='xml', endpoint=None):
        url = endpoint or self.connector.api_url
        headers = {
            'Content-Type': CONTENT_TYPES.get(content_type, 'application/xml'),
            'Authorization': f'Bearer {self.connector.api_secret}',
        }
        try:
            resp = requests.post(url, data=payload.encode('utf-8'), headers=headers, timeout=30)
        except requests.Timeout:
            return self._retriable_error('Request timed out')
        except requests.ConnectionError as e:
            return self._retriable_error(f'Connection error: {e}')

        if resp.status_code in (200, 201):
            return self._success()
        elif resp.status_code == 409:
            return self._success(note='already_exists')
        elif resp.status_code == 422:
            return self._validation_error(resp.text)
        else:
            return self._retriable_error(f'HTTP {resp.status_code}: {resp.text}')

    def poll(self, path=None):
        url = path or self.connector.api_url
        headers = {'Authorization': f'Bearer {self.connector.api_secret}'}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return [resp.text]
        except Exception as e:
            _logger.warning('REST poll failed: %s', e)
        return []
```

**Step 5: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestRestTransport
```
Expected: 4 tests PASS.

**Step 6: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): add transport base class and REST transport adapter"
```

---

### Task 6: SFTP and HTTP POST transports

**Files:**
- Create: `addons/stock_3pl_core/transport/sftp.py`
- Create: `addons/stock_3pl_core/transport/http_post.py`

**Step 1: Implement `transport/sftp.py`**

```python
# addons/stock_3pl_core/transport/sftp.py
import paramiko
import io
import logging
from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport

_logger = logging.getLogger(__name__)


class SftpTransport(AbstractTransport):

    def _get_client(self):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=self.connector.sftp_host,
            port=self.connector.sftp_port or 22,
            username=self.connector.sftp_username,
            password=self.connector.sftp_password,
            timeout=30,
        )
        return ssh.open_sftp(), ssh

    def send(self, payload, content_type='xml', filename=None, endpoint=None):
        sftp, ssh = None, None
        try:
            sftp, ssh = self._get_client()
            path = f"{self.connector.sftp_outbound_path}/{filename}"
            sftp.putfo(io.BytesIO(payload.encode('utf-8')), path)
            return self._success()
        except Exception as e:
            _logger.error('SFTP send failed: %s', e)
            return self._retriable_error(str(e))
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()

    def poll(self, path=None):
        """Retrieve and delete all files from inbound SFTP path."""
        sftp, ssh = None, None
        results = []
        try:
            sftp, ssh = self._get_client()
            inbound = path or self.connector.sftp_inbound_path
            files = sftp.listdir(inbound)
            for fname in files:
                fpath = f'{inbound}/{fname}'
                with sftp.open(fpath, 'r') as f:
                    results.append(f.read().decode('utf-8'))
                sftp.remove(fpath)  # Delete immediately after pickup
        except Exception as e:
            _logger.warning('SFTP poll failed: %s', e)
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()
        return results
```

**Step 2: Implement `transport/http_post.py`**

```python
# addons/stock_3pl_core/transport/http_post.py
import requests
import logging
from odoo.addons.stock_3pl_core.models.transport_base import AbstractTransport

_logger = logging.getLogger(__name__)


class HttpPostTransport(AbstractTransport):

    def send(self, payload, content_type='xml', endpoint=None):
        url = self.connector.http_post_url
        transport_name = self.connector.http_transport_name
        full_url = f'{url}?TransportName={transport_name}'
        try:
            resp = requests.post(
                full_url,
                data=payload.encode('utf-8'),
                headers={'Content-Type': 'multipart/form-data'},
                timeout=30,
            )
            if resp.status_code in (200, 201):
                return self._success()
            return self._retriable_error(f'HTTP {resp.status_code}: {resp.text}')
        except requests.Timeout:
            return self._retriable_error('Request timed out')
        except requests.ConnectionError as e:
            return self._retriable_error(str(e))

    def poll(self, path=None):
        return []  # HTTP POST is push-only from Mainfreight side
```

**Step 3: Add transport factory to connector**

Add to `models/connector.py`:
```python
def get_transport(self):
    """Return the appropriate transport adapter for this connector."""
    self.ensure_one()
    if self.transport == 'rest_api':
        from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport
        return RestTransport(self)
    elif self.transport == 'sftp':
        from odoo.addons.stock_3pl_core.transport.sftp import SftpTransport
        return SftpTransport(self)
    elif self.transport == 'http_post':
        from odoo.addons.stock_3pl_core.transport.http_post import HttpPostTransport
        return HttpPostTransport(self)
    raise NotImplementedError(f'No transport for: {self.transport}')
```

**Step 4: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): add SFTP and HTTP POST transports + connector.get_transport() factory"
```

---

### Task 7: Document base class and `FreightForwarderMixin`

**Files:**
- Create: `addons/stock_3pl_core/models/document_base.py`

**Step 1: Implement**

```python
# addons/stock_3pl_core/models/document_base.py
import hashlib


class AbstractDocument:
    """
    Base class for all 3PL document builders and parsers.
    Subclasses implement build_outbound() or parse_inbound().
    """
    document_type = None  # Must be set by subclass
    format = 'xml'  # 'xml', 'csv', 'json'

    def __init__(self, connector, env):
        self.connector = connector
        self.env = env

    def build_outbound(self, record):
        """Build payload from an Odoo record. Returns str."""
        raise NotImplementedError

    def parse_inbound(self, payload):
        """Parse raw inbound payload and return structured dict."""
        raise NotImplementedError

    def apply_inbound(self, message):
        """Apply a parsed inbound message to Odoo records."""
        raise NotImplementedError

    def get_filename(self, record):
        """Return a unique filename for SFTP/file transfer."""
        raise NotImplementedError

    @staticmethod
    def hash_payload(payload):
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    @staticmethod
    def make_idempotency_key(connector_id, document_type, odoo_ref):
        raw = f'{connector_id}:{document_type}:{odoo_ref}'
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def truncate(self, value, max_len):
        """Truncate string to MF field max length."""
        if not value:
            return ''
        return str(value)[:max_len]


class FreightForwarderMixin:
    """
    Mixin for document builders supporting multiple freight forwarders.
    Register field mappings per forwarder via FIELD_MAP.
    """
    FIELD_MAP = {}  # {'mainfreight': {'odoo_field': 'mf_field', ...}}

    def get_field_map(self, forwarder):
        return self.FIELD_MAP.get(forwarder, {})
```

**Step 2: Commit**

```bash
git add addons/stock_3pl_core/models/document_base.py
git commit -m "feat(core): add AbstractDocument base class and FreightForwarderMixin"
```

---

### Task 8: Outbound queue processor (scheduled action)

**Files:**
- Create: `addons/stock_3pl_core/data/cron.xml`
- Modify: `addons/stock_3pl_core/models/message.py`
- Create: `addons/stock_3pl_core/tests/test_retry_logic.py`

**Step 1: Write failing tests**

```python
# addons/stock_3pl_core/tests/test_retry_logic.py
from unittest.mock import patch
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install', 'retry')
class TestRetryLogic(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'Test',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
            'api_url': 'https://test.example.com',
            'api_secret': 'secret',
        })

    def _make_queued_message(self, payload='<test/>'):
        msg = self.env['3pl.message'].create({
            'connector_id': self.connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'payload_xml': payload,
            'state': 'queued',
        })
        return msg

    @patch('requests.post')
    def test_process_queued_sends_and_marks_sent(self, mock_post):
        from unittest.mock import MagicMock
        mock_post.return_value = MagicMock(status_code=200, text='OK')
        msg = self._make_queued_message()
        self.env['3pl.message']._process_outbound_queue()
        msg.invalidate_cache()
        self.assertEqual(msg.state, 'sent')

    @patch('requests.post')
    def test_process_queued_on_500_increments_retry(self, mock_post):
        from unittest.mock import MagicMock
        mock_post.return_value = MagicMock(status_code=500, text='Error')
        msg = self._make_queued_message()
        self.env['3pl.message']._process_outbound_queue()
        msg.invalidate_cache()
        self.assertEqual(msg.state, 'queued')
        self.assertEqual(msg.retry_count, 1)

    @patch('requests.post')
    def test_process_queued_on_422_dead_letters(self, mock_post):
        from unittest.mock import MagicMock
        mock_post.return_value = MagicMock(status_code=422, text='Bad data')
        msg = self._make_queued_message()
        self.env['3pl.message']._process_outbound_queue()
        msg.invalidate_cache()
        self.assertEqual(msg.state, 'dead')
```

**Step 2: Add `_process_outbound_queue` to `message.py`**

```python
# Add to ThreePlMessage class:

@api.model
def _process_outbound_queue(self):
    """Called by cron. Process all queued outbound messages."""
    queued = self.search([('state', '=', 'queued'), ('direction', '=', 'outbound')])
    for msg in queued:
        try:
            msg.action_sending()
            transport = msg.connector_id.get_transport()
            payload = msg.payload_xml or msg.payload_json or msg.payload_csv
            result = transport.send(payload, content_type=msg._detect_content_type())
            if result['success']:
                msg.action_sent()
            elif result.get('error_type') == 'validation':
                msg.action_validation_fail(result.get('error', 'Validation error'))
            else:
                msg.action_fail(result.get('error', 'Unknown error'))
        except Exception as e:
            msg.action_fail(str(e))

def _detect_content_type(self):
    if self.payload_xml:
        return 'xml'
    if self.payload_json:
        return 'json'
    return 'csv'
```

**Step 3: Create `data/cron.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="cron_process_outbound_queue" model="ir.cron">
        <field name="name">3PL: Process Outbound Queue</field>
        <field name="model_id" ref="model_3pl_message"/>
        <field name="state">code</field>
        <field name="code">model._process_outbound_queue()</field>
        <field name="interval_number">5</field>
        <field name="interval_type">minutes</field>
        <field name="numbercall">-1</field>
        <field name="active">True</field>
    </record>

    <record id="cron_poll_inbound" model="ir.cron">
        <field name="name">3PL: Poll Inbound Messages</field>
        <field name="model_id" ref="model_3pl_message"/>
        <field name="state">code</field>
        <field name="code">model._poll_inbound()</field>
        <field name="interval_number">15</field>
        <field name="interval_type">minutes</field>
        <field name="numbercall">-1</field>
        <field name="active">True</field>
    </record>
</odoo>
```

**Step 4: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestRetryLogic
```
Expected: 3 tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): add outbound queue processor cron and retry handling"
```

---

### Task 9: Views, menus, and manual sync wizard

**Files:**
- Create: `addons/stock_3pl_core/views/connector_views.xml`
- Create: `addons/stock_3pl_core/views/message_views.xml`
- Create: `addons/stock_3pl_core/views/menu.xml`
- Create: `addons/stock_3pl_core/wizard/manual_sync_wizard.py`
- Create: `addons/stock_3pl_core/wizard/inbound_simulator.py`

**Step 1: Create `views/connector_views.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_3pl_connector_form" model="ir.ui.view">
        <field name="name">3pl.connector.form</field>
        <field name="model">3pl.connector</field>
        <field name="arch" type="xml">
            <form>
                <header>
                    <button name="action_test_connection" type="object"
                            string="Test Connection" class="btn-primary"/>
                </header>
                <sheet>
                    <group>
                        <field name="name"/>
                        <field name="active"/>
                        <field name="warehouse_id"/>
                        <field name="forwarder"/>
                        <field name="environment"/>
                        <field name="region"/>
                    </group>
                    <group string="3PL Identity">
                        <field name="customer_id"/>
                        <field name="warehouse_code"/>
                    </group>
                    <notebook>
                        <page string="REST API" attrs="{'invisible': [('transport', '!=', 'rest_api')]}">
                            <group>
                                <field name="transport" invisible="1"/>
                                <field name="api_url"/>
                                <field name="api_secret" password="True"/>
                            </group>
                        </page>
                        <page string="SFTP" attrs="{'invisible': [('transport', '!=', 'sftp')]}">
                            <group>
                                <field name="sftp_host"/>
                                <field name="sftp_port"/>
                                <field name="sftp_username"/>
                                <field name="sftp_password" password="True"/>
                                <field name="sftp_outbound_path"/>
                                <field name="sftp_inbound_path"/>
                            </group>
                        </page>
                        <page string="HTTP POST" attrs="{'invisible': [('transport', '!=', 'http_post')]}">
                            <group>
                                <field name="http_post_url"/>
                                <field name="http_transport_name"/>
                            </group>
                        </page>
                        <page string="Settings">
                            <group>
                                <field name="notify_user_id"/>
                                <field name="last_soh_applied_at"/>
                            </group>
                        </page>
                    </notebook>
                </sheet>
            </form>
        </field>
    </record>

    <record id="view_3pl_connector_tree" model="ir.ui.view">
        <field name="name">3pl.connector.tree</field>
        <field name="model">3pl.connector</field>
        <field name="arch" type="xml">
            <tree>
                <field name="name"/>
                <field name="warehouse_id"/>
                <field name="forwarder"/>
                <field name="transport"/>
                <field name="environment"/>
                <field name="active"/>
            </tree>
        </field>
    </record>

    <record id="action_3pl_connector" model="ir.actions.act_window">
        <field name="name">3PL Connectors</field>
        <field name="res_model">3pl.connector</field>
        <field name="view_mode">tree,form</field>
    </record>
</odoo>
```

**Step 2: Create `views/message_views.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_3pl_message_tree" model="ir.ui.view">
        <field name="name">3pl.message.tree</field>
        <field name="model">3pl.message</field>
        <field name="arch" type="xml">
            <tree decoration-danger="state == 'dead'" decoration-warning="retry_count > 0">
                <field name="create_date"/>
                <field name="connector_id"/>
                <field name="direction"/>
                <field name="document_type"/>
                <field name="action"/>
                <field name="state"/>
                <field name="retry_count"/>
                <field name="forwarder_ref"/>
            </tree>
        </field>
    </record>

    <record id="view_3pl_message_form" model="ir.ui.view">
        <field name="name">3pl.message.form</field>
        <field name="model">3pl.message</field>
        <field name="arch" type="xml">
            <form>
                <header>
                    <button name="action_requeue" type="object" string="Requeue"
                            attrs="{'invisible': [('state', '!=', 'dead')]}" class="btn-warning"/>
                    <field name="state" widget="statusbar"/>
                </header>
                <sheet>
                    <group>
                        <group>
                            <field name="connector_id"/>
                            <field name="direction"/>
                            <field name="document_type"/>
                            <field name="action"/>
                            <field name="ref_model"/>
                            <field name="ref_id"/>
                        </group>
                        <group>
                            <field name="retry_count"/>
                            <field name="forwarder_ref"/>
                            <field name="report_date"/>
                            <field name="sent_at"/>
                            <field name="acked_at"/>
                        </group>
                    </group>
                    <group string="Error" attrs="{'invisible': [('last_error', '=', False)]}">
                        <field name="last_error" nolabel="1"/>
                    </group>
                    <notebook>
                        <page string="XML Payload" attrs="{'invisible': [('payload_xml', '=', False)]}">
                            <field name="payload_xml" nolabel="1" widget="code"/>
                        </page>
                        <page string="CSV Payload" attrs="{'invisible': [('payload_csv', '=', False)]}">
                            <field name="payload_csv" nolabel="1" widget="code"/>
                        </page>
                    </notebook>
                </sheet>
            </form>
        </field>
    </record>

    <record id="action_3pl_message_dead" model="ir.actions.act_window">
        <field name="name">Dead Letters</field>
        <field name="res_model">3pl.message</field>
        <field name="view_mode">tree,form</field>
        <field name="domain">[('state', '=', 'dead')]</field>
    </record>

    <record id="action_3pl_message_all" model="ir.actions.act_window">
        <field name="name">All Messages</field>
        <field name="res_model">3pl.message</field>
        <field name="view_mode">tree,form</field>
    </record>
</odoo>
```

**Step 3: Create `views/menu.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <menuitem id="menu_3pl_root" name="3PL Integration"
              parent="stock.menu_stock_config_settings" sequence="100"/>
    <menuitem id="menu_3pl_connectors" name="Connectors"
              parent="menu_3pl_root" action="action_3pl_connector" sequence="10"/>
    <menuitem id="menu_3pl_messages" name="Message Queue"
              parent="menu_3pl_root" action="action_3pl_message_all" sequence="20"/>
    <menuitem id="menu_3pl_dead_letters" name="Dead Letters"
              parent="menu_3pl_root" action="action_3pl_message_dead" sequence="30"/>
</odoo>
```

**Step 4: Commit**

```bash
git add addons/stock_3pl_core/
git commit -m "feat(core): add views, menus, connector form with tabbed transport config"
```

---

## PHASE 2 — `stock_3pl_mainfreight` Module

---

### Task 10: Mainfreight module scaffold and connector extension

**Files:**
- Create: `addons/stock_3pl_mainfreight/__manifest__.py`
- Create: `addons/stock_3pl_mainfreight/__init__.py`
- Create: `addons/stock_3pl_mainfreight/models/__init__.py`
- Create: `addons/stock_3pl_mainfreight/models/connector_mf.py`
- Create: `addons/stock_3pl_mainfreight/document/__init__.py`
- Create: `addons/stock_3pl_mainfreight/transport/__init__.py`
- Create: `addons/stock_3pl_mainfreight/tests/__init__.py`

**Step 1: Create `__manifest__.py`**

```python
# addons/stock_3pl_mainfreight/__manifest__.py
{
    'name': '3PL Integration — Mainfreight',
    'version': '15.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Mainfreight Warehousing 3PL integration',
    'depends': ['stock_3pl_core'],
    'data': [
        'security/ir.model.access.csv',
        'views/connector_mf_views.xml',
        'data/connector_mf_demo.xml',
    ],
    'demo': ['data/connector_mf_demo.xml'],
    'installable': True,
}
```

**Step 2: Extend connector with MF-specific fields**

```python
# addons/stock_3pl_mainfreight/models/connector_mf.py
from odoo import models, fields

MF_ENVIRONMENTS = {
    'test': {
        'rest_api': 'https://warehouseapi-test.mainfreight.com/api/v1.1',
        'sftp': 'xftp.mainfreight.com',
        'http_post': 'https://securetest.mainfreight.com/crossfire/submit.aspx',
    },
    'production': {
        'rest_api': 'https://warehouseapi.mainfreight.com/api/v1.1',
        'sftp': 'xftp.mainfreight.com',
        'http_post': 'https://secure.mainfreight.co.nz/crossfire/submit.aspx',
    },
}


class ThreePlConnectorMF(models.Model):
    _inherit = '3pl.connector'

    # MF REST API secrets (separate per API type per MF spec)
    mf_warehousing_secret = fields.Char('Warehousing API Secret')
    mf_label_secret = fields.Char('Label API Secret')
    mf_rating_secret = fields.Char('Rating API Secret')
    mf_tracking_secret = fields.Char('Tracking API Secret')

    def action_test_connection(self):
        """Test REST API connectivity to MF."""
        self.ensure_one()
        transport = self.get_transport()
        result = transport.send('<ping/>', endpoint=self._mf_endpoint('order'))
        if result['success'] or result.get('note') == 'already_exists':
            return self._notify('Connection to Mainfreight successful.')
        return self._notify(f"Connection failed: {result.get('error')}", error=True)

    def _mf_endpoint(self, resource):
        env = self.environment or 'test'
        base = MF_ENVIRONMENTS[env]['rest_api']
        endpoints = {
            'order': f'{base}/Order',
            'inward': f'{base}/Inward',
            'soh': f'{base}/StockOnHand',
        }
        return endpoints.get(resource, base)

    def _notify(self, message, error=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'type': 'danger' if error else 'success',
                'sticky': False,
            },
        }
```

**Step 3: Create demo connector**

```xml
<!-- addons/stock_3pl_mainfreight/data/connector_mf_demo.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="connector_mf_nz_test" model="3pl.connector">
        <field name="name">Mainfreight NZ (Test)</field>
        <field name="forwarder">mainfreight</field>
        <field name="transport">rest_api</field>
        <field name="environment">test</field>
        <field name="region">NZ</field>
        <field name="customer_id">REPLACE_WITH_MF_CUSTOMER_ID</field>
        <field name="warehouse_code">REPLACE_WITH_MF_WAREHOUSE_CODE</field>
        <field name="active">True</field>
    </record>
</odoo>
```

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/
git commit -m "feat(mf): scaffold stock_3pl_mainfreight module with connector extension"
```

---

### Task 11: Product Specification document builder

**Files:**
- Create: `addons/stock_3pl_mainfreight/document/product_spec.py`
- Create: `addons/stock_3pl_mainfreight/tests/test_product_spec.py`
- Create: `addons/stock_3pl_mainfreight/tests/fixtures/` (fixture helpers)

**Step 1: Write failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_product_spec.py
from odoo.tests import TransactionCase, tagged
import csv, io

@tagged('post_install', '-at_install', 'mf_product')
class TestProductSpec(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'MF Test',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        uom_kg = self.env.ref('uom.product_uom_kgm')
        self.product = self.env['product.product'].create({
            'name': 'Test Widget',
            'default_code': 'WIDGET001',
            'weight': 1.5,
            'volume': 0.002,
            'standard_price': 25.00,
            'type': 'product',
            'uom_id': uom_kg.id,
        })

    def _build(self):
        from odoo.addons.stock_3pl_mainfreight.document.product_spec import ProductSpecDocument
        doc = ProductSpecDocument(self.connector, self.env)
        return doc.build_outbound(self.product)

    def test_csv_has_header_row(self):
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        self.assertIn('Product Code', reader.fieldnames)
        self.assertIn('Product Description 1', reader.fieldnames)
        self.assertIn('Unit Weight', reader.fieldnames)

    def test_product_code_maps_to_default_code(self):
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        self.assertEqual(rows[0]['Product Code'], 'WIDGET001')

    def test_weight_and_volume_formatted(self):
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        row = list(reader)[0]
        self.assertEqual(float(row['Unit Weight']), 1.5)
        self.assertEqual(float(row['Unit Volume']), 0.002)

    def test_product_code_truncated_to_40_chars(self):
        self.product.default_code = 'A' * 50
        csv_str = self._build()
        reader = csv.DictReader(io.StringIO(csv_str))
        row = list(reader)[0]
        self.assertEqual(len(row['Product Code']), 40)

    def test_missing_default_code_raises(self):
        self.product.default_code = False
        from odoo.exceptions import ValidationError
        from odoo.addons.stock_3pl_mainfreight.document.product_spec import ProductSpecDocument
        doc = ProductSpecDocument(self.connector, self.env)
        with self.assertRaises(ValidationError):
            doc.build_outbound(self.product)
```

**Step 2: Run — verify fail**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestProductSpec
```

**Step 3: Implement `document/product_spec.py`**

```python
# addons/stock_3pl_mainfreight/document/product_spec.py
import csv, io
from odoo.exceptions import ValidationError
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

HEADERS = [
    'Product Code', 'Product Description 1', 'Product Description 2',
    'Unit Weight', 'Unit Volume', 'Unit Price',
    'Grade1', 'Grade2', 'Grade3', 'Expiry Date', 'Packing Date',
    'Carton Per Layer', 'Layer Per Pallet',
    'Default Pack Size', 'Default Pack Description', 'Default Barcode',
    'Default Length', 'Default Width', 'Default Height',
    'Pack Size 2', 'Pack Description 2', 'Pack Barcode 2',
    'Pack Size 3', 'Pack Description 3', 'Pack Barcode 3',
    'Pack Size 4', 'Pack Description 4', 'Pack Barcode 4',
    'Warehouse ID',
]

MAX_PACK_TYPES = 4


class ProductSpecDocument(AbstractDocument):
    document_type = 'product_spec'
    format = 'csv'

    def build_outbound(self, product):
        """Build MF Product Specification CSV for a single product.product record."""
        if not product.default_code:
            raise ValidationError(
                f'Product "{product.name}" has no Internal Reference (default_code). '
                f'This is mandatory for Mainfreight product sync.'
            )

        row = self._build_row(product)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=HEADERS, extrasaction='ignore')
        writer.writeheader()
        writer.writerow(row)
        return output.getvalue()

    def build_outbound_batch(self, products):
        """Build MF Product Specification CSV for multiple products."""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=HEADERS, extrasaction='ignore')
        writer.writeheader()
        for product in products:
            if not product.default_code:
                continue  # Skip silently in batch; log warning
            writer.writerow(self._build_row(product))
        return output.getvalue()

    def _build_row(self, product):
        row = {
            'Product Code': self.truncate(product.default_code, 40),
            'Product Description 1': self.truncate(product.name, 40),
            'Product Description 2': self.truncate(product.description_sale or '', 40),
            'Unit Weight': round(product.weight or 0, 4),
            'Unit Volume': round(product.volume or 0, 4),
            'Unit Price': round(product.standard_price or 0, 2),
            'Grade1': 'N',
            'Grade2': 'N',
            'Grade3': 'N',
            'Expiry Date': 'N',
            'Packing Date': 'N',
            'Warehouse ID': self.connector.warehouse_code or '',
        }

        # Grade attributes from lot tracking config
        if product.tracking == 'lot':
            row['Grade1'] = 'Y'

        # Packaging (up to 4 pack types from product.packaging)
        packagings = product.packaging_ids[:MAX_PACK_TYPES]
        for i, pkg in enumerate(packagings, start=1):
            suffix = '' if i == 1 else f' {i}'
            prefix = 'Default' if i == 1 else f'Pack'
            row[f'{prefix} Pack Size{suffix}' if i > 1 else 'Default Pack Size'] = int(pkg.qty or 1)
            row[f'{prefix} Pack Description{suffix}' if i > 1 else 'Default Pack Description'] = \
                self.truncate(pkg.name, 20)
            row[f'{prefix} Barcode{suffix}' if i > 1 else 'Default Barcode'] = \
                self.truncate(pkg.barcode or '', 40)
            if hasattr(pkg, 'length'):
                row[f'Default Length' if i == 1 else f'Length {i}'] = round(pkg.length or 0, 4)
                row[f'Default Width' if i == 1 else f'Width {i}'] = round(pkg.width or 0, 4)
                row[f'Default Height' if i == 1 else f'Height {i}'] = round(pkg.height or 0, 4)

        return row

    def get_filename(self, record):
        return f'product_spec_{record.default_code}.csv'
```

**Step 4: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestProductSpec
```
Expected: 5 tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/
git commit -m "feat(mf): add ProductSpecDocument CSV builder with packaging and grade support"
```

---

### Task 12: Sales Order document builder (XML)

**Files:**
- Create: `addons/stock_3pl_mainfreight/document/sales_order.py`
- Create: `addons/stock_3pl_mainfreight/tests/test_sales_order.py`

**Step 1: Write failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_sales_order.py
from odoo.tests import TransactionCase, tagged
from lxml import etree

@tagged('post_install', '-at_install', 'mf_so')
class TestSalesOrderDocument(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'MF Test',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.partner = self.env['res.partner'].create({
            'name': 'Test Customer',
            'ref': 'CUST001',
            'street': '10 Demo Street',
            'city': 'Auckland',
            'zip': '1010',
            'country_id': self.env.ref('base.nz').id,
        })
        product = self.env['product.product'].create({
            'name': 'Widget',
            'default_code': 'WIDG001',
            'type': 'product',
        })
        self.order = self.env['sale.order'].create({
            'name': 'SO001',
            'partner_id': self.partner.id,
            'warehouse_id': warehouse.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 10,
                'price_unit': 15.00,
            })],
        })

    def _build(self):
        from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
        doc = SalesOrderDocument(self.connector, self.env)
        return doc.build_outbound(self.order)

    def test_xml_root_is_order(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.tag, 'Order')

    def test_client_order_number_maps_to_so_name(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('ClientOrderNumber'), 'SO001')

    def test_consignee_code_maps_to_partner_ref(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('ConsigneeCode'), 'CUST001')

    def test_warehouse_code_from_connector(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('WarehouseCode'), '99')

    def test_order_lines_present(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        lines = root.findall('Lines/Line')
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].findtext('ProductCode'), 'WIDG001')
        self.assertEqual(lines[0].findtext('Units'), '10')

    def test_idempotency_key_generated(self):
        from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
        doc = SalesOrderDocument(self.connector, self.env)
        key = doc.get_idempotency_key(self.order)
        self.assertIsNotNone(key)
        self.assertEqual(len(key), 64)  # SHA-256 hex
```

**Step 2: Run — verify fail**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestSalesOrderDocument
```

**Step 3: Implement `document/sales_order.py`**

```python
# addons/stock_3pl_mainfreight/document/sales_order.py
from lxml import etree
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument


class SalesOrderDocument(AbstractDocument):
    document_type = 'sales_order'
    format = 'xml'

    def build_outbound(self, order):
        root = etree.Element('Order')
        partner = order.partner_shipping_id or order.partner_id
        invoice_partner = order.partner_invoice_id or order.partner_id

        self._add(root, 'ClientOrderNumber', order.name, max_len=50)
        self._add(root, 'ClientReference', order.client_order_ref or '', max_len=50)
        self._add(root, 'ConsigneeCode', partner.ref or '', max_len=18)
        self._add(root, 'DeliveryName', partner.name, max_len=50)
        self._add(root, 'DeliveryAddress1', partner.street or '', max_len=50)
        self._add(root, 'DeliveryAddress2', partner.street2 or '', max_len=50)
        self._add(root, 'DeliverySuburb', '', max_len=50)  # NZ suburb
        self._add(root, 'DeliveryPostCode', partner.zip or '', max_len=50)
        self._add(root, 'DeliveryCity', partner.city or '', max_len=50)
        self._add(root, 'DeliveryState', partner.state_id.name if partner.state_id else '', max_len=50)
        self._add(root, 'DeliveryCountry', partner.country_id.name if partner.country_id else '', max_len=50)
        self._add(root, 'DeliveryInstructions', order.note or '', max_len=500)
        self._add(root, 'InvoiceName', invoice_partner.name, max_len=60)
        self._add(root, 'InvoiceAddress1', invoice_partner.street or '', max_len=50)
        self._add(root, 'InvoiceCity', invoice_partner.city or '', max_len=50)
        self._add(root, 'InvoicePostCode', invoice_partner.zip or '', max_len=50)
        self._add(root, 'InvoiceCountry', invoice_partner.country_id.name if invoice_partner.country_id else '', max_len=50)
        self._add(root, 'WarehouseCode', self.connector.warehouse_code or '', max_len=3)
        self._add(root, 'CustomerID', self.connector.customer_id or '', max_len=50)
        if order.commitment_date:
            self._add(root, 'DateRequired', order.commitment_date.strftime('%d/%m/%Y'))

        # Lines
        lines_el = etree.SubElement(root, 'Lines')
        for i, line in enumerate(order.order_line, start=1):
            line_el = etree.SubElement(lines_el, 'Line')
            self._add(line_el, 'LineNumber', str(i))
            self._add(line_el, 'ProductCode',
                      self.truncate(line.product_id.default_code or '', 40))
            self._add(line_el, 'Units', str(int(line.product_uom_qty)))
            self._add(line_el, 'UnitPrice', str(round(line.price_unit, 2)))

        return etree.tostring(root, pretty_print=True, xml_declaration=True,
                              encoding='UTF-8').decode('utf-8')

    def _add(self, parent, tag, value, max_len=None):
        el = etree.SubElement(parent, tag)
        el.text = self.truncate(value, max_len) if max_len else str(value)

    def get_filename(self, order):
        return f'{order.name}.xml'

    def get_idempotency_key(self, order):
        return self.make_idempotency_key(
            self.connector.id, self.document_type, order.name
        )
```

**Step 4: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestSalesOrderDocument
```
Expected: 6 tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/
git commit -m "feat(mf): add SalesOrderDocument XML builder with full SOH/SOL mapping"
```

---

### Task 13: SO Confirmation handler (MF → Odoo)

**Files:**
- Create: `addons/stock_3pl_mainfreight/document/so_confirmation.py`
- Create: `addons/stock_3pl_mainfreight/tests/test_so_confirmation.py`
- Create: `addons/stock_3pl_mainfreight/tests/fixtures/so_confirmation.xml`

**Step 1: Create fixture**

```xml
<!-- addons/stock_3pl_mainfreight/tests/fixtures/so_confirmation.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<OrderConfirmation>
  <SCH>
    <Reference>SO001</Reference>
    <ConsignmentNo>OTR000000134</ConsignmentNo>
    <CarrierName>MAINFREIGHT</CarrierName>
    <FinalisedDate>29/09/2024</FinalisedDate>
    <ETADate>02/10/2024</ETADate>
    <Lines>
      <SCL>
        <ProductCode>WIDG001</ProductCode>
        <UnitsFulfilled>10</UnitsFulfilled>
        <LotNumber>LOT001</LotNumber>
      </SCL>
    </Lines>
  </SCH>
</OrderConfirmation>
```

**Step 2: Write failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_so_confirmation.py
import os
from odoo.tests import TransactionCase, tagged

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')

@tagged('post_install', '-at_install', 'mf_so_confirm')
class TestSOConfirmation(TransactionCase):

    def _load_fixture(self, name):
        with open(os.path.join(FIXTURE_DIR, name)) as f:
            return f.read()

    def test_parse_confirmation_extracts_reference(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import SOConfirmationDocument
        doc = SOConfirmationDocument(None, self.env)
        parsed = doc.parse_inbound(self._load_fixture('so_confirmation.xml'))
        self.assertEqual(parsed['reference'], 'SO001')
        self.assertEqual(parsed['consignment_no'], 'OTR000000134')
        self.assertEqual(parsed['carrier_name'], 'MAINFREIGHT')

    def test_parse_confirmation_extracts_lines(self):
        from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import SOConfirmationDocument
        doc = SOConfirmationDocument(None, self.env)
        parsed = doc.parse_inbound(self._load_fixture('so_confirmation.xml'))
        self.assertEqual(len(parsed['lines']), 1)
        self.assertEqual(parsed['lines'][0]['product_code'], 'WIDG001')
        self.assertEqual(parsed['lines'][0]['qty_done'], 10)
```

**Step 3: Implement `document/so_confirmation.py`**

```python
# addons/stock_3pl_mainfreight/document/so_confirmation.py
from lxml import etree
from datetime import datetime
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument


class SOConfirmationDocument(AbstractDocument):
    document_type = 'so_confirmation'
    format = 'xml'

    def parse_inbound(self, payload):
        root = etree.fromstring(payload.encode('utf-8'))
        sch = root.find('SCH') or root
        lines = []
        for scl in sch.findall('Lines/SCL'):
            lines.append({
                'product_code': scl.findtext('ProductCode', '').strip(),
                'qty_done': float(scl.findtext('UnitsFulfilled', '0') or 0),
                'lot_number': scl.findtext('LotNumber', '').strip(),
            })
        return {
            'reference': sch.findtext('Reference', '').strip(),
            'consignment_no': sch.findtext('ConsignmentNo', '').strip(),
            'carrier_name': sch.findtext('CarrierName', '').strip(),
            'finalised_date': self._parse_date(sch.findtext('FinalisedDate', '')),
            'eta_date': self._parse_date(sch.findtext('ETADate', '')),
            'lines': lines,
        }

    def apply_inbound(self, message):
        """Apply SO confirmation to stock.picking in Odoo."""
        parsed = self.parse_inbound(message.payload_xml)
        order = self.env['sale.order'].search(
            [('name', '=', parsed['reference'])], limit=1
        )
        if not order:
            raise ValueError(f"Sale order not found: {parsed['reference']}")

        picking = order.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel')
        )[:1]
        if not picking:
            return

        picking.write({
            'carrier_tracking_ref': parsed['consignment_no'],
            'date_done': parsed['finalised_date'],
            'scheduled_date': parsed['eta_date'],
        })

        # Match carrier by name
        if parsed['carrier_name']:
            carrier = self.env['delivery.carrier'].search(
                [('name', 'ilike', parsed['carrier_name'])], limit=1
            )
            if carrier:
                picking.carrier_id = carrier

        # Reconcile move lines
        for line_data in parsed['lines']:
            product = self.env['product.product'].search(
                [('default_code', '=', line_data['product_code'])], limit=1
            )
            if product:
                move = picking.move_lines.filtered(
                    lambda m: m.product_id == product
                )[:1]
                if move and move.move_line_ids:
                    move.move_line_ids[0].qty_done = line_data['qty_done']

    @staticmethod
    def _parse_date(date_str):
        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, AttributeError):
                continue
        return None
```

**Step 4: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestSOConfirmation
```

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/
git commit -m "feat(mf): add SOConfirmationDocument XML parser and stock.picking updater"
```

---

### Task 14: Inventory Report handler (MF SOH CSV → stock.quant)

**Files:**
- Create: `addons/stock_3pl_mainfreight/document/inventory_report.py`
- Create: `addons/stock_3pl_mainfreight/tests/test_inventory_report.py`
- Create: `addons/stock_3pl_mainfreight/tests/fixtures/inventory_report.csv`

**Step 1: Create fixture CSV**

```csv
LineNumber,CustomerID,CustomerName,WarehouseID,Product,ProductDescription,ProductDescription2,Grade1,Grade2,Grade3,ExpiryDate,PackingDate,ProductType,DescriptionGroup,StockOnHand,QuantityHeldByPick,QuantityOnHold,QuantityRestricted,QuantityCommitted,QuantityDamaged,QuantityAvailable,ArrivalDate
1,123456,TEST CO,99,WIDG001,Widget,,,,,,,,GENERAL,100,0,0,0,5,0,95,20/08/2024
2,123456,TEST CO,99,WIDG002,Widget 2,,,,,,,,GENERAL,50,0,10,0,0,0,40,20/08/2024
```

**Step 2: Write failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_inventory_report.py
import os
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
            'forwarder': 'mainfreight',
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
        with open(os.path.join(FIXTURE_DIR, 'inventory_report.csv')) as f:
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
```

**Step 3: Implement `document/inventory_report.py`**

```python
# addons/stock_3pl_mainfreight/document/inventory_report.py
import csv, io
from datetime import datetime
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument
import logging

_logger = logging.getLogger(__name__)


class InventoryReportDocument(AbstractDocument):
    document_type = 'inventory_report'
    format = 'csv'

    def parse_inbound(self, payload):
        reader = csv.DictReader(io.StringIO(payload))
        lines = []
        for row in reader:
            lines.append({
                'product_code': row.get('Product', '').strip(),
                'warehouse_id': row.get('WarehouseID', '').strip(),
                'stock_on_hand': int(float(row.get('StockOnHand', 0) or 0)),
                'qty_on_hold': int(float(row.get('QuantityOnHold', 0) or 0)),
                'qty_damaged': int(float(row.get('QuantityDamaged', 0) or 0)),
                'qty_available': int(float(row.get('QuantityAvailable', 0) or 0)),
                'grade1': row.get('Grade1', '').strip(),
                'grade2': row.get('Grade2', '').strip(),
                'expiry_date': self._parse_date(row.get('ExpiryDate', '')),
                'packing_date': self._parse_date(row.get('PackingDate', '')),
            })
        return lines

    def apply_csv(self, payload, report_date=None):
        """Parse and apply a full SOH report to stock.quant."""
        lines = self.parse_inbound(payload)
        stock_location = self.connector.warehouse_id.lot_stock_id

        for line in lines:
            product = self.env['product.product'].search(
                [('default_code', '=', line['product_code'])], limit=1
            )
            if not product:
                _logger.warning('MF SOH: product not found: %s', line['product_code'])
                continue

            self._sync_quant(product, stock_location, line['stock_on_hand'])

        if report_date:
            self.connector.last_soh_applied_at = datetime.now()

    def _sync_quant(self, product, location, quantity):
        quant = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', '=', location.id),
        ], limit=1)
        if quant:
            quant.sudo().write({'quantity': quantity})
        else:
            self.env['stock.quant'].sudo().create({
                'product_id': product.id,
                'location_id': location.id,
                'quantity': quantity,
            })

    @staticmethod
    def _parse_date(date_str):
        for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except (ValueError, AttributeError):
                continue
        return None
```

**Step 4: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestInventoryReport
```

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/
git commit -m "feat(mf): add InventoryReportDocument CSV parser and stock.quant sync"
```

---

### Task 15: Inward Order document builder [INACTIVE]

**Files:**
- Create: `addons/stock_3pl_mainfreight/document/inward_order.py`
- Create: `addons/stock_3pl_mainfreight/tests/test_inward_order.py`

**Step 1: Write failing tests**

```python
# addons/stock_3pl_mainfreight/tests/test_inward_order.py
from odoo.tests import TransactionCase, tagged
from lxml import etree

@tagged('post_install', '-at_install', 'mf_inward')
class TestInwardOrderDocument(TransactionCase):

    def setUp(self):
        super().setUp()
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.connector = self.env['3pl.connector'].create({
            'name': 'MF Test',
            'warehouse_id': warehouse.id,
            'forwarder': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'customer_id': '123456',
            'warehouse_code': '99',
        })
        self.supplier = self.env['res.partner'].create({'name': 'Test Supplier'})
        product = self.env['product.product'].create({
            'name': 'Widget',
            'default_code': 'WIDG001',
            'type': 'product',
        })
        self.po = self.env['purchase.order'].create({
            'name': 'PO001',
            'partner_id': self.supplier.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_qty': 50,
                'price_unit': 10.00,
                'date_planned': '2024-10-01',
            })],
        })

    def _build(self):
        from odoo.addons.stock_3pl_mainfreight.document.inward_order import InwardOrderDocument
        doc = InwardOrderDocument(self.connector, self.env)
        return doc.build_outbound(self.po)

    def test_xml_root_is_inward(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.tag, 'Inward')

    def test_inwards_reference_maps_to_po_name(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('InwardsReference'), 'PO001')

    def test_inward_lines_present(self):
        xml = self._build()
        root = etree.fromstring(xml.encode())
        lines = root.findall('Lines/Line')
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].findtext('ProductCode'), 'WIDG001')
```

**Step 2: Implement `document/inward_order.py`**

```python
# addons/stock_3pl_mainfreight/document/inward_order.py
"""
Inward Order document builder for Mainfreight.

STATUS: BUILT — INACTIVE
Enable by wiring up purchase.order confirm trigger in connector_mf.py.
Designed via FreightForwarderMixin to support additional 3PL providers.
"""
from lxml import etree
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument, FreightForwarderMixin


class InwardOrderDocument(AbstractDocument, FreightForwarderMixin):
    document_type = 'inward_order'
    format = 'xml'

    FIELD_MAP = {
        'mainfreight': {
            'order_ref': 'InwardsReference',
            'booking_date': 'BookingDate',
            'supplier_name': 'SupplierName',
            'warehouse_code': 'WarehouseCode',
            'customer_id': 'CustomerID',
        }
    }

    def build_outbound(self, po):
        field_map = self.get_field_map(self.connector.forwarder)
        root = etree.Element('Inward')

        self._add(root, field_map.get('order_ref', 'InwardsReference'), po.name, max_len=40)
        self._add(root, field_map.get('warehouse_code', 'WarehouseCode'),
                  self.connector.warehouse_code or '', max_len=3)
        self._add(root, field_map.get('customer_id', 'CustomerID'),
                  self.connector.customer_id or '', max_len=50)
        self._add(root, field_map.get('supplier_name', 'SupplierName'),
                  po.partner_id.name or '', max_len=50)

        if po.date_planned:
            self._add(root, field_map.get('booking_date', 'BookingDate'),
                      po.date_planned.strftime('%d/%m/%Y'))

        lines_el = etree.SubElement(root, 'Lines')
        for i, line in enumerate(po.order_line, start=1):
            line_el = etree.SubElement(lines_el, 'Line')
            self._add(line_el, 'LineNumber', str(i))
            self._add(line_el, 'ProductCode',
                      self.truncate(line.product_id.default_code or '', 40))
            self._add(line_el, 'Quantity', str(int(line.product_qty)))

        return etree.tostring(root, pretty_print=True, xml_declaration=True,
                              encoding='UTF-8').decode('utf-8')

    def _add(self, parent, tag, value, max_len=None):
        el = etree.SubElement(parent, tag)
        el.text = self.truncate(value, max_len) if max_len else str(value)

    def get_filename(self, po):
        return f'{po.name}.xml'
```

**Step 3: Run — verify pass**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_mainfreight:TestInwardOrderDocument
```

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/
git commit -m "feat(mf): add InwardOrderDocument XML builder [built, inactive, forwarder-extensible]"
```

---

### Task 16: Odoo event triggers — SO confirm and product sync

**Files:**
- Create: `addons/stock_3pl_mainfreight/models/sale_order_hook.py`
- Create: `addons/stock_3pl_mainfreight/models/product_hook.py`
- Modify: `addons/stock_3pl_mainfreight/models/__init__.py`

**Step 1: Implement `models/sale_order_hook.py`**

```python
# addons/stock_3pl_mainfreight/models/sale_order_hook.py
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class SaleOrderMF(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        result = super().action_confirm()
        for order in self:
            self._queue_mf_sales_order(order)
        return result

    def _queue_mf_sales_order(self, order):
        """Find the active MF connector for this order's warehouse and queue."""
        connector = self.env['3pl.connector'].search([
            ('warehouse_id', '=', order.warehouse_id.id),
            ('forwarder', '=', 'mainfreight'),
            ('active', '=', True),
        ], limit=1)
        if not connector:
            return

        from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
        doc = SalesOrderDocument(connector, self.env)
        idempotency_key = doc.get_idempotency_key(order)

        # Block if already queued for this order
        existing = self.env['3pl.message'].search([
            ('connector_id', '=', connector.id),
            ('document_type', '=', 'sales_order'),
            ('idempotency_key', '=', idempotency_key),
            ('state', 'not in', ('dead',)),
        ], limit=1)
        if existing:
            _logger.info('MF: SO %s already queued (msg %s), skipping.', order.name, existing.id)
            return

        payload = doc.build_outbound(order)
        self.env['3pl.message'].create({
            'connector_id': connector.id,
            'direction': 'outbound',
            'document_type': 'sales_order',
            'action': 'create',
            'payload_xml': payload,
            'ref_model': 'sale.order',
            'ref_id': order.id,
            'idempotency_key': idempotency_key,
            'state': 'queued',
        })
        _logger.info('MF: Queued sales order %s for connector %s', order.name, connector.name)
```

**Step 2: Implement `models/product_hook.py`**

```python
# addons/stock_3pl_mainfreight/models/product_hook.py
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)

SYNC_FIELDS = {'default_code', 'name', 'weight', 'volume', 'standard_price',
               'description_sale', 'tracking', 'packaging_ids'}


class ProductProductMF(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        result = super().write(vals)
        if SYNC_FIELDS.intersection(vals.keys()):
            for product in self:
                self._queue_mf_product_sync(product)
        return result

    def _queue_mf_product_sync(self, product):
        connectors = self.env['3pl.connector'].search([
            ('forwarder', '=', 'mainfreight'),
            ('active', '=', True),
        ])
        for connector in connectors:
            from odoo.addons.stock_3pl_mainfreight.document.product_spec import ProductSpecDocument
            doc = ProductSpecDocument(connector, self.env)
            if not product.default_code:
                continue
            idempotency_key = doc.make_idempotency_key(
                connector.id, 'product_spec', product.default_code
            )
            # Use update action if message already sent
            existing = self.env['3pl.message'].search([
                ('connector_id', '=', connector.id),
                ('document_type', '=', 'product_spec'),
                ('ref_id', '=', product.id),
                ('state', 'not in', ('dead',)),
            ], limit=1)
            if existing:
                continue
            payload = doc.build_outbound(product)
            self.env['3pl.message'].create({
                'connector_id': connector.id,
                'direction': 'outbound',
                'document_type': 'product_spec',
                'action': 'create',
                'payload_csv': payload,
                'ref_model': 'product.product',
                'ref_id': product.id,
                'idempotency_key': idempotency_key,
                'state': 'queued',
            })
```

**Step 3: Update `models/__init__.py`**

```python
from . import connector_mf, sale_order_hook, product_hook
```

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/
git commit -m "feat(mf): add SO confirm and product write triggers to queue MF messages"
```

---

### Task 17: MF REST transport and inbound poll wiring

**Files:**
- Create: `addons/stock_3pl_mainfreight/transport/mainfreight_rest.py`
- Modify: `addons/stock_3pl_core/models/message.py` (add `_poll_inbound`)

**Step 1: Implement MF REST transport**

```python
# addons/stock_3pl_mainfreight/transport/mainfreight_rest.py
import requests
import logging
from odoo.addons.stock_3pl_core.transport.rest_api import RestTransport

_logger = logging.getLogger(__name__)

MF_ENDPOINTS = {
    'test': 'https://warehouseapi-test.mainfreight.com/api/v1.1',
    'production': 'https://warehouseapi.mainfreight.com/api/v1.1',
}


class MainfreightRestTransport(RestTransport):
    """MF-specific REST transport — handles MF auth and endpoint routing."""

    def _get_base_url(self):
        return MF_ENDPOINTS.get(self.connector.environment, MF_ENDPOINTS['test'])

    def send_order(self, payload):
        return self.send(payload, content_type='xml',
                         endpoint=f'{self._get_base_url()}/Order')

    def send_inward(self, payload):
        return self.send(payload, content_type='xml',
                         endpoint=f'{self._get_base_url()}/Inward')

    def get_stock_on_hand(self):
        url = f'{self._get_base_url()}/StockOnHand'
        headers = {'Authorization': f'Bearer {self.connector.api_secret}'}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            _logger.warning('MF SOH poll failed: %s', e)
        return None
```

**Step 2: Add `_poll_inbound` to `3pl.message`**

```python
# Add to ThreePlMessage in message.py:

@api.model
def _poll_inbound(self):
    """Called by cron. Poll all active connectors for inbound messages."""
    connectors = self.env['3pl.connector'].search([('active', '=', True)])
    for connector in connectors:
        try:
            transport = connector.get_transport()
            payloads = transport.poll()
            for raw in payloads:
                source_hash = hashlib.sha256(raw.encode()).hexdigest()
                existing = self.search([
                    ('connector_id', '=', connector.id),
                    ('source_hash', '=', source_hash),
                ], limit=1)
                if existing:
                    continue  # Deduplicate
                self.create({
                    'connector_id': connector.id,
                    'direction': 'inbound',
                    'document_type': self._detect_inbound_type(raw),
                    'payload_xml': raw if raw.strip().startswith('<') else False,
                    'payload_csv': raw if not raw.strip().startswith('<') else False,
                    'source_hash': source_hash,
                    'state': 'received',
                })
        except Exception as e:
            _logger.error('Inbound poll failed for %s: %s', connector.name, e)

@staticmethod
def _detect_inbound_type(raw):
    raw = raw.strip()
    if '<OrderConfirmation' in raw or '<SCH' in raw:
        return 'so_confirmation'
    if '<InwardConfirmation' in raw:
        return 'inward_confirmation'
    return 'inventory_report'  # Default CSV
```

**Step 3: Commit**

```bash
git add addons/stock_3pl_mainfreight/ addons/stock_3pl_core/
git commit -m "feat(mf): add MF REST transport and inbound poll cron wiring"
```

---

### Task 18: Run full test suite and fix any failures

**Step 1: Run all tests**

```bash
python odoo-bin -u stock_3pl_core,stock_3pl_mainfreight \
  --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core,/stock_3pl_mainfreight
```

**Step 2: Fix any failures found**

For each failure:
1. Read the error
2. Fix the minimal code
3. Re-run that specific test tag
4. Do NOT fix other unrelated things

**Step 3: Final commit**

```bash
git add addons/
git commit -m "fix: resolve full test suite failures post-integration"
```

---

### Task 19: Update CLAUDE.md with final module structure

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add development commands**

Add to CLAUDE.md under a `## Development Commands` section:

```markdown
## Development Commands

### Install modules
python odoo-bin -i stock_3pl_core,stock_3pl_mainfreight -d testdb --stop-after-init

### Run all tests
python odoo-bin -u stock_3pl_core,stock_3pl_mainfreight --test-enable --stop-after-init -d testdb

### Run single module tests
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core

### Run single test class
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb \
  --test-tags=/stock_3pl_core:TestMessage

### Install paramiko (required for SFTP transport)
pip install paramiko
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with module structure and development commands"
```

---

## Open Items (from design doc)

- [ ] Confirm MF CustomerID and WarehouseID for NZ/AU/US with Mainfreight IT
- [ ] Confirm transport method (REST API vs SFTP) — update `connector_mf_demo.xml`
- [ ] Obtain MF sandbox credentials for development testing
- [ ] Check if `delivery_mainfreight` credentials can be reused for warehousing API
- [ ] Upgrade diff: review Odoo 15 → target version API changes when upgrade starts
- [ ] Wire up Inward Order trigger once scope activated
