/** @odoo-module **/
// addons/stock_3pl_mainfreight/static/src/js/mf_kpi_dashboard.js

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
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
        });

        onMounted(() => {
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
        // KPI targets are ir.config_parameter values; the connector settings form
        // contains the target configuration fields per the Phase 2 UX design spec.
        this.actionService.doAction("stock_3pl_core.action_3pl_connector");
    }

    openOrderPipeline() {
        this.actionService.doAction("stock_3pl_mainfreight.action_mf_order_pipeline");
    }

    openExceptionQueue() {
        this.actionService.doAction("stock_3pl_mainfreight.action_mf_exceptions");
    }

    openDiscrepancy() {
        this.actionService.doAction("stock_3pl_mainfreight.action_mf_discrepancy");
    }
}

MfKpiDashboard.template = "stock_3pl_mainfreight.MfKpiDashboard";

registry.category("actions").add("mf_kpi_dashboard", MfKpiDashboard);
