# addons/stock_3pl_mainfreight/models/inbound_cron.py
"""MF inbound cron — polls connectors for inventory reports and reconciles stale orders."""
import logging
from datetime import timedelta

from odoo import models, api, fields

_logger = logging.getLogger(__name__)

_MAX_CSV_BYTES = 50 * 1024 * 1024  # 50 MB


class MFInboundCron(models.AbstractModel):
    """Cron service model for the Mainfreight inbound polling pipeline.

    AbstractModel — no stored fields, no database table.
    Invoked via ir.cron as: self.env['mf.inbound.cron']._run_mf_inbound()

    Two responsibilities:
      1. Poll active MF connectors for SOH inventory report files and apply them.
      2. Flag stale mf_sent pickings (no connote after threshold hours) as mf_exception.
    """
    _name = 'mf.inbound.cron'
    _description = 'MF Inbound Cron'

    @api.model
    def _run_mf_inbound(self):
        """Main entry point called by the MF inbound ir.cron job.

        1. Polls all active MF connectors for inbound inventory report files
           and applies them to stock.quant.
        2. Processes 3pl.message records in state 'received' (XML inbound path).
        3. Flags stale mf_sent pickings (no connote after threshold) as mf_exception.
        """
        self._poll_inventory_reports()
        self._process_inbound_messages()
        self._reconcile_sent_orders()

    @api.model
    def _poll_inventory_reports(self):
        """Poll each active MF connector for inbound SOH inventory report files.

        For each active connector with warehouse_partner='mainfreight':
          - Calls connector.get_transport().poll() to retrieve available files.
          - SFTP poll returns [(filename, content), ...] tuples.
          - REST poll returns [raw_string, ...] (list of raw CSV strings).
          - Each item is dispatched to the correct document handler:
              * ACKH_*/ACKL_* filenames or CSV with ClientOrderNumber header
                → SOAcknowledgementDocument.apply_csv()
              * Everything else → InventoryReportDocument.apply_csv()

        Per-connector, per-file errors are caught and logged — one bad file
        must not prevent other connectors or files from being processed.
        """
        from odoo.addons.stock_3pl_mainfreight.document.inventory_report import (
            InventoryReportDocument,
        )
        from odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement import (
            SOAcknowledgementDocument,
        )
        from odoo.addons.stock_3pl_core.models.message import ThreePlMessage

        connectors = self.env['3pl.connector'].search([
            ('active', '=', True),
            ('warehouse_partner', '=', 'mainfreight'),
        ])

        for connector in connectors:
            applied = 0
            skipped = 0
            try:
                items = connector.get_transport().poll()
            except Exception as exc:
                _logger.error(
                    '_poll_inventory_reports: poll failed for connector %s: %s',
                    connector.name, exc,
                )
                continue

            for item in items:
                try:
                    # Detect SFTP tuple (filename, content) vs REST raw string
                    if isinstance(item, tuple):
                        filename, content = item
                    else:
                        filename = '<rest>'
                        content = item

                    # Skip if the content does not look like CSV inventory data
                    if not isinstance(content, str) or not content.strip():
                        _logger.warning(
                            '_poll_inventory_reports: connector=%s file=%s — '
                            'empty or non-string content, skipping',
                            connector.name, filename,
                        )
                        skipped += 1
                        continue

                    if len(content.encode('utf-8')) > _MAX_CSV_BYTES:
                        _logger.warning(
                            'MF inbound: connector=%s, file=%s exceeds 50 MB limit (%d bytes) — skipping.',
                            connector.name, filename, len(content.encode('utf-8')),
                        )
                        skipped += 1
                        continue

                    # Detect whether this is an ACK file (ACKH/ACKL) or a SOH
                    # inventory report.  Check filename prefix first (cheap),
                    # then fall back to header-sniffing via _detect_inbound_type.
                    basename = filename.upper()
                    is_ack = basename.startswith('ACKH_') or basename.startswith('ACKL_')
                    if not is_ack:
                        is_ack = ThreePlMessage._detect_inbound_type(content) == 'so_acknowledgement'

                    if is_ack:
                        _logger.info(
                            'MF inbound: connector=%s file=%s — detected SO Acknowledgement, '
                            'dispatching to SOAcknowledgementDocument',
                            connector.name, filename,
                        )
                        doc = SOAcknowledgementDocument(connector=connector, env=self.env)
                        doc.apply_csv(content)
                    else:
                        doc = InventoryReportDocument(connector=connector, env=self.env)
                        doc.apply_csv(content, report_date=fields.Datetime.now())
                    applied += 1

                except Exception as exc:
                    _logger.error(
                        '_poll_inventory_reports: connector=%s file=%s — error: %s',
                        connector.name, filename, exc,
                    )
                    skipped += 1

            _logger.info(
                'MF inbound: connector=%s, files=%d applied, %d skipped',
                connector.name, applied, skipped,
            )

    @api.model
    def _process_inbound_messages(self):
        """Process 3pl.message records that arrived via the queue path (state='received').

        Path A inbound messages (XML SO Confirmations, SO Acknowledgements, Inventory
        Reports) are created by ThreePlMessage._poll_inbound() with state='received'.
        This method dispatches each message to the correct document handler, then
        transitions the record to 'applied' on success or 'dead' on failure.

        Dispatch table:
          so_confirmation    → SOConfirmationDocument.apply_inbound(msg)
          so_acknowledgement → SOAcknowledgementDocument.apply_inbound(msg)
          inventory_report   → InventoryReportDocument.apply_inbound(msg)
          anything else      → warning logged, message skipped (not written)

        Per-message exceptions do not stop processing of subsequent messages.
        A summary INFO log is emitted at the end of the loop.
        """
        from odoo.addons.stock_3pl_mainfreight.document.so_confirmation import (
            SOConfirmationDocument,
        )
        from odoo.addons.stock_3pl_mainfreight.document.so_acknowledgement import (
            SOAcknowledgementDocument,
        )
        from odoo.addons.stock_3pl_mainfreight.document.inventory_report import (
            InventoryReportDocument,
        )

        messages = self.env['3pl.message'].search([
            ('direction', '=', 'inbound'),
            ('state', '=', 'received'),
            ('connector_id.warehouse_partner', '=', 'mainfreight'),
        ], order='create_date asc')

        _HANDLERS = {
            'so_confirmation': SOConfirmationDocument,
            'so_acknowledgement': SOAcknowledgementDocument,
            'inventory_report': InventoryReportDocument,
        }

        processed = 0
        dead = 0
        skipped = 0

        for msg in messages:
            doc_type = msg.document_type
            handler_cls = _HANDLERS.get(doc_type)

            if handler_cls is None:
                _logger.warning(
                    '_process_inbound_messages: unrecognised document_type=%s '
                    'on message id=%s — skipping',
                    doc_type, msg.id,
                )
                skipped += 1
                continue

            try:
                handler_cls(connector=msg.connector_id, env=self.env).apply_inbound(msg)
                msg.write({'state': 'applied'})
                processed += 1
            except Exception as exc:
                _logger.error(
                    '_process_inbound_messages: failed to apply message id=%s '
                    'document_type=%s: %s',
                    msg.id, doc_type, exc,
                )
                msg.write({
                    'state': 'dead',
                    'last_error': str(exc)[:500],
                })
                dead += 1

        _logger.info(
            'MF inbound: processed=%d dead=%d skipped=%d',
            processed, dead, skipped,
        )

    @api.model
    def _reconcile_sent_orders(self):
        """Flag stale mf_sent pickings as mf_exception.

        A picking is considered stale when:
          - x_mf_status = 'mf_sent' (sent to MF but no further update)
          - write_date < cutoff (older than the configured threshold)
          - x_mf_connote = False (MF has not yet confirmed with a connote number)

        The threshold (hours) is read from the system parameter
        'stock_3pl_mainfreight.reconcile_hours', defaulting to 48 hours.

        Per-picking errors are caught and logged — one bad picking must not
        block the rest of the reconciliation batch.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            threshold = int(
                ICP.get_param('stock_3pl_mainfreight.reconcile_hours', default=48)
            )
        except (ValueError, TypeError):
            threshold = 48

        cutoff = fields.Datetime.now() - timedelta(hours=threshold)

        stale_pickings = self.env['stock.picking'].search([
            ('x_mf_status', '=', 'mf_sent'),
            ('write_date', '<', cutoff),
            ('x_mf_connote', '=', False),
        ])

        flagged = 0
        for picking in stale_pickings:
            try:
                _logger.warning(
                    '_reconcile_sent_orders: flagging stale picking %s as mf_exception '
                    '(mf_sent since %s, no connote)',
                    picking.name, picking.write_date,
                )
                picking.write({'x_mf_status': 'mf_exception'})
                flagged += 1
            except Exception as exc:
                _logger.error(
                    '_reconcile_sent_orders: error flagging picking %s: %s',
                    picking.name, exc,
                )

        _logger.info(
            '_reconcile_sent_orders: flagged %d stale pickings as mf_exception '
            '(threshold=%dh)',
            flagged, threshold,
        )
