"""
One-time seed script: create a CRO Metrics Dashboard app for the demo org
and set it as the organization's home app.

Usage:
    cd backend && python -m scripts.seed_cro_metrics_app
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

# Ensure the backend package root is on sys.path so config/models resolve
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from models.database import get_admin_session
from models.organization import Organization
from models.app import App

DEMO_ORG_ID = "3ac0ef74-d82a-45b3-86d4-d510815689fa"  # Cro Metrics

# The first user in the org will be used as the owner
QUERIES: dict[str, dict[str, object]] = {
    "pipeline_summary": {
        "sql": """
SELECT
  COUNT(*)::int                                        AS total_deals,
  COALESCE(SUM(d.amount), 0)::float                    AS total_value,
  COALESCE(AVG(d.amount), 0)::float                    AS avg_deal_size,
  COALESCE(SUM(d.amount * COALESCE(d.probability, 50) / 100.0), 0)::float AS weighted_value
FROM deals d
LEFT JOIN pipeline_stages ps
  ON ps.pipeline_id = d.pipeline_id AND ps.name = d.stage
WHERE d.organization_id = :org_id
  AND (ps.id IS NULL OR (ps.is_closed_won = false AND ps.is_closed_lost = false))
  AND (
    :period = 'all'
    OR (:period = '30d'     AND d.created_date >= NOW() - INTERVAL '30 days')
    OR (:period = 'quarter' AND d.created_date >= DATE_TRUNC('quarter', NOW()))
    OR (:period = 'ytd'     AND d.created_date >= DATE_TRUNC('year', NOW()))
  )
""",
        "params": {"period": "all"},
    },
    "deals_by_stage": {
        "sql": """
SELECT
  COALESCE(d.stage, 'Unknown') AS stage,
  COUNT(*)::int                AS deal_count,
  COALESCE(SUM(d.amount), 0)::float AS total_value
FROM deals d
LEFT JOIN pipeline_stages ps
  ON ps.pipeline_id = d.pipeline_id AND ps.name = d.stage
WHERE d.organization_id = :org_id
  AND (ps.id IS NULL OR (ps.is_closed_won = false AND ps.is_closed_lost = false))
  AND (
    :period = 'all'
    OR (:period = '30d'     AND d.created_date >= NOW() - INTERVAL '30 days')
    OR (:period = 'quarter' AND d.created_date >= DATE_TRUNC('quarter', NOW()))
    OR (:period = 'ytd'     AND d.created_date >= DATE_TRUNC('year', NOW()))
  )
GROUP BY d.stage
ORDER BY total_value DESC
""",
        "params": {"period": "all"},
    },
    "win_rate": {
        "sql": """
SELECT
  COUNT(*) FILTER (WHERE ps.is_closed_won)::int  AS won,
  COUNT(*) FILTER (WHERE ps.is_closed_lost)::int AS lost,
  CASE
    WHEN COUNT(*) FILTER (WHERE ps.is_closed_won OR ps.is_closed_lost) = 0 THEN 0
    ELSE ROUND(
      100.0 * COUNT(*) FILTER (WHERE ps.is_closed_won)
            / COUNT(*) FILTER (WHERE ps.is_closed_won OR ps.is_closed_lost), 1
    )::float
  END AS win_rate_pct
FROM deals d
JOIN pipeline_stages ps
  ON ps.pipeline_id = d.pipeline_id AND ps.name = d.stage
WHERE d.organization_id = :org_id
  AND (ps.is_closed_won OR ps.is_closed_lost)
  AND (
    :period = 'all'
    OR (:period = '30d'     AND d.close_date >= CURRENT_DATE - 30)
    OR (:period = 'quarter' AND d.close_date >= DATE_TRUNC('quarter', CURRENT_DATE))
    OR (:period = 'ytd'     AND d.close_date >= DATE_TRUNC('year', CURRENT_DATE))
  )
""",
        "params": {"period": "all"},
    },
    "upcoming_closes": {
        "sql": """
SELECT
  d.name,
  COALESCE(d.amount, 0)::float AS amount,
  d.stage,
  d.close_date::text AS close_date
FROM deals d
LEFT JOIN pipeline_stages ps
  ON ps.pipeline_id = d.pipeline_id AND ps.name = d.stage
WHERE d.organization_id = :org_id
  AND d.close_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 30
  AND (ps.id IS NULL OR (ps.is_closed_won = false AND ps.is_closed_lost = false))
ORDER BY d.close_date ASC
LIMIT 20
""",
        "params": {},
    },
}

FRONTEND_CODE = r'''
import React, { useState, useMemo } from "react";
import { useAppQuery, useDateRange, Spinner, ErrorBanner } from "@revtops/app-sdk";
import Plot from "react-plotly.js";

const PERIODS = [
  { value: "30d", label: "Last 30 days" },
  { value: "quarter", label: "This Quarter" },
  { value: "ytd", label: "Year to Date" },
  { value: "all", label: "All Time" },
];

function KPICard({ label, value, prefix = "", suffix = "" }) {
  return (
    <div style={{
      background: "rgba(255,255,255,0.04)",
      border: "1px solid rgba(255,255,255,0.1)",
      borderRadius: 12,
      padding: "20px 24px",
      flex: "1 1 0",
      minWidth: 160,
    }}>
      <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </div>
      <div style={{ fontSize: 28, fontWeight: 600, color: "#f1f5f9", fontVariantNumeric: "tabular-nums" }}>
        {prefix}{typeof value === "number" ? value.toLocaleString("en-US", { maximumFractionDigits: 1 }) : value}{suffix}
      </div>
    </div>
  );
}

export default function CROMetrics() {
  const [period, setPeriod] = useState("all");

  const summary = useAppQuery("pipeline_summary", { period });
  const stages = useAppQuery("deals_by_stage", { period });
  const winRate = useAppQuery("win_rate", { period });
  const upcoming = useAppQuery("upcoming_closes", {});

  const isLoading = summary.loading || stages.loading || winRate.loading || upcoming.loading;
  const anyError = summary.error || stages.error || winRate.error || upcoming.error;

  const summaryRow = summary.data?.[0] ?? {};
  const winRow = winRate.data?.[0] ?? {};

  const stageNames = (stages.data ?? []).map((r) => r.stage);
  const stageCounts = (stages.data ?? []).map((r) => r.deal_count);
  const stageValues = (stages.data ?? []).map((r) => r.total_value);

  const fmt = (n) =>
    n >= 1_000_000
      ? `$${(n / 1_000_000).toFixed(1)}M`
      : n >= 1_000
      ? `$${(n / 1_000).toFixed(0)}K`
      : `$${Math.round(n)}`;

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1200, margin: "0 auto", fontFamily: "system-ui, sans-serif" }}>
      {/* Period selector */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "#f1f5f9" }}>CRO Metrics Dashboard</h1>
        <div style={{ display: "flex", gap: 6 }}>
          {PERIODS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              style={{
                padding: "6px 14px",
                borderRadius: 8,
                border: period === p.value ? "1px solid #6366f1" : "1px solid rgba(255,255,255,0.12)",
                background: period === p.value ? "rgba(99,102,241,0.15)" : "transparent",
                color: period === p.value ? "#a5b4fc" : "#94a3b8",
                cursor: "pointer",
                fontSize: 13,
                fontWeight: 500,
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {anyError && <ErrorBanner message={anyError} />}

      {isLoading ? (
        <Spinner />
      ) : (
        <>
          {/* KPI cards */}
          <div style={{ display: "flex", gap: 16, marginBottom: 28, flexWrap: "wrap" }}>
            <KPICard label="Total Pipeline" value={fmt(summaryRow.total_value ?? 0)} />
            <KPICard label="Weighted Pipeline" value={fmt(summaryRow.weighted_value ?? 0)} />
            <KPICard label="Avg Deal Size" value={fmt(summaryRow.avg_deal_size ?? 0)} />
            <KPICard label="Win Rate" value={winRow.win_rate_pct ?? 0} suffix="%" />
          </div>

          {/* Deals by stage chart */}
          <div style={{
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 12,
            padding: 24,
            marginBottom: 28,
          }}>
            <h2 style={{ margin: "0 0 16px", fontSize: 15, fontWeight: 600, color: "#e2e8f0" }}>
              Deals by Stage
            </h2>
            <Plot
              data={[
                {
                  type: "bar",
                  x: stageNames,
                  y: stageValues,
                  text: stageValues.map((v) => fmt(v)),
                  textposition: "auto",
                  marker: { color: "#6366f1" },
                  hovertemplate: "%{x}<br>%{y:$,.0f}<br>%{customdata} deals<extra></extra>",
                  customdata: stageCounts,
                },
              ]}
              layout={{
                paper_bgcolor: "transparent",
                plot_bgcolor: "transparent",
                font: { color: "#94a3b8", size: 12 },
                xaxis: { tickangle: -30, gridcolor: "rgba(255,255,255,0.06)" },
                yaxis: { gridcolor: "rgba(255,255,255,0.06)", tickprefix: "$" },
                margin: { l: 60, r: 20, t: 10, b: 80 },
                height: 320,
                bargap: 0.3,
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          {/* Upcoming closes table */}
          <div style={{
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 12,
            overflow: "hidden",
          }}>
            <div style={{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
              <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "#e2e8f0" }}>
                Upcoming Closes (Next 30 days)
              </h2>
            </div>
            {(upcoming.data ?? []).length === 0 ? (
              <div style={{ padding: 32, textAlign: "center", color: "#64748b", fontSize: 14 }}>
                No deals closing in the next 30 days
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                    <th style={{ textAlign: "left", padding: "10px 24px", fontSize: 11, fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>Deal</th>
                    <th style={{ textAlign: "left", padding: "10px 16px", fontSize: 11, fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>Stage</th>
                    <th style={{ textAlign: "right", padding: "10px 16px", fontSize: 11, fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>Amount</th>
                    <th style={{ textAlign: "right", padding: "10px 24px", fontSize: 11, fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>Close Date</th>
                  </tr>
                </thead>
                <tbody>
                  {(upcoming.data ?? []).map((row, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                      <td style={{ padding: "10px 24px", color: "#e2e8f0", fontSize: 14 }}>{row.name}</td>
                      <td style={{ padding: "10px 16px" }}>
                        <span style={{
                          display: "inline-block",
                          padding: "2px 8px",
                          borderRadius: 4,
                          background: "rgba(99,102,241,0.15)",
                          color: "#a5b4fc",
                          fontSize: 12,
                          fontWeight: 500,
                        }}>
                          {row.stage ?? "—"}
                        </span>
                      </td>
                      <td style={{ padding: "10px 16px", textAlign: "right", color: "#cbd5e1", fontVariantNumeric: "tabular-nums", fontSize: 14 }}>
                        ${row.amount.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                      </td>
                      <td style={{ padding: "10px 24px", textAlign: "right", color: "#94a3b8", fontSize: 13 }}>{row.close_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
'''


async def main() -> None:
    """Create the CRO Metrics app and set it as home for the demo org."""
    app_id: uuid.UUID = uuid.uuid4()

    async with get_admin_session() as session:
        # Find a user in the demo org to use as owner
        result = await session.execute(
            text("SELECT id FROM users WHERE organization_id = :org_id LIMIT 1"),
            {"org_id": DEMO_ORG_ID},
        )
        row = result.first()
        if row is None:
            print(f"No users found for org {DEMO_ORG_ID}")
            return
        owner_id: uuid.UUID = row[0]

        # Check if app already exists
        existing = await session.execute(
            text("SELECT id FROM apps WHERE organization_id = :org_id AND title = :title LIMIT 1"),
            {"org_id": DEMO_ORG_ID, "title": "CRO Metrics Dashboard"},
        )
        if existing.first() is not None:
            print("CRO Metrics Dashboard app already exists, skipping insert.")
            existing_row = await session.execute(
                text("SELECT id FROM apps WHERE organization_id = :org_id AND title = :title LIMIT 1"),
                {"org_id": DEMO_ORG_ID, "title": "CRO Metrics Dashboard"},
            )
            app_id = existing_row.first()[0]  # type: ignore[index]
        else:
            app = App(
                id=app_id,
                user_id=owner_id,
                organization_id=DEMO_ORG_ID,
                title="CRO Metrics Dashboard",
                description="Pipeline overview with KPIs, deals by stage, win rate, and upcoming closes.",
                queries=QUERIES,
                frontend_code=FRONTEND_CODE,
            )
            session.add(app)
            await session.flush()
            print(f"Created CRO Metrics Dashboard app: {app_id}")

        # Set as home app
        await session.execute(
            text("UPDATE organizations SET home_app_id = :app_id WHERE id = :org_id"),
            {"app_id": str(app_id), "org_id": DEMO_ORG_ID},
        )
        await session.commit()
        print(f"Set home_app_id = {app_id} for org {DEMO_ORG_ID}")


if __name__ == "__main__":
    asyncio.run(main())
