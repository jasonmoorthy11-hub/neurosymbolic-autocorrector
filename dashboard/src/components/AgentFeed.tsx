import type { CorrectionEvent } from "../types/api";

interface Props {
  events: CorrectionEvent[];
}

function actionBadge(action: string) {
  const styles: Record<string, { bg: string; text: string }> = {
    allow: { bg: "#dcfce7", text: "#166534" },
    correct: { bg: "#fef3c7", text: "#92400e" },
    escalate: { bg: "#fee2e2", text: "#991b1b" },
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

export function AgentFeed({ events }: Props) {
  return (
    <div className="panel agent-feed" role="feed" aria-label="Live agent feed" aria-live="polite">
      <h2>Live Agent Feed</h2>
      {events.length === 0 ? (
        <p className="muted">No events yet — start the demo to see live corrections.</p>
      ) : (
        <div className="feed-list" tabIndex={0} role="list" aria-label="Correction events">
          {events.slice(0, 50).map((e) => (
            <article
              key={e.event_id}
              className={`feed-item feed-item--${e.action}`}
              role="listitem"
              aria-label={`${e.agent_id}: ${e.action} — ${e.severity} severity`}
            >
              <div className="feed-header">
                <span className="agent-name">{e.agent_id.replace(/^agent-/i, '').replace(/^\w/, c => c.toUpperCase())}</span>
                {severityBadge(e.severity)}
                {actionBadge(e.action)}
                {e.pii_detected && (
                  <span className="badge badge-pii">PII</span>
                )}
                <span className="feed-time">{timeAgo(e.timestamp)}</span>
              </div>
              <div className="feed-body">
                <p className="original-text">{e.original_text}</p>
                {e.was_modified && (
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
                <span>Confidence: {(e.confidence * 100).toFixed(0)}%</span>
                <span>{e.latency_ms.toFixed(0)}ms</span>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
