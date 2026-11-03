/**
 * Replay-mode stand-ins for the live hooks in hooks/useApi.ts and
 * hooks/useWebSocket.ts: same return shapes, data derived from replay.json.
 */
import { useReplayContext } from "./context";

export function useReplayMetrics() {
  const r = useReplayContext();
  return { metrics: r.metrics, error: r.error, loading: r.loading, refresh: r.reload };
}

export function useReplayQueue() {
  const r = useReplayContext();
  return { queue: r.queue, error: r.error, loading: r.loading, refresh: r.reload };
}

export function useReplayRules() {
  const r = useReplayContext();
  return { rules: r.rules, error: r.error, loading: r.loading, refresh: r.reload };
}

export function useReplayAnalyticsTrends() {
  const r = useReplayContext();
  return { data: r.trends, error: r.error, loading: r.loading, refresh: r.reload };
}

export function useReplayAnalyticsSummary() {
  const r = useReplayContext();
  return { data: r.summary, error: r.error, loading: r.loading, refresh: r.reload };
}

export function useReplayQueueActions() {
  const r = useReplayContext();
  return { approve: r.approve, reject: r.reject };
}

export function useReplayExport() {
  return useReplayContext().exportEvents;
}

export function useReplayWebSocket() {
  const r = useReplayContext();
  return { events: r.events, connected: true, error: null, reconnecting: false };
}
