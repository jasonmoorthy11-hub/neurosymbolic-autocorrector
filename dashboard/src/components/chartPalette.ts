// Chart fills are theme tokens from src/theme.css (--chart-*), stepped per theme
// and validated with the dataviz palette checks for light (#ffffff) and dark
// (#1e293b) surfaces. Recharts passes fill/stroke strings through to SVG, so
// `var(--chart-…)` resolves against the active theme at paint time.

export const ACTION_FILL = {
  Corrections: "var(--chart-correct)",
  Escalations: "var(--chart-escalate)",
  Allowed: "var(--chart-allow)",
} as const;

export const VIOLATION_FILL = "var(--chart-violation)";

/** Stat-card accents that are not an action or a violation: deliberately the same hues as pie slots 1, 3 and 7. */
export const STAT_FILL = {
  total: "var(--chart-series-1)",
  rate: "var(--chart-series-3)",
  latency: "var(--chart-series-7)",
} as const;

/**
 * Fixed slot per intent type: colour follows the entity, never its position in
 * the data. Slots 1-6 follow the order the intents hold in the recorded replay's
 * metrics map at 25/25 (events newest-first), so the demo pie's neighbouring
 * slices are the validated adjacent pairs; the closing pair (slot 6 -> slot 1,
 * lime -> blue) was validated separately. chartPalette.test.ts pins this.
 * The palette has eight categorical slots (the dataviz cap); `cancellation`,
 * the ninth IntentType, deliberately folds into the neutral "other" slot
 * together with anything unmapped.
 */
export const INTENT_SLOTS: Record<string, number> = {
  general: 1,
  financial_compensation: 2,
  policy_explanation: 3,
  troubleshooting: 4,
  product_inquiry: 5,
  complaint: 6,
  refund_processing: 7,
  escalation_request: 8,
};

/** Token for an intent name; anything outside the eight slots folds into "other". */
export function intentFill(intent: string): string {
  const slot = INTENT_SLOTS[intent];
  return slot === undefined ? "var(--chart-series-other)" : `var(--chart-series-${slot})`;
}
