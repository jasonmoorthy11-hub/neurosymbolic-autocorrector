import type { TraceResult } from "../types/replay";

/** What the panel may send as customer context (the keys the sample rules read). */
export interface TryItContext {
  account_tenure_days?: number;
  plan_type?: string;
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
export const MAX_TENURE_DAYS = 36500;
export const PLANS = [
  { value: "", label: "Not given" },
  { value: "standard_2024", label: "Standard 2024" },
  { value: "premium_2024", label: "Premium 2024" },
] as const;

/** The form's values as a request, or the reason they cannot be sent. */
export function toRequest(
  text: string,
  tenure: string,
  plan: string,
): { text: string; context: TryItContext } | { error: string } {
  const reply = text.trim();
  if (!reply) return { error: "Type a reply to check." };
  if (reply.length > MAX_REPLY_CHARS) return { error: `Keep the reply under ${MAX_REPLY_CHARS} characters.` };
  const context: TryItContext = {};
  if (tenure.trim()) {
    const days = Number(tenure);
    if (!Number.isInteger(days) || days < 0 || days > MAX_TENURE_DAYS) {
      return { error: `Tenure is a whole number of days from 0 to ${MAX_TENURE_DAYS}.` };
    }
    context.account_tenure_days = days;
  }
  if (plan) {
    if (!PLANS.some((p) => p.value === plan)) return { error: "Unknown plan." };
    context.plan_type = plan;
  }
  return { text: reply, context };
}
