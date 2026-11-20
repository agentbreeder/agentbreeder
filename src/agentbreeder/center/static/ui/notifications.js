// Notifications (#827 M11, console half): the bell in the top bar, its panel ("Needs attention"
// from `next`'s conditions, "Recent events" from the stored events) and failure toasts. Every
// value shown comes from a checked server answer and goes through <Text>; a gate failure's report
// path is text, and its summary is read through the eval report viewer's own route, from the run
// and agent the server parsed out of the path (never the path itself).

import { failedCases, reportApi, reportHref } from "../lib/evals.js";
import { auditTime } from "../lib/format.js";
import { EVENTS_EVERY, MAX_TOASTS, SHOWN, bell, conditionsOf, eventTitle, newFailures, reportCommand } from "../lib/notifications.js";
import { Text, useConsole } from "./components.js";
import { html, useEffect, useRef, useState } from "./preact.js";

const NO_EVENTS = { events: null, unread: 0, error: null, loading: false };

/**
 * `GET /api/events` for the project in scope (all registered projects under All projects): on
 * mount, every EVENTS_EVERY ms while the page is visible, on window focus and when `version`
 * changes (a job ended). One read in flight per path: a trigger during it is kept and read right
 * after it ends, and an answer is applied only while its path is still the current one (a scope
 * change never gets the old scope's events). Returns [state, reload, setState, currentPath].
 */
export function useEvents(client, projectId, version, ready) {
  const [state, setState] = useState(NO_EVENTS);
  const [tick, setTick] = useState(0);
  const path = projectId ? `/api/events?project=${encodeURIComponent(projectId)}` : "/api/events";
  const current = useRef(path);
  current.current = path;
  const flight = useRef(null); // the path being read
  const pending = useRef(false); // a trigger arrived while it was read
  const mounted = useRef(true);
  useEffect(() => () => {
    mounted.current = false;
  }, []);
  useEffect(() => {
    if (!ready) return undefined;
    const again = () => {
      if (document.visibilityState === "visible") setTick((n) => n + 1);
    };
    const timer = setInterval(again, EVENTS_EVERY);
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [ready]);
  useEffect(() => {
    setState(NO_EVENTS);
  }, [projectId]);
  useEffect(() => {
    if (!ready) return;
    if (flight.current === path) {
      pending.current = true;
      return;
    }
    flight.current = path;
    pending.current = false;
    setState((previous) => ({ ...previous, loading: true }));
    const mine = () => mounted.current && current.current === path;
    client
      .request(path)
      .then((reply) => {
        if (!mine()) return;
        if (reply.data) setState({ events: reply.data.events, unread: reply.data.unread, error: null, loading: false });
        else setState((previous) => ({ ...previous, error: reply.error, loading: false }));
      })
      .catch((error) => {
        if (!mine() || error.kind === "session") return;
        setState((previous) => ({ ...previous, error: { code: error.code, message: error.message }, loading: false }));
      })
      .finally(() => {
        if (flight.current === path) flight.current = null;
        if (pending.current && mine()) {
          pending.current = false;
          setTick((n) => n + 1);
        }
      });
  }, [path, version, ready, tick]);
  return [state, () => setTick((n) => n + 1), setState, current];
}

/** The bell: one number, the split in words in its accessible name. */
export function Bell({ open, onToggle, events }) {
  const { project, guide } = useConsole();
  const { count, label } = bell({ scoped: Boolean(project), guide, events });
  return html`<button type="button" id="notifications-bell" class="pill bell" aria-label=${label} aria-expanded=${open ? "true" : "false"} aria-controls=${open ? "notifications" : undefined} onClick=${onToggle}>
    <svg class="icon" aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></svg>
    ${count ? html`<span class="bell-count" aria-hidden="true">${count}</span>` : null}
  </button>`;
}

function GateDetails({ event }) {
  const { client } = useConsole();
  const [summary, setSummary] = useState(null); // {data} | {error} | "loading"
  const gate = event.gate;
  const id = `gate-summary-${event.id}`;
  const read = async () => {
    if (summary && summary !== "loading") return setSummary(null);
    setSummary("loading");
    try {
      const reply = await client.request(reportApi(event.project_id, gate.report_run, { agent: gate.report_agent }));
      setSummary(reply.data ? { data: reply.data } : { error: reply.error });
    } catch (error) {
      if (error.kind !== "session") setSummary({ error: { code: error.code, message: error.message } });
    }
  };
  const shown = summary && summary !== "loading";
  return html`<div class="small">
    ${gate.report
      ? html`<div>Report: <code><${Text} value=${gate.report} /></code></div>`
      : html`<div class="muted">No report was saved.</div>`}
    ${gate.report_run
      ? html`<div class="row">
          <button type="button" class="action ghost small" aria-expanded=${shown ? "true" : "false"} aria-controls=${id} onClick=${read}>${summary === "loading" ? "Reading…" : "Show summary of failed cases"}</button>
          <a href=${reportHref(gate.report_run, gate.report_agent)}>Open the report</a>
        </div>`
      : gate.report ? html`<div class="muted">In a terminal: <code><${Text} value=${reportCommand(event)} /></code></div>` : null}
    <div id=${id} role="status">
      ${shown && summary.data
        ? html`<p><${Text} value=${`${summary.data.passed}/${summary.data.cases} passed. Failed: ${failedCases(summary.data).map((row) => row.id).join(", ") || "none listed"}.`} /></p>`
        : null}
      ${shown && summary.error ? html`<p class="notice error"><${Text} value=${`${summary.error.message || "The report could not be read"}${summary.error.code ? ` (${summary.error.code})` : ""}`} /></p>` : null}
    </div>
  </div>`;
}

function EventRow({ event, projectName, all }) {
  return html`<li class=${`event-row${event.read ? "" : " unread"}`}>
    <div class="row between">
      <strong>${event.read ? null : html`<span class="badge">new</span> `}<${Text} value=${eventTitle(event)} /></strong>
      <time class="muted small" datetime=${event.time}>${auditTime(event.time)}</time>
    </div>
    <div>${all ? html`<span class="muted"><${Text} value=${projectName} />: </span>` : null}<${Text} value=${event.message} /></div>
    ${event.kind === "gate_failed" ? html`<${GateDetails} event=${event} />` : null}
  </li>`;
}

/** The panel under the top bar: not modal; focus goes to its heading, Escape returns to the bell. */
export function NotificationsPanel({ events, onClose, onMarkRead }) {
  const { project, guide, projectName } = useConsole();
  const heading = useRef(null);
  const [marking, setMarking] = useState(false);
  const [said, setSaid] = useState("");
  useEffect(() => {
    if (heading.current) heading.current.focus();
    const onKey = (event) => {
      if (event.key === "Escape" && !event.defaultPrevented) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const conditions = project ? conditionsOf(guide) : null;
  const list = (events.events || []).slice(0, SHOWN);
  const unread = list.filter((e) => !e.read).map((e) => e.id);
  const mark = async () => {
    setMarking(true);
    const ok = await onMarkRead(unread);
    setMarking(false);
    setSaid(ok ? `Marked ${unread.length} ${unread.length === 1 ? "event" : "events"} as read.` : "Could not mark the events read.");
  };
  return html`<section id="notifications" class="notifications" aria-labelledby="notifications-title">
    <div class="row between">
      <h2 id="notifications-title" tabindex="-1" ref=${heading}>Notifications</h2>
      <button type="button" class="action ghost small" onClick=${onClose}>Close</button>
    </div>
    <h3>Needs attention${conditions && conditions.length ? ` (${conditions.length})` : ""}</h3>
    ${!project
      ? html`<p class="muted">Choose a project in the Project scope menu to see what needs attention.</p>`
      : conditions === null
        ? html`<p class="muted" role="status">${guide.error ? html`Not known: <${Text} value=${guide.error.message || guide.error.code || "the checks did not answer"} />` : "Checking…"}</p>`
        : conditions.length
          ? html`${guide.stale ? html`<p class="muted small">Last known; checked again when the running job ends.</p>` : null}
            <ul class="plain conditions">${conditions.map((c, index) => html`<li key=${index}><code><${Text} value=${c.code} /></code> <${Text} value=${c.message} /></li>`)}</ul>
            <p class="small"><a href="#/" onClick=${onClose}>Open Home</a> for what to do next.</p>`
          : html`<p class="muted">Nothing needs attention.</p>`}
    <h3>Recent events${events.unread ? ` (${events.unread} new)` : ""}</h3>
    ${events.error && !events.events ? html`<p class="notice error" role="status"><${Text} value=${`${events.error.message || "The events could not be read"}${events.error.code ? ` (${events.error.code})` : ""}`} /></p>` : null}
    ${events.events === null && !events.error ? html`<p class="muted">Reading the events…</p>` : null}
    ${events.events && !list.length ? html`<p class="muted">No events stored.</p>` : null}
    ${list.length ? html`<ul class="plain events">${list.map((e) => html`<${EventRow} key=${e.id} event=${e} all=${!project} projectName=${projectName(e.project_id)} />`)}</ul>` : null}
    ${list.length
      ? html`<button type="button" class="action secondary small" aria-disabled=${unread.length && !marking ? undefined : "true"} onClick=${() => unread.length && !marking && mark()}>${marking ? "Marking…" : unread.length ? "Mark all read" : "All read"}</button>`
      : null}
    <p class="visually-hidden" role="status">${said}</p>
    <p class="muted small">Failed jobs, eval gate failures and containers that exited by themselves or became unhealthy. Stored on this machine only; the newest 500 are kept.</p>
  </section>`;
}

/**
 * Failure toasts: a permanent region; each toast is `role="alert"` when it is added, never takes
 * focus and never times out. At most MAX_TOASTS; the rest are counted.
 */
export function Toasts({ events, onOpen }) {
  const { projectName, project } = useConsole();
  const [shown, setShown] = useState([]);
  const tracking = useRef({ baseline: null, toasted: new Set() });
  useEffect(() => {
    tracking.current = { baseline: null, toasted: new Set() };
    setShown([]);
  }, [project ? project.id : null]);
  useEffect(() => {
    if (!events.events) return;
    const { toast, state } = newFailures({ events: events.events }, tracking.current);
    tracking.current = state;
    if (toast.length) setShown((previous) => [...previous, ...toast]);
  }, [events.events]);
  // A toast whose event is now read (Mark all read, another tab) goes away.
  const unread = new Set((events.events || []).filter((e) => !e.read).map((e) => e.id));
  const live = events.events ? shown.filter((e) => unread.has(e.id)) : shown;
  const region = useRef(null);
  const dismiss = (id) => {
    setShown((previous) => previous.filter((e) => e.id !== id));
    // The focused button goes with its toast: focus moves to the next toast, else the bell.
    requestAnimationFrame(() => {
      const next = region.current && region.current.querySelector(".toast button");
      (next || document.getElementById("notifications-bell"))?.focus();
    });
  };
  const visibleToasts = live.slice(-MAX_TOASTS);
  const more = live.length - visibleToasts.length;
  return html`<div class="toasts" role="region" aria-label="Failure notifications" ref=${region}>
    ${visibleToasts.map((event) => html`<div class="toast" role="alert" key=${event.id}>
      <strong><${Text} value=${eventTitle(event)} /></strong>
      <p>${project ? null : html`<span class="muted"><${Text} value=${projectName(event.project_id)} />: </span>`}<${Text} value=${event.message} /></p>
      <div class="row">
        <button type="button" class="action secondary small" onClick=${() => { setShown((previous) => previous.filter((e) => e.id !== event.id)); onOpen(); }}>Open notifications</button>
        <button type="button" class="action ghost small" aria-label=${`Dismiss: ${eventTitle(event)}`} onClick=${() => dismiss(event.id)}>Dismiss</button>
      </div>
    </div>`)}
    ${more > 0 ? html`<p class="toast muted small">${more} more ${more === 1 ? "failure" : "failures"} in Notifications.</p>` : null}
  </div>`;
}
