import { useState } from "react";
import type { SupervisorQueueItem, RejectionReason } from "../types/api";
import { ErrorAlert } from "./ErrorAlert";
import { approveEvent, rejectEvent } from "../hooks/useApi";
import { AlertCircle, Check, X } from "lucide-react";

const REJECTION_REASONS: { value: RejectionReason; label: string }[] = [
  { value: "tone_wrong", label: "Tone incorrect" },
  { value: "incomplete_fix", label: "Incomplete fix" },
  { value: "over_corrected", label: "Over-corrected" },
  { value: "factually_wrong", label: "Factually wrong" },
  { value: "context_lost", label: "Context lost" },
];

interface Props {
  queue: SupervisorQueueItem[];
  error?: string | null;
  loading?: boolean;
  onRetry?: () => void;
  onDecision?: () => void;
}

function DecisionBadge({ decision, rejectionReason }: { decision: string; rejectionReason?: string | null }) {
  const cls = decision === "approved" ? "badge-approved" : "badge-rejected";
  const reasonLabel = rejectionReason
    ? REJECTION_REASONS.find((r) => r.value === rejectionReason)?.label ?? rejectionReason
    : null;
  return (
    <span className={`badge ${cls}`}>
      {decision === "approved" ? "Approved" : "Rejected"}
      {reasonLabel && <span className="rejection-reason-tag"> ({reasonLabel})</span>}
    </span>
  );
}

export function SupervisorQueue({ queue, error, loading, onRetry, onDecision }: Props) {
  const [actionError, setActionError] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [selectedReason, setSelectedReason] = useState<RejectionReason | "">("");

  const handleApprove = async (eventId: string) => {
    try {
      await approveEvent(eventId, "supervisor");
      setActionError(null);
      onDecision?.();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Approve failed");
    }
  };

  const handleRejectStart = (eventId: string) => {
    setRejectingId(eventId);
    setSelectedReason("");
  };

  const handleRejectConfirm = async () => {
    if (!rejectingId) return;
    try {
      await rejectEvent(rejectingId, "supervisor", "", selectedReason || null);
      setActionError(null);
      setRejectingId(null);
      setSelectedReason("");
      onDecision?.();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Reject failed");
    }
  };

  const handleRejectCancel = () => {
    setRejectingId(null);
    setSelectedReason("");
  };

  return (
    <div className="panel queue-panel" role="region" aria-label="Supervisor review queue">
      <h2>
        <AlertCircle size={18} style={{ marginRight: 8 }} aria-hidden="true" />
        Supervisor Queue
        {queue.length > 0 && (
          <span className="queue-count" aria-label={`${queue.length} items`}>
            {queue.length}
          </span>
        )}
      </h2>
      {error && <ErrorAlert message={error} onRetry={onRetry} />}
      {actionError && <ErrorAlert message={actionError} />}
      {loading && queue.length === 0 && !error ? (
        <div className="loading-spinner" aria-label="Loading queue">Loading...</div>
      ) : queue.length === 0 && !error ? (
        <p className="muted">No items pending review.</p>
      ) : (
        <div className="queue-list" role="list" tabIndex={0} aria-label="Queue items">
          {queue.map((item) => {
            const isPending = !item.decision;
            const isRejecting = rejectingId === item.event_id;
            return (
              <div
                key={item.event_id}
                className="queue-item"
                role="listitem"
                aria-label={`${item.agent_id}: ${item.severity} severity — ${item.decision || "pending review"}`}
              >
                <div className="queue-header">
                  <span className="agent-name">{item.agent_id.replace(/^agent-/i, '').replace(/^\w/, c => c.toUpperCase())}</span>
                  <span
                    className={`badge badge-severity badge-severity--${item.severity}`}
                  >
                    {item.severity.toUpperCase()}
                  </span>
                  {item.decision ? (
                    <DecisionBadge decision={item.decision} rejectionReason={item.rejection_reason} />
                  ) : (
                    <span className="queue-status">pending review</span>
                  )}
                </div>
                <p className="original-text">{item.original_text}</p>
                {item.violations.length > 0 && (
                  <div className="violations" role="list" aria-label="Violations">
                    {item.violations.map((v, i) => (
                      <span key={i} className="violation-tag" role="listitem">
                        {v.rule_id}: {v.description}
                      </span>
                    ))}
                  </div>
                )}
                {isPending && !isRejecting && (
                  <div className="queue-actions">
                    <button
                      className="btn btn-approve"
                      onClick={() => handleApprove(item.event_id)}
                      aria-label={`Approve event ${item.event_id}`}
                    >
                      <Check size={14} aria-hidden="true" /> Approve
                    </button>
                    <button
                      className="btn btn-reject"
                      onClick={() => handleRejectStart(item.event_id)}
                      aria-label={`Reject event ${item.event_id}`}
                    >
                      <X size={14} aria-hidden="true" /> Reject
                    </button>
                  </div>
                )}
                {isRejecting && (
                  <div className="queue-actions reject-form" role="form" aria-label="Rejection reason form">
                    <label htmlFor={`reason-${item.event_id}`} className="sr-only">
                      Rejection reason
                    </label>
                    <select
                      id={`reason-${item.event_id}`}
                      className="rejection-reason-select"
                      value={selectedReason}
                      onChange={(e) => setSelectedReason(e.target.value as RejectionReason)}
                      aria-label="Select rejection reason"
                    >
                      <option value="">Select reason (optional)</option>
                      {REJECTION_REASONS.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                    <button
                      className="btn btn-reject"
                      onClick={handleRejectConfirm}
                      aria-label="Confirm rejection"
                    >
                      <X size={14} aria-hidden="true" /> Confirm Reject
                    </button>
                    <button
                      className="btn btn-cancel"
                      onClick={handleRejectCancel}
                      aria-label="Cancel rejection"
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
