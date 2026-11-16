import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { FlaskConical } from "lucide-react";
import { LOAD_STEPS, MAX_REPLY_CHARS, PLANS, toRequest, type TryItForm } from "../tryit/protocol";
import { useTryIt, type TryItWorker } from "../tryit/useTryIt";
import { TraceSteps } from "./TraceSteps";
import "./TryItPanel.css";

const EMPTY: TryItForm = { text: "", tenure: "", purchase: "", plan: "" };

const EXAMPLES: { label: string; form: TryItForm }[] = [
  {
    label: "Credit over the limit",
    form: { ...EMPTY, text: "I can offer you a $75 credit for your trouble.", tenure: "30" },
  },
  {
    label: "Late refund",
    form: { ...EMPTY, text: "I've processed a full refund to your card.", purchase: "45" },
  },
  {
    label: "Reply with an SSN",
    form: { ...EMPTY, text: "I've verified your identity using your SSN 123-45-6789." },
  },
  {
    label: "Nothing to fix",
    form: { ...EMPTY, text: "Thanks for your patience. Your order shipped this morning." },
  },
];

interface Props {
  /** Tests pass a fake worker; the default starts the real one. */
  createWorker?: () => TryItWorker;
}

/** Check a typed reply against the sample rules, in the visitor's own browser. */
export function TryItPanel({ createWorker }: Props) {
  const { state, load, check } = useTryIt(createWorker);
  const [form, setForm] = useState<TryItForm>(EMPTY);
  const [formError, setFormError] = useState<string | null>(null);
  const ids = {
    text: useId(),
    tenure: useId(),
    purchase: useId(),
    plan: useId(),
    progress: useId(),
    hint: useId(),
  };
  const replyField = useRef<HTMLTextAreaElement>(null);
  const startButton = useRef<HTMLButtonElement>(null);
  const set = (field: keyof TryItForm) => (value: string) => setForm((f) => ({ ...f, [field]: value }));

  const loading = state.status === "loading";
  const loaded = state.status === "ready" || state.status === "checking";
  const error = formError ?? state.error;
  const run = state.run;

  // The start button leaves when the form arrives (and comes back after a failed
  // load): move keyboard focus with it instead of dropping it to the page.
  const wasLoading = useRef(false);
  useEffect(() => {
    if (wasLoading.current && state.status === "ready") replyField.current?.focus();
    if (wasLoading.current && state.status === "error") startButton.current?.focus();
    wasLoading.current = loading;
  }, [state.status, loading]);

  const start = () => {
    if (loading) return;
    setFormError(null);
    load();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const request = toRequest(form);
    if ("error" in request) {
      setFormError(request.error);
      return;
    }
    setFormError(null);
    check(request.text, request.context);
  };

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
        {loading && "Loading the checker."}
        {state.status === "ready" && !run && "The checker is ready."}
        {state.status === "ready" && run && `Checked: ${run.result.action}.`}
      </p>

      {!loaded && (
        <div className="tryit-start">
          {/* Stays mounted while loading so focus is not lost when it is pressed */}
          <button
            ref={startButton}
            type="button"
            className="btn btn-secondary"
            onClick={start}
            aria-disabled={loading}
          >
            {loading ? "Loading…" : state.status === "error" ? "Try loading again" : "Load the checker"}
          </button>
          {loading ? (
            <div className="tryit-loading">
              <label htmlFor={ids.progress}>
                {state.label || "Starting"}… (step {Math.max(state.step, 1)} of {LOAD_STEPS.length})
              </label>
              <progress id={ids.progress} max={LOAD_STEPS.length} value={Math.max(state.step - 0.5, 0.2)} />
            </div>
          ) : (
            <span className="tryit-note">Downloads about 8 MB (Python compiled for the browser).</span>
          )}
        </div>
      )}

      {loaded && (
        <form className="tryit-form" onSubmit={submit} noValidate>
          <div className="tryit-field tryit-field--reply">
            <label htmlFor={ids.text}>Agent reply</label>
            <textarea
              ref={replyField}
              id={ids.text}
              rows={3}
              maxLength={MAX_REPLY_CHARS}
              value={form.text}
              onChange={(e) => set("text")(e.target.value)}
              placeholder="I can offer you a $75 credit for your trouble."
              aria-describedby={ids.hint}
            />
          </div>
          <div className="tryit-field">
            <label htmlFor={ids.tenure}>Customer tenure (days)</label>
            <input
              id={ids.tenure}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={form.tenure}
              onChange={(e) => set("tenure")(e.target.value)}
            />
          </div>
          <div className="tryit-field">
            <label htmlFor={ids.purchase}>Days since purchase</label>
            <input
              id={ids.purchase}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={form.purchase}
              onChange={(e) => set("purchase")(e.target.value)}
            />
          </div>
          <div className="tryit-field">
            <label htmlFor={ids.plan}>Plan</label>
            <select id={ids.plan} value={form.plan} onChange={(e) => set("plan")(e.target.value)}>
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
                    setForm(example.form);
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
            languageNote={
              run.result.detected_language === "en"
                ? "assumed, not detected"
                : "it has letters outside the Latin script, so it is not read as English"
            }
          />
        </div>
      )}
    </section>
  );
}
