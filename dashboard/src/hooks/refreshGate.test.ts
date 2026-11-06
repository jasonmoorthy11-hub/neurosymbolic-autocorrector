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
});
