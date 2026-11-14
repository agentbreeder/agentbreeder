// Agents (#823, M7): every agent across projects (`list --all --json`), and one agent's detail with
// the gate strip (Build → Candidate → Evals n/N → Identity → Production), the identity strip and
// the tabs Overview, Try it, Releases and Logs.
//
// The list reads every stack's state without registering anything: a stack whose project is not
// registered here shows "not registered" and has no detail. A detail is per project: opening one
// from the list switches the scope to that row's project. Releases come from the project's audit
// chain; the identity strip shows what the stack declares until #909 adds the principal's fields.

import { auditTime, isCandidate } from "../lib/format.js";
import {
  STEP_WORDS, TABS, agentName, candidateOf, gateStrip, identityText, identityTone, lastReleaseCell, latestRun, listRows, releasedImage, localDate, projectStatus,
  releaseText, releaseTone, releasesFor, shortImage, tabHref,
} from "../lib/agents.js";
import { href } from "../lib/router.js";
import { visible } from "../lib/text.js";
import { productionRow } from "../lib/tryit.js";
import { Card, Table, Text, ViewState, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useEffect, useState } from "../ui/preact.js";
import { LogsLink } from "./logs.js";
import { TryIt } from "./try.js";

/**
 * The Releases view of each registered project in `ids`, read one after another (each read runs
 * two CLI children), again whenever the list itself is read again (`loaded`).
 */
function useProjectReleases(ids, loaded) {
  const { client } = useConsole();
  const [views, setViews] = useState({});
  const key = ids.join(",");
  useEffect(() => {
    let live = true;
    (async () => {
      for (const id of ids) {
        let view;
        try {
          const reply = await client.request(`/api/projects/${id}/releases`);
          view = reply.data ? { data: reply.data } : { error: true };
        } catch {
          view = { error: true };
        }
        if (!live) return;
        setViews((previous) => ({ ...previous, [id]: view }));
      }
    })();
    return () => {
      live = false;
    };
  }, [key, loaded]);
  return views;
}

function AgentLink({ row }) {
  const { setScope, project } = useConsole();
  const target = `/agents/${row.agent}`;
  let link;
  try {
    link = href("/agents/:name", { name: row.agent });
  } catch {
    return html`<${Text} value=${row.name} />`;
  }
  const open = (event) => {
    // A modified or middle click opens a new tab as usual; that tab picks its own scope.
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (project && project.id === row.console_project) return;
    event.preventDefault();
    setScope(row.console_project, target);
  };
  return html`<a href=${link} onClick=${open}><${Text} value=${row.name} /></a>`;
}

function ProjectCell({ row }) {
  const { projectName } = useConsole();
  const status = projectStatus(row);
  if (status === "registered") return html`<${Text} value=${projectName(row.console_project)} />`;
  if (status === "default state") return html`<span class="muted">default state</span>`;
  return html`<span class="muted" title="Add its project on Home to open it here">not registered</span>${row.stack ? html` <span class="muted small">(<${Text} value=${row.stack} />)</span>` : null}`;
}

function LastRelease({ row, views }) {
  const cell = lastReleaseCell(row, row.console_project ? views[row.console_project] : null);
  return html`<span class=${`gated ${cell.tone}`} title=${cell.title}><${Text} value=${cell.text} /></span>${cell.at ? html` <span class="muted small"><${Text} value=${auditTime(cell.at)} /></span>` : null}`;
}

export function Agents() {
  const [view] = useView("/api/agents");
  const data = view.data || {};
  const rows = listRows(data.deployments);
  const skipped = Array.isArray(data.skipped) ? data.skipped : [];
  const ids = [...new Set(rows.filter((row) => row.console_project && !row.candidate).map((row) => row.console_project))].sort();
  const views = useProjectReleases(ids, view.data);
  return html`
    <${Card} title="Deployed agents" busy=${view.loading}>
      ${rows.length
        ? html`<${Table} label="Agents" columns=${["Agent", "Project", "Target", "State", "Image", "Identity check", "Endpoint", "Last release"]}>
            ${rows.map((row) => html`<tr key=${`${row.stack || ""} ${row.environment || ""} ${row.id}`}>
              <td>${row.console_project ? html`<${AgentLink} row=${row} />` : html`<${Text} value=${row.name} />`}${row.candidate ? html` <span class="badge candidate" title="Removed when its release ends">candidate, temporary</span>` : null}</td>
              <td><${ProjectCell} row=${row} /></td>
              <td><${Text} value=${row.target} /></td>
              <td><${Text} value=${row.state} />${row.error ? html`: <${Text} value=${row.error} />` : null}</td>
              <td>${row.image ? html`<code title=${visible(row.image)}><${Text} value=${shortImage(row.image)} /></code>` : html`<span class="muted">none</span>`}</td>
              <td><span class=${`identity ${identityTone(row.compliance)}`}><${Text} value=${identityText(row.compliance)} /></span>${row.updated ? html`<br /><span class="muted small">as of <${Text} value=${auditTime(row.updated)} /></span>` : null}</td>
              <td>${row.endpoint ? html`<code><${Text} value=${row.endpoint} /></code>` : html`<span class="muted">none</span>`}</td>
              <td><${LastRelease} row=${row} views=${views} /></td>
            </tr>`)}
          <//>`
        : null}
      <${ViewState} view=${view} empty=${!rows.length && !view.loading && !view.error ? "No agent is deployed in any stack on this machine." : null} />
      <p class="muted small">Every stack on this machine, read without changing anything. The identity check is the one recorded at deploy or by the last <code>reconcile</code>; its time is when the deployment last changed. Local endpoints are unauthenticated.</p>
    <//>
    ${skipped.length
      ? html`<${Card} title="Not read">
          <p class="muted">These stacks could not be read; the rest are listed.</p>
          <ul>${skipped.map((entry, index) => html`<li key=${index}><${Text} value=${`${entry.stack || "stacks directory"}${entry.environment ? ` (${entry.environment})` : ""}: [${entry.code}] ${entry.reason}`} /></li>`)}</ul>
        <//>`
      : null}
  `;
}

// -- detail ------------------------------------------------------------------------------------

function GateStrip({ steps }) {
  return html`<ol class="gate-strip" aria-label="Gate">
    ${steps.map((step) => html`<li class=${`gate-step ${step.state}`} key=${step.key}>
      <span class="gate-icon" aria-hidden="true">${step.state === "passed" ? "✓" : step.state === "failed" ? "✕" : step.state === "running" ? "…" : step.state === "differs" ? "≠" : "○"}</span>
      <span class="gate-label">${step.label}</span><span class="visually-hidden">: ${STEP_WORDS[step.state]}.</span>
      <span class="gate-detail muted small"><${Text} value=${step.detail} /></span>
    </li>`)}
  </ol>`;
}

function IdentityStrip({ name, identity, production }) {
  const known = Boolean(identity.data);
  const declared = known && identity.data.agents ? identity.data.agents[name] : null;
  const missing = known ? html`<span class="muted">not declared in stack.yaml</span>` : html`<span class="muted">unknown</span>`;
  return html`<dl class="identity-strip" aria-label="Identity">
    <div><dt>Principal</dt><dd>${declared ? html`<code><${Text} value=${name} /></code> <span class="muted small">declared in stack.yaml: the static demo principal <code>services up</code> writes</span>` : missing}</dd></div>
    <div><dt>Sponsor</dt><dd>${declared ? html`<${Text} value=${declared.sponsor} />` : missing}</dd></div>
    <div><dt>Review by</dt><dd>${declared ? html`<${Text} value=${declared.review_by} />${declared.review_by < localDate() ? html` <span class="identity failed">review date has passed: the gate refuses deploys</span>` : null}` : missing}</dd></div>
    <div><dt>Identity check</dt><dd>${production ? html`<span class=${`identity ${identityTone(production.compliance)}`}><${Text} value=${identityText(production.compliance)} /></span> <span class="muted small">recorded at deploy or by the last reconcile; deployment changed <${Text} value=${auditTime(production.updated)} /></span>` : html`<span class="muted">no production deployment</span>`}</dd></div>
    ${identity.error ? html`<div><dt>stack.yaml</dt><dd class="muted"><${Text} value=${identity.error.message} /></dd></div>` : null}
  </dl>`;
}

function Tabs({ name, tab }) {
  return html`<nav class="agent-tabs" aria-label="Agent sections">
    <ul>${TABS.map(([key, label]) => html`<li key=${key}><a href=${tabHref(name, key)} aria-current=${key === tab ? "page" : undefined}>${label}</a></li>`)}</ul>
  </nav>`;
}

function Overview({ agent, production, candidate }) {
  const file = agent.data;
  return html`
    <${Card} title="Production">
      ${production
        ? html`<dl class="facts">
            <dt>Target</dt><dd><${Text} value=${production.target} /></dd>
            <dt>State</dt><dd><${Text} value=${production.state} />${production.error ? html`: <${Text} value=${production.error} />` : null}</dd>
            <dt>Endpoint</dt><dd>${production.endpoint ? html`<code><${Text} value=${production.endpoint} /></code> <span class="muted small">unauthenticated</span>` : html`<span class="muted">none</span>`}</dd>
            <dt>Image</dt><dd>${production.image ? html`<code><${Text} value=${production.image} /></code>` : html`<span class="muted">none</span>`}</dd>
            <dt>Deployment</dt><dd><code><${Text} value=${production.id} /></code></dd>
          </dl>`
        : html`<p class="muted">No production deployment in this project. <a href=${href("/release")}>Build and release</a> it.</p>`}
      ${candidate ? html`<p><span class="badge candidate">candidate, temporary</span> <code><${Text} value=${candidate.name} /></code> is running: a release is in progress, or one was cut short (check Deployments).</p>` : null}
    <//>
    <${Card} title="agent.yaml" busy=${agent.loading}>
      ${file
        ? html`<dl class="facts">
            ${file.description ? html`<dt>Description</dt><dd><${Text} value=${file.description} /></dd>` : null}
            <dt>Runtime</dt><dd><code><${Text} value=${file.runtime} /></code></dd>
            <dt>Model</dt><dd><code><${Text} value=${file.model.alias} /></code></dd>
            <dt>Prompt</dt><dd><code><${Text} value=${file.prompt.ref || file.prompt.path || "inline"} /></code></dd>
            <dt>Tools</dt><dd>${file.tools.length ? html`<${Text} value=${file.tools.map((t) => t.ref).join(", ")} />` : html`<span class="muted">none</span>`}</dd>
            <dt>Knowledge</dt><dd>${file.knowledge.length ? html`<${Text} value=${file.knowledge.map((k) => k.ref).join(", ")} />` : html`<span class="muted">none</span>`}</dd>
            <dt>MCP</dt><dd>${file.mcp.length ? html`<${Text} value=${file.mcp.map((m) => m.ref).join(", ")} />` : html`<span class="muted">none</span>`}</dd>
            <dt>Evals</dt><dd>${file.evals ? html`<code><${Text} value=${file.evals.ref} /></code> <span class="muted small">needs ${Math.round(file.evals.threshold * 100)}%</span>` : html`<span class="muted">none: release has no eval gate</span>`}</dd>
          </dl>`
        : html`<${ViewState} view=${agent} />`}
    <//>
  `;
}

function Releases({ name, releases }) {
  const { entries, own, truncated } = releasesFor(releases.data, name);
  return html`<${Card} title="Releases" busy=${releases.loading}>
    ${releases.data && !own
      ? html`<p class="muted">This project's <code>agent.yaml</code> is ${releases.data.agent ? html`<code><${Text} value=${releases.data.agent} /></code>` : "not readable"}, so its releases are not this agent's. Audit rows of a release name no agent.</p>`
      : null}
    ${entries.length
      ? html`<${Table} label="Releases" columns=${["When", "Result", "Image", "By"]}>
          ${entries.map((release) => html`<tr key=${release.seq}>
            <td class="nowrap"><${Text} value=${auditTime(release.at)} /></td>
            <td><span class=${`gated ${releaseTone(release)}`}><${Text} value=${releaseText(release)} /></span>${release.problem && release.problem.message ? html`<br /><span class="muted small"><${Text} value=${release.problem.message} /></span>` : null}</td>
            <td>${release.image ? html`<code title=${visible(release.image)}><${Text} value=${shortImage(release.image)} /></code>` : html`<span class="muted">none</span>`}</td>
            <td><${Text} value=${release.actor} />${release.actor_verified ? null : html` <span class="muted small">(unverified)</span>`}</td>
          </tr>`)}
        <//>`
      : null}
    <${ViewState} view=${releases} empty=${releases.data && own && !entries.length ? "No release recorded in this project's audit log." : null} />
    ${truncated ? html`<p class="muted small">Only the newest 1,000 audit rows were read; older releases are not shown.</p>` : null}
    <p class="muted small">From this project's audit log (<a href=${href("/audit")}>Audit</a>). A release builds, runs a temporary candidate, runs the evals against it and replaces production only when they pass. <code>release --rollback</code> runs show as rolled back.</p>
  <//>`;
}

function Logs({ name, rows }) {
  const own = Array.isArray(rows) ? rows.filter((row) => row && agentName(row) === name) : [];
  return html`<${Card} title="Logs">
    ${own.length
      ? html`<ul class="plain">${own.map((row) => html`<li key=${row.id}><${Text} value=${row.name} /> <span class="muted small"><${Text} value=${`${row.target}, ${row.state}`} /></span>${isCandidate(row) ? html` <span class="badge candidate">candidate, temporary</span>` : null} <${LogsLink} kind="deployment" name=${row.id} label=${`Logs of ${visible(row.name)} on ${visible(row.target)}`} /></li>`)}</ul>`
      : html`<p class="muted">No running deployment of this agent, so there are no logs to read.</p>`}
  <//>`;
}

function AgentDetail({ project, name, tab }) {
  const [deployments] = useView(`/api/projects/${project.id}/deployments`, project.id);
  const [releases] = useView(`/api/projects/${project.id}/releases`, project.id);
  const [identity] = useView(`/api/projects/${project.id}/identity`, project.id);
  const [agent] = useView(`/api/projects/${project.id}/agent`, project.id);
  const rows = Array.isArray(deployments.data) ? deployments.data : [];
  const production = productionRow(rows, name);
  const candidate = candidateOf(rows, name);
  const { entries, own } = releasesFor(releases.data, name);
  const steps = gateStrip({ run: latestRun(entries), released: releasedImage(entries), production, candidate });
  return html`
    <p><a href=${href("/agents")}>← All agents</a></p>
    <section class="card agent-head" aria-label="Gate and identity" aria-busy=${deployments.loading || releases.loading ? "true" : "false"}>
      <${GateStrip} steps=${steps} />
      <${IdentityStrip} name=${name} identity=${identity} production=${production} />
      ${deployments.error ? html`<p class="muted small">Deployments: <${Text} value=${deployments.error.message} /></p>` : null}
      ${releases.error ? html`<p class="muted small">Releases: <${Text} value=${releases.error.message} /></p>` : null}
      ${releases.data && !own ? html`<p class="muted small">Build, Candidate and Evals are not shown: this project's releases are another agent's.</p>` : null}
    </section>
    <${Tabs} name=${name} tab=${tab} />
    ${tab === "overview" ? html`<${Overview} agent=${agent} production=${production} candidate=${candidate} />` : null}
    ${tab === "try" ? html`<${TryIt} project=${project} params=${{ name }} />` : null}
    ${tab === "releases" ? html`<${Releases} name=${name} releases=${releases} />` : null}
    ${tab === "logs" ? html`<${Logs} name=${name} rows=${deployments.data} />` : null}
  `;
}

/**
 * One screen for every tab (the tab is a route parameter), so switching tabs re-renders instead of
 * remounting: focus stays on the tab link and the views already read are kept. Keyed by name, so
 * another agent starts fresh.
 */
function AgentScreen({ project, params }) {
  const tab = params.tab || "overview";
  if (!TABS.some(([key]) => key === tab)) {
    return html`<p>This agent has no "<${Text} value=${tab} />" tab. <a href=${href("/agents/:name", { name: params.name })}>Open its overview</a>.</p>`;
  }
  return html`<${AgentDetail} key=${params.name} project=${project} name=${params.name} tab=${tab} />`;
}

/** The routes, for screens/index.js. Tabs share one section: switching tabs keeps focus. The
 * title names the agent once #915's `routeTitle` (a title computed from the parameters) lands. */
const DETAIL = { title: "Agent", section: "/agents/:name", navAs: "/agents", scoped: true, screen: AgentScreen };
export const AGENT_ROUTES = [
  { pattern: "/agents", title: "Agents", nav: "Agents", screen: Agents },
  { pattern: "/agents/:name", ...DETAIL },
  { pattern: "/agents/:name/:tab", ...DETAIL },
];
