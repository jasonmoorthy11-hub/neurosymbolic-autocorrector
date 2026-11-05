import type { PieLabelRenderProps } from "recharts";

// Chart text wears the theme's text tokens (src/theme.css), never the series colour.
export const AXIS_TICK = { fontSize: 11, fill: "var(--text-muted)" };
export const LEGEND_STYLE = { color: "var(--text)", fontSize: 12 };
export const TOOLTIP_STYLE = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--text)",
  fontSize: 12,
};

// Tooltip rows default to the series colour; keep them in text ink (series identity is in the legend).
export const TOOLTIP_ITEM_STYLE = { color: "var(--text)" };

/** Legend entry text in text ink (Recharts colours it with the series by default). */
export function legendText(value: string) {
  return <span style={{ color: "var(--text)" }}>{value}</span>;
}

/** Pie slice label in text ink; with `withName` false only the percentage (the legend names it). */
export function pieLabel(withName: boolean) {
  return function PieLabel({ x, y, textAnchor, name, percent }: PieLabelRenderProps) {
    const pct = `${((percent ?? 0) * 100).toFixed(0)}%`;
    return (
      <text x={x} y={y} textAnchor={textAnchor} dominantBaseline="central" fill="var(--text)" fontSize={12}>
        {withName ? `${name ?? ""}: ${pct}` : pct}
      </text>
    );
  };
}
