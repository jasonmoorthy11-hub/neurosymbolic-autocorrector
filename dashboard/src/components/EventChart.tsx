import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
  LabelList,
} from "recharts";
import { AXIS_TICK, LEGEND_STYLE, TOOLTIP_ITEM_STYLE, TOOLTIP_STYLE, legendText, pieLabel } from "./chartStyle";
import { ACTION_FILL, VIOLATION_FILL, intentFill } from "./chartPalette";
import type { DashboardMetrics } from "../types/api";
import "./EventChart.css";

interface Props {
  metrics: DashboardMetrics | null;
}

const RULE_NAMES: Record<string, string> = {
  "CREDIT-001": "Credit Limit",
  "CREDIT-002": "High Credit",
  "REFUND-001": "Refund Window",
  "REFUND-002": "Refund Amount",
  "ESC-001": "Escalation",
  "PROMISE-001": "Promises",
  "SLA-001": "SLA",
  "DISC-001": "Disclosure",
};

export function EventChart({ metrics }: Props) {
  if (!metrics || metrics.total_events === 0) {
    return (
      <div className="panel chart-panel">
        <h2>Event Distribution</h2>
        <p className="muted">Waiting for events...</p>
      </div>
    );
  }

  const actionData: { name: keyof typeof ACTION_FILL; value: number }[] = [
    { name: "Corrections", value: metrics.corrections_made },
    { name: "Escalations", value: metrics.escalations },
    { name: "Allowed", value: metrics.allowed_through },
  ];

  const intentData = Object.entries(metrics.events_by_intent).map(
    ([name, value]) => ({ name, value })
  );

  const ruleData = Object.entries(metrics.violations_by_rule)
    .map(([id, value]) => ({ name: RULE_NAMES[id] || id, value }))
    .sort((a, b) => b.value - a.value);

  const actionSummary = actionData.map((d) => `${d.name}: ${d.value}`).join(", ");

  return (
    <div className="panel chart-panel" role="region" aria-label="Event distribution charts">
      <h2>Event Distribution</h2>
      <div className="chart-grid">
        <div className="chart-section" role="img" aria-label={`Action distribution: ${actionSummary}`}>
          <h3>Actions</h3>
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie
                data={actionData}
                cx="50%"
                cy="45%"
                innerRadius={40}
                outerRadius={70}
                dataKey="value"
                label={pieLabel(true)}
              >
                {actionData.map((entry) => (
                  <Cell key={entry.name} fill={ACTION_FILL[entry.name]} />
                ))}
              </Pie>
              <Tooltip contentStyle={TOOLTIP_STYLE} itemStyle={TOOLTIP_ITEM_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} formatter={legendText} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {ruleData.length > 0 && (
          <div className="chart-section" role="img" aria-label={`Violations by rule: ${ruleData.map((d) => `${d.name}: ${d.value}`).join(", ")}`}>
            <h3>Violations by Rule</h3>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={ruleData}>
                <XAxis dataKey="name" tick={{ ...AXIS_TICK, fontSize: 12 }} height={40} />
                <YAxis allowDecimals={false} tick={AXIS_TICK} />
                <Tooltip contentStyle={TOOLTIP_STYLE} itemStyle={TOOLTIP_ITEM_STYLE} />
                <Legend wrapperStyle={LEGEND_STYLE} formatter={legendText} />
                <Bar dataKey="value" fill={VIOLATION_FILL} name="Violations" radius={[4, 4, 0, 0]}>
                  <LabelList dataKey="value" position="top" fill="var(--text)" fontSize={12} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {intentData.length > 0 && (
          <div className="chart-section" role="img" aria-label={`Events by intent: ${intentData.map((d) => `${d.name}: ${d.value}`).join(", ")}`}>
            <h3>Events by Intent</h3>
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={intentData}
                  cx="50%"
                  cy="45%"
                  outerRadius={70}
                  dataKey="value"
                  label={pieLabel(false)}
                >
                  {intentData.map((entry) => (
                    <Cell key={entry.name} fill={intentFill(entry.name)} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TOOLTIP_STYLE} itemStyle={TOOLTIP_ITEM_STYLE} />
                <Legend wrapperStyle={LEGEND_STYLE} formatter={legendText} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
