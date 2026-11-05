import type { RuleInfo } from "../types/api";
import { ErrorAlert } from "./ErrorAlert";
import { BookOpen } from "lucide-react";
import "./RulesPanel.css";

interface Props {
  rules: RuleInfo[];
  error?: string | null;
  loading?: boolean;
  onRetry?: () => void;
}

export function RulesPanel({ rules, error, loading, onRetry }: Props) {
  return (
    <div className="panel rules-panel" role="region" aria-label="Active policy rules">
      <h2>
        <BookOpen size={18} style={{ marginRight: 8 }} aria-hidden="true" />
        Active Policy Rules
      </h2>
      {error && <ErrorAlert message={error} onRetry={onRetry} />}
      {loading && rules.length === 0 && !error ? (
        <div className="loading-spinner" aria-label="Loading rules">Loading...</div>
      ) : rules.length === 0 && !error ? (
        <p className="muted">No rules loaded.</p>
      ) : (
        <div className="rules-list" role="list" tabIndex={0} aria-label="Policy rules">
          {rules.map((r) => (
            <div key={r.rule_id} className="rule-card" role="listitem">
              <div className="rule-header">
                <span className="rule-id">{r.rule_id}</span>
                <span className={`badge badge-severity--${r.severity}`}>
                  {r.severity.toUpperCase()}
                </span>
              </div>
              <p className="rule-name">{r.name}</p>
              <p className="rule-desc">{r.description}</p>
              <div className="rule-footer">
                <span className="intent-tag">{r.intent_type}</span>
                <span className="violation-count">
                  {r.violation_count} violation{r.violation_count !== 1 && "s"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
