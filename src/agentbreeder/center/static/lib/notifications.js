// Notifications (#827 M11, console half; docs/superpowers/specs/2026-11-19-console-notifications-
// design.md): the bell's count and its words, and which stored events become failure toasts. Pure,
// so `node --test` covers it. Conditions come from the guide's `next --json` read (the same
// function as `agentbreeder next`); events from `GET /api/events` (the server's checked copy of
// `agentbreeder events --json`). Nothing here touches the DOM.

/** How often a visible page reads the events again (also on focus and when a job ends). */
export const EVENTS_EVERY = 20000;
/** Events listed in the panel; toasts on screen at once. */
export const SHOWN = 20;
export const MAX_TOASTS = 3;

/** The conditions of the project in scope, or null when there is no project or no answer. */
export function conditionsOf(guide) {
  const list = guide && guide.data && Array.isArray(guide.data.conditions) ? guide.data.conditions : null;
  return list ? list.filter((c) => c && typeof c.code === "string" && typeof c.message === "string") : null;
}

/** Unread events among an events answer. */
export function unreadOf(events) {
  return events && Array.isArray(events.events) ? events.events.filter((e) => !e.read).length : 0;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/**
 * The bell: one visible number (conditions plus unread events; unread events only under All
 * projects, where no conditions are read), and the split in words for its accessible name. A
 * failed or missing guide read says "not known" or "last known", never zero.
 */
export function bell({ scoped, guide, events }) {
  const conditions = scoped ? conditionsOf(guide) : null;
  const unread = unreadOf(events);
  const count = (conditions ? conditions.length : 0) + unread;
  const parts = [];
  if (scoped) {
    if (conditions === null) parts.push("what needs attention is not known yet");
    else parts.push(conditions.length ? `${conditions.length} ${conditions.length === 1 ? "needs" : "need"} attention` : "nothing needs attention");
    if (conditions !== null && guide && guide.stale) parts[0] += " (last known)";
  }
  if (events && events.error && !events.events) parts.push("events could not be read");
  else if (!events || !Array.isArray(events.events)) parts.push("unread events are not known yet");
  else parts.push(`${unread ? plural(unread, "unread event", "unread events") : "no unread events"}${events.error ? " (last known)" : ""}`);
  return { count, label: `Notifications: ${parts.join(", ")}` };
}

/**
 * The events to toast now, and the new baseline. A toast is for a failure stored after the page
 * first read the events (`baseline`, the highest id seen then), still unread, and not toasted
 * before in this tab. A highest id below the baseline means the store was deleted: the baseline
 * resets without a toast.
 */
export function newFailures(answer, state) {
  const events = answer && Array.isArray(answer.events) ? answer.events : null;
  if (!events) return { toast: [], state };
  const highest = events.reduce((max, e) => Math.max(max, e.id), 0);
  if (state.baseline === null || highest < state.baseline) return { toast: [], state: { baseline: highest, toasted: state.toasted } };
  const toast = events.filter((e) => e.id > state.baseline && !e.read && !state.toasted.has(e.id)).sort((a, b) => a.id - b.id);
  const toasted = new Set(state.toasted);
  for (const e of toast) toasted.add(e.id);
  return { toast, state: { baseline: state.baseline, toasted } };
}

/** "Eval gate failed …" and the like: the CLI's fixed-template message, and a short title. */
export function eventTitle(event) {
  if (!event) return "";
  if (event.kind === "gate_failed") return "Eval gate failed";
  if (event.kind === "job_failed") return "Job failed";
  if (event.kind === "container_died") return "Container exited";
  if (event.kind === "container_unhealthy") return "Container unhealthy";
  return "Event";
}

/** The terminal command for a report the console cannot read (not in the project's `deploy/`). */
export function reportCommand(event) {
  const gate = event && event.gate;
  if (!gate || !gate.report || gate.report_run) return "";
  return "agentbreeder evals show RUN --output DIR (the folder the report is in)";
}
