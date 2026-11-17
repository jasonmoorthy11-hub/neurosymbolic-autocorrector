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
function type(target: string | HTMLTextAreaElement | HTMLInputElement, value: string) {
  const field =
    typeof target === "string" ? (host.querySelector(target) as HTMLTextAreaElement | HTMLInputElement) : target;
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
    const [tenure, purchase] = [...host.querySelectorAll("input")];
    type(tenure, "30");
    type(purchase, "12");
    click("Check reply");
    expect(workers[0].sent[1]).toEqual({
      type: "check",
      id: 1,
      text: "I can offer you a $75 credit.",
      context: { account_tenure_days: 30, days_since_purchase: 12 },
    });
    // aria-disabled, not disabled: the pressed button keeps keyboard focus
    expect(button("Checking…").disabled).toBe(false);
    expect(button("Checking…").getAttribute("aria-disabled")).toBe("true");
    click("Checking…"); // a second press asks nothing
    expect(workers[0].sent).toHaveLength(2);
    workers[0].say({ type: "result", id: 1, result: RESULT, ms: 4.2 });
    expect(host.textContent).toContain("CREDIT-001");
    expect(host.textContent).toContain("Sent: “I can offer you a $50 credit.”");
    expect(host.textContent).toContain("en (assumed, not detected)");
    expect(host.textContent).toContain("4.2 ms (measured in this browser");
    expect(host.textContent).toContain("account_tenure_days30");
    expect(host.textContent).toContain("days_since_purchase12");
    expect(host.querySelector('[aria-live="polite"]')?.textContent).toBe("Checked: correct.");
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
    type("input", "-3");
    click("Check reply");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("whole number of days");
    expect(workers[0].sent).toHaveLength(1);
  });

  it("keeps the form after a failed check and offers a reload after a failed load", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    type("textarea", "I can offer you a $75 credit.");
    click("Check reply");
    workers[0].say({ type: "result", id: 1, result: RESULT, ms: 1 });
    expect(host.textContent).toContain("CREDIT-001");
    type("textarea", "Hello there.");
    click("Check reply");
    workers[0].say({ type: "error", message: "The check failed on this reply." });
    expect(host.querySelector("form")).not.toBeNull();
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("The check failed on this reply.");
    // the answer to the reply before is not left standing, or announced, beside the failure
    expect(host.textContent).not.toContain("CREDIT-001");
    expect(host.querySelector('[aria-live="polite"]')?.textContent).toBe("");

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
    expect((host.querySelector("input") as HTMLInputElement).value).toBe("30");
    // An example sets every field, so nothing is left over from the one before
    click("Late refund");
    const [tenure, purchase] = [...host.querySelectorAll("input")].map((i) => i.value);
    expect([tenure, purchase]).toEqual(["", "45"]);
  });
});

describe("TryItPanel focus and failures", () => {
  it("keeps the pressed button while loading and moves focus to the reply when ready", () => {
    button("Load the checker").focus();
    click("Load the checker");
    const loadingButton = button("Loading…");
    expect(document.activeElement).toBe(loadingButton);
    expect(loadingButton.getAttribute("aria-disabled")).toBe("true");
    click("Loading…"); // a second press does nothing
    expect(workers).toHaveLength(1);
    workers[0].say({ type: "ready" });
    expect(document.activeElement).toBe(host.querySelector("textarea"));
  });

  it("shows the worker's fixed message after a failed load and leaves focus on the pressed button", () => {
    button("Load the checker").focus();
    click("Load the checker");
    workers[0].say({ type: "error", message: "The checker could not be loaded." });
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("The checker could not be loaded.");
    expect(workers[0].terminated).toBe(true);
    expect(document.activeElement).toBe(button("Try loading again"));
  });

  it("does not pull focus from somewhere else when the load ends", () => {
    const elsewhere = document.body.appendChild(document.createElement("button"));
    try {
      for (const ending of [
        { type: "ready" } as const,
        { type: "error", message: "The checker could not be loaded." } as const,
      ]) {
        button(workers.length ? "Try loading again" : "Load the checker").focus();
        click(workers.length ? "Try loading again" : "Load the checker");
        elsewhere.focus(); // the visitor moves on during the download
        workers[workers.length - 1].say(ending);
        expect(document.activeElement, ending.type).toBe(elsewhere);
        if (ending.type === "ready") act(() => workers[workers.length - 1].onerror?.({} as ErrorEvent));
        expect(document.activeElement, ending.type).toBe(elsewhere);
      }
      // and a press that never held focus (a pointer in a browser that does not focus buttons)
      act(() => (document.activeElement as HTMLElement).blur());
      click("Try loading again");
      workers[workers.length - 1].say({ type: "ready" });
      expect(document.activeElement).toBe(document.body);
    } finally {
      elsewhere.remove();
    }
  });

  it("shows the crash, not an old form error, and moves focus from the lost form to the retry", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    click("Check reply");
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("Type a reply to check.");
    (host.querySelector("textarea") as HTMLTextAreaElement).focus();
    act(() => workers[0].onerror?.({} as ErrorEvent));
    expect(host.querySelector("form")).toBeNull();
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("The checker could not be loaded.");
    expect(document.activeElement).toBe(button("Try loading again"));
  });

  it("clears an old form error when the checker is loaded again", () => {
    click("Load the checker");
    workers[0].say({ type: "ready" });
    click("Check reply");
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    act(() => workers[0].onerror?.({} as ErrorEvent));
    click("Try loading again");
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it("stops the worker when the panel goes away", () => {
    click("Load the checker");
    act(() => root.render(<span />));
    expect(workers[0].terminated).toBe(true);
  });

  it("says so when the browser cannot start a worker", () => {
    act(() => root.unmount());
    root = createRoot(host);
    act(() =>
      root.render(
        <TryItPanel
          createWorker={() => {
            throw new Error("no workers here");
          }}
        />,
      ),
    );
    click("Load the checker");
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("This browser could not start the checker.");
  });
});

describe("toRequest", () => {
  const form = { text: "Hi.", tenure: "", purchase: "", plan: "" };
  it("accepts a reply with no context", () => {
    expect(toRequest(form)).toEqual({ text: "Hi.", context: {} });
  });
  it("carries every context field the rules read", () => {
    expect(toRequest({ ...form, tenure: "0", purchase: "45", plan: "premium_2024" })).toEqual({
      text: "Hi.",
      context: { account_tenure_days: 0, days_since_purchase: 45, plan_type: "premium_2024" },
    });
  });
  it("rejects an unknown plan, fractions, negatives, words and over-long replies", () => {
    expect(toRequest({ ...form, plan: "gold" })).toEqual({ error: "Unknown plan." });
    for (const bad of ["1.5", "-3", "abc", "1e3", "99999"]) {
      expect("error" in toRequest({ ...form, tenure: bad }), bad).toBe(true);
      expect("error" in toRequest({ ...form, purchase: bad }), bad).toBe(true);
    }
    expect("error" in toRequest({ ...form, text: "x".repeat(2001) })).toBe(true);
  });
  it("takes days up to the limit, with spaces around them or a leading zero", () => {
    for (const [typed, days] of [["36500", 36500], [" 30 ", 30], ["07", 7], ["0", 0]] as const) {
      expect(toRequest({ ...form, tenure: typed, purchase: typed }), typed).toEqual({
        text: "Hi.",
        context: { account_tenure_days: days, days_since_purchase: days },
      });
    }
    for (const bad of ["36501", "+5", "1.0", "٣٠", "3 0", "9".repeat(400)]) {
      expect("error" in toRequest({ ...form, purchase: bad }), bad).toBe(true);
    }
  });
});
