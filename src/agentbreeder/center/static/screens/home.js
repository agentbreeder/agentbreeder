// Home: what this console runs on (doctor), the registered projects, and adding one.

import { Action, Card, Notice, Table, Text, useConsole } from "../ui/components.js";
import { html, useState } from "../ui/preact.js";
import { jobLabel } from "../lib/format.js";

export function Home() {
  const { overview, projects, addProject, setScope } = useConsole();
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState("");
  const checks = (overview && overview.doctor && overview.doctor.data) || [];
  const docker = checks.find((check) => check.name === "Docker");
  const failed = checks.filter((check) => !check.passed && !check.info && check !== docker);

  const add = async (body) => {
    setBusy(true);
    setNotice(null);
    try {
      const project = await addProject(body);
      setScope(project.id, "/services");
    } catch (error) {
      setNotice({ tone: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  };

  return html`
    ${docker && !docker.passed
      ? html`<div class="notice error blocker" role="alert">
          <strong>Docker is not reachable.</strong> <${Text} value=${docker.message} /> Start Docker, then run
          <code>agentbreeder doctor</code> in a terminal. Services, builds and releases need it.
        </div>`
      : null}
    ${failed.length
      ? html`<${Card} title="Doctor">
          <ul class="plain">${failed.map((check) => html`<li><strong><${Text} value=${check.name} />:</strong> <${Text} value=${check.message} />${check.required ? "" : " (optional)"}</li>`)}</ul>
        <//>`
      : null}
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    ${projects.length
      ? html`<${Card} title="Projects">
          <${Table} label="Projects" columns=${["Project", "Environment", "Last job", ""]}>
            ${projects.map((project) => html`<tr key=${project.id}>
              <td><strong><${Text} value=${project.name} /></strong><br /><span class="muted small"><${Text} value=${project.path} /></span></td>
              <td><${Text} value=${project.environment} /></td>
              <td>${lastJob(project)}</td>
              <td><${Action} small tone="secondary" onClick=${() => setScope(project.id, "/services")}>Open<//></td>
            </tr>`)}
          <//>
        <//>`
      : null}
    <${Card} title=${projects.length ? "Add a project" : "Start with a project"} busy=${busy}>
      <div class="split">
        <div>
          <h3>Northwind example</h3>
          <p class="muted">A customer support agent with knowledge search, memory, order tools and a 61-case eval set. Copied to ~/agentbreeder-projects/northwind.</p>
          <${Action} disabled=${busy && "Adding a project…"} onClick=${() => add({ example: "northwind" })}>Onboard Northwind<//>
        </div>
        <form class="stack" onSubmit=${(event) => { event.preventDefault(); add({ path }); }}>
          <h3>An existing project</h3>
          <p class="muted">A directory with agent.yaml and stack.yaml that only you can write. Building runs its code in Docker, so add only projects you trust.</p>
          <label for="project-path">Absolute path</label>
          <input id="project-path" autocomplete="off" spellcheck="false" placeholder="/path/to/project" value=${path} onInput=${(event) => setPath(event.currentTarget.value)} />
          <${Action} type="submit" tone="secondary" disabled=${busy ? "Adding a project…" : !path.trim() && "Type the project's absolute path first."}>Add project<//>
        </form>
      </div>
    <//>
    <p class="muted small">The local Docker admin has full control. The gate prevents mistakes and policy drift, not a local admin.</p>
  `;
}

function lastJob(project) {
  const jobs = Object.values(project.jobs || {});
  if (!jobs.length) return html`<span class="muted">None yet</span>`;
  const latest = jobs.reduce((a, b) => ((a.created || 0) > (b.created || 0) ? a : b));
  return html`<${Text} value=${`${jobLabel(latest.kind)}: ${String(latest.state || "").replace("_", " ")}`} />`;
}
