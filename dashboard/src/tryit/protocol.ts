import type { TraceResult } from "../types/replay";

/** What the panel may send as customer context: every key the sample rules and facts read. */
export interface TryItContext {
  account_tenure_days?: number;
  days_since_purchase?: number;
  plan_type?: string;
}

/** The form's fields, as typed. */
export interface TryItForm {
  text: string;
  tenure: string;
  purchase: string;
  plan: string;
}

export type TryItRequest =
  | { type: "load" }
  | { type: "check"; id: number; text: string; context: TryItContext };

export type TryItMessage =
  | { type: "progress"; step: number; label: string }
  | { type: "ready" }
  | { type: "result"; id: number; result: Omit<TraceResult, "latency_ms">; ms: number }
  | { type: "error"; message: string };

/** Load steps, in order; the progress bar's maximum is their count. */
export const LOAD_STEPS = [
  "Downloading the Python runtime",
  "Loading pydantic",
  "Loading the rules",
] as const;

export const MAX_REPLY_CHARS = 2000;
export const MAX_DAYS = 36500;
export const PLANS = [
  { value: "", label: "Not given" },
  { value: "standard_2024", label: "Standard 2024" },
  { value: "premium_2024", label: "Premium 2024" },
] as const;

function days(value: string, name: string): number | undefined | { error: string } {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  if (!/^\d+$/.test(value.trim()) || parsed > MAX_DAYS) {
    return { error: `${name} is a whole number of days from 0 to ${MAX_DAYS}.` };
  }
  return parsed;
}

/** The form's values as a request, or the reason they cannot be sent. */
export function toRequest(form: TryItForm): { text: string; context: TryItContext } | { error: string } {
  const reply = form.text.trim();
  if (!reply) return { error: "Type a reply to check." };
  if (reply.length > MAX_REPLY_CHARS) return { error: `Keep the reply under ${MAX_REPLY_CHARS} characters.` };
  const tenure = days(form.tenure, "Tenure");
  if (typeof tenure === "object") return tenure;
  const purchase = days(form.purchase, "Days since purchase");
  if (typeof purchase === "object") return purchase;
  if (form.plan && !PLANS.some((p) => p.value === form.plan)) return { error: "Unknown plan." };
  const context: TryItContext = {
    ...(tenure === undefined ? {} : { account_tenure_days: tenure }),
    ...(purchase === undefined ? {} : { days_since_purchase: purchase }),
    ...(form.plan ? { plan_type: form.plan } : {}),
  };
  return { text: reply, context };
}
