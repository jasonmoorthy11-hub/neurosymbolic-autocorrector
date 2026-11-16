// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { TryItMessage, TryItRequest } from "../tryit/protocol";
import { toRequest } from "../tryit/protocol";
import type { TryItWorker } from "../tryit/useTryIt";
import { TryItPanel } from "./TryItPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class FakeWorker implements TryItWorker {
  sent: TryItRequest[] = [];
  terminated = false;
  onmessage: ((event: MessageEvent<TryItMessage>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  postMessage(message: TryItRequest) {
    this.sent.push(message);
  }
  terminate() {
    this.terminated = true;
  }
  say(data: TryItMessage) {
    act(() => this.onmessage?.({ data } as MessageEvent<TryItMessage>));
  }
}

const RESULT = {
  detected_language: "en",
  intent: "credit_offer",
  intent_confidence: 0.6,
  pii_types: [],
  violations: [
    {
      rule_id: "CREDIT-001",
      rule_name: "Credit limit",
      description: "Credits over $50 need a supervisor",
      severity: "high" as const,
      source_document: "Credit Policy",
      source_section: "2.1",
    },
  ],
  fact_errors: [],
  edits: [],
  action: "correct" as const,
  was_modified: true,
  corrected_text: "I can offer you a $50 credit.",
  severity: "high" as const,
  heuristic_confidence: 0.92,
  guardrails_applied: [],
  explanation: null,
};

let host: HTMLDivElement;
let root: Root;
let workers: FakeWorker[];

const button = (name: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === name) as HTMLButtonElement;
const click = (name: string) => act(() => button(name).click());
function type(selector: string, value: string) {
  const field = host.querySelector(selector) as HTMLTextAreaElement | HTMLInputElement;
  const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  act(() => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  workers = [];
  act(() =>
    root.render(
      <TryItPanel
        createWorker={() => {
          workers.push(new FakeWorker());
          return workers[workers.length - 1];
        }}
      />,
    ),
  );
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("TryItPanel", () => {
  it("starts no worker and shows no form until the button is pressed", () => {
    expect(workers).toHaveLength(0);
    expect(host.querySelector("form")).toBeNull();
    expect(host.textContent).toContain("nothing you type is sent anywhere");
    expect(host.textContent).toContain("English only");
  });

  it("shows each load step, then the form", () => {
    click("Load the checker");
    expect(workers[0].sent).toEqual([{ type: "load" }]);
    workers[0].say({ type: "progress", step: 2, label: "Loading pydantic" });
    expect(host.querySelector("label")?.textContent).toContain("Loading pydantic… (step 2 of 3)");
    expect(host.querySelector("progress")).not.toBeNull();
    workers[0].say({ type: "ready" });
    expect(host.querySelector("progress")).toBeNull();
    expect(host.querySelector("form")).not.toBeNull();
  });

  it("sends the typed reply with its context and shows the trace of the answer", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    type("textarea", "  I can offer you a $75 credit.  ");
    type('input[type="number"]', "30");
    click("Check reply");
    expect(workers[0].sent[1]).toEqual({
      type: "check",
      id: 1,
      text: "I can offer you a $75 credit.",
      context: { account_tenure_days: 30 },
    });
    expect(button("Checking…").disabled).toBe(true);
    workers[0].say({ type: "result", id: 1, result: RESULT, ms: 4.2 });
    expect(host.textContent).toContain("CREDIT-001");
    expect(host.textContent).toContain("Sent: “I can offer you a $50 credit.”");
    expect(host.textContent).toContain("en (assumed, not detected)");
    expect(host.textContent).toContain("4.2 ms (measured in this browser");
    expect(host.textContent).toContain("account_tenure_days30");
  });

  it("ignores an answer to an earlier request", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    type("textarea", "Hello there.");
    click("Check reply");
    workers[0].say({ type: "result", id: 99, result: RESULT, ms: 1 });
    expect(host.textContent).not.toContain("CREDIT-001");
  });

  it("refuses an empty reply and a bad tenure without asking the worker", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    click("Check reply");
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("Type a reply to check.");
    type("textarea", "Hello there.");
    type('input[type="number"]', "-3");
    click("Check reply");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("whole number of days");
    expect(workers[0].sent).toHaveLength(1);
  });

  it("keeps the form after a failed check and offers a reload after a failed load", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    type("textarea", "Hello there.");
    click("Check reply");
    workers[0].say({ type: "error", message: "The check failed on this reply." });
    expect(host.querySelector("form")).not.toBeNull();
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("The check failed on this reply.");

    act(() => workers[0].onerror?.({} as ErrorEvent));
    expect(workers[0].terminated).toBe(true);
    expect(host.querySelector("form")).toBeNull();
    click("Try loading again");
    expect(workers).toHaveLength(2);
  });

  it("fills the form from an example", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    click("Credit over the limit");
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toContain("$75 credit");
    expect((host.querySelector('input[type="number"]') as HTMLInputElement).value).toBe("30");
  });
});

describe("toRequest", () => {
  it("accepts a reply with no context", () => {
    expect(toRequest("Hi.", "", "")).toEqual({ text: "Hi.", context: {} });
  });
  it("carries a known plan and rejects an unknown one", () => {
    expect(toRequest("Hi.", "0", "premium_2024")).toEqual({
      text: "Hi.",
      context: { account_tenure_days: 0, plan_type: "premium_2024" },
    });
    expect(toRequest("Hi.", "", "gold")).toEqual({ error: "Unknown plan." });
  });
  it("rejects fractions, negatives and over-long replies", () => {
    expect("error" in toRequest("Hi.", "1.5", "")).toBe(true);
    expect("error" in toRequest("Hi.", "99999", "")).toBe(true);
    expect("error" in toRequest("x".repeat(2001), "", "")).toBe(true);
  });
});
