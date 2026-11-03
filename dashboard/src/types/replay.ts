/** Types matching dashboard/public/replay/replay.json (scripts/record_replay.py). */

export type Severity = "low" | "medium" | "high" | "critical";

export interface ReplayViolation {
  rule_id: string;
  rule_name: string;
  description: string;
  severity: Severity;
  source_document: string;
  source_section: string;
}

export interface ReplayFactError {
  claim: string;
  correct_value: string;
  source_document: string;
  source_page: number | null;
}

export interface ReplayEdit {
  rule_id: string;
  before: string;
  after: string;
  start: number;
  end: number;
  source_document: string;
  source_section: string;
  tier: "neo4j" | "llm" | "span" | "template";
}

export interface ReplayExplanation {
  rationale: string;
  rules_applied: string[];
  source_documents: string[];
}

export interface ReplayScenario {
  id: string;
  title: string;
  agent_id: string;
  input: string;
  customer_context: Record<string, string | number>;
  detected_language: string;
  intent: string;
  intent_confidence: number;
  pii_types: string[];
  violations: ReplayViolation[];
  fact_errors: ReplayFactError[];
  edits: ReplayEdit[];
  action: "allow" | "correct" | "escalate";
  was_modified: boolean;
  /** null for escalations: nothing is sent, the supervisor decides. */
  corrected_text: string | null;
  severity: Severity;
  heuristic_confidence: number;
  guardrails_applied: string[];
  explanation: ReplayExplanation | null;
  latency_ms: number;
  t_offset_ms: number;
}

export interface ReplayRule {
  rule_id: string;
  name: string;
  description: string;
  severity: Severity;
  intent_type: string;
  source_document: string | null;
  source_section: string | null;
}

export interface ReplayFile {
  schema_version: 1;
  recorded_at: string;
  git_sha: string;
  mode: string;
  python: string;
  latency_note: string;
  roi_assumptions: { cost_per_violation: number; cost_per_escalation: number };
  rules: ReplayRule[];
  scenarios: ReplayScenario[];
}

/** A supervisor decision made in the replay (local state, reset on reload). */
export interface ReplayDecision {
  decision: "approved" | "rejected";
  rejection_reason: string | null;
  decided_at: string;
}
