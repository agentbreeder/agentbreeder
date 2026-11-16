// LLM cost (#906, console half): `cost --json` for one project, read fresh by the server (an
// uncached, tracked CLI child; the gateway's master key stays with the CLI). Home shows a project's
// total and its models; an agent's Overview shows its own deployment keys (removed ones included)
// and their models. Amounts are LiteLLM's spend: its estimate from its price list, not a bill.

import { WINDOWS, agentCost, asOfText, costNotes, costPath, costRefusal, count, keyLabel, modelLabel, money, partial, windowText } from "../lib/cost.js";
import { Action, Card, Skeleton, Table, Text, nextId, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useRef, useState } from "../ui/preact.js";

const SOURCE = "LiteLLM spend: its estimate from its model price list, not a bill. Spend logs arrive about a minute after a call.";

function WindowPicker({ days, onDays, id }) {
  return html`<label class="field" for=${id}>
    <span>Window</span>
    <select id=${id} value=${String(days)} onChange=${(event) => onDays(Number(event.currentTarget.value))}>
      ${WINDOWS.map((n) => html`<option key=${n} value=${String(n)}>Last ${n} days</option>`)}
    </select>
  </label>`;
}

function Refusal({ error }) {
  const refusal = costRefusal(error);
  return html`<div class="notice error" role="alert"><span><${Text} value=${`Unavailable: ${refusal.reason}`} /><br /><span class="small"><${Text} value=${`Next: ${refusal.next}.`} /></span></span></div>`;
}

/** The facts every cost view starts with: the total with its currency, the window, the read time. */
function Total({ answer, total }) {
  return html`<p role="status" class="cost-total">
    <strong><${Text} value=${money(total.cost_usd, answer.currency)} /></strong>
    <span class="muted small"> <${Text} value=${`${count(total.calls)} calls, ${count(total.failed)} failed; ${windowText(answer)}; ${asOfText(answer)}`} /></span>
  </p>`;
}

function Counts({ item, currency }) {
  return html`<td class="num">${count(item.calls)}</td>
    <td class="num">${count(item.prompt_tokens)}</td>
    <td class="num">${count(item.cached_prompt_tokens)}</td>
    <td class="num">${count(item.completion_tokens)}</td>
    <td class="num"><${Text} value=${money(item.cost_usd, currency)} /></td>`;
}

// "Cached in" is the part of "Tokens in" the provider read from its cache (#1056); "-" when the
// CLI does not report it.
const COUNT_COLUMNS = ["Calls", "Tokens in", "Cached in", "Tokens out", "Spend"];

function ModelTable({ label, models, currency }) {
  return html`<${Table} label=${label} columns=${["Model", ...COUNT_COLUMNS]}>
    ${models.map((model, index) => html`<tr key=${index}>
      <td><code><${Text} value=${modelLabel(model)} /></code></td>
      <${Counts} item=${model} currency=${currency} />
    </tr>`)}
  <//>`;
}

function Notes({ answer, total, extra = [], keys = true }) {
  return html`${[...costNotes(answer, total, { keys }), ...extra].map((note, index) => html`<p key=${index} class="muted small"><${Text} value=${note} /></p>`)}`;
}

function useCost(project, days) {
  return useView(costPath(project.id, days), project.id);
}

function Controls({ days, onDays, view, reload, children }) {
  const id = useRef(nextId("cost-window")).current;
  return html`<div class="row cost-controls">
    ${children}
    <${WindowPicker} id=${id} days=${days} onDays=${onDays} />
    <${Action} tone="ghost" small onClick=${reload} disabled=${view.loading ? "Reading the cost." : false}>Refresh<//>
  </div>`;
}

function CostBody({ view, empty, render, keys = true }) {
  const answer = view.data && !view.error ? view.data : null;
  return html`
    ${view.loading && !answer && !view.error ? html`<${Skeleton} />` : null}
    ${view.error ? html`<${Refusal} error=${view.error} />` : null}
    ${answer ? (answer.total.calls ? render(answer) : html`<p class="muted">${empty} <span class="small"><${Text} value=${`${windowText(answer)}; ${asOfText(answer)}.`} /></span></p><${Notes} answer=${answer} total=${answer.total} keys=${keys} />`) : null}
  `;
}

/** Home: one registered project's LLM cost (the one in scope first), its total and its models. */
export function HomeCost({ projects }) {
  const { project: scoped } = useConsole();
  const [picked, setPicked] = useState(null);
  const [days, setDays] = useState(30);
  const pickId = useRef(nextId("cost-project")).current;
  const project = projects.find((p) => p.id === picked) || (scoped && projects.find((p) => p.id === scoped.id)) || projects[0];
  const [view, reload] = useCost(project, days);
  return html`<${Card} title="LLM cost" busy=${view.loading}>
    <p class="muted small">${SOURCE}</p>
    <${Controls} days=${days} onDays=${setDays} view=${view} reload=${reload}>
      ${projects.length > 1
        ? html`<label class="field" for=${pickId}>
            <span>Project</span>
            <select id=${pickId} value=${project.id} onChange=${(event) => setPicked(event.currentTarget.value)}>
              ${projects.map((p) => html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
            </select>
          </label>`
        : null}
    <//>
    <${CostBody} view=${view} empty="No model calls by this project's deployments in this window." render=${(answer) => html`
      <${Total} answer=${answer} total=${answer.total} />
      <${ModelTable} label="Spend per model" models=${answer.models} currency=${answer.currency} />
      <${Notes} answer=${answer} total=${answer.total} keys=${false} />
    `} keys=${false} />
    <p class="muted small">Per agent: open the agent in Agents. Only AgentBreeder's own gateway keys are counted (deployments and eval judges), removed deployments included.</p>
  <//>`;
}

/** An agent's Overview: its deployment keys (by name, candidates and removed ones included) and
 * their models, from the project's cost read. */
export function AgentCost({ project, name }) {
  const [days, setDays] = useState(30);
  const [view, reload] = useCost(project, days);
  return html`<${Card} title="LLM cost" busy=${view.loading}>
    <p class="muted small">${SOURCE}</p>
    <${Controls} days=${days} onDays=${setDays} view=${view} reload=${reload} />
    <${CostBody} view=${view} empty="No model calls by this project's deployments in this window." render=${(answer) => {
      const own = agentCost(answer, name);
      if (!own.keys.length) {
        const where = partial(answer) ? "in the part of this window that was read" : "in this window";
        return html`<p class="muted">No model calls by this agent's deployments ${where}. <span class="small"><${Text} value=${`${windowText(answer)}; ${asOfText(answer)}.`} /></span></p>
          <${Notes} answer=${answer} total=${answer.total} />`;
      }
      return html`
        <${Total} answer=${answer} total=${own.total} />
        <${Table} label="Spend per deployment" columns=${["Deployment", "Target, state", ...COUNT_COLUMNS]}>
          ${own.keys.map((key) => html`<tr key=${key.key_alias}>
            <td><${Text} value=${keyLabel(key)} /><br /><code class="small"><${Text} value=${key.id} /></code></td>
            <td><${Text} value=${`${key.target || "-"}, ${key.state || "-"}`} /></td>
            <${Counts} item=${key} currency=${answer.currency} />
          </tr>`)}
        <//>
        <${ModelTable} label="Spend per model" models=${own.models} currency=${answer.currency} />
        <${Notes} answer=${answer} total=${own.total} extra=${own.modelsCut ? ["A deployment used more models than are listed; the per-model rows are partial."] : []} />
      `;
    }} />
  <//>`;
}
