/**
 * Replay lanes: which recording the dashboard plays. The default (no query
 * string) is the deterministic offline recording; `?lane=llm` plays the one
 * recorded run with the LLM correction tier.
 */

export type Lane = "deterministic" | "llm";

export const LANE_PARAM = "lane";

export function laneFromSearch(search: string): Lane {
  return new URLSearchParams(search).get(LANE_PARAM) === "llm" ? "llm" : "deterministic";
}

/** The query string for `lane`, keeping every other parameter of `search`. */
export function laneSearch(search: string, lane: Lane): string {
  const params = new URLSearchParams(search);
  if (lane === "llm") params.set(LANE_PARAM, "llm");
  else params.delete(LANE_PARAM);
  const next = params.toString();
  return next ? `?${next}` : "";
}

export function replayFileFor(lane: Lane): "replay/replay.json" | "replay/replay_llm.json" {
  return lane === "llm" ? "replay/replay_llm.json" : "replay/replay.json";
}

export function laneLabel(lane: Lane): string {
  return lane === "llm" ? "LLM tier · Haiku 5.5" : "Deterministic";
}

export function laneBanner(lane: Lane): string {
  return lane === "llm"
    ? "One recorded run with the LLM correction tier (Claude Haiku 5.5) — no backend, no API key at view time"
    : "Recorded runs of the offline pipeline — no backend, no API key";
}

/** Prefix of the latency line, from the file's `mode` (scripts/record_replay.py). */
export function latencyPrefix(mode: string): string {
  if (mode === "offline-deterministic") return "offline path";
  if (mode === "llm-recorded") return "LLM tier";
  return "recorded";
}
