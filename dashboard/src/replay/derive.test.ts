import { describe, expect, it } from "vitest";
import recorded from "../../public/replay/replay.json";
import type { ReplayFile, ReplayScenario } from "../types/replay";
import {
  deriveMetrics,
  deriveQueue,
  deriveRules,
  deriveSummary,
  deriveTrends,
  filterEvents,
  isReplayFile,
  toCorrectionEvent,
  toCsv,
} from "./derive";

const RECORDED_AT = "2026-11-03T12:00:00Z";

function scenario(overrides: Partial<ReplayScenario>): ReplayScenario {
  return {
    id: "S00",
    title: "t",
    agent_id: "agent-mike",
    input: "Hello.",
    customer_context: {},
    detected_language: "en",
    intent: "general",
    intent_confidence: 0.5,
    pii_types: [],
    violations: [],
    fact_errors: [],
    edits: [],
    action: "allow",
    was_modified: false,
    corrected_text: "Hello.",
    severity: "low",
    heuristic_confidence: 1,
    guardrails_applied: [],
    explanation: null,
    latency_ms: 10,
    t_offset_ms: 0,
    ...overrides,
  };
}

const violation = (rule_id: string, severity: "medium" | "high" | "critical") => ({
  rule_id,
  rule_name: rule_id,
  description: `${rule_id} desc`,
  severity,
  source_document: "Doc",
  source_section: "Section 1",
});

const ALLOW = scenario({ id: "S01", latency_ms: 10, t_offset_ms: 0 });
const CORRECT = scenario({
  id: "S02",
  agent_id: "agent-sarah",
  input: "I promise a $200 credit. It's permanent.",
  intent: "financial_compensation",
  violations: [violation("CREDIT-001", "high"), violation("PROMISE-001", "high")],
  edits: [
    {
      rule_id: "CREDIT-001",
      before: "I promise a $200 credit.",
      after: "I can offer you a $50 credit.",
      start: 0,
      end: 24,
      source_document: "Credit",
      source_section: "Section 4.1",
      tier: "span",
    },
    {
      rule_id: "PROMISE-001",
      before: "It's permanent.",
      after: "",
      start: 25,
      end: 40,
      source_document: "Conduct",
      source_section: "Section 2.3",
      tier: "span",
    },
  ],
  action: "correct",
  was_modified: true,
  corrected_text: "I can offer you a $50 credit.",
  severity: "high",
  heuristic_confidence: 0.92,
  latency_ms: 20,
  t_offset_ms: 2500,
});
const ESCALATE = scenario({
  id: "S03",
  agent_id: "agent-james",
  input: "I'll apply a $1,000 credit.",
  intent: "financial_compensation",
  violations: [violation("CREDIT-001", "high"), violation("CREDIT-002", "critical")],
  action: "escalate",
  was_modified: true,
  corrected_text: null,
  severity: "critical",
  heuristic_confidence: 0.92,
  latency_ms: 30,
  t_offset_ms: 5000,
});
const PII = scenario({
  id: "S04",
  input: "Your SSN 123-45-6789, sir.",
  pii_types: ["ssn"],
  action: "correct",
  was_modified: true,
  corrected_text: "Your SSN [SSN REDACTED], sir.",
  heuristic_confidence: 0.92,
  latency_ms: 40,
  t_offset_ms: 12500,
});
const FIXTURE = [ALLOW, CORRECT, ESCALATE, PII];
const EVENTS = FIXTURE.map((s) => toCorrectionEvent(s, RECORDED_AT));

describe("toCorrectionEvent", () => {
  it("maps a scenario onto the live event shape", () => {
    const e = toCorrectionEvent(CORRECT, RECORDED_AT);
    expect(e.event_id).toBe("S02");
    expect(e.timestamp).toBe("2026-11-03T12:00:02.500Z");
    expect(e.session_id).toBe("S02");
    expect(e.action).toBe("correct");
    expect(e.confidence).toBe(0.92);
    expect(e.corrected_text).toBe("I can offer you a $50 credit.");
    expect(e.severity).toBe("high");
    expect(e.pii_detected).toBe(false);
    expect(e.violations[0].details).toBe("Doc, Section 1");
  });

  it("sends no text for an escalation", () => {
    const e = toCorrectionEvent(ESCALATE, RECORDED_AT);
    expect(e.corrected_text).toBe("");
    expect(e.was_modified).toBe(false);
  });

  it("flags PII from the recorded types", () => {
    expect(toCorrectionEvent(PII, RECORDED_AT).pii_detected).toBe(true);
  });
});

describe("deriveMetrics", () => {
  it("counts actions, PII, rules and intents", () => {
    const m = deriveMetrics(EVENTS);
    expect(m.total_events).toBe(4);
    expect(m.corrections_made).toBe(2);
    expect(m.escalations).toBe(1);
    expect(m.allowed_through).toBe(1);
    expect(m.pii_detected).toBe(1);
    expect(m.correction_rate).toBe(0.5);
    expect(m.avg_latency_ms).toBe(25);
    expect(m.avg_confidence).toBeCloseTo((1 + 0.92 * 3) / 4, 10);
    expect(m.violations_by_rule).toEqual({ "CREDIT-001": 2, "PROMISE-001": 1, "CREDIT-002": 1 });
    expect(m.events_by_intent).toEqual({ general: 2, financial_compensation: 2 });
  });

  it("is all zeros before any event", () => {
    const m = deriveMetrics([]);
    expect(m.total_events).toBe(0);
    expect(m.correction_rate).toBe(0);
    expect(m.avg_latency_ms).toBe(0);
  });
});

describe("deriveQueue", () => {
  it("holds escalations and high/critical corrections, newest first", () => {
    const q = deriveQueue(EVENTS, {});
    expect(q.map((i) => i.event_id)).toEqual(["S03", "S02"]);
    expect(q[0].severity).toBe("critical");
    expect(q[1].severity).toBe("high");
    expect(q[0].status).toBe("pending_review");
    expect(q[0].decision).toBeNull();
  });

  it("applies local decisions", () => {
    const q = deriveQueue(EVENTS, {
      S03: { decision: "rejected", rejection_reason: "over_corrected", decided_at: RECORDED_AT },
    });
    expect(q[0].status).toBe("rejected");
    expect(q[0].decision).toBe("rejected");
    expect(q[0].rejection_reason).toBe("over_corrected");
    expect(q[0].reviewer).toBe("supervisor");
    expect(q[1].decision).toBeNull();
  });
});

describe("deriveRules", () => {
  it("lists every rule with the violations seen so far", () => {
    const rules = deriveRules(
      [
        { rule_id: "CREDIT-001", name: "Credit", description: "d", severity: "high", intent_type: "financial_compensation", source_document: "Credit", source_section: "Section 4.1" },
        { rule_id: "ESC-001", name: "Esc", description: "d", severity: "critical", intent_type: "escalation_request", source_document: null, source_section: null },
      ],
      EVENTS,
    );
    expect(rules.map((r) => [r.rule_id, r.violation_count])).toEqual([
      ["CREDIT-001", 2],
      ["ESC-001", 0],
    ]);
    expect(rules[1].source_document).toBeUndefined();
  });
});

describe("deriveTrends", () => {
  it("buckets events by replay time", () => {
    const t = deriveTrends(EVENTS, RECORDED_AT, 10_000);
    expect(t).toEqual([
      { timestamp: "2026-11-03T12:00:00.000Z", total: 3, corrections: 1, escalations: 1, allowed: 1, avg_latency_ms: 20 },
      { timestamp: "2026-11-03T12:00:10.000Z", total: 1, corrections: 1, escalations: 0, allowed: 0, avg_latency_ms: 40 },
    ]);
  });
});

describe("deriveSummary", () => {
  it("computes percentiles and illustrative savings like the backend", () => {
    const s = deriveSummary(EVENTS, { cost_per_violation: 15, cost_per_escalation: 25 });
    expect(s.total_events).toBe(4);
    expect(s.unique_agents).toBe(3);
    expect(s.unique_sessions).toBe(4);
    expect(s.avg_latency_ms).toBe(25);
    expect(s.p50_latency_ms).toBe(30);
    expect(s.p95_latency_ms).toBe(40);
    expect(s.violations_prevented).toBe(2);
    expect(s.estimated_savings).toBe(2 * 15 + 1 * 25);
    expect(s.top_rules[0]).toEqual({ rule_id: "CREDIT-001", count: 2 });
    expect(s.top_intents).toEqual([
      { intent: "general", count: 2 },
      { intent: "financial_compensation", count: 2 },
    ]);
    expect(s.time_range).toEqual({ first: EVENTS[0].timestamp, last: EVENTS[3].timestamp });
  });

  it("is empty before any event", () => {
    const s = deriveSummary([], { cost_per_violation: 15, cost_per_escalation: 25 });
    expect(s.p95_latency_ms).toBe(0);
    expect(s.time_range).toEqual({ first: null, last: null });
  });
});

describe("filterEvents", () => {
  it("filters by action, intent, agent and violation severity", () => {
    expect(filterEvents(EVENTS, { action: "correct" }).map((e) => e.event_id)).toEqual(["S02", "S04"]);
    expect(filterEvents(EVENTS, { intent_type: "financial_compensation", agent_id: "agent-james" }).map((e) => e.event_id)).toEqual(["S03"]);
    expect(filterEvents(EVENTS, { severity: "critical" }).map((e) => e.event_id)).toEqual(["S03"]);
  });

  it("matches severity on any violation, like the backend, not the event's top severity", () => {
    // S03 is a critical event that also carries a high violation
    expect(filterEvents(EVENTS, { severity: "high" }).map((e) => e.event_id)).toEqual(["S02", "S03"]);
  });

  it("treats dates as whole days", () => {
    expect(filterEvents(EVENTS, { date_from: "2026-11-03", date_to: "2026-11-03" })).toHaveLength(4);
    expect(filterEvents(EVENTS, { date_from: "2026-11-04" })).toHaveLength(0);
    expect(filterEvents(EVENTS, { date_to: "2026-11-02" })).toHaveLength(0);
  });
});

describe("toCsv", () => {
  it("writes the backend export columns with quoting", () => {
    const lines = toCsv([EVENTS[1]]).split("\r\n");
    expect(lines[0]).toBe(
      "event_id,timestamp,agent_id,session_id,original_text,corrected_text,was_modified,action_taken,intent_type,confidence,latency_ms",
    );
    expect(lines[1]).toBe(
      "S02,2026-11-03T12:00:02.500Z,agent-sarah,S02,I promise a $200 credit. It's permanent.,I can offer you a $50 credit.,True,correct,financial_compensation,0.92,20",
    );
  });

  it("quotes a field that contains a comma", () => {
    const e = { ...EVENTS[0], original_text: "Hi, there" };
    expect(toCsv([e]).split("\r\n")[1]).toContain(',"Hi, there",');
  });

  it("doubles embedded quotes", () => {
    const e = { ...EVENTS[0], original_text: 'Say "hi", ok' };
    expect(toCsv([e]).split("\r\n")[1]).toContain('"Say ""hi"", ok"');
  });
});

describe("isReplayFile", () => {
  const file: ReplayFile = {
    schema_version: 1,
    recorded_at: RECORDED_AT,
    git_sha: "abc",
    mode: "offline-deterministic",
    python: "3.14",
    latency_note: "n",
    roi_assumptions: { cost_per_violation: 15, cost_per_escalation: 25 },
    rules: [],
    scenarios: FIXTURE,
  };

  it("accepts a schema v1 file", () => {
    expect(isReplayFile(file)).toBe(true);
  });

  it("accepts the recorded public/replay/replay.json", () => {
    expect(isReplayFile(recorded as unknown)).toBe(true);
  });

  it("rejects other shapes", () => {
    expect(isReplayFile(null)).toBe(false);
    expect(isReplayFile({ ...file, schema_version: 2 })).toBe(false);
    expect(isReplayFile({ ...file, scenarios: "x" })).toBe(false);
    expect(isReplayFile({ ...file, recorded_at: 5 })).toBe(false);
  });

  it("rejects scenarios and rules the dashboard could not render", () => {
    const bad = (s: Record<string, unknown>) => ({ ...file, scenarios: [{ ...ALLOW, ...s }] });
    expect(isReplayFile(bad({ violations: null }))).toBe(false);
    expect(isReplayFile(bad({ edits: "x" }))).toBe(false);
    expect(isReplayFile(bad({ pii_types: undefined }))).toBe(false);
    expect(isReplayFile(bad({ fact_errors: {} }))).toBe(false);
    expect(isReplayFile(bad({ action: "block" }))).toBe(false);
    expect(isReplayFile(bad({ latency_ms: "12" }))).toBe(false);
    expect(isReplayFile(bad({ heuristic_confidence: null }))).toBe(false);
    expect(isReplayFile(bad({ input: 3 }))).toBe(false);
    expect(isReplayFile(bad({ severity: "constructor" }))).toBe(false);
    expect(isReplayFile(bad({ violations: [{ rule_id: "X" }] }))).toBe(false);
    expect(isReplayFile(bad({ edits: [{ rule_id: "X", before: "a", after: "b", tier: "magic" }] }))).toBe(false);
    expect(isReplayFile(bad({ fact_errors: [{ claim: 1, correct_value: "x" }] }))).toBe(false);
    expect(isReplayFile(bad({ pii_types: [5] }))).toBe(false);
    expect(isReplayFile({ ...file, rules: [{ name: "no id" }] })).toBe(false);
    expect(isReplayFile({ ...file, roi_assumptions: { cost_per_violation: "15", cost_per_escalation: 25 } })).toBe(false);
  });
});
