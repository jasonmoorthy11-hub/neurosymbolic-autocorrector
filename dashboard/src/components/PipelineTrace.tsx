import { Route } from "lucide-react";
import { useReplayContext } from "../replay/context";
import { latencyPrefix } from "../replay/lane";
import { TraceSteps } from "./TraceSteps";
import "./PipelineTrace.css";

export function PipelineTrace() {
  const { selected: s, following, select, file } = useReplayContext();

  return (
    <section className="panel trace-panel" aria-label="Pipeline trace">
      {/* Announce only what the user chose: following updates every 2.5 s */}
      <p className="sr-only" aria-live="polite">
        {s && !following ? `Tracing ${s.id}, ${s.action}` : ""}
      </p>
      <h2>
        <Route size={18} style={{ marginRight: 8 }} aria-hidden="true" />
        Pipeline Trace
      </h2>
      {!s ? (
        <p className="muted">
          {file ? "Press Play or Step to trace a recorded run." : "Loading recorded runs..."}
        </p>
      ) : (
        <>
          <div className="trace-heading">
            <span className="trace-id">{s.id}</span>
            <span className="trace-title">{s.title}</span>
            {/* Always rendered so focus is not lost when it is pressed */}
            <button
              type="button"
              className="btn btn-secondary btn-follow"
              onClick={() => select(null)}
              aria-pressed={following}
            >
              Follow newest run
            </button>
            <span className="trace-follow">
              {following ? "Following the newest run; press Trace on a run to pin it." : `Pinned to ${s.id}.`}
            </span>
          </div>
          <p className="trace-input">“{s.input}”</p>
          {Object.keys(s.customer_context).length > 0 && (
            <dl className="trace-context" aria-label="Customer context">
              {Object.entries(s.customer_context).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}

          <TraceSteps
            s={s}
            latencyNote={file ? `${latencyPrefix(file.mode)}; ${file.latency_note}` : "recorded"}
          />
        </>
      )}
    </section>
  );
}
