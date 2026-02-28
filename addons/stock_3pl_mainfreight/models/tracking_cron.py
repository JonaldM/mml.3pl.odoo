# addons/stock_3pl_mainfreight/models/tracking_cron.py
"""MF tracking cron — polls MF Tracking API and updates x_mf_status on pickings."""
import logging
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


class MFTrackingCron(models.AbstractModel):
    """Cron service model for the Mainfreight tracking poll pipeline.

    AbstractModel — no stored fields, no database table.
    Invoked via ir.cron as: self.env['mf.tracking.cron']._run_mf_tracking()
    """
    _name = 'mf.tracking.cron'
    _description = 'MF Tracking Cron'

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
        if new_status and picking.x_mf_status not in _TERMINAL_STATUSES:
            write_vals['x_mf_status'] = new_status

        pod_url = result.get('pod_url')
        if pod_url:
            write_vals['x_mf_pod_url'] = pod_url

        signed_by = result.get('signed_by')
        if signed_by:
            write_vals['x_mf_signed_by'] = signed_by

        delivered_at = result.get('delivered_at')
        if delivered_at:
            write_vals['x_mf_delivered_date'] = delivered_at

        if write_vals:
            picking.write(write_vals)
