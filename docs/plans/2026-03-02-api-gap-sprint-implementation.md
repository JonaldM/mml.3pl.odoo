# API Gap Sprint Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close all pre-deployment discrepancies between the codebase and the Mainfreight public API docs — correct endpoint URLs, add required `region` parameter, add Order/Inward CRUD methods, harden parsers to handle both the PDF-spec and public API schemas, and add a dormant webhook stub.

**Architecture:** All changes land in `stock_3pl_mainfreight`; `stock_3pl_core` is untouched except Task 4 (new PUT/DELETE methods on `RestTransport`). Every gap fix is backward-compatible — existing parsers/builders gain a second path, not a replacement. Cron polling stays; the webhook controller is dormant until cloud hosting.

**Tech Stack:** Python 3, lxml, requests, Odoo 19 (ORM models + HTTP controller), pytest (pure-Python), odoo-bin --test-enable (Odoo integration tests)

---

## Running tests

**Pure-Python (no Odoo needed):**
```bash
python -m pytest -m "not odoo_integration" -q
```

**Odoo integration (requires live db):**
```bash
python odoo-bin -u stock_3pl_core,stock_3pl_mainfreight --test-enable --stop-after-init -d testdb
```

---

## Task 1: Fix endpoint URL constants and add `_region()` helper

**Files:**
- Modify: `addons/stock_3pl_mainfreight/transport/mainfreight_rest.py`
- Modify: `addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py`

**Context:** `MF_ENDPOINTS` currently points to `warehouseapi[-test].mainfreight.com/api/v1.1`. The public API uses `api[-test].mainfreight.com/warehousing/1.1/Customers`. All warehousing methods must append `?region={region}`. The `_FakeConnector` in the test file needs a `mf_region` attribute.

**Step 1: Update existing URL constant tests to expect new format (they will now be the failing spec)**

In `test_mainfreight_rest.py`, update `TestMFEndpointsConstant`:

```python
class TestMFEndpointsConstant(unittest.TestCase):

    def test_endpoints_has_test_key(self):
        self.assertIn('test', MF_ENDPOINTS)

    def test_endpoints_has_production_key(self):
        self.assertIn('production', MF_ENDPOINTS)

    def test_test_url_points_to_test_host(self):
        """Test URL must use the public API host, not the old warehouse-specific subdomain."""
        self.assertIn('api-test.mainfreight.com', MF_ENDPOINTS['test'])

    def test_production_url_points_to_prod_host(self):
        self.assertIn('api.mainfreight.com', MF_ENDPOINTS['production'])

    def test_urls_include_warehousing_path(self):
        self.assertIn('/warehousing/1.1/Customers', MF_ENDPOINTS['test'])
        self.assertIn('/warehousing/1.1/Customers', MF_ENDPOINTS['production'])
```

Also update `_FakeConnector` to add `mf_region`:

```python
class _FakeConnector:
    def __init__(self, environment, api_secret='secret', mf_region='ANZ'):
        self.environment = environment
        self.api_secret = api_secret
        self.mf_region = mf_region

    def get_credential(self, field_name):
        return self.api_secret
```

Add new region tests (these also fail initially):

```python
class TestRegionParam(unittest.TestCase):

    def test_region_helper_returns_connector_mf_region(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='EU'))
        self.assertEqual(transport._region(), 'EU')

    def test_region_helper_defaults_to_anz_when_empty(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region=''))
        self.assertEqual(transport._region(), 'ANZ')

    def test_region_helper_defaults_to_anz_when_none(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region=None))
        self.assertEqual(transport._region(), 'ANZ')

    def test_send_order_url_includes_region_param(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_order('<Order/>')
        call_kwargs = mock_send.call_args
        endpoint = call_kwargs[1]['endpoint'] if call_kwargs[1] else call_kwargs[0][2]
        self.assertIn('?region=ANZ', endpoint)

    def test_send_inward_url_includes_region_param(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_inward('<Inward/>')
        call_kwargs = mock_send.call_args
        endpoint = call_kwargs[1]['endpoint'] if call_kwargs[1] else call_kwargs[0][2]
        self.assertIn('?region=ANZ', endpoint)

    def test_get_stock_on_hand_url_includes_region_param(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'poll', return_value=[]) as mock_poll:
            transport.get_stock_on_hand()
        path_arg = mock_poll.call_args[1]['path']
        self.assertIn('?region=ANZ', path_arg)

    def test_eu_region_used_in_url(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='EU'))
        with patch.object(transport, 'send', return_value={'success': True}) as mock_send:
            transport.send_order('<Order/>')
        endpoint = mock_send.call_args[1]['endpoint']
        self.assertIn('?region=EU', endpoint)
```

**Step 2: Run tests to confirm failures**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py -v -m "not odoo_integration"
```

Expected: `TestMFEndpointsConstant` URL tests FAIL, `TestRegionParam` tests FAIL (method/attr missing).

**Step 3: Update `mainfreight_rest.py`**

Replace `MF_ENDPOINTS` and update all warehousing methods:

```python
MF_ENDPOINTS = {
    'test': 'https://api-test.mainfreight.com/warehousing/1.1/Customers',
    'production': 'https://api.mainfreight.com/warehousing/1.1/Customers',
}

# NOTE: test base URL (api-test.mainfreight.com) is inferred from public docs.
# Confirm with MF before first live test. Contact: APISupport@mainfreight.co.nz
# See open question #1 in docs/plans/2026-03-02-api-gap-sprint-design.md
```

Add `_region()` method to `MainfreightRestTransport`:

```python
def _region(self):
    """Return the MF region code for the ?region= query parameter.

    Reads mf_region from the connector (added in Task 3).
    Defaults to 'ANZ' (New Zealand / Australia) if empty or not set.
    Valid values per MF docs: ANZ, EU, AMERICAS — confirm with MF if unsure.
    """
    return getattr(self.connector, 'mf_region', None) or 'ANZ'
```

Update `send_order`, `send_inward`, `get_stock_on_hand`:

```python
def send_order(self, payload):
    return self.send(payload, content_type='xml',
                     endpoint=f'{self._get_base_url()}/Order?region={self._region()}')

def send_inward(self, payload):
    return self.send(payload, content_type='xml',
                     endpoint=f'{self._get_base_url()}/Inward?region={self._region()}')

def get_stock_on_hand(self):
    return self.poll(path=f'{self._get_base_url()}/StockOnHand?region={self._region()}')
```

Also update existing `TestSendOrderEndpoint` / `TestSendInwardEndpoint` / `TestGetStockOnHand` tests that assert the old URL format — replace old endpoint assertions with new format including `/Order?region=ANZ` etc.

**Step 4: Run tests to confirm all pass**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py -v -m "not odoo_integration"
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/transport/mainfreight_rest.py \
        addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py
git commit -m "fix(transport): update MF endpoint URLs to public API format + add region param"
```

---

## Task 2: Add `mf_region` field to `ThreePlConnectorMF`

**Files:**
- Modify: `addons/stock_3pl_mainfreight/models/connector_mf.py`
- Modify: `addons/stock_3pl_mainfreight/views/connector_mf_views.xml` (expose field in form)

**Context:** `_region()` already reads `connector.mf_region` (Task 1). This task adds the actual Odoo field so it persists and is configurable via the UI. This is an Odoo model change — no pure-Python test possible; verified manually and via the existing Odoo integration test suite.

**Step 1: Add field to `connector_mf.py`**

Inside `ThreePlConnectorMF`, after `mf_tracking_secret`:

```python
mf_region = fields.Char(
    'MF Region',
    default='ANZ',
    help='Warehousing API region parameter. Valid values: ANZ, EU, AMERICAS. '
         'Default ANZ covers New Zealand and Australia. '
         'Confirm exact value with Mainfreight before going live.',
)
```

**Step 2: Expose field in connector form view**

In `views/connector_mf_views.xml`, add inside the Mainfreight credentials group (after `mf_tracking_secret`):

```xml
<field name="mf_region" placeholder="ANZ"/>
```

**Step 3: Verify (Odoo integration — no pure-Python test)**

After installing in Odoo, open any 3PL connector record → confirm "MF Region" field appears with default "ANZ".

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/models/connector_mf.py \
        addons/stock_3pl_mainfreight/views/connector_mf_views.xml
git commit -m "feat(connector): add mf_region field — warehousing API region query parameter"
```

---

## Task 3: Add `send_put()` and `send_delete()` to `RestTransport`

**Files:**
- Modify: `addons/stock_3pl_core/transport/rest_api.py`
- Modify: `addons/stock_3pl_core/tests/test_transport_rest.py`

**Context:** `rest_api.py` only has `send()` (POST) and `poll()` (GET). Order Update needs PUT; Order/Inward Delete needs DELETE. The new methods follow the identical auth/timeout/error-return pattern as `send()`.

**Step 1: Write failing tests in `test_transport_rest.py`**

Add these test classes (they are Odoo integration tests — `TransactionCase` pattern already used in that file):

```python
@patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.put')
def test_send_put_success(self, mock_put):
    mock_put.return_value = MagicMock(status_code=200, text='OK')
    transport = self._make_transport()
    result = transport.send_put('<Order action="UPDATE"/>', content_type='xml',
                                endpoint='https://test.example.com/Order')
    self.assertTrue(result['success'])

@patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.put')
def test_send_put_422_returns_validation_error(self, mock_put):
    mock_put.return_value = MagicMock(status_code=422, text='Bad payload')
    transport = self._make_transport()
    result = transport.send_put('<Order/>', content_type='xml',
                                endpoint='https://test.example.com/Order')
    self.assertFalse(result['success'])
    self.assertEqual(result['error_type'], 'validation')

@patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.put')
def test_send_put_500_returns_retriable(self, mock_put):
    mock_put.return_value = MagicMock(status_code=500, text='Error')
    transport = self._make_transport()
    result = transport.send_put('<Order/>', content_type='xml',
                                endpoint='https://test.example.com/Order')
    self.assertFalse(result['success'])
    self.assertEqual(result['error_type'], 'retriable')

@patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.delete')
def test_send_delete_success(self, mock_delete):
    mock_delete.return_value = MagicMock(status_code=200, text='')
    transport = self._make_transport()
    result = transport.send_delete(endpoint='https://test.example.com/Order/SO001')
    self.assertTrue(result['success'])

@patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.delete')
def test_send_delete_404_returns_retriable(self, mock_delete):
    mock_delete.return_value = MagicMock(status_code=404, text='Not found')
    transport = self._make_transport()
    result = transport.send_delete(endpoint='https://test.example.com/Order/MISSING')
    self.assertFalse(result['success'])
    self.assertEqual(result['error_type'], 'retriable')

@patch('odoo.addons.stock_3pl_core.transport.rest_api.requests.delete')
def test_send_delete_rejects_non_https(self, mock_delete):
    transport = self._make_transport()
    with self.assertRaises(ValueError):
        transport.send_delete(endpoint='http://test.example.com/Order/SO001')
```

**Step 2: Run to confirm failures**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb 2>&1 | grep -E "FAIL|ERROR|send_put|send_delete"
```

Expected: `AttributeError: 'RestTransport' object has no attribute 'send_put'`.

**Step 3: Implement `send_put()` and `send_delete()` in `rest_api.py`**

Add after `send()`:

```python
def send_put(self, payload, content_type='xml', endpoint=None):
    """PUT request — used for Order/Inward Update.

    Same auth, timeout, and error-return contract as send().
    Returns a result dict: {'success': True} or {'success': False, 'error': ..., 'error_type': ...}
    """
    url = endpoint or self.connector.api_url
    self._validate_url(url)
    headers = {
        'Content-Type': CONTENT_TYPES.get(content_type, 'application/xml'),
        'Authorization': f'Bearer {self._get_auth_secret()}',
    }
    try:
        data = payload if isinstance(payload, bytes) else payload.encode('utf-8')
        resp = requests.put(url, data=data, headers=headers, timeout=30)
    except requests.Timeout:
        return self._retriable_error('Request timed out')
    except requests.ConnectionError as e:
        return self._retriable_error(f'Connection error: {e}')
    except requests.exceptions.RequestException as e:
        return self._retriable_error(f'Transport error: {str(e).split(chr(10))[0][:200]}')

    if resp.status_code in (200, 201):
        return self._success()
    elif resp.status_code == 422:
        error_body = resp.text[:500].replace('\n', ' ').replace('\r', '') if resp.text else ''
        return self._validation_error(error_body)
    else:
        error_body = resp.text[:500].replace('\n', ' ').replace('\r', '') if resp.text else ''
        return self._retriable_error(f'HTTP {resp.status_code}: {error_body}')

def send_delete(self, endpoint):
    """DELETE request — used for Order/Inward Delete.

    No request body. Resource reference is encoded in the URL path.
    Returns a result dict: {'success': True} or {'success': False, 'error': ..., 'error_type': ...}
    """
    self._validate_url(endpoint)
    headers = {'Authorization': f'Bearer {self._get_auth_secret()}'}
    try:
        resp = requests.delete(endpoint, headers=headers, timeout=30)
    except requests.Timeout:
        return self._retriable_error('Request timed out')
    except requests.ConnectionError as e:
        return self._retriable_error(f'Connection error: {e}')
    except requests.exceptions.RequestException as e:
        return self._retriable_error(f'Transport error: {str(e).split(chr(10))[0][:200]}')

    if resp.status_code in (200, 204):
        return self._success()
    else:
        error_body = resp.text[:500].replace('\n', ' ').replace('\r', '') if resp.text else ''
        return self._retriable_error(f'HTTP {resp.status_code}: {error_body}')
```

**Step 4: Run tests to confirm all pass**

```bash
python odoo-bin -u stock_3pl_core --test-enable --stop-after-init -d testdb 2>&1 | grep -E "ok|FAIL|ERROR" | tail -20
```

**Step 5: Commit**

```bash
git add addons/stock_3pl_core/transport/rest_api.py \
        addons/stock_3pl_core/tests/test_transport_rest.py
git commit -m "feat(transport): add send_put and send_delete to RestTransport base"
```

---

## Task 4: Add `update_order()`, `delete_order()`, `delete_inward()` to `MainfreightRestTransport`

**Files:**
- Modify: `addons/stock_3pl_mainfreight/transport/mainfreight_rest.py`
- Modify: `addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py`

**Context:** The `_FakeConnector`'s stubbed `RestTransport` (inside `_stub_odoo_for_transport()`) also needs `send_put` and `send_delete` stubs so the pure-Python tests can import correctly.

**Step 1: Update `_FakeConnector`'s inner `RestTransport` stub in `test_mainfreight_rest.py`**

Inside `_stub_odoo_for_transport()`, add to the `RestTransport` class:

```python
class RestTransport:
    def __init__(self, connector):
        self.connector = connector

    def send(self, payload, content_type='xml', filename=None, endpoint=None):
        return {'success': True}

    def send_put(self, payload, content_type='xml', endpoint=None):
        return {'success': True}

    def send_delete(self, endpoint):
        return {'success': True}

    def poll(self, path=None):
        return []
```

Add new test class at the bottom of `test_mainfreight_rest.py`:

```python
class TestMFCrudMethods(unittest.TestCase):

    def test_update_order_uses_put_with_order_endpoint(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_put', return_value={'success': True}) as mock_put:
            transport.update_order('<Order action="UPDATE"/>')
        mock_put.assert_called_once()
        call_kwargs = mock_put.call_args
        endpoint = call_kwargs[1].get('endpoint') or call_kwargs[0][2]
        self.assertIn('/Order', endpoint)
        self.assertIn('?region=ANZ', endpoint)

    def test_delete_order_uses_delete_with_ref_in_url(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_delete', return_value={'success': True}) as mock_del:
            transport.delete_order('SO-001')
        mock_del.assert_called_once()
        endpoint = mock_del.call_args[1].get('endpoint') or mock_del.call_args[0][0]
        self.assertIn('SO-001', endpoint)
        self.assertIn('/Order/', endpoint)
        self.assertIn('?region=ANZ', endpoint)

    def test_delete_order_url_encodes_ref_with_slash(self):
        """Order names containing '/' must be percent-encoded in the DELETE URL."""
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_delete', return_value={'success': True}) as mock_del:
            transport.delete_order('S/001')
        endpoint = mock_del.call_args[1].get('endpoint') or mock_del.call_args[0][0]
        self.assertNotIn('S/001', endpoint)   # raw slash must not appear
        self.assertIn('S%2F001', endpoint)

    def test_delete_inward_uses_delete_with_ref_in_url(self):
        transport = MainfreightRestTransport(_FakeConnector('test', mf_region='ANZ'))
        with patch.object(transport, 'send_delete', return_value={'success': True}) as mock_del:
            transport.delete_inward('PO-001')
        endpoint = mock_del.call_args[1].get('endpoint') or mock_del.call_args[0][0]
        self.assertIn('PO-001', endpoint)
        self.assertIn('/Inward/', endpoint)
        self.assertIn('?region=ANZ', endpoint)

    def test_update_order_production_uses_production_url(self):
        transport = MainfreightRestTransport(_FakeConnector('production', mf_region='ANZ'))
        with patch.object(transport, 'send_put', return_value={'success': True}) as mock_put:
            transport.update_order('<Order/>')
        endpoint = mock_put.call_args[1].get('endpoint') or mock_put.call_args[0][2]
        self.assertIn('api.mainfreight.com', endpoint)
        self.assertNotIn('api-test', endpoint)
```

**Step 2: Run to confirm failures**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py::TestMFCrudMethods -v -m "not odoo_integration"
```

Expected: `AttributeError: 'MainfreightRestTransport' has no attribute 'update_order'`

**Step 3: Implement in `mainfreight_rest.py`**

Add after `get_stock_on_hand()`:

```python
def update_order(self, payload):
    """PUT an updated order to MF — for amending an already-submitted sale order."""
    return self.send_put(payload, content_type='xml',
                         endpoint=f'{self._get_base_url()}/Order?region={self._region()}')

def delete_order(self, order_ref):
    """DELETE a previously submitted order from MF by client order reference."""
    return self.send_delete(
        endpoint=f'{self._get_base_url()}/Order/{quote(order_ref, safe="")}?region={self._region()}'
    )

def delete_inward(self, order_ref):
    """DELETE a previously submitted inward order from MF by reference."""
    return self.send_delete(
        endpoint=f'{self._get_base_url()}/Inward/{quote(order_ref, safe="")}?region={self._region()}'
    )
```

**Step 4: Run all mainfreight_rest tests**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py -v -m "not odoo_integration"
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/transport/mainfreight_rest.py \
        addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py
git commit -m "feat(transport): add update_order, delete_order, delete_inward to MF transport"
```

---

## Task 5: `SalesOrderDocument` — `action` param + `build_delete_ref()`

**Files:**
- Modify: `addons/stock_3pl_mainfreight/document/sales_order.py`
- Modify: `addons/stock_3pl_mainfreight/tests/test_sales_order.py`

**Context:** `InwardOrderDocument` already has an `action` param and produces `action=CREATE|UPDATE` on the XML root. `SalesOrderDocument` needs the same. `build_delete_ref()` returns the order name used in the DELETE URL path — no XML, just the reference string. Tests are Odoo integration tests (existing pattern in `test_sales_order.py`).

**Step 1: Add failing tests to `test_sales_order.py`**

Add to `TestSalesOrderDocument`:

```python
def test_build_outbound_default_action_is_create(self):
    from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
    doc = SalesOrderDocument(self.connector, self.env)
    xml = doc.build_outbound(self.order)
    root = etree.fromstring(xml.encode(), _XML_PARSER)
    self.assertEqual(root.get('action'), 'CREATE')

def test_build_outbound_update_action(self):
    from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
    doc = SalesOrderDocument(self.connector, self.env)
    xml = doc.build_outbound(self.order, action='update')
    root = etree.fromstring(xml.encode(), _XML_PARSER)
    self.assertEqual(root.get('action'), 'UPDATE')

def test_build_outbound_invalid_action_raises(self):
    from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
    doc = SalesOrderDocument(self.connector, self.env)
    with self.assertRaises(ValueError):
        doc.build_outbound(self.order, action='delete')

def test_build_delete_ref_returns_order_name(self):
    from odoo.addons.stock_3pl_mainfreight.document.sales_order import SalesOrderDocument
    doc = SalesOrderDocument(self.connector, self.env)
    ref = doc.build_delete_ref(self.order)
    self.assertEqual(ref, self.order.name)
```

**Step 2: Run to confirm failures**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb 2>&1 | grep -E "FAIL|ERROR|test_build"
```

Expected: `test_build_outbound_default_action_is_create` fails (no `action` attribute).

**Step 3: Update `sales_order.py`**

```python
def build_outbound(self, order, action='create'):
    """Build MF Sales Order XML (SOH header + SOL lines) for a sale.order record.

    action: 'create' (default) or 'update'. Controls the action= attribute on <Order>.
    """
    if action not in ('create', 'update'):
        raise ValueError(f"SalesOrderDocument.build_outbound: invalid action {action!r}")
    root = etree.Element('Order', action=action.upper())
    # ... rest of method unchanged ...
```

Add after `get_idempotency_key`:

```python
def build_delete_ref(self, order):
    """Return the order reference used in the MF DELETE /Order/{ref} URL path."""
    return order.name
```

**Step 4: Run tests**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb 2>&1 | grep -E "ok|FAIL|ERROR" | grep "test_build"
```

Expected: all new tests PASS, existing tests still PASS (backward-compatible — `action` defaults to `'create'`).

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/document/sales_order.py \
        addons/stock_3pl_mainfreight/tests/test_sales_order.py
git commit -m "feat(document): add action param and build_delete_ref to SalesOrderDocument"
```

---

## Task 6: `InwardOrderDocument` — `build_delete_ref()`

**Files:**
- Modify: `addons/stock_3pl_mainfreight/document/inward_order.py`
- Modify: `addons/stock_3pl_mainfreight/tests/test_inward_order_builder.py`

**Context:** `InwardOrderDocument` already handles `create`/`update`. Just needs `build_delete_ref()` to match the pattern.

**Step 1: Add failing test to `test_inward_order_builder.py`**

Add to `TestInwardOrderBuilder`:

```python
def test_build_delete_ref_returns_po_name(self):
    doc = self._doc()
    ref = doc.build_delete_ref(self.booking)
    self.assertEqual(ref, self.po.name)

def test_build_delete_ref_falls_back_to_booking_name_when_no_po(self):
    """If booking has no purchase_order_id, use booking.name as fallback."""
    from odoo.addons.stock_3pl_mainfreight.document.inward_order import InwardOrderDocument
    # Create a booking without a PO link
    nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) \
          or self.env.company.currency_id
    bare_booking = self.env['freight.booking'].create({
        'name': 'FB-NOPO',
        'carrier_id': self.env['delivery.carrier'].search([], limit=1).id,
        'currency_id': nzd.id,
    })
    doc = InwardOrderDocument(self.connector, self.env)
    ref = doc.build_delete_ref(bare_booking)
    self.assertEqual(ref, 'FB-NOPO')
```

**Step 2: Confirm failure, then implement**

Add to `inward_order.py` after `get_idempotency_key`:

```python
def build_delete_ref(self, booking):
    """Return the inward order reference used in the MF DELETE /Inward/{ref} URL path."""
    return booking.purchase_order_id.name if booking.purchase_order_id else booking.name
```

**Step 3: Run and verify**

```bash
python odoo-bin -u stock_3pl_mainfreight --test-enable --stop-after-init -d testdb 2>&1 | grep -E "test_build_delete"
```

**Step 4: Commit**

```bash
git add addons/stock_3pl_mainfreight/document/inward_order.py \
        addons/stock_3pl_mainfreight/tests/test_inward_order_builder.py
git commit -m "feat(document): add build_delete_ref to InwardOrderDocument"
```

---

## Task 7: Dual tracking status map (flat `Status` + `eventCode` fallback)

**Files:**
- Modify: `addons/stock_3pl_mainfreight/transport/mainfreight_rest.py`
- Modify: `addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py`

**Context:** Polling API may return either `{"Status": "DELIVERED"}` (PDF spec) or the richer schema with `{"events": [{"sequence": 1, "code": "GoodsDelivered"}]}` (public API format). The integration must handle both without knowing in advance which format MF serves.

**Step 1: Add failing tests**

Add new test class to `test_mainfreight_rest.py`:

```python
class TestTrackingStatusMap(unittest.TestCase):

    def setUp(self):
        self.transport = MainfreightRestTransport(_FakeConnector('test'))

    def _call_with_response(self, response_data):
        """Helper: patch requests.get to return response_data as JSON, call get_tracking_status."""
        import json
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = response_data
        with patch('requests.get', return_value=mock_resp):
            return self.transport.get_tracking_status('OTR000001')

    # --- Existing flat-Status path (must still work) ---

    def test_flat_status_delivered_maps_correctly(self):
        result = self._call_with_response({'Status': 'DELIVERED'})
        self.assertEqual(result.get('status'), 'mf_delivered')

    def test_flat_status_dispatched_maps_correctly(self):
        result = self._call_with_response({'Status': 'DISPATCHED'})
        self.assertEqual(result.get('status'), 'mf_dispatched')

    def test_flat_status_unknown_returns_empty(self):
        result = self._call_with_response({'Status': 'UNKNOWN_CODE'})
        self.assertEqual(result, {})

    # --- New eventCode fallback path ---

    def test_event_code_goods_delivered_maps_to_mf_delivered(self):
        data = {'events': [{'sequence': 1, 'code': 'GoodsDelivered'}]}
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_delivered')

    def test_event_code_picked_up_maps_to_mf_dispatched(self):
        data = {'events': [{'sequence': 1, 'code': 'PickedUp'}]}
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_dispatched')

    def test_event_code_latest_event_used_when_multiple(self):
        """When multiple events exist, the one with the highest sequence wins."""
        data = {
            'events': [
                {'sequence': 1, 'code': 'PickedUp'},
                {'sequence': 3, 'code': 'GoodsDelivered'},
                {'sequence': 2, 'code': 'InTransit'},
            ]
        }
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_delivered')

    def test_event_code_unknown_returns_empty(self):
        data = {'events': [{'sequence': 1, 'code': 'SomeUnknownEvent'}]}
        result = self._call_with_response(data)
        self.assertEqual(result, {})

    def test_no_status_and_no_events_returns_empty(self):
        result = self._call_with_response({'trackingUrl': 'https://track.example.com'})
        self.assertEqual(result, {})

    def test_flat_status_takes_priority_over_events(self):
        """If both Status and events are present, flat Status wins."""
        data = {
            'Status': 'IN_TRANSIT',
            'events': [{'sequence': 1, 'code': 'GoodsDelivered'}],
        }
        result = self._call_with_response(data)
        self.assertEqual(result.get('status'), 'mf_in_transit')
```

**Step 2: Run to confirm failures**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py::TestTrackingStatusMap -v -m "not odoo_integration"
```

Expected: `eventCode` tests FAIL (`result == {}`).

**Step 3: Update `mainfreight_rest.py`**

Add after `MF_TRACKING_STATUS_MAP`:

```python
# Fallback map for the richer public API schema where tracking events use
# an eventCode string rather than a flat Status field.
# See: https://developer.mainfreight.com/global/en/global-home/subscription-api/tracking-update-webhook.aspx
MF_EVENT_CODE_MAP = {
    'GoodsDelivered':    'mf_delivered',
    'PickedUp':          'mf_dispatched',
    'InTransit':         'mf_in_transit',
    'OutForDelivery':    'mf_out_for_delivery',
    'GoodsReceived':     'mf_received',
    'DeliveryException': 'mf_exception',
}
```

Update `get_tracking_status()` — replace the `mf_status = data.get('Status')` block:

```python
# Path A: flat Status field (PDF spec format)
mf_status = data.get('Status')
mapped_status = MF_TRACKING_STATUS_MAP.get(mf_status)

# Path B: eventCode from events array (public API / webhook schema)
# Only used if flat Status produced no match.
if mapped_status is None:
    events = data.get('events', [])
    if events:
        latest = sorted(events, key=lambda e: e.get('sequence', 0), reverse=True)[0]
        event_code = latest.get('code') or latest.get('eventCode', '')
        mapped_status = MF_EVENT_CODE_MAP.get(event_code)

if mapped_status is None:
    _logger.warning(
        'get_tracking_status: unknown MF status/eventCode for connote %s '
        '(Status=%r, events=%r)',
        connote,
        data.get('Status'),
        [e.get('code') for e in data.get('events', [])],
    )
    return {}
```

**Step 4: Run all tests**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py -v -m "not odoo_integration"
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/transport/mainfreight_rest.py \
        addons/stock_3pl_mainfreight/tests/test_mainfreight_rest.py
git commit -m "feat(tracking): dual status map — flat Status + eventCode fallback for public API schema"
```

---

## Task 8: Dual SO Confirmation parser (SCH/SCL + webhook-style schema)

**Files:**
- Modify: `addons/stock_3pl_mainfreight/document/so_confirmation.py`
- Create: `addons/stock_3pl_mainfreight/tests/test_so_confirmation_parser.py`

**Context:** `so_confirmation.py` only parses the PDF-spec `SCH/SCL` XML. The public API delivers a richer schema with camelCase element names (`orderReference`, `orderConfirmationLines`, etc.). Both schemas must parse to the same normalised dict so `apply_inbound()` is unchanged.

The new test file is **pure-Python** (no Odoo) since the parsing is pure XML. It uses the same importlib/stub approach as `test_mainfreight_rest.py`.

**Step 1: Create `test_so_confirmation_parser.py`**

```python
# addons/stock_3pl_mainfreight/tests/test_so_confirmation_parser.py
"""
Pure-Python tests for SOConfirmationDocument dual-schema parser.
No Odoo runtime required — tests parse_inbound() and its two sub-paths.
"""
import sys
import types
import unittest
import importlib.util
import pathlib


def _stub_odoo_for_document():
    """Minimal Odoo stubs so so_confirmation.py can be imported."""
    if 'odoo' not in sys.modules:
        sys.modules['odoo'] = types.ModuleType('odoo')

    # odoo.exceptions.ValidationError
    exc_mod = types.ModuleType('odoo.exceptions')
    class ValidationError(Exception):
        pass
    exc_mod.ValidationError = ValidationError
    sys.modules['odoo.exceptions'] = exc_mod

    # odoo.addons.stock_3pl_core.models.document_base.AbstractDocument
    class AbstractDocument:
        def __init__(self, connector, env):
            self.connector = connector
            self.env = env
        def truncate(self, value, max_len=None):
            if max_len and value:
                return str(value)[:max_len]
            return str(value) if value is not None else ''
        @staticmethod
        def make_idempotency_key(*args):
            return ':'.join(str(a) for a in args)

    core = types.ModuleType('odoo.addons.stock_3pl_core')
    core_models = types.ModuleType('odoo.addons.stock_3pl_core.models')
    core_doc = types.ModuleType('odoo.addons.stock_3pl_core.models.document_base')
    core_doc.AbstractDocument = AbstractDocument
    sys.modules.setdefault('odoo.addons', types.ModuleType('odoo.addons'))
    sys.modules['odoo.addons.stock_3pl_core'] = core
    sys.modules['odoo.addons.stock_3pl_core.models'] = core_models
    sys.modules['odoo.addons.stock_3pl_core.models.document_base'] = core_doc


_stub_odoo_for_document()

_DOC_DIR = pathlib.Path(__file__).parent.parent / 'document'

def _load_doc(name):
    spec = importlib.util.spec_from_file_location(name, _DOC_DIR / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_so_conf_mod = _load_doc('so_confirmation')
SOConfirmationDocument = _so_conf_mod.SOConfirmationDocument


# --- Fixture XML strings ---

SCH_SCL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<SOConfirmation>
  <SCH>
    <Reference>SO001</Reference>
    <ConsignmentNo>OTR000000134</ConsignmentNo>
    <CarrierName>MAINFREIGHT</CarrierName>
    <FinalisedDate>01/03/2026</FinalisedDate>
    <ETADate>03/03/2026</ETADate>
    <Lines>
      <SCL>
        <ProductCode>WIDG001</ProductCode>
        <UnitsFulfilled>10</UnitsFulfilled>
        <LotNumber>LOT001</LotNumber>
      </SCL>
    </Lines>
  </SCH>
</SOConfirmation>
"""

WEBHOOK_STYLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<orderConfirmation>
  <customerOrderReference>SO001</customerOrderReference>
  <orderReference>ORD-9999</orderReference>
  <dateDispatched>2026-03-01</dateDispatched>
  <etaDate>2026-03-03</etaDate>
  <serviceProvider>
    <name>MAINFREIGHT</name>
  </serviceProvider>
  <consignments>
    <consignment>
      <consignmentNumber>OTR000000134</consignmentNumber>
    </consignment>
  </consignments>
  <orderConfirmationLines>
    <orderConfirmationLine>
      <productCode>WIDG001</productCode>
      <unitsFulfilled>10</unitsFulfilled>
      <lotNumber>LOT001</lotNumber>
    </orderConfirmationLine>
  </orderConfirmationLines>
</orderConfirmation>
"""


class TestSOConfirmationSchSclParser(unittest.TestCase):
    """Original SCH/SCL XML path."""

    def setUp(self):
        self.doc = SOConfirmationDocument(connector=None, env=None)

    def test_parses_reference(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['reference'], 'SO001')

    def test_parses_consignment_no(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['consignment_no'], 'OTR000000134')

    def test_parses_carrier_name(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['carrier_name'], 'MAINFREIGHT')

    def test_parses_finalised_date(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertIsNotNone(result['finalised_date'])

    def test_parses_eta_date(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertIsNotNone(result['eta_date'])

    def test_parses_line_product_code(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['lines'][0]['product_code'], 'WIDG001')

    def test_parses_line_qty_done(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['lines'][0]['qty_done'], 10.0)

    def test_parses_line_lot_number(self):
        result = self.doc.parse_inbound(SCH_SCL_XML)
        self.assertEqual(result['lines'][0]['lot_number'], 'LOT001')


class TestSOConfirmationWebhookStyleParser(unittest.TestCase):
    """Webhook/public-API schema path — camelCase elements, richer structure."""

    def setUp(self):
        self.doc = SOConfirmationDocument(connector=None, env=None)

    def test_parses_customer_order_reference_as_reference(self):
        """customerOrderReference (SO name) takes priority over orderReference."""
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['reference'], 'SO001')

    def test_parses_consignment_number(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['consignment_no'], 'OTR000000134')

    def test_parses_carrier_from_service_provider(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['carrier_name'], 'MAINFREIGHT')

    def test_parses_finalised_date_from_date_dispatched(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertIsNotNone(result['finalised_date'])

    def test_parses_eta_date(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertIsNotNone(result['eta_date'])

    def test_parses_line_product_code(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['lines'][0]['product_code'], 'WIDG001')

    def test_parses_line_qty_done(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['lines'][0]['qty_done'], 10.0)

    def test_parses_line_lot_number(self):
        result = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(result['lines'][0]['lot_number'], 'LOT001')

    def test_both_schemas_produce_same_keys(self):
        """Both parse paths must return the same dict shape for apply_inbound()."""
        sch_scl = self.doc.parse_inbound(SCH_SCL_XML)
        webhook = self.doc.parse_inbound(WEBHOOK_STYLE_XML)
        self.assertEqual(set(sch_scl.keys()), set(webhook.keys()))


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run to confirm failures**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_so_confirmation_parser.py -v
```

Expected: `TestSOConfirmationWebhookStyleParser` tests FAIL (parser only handles SCH/SCL).

**Step 3: Refactor `so_confirmation.py` to support dual schemas**

Replace `parse_inbound()` with delegating version; extract original logic to `_parse_sch_scl()`:

```python
def parse_inbound(self, payload):
    """Parse MF SO Confirmation into a normalised dict.

    Detects schema automatically:
    - SCH/SCL XML (PDF spec): root tag SOConfirmation with nested SCH element
    - Webhook-style XML (public API): root tag orderConfirmation with camelCase elements
    """
    root = etree.fromstring(payload.encode('utf-8'), _XML_PARSER)
    if root.find('SCH') is not None or root.tag in ('SOConfirmation', 'SCH'):
        return self._parse_sch_scl(root)
    return self._parse_webhook_style(root)

def _parse_sch_scl(self, root):
    """Original PDF-spec parser: SCH header + SCL lines."""
    sch = root.find('SCH') or root
    lines = []
    for scl in sch.findall('Lines/SCL'):
        lines.append({
            'product_code': scl.findtext('ProductCode', '').strip(),
            'qty_done': float(scl.findtext('UnitsFulfilled', '0').strip() or '0'),
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

def _parse_webhook_style(self, root):
    """Webhook/public-API schema: camelCase elements, richer structure.

    customerOrderReference is the Odoo SO name (maps to 'reference').
    orderReference is the MF internal reference — used as fallback only.
    TODO: expand field coverage when webhook is activated on cloud hosting.
    """
    lines = []
    for line_el in root.findall('.//orderConfirmationLine'):
        lines.append({
            'product_code': line_el.findtext('productCode', '').strip(),
            'qty_done': float(line_el.findtext('unitsFulfilled', '0').strip() or '0'),
            'lot_number': line_el.findtext('lotNumber', '').strip(),
        })
    reference = (
        root.findtext('customerOrderReference', '').strip()
        or root.findtext('orderReference', '').strip()
    )
    consignment_no = ''
    consignment_el = root.find('.//consignment')
    if consignment_el is not None:
        consignment_no = consignment_el.findtext('consignmentNumber', '').strip()
    carrier_name = ''
    sp_el = root.find('serviceProvider')
    if sp_el is not None:
        carrier_name = sp_el.findtext('name', '').strip()
    return {
        'reference': reference,
        'consignment_no': consignment_no,
        'carrier_name': carrier_name,
        'finalised_date': self._parse_date(root.findtext('dateDispatched', '')),
        'eta_date': self._parse_date(root.findtext('etaDate', '')),
        'lines': lines,
    }
```

**Step 4: Run all tests**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_so_confirmation_parser.py -v
python -m pytest -m "not odoo_integration" -q
```

Expected: all pure-Python tests PASS.

**Step 5: Commit**

```bash
git add addons/stock_3pl_mainfreight/document/so_confirmation.py \
        addons/stock_3pl_mainfreight/tests/test_so_confirmation_parser.py
git commit -m "feat(parser): dual-schema SO confirmation parser — SCH/SCL + webhook-style XML"
```

---

## Task 9: Webhook stub controller

**Files:**
- Create: `addons/stock_3pl_mainfreight/controllers/__init__.py`
- Create: `addons/stock_3pl_mainfreight/controllers/webhook.py`
- Modify: `addons/stock_3pl_mainfreight/__init__.py`
- Create: `addons/stock_3pl_mainfreight/tests/test_webhook_controller.py`

**Context:** Three dormant POST routes registered under `/mf/webhook/`. Each validates a shared secret, logs the payload, returns 200. No payload processing until cloud hosting. The auth check is extracted to a module-level function so it can be tested without the Odoo HTTP stack.

**Step 1: Create failing test file**

```python
# addons/stock_3pl_mainfreight/tests/test_webhook_controller.py
"""
Pure-Python tests for the MF webhook stub controller.
Tests the secret validation helper only — HTTP routing tested manually.
No Odoo runtime required.
"""
import sys
import types
import unittest
import importlib.util
import pathlib
from unittest.mock import MagicMock


def _stub_odoo_for_controller():
    """Minimal stubs for Odoo http + models."""
    if 'odoo' not in sys.modules:
        sys.modules['odoo'] = types.ModuleType('odoo')

    # odoo.http
    http_mod = types.ModuleType('odoo.http')
    http_mod.request = MagicMock()

    class Controller:
        pass

    def route(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

    http_mod.Controller = Controller
    http_mod.route = route
    http_mod.Response = MagicMock()
    sys.modules['odoo.http'] = http_mod

    # odoo.addons
    sys.modules.setdefault('odoo.addons', types.ModuleType('odoo.addons'))


_stub_odoo_for_controller()

_CTRL_DIR = pathlib.Path(__file__).parent.parent / 'controllers'


def _load_controller(name):
    spec = importlib.util.spec_from_file_location(name, _CTRL_DIR / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_webhook_mod = _load_controller('webhook')
_validate_webhook_secret = _webhook_mod._validate_webhook_secret


class TestValidateWebhookSecret(unittest.TestCase):

    def _make_env(self, stored_secret):
        env = MagicMock()
        env.__getitem__.return_value.sudo.return_value.get_param.return_value = stored_secret
        return env

    def test_matching_secret_returns_true(self):
        env = self._make_env('supersecret')
        self.assertTrue(_validate_webhook_secret(env, 'supersecret'))

    def test_wrong_secret_returns_false(self):
        env = self._make_env('supersecret')
        self.assertFalse(_validate_webhook_secret(env, 'wrongsecret'))

    def test_empty_request_secret_returns_false(self):
        env = self._make_env('supersecret')
        self.assertFalse(_validate_webhook_secret(env, ''))

    def test_none_request_secret_returns_false(self):
        env = self._make_env('supersecret')
        self.assertFalse(_validate_webhook_secret(env, None))

    def test_empty_stored_secret_returns_false_even_with_match(self):
        """An unconfigured webhook secret must never grant access."""
        env = self._make_env('')
        self.assertFalse(_validate_webhook_secret(env, ''))

    def test_none_stored_secret_returns_false(self):
        env = self._make_env(None)
        self.assertFalse(_validate_webhook_secret(env, 'anysecret'))


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run to confirm failures**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_webhook_controller.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — controllers directory doesn't exist yet.

**Step 3: Create controller files**

`controllers/__init__.py`:
```python
from . import webhook
```

`controllers/webhook.py`:
```python
# addons/stock_3pl_mainfreight/controllers/webhook.py
"""
Mainfreight Subscription API webhook receiver — dormant stub.

Three routes accept POST from MF's subscription API.
Currently: validates secret, logs payload, returns 200.
Does NOT process payloads — activate when on cloud hosting.

TODO: wire to inbound message queue when on cloud hosting.
See: docs/plans/2026-03-02-api-gap-sprint-design.md#gap-3
"""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_WEBHOOK_SECRET_PARAM = 'stock_3pl_mainfreight.webhook_secret'


def _validate_webhook_secret(env, request_secret):
    """Return True if request_secret matches the configured webhook secret.

    An empty stored secret always returns False — an unconfigured instance
    must never accept webhook calls.
    """
    if not request_secret:
        return False
    stored = env['ir.config_parameter'].sudo().get_param(_WEBHOOK_SECRET_PARAM, default='')
    return bool(stored) and stored == request_secret


class MFWebhookController(http.Controller):

    @http.route('/mf/webhook/order-confirmation', type='http', auth='none', methods=['POST'], csrf=False)
    def order_confirmation(self, **kwargs):
        return self._handle_webhook('order_confirmation')

    @http.route('/mf/webhook/inward-confirmation', type='http', auth='none', methods=['POST'], csrf=False)
    def inward_confirmation(self, **kwargs):
        return self._handle_webhook('inward_confirmation')

    @http.route('/mf/webhook/tracking-update', type='http', auth='none', methods=['POST'], csrf=False)
    def tracking_update(self, **kwargs):
        return self._handle_webhook('tracking_update')

    def _handle_webhook(self, event_type):
        """Validate secret, log payload, return 200. Stub — no processing yet."""
        secret = request.httprequest.headers.get('X-MF-Secret', '')
        if not _validate_webhook_secret(request.env, secret):
            _logger.warning('MF webhook %s: rejected — invalid or missing X-MF-Secret', event_type)
            return request.make_response(
                json.dumps({'error': 'Unauthorized'}),
                headers=[('Content-Type', 'application/json')],
                status=401,
            )
        body = request.httprequest.get_data(as_text=True)
        _logger.info('MF webhook %s received (stub — not processed): %.500s', event_type, body)
        # TODO: wire to inbound message queue when on cloud hosting
        # Example future code:
        # msg = request.env['3pl.message'].sudo().create({...})
        return request.make_response(
            json.dumps({'status': 'received'}),
            headers=[('Content-Type', 'application/json')],
        )
```

**Step 4: Register controllers in `__init__.py`**

In `addons/stock_3pl_mainfreight/__init__.py`, add:

```python
from . import models
from . import document
from . import transport
from . import wizard
from . import controllers
```

**Step 5: Run tests to confirm all pass**

```bash
python -m pytest addons/stock_3pl_mainfreight/tests/test_webhook_controller.py -v
python -m pytest -m "not odoo_integration" -q
```

Expected: all pure-Python tests PASS. Full suite should be ~337+ tests (312 existing + 25+ new).

**Step 6: Commit**

```bash
git add addons/stock_3pl_mainfreight/controllers/__init__.py \
        addons/stock_3pl_mainfreight/controllers/webhook.py \
        addons/stock_3pl_mainfreight/__init__.py \
        addons/stock_3pl_mainfreight/tests/test_webhook_controller.py
git commit -m "feat(webhook): add dormant MF webhook stub controller — order/inward/tracking routes"
```

---

## Final verification

Run the full pure-Python test suite and confirm count:

```bash
python -m pytest -m "not odoo_integration" -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass, total count ~337+.

Run a final full-suite check (shows expected Odoo integration failures):

```bash
python -m pytest -q
```

Commit a test count update to the README if the count changed significantly:

```bash
git add README.md
git commit -m "docs: update test count post API gap sprint"
```

---

## Open questions to resolve with MF before go-live

1. **Test environment base URL** — confirm `api-test.mainfreight.com` or alternative. Validate with a real credential against `/StockOnHand?region=ANZ`.
2. **`region` exact value for NZ** — confirm `ANZ` (vs `NZ`, `NewZealand`, `nz`).
3. **Tracking poll response schema** — confirm whether `GET /Tracking/{connote}` returns flat `{Status}` or richer `{events[]}`.
4. **SO Confirmation inbound schema** — confirm whether REST polling returns SCH/SCL XML or webhook-style JSON/XML.
