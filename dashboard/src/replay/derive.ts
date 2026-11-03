/**
 * Pure derivations for the replay dashboard: every number shown in replay mode
 * comes from replay.json through these functions, using the backend's formulas
 * (dashboard/api.py, audit/store.py).
 */
import type {
  AnalyticsSummary,
  CorrectionEvent,
  DashboardMetrics,
  EventFilters,
  RuleInfo,
  SupervisorQueueItem,
  TrendBucket,
} from "../types/api";
import type { ReplayDecision, ReplayFile, ReplayRule, ReplayScenario, Severity } from "../types/replay";

const SEVERITY_RANK: Record<Severity, number> = { low: 0, medium: 1, high: 2, critical: 3 };

function isSeverity(value: string): value is Severity {
  return Object.hasOwn(SEVERITY_RANK, value);
}

function rank(value: string): number {
  return isSeverity(value) ? SEVERITY_RANK[value] : -1;
}

function increment(counts: Record<string, number>, key: string): Record<string, number> {
  return { ...counts, [key]: (counts[key] ?? 0) + 1 };
}

function mean(values: number[]): number {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

export function offsetTimestamp(recordedAt: string, offsetMs: number): string {
  return new Date(Date.parse(recordedAt) + offsetMs).toISOString();
}

export function toCorrectionEvent(s: ReplayScenario, recordedAt: string): CorrectionEvent {
  const escalated = s.action === "escalate";
  return {
    event_id: s.id,
    timestamp: offsetTimestamp(recordedAt, s.t_offset_ms),
    agent_id: s.agent_id,
    session_id: s.id,
    original_text: s.input,
    // An escalation sends nothing: there is no corrected text to show
    corrected_text: s.corrected_text ?? "",
    was_modified: escalated ? false : s.was_modified,
    action: s.action,
    intent_type: s.intent,
    confidence: s.heuristic_confidence,
    violations: s.violations.map((v) => ({
      rule_id: v.rule_id,
      rule_name: v.rule_name,
      description: v.description,
      severity: v.severity,
      details: `${v.source_document}, ${v.source_section}`,
    })),
    pii_detected: s.pii_types.length > 0,
    latency_ms: s.latency_ms,
    severity: s.severity,
  };
}

export function deriveMetrics(events: CorrectionEvent[]): DashboardMetrics {
  const total = events.length;
  const corrections = events.filter((e) => e.action === "correct").length;
  return {
    total_events: total,
    corrections_made: corrections,
    escalations: events.filter((e) => e.action === "escalate").length,
    allowed_through: events.filter((e) => e.action === "allow").length,
    pii_detected: events.filter((e) => e.pii_detected).length,
    correction_rate: total > 0 ? corrections / total : 0,
    avg_latency_ms: mean(events.map((e) => e.latency_ms).filter(Boolean)),
    avg_confidence: mean(events.map((e) => e.confidence).filter(Boolean)),
    violations_by_rule: events
      .flatMap((e) => e.violations)
      .reduce<Record<string, number>>((acc, v) => increment(acc, v.rule_id), {}),
    events_by_intent: events.reduce<Record<string, number>>(
      (acc, e) => increment(acc, e.intent_type),
      {},
    ),
    uptime_seconds: 0,
  };
}

/** Same selection as the backend queue: escalations plus high/critical violations. */
function needsReview(e: CorrectionEvent): boolean {
  return e.action === "escalate" || e.violations.some((v) => rank(v.severity) >= SEVERITY_RANK.high);
}

export function deriveQueue(
  events: CorrectionEvent[],
  decisions: Record<string, ReplayDecision>,
): SupervisorQueueItem[] {
  return events
    .filter(needsReview)
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
    .map((e) => {
      const d = decisions[e.event_id];
      return {
        event_id: e.event_id,
        timestamp: e.timestamp,
        agent_id: e.agent_id,
        original_text: e.original_text,
        action: e.action,
        severity: e.violations.reduce<string>(
          (top, v) => (rank(v.severity) > rank(top) ? v.severity : top),
          "low",
        ),
        violations: e.violations,
        status: d ? d.decision : "pending_review",
        decision: d ? d.decision : null,
        reviewer: d ? "supervisor" : null,
        comment: null,
        rejection_reason: d ? d.rejection_reason : null,
        decided_at: d ? d.decided_at : null,
      };
    });
}

export function deriveRules(rules: ReplayRule[], events: CorrectionEvent[]): RuleInfo[] {
  const counts = events
    .flatMap((e) => e.violations)
    .reduce<Record<string, number>>((acc, v) => increment(acc, v.rule_id), {});
  return rules.map((r) => ({
    rule_id: r.rule_id,
    name: r.name,
    description: r.description,
    severity: r.severity,
    intent_type: r.intent_type,
    violation_count: counts[r.rule_id] ?? 0,
    source_document: r.source_document ?? undefined,
    source_section: r.source_section ?? undefined,
  }));
}

/** Buckets of replay time (the backend buckets wall-clock time by the hour). */
export function deriveTrends(
  events: CorrectionEvent[],
  recordedAt: string,
  bucketMs: number,
): TrendBucket[] {
  const start = Date.parse(recordedAt);
  const buckets = new Map<number, CorrectionEvent[]>();
  for (const e of events) {
    const key = Math.floor((Date.parse(e.timestamp) - start) / bucketMs) * bucketMs;
    buckets.set(key, [...(buckets.get(key) ?? []), e]);
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a - b)
    .map(([key, group]) => ({
      timestamp: offsetTimestamp(recordedAt, key),
      total: group.length,
      corrections: group.filter((e) => e.action === "correct").length,
      escalations: group.filter((e) => e.action === "escalate").length,
      allowed: group.filter((e) => e.action === "allow").length,
      avg_latency_ms: round2(mean(group.map((e) => e.latency_ms))),
    }));
}

function topCounts(counts: Record<string, number>, limit: number): Array<[string, number]> {
  return Object.entries(counts)
    .sort(([, a], [, b]) => b - a)
    .slice(0, limit);
}

export function deriveSummary(
  events: CorrectionEvent[],
  roi: ReplayFile["roi_assumptions"],
): AnalyticsSummary {
  const metrics = deriveMetrics(events);
  const latencies = events.map((e) => e.latency_ms).sort((a, b) => a - b);
  const n = latencies.length;
  const timestamps = events.map((e) => e.timestamp).sort();
  return {
    total_events: metrics.total_events,
    corrections: metrics.corrections_made,
    escalations: metrics.escalations,
    allowed: metrics.allowed_through,
    unique_agents: new Set(events.map((e) => e.agent_id)).size,
    unique_sessions: new Set(events.map((e) => e.session_id)).size,
    top_rules: topCounts(metrics.violations_by_rule, 5).map(([rule_id, count]) => ({ rule_id, count })),
    top_intents: topCounts(metrics.events_by_intent, 5).map(([intent, count]) => ({ intent, count })),
    avg_latency_ms: round2(mean(latencies)),
    p50_latency_ms: n ? round2(latencies[Math.floor(n / 2)]) : 0,
    p95_latency_ms: n ? round2(latencies[Math.min(Math.floor(n * 0.95), n - 1)]) : 0,
    time_range: { first: timestamps[0] ?? null, last: timestamps[n - 1] ?? null },
    violations_prevented: metrics.corrections_made,
    estimated_savings: round2(
      metrics.corrections_made * roi.cost_per_violation + metrics.escalations * roi.cost_per_escalation,
    ),
    cost_per_violation: roi.cost_per_violation,
    cost_per_escalation: roi.cost_per_escalation,
  };
}

/**
 * The backend's export filters, except dates: these compare whole days, so
 * date_to includes that day (the backend's string compare excludes it).
 */
export function filterEvents(events: CorrectionEvent[], f: EventFilters): CorrectionEvent[] {
  return events.filter((e) => {
    const day = e.timestamp.slice(0, 10);
    return (
      (!f.date_from || day >= f.date_from) &&
      (!f.date_to || day <= f.date_to) &&
      (!f.intent_type || e.intent_type === f.intent_type) &&
      (!f.action || e.action === f.action) &&
      (!f.severity || e.violations.some((v) => v.severity === f.severity)) &&
      (!f.agent_id || e.agent_id === f.agent_id)
    );
  });
}

const CSV_COLUMNS = [
  "event_id",
  "timestamp",
  "agent_id",
  "session_id",
  "original_text",
  "corrected_text",
  "was_modified",
  "action_taken",
  "intent_type",
  "confidence",
  "latency_ms",
];

function csvField(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Same columns as GET /api/events/export?format=csv. */
export function toCsv(events: CorrectionEvent[]): string {
  const rows = events.map((e) =>
    [
      e.event_id,
      e.timestamp,
      e.agent_id,
      e.session_id,
      e.original_text,
      e.corrected_text,
      e.was_modified ? "True" : "False",
      e.action,
      e.intent_type,
      String(e.confidence),
      String(e.latency_ms),
    ]
      .map(csvField)
      .join(","),
  );
  return [CSV_COLUMNS.join(","), ...rows].join("\r\n") + "\r\n";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

const ACTIONS: ReadonlySet<unknown> = new Set(["allow", "correct", "escalate"]);
const TIERS: ReadonlySet<unknown> = new Set(["neo4j", "llm", "span", "template"]);

function hasStrings(value: unknown, keys: string[]): value is Record<string, unknown> {
  return isRecord(value) && keys.every((k) => typeof value[k] === "string");
}

function isArrayOf(value: unknown, check: (item: unknown) => boolean): boolean {
  return Array.isArray(value) && value.every(check);
}

function isScenario(s: unknown): boolean {
  return (
    isRecord(s) &&
    ["id", "title", "agent_id", "input", "intent", "detected_language"].every(
      (k) => typeof s[k] === "string",
    ) &&
    ["t_offset_ms", "latency_ms", "heuristic_confidence", "intent_confidence"].every(
      (k) => typeof s[k] === "number",
    ) &&
    isArrayOf(
      s.violations,
      (v) =>
        hasStrings(v, ["rule_id", "rule_name", "description", "source_document", "source_section"]) &&
        isSeverity(String(v.severity)),
    ) &&
    isArrayOf(
      s.edits,
      (e) =>
        hasStrings(e, ["rule_id", "before", "after", "source_document", "source_section"]) &&
        TIERS.has(e.tier),
    ) &&
    isArrayOf(s.fact_errors, (f) => hasStrings(f, ["claim", "correct_value", "source_document"])) &&
    isArrayOf(s.pii_types, (t) => typeof t === "string") &&
    ACTIONS.has(s.action) &&
    isSeverity(String(s.severity)) &&
    (s.corrected_text === null || typeof s.corrected_text === "string") &&
    isRecord(s.customer_context)
  );
}

/** Shape check for the fetched file (schema v1 written by scripts/record_replay.py). */
export function isReplayFile(value: unknown): value is ReplayFile {
  return (
    isRecord(value) &&
    value.schema_version === 1 &&
    typeof value.recorded_at === "string" &&
    !Number.isNaN(Date.parse(value.recorded_at)) &&
    Array.isArray(value.scenarios) &&
    value.scenarios.every(isScenario) &&
    Array.isArray(value.rules) &&
    value.rules.every((r) => isRecord(r) && typeof r.rule_id === "string") &&
    isRecord(value.roi_assumptions) &&
    typeof value.roi_assumptions.cost_per_violation === "number" &&
    typeof value.roi_assumptions.cost_per_escalation === "number"
  );
}
