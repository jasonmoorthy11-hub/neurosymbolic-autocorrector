import { describe, expect, it } from "vitest";
import { RefreshGate } from "./refreshGate";

describe("RefreshGate", () => {
  it("lets the first refresh through and blocks a second one inside the gap", () => {
    const gate = new RefreshGate(2000);
    expect(gate.allow(10_000)).toBe(true);
    expect(gate.allow(11_000)).toBe(false);
    expect(gate.allow(11_999)).toBe(false);
  });

  it("opens again once the gap has passed, measured from the last allowed refresh", () => {
    const gate = new RefreshGate(2000);
    expect(gate.allow(10_000)).toBe(true);
    expect(gate.allow(11_500)).toBe(false); // blocked calls do not move the clock
    expect(gate.allow(12_000)).toBe(true);
    expect(gate.allow(13_999)).toBe(false);
    expect(gate.allow(14_000)).toBe(true);
  });

  it("reports how long a blocked caller must wait, and zero once the gate is open", () => {
    const gate = new RefreshGate(2000);
    expect(gate.remainingMs(10_000)).toBe(0); // never allowed anything yet
    expect(gate.allow(10_000)).toBe(true);
    expect(gate.remainingMs(10_500)).toBe(1500);
    expect(gate.remainingMs(12_000)).toBe(0);
    expect(gate.remainingMs(15_000)).toBe(0);
  });
});
