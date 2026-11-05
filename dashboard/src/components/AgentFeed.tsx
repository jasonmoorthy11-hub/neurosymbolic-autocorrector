import { Route } from "lucide-react";
import type { CorrectionEvent } from "../types/api";
import "./AgentFeed.css";

interface Props {
  events: CorrectionEvent[];
  title?: string;
  emptyMessage?: string;
  /** Selection for the pipeline trace (replay mode); omitted in live mode. */
  selectedId?: string | null;
  /** The run the user pinned (null while the trace follows the newest run). */
  pinnedId?: string | null;
  onSelect?: (eventId: string | null) => void;
  timeLabel?: (e: CorrectionEvent) => string;
}

function actionBadge(action: string) {
  const styles: Record<string, { bg: string; text: string }> = {
    allow: { bg: "var(--badge-allow-bg)", text: "var(--badge-allow-text)" },
    correct: { bg: "var(--badge-correct-bg)", text: "var(--badge-correct-text)" },
    escalate: { bg: "var(--badge-escalate-bg)", text: "var(--badge-escalate-text)" },
  };
  const s = styles[action] ?? styles.allow;
  return (
    <span
      className="badge"
      style={{ backgroundColor: s.bg, color: s.text }}
    >
      {action.toUpperCase()}
    </span>
  );
}

function severityBadge(severity: string) {
  return (
    <span className={`badge badge-severity badge-severity--${severity}`}>
      {severity.toUpperCase()}
    </span>
  );
}

function timeAgo(ts: string) {
  const diff = Date.now() - new Date(ts).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ago`;
}

function agentName(agentId: string) {
  return agentId.replace(/^agent-/i, '').replace(/^\w/, c => c.toUpperCase());
}

export function AgentFeed({
  events,
  title = "Live Agent Feed",
  emptyMessage = "No events yet — start the demo to see live corrections.",
  selectedId,
  pinnedId = null,
  onSelect,
  timeLabel = (e) => timeAgo(e.timestamp),
}: Props) {
  return (
    <div className="panel agent-feed" role="feed" aria-label={title} aria-live="polite">
      <h2>{title}</h2>
      {events.length === 0 ? (
        <p className="muted">{emptyMessage}</p>
      ) : (
        <div className="feed-list" tabIndex={0} role="list" aria-label="Correction events">
          {events.slice(0, 50).map((e) => (
            <article
              key={e.event_id}
              className={`feed-item feed-item--${e.action}${selectedId === e.event_id ? " feed-item--selected" : ""}`}
              role="listitem"
              aria-label={`${e.agent_id}: ${e.action} — ${e.severity} severity`}
              aria-current={selectedId === e.event_id ? "true" : undefined}
              onClick={onSelect ? () => onSelect(e.event_id) : undefined}
            >
              <div className="feed-header">
                <span className="agent-name">{agentName(e.agent_id)}</span>
                {severityBadge(e.severity)}
                {actionBadge(e.action)}
                {e.pii_detected && (
                  <span className="badge badge-pii">PII</span>
                )}
                <span className="feed-time">{timeLabel(e)}</span>
              </div>
              <div className="feed-body">
                <p className="original-text">{e.original_text}</p>
                {/* An escalation sends nothing, so there is no corrected text to show */}
                {e.was_modified && e.action !== "escalate" && (
                  <p className="corrected-text">{e.corrected_text}</p>
                )}
              </div>
              {e.violations.length > 0 && (
                <div className="violations" role="list" aria-label="Violations">
                  {e.violations.map((v, i) => (
                    <span key={i} className="violation-tag" role="listitem">
                      {v.rule_id}
                    </span>
                  ))}
                </div>
              )}
              <div className="feed-meta">
                <span>Intent: {e.intent_type}</span>
                <span title="Fixed value, not a calibrated probability">
                  Heuristic confidence: {(e.confidence * 100).toFixed(0)}%
                </span>
                <span>{e.latency_ms.toFixed(0)}ms</span>
                {onSelect && (
                  <button
                    type="button"
                    className="btn btn-trace"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      // A second press unpins and goes back to following the newest run
                      onSelect(pinnedId === e.event_id ? null : e.event_id);
                    }}
                    aria-pressed={pinnedId === e.event_id}
                    aria-label={`Trace: pin the pipeline trace for ${agentName(e.agent_id)}, ${e.action}`}
                  >
                    <Route size={14} aria-hidden="true" /> Trace
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
