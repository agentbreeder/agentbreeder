/** Analytics seam for the conversational builder funnel.
 *
 *  `track()` is fire-and-forget: it POSTs the event to the ingest endpoint
 *  (via `ingestAnalytics`, which never throws/blocks) AND dispatches an
 *  in-page CustomEvent so live listeners (e.g. a debug panel) can observe it.
 *  PII-free: callers must never pass message/prompt bodies in `props`. */
import { ingestAnalytics } from "./api";

/** The full conversational-builder funnel taxonomy (W4). */
export const ANALYTICS_EVENTS = [
  "builder_session_started",
  "user_message_sent",
  "stack_recommended",
  "setup_card_shown",
  "setup_card_completed",
  "spec_validated",
  "eject_to_code_started",
  "coding_agent_turn",
  // Retained from the W3 eject-to-code seam (ChatBuildPanel) — not part of the
  // core funnel taxonomy but still emitted, so it stays a valid event.
  "eject_to_code_completed",
  "deploy_started",
  "deploy_succeeded",
  "deploy_failed",
  "first_invoke",
] as const;

export type AnalyticsEvent = (typeof ANALYTICS_EVENTS)[number];

export function track(event: AnalyticsEvent, props: Record<string, unknown> = {}): void {
  // Fire-and-forget ingest — best-effort, never blocks the UI.
  ingestAnalytics(event, props);
  // Dispatch an in-page event for live listeners (W4 collectors / debug panel).
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new CustomEvent("agentbreeder:analytics", { detail: { event, props } }));
  }
}
