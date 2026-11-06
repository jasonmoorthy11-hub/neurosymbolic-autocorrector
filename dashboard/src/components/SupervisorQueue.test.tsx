import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SupervisorQueueItem } from "../types/api";
import { SupervisorQueue } from "./SupervisorQueue";

function item(overrides: Partial<SupervisorQueueItem>): SupervisorQueueItem {
  return {
    event_id: "0532da5a",
    timestamp: "2026-11-05T12:00:00Z",
    agent_id: "agent-james",
    original_text: "I can offer you a $1,000 credit.",
    action: "escalate",
    severity: "critical",
    violations: [],
    status: "pending_review",
    decision: null,
    reviewer: null,
    comment: null,
    rejection_reason: null,
    decided_at: null,
    ...overrides,
  };
}

describe("SupervisorQueue decisions", () => {
  it("renders a pending item with approve and reject controls", () => {
    const html = renderToStaticMarkup(<SupervisorQueue queue={[item({})]} />);
    expect(html).toContain("Approve event 0532da5a");
    expect(html).toContain("Reject event 0532da5a");
    expect(html).toContain("pending review");
  });

  it("keeps the rejection reason out of the badge so neither wraps mid-pill", () => {
    const html = renderToStaticMarkup(
      <SupervisorQueue queue={[item({ status: "rejected", decision: "rejected", rejection_reason: "over_corrected" })]} />,
    );
    expect(html).toMatch(/<span class="badge badge-rejected">Rejected<\/span>/);
    expect(html).toMatch(/class="rejection-reason"[^>]*>[^<]*Over-corrected/);
    expect(html).not.toContain("Approve event");
  });

  it("renders an approved item with its badge and no reason line", () => {
    const html = renderToStaticMarkup(
      <SupervisorQueue queue={[item({ status: "approved", decision: "approved" })]} />,
    );
    expect(html).toMatch(/<span class="badge badge-approved">Approved<\/span>/);
    expect(html).not.toContain('class="rejection-reason"');
  });
});
