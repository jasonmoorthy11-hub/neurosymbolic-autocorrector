/** Types matching the FastAPI backend models. */

export interface CorrectionEvent {
  event_id: string;
  timestamp: string;
  agent_id: string;
  session_id: string;
  original_text: string;
  corrected_text: string;
  was_modified: boolean;
  action: "allow" | "correct" | "escalate";
  intent_type: string;
  confidence: number;
  violations: Violation[];
  pii_detected: boolean;
  latency_ms: number;
  severity: "low" | "medium" | "high" | "critical";
}

export interface Violation {
  rule_id: string;
  rule_name: string;
  description: string;
  severity: string;
  details?: string;
}

export interface DashboardMetrics {
  total_events: number;
  corrections_made: number;
  escalations: number;
  allowed_through: number;
  pii_detected: number;
  correction_rate: number;
  avg_latency_ms: number;
  avg_confidence: number;
  violations_by_rule: Record<string, number>;
  events_by_intent: Record<string, number>;
  uptime_seconds: number;
}

export interface SupervisorQueueItem {
  event_id: string;
  timestamp: string;
  agent_id: string;
  original_text: string;
  action: string;
  severity: string;
  violations: Violation[];
  status: string;
  decision: string | null;
  reviewer: string | null;
  comment: string | null;
  rejection_reason: string | null;
  decided_at: string | null;
}

export type RejectionReason =
  | "tone_wrong"
  | "incomplete_fix"
  | "over_corrected"
  | "factually_wrong"
  | "context_lost";

export interface EventFilters {
  date_from?: string;
  date_to?: string;
  intent_type?: string;
  action?: string;
  severity?: string;
  agent_id?: string;
}

export interface RuleInfo {
  rule_id: string;
  name: string;
  description: string;
  severity: string;
  intent_type: string;
  violation_count: number;
  source_document?: string;
  source_section?: string;
}

export interface TrendBucket {
  timestamp: string;
  total: number;
  corrections: number;
  escalations: number;
  allowed: number;
  avg_latency_ms: number;
}

export interface AnalyticsSummary {
  total_events: number;
  corrections: number;
  escalations: number;
  allowed: number;
  unique_agents: number;
  unique_sessions: number;
  top_rules: Array<{ rule_id: string; count: number }>;
  top_intents: Array<{ intent: string; count: number }>;
  avg_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  time_range: { first: string | null; last: string | null };
  violations_prevented: number;
  estimated_savings: number;
  cost_per_violation: number;
  cost_per_escalation: number;
}

export interface WebSocketMessage {
  type: "correction_event" | "queue_decision";
  data: CorrectionEvent | QueueDecision;
}

export interface QueueDecision {
  event_id: string;
  decision: string;
  rejection_reason?: string;
}
