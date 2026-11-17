import { useCallback, useEffect, useRef, useState } from "react";
import type { TraceResult } from "../types/replay";
import type { TryItContext, TryItMessage, TryItRequest } from "./protocol";

/** The part of a Worker the panel uses; tests pass a fake. */
export interface TryItWorker {
  postMessage(message: TryItRequest): void;
  terminate(): void;
  onmessage: ((event: MessageEvent<TryItMessage>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
}

export interface TryItRun {
  input: string;
  context: TryItContext;
  result: TraceResult;
}

export interface TryItState {
  status: "idle" | "loading" | "ready" | "checking" | "error";
  step: number;
  label: string;
  run: TryItRun | null;
  error: string | null;
}

const IDLE: TryItState = { status: "idle", step: 0, label: "", run: null, error: null };

function startWorker(): TryItWorker {
  return new Worker(new URL("./worker.ts", import.meta.url), { type: "module" }) as TryItWorker;
}

/** Loads the in-browser checker on request and runs replies through it. */
export function useTryIt(createWorker: () => TryItWorker = startWorker) {
  const [state, setState] = useState<TryItState>(IDLE);
  const worker = useRef<TryItWorker | null>(null);
  const pending = useRef<{ id: number; input: string; context: TryItContext } | null>(null);
  const nextId = useRef(1);

  const stop = useCallback(() => {
    worker.current?.terminate();
    worker.current = null;
    pending.current = null;
  }, []);
  useEffect(() => stop, [stop]);

  const load = useCallback(() => {
    stop();
    const fail = (error: string) => {
      stop();
      setState({ ...IDLE, status: "error", error });
    };
    let started: TryItWorker;
    try {
      started = createWorker();
    } catch {
      fail("This browser could not start the checker.");
      return;
    }
    worker.current = started;
    setState({ ...IDLE, status: "loading" });
    started.onerror = () => fail("The checker could not be loaded.");
    started.onmessage = ({ data }) => {
      if (data.type === "progress") {
        setState((s) => ({ ...s, step: data.step, label: data.label }));
      } else if (data.type === "ready") {
        setState((s) => ({ ...s, status: "ready" }));
      } else if (data.type === "result") {
        const asked = pending.current;
        if (!asked || asked.id !== data.id) return; // an answer to an earlier press
        pending.current = null;
        setState((s) => ({
          ...s,
          status: "ready",
          error: null,
          run: { input: asked.input, context: asked.context, result: { ...data.result, latency_ms: data.ms } },
        }));
      } else if (pending.current) {
        // A failed check: the checker itself is still loaded. The answer to the
        // reply before goes, so it is not read as the answer to this one.
        pending.current = null;
        setState((s) => ({ ...s, status: "ready", run: null, error: data.message }));
      } else {
        fail(data.message);
      }
    };
    started.postMessage({ type: "load" });
  }, [createWorker, stop]);

  const check = useCallback((text: string, context: TryItContext) => {
    if (!worker.current || pending.current) return;
    const id = nextId.current++;
    pending.current = { id, input: text, context };
    setState((s) => ({ ...s, status: "checking", error: null }));
    worker.current.postMessage({ type: "check", id, text, context });
  }, []);

  return { state, load, check };
}
