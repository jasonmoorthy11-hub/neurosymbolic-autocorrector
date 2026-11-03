/**
 * Replay clock as pure state transitions. Scenario i becomes visible once
 * elapsedMs >= its t_offset_ms; elapsedMs = -1 means nothing is visible yet.
 */

export type Speed = 1 | 2 | 4;

export interface PlayerState {
  elapsedMs: number;
  playing: boolean;
  speed: Speed;
}

export function visibleCount(offsets: number[], elapsedMs: number): number {
  return offsets.filter((o) => o <= elapsedMs).length;
}

export function initialPlayer(reducedMotion: boolean): PlayerState {
  return { elapsedMs: -1, playing: !reducedMotion, speed: 1 };
}

export function tick(s: PlayerState, dtMs: number, offsets: number[]): PlayerState {
  if (!s.playing) return s;
  // Replay time starts at 0 on the first tick (the -1 sentinel is not time)
  const elapsedMs = Math.max(s.elapsedMs, 0) + dtMs * s.speed;
  const done = visibleCount(offsets, elapsedMs) >= offsets.length;
  return { ...s, elapsedMs, playing: !done };
}

export function play(s: PlayerState, offsets: number[]): PlayerState {
  const atEnd = visibleCount(offsets, s.elapsedMs) >= offsets.length;
  return { ...s, elapsedMs: atEnd ? -1 : s.elapsedMs, playing: true };
}

export function pause(s: PlayerState): PlayerState {
  return { ...s, playing: false };
}

/** Reveal the next scenario and pause. */
export function step(s: PlayerState, offsets: number[]): PlayerState {
  const next = offsets[visibleCount(offsets, s.elapsedMs)];
  return { ...s, playing: false, elapsedMs: next ?? s.elapsedMs };
}

export function reset(s: PlayerState): PlayerState {
  return { ...s, elapsedMs: -1, playing: false };
}

export function setSpeed(s: PlayerState, speed: Speed): PlayerState {
  return { ...s, speed };
}
