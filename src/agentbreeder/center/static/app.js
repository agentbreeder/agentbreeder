// The command center UI. Server text is only ever placed with textContent.

const HEADERS = { "X-AgentBreeder-Center": "1" };
const STEPS = [
  ["project", "Project"],
  ["services", "Services"],
  ["release", "Build and release"],
  ["deployments", "Deployments"],
  ["kubernetes", "Kubernetes"],
  ["audit", "Audit"],
];
const ui = { step: null, projects: [], project: null, overview: null, job: null, jobLines: [], lastLine: 0, view: {}, plan: null, notice: null };

function sessionToken(value) {
  // Per tab, and gone with it. Falls back to memory if storage is unavailable.
  try {
    if (value) sessionStorage.setItem("center-session", value);
    return value || sessionStorage.getItem("center-session") || ui.session;
  } catch { if (value) ui.session = value; return ui.session; }
}

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "disabled") node.disabled = Boolean(value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, body) {
  const options = { headers: { ...HEADERS }, credentials: "omit" };
  const session = sessionToken();
  if (session) options.headers["X-AgentBreeder-Session"] = session;
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  // Every reply is an envelope {data, error: null | {code, message}, stale}.
  const reply = await response.json().catch(() => ({ data: null, error: { code: "UNREADABLE", message: "Unreadable response" }, stale: false }));
  if (response.status === 401) { showSignIn(); throw new Error("Sign in again"); }
  if (!response.ok) { const error = new Error(problem(reply) || "Request failed"); error.job = reply.data && reply.data.job; error.status = response.status; throw error; }
  return reply;
}

// The data of a reply; views keep the whole envelope (data, error, stale).
const fetchData = async (path, body) => (await api(path, body)).data;
const problem = (reply) => (reply && reply.error ? reply.error.message : null);

function announce(text) { document.getElementById("announce").textContent = text; }

function showSignIn() {
  const app = document.getElementById("app");
  app.replaceChildren(h("main", {}, h("h2", {}, "Sign in"),
    h("p", {}, "This link was already used, has expired, or there is no session."),
    h("p", { class: "muted" }, "In the terminal running agentbreeder up, press Enter for a new one-time link.")));
  app.setAttribute("aria-busy", "false");
}

function confirmAction(title, text, label) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { "aria-labelledby": "dialog-title" },
      h("h2", { id: "dialog-title" }, title), h("p", {}, text),
      h("div", { class: "row" },
        h("button", { class: "action danger", onclick: () => { dialog.close(); dialog.remove(); resolve(true); } }, label),
        h("button", { class: "action secondary", onclick: () => { dialog.close(); dialog.remove(); resolve(false); } }, "Cancel")));
    dialog.addEventListener("cancel", () => { dialog.remove(); resolve(false); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

// Engine jobs run one at a time per project: only this project's running job disables its actions.
const running = () => Boolean(ui.job && ui.job.state === "running" && ui.project && ui.job.project === ui.project.id);

async function refresh() {
  ui.projects = (await fetchData("/api/projects")).projects;
  if (ui.project) ui.project = ui.projects.find((p) => p.id === ui.project.id) || null;
  if (!ui.project) ui.project = ui.projects[ui.projects.length - 1] || null;
  if (ui.project) await loadView();
  if (!ui.step) ui.step = chooseStep();
  render();
}

function chooseStep() {
  if (!ui.project) return "project";
  const deployments = ui.view.deployments && ui.view.deployments.data;
  return deployments && deployments.length ? "deployments" : "services";
}

async function loadView() {
  const base = `/api/projects/${ui.project.id}`;
  const wanted = { services: ["services"], project: ["services"], release: ["deployments"], deployments: ["deployments"], kubernetes: ["cluster", "deployments"], audit: ["audit"] }[ui.step || "services"] || [];
  if (ui.step === "services") wanted.push("registry");
  if (!ui.view.deployments && !wanted.includes("deployments")) wanted.push("deployments");
  await Promise.all(wanted.map(async (view) => { ui.view[view] = await api(`${base}/${view}`); }));
}

// Follow one job in the drawer. Each call starts a new generation, so an older poll loop stops
// at its next tick and two loops never append to the same log. `project` comes from the
// server's job row on the first poll.
function follow(job) {
  ui.pollGeneration = (ui.pollGeneration || 0) + 1;
  ui.job = job ? { state: "running", ...job } : null;
  ui.jobLines = [];
  ui.lastLine = 0;
  if (job) pollJob(ui.pollGeneration);
}

async function startJob(kind, options = {}, keys) {
  try {
    const { job } = await fetchData(`/api/projects/${ui.project.id}/jobs`, { kind, options, keys });
    ui.notice = null;
    follow({ id: job, kind, project: ui.project.id });
    announce(`${kind} started`);
    render();
  } catch (error) {
    ui.notice = { kind: "error", text: error.message };
    if (error.job) follow({ id: error.job, kind: "job" });
    render();
  }
}

async function pollJob(generation) {
  if (!ui.job || generation !== ui.pollGeneration) return;
  let job;
  try {
    job = await fetchData(`/api/jobs/${ui.job.id}?after=${ui.lastLine}`);
  } catch (error) {
    // A failed poll retries (the sign-in screen handles a 401); the loop never just stops.
    if (error.status !== 401 && generation === ui.pollGeneration) setTimeout(() => pollJob(generation), 5000);
    return;
  }
  if (generation !== ui.pollGeneration) return;
  for (const line of job.lines) { ui.jobLines.push(line.text); ui.lastLine = line.seq; }
  const wasRunning = ui.job.state === "running";
  const learnedProject = !ui.job.project;
  ui.job = { ...ui.job, ...job };
  if (job.state === "running") {
    // A full render only when the job's project first arrives (it decides which actions are
    // disabled); otherwise only the drawer, so form inputs are kept.
    if (learnedProject) render(); else renderDrawer();
    setTimeout(() => pollJob(generation), 2000);
    return;
  }
  if (wasRunning) { announceEnd(job); await refresh(); } else { renderDrawer(); }
}

// On a project switch or at start: follow that project's running job, if any. Reads
// /api/activity, which starts no child process.
async function followProject() {
  const activity = await fetchData("/api/activity");
  const mine = activity.running.find((job) => ui.project && job.project === ui.project.id);
  if (mine) follow({ id: mine.id, kind: mine.kind, project: mine.project });
  else if (ui.job && ui.job.state === "running" && ui.project && ui.job.project !== ui.project.id) follow(null);
}

const ENDINGS = {
  succeeded: "finished",
  failed: "failed",
  waiting_approval: "is waiting for a second person's approval",
  interrupted: "was interrupted",
};

function announceEnd(job) {
  const text = `${job.kind} ${ENDINGS[job.state] || job.state}`;
  announce(text);
  if (job.state === "failed" && (job.kind === "release" || job.kind === "promote")) {
    ui.notice = { kind: "error", text: job.kind === "release"
      ? (job.exit_code === 5 ? "The eval gate failed. Production unchanged." : "The release failed. See the log below.")
      : (job.exit_code === 5 ? "Cluster evals failed. The log says what was restored." : "The promote failed. See the log below.") };
  } else if (job.state === "waiting_approval") {
    ui.notice = { kind: "warn", text: "A second person must approve this. The log shows the command. It cannot be approved here, because this session is one person." };
  } else if (job.state === "interrupted") {
    ui.notice = { kind: "warn", text: "The job was interrupted. Check Deployments for a leftover candidate and remove it." };
  } else {
    ui.notice = job.state === "succeeded" ? { kind: "ok", text } : { kind: "error", text };
  }
}

function render() {
  const app = document.getElementById("app");
  const select = h("select", { "aria-label": "Project", onchange: async (event) => { ui.project = ui.projects.find((p) => p.id === event.target.value); ui.view = {}; ui.plan = null; try { await followProject(); } catch (error) { if (error.status === 401) return; ui.notice = { kind: "error", text: error.message }; } try { await refresh(); } catch { /* the sign-in screen is already shown */ } } },
    ui.projects.map((p) => { const o = h("option", { value: p.id }, `${p.name} (${p.environment})`); o.selected = ui.project && p.id === ui.project.id; return o; }));
  app.replaceChildren(
    h("header", { class: "top" },
      h("div", {}, h("h1", {}, "AgentBreeder Command Center"),
        h("span", { class: "muted" }, `preview ${ui.overview ? ui.overview.version : ""}, this machine only`)),
      ui.projects.length ? select : null),
    h("div", { class: "layout" },
      h("nav", { class: "steps", "aria-label": "Walkthrough" }, h("ol", {}, STEPS.map(([id, label]) =>
        h("li", {}, h("button", { "aria-current": ui.step === id ? "step" : null, onclick: () => goto(id) }, label, h("small", {}, stepStatus(id))))))),
      h("main", {}, ui.notice ? h("div", { class: `banner ${ui.notice.kind}`, role: ui.notice.kind === "error" ? "alert" : "status" }, ui.notice.text) : null, panel())),
    h("div", { class: "drawer", id: "drawer" }));
  app.setAttribute("aria-busy", "false");
  renderDrawer();
}

async function goto(step) {
  ui.step = step;
  if (ui.project) { try { await loadView(); } catch { /* the sign-in screen is already shown */ return; } }
  render();
}

function stepStatus(id) {
  const jobs = (ui.project && ui.project.jobs) || {};
  const last = (kind) => (jobs[kind] ? ` (last: ${jobs[kind].state})` : "");
  if (id === "project") return ui.project ? ui.project.name : "None yet";
  if (id === "services") return `Stack${last("services-up")}`;
  if (id === "release") return `Eval gate${last("release")}`;
  if (id === "deployments") { const rows = ui.view.deployments && ui.view.deployments.data; return rows ? `${rows.length} active` : "Not loaded"; }
  if (id === "kubernetes") return `Local cluster${last("promote")}`;
  return "Attempts and verify";
}

function panel() {
  if (ui.step !== "project" && !ui.project) return panelProject();
  return { project: panelProject, services: panelServices, release: panelRelease, deployments: panelDeployments, kubernetes: panelKubernetes, audit: panelAudit }[ui.step]();
}

const staleNote = (view) => (view && view.stale ? h("p", { class: "muted" }, "Showing last-known data. It refreshes when the running job ends.") : null);
const errorNote = (view) => (view && view.error && !view.stale ? h("p", { class: "muted" }, problem(view)) : null);

function trust() {
  return h("p", { class: "muted" }, "The local Docker admin has full control. The gate prevents mistakes and policy drift, not a local admin.");
}

function panelProject() {
  const path = h("input", { id: "path", placeholder: "/path/to/project", autocomplete: "off" });
  const add = async (body) => {
    try { ui.project = await fetchData("/api/projects", body); ui.view = {}; ui.notice = null; ui.step = "services"; await refresh(); } catch (error) { ui.notice = { kind: "error", text: error.message }; render(); }
  };
  return h("section", {}, h("h2", {}, "Project"),
    h("div", { class: "card" }, h("h3", {}, "Start with the Northwind example"),
      h("p", { class: "muted" }, "A customer support agent with knowledge search, memory, order tools and a 61-case eval set. Copied to ~/agentbreeder-projects/northwind."),
      h("button", { class: "action", onclick: () => add({ example: "northwind" }) }, "Onboard Northwind")),
    h("div", { class: "card" }, h("h3", {}, "Or use an existing project"),
      h("p", { class: "muted" }, "A directory with agent.yaml and stack.yaml. Building runs its code in Docker, so only use projects you trust."),
      h("label", { for: "path" }, h("span", {}, "Absolute path"), path),
      h("button", { class: "action secondary", onclick: () => add({ path: path.value }) }, "Add project")),
    trust());
}

function diffLines(before, after) {
  if (!before || before === after) return null;
  const old = new Set(before.split("\n")); const now = new Set(after.split("\n"));
  const lines = [...before.split("\n").filter((l) => !now.has(l)).map((l) => ["del", `- ${l}`]), ...after.split("\n").filter((l) => !old.has(l)).map((l) => ["add", `+ ${l}`])];
  return lines.length ? h("div", {}, h("h3", {}, "Changed since you last accepted"), h("pre", {}, lines.map(([kind, text]) => h("div", { class: `diff-${kind}` }, text)))) : null;
}

function panelServices() {
  const services = ui.view.services || {};
  const rows = services.data ? Object.entries(services.data.services || {}) : [];
  const review = async () => {
    try { const reply = await api(`/api/projects/${ui.project.id}/plan`, {}); ui.plan = reply.data; ui.notice = reply.error ? { kind: "error", text: problem(reply) } : null; } catch (error) { ui.notice = { kind: "error", text: error.message }; }
    render();
  };
  return h("section", {}, h("h2", {}, "Services"),
    h("div", { class: "card" },
      rows.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Service"), h("th", {}, "State"), h("th", {}, "Host ports"))),
        h("tbody", {}, rows.map(([name, info]) => h("tr", {}, h("td", {}, name), h("td", {}, `${info.state}${info.health ? ` (${info.health})` : ""}`), h("td", {}, Object.entries(info.host_ports || {}).map(([k, v]) => `${k} 127.0.0.1:${v}`).join(", "))))))) : h("p", { class: "muted" }, "Not started yet."),
      staleNote(services),
      h("div", { class: "row" },
        h("button", { class: "action", disabled: running(), onclick: review }, rows.length ? "Review and update" : "Review what will run"),
        rows.length ? h("button", { class: "action secondary", disabled: running(), onclick: async () => { if (await confirmAction("Stop the services?", "The containers stop. Their data and generated secrets are kept. Refused while an agent is deployed.", "Stop services")) startJob("services-down"); } }, "Stop services") : null)),
    ui.plan && ui.plan.grant ? grantCard(ui.plan) : null,
    registryCard(),
    trust());
}

function grantCard(plan) {
  const data = plan.grant;
  const inputs = data.keys.filter((key) => !key.stored).map((key) => ({ key, input: h("input", { type: "password", autocomplete: "off", id: `key-${key.name}` }) }));
  const stored = data.keys.filter((key) => key.stored);
  const start = () => {
    const keys = {};
    for (const { key, input } of inputs) {
      if (!input.value && !key.optional) { ui.notice = { kind: "error", text: `${key.name} is required.` }; render(); return; }
      if (input.value) keys[key.name] = input.value;
    }
    ui.plan = null;
    startJob("services-up", { digest: data.digest }, Object.keys(keys).length ? keys : undefined);
  };
  return h("div", { class: "card" }, h("h3", {}, "What will run"),
    data.mcp.length ? h("p", { class: "banner" }, `This stack has MCP servers (${data.mcp.join(", ")}). Review their tools with the CLI: agentbreeder services up.`) : null,
    diffLines(plan.accepted_text, data.text),
    h("pre", { tabindex: "0" }, data.text),
    h("p", { class: "muted" }, `Grant digest ${data.digest.slice(0, 16)}…`),
    inputs.length ? h("h3", {}, "Provider keys") : null,
    inputs.map(({ key, input }) => h("label", { for: input.id }, h("span", {}, `${key.name}${key.optional ? " (optional)" : ""}`), input)),
    stored.length ? h("p", { class: "muted" }, `Already stored: ${stored.map((k) => k.name).join(", ")}. Enter a value through the CLI to replace one.`) : null,
    h("p", { class: "muted" }, "Keys go to the stack's private state and are never shown again."),
    h("button", { class: "action", disabled: running() || data.mcp.length > 0, onclick: start }, "Confirm and start"));
}

function registryCard() {
  const registry = ui.view.registry && ui.view.registry.data;
  if (!registry) return null;
  const versions = registry.versions.data || [];
  const problems = registry.problems.data;
  return h("div", { class: "card" }, h("h3", {}, "Registries"),
    h("p", {}, `${versions.length} versions available. `, problems ? (problems.length ? `${problems.length} failed verification.` : "All passed verification (corruption check).") : (problem(registry.problems) || "")),
    h("details", {}, h("summary", {}, "Show versions"), h("pre", { tabindex: "0" }, versions.map((row) => row.ref).join("\n"))));
}

function panelRelease() {
  return h("section", {}, h("h2", {}, "Build and release"),
    h("div", { class: "card" },
      h("p", {}, "Builds the image, deploys a candidate, runs the eval gate and promotes the exact evaluated image to local Docker only if every gate passes."),
      h("p", { class: "muted" }, "For Northwind: 61 evals, about 4 minutes and about $0.24 in model usage (measured)."),
      h("div", { class: "row" },
        h("button", { class: "action", disabled: running(), onclick: () => startJob("release") }, "Run release"),
        h("button", { class: "action secondary", disabled: running(), onclick: () => startJob("build") }, "Build only"),
        h("button", { class: "action secondary", disabled: running(), onclick: async () => { if (await confirmAction("Roll back local production?", "Redeploys the previous local release. Anything already on the cluster keeps its release.", "Roll back")) startJob("rollback"); } }, "Roll back"))));
}

function panelDeployments() {
  const view = ui.view.deployments || {};
  const rows = view.data || [];
  return h("section", {}, h("h2", {}, "Deployments"),
    h("div", { class: "card" }, staleNote(view), errorNote(view),
      rows.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Name", "Target", "State", "Identity check", "Endpoint", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, rows.map((row) => h("tr", {}, h("td", {}, row.name), h("td", {}, row.target), h("td", {}, row.error ? `${row.state}: ${row.error}` : row.state), h("td", {}, row.compliance || "not checked"), h("td", {}, h("code", {}, row.endpoint || "none")),
          h("td", {}, h("button", { class: "action danger", disabled: running(), onclick: async () => { if (await confirmAction(`Remove ${row.name}?`, `Removes the ${row.target} workload and revokes its gateway key. History and the audit log are kept.`, "Remove")) startJob("remove", { deployment: row.id, target: row.target }); } }, "Remove"))))))) : h("p", { class: "muted" }, "Nothing deployed."),
      h("p", { class: "muted" }, "Local endpoints are unauthenticated. Cluster deployments have no public address: use agentbreeder forward ID."),
      h("button", { class: "action secondary", disabled: running(), onclick: () => startJob("reconcile") }, "Re-verify (reconcile)")),
    h("p", { class: "muted" }, "A deployment needing approval shows its command in the job log. A second person runs it."),
    trust());
}

function panelKubernetes() {
  const cluster = ui.view.cluster || {};
  const info = cluster.data;
  const rows = (ui.view.deployments && ui.view.deployments.data) || [];
  const local = rows.find((row) => row.target === "local" && row.state === "ready" && !row.name.endsWith("-candidate"));
  const onCluster = rows.filter((row) => row.target === "kubernetes");
  const missing = info ? Object.entries(info.tools).filter(([, tool]) => !tool.present) : [];
  const unsupported = info && info.tools.kind.present && !info.tools.kind.supported;
  return h("section", {}, h("h2", {}, "Kubernetes"),
    h("p", { class: "muted" }, "Local kind cluster only. Verified there, not on a production cluster. Stack services stay in Docker, and Secrets are unencrypted in kind's etcd."),
    h("div", { class: "card" }, h("h3", {}, "Local cluster"), staleNote(cluster), errorNote(cluster),
      missing.map(([name, tool]) => h("p", { class: "banner" }, `${name} is not on PATH. Install it: ${tool.install}`)),
      unsupported ? h("p", { class: "banner" }, "kind 0.33 or later is required: network policy was measured on that release.") : null,
      info ? h("p", {}, `Cluster: ${info.cluster ? "running" : "not created"}. Registry: ${info.registry ? "running" : "not created"}. In the accepted grant: ${info.configured ? "yes" : "no"}.`) : null,
      info && info.cluster && !info.configured ? h("p", {}, h("button", { class: "action", disabled: running(), onclick: () => goto("services") }, "Review the grant change")) : null,
      h("div", { class: "row" },
        h("button", { class: "action", disabled: running() || missing.length > 0 || unsupported, onclick: async () => { if (await confirmAction("Connect a local cluster?", "Creates a kind cluster and a registry on 127.0.0.1:5001 and attaches the node to the stack's agents network. You then review the grant change before it applies.", "Connect")) startJob("cluster-up"); } }, "Connect local cluster"),
        info && info.cluster ? h("button", { class: "action danger", disabled: running(), onclick: async () => { if (await confirmAction("Delete the local cluster?", "Deletes the kind cluster and registry. Refused while something is deployed there.", "Delete cluster")) startJob("cluster-down"); } }, "Delete cluster") : null)),
    h("div", { class: "card" }, h("h3", {}, "Deploy this release to the cluster"),
      h("p", {}, local ? `Released locally: ${local.name}.` : "Run a release first. Only an image that passed the eval gate can be promoted."),
      h("p", { class: "muted" }, "Pushes the evaluated image, deploys it by digest through the same gate, then runs the eval set on the cluster. For Northwind: about 4 minutes and about $0.24."),
      h("button", { class: "action", disabled: running() || !local || !(info && info.configured), onclick: () => startJob("promote") }, "Deploy to cluster"),
      onCluster.length ? h("p", { class: "muted" }, `On the cluster: ${onCluster.map((row) => `${row.name} (${row.state})`).join(", ")}.`) : null));
}

function panelAudit() {
  const reply = ui.view.audit || {};
  if (reply.error) return h("section", {}, h("h2", {}, "Audit"), h("p", { class: "muted" }, problem(reply)));
  const view = reply.data || {};
  const rows = (view.rows && view.rows.data) || [];
  const verify = view.verify && view.verify.data;
  return h("section", {}, h("h2", {}, "Audit"),
    verify ? h("div", { class: "card" }, h("h3", {}, "Verify"), Object.entries(verify).map(([key, value]) => h("p", {}, h("strong", {}, `${key}: `), typeof value === "object" ? JSON.stringify(value) : String(value)))) : null,
    h("div", { class: "card" }, staleNote(view.rows), h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Time", "Action", "Decision", "Actor"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.map((row) => h("tr", {}, h("td", {}, row.timestamp || ""), h("td", {}, row.action), h("td", {}, row.decision), h("td", {}, row.actor))))))));
}

function projectName(id) {
  const project = ui.projects.find((p) => p.id === id);
  return project ? `${project.name}: ` : "";
}

function renderDrawer() {
  const drawer = document.getElementById("drawer");
  if (!drawer) return;
  if (!ui.job) { drawer.hidden = true; return; }
  drawer.hidden = false;
  const log = h("pre", { role: "log", tabindex: "0", "aria-label": "Job log" }, ui.jobLines.join("\n"));
  drawer.replaceChildren(h("div", { class: "row" },
    h("strong", {}, `${projectName(ui.job.project)}${ui.job.kind}: ${(ui.job.state || "").replace("_", " ")}`),
    ui.job.state === "running" ? h("button", { class: "action secondary", onclick: () => api(`/api/jobs/${ui.job.id}/cancel`, {}) }, "Cancel") : null,
    h("button", { class: "action secondary", onclick: () => { follow(null); renderDrawer(); } }, "Hide")), log);
  log.scrollTop = log.scrollHeight;
}

async function start() {
  const match = /^#t=([A-Za-z0-9_-]+)$/.exec(location.hash);
  if (match) {
    history.replaceState(null, "", "/");
    try { sessionToken((await fetchData("/api/session", { token: match[1] })).session); } catch { showSignIn(); return; }
  }
  try {
    ui.overview = await fetchData("/api/overview");
    await refresh();
    // Several jobs can run (one per project): follow this project's, if any.
    await followProject();
    render();
  } catch { showSignIn(); }
}

start();
