import { useState, useEffect, useCallback } from "react";
import type {
  DashboardMetrics,
  SupervisorQueueItem,
  RuleInfo,
  CorrectionEvent,
  EventFilters,
  TrendBucket,
  AnalyticsSummary,
} from "../types/api";

const API_BASE = "";

async function fetchJson<T>(
  path: string,
  method: "GET" | "POST" = "GET",
  body?: unknown
): Promise<T> {
  const opts: RequestInit = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${API_BASE}${path}`, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export function useMetrics(refreshInterval = 2000) {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setMetrics(await fetchJson<DashboardMetrics>("/api/metrics"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load metrics");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, refreshInterval);
    return () => clearInterval(id);
  }, [refresh, refreshInterval]);

  return { metrics, error, loading, refresh };
}

export function useQueue(refreshInterval = 3000) {
  const [queue, setQueue] = useState<SupervisorQueueItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchJson<{ queue: SupervisorQueueItem[] }>("/api/queue");
      setQueue(data.queue);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, refreshInterval);
    return () => clearInterval(id);
  }, [refresh, refreshInterval]);

  return { queue, error, loading, refresh };
}

export function useRules() {
  const [rules, setRules] = useState<RuleInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const d = await fetchJson<{ rules: RuleInfo[] }>("/api/rules");
      setRules(d.rules);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load rules");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { rules, error, loading, refresh };
}

export function useEvents(filters: EventFilters = {}, refreshInterval = 5000) {
  const [events, setEvents] = useState<CorrectionEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filters.date_from) params.set("date_from", filters.date_from);
      if (filters.date_to) params.set("date_to", filters.date_to);
      if (filters.intent_type) params.set("intent_type", filters.intent_type);
      if (filters.action) params.set("action", filters.action);
      if (filters.severity) params.set("severity", filters.severity);
      if (filters.agent_id) params.set("agent_id", filters.agent_id);

      const qs = params.toString();
      const path = `/api/events${qs ? `?${qs}` : ""}`;
      const data = await fetchJson<{ events: CorrectionEvent[]; total: number }>(path);
      setEvents(data.events);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load events");
    } finally {
      setLoading(false);
    }
  }, [
    filters.date_from,
    filters.date_to,
    filters.intent_type,
    filters.action,
    filters.severity,
    filters.agent_id,
  ]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, refreshInterval);
    return () => clearInterval(id);
  }, [refresh, refreshInterval]);

  return { events, total, error, loading, refresh };
}

export function useAnalyticsTrends(hours = 24, refreshInterval = 10000) {
  const [data, setData] = useState<TrendBucket[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const resp = await fetchJson<{ trends: TrendBucket[] }>(
        `/api/analytics/trends?hours=${hours}`
      );
      setData(resp.trends);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load trends");
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, refreshInterval);
    return () => clearInterval(id);
  }, [refresh, refreshInterval]);

  return { data, error, loading, refresh };
}

export function useAnalyticsSummary(refreshInterval = 10000) {
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setData(await fetchJson<AnalyticsSummary>("/api/analytics/summary"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load summary");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, refreshInterval);
    return () => clearInterval(id);
  }, [refresh, refreshInterval]);

  return { data, error, loading, refresh };
}

export async function approveEvent(eventId: string, reviewer = "", comment = "") {
  return fetchJson<{ event_id: string; decision: string }>(
    `/api/queue/${eventId}/approve`,
    "POST",
    { reviewer, comment }
  );
}

export async function rejectEvent(
  eventId: string,
  reviewer = "",
  comment = "",
  rejection_reason: string | null = null
) {
  return fetchJson<{ event_id: string; decision: string; rejection_reason: string | null }>(
    `/api/queue/${eventId}/reject`,
    "POST",
    { reviewer, comment, rejection_reason }
  );
}

export async function exportEvents(format: "csv" | "json", filters: EventFilters = {}) {
  const params = new URLSearchParams({ format });
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  if (filters.intent_type) params.set("intent_type", filters.intent_type);
  if (filters.action) params.set("action", filters.action);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.agent_id) params.set("agent_id", filters.agent_id);

  const res = await fetch(`${API_BASE}/api/events/export?${params.toString()}`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `events.${format}`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function startDemo(interval = 3.0) {
  return fetchJson<{ status: string }>(`/api/demo/start?interval=${interval}`, "POST");
}

export async function stopDemo() {
  return fetchJson<{ status: string }>("/api/demo/stop", "POST");
}
