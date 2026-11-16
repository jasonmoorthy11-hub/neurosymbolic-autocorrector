import { useId, useState, type FormEvent } from "react";
import { FlaskConical } from "lucide-react";
import { LOAD_STEPS, MAX_REPLY_CHARS, MAX_TENURE_DAYS, PLANS, toRequest } from "../tryit/protocol";
import { useTryIt, type TryItWorker } from "../tryit/useTryIt";
import { TraceSteps } from "./TraceSteps";
import "./TryItPanel.css";

const EXAMPLES = [
  { label: "Credit over the limit", text: "I can offer you a $75 credit for your trouble.", tenure: "30" },
  { label: "Reply with an SSN", text: "I've verified your identity using your SSN 123-45-6789.", tenure: "" },
  { label: "Nothing to fix", text: "Thanks for your patience. Your order shipped this morning.", tenure: "" },
];

interface Props {
  /** Tests pass a fake worker; the default starts the real one. */
  createWorker?: () => TryItWorker;
}

/** Check a typed reply against the sample rules, in the visitor's own browser. */
export function TryItPanel({ createWorker }: Props) {
  const { state, load, check } = useTryIt(createWorker);
  const [text, setText] = useState("");
  const [tenure, setTenure] = useState("");
  const [plan, setPlan] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const ids = { text: useId(), tenure: useId(), plan: useId(), progress: useId(), hint: useId() };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const request = toRequest(text, tenure, plan);
    if ("error" in request) {
      setFormError(request.error);
      return;
    }
    setFormError(null);
    check(request.text, request.context);
  };

  const loaded = state.status === "ready" || state.status === "checking";
  const error = formError ?? state.error;
  const run = state.run;

  return (
    <section className="panel tryit-panel" aria-label="Try it">
      <h2>
        <FlaskConical size={18} style={{ marginRight: 8 }} aria-hidden="true" />
        Try it
      </h2>
      <p className="tryit-lead">
        Check a reply of your own against the eight sample rules. The checker runs in this browser
        tab: nothing you type is sent anywhere. English only, deterministic path (no language
        model, no LLM tier).
      </p>

      {/* One short status per change, for screen readers */}
      <p className="sr-only" aria-live="polite">
        {state.status === "loading" && "Loading the checker."}
        {state.status === "ready" && !run && "The checker is ready."}
        {state.status === "ready" && run && `Checked: ${run.result.action}.`}
      </p>

      {(state.status === "idle" || state.status === "error") && (
        <div className="tryit-start">
          <button type="button" className="btn btn-secondary" onClick={load}>
            {state.status === "error" ? "Try loading again" : "Load the checker"}
          </button>
          <span className="tryit-note">Downloads about 8 MB (Python compiled for the browser).</span>
        </div>
      )}

      {state.status === "loading" && (
        <div className="tryit-loading">
          <label htmlFor={ids.progress}>
            {state.label || "Starting"}… (step {Math.max(state.step, 1)} of {LOAD_STEPS.length})
          </label>
          <progress id={ids.progress} max={LOAD_STEPS.length} value={Math.max(state.step - 0.5, 0.2)} />
        </div>
      )}

      {loaded && (
        <form className="tryit-form" onSubmit={submit} noValidate>
          <div className="tryit-field tryit-field--reply">
            <label htmlFor={ids.text}>Agent reply</label>
            <textarea
              id={ids.text}
              rows={3}
              maxLength={MAX_REPLY_CHARS}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="I can offer you a $75 credit for your trouble."
              aria-describedby={ids.hint}
            />
          </div>
          <div className="tryit-field">
            <label htmlFor={ids.tenure}>Customer tenure (days)</label>
            <input
              id={ids.tenure}
              type="number"
              inputMode="numeric"
              min={0}
              max={MAX_TENURE_DAYS}
              step={1}
              value={tenure}
              onChange={(e) => setTenure(e.target.value)}
            />
          </div>
          <div className="tryit-field">
            <label htmlFor={ids.plan}>Plan</label>
            <select id={ids.plan} value={plan} onChange={(e) => setPlan(e.target.value)}>
              {PLANS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <div className="tryit-actions">
            <button type="submit" className="btn btn-start" disabled={state.status === "checking"}>
              {state.status === "checking" ? "Checking…" : "Check reply"}
            </button>
            <span id={ids.hint} className="tryit-examples">
              Examples:{" "}
              {EXAMPLES.map((example) => (
                <button
                  key={example.label}
                  type="button"
                  className="btn btn-secondary btn-example"
                  onClick={() => {
                    setText(example.text);
                    setTenure(example.tenure);
                    setFormError(null);
                  }}
                >
                  {example.label}
                </button>
              ))}
            </span>
          </div>
        </form>
      )}

      {error && (
        <p className="tryit-error" role="alert">
          {error}
        </p>
      )}

      {loaded && run && (
        <div className="tryit-result">
          <p className="trace-input">“{run.input}”</p>
          {Object.keys(run.context).length > 0 && (
            <dl className="trace-context" aria-label="Customer context">
              {Object.entries(run.context).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
          <TraceSteps
            s={run.result}
            latencyNote="measured in this browser, Python under WebAssembly"
            languageNote={run.result.detected_language === "en" ? "assumed, not detected" : "not Latin script, so not read as English"}
          />
        </div>
      )}
    </section>
  );
}
