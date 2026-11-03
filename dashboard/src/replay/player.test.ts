import { describe, expect, it } from "vitest";
import { initialPlayer, play, reset, setSpeed, step, tick, visibleCount } from "./player";

const OFFSETS = [0, 2500, 5000];

describe("player", () => {
  it("starts with nothing visible, playing unless motion is reduced", () => {
    expect(visibleCount(OFFSETS, initialPlayer(false).elapsedMs)).toBe(0);
    expect(initialPlayer(false).playing).toBe(true);
    expect(initialPlayer(true).playing).toBe(false);
  });

  it("reveals scenarios as replay time passes, scaled by speed", () => {
    let s = tick(initialPlayer(false), 100, OFFSETS);
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(1);
    s = tick(s, 2400, OFFSETS);
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(2);
    s = tick(setSpeed(s, 4), 600, OFFSETS); // 2500 + 2400
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(2);
    s = tick(s, 25, OFFSETS); // + 100 at 4x
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(3);
  });

  it("does not advance while paused", () => {
    const s = tick(initialPlayer(true), 10_000, OFFSETS);
    expect(s.elapsedMs).toBe(initialPlayer(true).elapsedMs);
  });

  it("stops at the end", () => {
    const s = tick(initialPlayer(false), 60_000, OFFSETS);
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(3);
    expect(s.playing).toBe(false);
  });

  it("steps one scenario at a time and pauses", () => {
    let s = step(initialPlayer(false), OFFSETS);
    expect(s.playing).toBe(false);
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(1);
    s = step(step(step(s, OFFSETS), OFFSETS), OFFSETS);
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(3);
  });

  it("restarts from the beginning when played at the end", () => {
    const end = tick(initialPlayer(false), 60_000, OFFSETS);
    const again = play(end, OFFSETS);
    expect(again.playing).toBe(true);
    expect(visibleCount(OFFSETS, again.elapsedMs)).toBe(0);
  });

  it("resets to the start, paused, keeping the speed", () => {
    const s = reset(setSpeed(tick(initialPlayer(false), 3000, OFFSETS), 2));
    expect(visibleCount(OFFSETS, s.elapsedMs)).toBe(0);
    expect(s.playing).toBe(false);
    expect(s.speed).toBe(2);
  });
});
