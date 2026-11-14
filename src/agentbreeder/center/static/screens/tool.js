// A tool version's detail (#828, M12 console half), mounted inside the registry's version card:
// its functions (tool.yaml's description and parameters, which the model sees, and the Python
// signature the CLI read with `ast`, never importing the code), its dependencies and who may use
// it. The source is the version's Files list below. Everything from tool.yaml renders as text.

import { argsText, functionRows, LOG_LIMITS, LOG_WINDOWS, logPath, logRefusal, logRows, seenAgents } from "../lib/tools.js";
import { href } from "../lib/router.js";
import { Action, nextId, Skeleton, Table, Text, useConsole } from "../ui/components.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";

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
export function ToolFacts({ data, project }) {
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
    </dl>
    ${project ? html`<${ToolLog} key=${data.ref} project=${project} versionRef=${data.ref} />` : null}`;
}

// -- the execution log (#828 part 2): `tool log --json`, read fresh on every change ----------------

/** One read per path: a new path drops the previous answer at once (it may hold arguments, which
 * customer data a change of choice must not leave on screen); Refresh keeps it while it reads. */
function useToolLog(path) {
  const { client } = useConsole();
  const [state, setState] = useState({ loading: false, data: null, error: null });
  const [tick, setTick] = useState(0);
  const shown = useRef(path);
  useEffect(() => {
    if (!path) return undefined;
    let live = true;
    const same = shown.current === path;
    shown.current = path;
    setState((previous) => (same ? { ...previous, loading: true } : { loading: true, data: null, error: null }));
    client
      .request(path)
      .then((reply) => live && setState({ loading: false, data: reply.data, error: reply.error }))
      .catch((error) => {
        if (!live || error.kind === "session") return;
        setState({ loading: false, data: null, error: { code: error.code, message: error.message } });
      });
    return () => {
      live = false;
    };
  }, [path, tick]);
  return [state, () => setTick((n) => n + 1)];
}

function Select({ label, value, onChange, children }) {
  return html`<label class="field"><span>${label}</span><select value=${value} onChange=${(event) => onChange(event.currentTarget.value)}>${children}</select></label>`;
}

/**
 * The calls the agent runtime recorded for this tool. It is what the runtime claims it called
 * (the agent's database role can insert any row). Arguments and error text are customer data:
 * read only after "Show arguments", for this view, and dropped on "Hide arguments" or any change.
 */
function ToolLog({ project, versionRef }) {
  const name = versionRef.split("@")[0];
  const [scope, setScope] = useState("version");
  const [since, setSince] = useState("24h");
  const [limit, setLimit] = useState("100");
  const [agent, setAgent] = useState("");
  const [content, setContent] = useState(false);
  const [agents, setAgents] = useState([]);
  const heading = useRef(nextId("log")).current;
  const ref = scope === "all" ? name : versionRef;
  const path = logPath(project.id, { ref, since, agent, limit: Number(limit), content });
  const [log, reload] = useToolLog(path);
  const change = (setter) => (value) => {
    setContent(false);
    setter(value);
  };
  // A new scope offers its own agents: the filter and its list reset in the same render, so the
  // read for the new scope is the only one.
  const changeScope = (value) => {
    setContent(false);
    setAgent("");
    setAgents([]);
    setScope(value);
  };
  const rows = log.data && !log.error ? logRows(log.data) : [];
  useEffect(() => {
    // The agent filter offers the agents seen in this scope (capped: rows are what the runtime
    // claims, so their names are untrusted); a filtered read only narrows it.
    const next = seenAgents(agents, rows);
    if (next.length !== agents.length) setAgents(next);
  }, [log.data]);

  const refusal = log.error ? logRefusal(log.error, ref) : null;
  const data = log.data && !log.error ? log.data : null;
  return html`<section class="toollog" aria-labelledby=${heading} aria-busy=${log.loading ? "true" : "false"}>
    <h3 id=${heading}>Execution log</h3>
    <p class="muted small">The calls the agent runtime recorded for this tool, newest first. It is what the runtime claims it called: the agent's database role can write any row. Results are not stored; the trace holds them.</p>
    <div class="row">
      <${Select} label="Versions" value=${scope} onChange=${changeScope}>
        <option value="version">${versionRef.split("@")[1]} only</option>
        <option value="all">Every version of ${name}</option>
      <//>
      <${Select} label="Window" value=${since} onChange=${change(setSince)}>
        ${LOG_WINDOWS.map((window) => html`<option value=${window.value}>${window.label}</option>`)}
      <//>
      <${Select} label="Rows" value=${limit} onChange=${change(setLimit)}>
        ${LOG_LIMITS.map((count) => html`<option value=${String(count)}>${count}</option>`)}
      <//>
      <${Select} label="Agent" value=${agent} onChange=${change(setAgent)}>
        <option value="">Every agent</option>
        ${agents.map((value) => html`<option value=${value}>${value}</option>`)}
      <//>
      <${Action} tone="ghost" small onClick=${reload} disabled=${log.loading ? "Reading the log." : false}>Refresh</${Action}>
    </div>
    ${log.loading && !log.data && !log.error ? html`<${Skeleton} />` : null}
    ${refusal && refusal.empty ? html`<p class="muted">No calls recorded yet: the agent has not finished a turn with this database, or its agent_events are off.</p>` : null}
    ${refusal && !refusal.empty
      ? html`<div class="notice error" role="status"><span><${Text} value=${`Unavailable: ${refusal.reason}`} />${refusal.next ? html`<br /><span class="small"><${Text} value=${`Next: ${refusal.next}.`} /></span>` : null}</span></div>`
      : null}
    ${data
      ? html`
        <p class="muted small"><${Text} value=${`Database ${data.database || "-"}, schema ${data.schema || "-"}, environment ${data.environment || "-"}, ${data.credential} credential. ${data.window.since ? `Since ${data.window.since}.` : ""}`} /></p>
        <div class="row">
          ${content
            ? html`<${Action} tone="ghost" small onClick=${() => setContent(false)}>Hide arguments</${Action}>`
            : html`<${Action} tone="secondary" small onClick=${() => setContent(true)}>Show arguments</${Action}>`}
          <span class="badge customer">Customer data</span>
          <span class="muted small">Arguments and error text are customer data, redacted of the stack's secrets. They are read for this view only and not kept.</span>
        </div>
        ${rows.length
          ? html`<${Table} label=${`Execution log of ${ref}`} columns=${["Time", "Agent", "Version", "Tool function", "Status", "Latency", "Turn", "Trace", "DB role"]}>
              ${rows.map((row) => html`<tr key=${row.key}>
                <td><${Text} value=${row.at} /></td>
                <td><${Text} value=${row.agent} /></td>
                <td><${Text} value=${row.version} /></td>
                <td><code><${Text} value=${row.function} /></code></td>
                <td><${Text} value=${row.status} /></td>
                <td><${Text} value=${row.latency} /></td>
                <td><code class="small"><${Text} value=${row.turn} /></code></td>
                <td><code class="small"><${Text} value=${row.trace} /></code></td>
                <td><${Text} value=${row.role} /></td>
              </tr>`)}
            <//>`
          : html`<p class="muted">No calls in this window.</p>`}
        ${content && data.content && rows.length
          ? html`<div class="args-list">${rows.map((row) => html`<details key=${`args-${row.key}`} class="args"><summary><${Text} value=${`${row.at} ${row.function}${row.error ? " (error)" : ""}`} /></summary>
              <${Text} block value=${argsText(row.args)} label=${`Arguments of ${row.function} at ${row.at}`} />
              ${row.error ? html`<${Text} block value=${row.error} label=${`Error of ${row.function} at ${row.at}`} />` : null}
            </details>`)}</div>`
          : null}
        ${data.truncated ? html`<p class="muted small">More calls match: choose a shorter window or more rows.</p>` : null}
        ${data.notes.map((note) => html`<p class="muted small">Note: <${Text} value=${note} /></p>`)}
        ${data.phoenix_url ? html`<p class="muted small">Look up a trace id in Phoenix: <a href=${data.phoenix_url} target="_blank" rel="noopener noreferrer"><${Text} value=${data.phoenix_url} /></a></p>` : null}`
      : null}
  </section>`;
}
