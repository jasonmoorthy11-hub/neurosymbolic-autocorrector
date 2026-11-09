import { Route } from "lucide-react";
import type { CorrectionEvent } from "../types/api";
import "./AgentFeed.css";

interface Props {
  events: CorrectionEvent[];
  title?: string;
  emptyMessage?: string;
  /** A second, smaller line under the empty message (what the feed does not show). */
  emptyNote?: string;
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
  emptyNote,
  selectedId,
  pinnedId = null,
  onSelect,
  timeLabel = (e) => timeAgo(e.timestamp),
}: Props) {
  return (
    // A region landmark, not role="feed": that role allows only article children,
    // and this panel has a heading and a list (axe aria-required-children).
    <section className="panel agent-feed" aria-label={title}>
      <h2>{title}</h2>
      {/* One short status per change. The list itself is not a live region: every new
          event re-renders every item's "Ns ago" label, which would queue dozens of
          announcements per event. */}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {events.length > 0
          ? `${events.length} event${events.length === 1 ? "" : "s"} shown; newest: ${agentName(events[0].agent_id)}, ${events[0].action}, ${events[0].severity} severity`
          : ""}
      </p>
      {events.length === 0 ? (
        <>
          <p className="muted">{emptyMessage}</p>
          {emptyNote && <p className="feed-note">{emptyNote}</p>}
        </>
      ) : (
        <ul className="feed-list" tabIndex={0} aria-label="Correction events">
          {events.slice(0, 50).map((e) => (
            <li
              key={e.event_id}
              className={`feed-item feed-item--${e.action}${selectedId === e.event_id ? " feed-item--selected" : ""}`}
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
                {/* An escalation sends nothing, so there is no corrected text to show */}
                {e.action !== "escalate" && e.was_modified && e.original_text === e.corrected_text ? (
                  <>
                    {/* The audit log stores the original already redacted, so a PII-only
                        correction has nothing to strike through: show what was sent once. */}
                    <p className="corrected-text">{e.corrected_text}</p>
                    <p className="feed-note">
                      {e.pii_detected
                        ? "PII redacted before delivery; the audit log keeps no unredacted copy."
                        : "Marked modified with no visible text change."}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="original-text">{e.original_text}</p>
                    {e.was_modified && e.action !== "escalate" && (
                      <p className="corrected-text">{e.corrected_text}</p>
                    )}
                  </>
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
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
