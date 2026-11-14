// The gate-failed view (#826, M10 console half, PR 3): when an agent's latest release failed the
// eval gate, one row per failed check of each failed case (the case, the check, what was expected
// and what happened, the judge's reason), links to edit the prompt and the case, and the log tail
// `release` saved from the candidate before removing it. Shown on the agent's Overview and on
// Build and release. The report is `evals show RUN --json` (lib/evals.js), the expected values
// come from the eval set's `registry show`, the tail from `logs --failed NAME --json`. Every
// string is the CLI's redacted text, shown through Text (controls made visible), never as markup.

import { parseCases, casesFile } from "../lib/cases.js";
import { reportApi, reportHref, scoreText } from "../lib/evals.js";
import { caseHref, casesById, evalSetRef, failureRows, promptHref, tailApi } from "../lib/gate.js";
import { Action, Card, Table, Text, ViewState } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useState } from "../ui/preact.js";

/** The eval set's cases by id, read with `registry show` of the report's eval set (or none). */
function useEvalCases(project, evalSet) {
  const path = evalSetRef(evalSet) ? `/api/projects/${project.id}/registry/show?ref=${encodeURIComponent(evalSet)}` : null;
  const [view] = useView(path, project.id);
  const show = view.data;
  const name = casesFile(show);
  const file = name && show && Array.isArray(show.files) ? show.files.find((f) => f && f.path === name && typeof f.text === "string") : null;
  const parsed = file && !file.truncated ? parseCases(file.text) : null;
  return { byId: parsed && !parsed.error ? casesById(parsed.entries) : new Map(), read: Boolean(parsed && !parsed.error), view };
}

function Failures({ project, name, run, agentFile }) {
  const [content, setContent] = useState(false);
  const [view, reload] = useView(reportApi(project.id, run, { agent: name, content }), project.id);
  const report = view.data;
  const cases = useEvalCases(project, report && report.eval_set);
  const rows = failureRows(report, cases.byId);
  const prompt = promptHref(agentFile);
  const shown = Boolean(report && report.content);
  const reportLink = reportHref(run, name);
  return html`<${Card} title="Eval gate failed" busy=${view.loading}>
    <p class="verdict bad" role="status">${report ? html`<${Text} value=${`${scoreText(report)}. Production is unchanged.`} />` : "The latest release failed the eval gate. Production is unchanged."}</p>
    <p class="small">
      ${prompt ? html`<a href=${prompt}>Edit the prompt</a> · ` : html`<span class="muted">The prompt is not a registry ref, so edit it in agent.yaml's folder.</span> · `}
      ${reportLink ? html`<a href=${reportLink}>Full eval report</a>` : null}
      ${report && report.eval_set ? html` · eval set <code><${Text} value=${String(report.eval_set)} /></code>` : null}
    </p>
    <${ViewState} view=${view} />
    ${view.error && !report ? html`<div class="row-actions"><${Action} small tone="secondary" onClick=${reload}>Retry<//></div>` : null}
    ${report
      ? html`
        <div class="row-actions">
          <${Action} small tone="secondary" disabled=${view.loading} onClick=${() => (content && !shown ? reload() : setContent(!shown))}>${shown ? "Hide the agent's replies" : "Show the agent's replies"}<//>
        </div>
        ${rows.length
          ? html`<${Table} label="Failed checks" columns=${["Case", "Check", "Expected", "Actual", "Judge's reason", ""]}>
              ${rows.map((row) => {
                const edit = row.first ? caseHref(report.eval_set, row.id) : null;
                return html`<tr key=${row.key}>
                  <td><code><${Text} value=${row.id} /></code></td>
                  <td>${row.check === null ? html`<span class="muted">none recorded</span>` : html`<code><${Text} value=${row.check} /></code>`}</td>
                  <td>${row.expected ? html`<${Text} value=${row.expected} />` : html`<span class="muted">not known</span>`}</td>
                  <td><${Text} value=${row.actual} />${row.reply !== null ? html`<details class="case-content"><summary>Reply</summary><${Text} value=${row.reply || "(empty)"} block label=${`reply to ${row.id}`} /></details>` : null}</td>
                  <td>${row.reason ? html`<${Text} value=${row.reason} />` : html`<span class="muted">none</span>`}</td>
                  <td class="nowrap">${edit ? html`<a href=${edit} aria-label=${`Edit case ${row.id}`}>Edit case</a>` : null}</td>
                </tr>`;
              })}
            <//>`
          : html`<p class="muted">The report has no readable failed case. The full report says more.</p>`}
        ${report.eval_set && !cases.read && (!evalSetRef(report.eval_set) || cases.view.error || !cases.view.loading)
          ? html`<p class="muted small">The eval set's cases could not be read, so reference answers are not shown.</p>`
          : null}
        <p class="muted small">Reasons and replies are model output, redacted and capped by the CLI. A case edit or a prompt edit saves a new version; move the agent to it, then release again.</p>`
      : null}
  <//>`;
}

function CandidateTail({ project, name }) {
  const [view, reload] = useView(tailApi(project.id, name), project.id);
  const data = view.data;
  const lines = data && data.saved ? data.lines : [];
  return html`<${Card} title="Candidate log (saved tail)" busy=${view.loading}>
    <${ViewState} view=${view} />
    ${view.error && !data ? html`<div class="row-actions"><${Action} small tone="secondary" onClick=${reload}>Retry<//></div>` : null}
    ${data && data.saved && !lines.length ? html`<p class="muted">The saved tail is empty: the candidate printed nothing.</p>` : null}
    ${data && data.saved && lines.length
      ? html`<p class="muted small">The candidate's last ${lines.length} ${lines.length === 1 ? "line" : "lines"}, saved by <code>release</code> before it removed the candidate, redacted (best effort). They can hold customer messages and model output.</p>
          <${Text} value=${lines.join("\n")} block label="candidate log tail" />`
      : null}
    ${data && !data.saved ? html`<p class="muted">No tail is saved: release could not save one (it says why in the job log), or a later release passed and discarded it.</p>` : null}
    <p class="muted small">In a terminal: <code>agentbreeder logs --failed <${Text} value=${name} /></code></p>
  <//>`;
}

/** The gate-failed view for agent `name` and its failed run `run` (lib/gate.js gateFailedRun). */
export function GateFailed({ project, name, run, agentFile }) {
  return html`<div class="gate-failed">
    <${Failures} key=${run} project=${project} name=${name} run=${run} agentFile=${agentFile} />
    <${CandidateTail} project=${project} name=${name} />
  </div>`;
}
