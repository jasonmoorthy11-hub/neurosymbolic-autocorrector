import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { ErrorAlert } from "./ErrorAlert";
import type { TrendBucket, AnalyticsSummary } from "../types/api";

interface Props {
  trends: TrendBucket[];
  summary: AnalyticsSummary | null;
  error: string | null;
  loading: boolean;
  onRetry: () => void;
}

function formatCurrency(value: number): string {
  return value >= 1000
    ? `$${(value / 1000).toFixed(1)}k`
    : `$${value.toFixed(0)}`;
}

function formatTime(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function AnalyticsPanel({ trends, summary, error, loading, onRetry }: Props) {
  if (error) {
    return (
      <div className="panel analytics-panel">
        <h2>Analytics &amp; ROI</h2>
        <ErrorAlert message={error} onRetry={onRetry} />
      </div>
    );
  }

  if (loading || !summary) {
    return (
      <div className="panel analytics-panel">
        <h2>Analytics &amp; ROI</h2>
        <div className="analytics-cards">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="analytics-card">
              <span className="skeleton" style={{ width: 60, height: 28 }} />
              <span className="skeleton" style={{ width: 100, height: 14, marginTop: 6 }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="panel analytics-panel" role="region" aria-label="Analytics and ROI dashboard">
      <h2>Analytics &amp; ROI</h2>

      <div className="analytics-cards">
        <div className="analytics-card analytics-card--green">
          <span className="analytics-card-value">{summary.violations_prevented}</span>
          <span className="analytics-card-label">Violations Prevented</span>
        </div>
        <div className="analytics-card analytics-card--green">
          <span className="analytics-card-value">
            {formatCurrency(summary.estimated_savings)}
          </span>
          <span className="analytics-card-label">Estimated Savings</span>
        </div>
        <div className="analytics-card analytics-card--blue">
          <span className="analytics-card-value">
            {summary.avg_latency_ms.toFixed(0)}ms
          </span>
          <span className="analytics-card-label">Avg Latency</span>
        </div>
        <div className="analytics-card analytics-card--blue">
          <span className="analytics-card-value">
            {summary.p95_latency_ms.toFixed(0)}ms
          </span>
          <span className="analytics-card-label">Worst-Case Latency</span>
        </div>
      </div>

      {trends.length > 0 && (
        <div className="analytics-chart" role="img" aria-label="Event trends over time">
          <h3>Event Trends</h3>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={trends}>
              <XAxis
                dataKey="timestamp"
                tickFormatter={formatTime}
                tick={{ fontSize: 11, fill: "#94a3b8" }}
              />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <Tooltip
                contentStyle={{
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelFormatter={(label) => formatTime(String(label))}
              />
              <Legend wrapperStyle={{ color: "#e2e8f0", fontSize: 12 }} />
              <Line
                type="monotone"
                dataKey="corrections"
                name="Corrections"
                stroke="#10b981"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="escalations"
                name="Escalations"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="allowed"
                name="Allowed"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {trends.length === 0 && (
        <p className="muted">No trend data yet. Start a demo to populate.</p>
      )}
    </div>
  );
}
