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
    """DIFOT as a percentage. Returns 100.0 if no delivered orders (no denominator)."""
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
            'shrinkage_target': float(ICP.get_param(
                'stock_3pl_mainfreight.kpi_shrinkage_target', '0.5'
            )),
            'difot_amber_offset': float(ICP.get_param(
                'stock_3pl_mainfreight.kpi_difot_amber_offset', '5'
            )),
            'ira_amber_offset': float(ICP.get_param(
                'stock_3pl_mainfreight.kpi_ira_amber_offset', '3'
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
          - difot, ira, exception_rate, shrinkage, in_flight (value + rag)
          - today: sent, received, delivered, exceptions
          - targets: configured targets
          - data_available: False on fresh install (prevents all-green confusion)
        """
        targets = self.get_kpi_targets()
        now = fields.Datetime.now()
        thirty_days_ago = now - timedelta(days=30)

        difot_val = self._compute_difot_value(thirty_days_ago, targets['difot_grace_days'])
        ira_val = self._compute_ira_value(thirty_days_ago, targets['ira_tolerance'])
        exception_rate_val, in_flight = self._compute_exception_and_inflight(thirty_days_ago)
        shrinkage_val = self._compute_shrinkage_value()

        t = targets['exception_rate_target']
        st = targets['shrinkage_target']
        return {
            'difot': {
                'value': difot_val,
                'rag': _rag_status(
                    difot_val,
                    targets['difot_target'],
                    targets['difot_target'] - targets['difot_amber_offset'],
                ),
            },
            'ira': {
                'value': ira_val,
                'rag': _rag_status(
                    ira_val,
                    targets['ira_target'],
                    targets['ira_target'] - targets['ira_amber_offset'],
                ),
            },
            'exception_rate': {
                'value': exception_rate_val,
                'rag': _rag_status_lower_is_better(exception_rate_val, t, t * 2.5),
            },
            'shrinkage': {
                'value': shrinkage_val,
                'rag': _rag_status_lower_is_better(shrinkage_val, st, st * 2),
            },
            'in_flight': {'value': in_flight, 'rag': 'none'},
            'today': self._compute_today_summary(now),
            'targets': targets,
            # Prevents fresh-install all-green confusion
            'data_available': self._compute_data_available(),
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

    def _compute_data_available(self) -> bool:
        """Return True if any MF-tracked pickings exist (prevents all-green on fresh install)."""
        return self.env['stock.picking'].search_count([
            ('x_mf_status', 'not in', [False, 'draft']),
        ]) > 0

    def _compute_difot_value(self, since, grace_days: int) -> float:
        Picking = self.env['stock.picking']
        total = Picking.search_count([
            ('x_mf_status', '=', 'mf_delivered'),
            ('x_mf_delivered_date', '>=', since),
        ])
        if not total:
            return 100.0
        # Count pickings with no deadline (always on time)
        no_deadline = Picking.search_count([
            ('x_mf_status', '=', 'mf_delivered'),
            ('x_mf_delivered_date', '>=', since),
            ('date_deadline', '=', False),
        ])
        # Count pickings delivered on or before their deadline
        # Odoo search can compare two date fields using the ORM domain
        with_deadline_on_time = Picking.search_count([
            ('x_mf_status', '=', 'mf_delivered'),
            ('x_mf_delivered_date', '>=', since),
            ('date_deadline', '!=', False),
            ('x_mf_delivered_date', '<=', 'date_deadline'),
        ])
        return _compute_difot(no_deadline + with_deadline_on_time, total)

    def _compute_ira_value(self, since, tolerance: float) -> float:
        total_skus = self.env['stock.quant'].search_count([
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ])
        if not total_skus:
            return 100.0
        skus_with_discrepancy = self.env['mf.soh.discrepancy'].search_count([
            ('state', '=', 'open'),
            ('detected_date', '>=', since),
            ('variance_pct', '>', tolerance * 100),
        ])
        ira = ((total_skus - skus_with_discrepancy) / total_skus) * 100
        return round(max(ira, 0.0), 2)

    def _compute_exception_and_inflight(self, since):
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

    def _compute_shrinkage_value(self) -> float:
        """Shrinkage % = accepted loss variance (rolling 12M) / total inventory units x 100.

        Only counts 'accepted' discrepancies where MF qty < Odoo qty (stock lost).
        """
        one_year_ago = fields.Datetime.now() - timedelta(days=365)
        accepted = self.env['mf.soh.discrepancy'].search([
            ('state', '=', 'accepted'),
            ('accepted_date', '>=', one_year_ago),
            ('variance_qty', '<', 0),  # loss: MF qty < Odoo qty
        ])
        total_lost = sum(abs(r.variance_qty) for r in accepted)
        quants = self.env['stock.quant'].search([
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ])
        total_stock = sum(q.quantity for q in quants) or 1.0
        return round(total_lost / total_stock * 100, 3)

    def _compute_today_summary(self, now) -> dict:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        Picking = self.env['stock.picking']
        return {
            'sent': Picking.search_count([
                ('x_mf_status', '=', 'mf_sent'), ('write_date', '>=', today_start),
            ]),
            'received': Picking.search_count([
                ('x_mf_status', '=', 'mf_received'), ('write_date', '>=', today_start),
            ]),
            'delivered': Picking.search_count([
                ('x_mf_status', '=', 'mf_delivered'), ('write_date', '>=', today_start),
            ]),
            'exceptions': Picking.search_count([
                ('x_mf_status', '=', 'mf_exception'), ('write_date', '>=', today_start),
            ]),
        }
