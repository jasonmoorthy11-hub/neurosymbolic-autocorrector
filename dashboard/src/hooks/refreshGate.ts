/**
 * Caps how often an event-driven refresh may fire. The API rate-limits its
 * read endpoints (60/min), and a fast demo would otherwise stack one refresh
 * per WebSocket event on top of the polling.
 */
export class RefreshGate {
  private last = Number.NEGATIVE_INFINITY;
  private readonly minGapMs: number;

  constructor(minGapMs: number) {
    this.minGapMs = minGapMs;
  }

  /** True when a refresh may run now; blocked calls do not move the clock. */
  allow(nowMs: number): boolean {
    if (nowMs - this.last < this.minGapMs) return false;
    this.last = nowMs;
    return true;
  }

  /** Milliseconds until the gate opens again; 0 when it is open now. */
  remainingMs(nowMs: number): number {
    return Math.max(0, this.last + this.minGapMs - nowMs);
  }
}
