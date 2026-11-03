import { useEffect, useRef, useState } from "react";
import type { CorrectionEvent, WebSocketMessage } from "../types/api";

const WS_URL = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
const RECONNECT_DELAY = 3000;
const PING_INTERVAL = 15000;

export function useWebSocket() {
  const [events, setEvents] = useState<CorrectionEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const pingTimer = useRef<ReturnType<typeof setInterval>>(undefined);
  const seenIds = useRef<Set<string>>(new Set());
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    function connect() {
      if (wsRef.current?.readyState === WebSocket.OPEN) return;
      // Clean up previous connection
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
      }

      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setConnected(true);
        setError(null);
        setReconnecting(false);
        clearInterval(pingTimer.current);
        pingTimer.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send("ping");
          }
        }, PING_INTERVAL);
      };

      ws.onmessage = (e) => {
        if (!mountedRef.current) return;
        try {
          if (e.data === "pong") return;
          const msg = JSON.parse(e.data);
          if ("type" in msg && msg.type === "queue_decision") return;
          const event: CorrectionEvent =
            "type" in msg && msg.type === "correction_event"
              ? ((msg as WebSocketMessage).data as CorrectionEvent)
              : (msg as CorrectionEvent);

          if (event.event_id && seenIds.current.has(event.event_id)) return;
          if (event.event_id) seenIds.current.add(event.event_id);

          setEvents((prev) => [event, ...prev].slice(0, 200));
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        setReconnecting(true);
        clearInterval(pingTimer.current);
        reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY);
      };

      ws.onerror = () => {
        setError("WebSocket connection failed");
        ws.close();
      };
    }

    connect();

    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimer.current);
      clearInterval(pingTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on cleanup
        wsRef.current.close();
      }
    };
  }, []); // no dependencies — connect once on mount

  return { events, connected, error, reconnecting };
}
