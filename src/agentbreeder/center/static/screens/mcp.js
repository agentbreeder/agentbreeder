// MCP (#831, M15 console half, part 1): the project's MCP servers, the tools stack.lock pinned for
// each, each agent's grants and each relay's state. Read-only: grants are edited with
// `agentbreeder mcp grant|revoke` and take effect at the next `services up`.

import { grantRows, repinCommand, serverRows } from "../lib/mcp.js";
import { href } from "../lib/router.js";
import { Card, Table, Text, ViewState } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { LogsLink } from "./logs.js";
import { html } from "../ui/preact.js";

export function Mcp({ project }) {
  const base = `/api/projects/${project.id}`;
  const [files] = useView(`${base}/mcp`, project.id);
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
  `;
}

function Server({ server, project }) {
  return html`<${Card} title=${`mcp.${server.name}`}>
    <dl class="facts">
      <dt>Version</dt><dd><code><${Text} value=${server.ref} /></code></dd>
      <dt>Origin</dt><dd>${server.origin ? html`<${Text} value=${server.origin} />` : html`<span class="muted">not locked yet</span>`}</dd>
      <dt>Upstream</dt><dd>${server.upstream ? html`<code><${Text} value=${server.upstream} /></code>` : html`<span class="muted">not locked yet</span>`}</dd>
      <dt>Relay</dt><dd><code><${Text} value=${server.relay} /></code> <${Text} value=${server.relayState || "status unknown"} /> <${LogsLink} kind="service" name=${server.relay} label=${`Logs of ${server.relay}`} /></dd>
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
    <p class="muted small">From <code>environment.agents</code> in stack.yaml. A grant takes effect at the next <code>services up</code>, and an agent uses a tool only when its <code>agent.yaml</code> asks for it. Edit grants with <code>agentbreeder mcp grant|revoke</code>.</p>
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
