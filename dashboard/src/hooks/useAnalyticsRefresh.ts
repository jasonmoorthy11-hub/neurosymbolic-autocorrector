import { useEffect, useRef } from "react";
import { RefreshGate } from "./refreshGate";

/**
 * Refreshes the analytics panel when a new event arrives, at most once per
 * `minGapMs` so a fast demo cannot push `/api/analytics/*` past its 60/min
 * limiter on top of the 10 s poll. An event that lands inside the gap is not
 * dropped: its refresh runs on the trailing edge, so the last event of a burst
 * still reaches the panel within the gap rather than at the next poll.
 */
export function useAnalyticsRefresh(
  newestId: string | undefined,
  refresh: () => void,
  enabled: boolean,
  minGapMs = 2000,
) {
  const gate = useRef(new RefreshGate(minGapMs));
  useEffect(() => {
    if (!enabled || !newestId) return;
    const now = Date.now();
    if (gate.current.allow(now)) {
      refresh();
      return;
    }
    const timer = setTimeout(() => {
      if (gate.current.allow(Date.now())) refresh();
    }, gate.current.remainingMs(now));
    // A newer event re-arms the timer from its own effect; unmount drops it.
    return () => clearTimeout(timer);
  }, [newestId, refresh, enabled]);
}
