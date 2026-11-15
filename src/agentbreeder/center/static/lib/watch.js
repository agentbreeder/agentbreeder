// Container events (#821, M5 console half, part 3): the words for what `agentbreeder watch`
// reported, from `GET /api/projects/ID/watch` (the server's checked copy of the newest events and
// the latest status record of the project's `watch --record` child). Pure, so `node --test` covers
// it. Nothing here touches the DOM.

import { auditTime } from "./format.js";

/** How often a visible page asks (the CLI reports events as they happen, status every 60 s). */
export const WATCH_EVERY = 5000;
/** Events shown per project on Home; the server keeps more. */
export const SHOWN = 5;

/** Which container: "postgres", or "agent northwind-support (egress)" for an agent's sidecar. */
export function sourceText(event) {
  if (!event) return "";
  if (event.source !== "agent") return String(event.name || "");
  return `agent ${event.name}${event.role ? ` (${event.role})` : ""}`;
}

/** One event in words, from its checked fields only (the server passes nothing else). */
export function eventText(event) {
  if (!event) return "";
  const what = sourceText(event);
  if (event.event === "died") {
    const detail = [typeof event.exit_code === "number" ? `exit ${event.exit_code}` : "no exit code"];
    if (event.oom) detail.push("out of memory");
    return `${what} exited by itself (${detail.join(", ")})`;
  }
  if (event.event === "unhealthy") return `${what} became unhealthy`;
  if (event.event === "restarted") return `${what} started again`;
  return what;
}

/** "error" for an exit, "warning" for a failing health check, "" for a restart. */
export function eventTone(event) {
  if (!event) return "";
  if (event.event === "died") return "error";
  if (event.event === "unhealthy") return "warning";
  return "";
}

/** The event's time in local words, or "time not reported". */
export function eventTime(event, locale) {
  return event && event.time ? auditTime(event.time, locale) : "time not reported";
}

/** The newest event id (0 for none): a change means a new event, so status is read again. */
export function newestId(watch) {
  const events = watch && watch.data && Array.isArray(watch.data.events) ? watch.data.events : [];
  return events.reduce((top, event) => (typeof event.id === "number" && event.id > top ? event.id : top), 0);
}

/** The events to show (newest first, at most `limit`) and how many more the server keeps. */
export function shownEvents(watch, limit = SHOWN) {
  const events = watch && watch.data && Array.isArray(watch.data.events) ? watch.data.events : [];
  return { events: events.slice(0, limit), more: Math.max(0, events.length - limit) };
}

/** The coded error of a watch that is not running, in words; null while it runs. */
export function watchProblem(watch) {
  if (!watch || !watch.error) return null;
  const message = watch.error.message || "The watch command failed.";
  return `${message} Container events are not being watched; it is tried again in a few seconds.`;
}

/** A line under the events: what watching means, and the status record's time. */
export function watchNote(watch, locale) {
  const data = watch && watch.data;
  if (!data) return "Starting to watch its containers…";
  const parts = [];
  if (data.running) parts.push("Watching its containers while this page is open.");
  if (data.status && data.status.time) parts.push(`Last checked ${auditTime(data.status.time, locale)}.`);
  if (data.held) parts.push(`${data.held} restart${data.held === 1 ? "" : "s"} during a Start services job not shown.`);
  return parts.join(" ");
}
