/** Minimal analytics seam (W3). A full funnel dashboard lands in W4.
 *  track() dispatches a CustomEvent so a listener (or W4 collector) can consume it. */
export type AnalyticsEvent =
  | "eject_to_code_started"
  | "coding_agent_turn"
  | "eject_to_code_completed"
  | "deploy_started"
  | "deploy_succeeded";

export function track(event: AnalyticsEvent, props: Record<string, unknown> = {}): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  window.dispatchEvent(new CustomEvent("agentbreeder:analytics", { detail: { event, props } }));
}
