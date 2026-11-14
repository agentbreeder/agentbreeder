// The app shell: skip link, top bar (scope switcher, activity pill, user menu), sidebar of the
// screens that work, the main region with the route's h1, and the job dock.

import { jobLabel, jobOutcome } from "../lib/format.js";
import { href, routeTitle } from "../lib/router.js";
import { visible } from "../lib/text.js";
import { ROUTES } from "../screens/index.js";
import { Action, Text, useConfirm, useConsole } from "./components.js";
import { JobResult, NextPill } from "./guide.js";
import { html, useEffect, useLayoutEffect, useRef, useState } from "./preact.js";

export function Shell({ match }) {
  const { project, dockOpen, setDockOpen } = useConsole();
  const route = match ? match.route : null;
  const h1 = useRef(null);
  const previous = useRef(null);
  // What moves focus to the h1: a route with `section` (one screen whose parameter is a picker,
  // like Logs) only when the section changes; any other route on a change of its parameters too,
  // so list → detail and name → name move focus.
  const path = route ? route.section || `${route.pattern} ${JSON.stringify(match.params || {})}` : "missing";
  // The sidebar link kept current: a detail route without a nav entry names its list's pattern
  // as `navAs`.
  const section = route ? route.navAs || route.section || route.pattern : null;
  const projectId = project ? project.id : null;
  useEffect(() => {
    // A route change moves focus to the new h1, so keyboard and screen-reader users land on it.
    // Not on first load (the page starts at its top as usual), and not on a scope change made
    // from the switcher, which keeps its focus; only when that change unmounted the focused
    // control (a "Choose a project" button) does focus go to the h1 instead of being lost.
    const before = previous.current;
    previous.current = { path, projectId };
    if (!before || !h1.current) return;
    const active = document.activeElement;
    const lost = !active || active === document.body;
    // Focus being used in the top bar (scope switcher, pill, menu) is never taken away.
    const inTopBar = Boolean(active && active.closest && active.closest("header.top"));
    if (inTopBar) return;
    if (before.path !== path || (before.projectId !== projectId && lost)) h1.current.focus();
  }, [path, projectId]);
  const Screen = route && route.screen;
  const needsProject = route && route.scoped && !project;
  return html`
    <a class="skip" href="#main" onClick=${(event) => { event.preventDefault(); h1.current && h1.current.focus(); }}>Skip to content</a>
    <${TopBar} />
    <div class="layout">
      <${Sidebar} current=${section} />
      <main id="main">
        <h1 tabindex="-1" ref=${h1}>${route ? routeTitle(route, match.params) : "Not found"}${route && route.scoped && project ? html` <span class="scope-chip"><${Text} value=${`${project.name} · ${project.environment}`} /></span>` : null}</h1>
        ${!route ? html`<p>There is no screen at this address. <a href="#/">Go to Home</a>.</p>` : needsProject ? html`<${ChooseProject} />` : html`<${Screen} project=${project} params=${match.params} key=${project ? project.id : "all"} />`}
      </main>
    </div>
    ${dockOpen
      ? html`<${Dock} onClose=${() => {
          setDockOpen(false);
          requestAnimationFrame(() => document.getElementById("activity-pill")?.focus());
        }} />`
      : null}
  `;
}

function TopBar() {
  const { overview, projects, project, setScope, jobs, dockOpen, setDockOpen } = useConsole();
  const running = jobs.running().length;
  const followed = jobs.list().length;
  return html`<header class="top">
    <div class="brand">
      <strong>AgentBreeder console</strong>
      <span class="muted small">preview <${Text} value=${overview ? overview.version : ""} />, this machine only</span>
    </div>
    <div class="top-controls">
      <label class="scope">
        <span class="visually-hidden">Project scope</span>
        <select aria-label="Project scope" onChange=${(event) => setScope(event.currentTarget.value || null)}>
          <option value="" selected=${!project}>All projects</option>
          ${projects.map((p) => html`<option value=${p.id} selected=${project && project.id === p.id}>${visible(`${p.name} (${p.environment})`)}</option>`)}
        </select>
      </label>
      <${NextPill} />
      <button type="button" id="activity-pill" class=${`pill${running ? " running" : ""}`} aria-expanded=${dockOpen ? "true" : "false"} aria-controls=${dockOpen ? "dock" : undefined} onClick=${() => setDockOpen(!dockOpen)}>
        ${running ? `${running} running` : followed ? "Jobs" : "No jobs running"}
      </button>
      <${UserMenu} />
    </div>
  </header>`;
}

/** The user menu: a disclosure that also closes on Escape (focus back to it) and outside clicks. */
function UserMenu() {
  const { theme, setTheme, signOut } = useConsole();
  const menu = useRef(null);
  useEffect(() => {
    const onPointer = (event) => {
      if (menu.current && menu.current.open && !menu.current.contains(event.target)) menu.current.open = false;
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, []);
  const onKey = (event) => {
    if (event.key === "Escape" && menu.current.open) {
      menu.current.open = false;
      menu.current.querySelector("summary").focus();
    }
  };
  return html`<details class="menu" ref=${menu} onKeyDown=${onKey}>
    <summary>Owner<span class="visually-hidden"> menu</span></summary>
    <div class="menu-body" role="group" aria-label="User menu">
      <p class="muted small">Signed in on this tab with a one-time link from the terminal.</p>
      <label for="theme">Theme</label>
      <select id="theme" onChange=${(event) => setTheme(event.currentTarget.value)}>
        ${["system", "light", "dark"].map((value) => html`<option value=${value} selected=${theme === value}>${value[0].toUpperCase() + value.slice(1)}</option>`)}
      </select>
      <button type="button" class="action secondary small" onClick=${signOut}>Sign out of this tab</button>
    </div>
  </details>`;
}

function Sidebar({ current }) {
  const groups = [];
  for (const route of ROUTES.filter((r) => r.nav)) {
    const name = route.group || "";
    let group = groups.find((g) => g.name === name);
    if (!group) groups.push((group = { name, routes: [] }));
    group.routes.push(route);
  }
  return html`<nav class="sidebar" aria-label="Console">
    ${groups.map((group) => html`<div class="nav-group">
      ${group.name ? html`<p class="nav-heading">${group.name}</p>` : null}
      <ul>${group.routes.map((route) => html`<li><a href=${href(route.pattern)} aria-current=${current === route.pattern ? "page" : undefined}>${route.nav}</a></li>`)}</ul>
    </div>`)}
  </nav>`;
}

function ChooseProject() {
  const { projects, setScope } = useConsole();
  if (!projects.length) return html`<p>This screen works on one project. <a href="#/">Add a project on Home</a> first.</p>`;
  return html`<p>This screen works on one project. Choose one:</p>
    <ul class="plain choose">${projects.map((p) => html`<li><button type="button" class="action secondary" onClick=${() => setScope(p.id)}><${Text} value=${`${p.name} (${p.environment})`} /></button></li>`)}</ul>`;
}

/** The dock: every followed job. A finished job collapses to its one-line result. */
function Dock({ onClose }) {
  const { jobs, projectName, project } = useConsole();
  const [dialog, confirm] = useConfirm();
  const entries = jobs.list();
  // The guided path's result row goes on the newest finished job of the project in scope (the
  // guide is read for that project only, again when the job ends), and only while no job runs
  // there: until that one ends, the guide does not reflect it.
  const latest = project && !jobs.runningIn(project.id) ? entries.filter((e) => e.project === project.id && e.state !== "running").sort((a, b) => b.started - a.started)[0] : null;
  const stopNow = async (entry) => {
    if (await confirm({ title: "Stop the job now?", text: "Cancel has not ended it. Stopping ends every process it started, and may skip restoring the previous production: check Deployments afterwards.", confirm: "Stop now" })) jobs.stop(entry.id);
  };
  return html`<aside id="dock" class="dock" aria-label="Jobs">
    ${dialog}
    <div class="row between"><h2 id="dock-title" tabindex="-1">Jobs</h2><button type="button" class="action ghost small" onClick=${onClose}>Close</button></div>
    ${entries.length ? null : html`<p class="muted">No jobs yet in this tab. Closing this panel never stops a job.</p>`}
    ${entries.map((entry) => {
      const outcome = jobOutcome(entry);
      return html`<section class=${`job ${entry.state}`} key=${entry.id} aria-label=${`${projectName(entry.project)} ${jobLabel(entry.kind)}`}>
        <div class="row between">
          <strong>${projectName(entry.project)}${projectName(entry.project) ? ": " : ""}${jobLabel(entry.kind)}</strong>
          <span class="muted small"><${Text} value=${String(entry.state).replace("_", " ")} /></span>
        </div>
        ${outcome ? html`<p class=${`outcome ${outcome.tone}`}><${Text} value=${outcome.text} /></p>` : null}
        ${entry.error ? html`<p class="muted small" role="status"><${Text} value=${entry.error} /></p>` : null}
        ${entry === latest ? html`<${JobResult} />` : null}
        <div class="row">
          ${entry.state === "running" ? jobControl(entry, jobs, stopNow) : null}
          <button type="button" class="action ghost small" aria-expanded=${entry.collapsed ? "false" : "true"} onClick=${() => jobs.toggle(entry.id)}>${entry.collapsed ? "Show log" : "Hide log"}</button>
          ${entry.state !== "running" ? html`<button type="button" class="action ghost small" onClick=${() => jobs.dismiss(entry.id)}>Dismiss</button>` : null}
        </div>
        ${entry.collapsed ? null : html`<${JobLog} entry=${entry} />`}
      </section>`;
    })}
  </aside>`;
}

// Cancel sends one SIGINT (release and promote then restore), so it is offered once. If the job
// is still running long after, the server allows a confirmed stop of its process group.
function jobControl(entry, jobs, stopNow) {
  if (!entry.cancelling && !entry.cancelAsked) return html`<${Action} small tone="secondary" onClick=${() => jobs.cancel(entry.id)}>Cancel<//>`;
  if (!entry.canStop) return html`<span class="muted small" role="status">Cancelling…</span>`;
  return html`<${Action} small tone="danger" onClick=${() => stopNow(entry)}>Stop now<//>`;
}

/** Append-only: each line is a keyed row, so new lines add rows and old rows are kept. */
function JobLog({ entry }) {
  const box = useRef(null);
  const pinned = useRef(true);
  // Keyed on the last line's seq: the count stops changing once the log reaches its cap.
  useLayoutEffect(() => {
    if (box.current && pinned.current) box.current.scrollTop = box.current.scrollHeight;
  }, [entry.lastSeq]);
  const onScroll = () => {
    const el = box.current;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };
  return html`<div class="log" role="log" aria-live="off" aria-label=${`${jobLabel(entry.kind)} log`} tabindex="0" ref=${box} onScroll=${onScroll}>
    ${entry.dropped ? html`<div class="muted small">${entry.dropped} earlier lines are not shown.</div>` : null}
    ${entry.lines.map((line) => html`<div class="line" key=${line.seq}><${Text} value=${line.text} /></div>`)}
  </div>`;
}

/** Not signed in, or the session ended. Re-auth returns to the screen you were on. */
export function SignedOut({ reason }) {
  return html`<main id="main" class="center-page">
    <h1 tabindex="-1">${reason === "ended" ? "Session ended" : "Sign in"}</h1>
    <p>${reason === "ended" ? "This tab's session ended (8 hours, or 1 hour idle)." : reason === "used" ? "This link was already used or has expired." : "There is no session in this tab."}</p>
    <p class="muted">In the terminal running <code>agentbreeder up</code>, press Enter for a new one-time link. It brings you back to this screen.</p>
  </main>`;
}

/** No HTTP answer at all: the console stopped, or this is not its address. */
export function Unreachable({ onRetry }) {
  return html`<main id="main" class="center-page">
    <h1 tabindex="-1">Console not reachable at this address</h1>
    <p>Nothing answered at ${location.host}. The console may have stopped, or it is running on another port.</p>
    <p class="muted">Start it with <code>agentbreeder up</code>; it prints its address. Trying again every 5 seconds.</p>
    <button type="button" class="action secondary" onClick=${onRetry}>Try now</button>
  </main>`;
}
