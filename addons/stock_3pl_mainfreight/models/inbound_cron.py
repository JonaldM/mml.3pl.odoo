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
        2. Flags stale mf_sent pickings (no connote after threshold) as mf_exception.
        """
        self._poll_inventory_reports()
        self._reconcile_sent_orders()

    @api.model
    def _poll_inventory_reports(self):
        """Poll each active MF connector for inbound SOH inventory report files.

        For each active connector with warehouse_partner='mainfreight':
          - Calls connector.get_transport().poll() to retrieve available files.
          - SFTP poll returns [(filename, content), ...] tuples.
          - REST poll returns [raw_string, ...] (list of raw CSV strings).
          - Each item is applied via InventoryReportDocument.apply_csv().

        Per-connector, per-file errors are caught and logged — one bad file
        must not prevent other connectors or files from being processed.
        """
        from odoo.addons.stock_3pl_mainfreight.document.inventory_report import (
            InventoryReportDocument,
        )

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
