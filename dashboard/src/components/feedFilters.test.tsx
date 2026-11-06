import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReplayContext, type ReplayValue } from "../replay/context";
import type { CorrectionEvent } from "../types/api";
import { LiveFeed } from "./LiveFeed";
import { ReplayFeed } from "./ReplayFeed";

function event(overrides: Partial<CorrectionEvent>): CorrectionEvent {
  return {
    event_id: "e1",
    timestamp: "2026-11-05T12:00:00Z",
    agent_id: "agent-mike",
    session_id: "s1",
    original_text: "before",
    corrected_text: "after",
    was_modified: true,
    action: "correct",
    intent_type: "general",
    confidence: 0.92,
    violations: [],
    pii_detected: false,
    latency_ms: 12,
    severity: "low",
    ...overrides,
  };
}

const EVENTS = [
  event({ event_id: "e1", action: "escalate", original_text: "escalated text", corrected_text: "escalated text" }),
  event({ event_id: "e2", action: "correct", original_text: "corrected text before", corrected_text: "corrected text after" }),
  event({ event_id: "e3", action: "allow", intent_type: "troubleshooting", original_text: "allowed text", corrected_text: "allowed text", was_modified: false }),
];

const noop = () => {};
const noopAsync = async () => {};

function replayValue(events: CorrectionEvent[]): ReplayValue {
  return {
    file: null,
    error: null,
    loading: false,
    reload: noop,
    lane: "deterministic",
    setLane: noop,
    player: { playing: false, speed: 1 },
    total: events.length,
    visible: [],
    events,
    metrics: null,
    queue: [],
    rules: [],
    trends: [],
    summary: null,
    selected: null,
    following: true,
    select: noop,
    play: noop,
    pause: noop,
    step: noop,
    reset: noop,
    setSpeed: noop,
    approve: noopAsync,
    reject: noopAsync,
    exportEvents: noopAsync,
  };
}

describe("the filter bar narrows the feed", () => {
  it("LiveFeed shows only events matching the action filter", () => {
    const html = renderToStaticMarkup(<LiveFeed events={EVENTS} filters={{ action: "escalate" }} />);
    expect(html).toContain("escalated text");
    expect(html).not.toContain("corrected text");
    expect(html).not.toContain("allowed text");
  });

  it("LiveFeed shows everything with no filter", () => {
    const html = renderToStaticMarkup(<LiveFeed events={EVENTS} filters={{}} />);
    expect(html).toContain("escalated text");
    expect(html).toContain("corrected text after");
    expect(html).toContain("allowed text");
  });

  it("ReplayFeed applies the same filter to the recorded events", () => {
    const html = renderToStaticMarkup(
      <ReplayContext.Provider value={replayValue(EVENTS)}>
        <ReplayFeed filters={{ intent_type: "troubleshooting" }} />
      </ReplayContext.Provider>,
    );
    expect(html).toContain("allowed text");
    expect(html).not.toContain("escalated text");
    expect(html).not.toContain("corrected text");
  });
});
