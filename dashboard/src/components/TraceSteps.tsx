import type { ReactNode } from "react";
import type { ReplayEdit, TraceResult } from "../types/replay";
import "./PipelineTrace.css";

const TIER_LABELS: Record<ReplayEdit["tier"], string> = {
  span: "span rewrite",
  template: "rule template",
  llm: "LLM rewrite",
  neo4j: "graph template",
};

const ACTION_LABELS: Record<TraceResult["action"], string> = {
  allow: "ALLOW — passed through unchanged",
  correct: "CORRECT — corrected text sent, conversation continues",
  escalate: "Escalated — held for supervisor",
};

function cite(document: string, section: string): string {
  return [document, section].filter(Boolean).join(", ");
}

function Step({ label, children }: { label: string; children: ReactNode }) {
  return (
    <li className="trace-step">
      <h3>{label}</h3>
      <div className="trace-step-body">{children}</div>
    </li>
  );
}

function None({ text }: { text: string }) {
  return <p className="muted">{text}</p>;
}

interface Props {
  s: TraceResult;
  /** Where the latency figure comes from, shown in brackets after it. */
  latencyNote: string;
  /** Shown after the language when it was not detected (the browser panel). */
  languageNote?: string;
}

/** The eight numbered steps of one run: what was found, what was done, and why. */
export function TraceSteps({ s, latencyNote, languageNote }: Props) {
  return (
    <ol className="trace-steps">
      <Step label="1. Language">
        <p>
          {s.detected_language}
          {languageNote && <span className="muted"> ({languageNote})</span>}
        </p>
      </Step>
      <Step label="2. Intent">
        <p>
          {s.intent} <span className="muted">(classifier score {s.intent_confidence.toFixed(2)})</span>
        </p>
      </Step>
      <Step label="3. PII">
        {s.pii_types.length ? (
          <p>
            {s.pii_types.join(", ")} <span className="muted">— redacted before delivery</span>
          </p>
        ) : (
          <None text="none found" />
        )}
      </Step>
      <Step label="4. Policy rules">
        {s.violations.length ? (
          <ul className="trace-list">
            {s.violations.map((v) => (
              <li key={v.rule_id}>
                <span className="rule-id">{v.rule_id}</span>{" "}
                <span className={`badge badge-severity badge-severity--${v.severity}`}>
                  {v.severity.toUpperCase()}
                </span>{" "}
                {v.description}
                <span className="trace-source">{cite(v.source_document, v.source_section)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <None text="no rule triggered" />
        )}
      </Step>
      <Step label="5. Fact check">
        {s.fact_errors.length ? (
          <ul className="trace-list">
            {s.fact_errors.map((f, i) => (
              <li key={i}>
                Claimed {f.claim}, policy says {f.correct_value}
                <span className="trace-source">
                  {cite(f.source_document, f.source_page === null ? "" : `p. ${f.source_page}`)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <None text="no checkable claim contradicted the policy" />
        )}
      </Step>
      <Step label="6. Action">
        <p className={`trace-action trace-action--${s.action}`}>{ACTION_LABELS[s.action]}</p>
      </Step>
      <Step label="7. Edits">
        {s.action === "escalate" ? (
          <None text="No rewritten text: nothing is sent until a supervisor decides." />
        ) : s.edits.length ? (
          <ul className="trace-list trace-edits">
            {s.edits.map((e, i) => (
              <li key={i}>
                <div className="trace-edit-meta">
                  <span className="rule-id">{e.rule_id}</span> · {TIER_LABELS[e.tier]}
                  <span className="trace-source">{cite(e.source_document, e.source_section)}</span>
                </div>
                <del className="trace-before">{e.before}</del>
                {e.after ? (
                  <ins className="trace-after">{e.after}</ins>
                ) : (
                  <span className="trace-removed">removed</span>
                )}
              </li>
            ))}
          </ul>
        ) : s.pii_types.length ? (
          <None text="No policy edits; PII redaction only." />
        ) : (
          <None text="No edits." />
        )}
        {s.action === "correct" && s.corrected_text && (
          <p className="trace-output">
            <span className="muted">Sent: </span>“{s.corrected_text}”
          </p>
        )}
      </Step>
      <Step label="8. Latency">
        <p>
          {s.latency_ms.toFixed(1)} ms <span className="muted">({latencyNote})</span>
        </p>
      </Step>
    </ol>
  );
}
