import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CorrectionEvent } from "../types/api";
import { AgentFeed } from "./AgentFeed";

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

const count = (html: string, needle: string) => html.split(needle).length - 1;

describe("AgentFeed before/after rendering", () => {
  it("shows the original struck through and the corrected text when they differ", () => {
    const html = renderToStaticMarkup(
      <AgentFeed events={[event({ original_text: "a $200 credit", corrected_text: "a $50 credit" })]} />,
    );
    expect(html).toContain('class="original-text"');
    expect(html).toContain("a $200 credit");
    expect(html).toContain("a $50 credit");
  });

  it("does not strike through text identical to the correction (the audit log stores PII redacted)", () => {
    const text = "Your SSN [SSN REDACTED] has been verified.";
    const html = renderToStaticMarkup(
      <AgentFeed events={[event({ original_text: text, corrected_text: text, pii_detected: true })]} />,
    );
    expect(html).not.toContain('class="original-text"');
    expect(count(html, text)).toBe(1);
    expect(html).toMatch(/redacted before delivery/i);
  });

  it("says so when a modified event has no visible change and no PII", () => {
    const html = renderToStaticMarkup(
      <AgentFeed events={[event({ original_text: "same", corrected_text: "same" })]} />,
    );
    expect(count(html, ">same<")).toBe(1);
    expect(html).toMatch(/no visible text change/i);
  });

  it("shows an escalation's text once, with no corrected line", () => {
    const html = renderToStaticMarkup(
      <AgentFeed events={[event({ action: "escalate", original_text: "a $750 credit", corrected_text: "a $750 credit" })]} />,
    );
    expect(count(html, "a $750 credit")).toBe(1);
    expect(html).not.toContain('class="corrected-text"');
  });
});
