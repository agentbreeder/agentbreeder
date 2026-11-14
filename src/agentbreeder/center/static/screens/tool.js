// A tool version's detail (#828, M12 console half), mounted inside the registry's version card:
// its functions (tool.yaml's description and parameters, which the model sees, and the Python
// signature the CLI read with `ast`, never importing the code), its dependencies and who may use
// it. The source is the version's Files list below. Everything from tool.yaml renders as text.

import { functionRows } from "../lib/tools.js";
import { href } from "../lib/router.js";
import { Table, Text } from "../ui/components.js";
import { html } from "../ui/preact.js";

function Parameters({ rows }) {
  if (!rows.length) return html`<span class="muted">none</span>`;
  return html`<ul class="params">${rows.map((row) => html`<li key=${row.name}>
    <code><${Text} value=${row.name} /></code> <span class="muted small"><${Text} value=${row.type} />${row.required ? ", required" : ""}</span>
    ${row.description ? html`<div class="small"><${Text} value=${row.description} /></div>` : null}
  </li>`)}</ul>`;
}

/** The tool parts of one version's card: null for any other kind.
 * Dependencies name `runtime/adk`, the only runtime today: name the agent's runtime when there
 * are more. */
export function ToolFacts({ data }) {
  const functions = functionRows(data);
  if (!functions) return null;
  return html`<h3>Functions</h3>
    <p class="muted small">From <code>tool.yaml</code> (what the model sees) and the module's source, read as text: the console and the CLI never import tool code.</p>
    ${functions.error
      ? html`<p class="notice error" role="status"><${Text} value=${`tool.yaml could not be read: ${functions.error}`} /></p>`
      : functions.rows.length
        ? html`<${Table} label=${`Functions of ${data.ref}`} columns=${["Name", "Python signature", "Description", "Parameters"]}>
            ${functions.rows.map((row) => html`<tr key=${row.key}>
              <td><code><${Text} value=${row.name} /></code><div class="muted small"><${Text} value=${`${row.module}.${row.function}`} /></div></td>
              <td>${row.signature ? html`<code><${Text} value=${row.signature} /></code>` : html`<span class="muted small"><${Text} value=${row.why} /></span>`}</td>
              <td>${row.description ? html`<${Text} value=${row.description} />` : html`<span class="muted">none</span>`}</td>
              <td><${Parameters} rows=${row.parameters} /></td>
            </tr>`)}
          <//>`
        : html`<p class="muted">tool.yaml declares no functions.</p>`}
    <dl class="facts">
      <dt>Dependencies</dt>
      <dd>None of its own: a tool runs with the locked packages of its agent's runtime (<a href=${href("/registry/runtimes/adk")}><code>runtime/adk</code></a>).</dd>
      <dt>Grants</dt>
      <dd>Available to the agents that reference it in <code>agent.yaml</code> (see Used by below). This code runs inside those agents.</dd>
    </dl>`;
}
