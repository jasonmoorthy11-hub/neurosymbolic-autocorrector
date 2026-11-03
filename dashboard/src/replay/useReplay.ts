import { useCallback, useEffect, useMemo, useState } from "react";
import type { EventFilters } from "../types/api";
import type { ReplayDecision, ReplayFile } from "../types/replay";
import { saveBlob } from "../hooks/download";
import type { ReplayValue } from "./context";
import {
  deriveMetrics,
  deriveQueue,
  deriveRules,
  deriveSummary,
  deriveTrends,
  filterEvents,
  isReplayFile,
  toCorrectionEvent,
  toCsv,
} from "./derive";
import * as player from "./player";

const TICK_MS = 100;
const TREND_BUCKET_MS = 10_000;

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

/** Loads replay.json (same origin) and plays it back; no other network access. */
export function useReplay(): ReplayValue {
  const [file, setFile] = useState<ReplayFile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadKey, setLoadKey] = useState(0);
  const [state, setState] = useState(() => player.initialPlayer(prefersReducedMotion()));
  const [decisions, setDecisions] = useState<Record<string, ReplayDecision>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${import.meta.env.BASE_URL}replay/replay.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json() as Promise<unknown>;
      })
      .then((data) => {
        if (!isReplayFile(data)) throw new Error("unsupported file format");
        if (!cancelled) {
          setFile(data);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(`Could not load replay data${e instanceof Error ? `: ${e.message}` : ""}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [loadKey]);

  const offsets = useMemo(() => file?.scenarios.map((s) => s.t_offset_ms) ?? [], [file]);

  useEffect(() => {
    if (!file || !state.playing) return;
    const id = setInterval(() => setState((s) => player.tick(s, TICK_MS, offsets)), TICK_MS);
    return () => clearInterval(id);
  }, [file, state.playing, offsets]);

  const count = player.visibleCount(offsets, state.elapsedMs);
  const visible = useMemo(() => file?.scenarios.slice(0, count) ?? [], [file, count]);
  const events = useMemo(
    () => (file ? visible.map((s) => toCorrectionEvent(s, file.recorded_at)).reverse() : []),
    [file, visible],
  );

  const pinned = selectedId ? visible.find((s) => s.id === selectedId) : undefined;
  const selected = pinned ?? visible[visible.length - 1] ?? null;

  const decide = useCallback(
    (eventId: string, decision: ReplayDecision["decision"], reason: string | null) => {
      setDecisions((d) =>
        d[eventId]
          ? d
          : {
              ...d,
              [eventId]: { decision, rejection_reason: reason, decided_at: new Date().toISOString() },
            },
      );
      return Promise.resolve();
    },
    [],
  );

  const exportEvents = useCallback(
    (format: "csv" | "json", filters: EventFilters = {}) => {
      const rows = filterEvents(events, filters);
      const blob =
        format === "csv"
          ? new Blob([toCsv(rows)], { type: "text/csv" })
          : new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
      saveBlob(blob, `events.${format}`);
      return Promise.resolve();
    },
    [events],
  );

  const derived = useMemo(
    () => ({
      metrics: file ? deriveMetrics(events) : null,
      rules: file ? deriveRules(file.rules, events) : [],
      trends: file ? deriveTrends(events, file.recorded_at, TREND_BUCKET_MS) : [],
      summary: file ? deriveSummary(events, file.roi_assumptions) : null,
    }),
    [file, events],
  );
  const queue = useMemo(() => deriveQueue(events, decisions), [events, decisions]);

  // The value changes only when something shown changes (a run revealed, play
  // state, speed, decisions, selection), never on a bare 100 ms clock tick:
  // re-rendering every tick would restart the chart animations before they finish
  const { playing, speed } = state;
  return useMemo<ReplayValue>(
    () => ({
      file,
      error,
      loading: !file && !error,
      // Panels call refresh after every decision; once loaded the file never changes,
      // so only a failed load is retried
      reload: () => {
        if (file) return;
        setError(null);
        setLoadKey((k) => k + 1);
      },
      player: { playing, speed },
      total: offsets.length,
      visible,
      events,
      ...derived,
      queue,
      selected,
      following: !pinned,
      select: setSelectedId,
      play: () => {
        // Replaying from the end starts over, like Reset: no leftover decisions
        if (visible.length >= offsets.length) {
          setDecisions({});
          setSelectedId(null);
        }
        setState((s) => player.play(s, offsets));
      },
      pause: () => setState(player.pause),
      step: () => setState((s) => player.step(s, offsets)),
      reset: () => {
        setState(player.reset);
        setDecisions({});
        setSelectedId(null);
      },
      setSpeed: (next) => setState((s) => player.setSpeed(s, next)),
      approve: (eventId) => decide(eventId, "approved", null),
      reject: (eventId, _reviewer, _comment, reason = null) => decide(eventId, "rejected", reason),
      exportEvents,
    }),
    [file, error, playing, speed, offsets, visible, events, derived, queue, selected, pinned, decide, exportEvents],
  );
}
