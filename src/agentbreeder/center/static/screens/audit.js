// Governance ▸ Audit: the deploy audit chain in words, and its rows, 50 to a page, newest first.
// Each row names its agent (a candidate is marked), its enforcement mode and reason codes; an
// agent with a ready local production deployment links to Try it and shows its endpoint, and a
// release row shows its eval run, linked to the eval report viewer (#932), and the report file
// `release` wrote (#824). The agent's name opens its detail (#823); a candidate's row opens the
// agent it belongs to.

import { pageLabel, production, releaseEval, rowAgents } from "../lib/audit.js";
import { reportHref } from "../lib/evals.js";
import { auditTime, incompleteSentence, reasonCodes, verifySentence } from "../lib/format.js";
import { href } from "../lib/router.js";
import { visible } from "../lib/text.js";
import { Action, Card, Table, Text, ViewState } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useRef, useState } from "../ui/preact.js";

function AgentCell({ agent, deployments, newest }) {
  if (!agent) return html`<span class="muted">not recorded</span>`;
  // Today's production, not the row's time: shown once, on the agent's newest row on this page.
  const live = agent.candidate || !newest ? null : production(deployments, agent.name);
  return html`<a href=${href("/agents/:name", { name: agent.name })}><${Text} value=${agent.name} /></a>
    ${agent.candidate ? html` <span class="badge candidate" title="Removed when its release ends">candidate</span>` : null}
    ${live
      ? html`<div class="small"><a href=${href("/agents/:name/try", { name: agent.name })} aria-label=${`Try ${visible(agent.name)} in production`}>Production</a> <code><${Text} value=${live.endpoint} /></code></div>`
      : null}`;
}

/** The run id, linked to its report in the eval viewer (#932). */
function RunLink({ run, agent }) {
  const target = reportHref(run, agent || null);
  const code = html`<code><${Text} value=${run} /></code>`;
  return target ? html`<a href=${target} aria-label=${`Eval report of run ${run}`}>${code}</a>` : code;
}

function ReasonCell({ row, agent }) {
  const run = releaseEval(row, agent && agent.name);
  return html`<${Text} value=${reasonCodes(row)} />
    ${run
      ? html`<div class="small">Evals ${run.passed}/${run.cases}, run <${RunLink} run=${run.run} agent=${agent && agent.name} />${run.report
          ? html`<br />Report <code><${Text} value=${run.report} /></code>`
          : null}</div>`
      : null}`;
}

export function Audit({ project }) {
  // `pages` holds the `before` seq of each older page opened; the last one is on screen. The
  // shell keys this screen by project, so another project starts again at the newest page.
  const [pages, setPages] = useState([]);
  const before = pages.length ? pages[pages.length - 1] : null;
  const path = `/api/projects/${project.id}/audit${before ? `?before=${before}` : ""}`;
  const [view, reload] = useView(path, project.id);
  // Paging moves focus to the page's status line, which is always there: the button pressed
  // can disappear (no older page, back to the newest).
  const status = useRef(null);
  const [older, setOlder] = useState(null); // the shown page's cursor, kept while the next loads
  const go = (next) => {
    setPages(next);
    if (status.current) status.current.focus();
  };
  // useView keeps the last answer while a new page loads: show rows only under the page they answer.
  const current = view.data && (view.data.before ?? null) === before ? view.data : null;
  const answered = current && Number.isInteger(current.older) ? current.older : null;
  if (current && answered !== older) setOlder(answered);
  if (!view.data && !pages.length) return html`<${Card} title="Audit log"><${ViewState} view=${view} /><//>`;
  const report = view.data && view.data.verify && view.data.verify.data;
  const verifyError = view.data && view.data.verify && view.data.verify.error;
  const sentence = verifySentence(report);
  const incomplete = incompleteSentence(report);
  const rows = (current && current.rows && Array.isArray(current.rows.data) && current.rows.data) || [];
  const rowsError = current && current.rows && current.rows.error;
  const failed = !current && !view.loading ? view.error || { message: "This page could not be read." } : null;
  const deployments = (current && current.deployments && Array.isArray(current.deployments.data) && current.deployments.data) || [];
  const agents = rowAgents(rows, deployments);
  const seen = new Set();
  const newest = agents.map((agent) => Boolean(agent) && !agent.candidate && !seen.has(agent.name) && Boolean(seen.add(agent.name)));
  const statusText = failed
    ? `This page could not be read: ${(failed && failed.message) || "unknown error"}`
    : !current
      ? "Loading this page…"
      : rowsError
        ? rowsError.message || "The audit rows could not be read."
        : rows.length
          ? pageLabel(pages, rows.length)
          : pages.length
            ? "No older rows."
            : "No audit rows yet. A deploy, release or removal records its attempt here.";
  const loading = view.loading || !current;
  return html`
    <${Card} title="Verify">
      ${report
        ? html`<p class=${`verdict ${sentence.ok ? "ok" : "bad"}`} role="status"><${Text} value=${sentence.text} /></p>
            ${incomplete ? html`<p><${Text} value=${incomplete} /></p>` : null}
            ${report.limitations ? html`<p class="muted small"><${Text} value=${report.limitations} /></p>` : null}`
        : html`<p class="muted"><${Text} value=${(verifyError && verifyError.message) || "Not verified yet."} /></p>`}
    <//>
    <${Card} title="Attempts" busy=${view.loading}>
      ${current && current.rows && current.rows.stale ? html`<p class="muted">Showing last-known data. It refreshes when the running job ends.</p>` : null}
      <p class="muted small" role="status" tabindex="-1" ref=${status}><${Text} value=${statusText} /></p>
      ${failed ? html`<${Action} small tone="secondary" onClick=${() => { reload(); if (status.current) status.current.focus(); }}>Retry<//>` : null}
      ${rows.length
        ? html`<${Table} label="Audit rows, newest first" columns=${["Time", "Agent", "Action", "Decision", "Mode", "Reasons", "Deployment", "Actor"]}>
            ${rows.map((row, index) => html`<tr key=${row.seq}>
              <td class="nowrap">${auditTime(row.at)}</td>
              <td><${AgentCell} agent=${agents[index]} deployments=${deployments} newest=${newest[index]} /></td>
              <td><${Text} value=${row.action} /></td>
              <td><${Text} value=${row.decision} /></td>
              <td><${Text} value=${row.enforcement_mode || ""} /></td>
              <td><${ReasonCell} row=${row} agent=${agents[index]} /></td>
              <td>${row.deployment_id ? html`<code><${Text} value=${String(row.deployment_id).slice(0, 12)} /></code>` : html`<span class="muted">none</span>`}</td>
              <td><${Text} value=${row.actor || ""} />${row.actor_verified ? "" : html` <span class="muted small">(unverified)</span>`}</td>
            </tr>`)}
          <//>`
        : null}
      <div class="row-actions">
        ${pages.length ? html`<${Action} small tone="secondary" disabled=${loading && !failed} onClick=${() => go([])}>Newest<//>` : null}
        ${pages.length ? html`<${Action} small tone="secondary" disabled=${loading && !failed} onClick=${() => go(pages.slice(0, -1))}>Newer<//>` : null}
        ${older && !failed ? html`<${Action} small tone="secondary" disabled=${loading} onClick=${() => go([...pages, older])}>Older<//>` : null}
      </div>
      <p class="muted small">The Production link is today's deployment, on the agent's newest row on this page. A release row's report path is relative to the project. The chain detects a corrupted or edited row; whoever owns the database can recompute it, so it is evidence, not proof.</p>
    <//>
  `;
}
