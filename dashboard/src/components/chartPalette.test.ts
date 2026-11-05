// The node types reference is for readFileSync below; tsconfig.app.json lists only
// vite/client, and this directive adds node's globals to the whole app program.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ACTION_FILL, INTENT_SLOTS, STAT_FILL, VIOLATION_FILL, intentFill } from "./chartPalette";
import recorded from "../../public/replay/replay.json";
import { deriveMetrics, isReplayFile, toCorrectionEvent } from "../replay/derive";
import analyticsSrc from "./AnalyticsPanel.tsx?raw";
import eventChartSrc from "./EventChart.tsx?raw";
import metricsSrc from "./MetricsPanel.tsx?raw";

const SOURCES: Record<string, string> = {
  "EventChart.tsx": eventChartSrc,
  "AnalyticsPanel.tsx": analyticsSrc,
  "MetricsPanel.tsx": metricsSrc,
};

const TOKEN = /^var\(--chart-[a-z0-9-]+\)$/;

describe("chart palette tokens", () => {
  it("action and violation fills are theme tokens, not hex literals", () => {
    for (const fill of [...Object.values(ACTION_FILL), ...Object.values(STAT_FILL), VIOLATION_FILL]) {
      expect(fill).toMatch(TOKEN);
    }
  });

  it("the same intent always gets the same token", () => {
    expect(intentFill("troubleshooting")).toBe(intentFill("troubleshooting"));
    expect(intentFill("general")).toMatch(TOKEN);
  });

  it("mapped intents get distinct tokens regardless of order or count", () => {
    const names = Object.keys(INTENT_SLOTS);
    expect(names.length).toBe(8);
    const fills = names.map(intentFill);
    expect(new Set(fills).size).toBe(names.length);
    // colour follows the entity: a subset keeps its colours
    expect(intentFill(names[3])).toBe(fills[3]);
  });

  it("unmapped intents fold into the 'other' slot", () => {
    expect(intentFill("cancellation")).toBe("var(--chart-series-other)");
    expect(intentFill("something_new")).toBe("var(--chart-series-other)");
  });

  it("the recorded demo pie walks slots 1..n in order, so its neighbours are validated adjacent pairs", () => {
    // Same ordering as useReplay at 25/25: every scenario, newest first.
    const file: unknown = recorded;
    if (!isReplayFile(file)) throw new Error("replay.json does not validate");
    const events = file.scenarios.map((s) => toCorrectionEvent(s, file.recorded_at)).reverse();
    const names = Object.keys(deriveMetrics(events).events_by_intent);
    expect(
      names.map((n) => [n, INTENT_SLOTS[n]]),
      "replay intent order changed: re-order INTENT_SLOTS to match it and re-validate the adjacent pairs",
    ).toEqual(names.map((n, i) => [n, i + 1]));
  });
});

describe("chart components carry no hex literals", () => {
  it.each(Object.keys(SOURCES))("%s", (file) => {
    expect(SOURCES[file], file).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe("theme.css defines every chart token in both themes", () => {
  // vitest replaces CSS imports (even `?raw`) with an empty module, so read the file.
  const themeCss = readFileSync(new URL("../theme.css", import.meta.url), "utf8");
  const [dark, light] = themeCss.split(':root[data-theme="light"]');
  const used = new Set<string>();
  for (const fill of [
    ...Object.values(ACTION_FILL),
    ...Object.values(STAT_FILL),
    VIOLATION_FILL,
    ...Object.keys(INTENT_SLOTS).map(intentFill),
    intentFill("other"),
  ]) {
    used.add(fill.slice("var(".length, -1));
  }

  it("has a light theme block", () => {
    expect(light, "light theme block not found").toBeDefined();
  });

  it.each([...used])("%s", (token) => {
    const decl = new RegExp(`${token}:\\s*#[0-9a-fA-F]{6};`);
    expect(dark, "dark block").toMatch(decl);
    expect(light, "light block").toMatch(decl);
  });
});
