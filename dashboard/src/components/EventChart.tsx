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
import type { PieLabelRenderProps } from "recharts";
import type { DashboardMetrics } from "../types/api";

interface Props {
  metrics: DashboardMetrics | null;
}

const ACTION_COLORS = {
  Corrections: "#10b981",
  Escalations: "#f59e0b",
  Allowed: "#3b82f6",
};

const INTENT_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
];

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

const renderPercentLabel = ({ name, percent }: PieLabelRenderProps) =>
  `${name ?? ""}: ${((percent ?? 0) * 100).toFixed(0)}%`;

export function EventChart({ metrics }: Props) {
  if (!metrics || metrics.total_events === 0) {
    return (
      <div className="panel chart-panel">
        <h2>Event Distribution</h2>
        <p className="muted">Waiting for events...</p>
      </div>
    );
  }

  const actionData = [
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
                label={renderPercentLabel}
              >
                {actionData.map((entry) => (
                  <Cell
                    key={entry.name}
                    fill={ACTION_COLORS[entry.name as keyof typeof ACTION_COLORS]}
                  />
                ))}
              </Pie>
              <Tooltip />
              <Legend wrapperStyle={{ color: "#e2e8f0", fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {ruleData.length > 0 && (
          <div className="chart-section" role="img" aria-label={`Violations by rule: ${ruleData.map((d) => `${d.name}: ${d.value}`).join(", ")}`}>
            <h3>Violations by Rule</h3>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={ruleData}>
                <XAxis dataKey="name" tick={{ fontSize: 12 }} height={40} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Legend wrapperStyle={{ color: "#e2e8f0", fontSize: 12 }} />
                <Bar dataKey="value" fill="#ef4444" name="Violations" radius={[4, 4, 0, 0]}>
                  <LabelList dataKey="value" position="top" fill="#e2e8f0" fontSize={12} />
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
                  label={renderPercentLabel}
                >
                  {intentData.map((_, i) => (
                    <Cell
                      key={i}
                      fill={INTENT_COLORS[i % INTENT_COLORS.length]}
                    />
                  ))}
                </Pie>
                <Tooltip />
                <Legend wrapperStyle={{ color: "#e2e8f0", fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
