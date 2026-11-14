// Governance ▸ Audit ▸ Eval run (#932): one saved eval report, read with `evals show RUN --json`
// (docs/evals.md). The header is the summary `release` saved; the cases come failed first, with
// their failed checks, the judge's reason and the trace id. Replies and the judge's full
// explanations are read only when asked for (`--content`). Every string is the CLI's redacted,
// capped text, shown through Text (controls made visible), never as markup.

import { RUN_ID, allPassed, caseState, isAgentName, orderedCases, reportApi, scoreText } from "../lib/evals.js";
import { href } from "../lib/router.js";
import { Action, Card, Table, Text, ViewState } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useState } from "../ui/preact.js";

const STATE_WORDS = { failed: "Failed", flaky: "Passed after a retry", passed: "Passed" };

function Fact({ term, children }) {
  return html`<dt>${term}</dt><dd>${children}</dd>`;
}

function present(value) {
  return value !== null && value !== undefined && value !== "";
}

/** A header string, or "not recorded". */
function Recorded({ value, code = false }) {
  if (!present(value)) return html`<span class="muted">not recorded</span>`;
  const text = html`<${Text} value=${String(value)} />`;
  return code ? html`<code>${text}</code>` : text;
}

/** The agent's Releases tab, or nothing for a name the router would refuse. */
function ReleasesLink({ agent }) {
  if (!isAgentName(agent)) return null;
  return html` · <a href=${href("/agents/:name/:tab", { name: agent, tab: "releases" })}>Releases of <${Text} value=${agent} /></a>`;
}

function number(value, digits) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "";
}

function Content({ row }) {
  return html`<details class="case-content">
    <summary>Reply and explanation</summary>
    <p class="small muted">Reply</p>
    <${Text} value=${typeof row.reply === "string" && row.reply ? row.reply : "(empty)"} block label="reply" />
    <p class="small muted">Judge's explanation</p>
    <${Text} value=${typeof row.explanation === "string" && row.explanation ? row.explanation : "(empty)"} block label="judge's explanation" />
  </details>`;
}

function CaseRow({ row, content }) {
  const state = caseState(row);
  const failedChecks = Array.isArray(row.failed_checks) ? row.failed_checks.filter((name) => typeof name === "string") : [];
  return html`<tr>
    <td><code><${Text} value=${String(row.id ?? "")} /></code>${present(row.category) ? html`<div class="small muted"><${Text} value=${String(row.category)} /></div>` : null}</td>
    <td><span class=${`gated ${state === "flaky" ? "passed" : state}`}>${STATE_WORDS[state]}</span>${state === "flaky" ? html`<div class="small muted">${row.attempts} attempts</div>` : null}</td>
    <td>${failedChecks.length ? failedChecks.map((name, index) => html`${index ? ", " : ""}<code><${Text} value=${name} /></code>`) : html`<span class="muted">none</span>`}</td>
    <td class="nowrap">${number(row.correctness, 2)}</td>
    <td>${row.reason ? html`<${Text} value=${String(row.reason)} />` : html`<span class="muted">none</span>`}${content ? html`<${Content} row=${row} />` : null}</td>
    <td class="nowrap">${Number.isInteger(row.latency_ms) ? `${row.latency_ms} ms` : ""}</td>
    <td>${typeof row.trace_id === "string" ? html`<${Text} value=${row.trace_id} copy label="trace id" />` : html`<span class="muted">none</span>`}</td>
  </tr>`;
}

function Report({ project, run, agent }) {
  const [content, setContent] = useState(false);
  const [view, reload] = useView(reportApi(project.id, run, { agent, content }), project.id);
  const report = view.data;
  // useView keeps the last answer while a content read is in flight: it stays, marked busy.
  const notes = report && Array.isArray(report.notes) ? report.notes.filter((note) => typeof note === "string") : [];
  const cases = orderedCases(report);
  const shown = Boolean(report && report.content);
  // A content read that failed: the report without replies is one click away.
  const contentFailed = content && view.error && !view.loading;
  return html`
    <p class="small"><a href=${href("/audit")}>Back to Audit</a>${report ? html`<${ReleasesLink} agent=${report.agent} />` : null}</p>
    <${Card} title="Eval run" busy=${view.loading}>
      ${report
        ? html`<p class=${`verdict ${allPassed(report) ? "ok" : "bad"}`} role="status"><${Text} value=${scoreText(report)} /></p>
            <dl class="facts">
              <${Fact} term="Run"><code><${Text} value=${String(report.run_id)} /></code><//>
              <${Fact} term="Agent"><${Recorded} value=${report.agent} /><//>
              <${Fact} term="Eval set"><${Recorded} value=${report.eval_set} code /><//>
              <${Fact} term="Judge"><${Recorded} value=${report.judge} /><//>
              <${Fact} term="Mean correctness">${number(report.mean_correctness, 2) || html`<span class="muted">not recorded</span>`}<//>
              <${Fact} term="Median latency">${Number.isFinite(report.p50_latency_ms) ? `${report.p50_latency_ms} ms` : html`<span class="muted">not recorded</span>`}<//>
              <${Fact} term="Evaluated endpoint"><${Recorded} value=${report.endpoint} code /> <span class="muted small">(the candidate; release removes it afterwards)</span><//>
              <${Fact} term="Report file"><code><${Text} value=${`deploy/${String(report.file ?? "")}`} /></code><//>
            </dl>
            ${notes.length ? html`<ul class="small">${notes.map((note, index) => html`<li key=${index}><${Text} value=${note} /></li>`)}</ul>` : null}`
        : null}
      ${contentFailed && view.stale
        ? html`<p class="muted"><${Text} value=${(view.error && view.error.message) || "The replies could not be read."} /></p>`
        : html`<${ViewState} view=${view} />`}
      ${view.error && (!report || contentFailed)
        ? html`<div class="row-actions">
            <${Action} small tone="secondary" onClick=${reload}>Retry<//>
            ${content ? html`<${Action} small tone="secondary" onClick=${() => setContent(false)}>Show the report without replies<//>` : null}
          </div>`
        : null}
    <//>
    ${report
      ? html`<${Card} title="Cases" busy=${view.loading}>
          <p class="muted small">${cases.length} ${cases.length === 1 ? "case" : "cases"} shown, failed first.</p>
          <div class="row-actions">
            <${Action} small tone="secondary" disabled=${view.loading} onClick=${() => (content && !shown ? reload() : setContent(!shown))}>${shown ? "Hide replies and explanations" : "Show replies and explanations"}<//>
          </div>
          ${cases.length
            ? html`<${Table} label="Eval cases, failed first" columns=${["Case", "Result", "Failed checks", "Correctness", "Judge's reason", "Latency", "Trace"]}>
                ${cases.map((row, index) => html`<${CaseRow} key=${index} row=${row} content=${shown} />`)}
              <//>`
            : html`<p class="muted">This report has no readable cases.</p>`}
          <p class="muted small">Replies, reasons and explanations are model output, redacted and capped by the CLI. Read a trace with <code>agentbreeder traces --trace ID</code>.</p>
        <//>`
      : null}
  `;
}

export function EvalReport({ project, params }) {
  const run = params && params.run;
  if (!RUN_ID.test(String(run || ""))) {
    return html`<p>That is not an eval run id. <a href=${href("/audit")}>Back to Audit</a>.</p>`;
  }
  // A named agent outside the rule is refused, never dropped: without it the run could be
  // another agent's.
  if (params.agent !== undefined && !isAgentName(params.agent)) {
    return html`<p>That is not an agent name. <a href=${href("/audit")}>Back to Audit</a>.</p>`;
  }
  return html`<${Report} key=${`${run} ${params.agent || ""}`} project=${project} run=${run} agent=${params.agent || null} />`;
}

const VIEWER = { title: (params) => `Eval run ${params.run}`, navAs: "/audit", scoped: true, screen: EvalReport };
/** The routes, for screens/index.js: a run alone, or a run and the agent it belongs to. */
export const EVAL_ROUTES = [
  { pattern: "/audit/evals/:run", ...VIEWER },
  { pattern: "/audit/evals/:run/:agent", ...VIEWER },
];
