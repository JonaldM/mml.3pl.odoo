# addons/stock_3pl_mainfreight/models/tracking_cron.py
"""MF tracking cron — polls MF Tracking API and updates x_mf_status on pickings."""
import html
import logging
import re
from datetime import datetime, timezone
from odoo import models, api

_logger = logging.getLogger(__name__)

# Statuses that are still in-flight and should be polled for updates.
_TRACKABLE_STATUSES = (
    'mf_sent',
    'mf_received',
    'mf_dispatched',
    'mf_in_transit',
    'mf_out_for_delivery',
)

# Terminal statuses — do not overwrite these with a poll result.
_TERMINAL_STATUSES = ('mf_delivered', 'mf_exception')

# One alert per hour per module — prevents inbox flooding on repeated failures.
_ALERT_COOLDOWN_SECONDS = 3600


def _phase0_should_target(picking) -> bool:
    """Return True if this picking should be processed by the Phase 0 chained reference query."""
    return (
        picking.x_mf_status == 'mf_sent'
        and not picking.x_mf_connote
        and bool(picking.x_mf_outbound_ref)
    )


class MFTrackingCron(models.AbstractModel):
    """Cron service model for the Mainfreight tracking poll pipeline.

    AbstractModel — no stored fields, no database table.
    Invoked via ir.cron as: self.env['mf.tracking.cron']._run_mf_tracking()
    """
    _name = 'mf.tracking.cron'
    _description = 'MF Tracking Cron'

    @api.model
    def _run_mf_tracking_phase0(self):
        """Phase 0 — chained reference query.

        Finds pickings with x_mf_status='mf_sent', no connote, and an outbound_ref set.
        Queries the Mainfreight Tracking API by OutboundReference (chained mode).
        If a linked transport consignment is returned, writes:
          - x_mf_connote (enables Phase 1 to take over from next cycle)
          - x_mf_tracking_url
          - x_mf_status -> 'mf_dispatched'
          - x_mf_dispatched_date -> now()
        and posts a chatter note on the linked SO.

        Per-picking errors are caught and logged.
        """
        pickings = self.env['stock.picking'].search([
            ('x_mf_status', '=', 'mf_sent'),
            ('x_mf_connote', '=', False),
            ('x_mf_outbound_ref', '!=', False),
        ])

        for picking in pickings:
            if not _phase0_should_target(picking):
                continue
            try:
                self._phase0_process(picking)
            except Exception as exc:
                _logger.error(
                    '_run_mf_tracking_phase0: error for picking %s (outbound_ref %s): %s',
                    picking.name, picking.x_mf_outbound_ref, exc,
                )
                self._send_cron_alert(
                    'stock_3pl_mainfreight',
                    'Phase 0 tracking failed for picking %s (outbound_ref %s)' % (
                        picking.name, picking.x_mf_outbound_ref),
                    str(exc),
                )

    def _phase0_process(self, picking):
        """Query by OutboundReference and write updates for a single picking."""
        warehouse = picking.picking_type_id.warehouse_id
        connector = self.env['3pl.connector'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1
        )
        if not connector:
            _logger.warning(
                '_phase0_process: no connector for warehouse %s (picking %s) — skipping',
                warehouse.name if warehouse else 'unknown', picking.name,
            )
            return

        result = connector.get_transport().get_tracking_by_outbound_ref(
            picking.x_mf_outbound_ref
        )
        if not result or not result.get('connote'):
            return

        connote = result['connote']
        tracking_url = result.get('tracking_url', '')
        status = result.get('status', 'mf_dispatched')

        write_vals = {
            'x_mf_connote': connote,
            'x_mf_status': status,
            'x_mf_dispatched_date': datetime.now(timezone.utc).replace(tzinfo=None),
        }
        if tracking_url:
            write_vals['x_mf_tracking_url'] = tracking_url
        picking.write(write_vals)

        # Post chatter on linked SO
        sale = getattr(picking, 'sale_id', None)
        if sale and tracking_url:
            sale.message_post(
                body='Order dispatched \u2014 Track your delivery: <a href="%s">%s</a>' % (
                    tracking_url, tracking_url)
            )
        elif sale:
            sale.message_post(body='Order dispatched by Mainfreight (connote: %s).' % connote)

    @api.model
    def _run_mf_tracking(self):
        """Main entry point called by the MF tracking ir.cron job.

        1. Finds all in-flight stock.pickings that have a connote number.
        2. For each picking, looks up the connector for that picking's warehouse.
        3. Calls get_tracking_status() on the connector's transport.
        4. Writes updated status fields back to the picking.

        Per-picking errors are caught and logged — one bad picking must not
        block the rest of the batch.
        """
        self._run_mf_tracking_phase0()

        pickings = self.env['stock.picking'].search([
            ('x_mf_status', 'in', list(_TRACKABLE_STATUSES)),
            ('x_mf_connote', '!=', False),
        ])

        for picking in pickings:
            try:
                self._poll_and_update(picking)
            except Exception as exc:
                _logger.error(
                    '_run_mf_tracking: unexpected error for picking %s (connote %s): %s',
                    picking.name, picking.x_mf_connote, exc,
                )
                self._send_cron_alert(
                    'stock_3pl_mainfreight',
                    'Tracking poll failed for picking %s (connote %s)' % (
                        picking.name, picking.x_mf_connote),
                    str(exc),
                )

    def _send_cron_alert(self, module_name: str, subject: str, body: str) -> None:
        """Send an email alert when a scheduled action fails.

        Rate-limited to one alert per hour per module to prevent alert storms.
        Timestamp stored in ir.config_parameter under mml_3pl.last_alert.<module>.
        """
        alert_email = self.env['ir.config_parameter'].sudo().get_param(
            'mml.cron_alert_email', False
        )
        if not alert_email:
            return

        # Rate limiting: suppress if an alert was sent within the cooldown window.
        param_key = 'mml_3pl.last_alert.%s' % module_name
        ICP = self.env['ir.config_parameter'].sudo()
        last_alert_str = ICP.get_param(param_key, '')
        if last_alert_str:
            try:
                last_alert = datetime.fromisoformat(last_alert_str)
                if last_alert.tzinfo is None:
                    last_alert = last_alert.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last_alert).total_seconds()
                if elapsed < _ALERT_COOLDOWN_SECONDS:
                    _logger.debug(
                        '3PL alert suppressed for %s (%.0fs ago, cooldown %ds)',
                        module_name, elapsed, _ALERT_COOLDOWN_SECONDS,
                    )
                    return
            except (ValueError, TypeError):
                pass  # Malformed stored value — send the alert.

        try:
            self.env['mail.mail'].sudo().create({
                'subject': '[MML ALERT] %s: %s' % (module_name, subject),
                'body_html': '<pre>%s</pre>' % html.escape(body),
                'email_to': alert_email,
            }).send()
            # Record timestamp only after a successful send.
            ICP.set_param(param_key, datetime.now(timezone.utc).isoformat())
        except Exception:
            _logger.exception('Failed to send cron alert email for %s', module_name)

    def _poll_and_update(self, picking):
        """Poll the tracking API for a single picking and write updates.

        Finds the connector for the picking's warehouse, calls get_tracking_status(),
        and writes any returned values back to the picking.

        Does nothing if no connector is found or the response is empty.
        """
        warehouse = picking.picking_type_id.warehouse_id
        connector = self.env['3pl.connector'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1
        )
        if not connector:
            _logger.warning(
                '_run_mf_tracking: no connector found for warehouse %s (picking %s) — skipping',
                warehouse.name if warehouse else 'unknown',
                picking.name,
            )
            return

        result = connector.get_transport().get_tracking_status(picking.x_mf_connote)
        if not result:
            return

        write_vals = {}

        new_status = result.get('status')
        if new_status:
            # Validate against known trackable statuses (not terminals — those stop tracking)
            if new_status not in _TRACKABLE_STATUSES and new_status not in _TERMINAL_STATUSES:
                _logger.warning(
                    '_poll_and_update: unknown status %r from tracking API for picking %s — ignoring',
                    new_status, picking.name,
                )
                new_status = None

        if new_status and picking.x_mf_status not in _TERMINAL_STATUSES:
            write_vals['x_mf_status'] = new_status

        # Validate pod_url scheme — only allow https://
        pod_url = result.get('pod_url')
        if pod_url:
            if not isinstance(pod_url, str) or not pod_url.startswith('https://'):
                _logger.warning(
                    '_poll_and_update: rejecting pod_url with unsafe scheme for picking %s: %r',
                    picking.name, pod_url[:100],
                )
                pod_url = None

        if pod_url:
            write_vals['x_mf_pod_url'] = pod_url

        raw_signed = result.get('signed_by')
        if raw_signed:
            raw = str(raw_signed)
            signed_by = re.sub(r'[^\x20-\x7E]', '', raw)[:128]
            if signed_by:
                write_vals['x_mf_signed_by'] = signed_by

        delivered_at = result.get('delivered_at')
        if delivered_at:
            write_vals['x_mf_delivered_date'] = delivered_at

        if write_vals:
            picking.write(write_vals)
