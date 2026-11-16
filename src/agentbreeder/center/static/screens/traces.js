// The Traces tab of an agent (#907): one deployment's recent traces from the stack's Phoenix,
// through `traces ID --last N --json` (center/traces.py). Each trace is a summary line (start,
// status, latency, models, tokens, spans) and its tool calls; its span tree opens under it through
// the Try it trace panel (`traces --trace ID`). The CLI never reads prompts, replies or tool
// arguments and results, and redacts every string as for `logs`; it is all shown through <Text>.

import { agentName } from "../lib/agents.js";
import { isCandidate } from "../lib/format.js";
import { visible } from "../lib/text.js";
import { DEFAULT_LAST, LAST_CHOICES, arrivingText, defaultDeployment, startText, toolCallsText, traceLine, tracesPath, tracesProblemText, windowText } from "../lib/traces.js";
import { productionRow, tracePath } from "../lib/tryit.js";
import { Action, Card, CopyButton, Notice, Text } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useState } from "../ui/preact.js";
import { TracePanel } from "./try.js";

function TraceItem({ project, trace }) {
  const tools = toolCallsText(trace);
  const path = tracePath(project.id, trace.trace_id);
  return html`<li class="trace-item">
    <div><strong><${Text} value=${startText(trace.start_time)} /></strong> <span class="small"><${Text} value=${traceLine(trace)} /></span></div>
    <div class="small">Tool calls: ${tools ? html`<${Text} value=${tools} />` : html`<span class="muted">none</span>`}</div>
    <div class="small muted">Trace <code><${Text} value=${trace.trace_id} /></code> <${CopyButton} value=${trace.trace_id} label="trace ID" /></div>
    ${path ? html`<${TracePanel} path=${path} />` : null}
  </li>`;
}

function TraceList({ project, deployment, last }) {
  const path = tracesPath(project.id, deployment, last);
  const [view, reload] = useView(path, project.id);
  if (!path) return html`<p class="muted">This deployment's ID cannot be read, so its traces cannot be asked for.</p>`;
  const answer = view.data;
  const traces = answer && Array.isArray(answer.traces) ? answer.traces : [];
  const notes = answer && Array.isArray(answer.notes) ? answer.notes.filter((note) => typeof note === "string") : [];
  const matched = answer ? windowText(answer) : null;
  const arriving = answer ? arrivingText(answer) : null;
  return html`<div aria-busy=${view.loading ? "true" : "false"}>
    <p class="muted small" role="status">${view.loading ? "Reading traces from Phoenix…" : answer ? `${traces.length} trace${traces.length === 1 ? "" : "s"} read, newest first.` : ""}</p>
    ${view.error && !view.loading ? html`<${Notice} notice=${{ tone: "error", text: tracesProblemText(view.error) }} />` : null}
    ${answer && !view.loading && !traces.length && !view.error ? html`<p class="muted">Phoenix has no traces for this deployment yet. A trace appears a few seconds after a turn (Try it) or an eval case.</p>` : null}
    ${matched ? html`<p class="muted small"><${Text} value=${matched} /></p>` : null}
    ${arriving ? html`<p class="small"><${Text} value=${arriving} /></p>` : null}
    ${notes.map((note, index) => html`<p class="muted small" key=${index}><${Text} value=${note} /></p>`)}
    ${answer && answer.truncated ? html`<p class="muted small">Phoenix returned more than is read; the list is partial. Ask for fewer traces.</p>` : null}
    ${answer && answer.dropped ? html`<p class="muted small">${answer.dropped} trace${answer.dropped === 1 ? " was" : "s were"} left out: not this deployment's, or not readable.</p>` : null}
    ${traces.length ? html`<ul class="plain trace-list" aria-label="Traces, newest first">${traces.map((trace) => html`<${TraceItem} key=${trace.trace_id} project=${project} trace=${trace} />`)}</ul>` : null}
    <div class="row-actions"><${Action} small tone="secondary" disabled=${view.loading} onClick=${reload}>Read again<//></div>
  </div>`;
}

export function Traces({ project, name, rows, loading = false }) {
  const own = Array.isArray(rows) ? rows.filter((row) => row && agentName(row) === name && typeof row.id === "string") : [];
  const [chosen, setChosen] = useState("");
  const [last, setLast] = useState(DEFAULT_LAST);
  const deployment = own.some((row) => row.id === chosen) ? chosen : defaultDeployment(own, productionRow(own, name));
  return html`<${Card} title="Traces" busy=${loading}>
    ${own.length
      ? html`<div class="row">
            <label class="field">
              <span>Deployment</span>
              <select value=${deployment} onChange=${(e) => setChosen(e.currentTarget.value)}>
                ${own.map((row) => html`<option value=${row.id}>${visible(`${row.name} (${row.target}, ${row.state}, ${String(row.id).slice(0, 8)})${isCandidate(row) ? ", candidate" : ""}`)}</option>`)}
              </select>
            </label>
            <label class="field">
              <span>Show</span>
              <select value=${String(last)} onChange=${(e) => setLast(Number(e.currentTarget.value))}>
                ${LAST_CHOICES.map((n) => html`<option value=${String(n)}>Newest ${n} traces</option>`)}
              </select>
            </label>
          </div>
          <${TraceList} key=${`${deployment} ${last}`} project=${project} deployment=${deployment} last=${last} />`
      : loading
        ? html`<p class="muted">Loading deployments…</p>`
        : html`<p class="muted">No deployment of this agent in this project, so there are no traces to read.</p>`}
    <p class="muted small">From this stack's Phoenix (<code>agentbreeder traces</code>). Prompts, replies and tool arguments are never read. Error text in a span tree is the runtime's own and can quote what the agent was given.</p>
  <//>`;
}
