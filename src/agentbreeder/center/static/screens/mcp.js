// MCP (#831, M15 console half): the project's MCP servers, the tools stack.lock pinned for each,
// each agent's grants and each relay's state (part 1), and grant or revoke through `agentbreeder
// mcp grant|revoke`, reviewed first (part 2). A grant takes effect at the next `services up`.

import { grantChoices, grantedTools, grantProblem, grantRows, grantWriteProblem, grantSteps, repinCommand, serverRows } from "../lib/mcp.js";
import { href } from "../lib/router.js";
import { visible } from "../lib/text.js";
import { Action, Actions, Card, Notice, Table, Text, ViewState, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { LogsLink } from "./logs.js";
import { html, useState } from "../ui/preact.js";

export function Mcp({ project }) {
  const base = `/api/projects/${project.id}`;
  const [files, reloadFiles] = useView(`${base}/mcp`, project.id);
  const [status] = useView(`${base}/services`, project.id);
  const servers = serverRows(files.data, status.data);
  const grants = grantRows(files.data);
  const none = files.data && !servers.length;
  return html`
    <${Card} title="MCP servers" busy=${files.loading || status.loading}>
      <p class="muted small">Each server runs behind its own relay (<code>mcp-NAME</code>), which holds the credential and serves only the tools stack.lock pinned. See <a href=${href("/services")}>Services</a> to review and start the stack.</p>
      <${ViewState} view=${files} empty=${none ? "This stack has no MCP servers (stack.yaml `mcp:`)." : null} />
      ${status.error ? html`<p class="notice warn" role="status">Relay status unavailable: <${Text} value=${status.error.message || status.error.code || "unknown"} /></p>` : null}
    <//>
    ${servers.map((server) => html`<${Server} key=${server.name} server=${server} project=${project} />`)}
    ${files.data && servers.length ? html`<${Grants} rows=${grants} />` : null}
    ${files.data && servers.length ? html`<${ChangeGrant} project=${project} files=${files.data} servers=${servers} onChanged=${reloadFiles} />` : null}
  `;
}

function Server({ server, project }) {
  return html`<${Card} title=${`mcp.${server.name}`}>
    <dl class="facts">
      <dt>Version</dt><dd><code><${Text} value=${server.ref} /></code></dd>
      <dt>Origin</dt><dd>${server.origin ? html`<${Text} value=${server.origin} />` : html`<span class="muted">not locked yet</span>`}</dd>
      <dt>Upstream</dt><dd>${server.upstream ? html`<code><${Text} value=${server.upstream} /></code>` : html`<span class="muted">not locked yet</span>`}</dd>
      <dt>Relay</dt><dd><code><${Text} value=${server.relay} /></code> <${Text} value=${server.relayState || "status unknown"} /> ${server.relayLogs ? html`<${LogsLink} kind="service" name=${server.relay} label=${`Logs of ${server.relay}`} />` : null}</dd>
      <dt>Upstream check</dt><dd>${server.stateText ? html`<${Text} value=${server.stateText} />` : html`<span class="muted">unknown</span>`}${server.error ? html` <code><${Text} value=${server.error} /></code>` : null}</dd>
    </dl>
    ${server.ref && !server.lockCurrent ? html`<p class="notice warn" role="status">stack.lock has no pins for this version yet. The next <code>services up</code> asks to confirm the server and pins its tools.</p>` : null}
    ${server.state === "drift" ? html`<p class="notice warn" role="status">A pinned tool changed or went missing upstream. The relay hides it until you review the change in a terminal: <code><${Text} value=${repinCommand(project.path, project.environment)} /></code></p>` : null}
    ${server.tools.length
      ? html`<${Table} label=${`Pinned tools of mcp.${server.name}`} columns=${["Tool", "Pin", "State"]}>
          ${server.tools.map((tool) => html`<tr key=${tool.name}>
            <td><code><${Text} value=${tool.name} /></code></td>
            <td><code><${Text} value=${tool.hash} /></code></td>
            <td><${Text} value=${tool.stateText} /></td>
          </tr>`)}
        <//>`
      : html`<p class="muted">No tools pinned.</p>`}
    ${server.unpinned.length ? html`<p class="muted small">Served upstream but not pinned (the relay does not serve them): <${Text} value=${server.unpinned.join(", ")} /></p>` : null}
  <//>`;
}

function Grants({ rows }) {
  return html`<${Card} title="Grants per agent">
    <p class="muted small">From <code>environment.agents</code> in stack.yaml. A grant takes effect at the next <code>services up</code>, and an agent uses a tool only when its <code>agent.yaml</code> asks for it. Change one below, or with <code>agentbreeder mcp grant|revoke</code>.</p>
    ${rows.length
      ? html`<${Table} label="Grants per agent" columns=${["Agent", "Server", "Tools"]}>
          ${rows.map((row) => html`<tr key=${`${row.agent} ${row.server}`}>
            <td><${Text} value=${row.agent} /></td>
            <td>${row.server ? html`<code><${Text} value=${row.server} /></code>` : html`<span class="muted">none</span>`}</td>
            <td>${row.tools.length
              ? row.tools.map((tool, index) => html`${index ? ", " : ""}<code><${Text} value=${tool.name} /></code>${tool.pinned ? "" : html` <span class="muted small">(not pinned yet)</span>`}`)
              : html`<span class="muted">no MCP tools</span>`}</td>
          </tr>`)}
        <//>`
      : html`<p class="muted">stack.yaml lists no agents.</p>`}
  <//>`;
}

// -- grant and revoke (#831 part 2): a dry run (the diff and the digest of stack.yaml), then the
// write bound to that digest, as "Move agent to this version" ------------------------------------

async function send(client, path, body) {
  try {
    const reply = await client.request(path, { body });
    return { data: reply.error ? null : reply.data, problem: reply.error };
  } catch (error) {
    if (error.kind === "session") throw error;
    return { data: null, problem: { code: error.code, message: error.message } };
  }
}

function Steps({ steps }) {
  return html`<ul class="plain">${steps.map((step, index) => html`<li key=${index}><${Text} value=${step.text} /> <code><${Text} value=${step.command} /></code></li>`)}</ul>`;
}

function ChangeGrant({ project, files, servers, onChanged }) {
  const { client, busyReason } = useConsole();
  const agents = (Array.isArray(files.agents) ? files.agents : []).map((agent) => agent.name);
  const [agentChoice, setAgent] = useState(agents[0] || "");
  const [serverChoice, setServer] = useState(servers[0] ? servers[0].name : "");
  // After a reload (a write here or an edit elsewhere) a chosen name may be gone: fall back to
  // the first, so the form never posts a name the project no longer has.
  const agent = agents.includes(agentChoice) ? agentChoice : agents[0] || "";
  const server = servers.some((item) => item.name === serverChoice) ? serverChoice : servers[0] ? servers[0].name : "";
  const [verb, setVerb] = useState("grant");
  const [picked, setPicked] = useState([]);
  const [review, setReview] = useState(null); // the dry run's summary
  const [done, setDone] = useState(null); // the write's summary
  const [notice, setNotice] = useState(null);
  const [sending, setSending] = useState(false);
  const base = `/api/projects/${project.id}`;
  const chosen = servers.find((item) => item.name === server);
  const ref = chosen ? chosen.ref : "";
  const [declared] = useView(ref ? `${base}/mcp/tools?ref=${encodeURIComponent(ref)}` : null, project.id);
  const granted = grantedTools(files, agent, server);
  const choices = grantChoices(verb, declared.data, granted);
  const busy = busyReason(project.id) || sending;
  // Any change of choice is a new form: a review of other choices never carries over.
  const reset = (apply) => (event) => { apply(event.currentTarget.value); setPicked([]); setReview(null); setNotice(null); setDone(null); };
  const toggle = (name) => { setPicked(picked.includes(name) ? picked.filter((item) => item !== name) : [...picked, name]); setReview(null); setDone(null); };
  // Only tools still offered are sent (the files may have reloaded since they were ticked).
  const sent = picked.filter((name) => choices.some((tool) => tool.name === name));

  const ask = async (extra) => {
    setSending(true);
    setNotice(null);
    const { data, problem } = await send(client, `${base}/mcp/grant`, { verb, agent, server, tools: sent, ...extra });
    setSending(false);
    return { data, problem };
  };
  const preview = async () => {
    setDone(null);
    const { data, problem } = await ask({});
    if (problem) setNotice({ tone: "error", text: grantProblem(problem).text });
    else setReview(data);
  };
  const write = async () => {
    const { data, problem } = await ask({ base: review.base_sha256 });
    if (problem) {
      // A refusal before the write keeps nothing to retry; any other problem (a timeout, an
      // answer that fails its check) may come after the file changed: read the grants again.
      const shown = grantWriteProblem(problem);
      setReview(null);
      setNotice({ tone: "error", text: shown.text });
      if (shown.uncertain) onChanged();
      return;
    }
    setReview(null);
    setPicked([]);
    setDone(data);
    onChanged();
  };

  if (!agents.length || !servers.length) return null;
  return html`<${Card} title="Change a grant">
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <p class="muted small">Edits <code>environment.agents.AGENT.mcp</code> in stack.yaml through <code>agentbreeder mcp grant|revoke</code>: review the change first, then write exactly that.</p>
    <div class="stack">
      <label for="grant-agent">Agent</label>
      <select id="grant-agent" value=${agent} onChange=${reset(setAgent)}>${agents.map((name) => html`<option key=${name} value=${name}>${visible(name)}</option>`)}</select>
      <label for="grant-server">MCP server</label>
      <select id="grant-server" value=${server} onChange=${reset(setServer)}>${servers.map((item) => html`<option key=${item.name} value=${item.name}>${visible(item.name)}</option>`)}</select>
      <fieldset>
        <legend>Change</legend>
        <label><input type="radio" name="grant-verb" value="grant" checked=${verb === "grant"} onChange=${reset(setVerb)} /> Grant tools</label>
        <label><input type="radio" name="grant-verb" value="revoke" checked=${verb === "revoke"} onChange=${reset(setVerb)} /> Revoke tools</label>
      </fieldset>
      <fieldset>
        <legend>Tools</legend>
        ${verb === "grant" && declared.error ? html`<p class="notice warn" role="status"><${Text} value=${declared.error.message || "The declared tools could not be read."} /></p>` : null}
        ${choices.length
          ? choices.map((tool) => html`<label key=${tool.name}><input type="checkbox" checked=${picked.includes(tool.name)} onChange=${() => toggle(tool.name)} /> <code><${Text} value=${tool.name} /></code> <span class="muted small"><${Text} value=${tool.effect || "no longer declared"} /></span></label>`)
          : html`<p class="muted"><${Text} value=${verb === "grant" ? (declared.loading ? "Reading the declared tools…" : "Every declared tool is granted already.") : "This agent has no tools on this server."} /></p>`}
      </fieldset>
    </div>
    <${Actions} disabled=${busy}>
      <${Action} disabled=${!sent.length && "Choose at least one tool."} onClick=${preview}>Review change<//>
    <//>
    ${review ? html`<${GrantReview} summary=${review} project=${project} busy=${busy} onWrite=${write} />` : null}
    ${done ? html`<div role="status">
      <p><${Text} value=${done.changed ? `${done.verb === "grant" ? "Granted" : "Revoked"} ${done.moved.join(", ")} for ${done.agent} on mcp.${done.server}.` : "Nothing to change."} /></p>
      ${done.changed ? html`<${Steps} steps=${grantSteps(done, project.path, project.environment)} />` : null}
    </div>` : null}
  <//>`;
}

function GrantReview({ summary, project, busy, onWrite }) {
  if (!summary.changed) {
    return html`<p class="muted" role="status"><${Text} value=${summary.verb === "grant" ? "Already granted: nothing to write." : "Not granted: nothing to write."} /></p>`;
  }
  const now = summary.tools.map((tool) => `${tool.name} (${tool.effect || "undeclared"})`).join(", ");
  return html`<div>
    <h3>Review</h3>
    <${Text} block value=${summary.diff} label="stack.yaml change" />
    <p><${Text} value=${`After this, ${summary.agent} has on mcp.${summary.server}: ${now || "no tools"}.`} /></p>
    <p class="muted small">It takes effect at the next <code>services up</code>, and the agent uses a tool only when its <code>agent.yaml</code> asks for it (<code>mcp: [{ref, tools}]</code>).</p>
    ${summary.in_use.length ? html`<div class="notice warn" role="status"><${Steps} steps=${grantSteps(summary, project.path, project.environment).slice(0, -1)} /><p>The revoke is still written.</p></div>` : null}
    <p class="muted small">stack.yaml digest ${summary.base_sha256.slice(7, 23)}…</p>
    <${Actions} disabled=${busy}>
      <${Action} onClick=${onWrite}>${summary.verb === "grant" ? "Write grant" : "Write revoke"}<//>
    <//>
  </div>`;
}
