// The AgentBreeder console. Server and agent text is only ever placed as text (Preact text nodes,
// through <Text>); the CSP requires Trusted Types and allows no policy, so an HTML sink would throw.

import { createClient } from "./lib/api.js";
import { jobLabel, jobOutcome } from "./lib/format.js";
import { createJobs } from "./lib/jobs.js";
import { createRouter, pathFrom, titleFor, tokenFrom } from "./lib/router.js";
import { visible } from "./lib/text.js";
import { ROUTES } from "./screens/index.js";
import { Console, Text } from "./ui/components.js";
import { html, render, useEffect, useReducer, useRef, useState } from "./ui/preact.js";
import { Shell, SignedOut, Unreachable } from "./ui/shell.js";

const router = createRouter(ROUTES);
const RETURN_TO = /^#\/[A-Za-z0-9/._-]{0,200}$/;

// Per-tab storage (sessionStorage, gone with the tab); memory if storage is unavailable.
const memory = {};
const tab = {
  get(key) {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return memory[key] ?? null;
    }
  },
  set(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch {
      if (value === null) delete memory[key];
      else memory[key] = value;
    }
  },
};
const prefs = {
  get(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* a preference only */
    }
  },
};

// Phase changes from outside Preact (a 401 or no answer from any request) go through this hook.
const bus = { phase: () => {} };
const session = { get: () => tab.get("console-session"), set: (value) => tab.set("console-session", value) };

function rememberRoute() {
  if (RETURN_TO.test(location.hash)) tab.set("console-return", location.hash);
}

const base = createClient({
  fetchImpl: (...args) => fetch(...args),
  session,
  onSessionEnded: () => {
    rememberRoute();
    session.set(null);
    bus.phase({ name: "signed-out", reason: "ended" });
  },
});
const client = {
  ...base,
  async request(path, options) {
    try {
      return await base.request(path, options);
    } catch (error) {
      if (error.kind === "unreachable") bus.phase({ name: "unreachable" });
      throw error;
    }
  },
};
client.get = async (path) => (await client.request(path)).data;
client.post = async (path, body = {}) => (await client.request(path, { body })).data;

const jobs = createJobs({ client });

function applyTheme(theme) {
  const dark = theme === "dark" || (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

function announce(text) {
  const region = document.getElementById("announce");
  if (region) region.textContent = text;
}

const NO_GUIDE = { loading: false, data: null, error: null, stale: false };

/**
 * `next --json` for the project in scope (#820), read once per scope, again when one of its
 * jobs ends (its version changes), when the window gets focus back (most fixes are done in a
 * terminal) and on "Check again". The checklist, the pill, empty states and job result rows
 * share this one read, so `next` runs once per change, not once per surface.
 * Returns [guide, reload].
 */
function useGuide(projectId, version, ready) {
  const [guide, setGuide] = useState(NO_GUIDE);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!projectId || !ready) return undefined;
    const onFocus = () => setTick((n) => n + 1);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [projectId, ready]);
  useEffect(() => {
    if (!projectId || !ready) {
      setGuide(NO_GUIDE);
      return undefined;
    }
    let live = true;
    setGuide((previous) => (previous.project === projectId ? { ...previous, loading: true } : { ...NO_GUIDE, loading: true, project: projectId }));
    client
      .request(`/api/projects/${projectId}/guide`)
      .then((reply) => live && setGuide({ project: projectId, loading: false, data: reply.data, error: reply.error, stale: reply.stale }))
      .catch((error) => {
        if (!live || error.kind === "session" || error.kind === "unreachable") return;
        setGuide((previous) => ({ ...previous, loading: false, error: { code: error.code, message: error.message }, stale: Boolean(previous.data) }));
      });
    return () => {
      live = false;
    };
  }, [projectId, version, ready, tick]);
  return [guide, () => setTick((n) => n + 1)];
}

function App({ initial }) {
  const [phase, setPhase] = useState(initial);
  const [path, setPath] = useState(pathFrom(location.hash));
  const [overview, setOverview] = useState(null);
  const [projects, setProjects] = useState([]);
  const [scope, setScopeState] = useState(tab.get("console-scope"));
  const [dockOpen, setDockOpen] = useState(false);
  const [theme, setThemeState] = useState(prefs.get("console-theme") || "system");
  const [, bump] = useReducer((n) => n + 1, 0);
  const seen = useRef(new Map());
  const finished = useRef(0);
  const scoped = projects.find((p) => p.id === scope) || null;
  const [guide, reloadGuide] = useGuide(scoped ? scoped.id : null, scoped ? jobs.version(scoped.id) : 0, phase.name === "ready");

  bus.phase = (next) => {
    jobs.halt();
    setPhase(next);
  };

  async function loadProjects() {
    const data = await client.get("/api/projects");
    setProjects(data.projects || []);
    return data.projects || [];
  }

  async function load() {
    try {
      const [over] = await Promise.all([client.get("/api/overview"), loadProjects()]);
      setOverview(over);
      setPhase({ name: "ready" });
      jobs.begin();
    } catch (error) {
      if (error.kind === "session" || error.kind === "unreachable") return; // the phase is already set
      setPhase({ name: "ready", error: error.message });
    }
  }

  useEffect(() => {
    // The route is always remembered, so a new one-time link (after a session ends, or pasted
    // into this tab) brings the tab back to it.
    rememberRoute();
    if (!session.get()) {
      if (phase.name === "loading") setPhase({ name: "signed-out", reason: "none" });
    } else if (phase.name === "loading") {
      load();
    }
    // Registered in every phase: a new one-time link pasted into a signed-out tab is a
    // hash-only change, and it must be redeemed (and leave the address bar) at once.
    const onHash = () => {
      if (tokenFrom(location.hash)) {
        location.reload(); // a new one-time link pasted into this tab: boot redeems it
        return;
      }
      rememberRoute();
      setPath(pathFrom(location.hash));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (phase.name !== "unreachable") return undefined;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [phase.name]);

  useEffect(() => {
    applyTheme(theme);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme(theme);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  useEffect(
    () =>
      jobs.subscribe(() => {
        // Announce a job's start and end once each; re-read projects when one ends.
        let ended = 0;
        for (const entry of jobs.list()) {
          const before = seen.current.get(entry.id);
          if (before !== entry.state) {
            seen.current.set(entry.id, entry.state);
            if (entry.state === "running" && !before) announce(`${jobLabel(entry.kind)} started`);
            else if (entry.state !== "running") announce(jobOutcome(entry).text);
          }
          if (entry.state !== "running") ended += 1;
        }
        if (ended !== finished.current) {
          finished.current = ended;
          loadProjects().catch(() => {});
        }
        bump();
      }),
    [],
  );

  const match = router.match(path);
  useEffect(() => {
    document.title = phase.name === "ready" ? titleFor(match && match.route) : titleFor(null);
  }, [path, phase.name]);

  if (phase.name === "loading") return html`<main id="main" class="center-page" aria-busy="true"><p class="muted">Loading the console…</p></main>`;
  if (phase.name === "signed-out") return html`<${SignedOut} reason=${phase.reason} />`;
  if (phase.name === "unreachable") return html`<${Unreachable} onRetry=${load} />`;

  const project = scoped;
  const value = {
    client,
    // Starting a job opens the dock so its log is in view (also for a refused start: the dock
    // then shows the job holding the lock).
    jobs: {
      ...jobs,
      async start(...args) {
        try {
          return await jobs.start(...args);
        } finally {
          setDockOpen(true);
          // The clicked button is now disabled (a job runs in this project), which drops focus
          // to the page: move it to the dock that just opened.
          requestAnimationFrame(() => {
            const active = document.activeElement;
            if (!active || active === document.body || active.disabled) document.getElementById("dock-title")?.focus();
          });
        }
      },
    },
    overview,
    projects,
    project,
    guide,
    reloadGuide,
    dockOpen,
    setDockOpen,
    theme,
    setTheme(next) {
      prefs.set("console-theme", next);
      setThemeState(next);
    },
    navigate(to) {
      location.hash = `#${to}`;
    },
    setScope(id, to) {
      tab.set("console-scope", id);
      setScopeState(id);
      if (to) location.hash = `#${to}`;
    },
    async addProject(body) {
      const added = await client.post("/api/projects", body);
      await loadProjects();
      return added;
    },
    signOut() {
      rememberRoute();
      session.set(null);
      bus.phase({ name: "signed-out", reason: "none" });
    },
    versionOf: (id) => jobs.version(id),
    projectName: (id) => visible((projects.find((p) => p.id === id) || {}).name || ""),
    busyReason(id) {
      const entry = jobs.runningIn(id);
      return entry ? `${jobLabel(entry.kind)} is running in this project. Wait for it, or cancel it in Jobs.` : false;
    },
  };
  return html`<${Console.Provider} value=${value}>
    ${phase.error ? html`<div class="notice error" role="alert"><${Text} value=${phase.error} /></div>` : null}
    <${Shell} match=${match} />
  <//>`;
}

async function boot() {
  const root = document.getElementById("app");
  let initial = { name: "loading" };
  const token = tokenFrom(location.hash);
  if (token) {
    // Consume the one-time token first and drop it from the address bar and history, then go back
    // to the screen this tab was on before its session ended (re-auth returns to the route).
    const back = tab.get("console-return");
    tab.set("console-return", null);
    history.replaceState(null, "", `/${back && RETURN_TO.test(back) ? back : "#/"}`);
    try {
      session.set((await base.post("/api/session", { token })).session);
    } catch (error) {
      initial = error.kind === "unreachable" ? { name: "unreachable" } : { name: "signed-out", reason: "used" };
    }
  }
  applyTheme(prefs.get("console-theme") || "system");
  render(html`<${App} initial=${initial} />`, root);
  root.setAttribute("aria-busy", "false");
}

boot();
