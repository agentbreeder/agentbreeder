// Services: the project's stack, the grant review that starts it, and its registries.

import { portsText, serviceState, serviceUp } from "../lib/format.js";
import { Action, Actions, Card, Notice, Table, Text, ViewState, useConfirm, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";

export function Services({ project }) {
  const { client, jobs, busyReason } = useConsole();
  const base = `/api/projects/${project.id}`;
  const [services] = useView(`${base}/services`, project.id);
  const [registry, reloadRegistry] = useView(`${base}/registry`, project.id);
  const [plan, setPlan] = useState(null);
  const [notice, setNotice] = useState(null);
  const [dialog, confirm] = useConfirm();
  const busy = busyReason(project.id);
  const rows = Object.entries((services.data && services.data.services) || {});
  // `services down` also removes exited containers, so Stop stays on until every service is absent.
  const stopped = rows.every(([, info]) => !info || info.state === "absent");

  useVerifyFollow(registry, reloadRegistry);

  const review = async () => {
    setNotice(null);
    try {
      const reply = await client.request(`${base}/plan`, { body: {} });
      // A new review is a new form: typed keys from an earlier review never carry over.
      setPlan(reply.data && { ...reply.data, review: Date.now() });
      if (reply.error) setNotice({ tone: "error", text: reply.error.message });
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  const start = async (kind, options, keys) => {
    setNotice(null);
    try {
      await jobs.start(project.id, kind, options, keys);
      setPlan(null);
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  const stop = async () => {
    if (await confirm({ title: "Stop the services?", text: "The containers stop. Their data and generated secrets are kept. Refused while an agent is deployed.", confirm: "Stop services" })) start("services-down");
  };

  return html`
    ${dialog}
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <${Card} title="Stack services" busy=${services.loading}>
      ${rows.length
        ? html`<${Table} label="Stack services" columns=${["Service", "State", "Host ports"]}>
            ${rows.map(([name, info]) => html`<tr key=${name}>
              <td><${Text} value=${name} /></td>
              <td><${Text} value=${serviceState(info)} /></td>
              <td><${Ports} info=${info} /></td>
            </tr>`)}
          <//>`
        : null}
      <${ViewState} view=${services} empty=${!rows.length && !services.loading && !services.error ? "Not started yet." : null} />
      <${Actions} disabled=${busy}>
        <${Action} onClick=${review}>${rows.length ? "Review and update" : "Review what will run"}<//>
        ${rows.length ? html`<${Action} tone="secondary" disabled=${!busy && stopped && "The services are stopped."} onClick=${stop}>Stop services<//>` : null}
      <//>
    <//>
    ${plan && plan.grant ? html`<${Grant} key=${plan.review} plan=${plan} busy=${busy} onStart=${start} onError=${setNotice} />` : null}
    <${Registries} view=${registry} />
  `;
}

function Ports({ info }) {
  const text = portsText(info);
  if (text === "internal only") return html`<span class="muted" title="Reached by agents on the stack network, not from this machine">internal only</span>`;
  return serviceUp(info) ? html`<${Text} value=${text} />` : html`<span class="muted"><${Text} value=${text} /></span>`;
}

function Grant({ plan, busy, onStart, onError }) {
  const grant = plan.grant;
  const needed = grant.keys.filter((key) => !key.stored);
  const stored = grant.keys.filter((key) => key.stored);
  const values = useRef(Object.create(null)); // key names come from the CLI: no prototype
  const mcp = grant.mcp.length > 0;
  const submit = (event) => {
    event.preventDefault();
    const keys = {};
    for (const key of needed) {
      const value = values.current[key.name] || "";
      if (!value && !key.optional) {
        onError({ tone: "error", text: `${key.name} is required.` }); // the notice renders through <Text>
        return; // typed values stay in their fields
      }
      if (value) keys[key.name] = value;
    }
    onStart("services-up", { digest: grant.digest }, Object.keys(keys).length ? keys : undefined);
  };
  return html`<${Card} title="What will run">
    ${mcp ? html`<p class="notice warn">This stack has MCP servers (<${Text} value=${grant.mcp.join(", ")} />). Review their tools with the CLI: <code>agentbreeder services up</code>.</p>` : null}
    <${Changes} before=${plan.accepted_text} after=${grant.text} />
    <${Text} block value=${grant.text} label="Grant" />
    <p class="muted small">Grant digest ${grant.digest.slice(0, 16)}…</p>
    <form class="stack" onSubmit=${submit}>
      ${needed.length ? html`<h3>Provider keys</h3>` : null}
      ${needed.map((key) => html`<label for=${`key-${key.name}`}><${Text} value=${key.name} />${key.optional ? " (optional)" : ""}</label>
        <input type="password" autocomplete="new-password" id=${`key-${key.name}`} onInput=${(event) => { values.current[key.name] = event.currentTarget.value; }} />`)}
      ${stored.length ? html`<p class="muted">Already stored: <${Text} value=${stored.map((key) => key.name).join(", ")} />. Enter a value through the CLI to replace one.</p>` : null}
      <p class="muted small">Keys go to the stack's private state and are never shown again.</p>
      <${Action} type="submit" disabled=${busy || (mcp && "Stacks with MCP servers start from the CLI, so their tools are reviewed there.")}>Confirm and start<//>
    </form>
  <//>`;
}

/** Lines added and removed since the grant was last accepted (set comparison, like a2). */
function Changes({ before, after }) {
  if (!before || before === after) return null;
  const old = new Set(before.split("\n"));
  const now = new Set(after.split("\n"));
  const removed = before.split("\n").filter((line) => !now.has(line));
  const added = after.split("\n").filter((line) => !old.has(line));
  if (!removed.length && !added.length) return null;
  return html`<div>
    <h3>Changed since you last accepted</h3>
    <pre class="text diff" tabindex="0" aria-label="Grant changes">${removed.map((line) => html`<div class="diff-del">- <${Text} value=${line} /></div>`)}${added.map((line) => html`<div class="diff-add">+ <${Text} value=${line} /></div>`)}</pre>
  </div>`;
}

function Registries({ view }) {
  const registry = view.data;
  if (!registry) return html`<${Card} title="Registries"><${ViewState} view=${view} /><//>`;
  const versions = (registry.versions && registry.versions.data) || [];
  const problems = registry.problems && registry.problems.data;
  const problemError = registry.problems && registry.problems.error;
  const verifying = problemError && problemError.code === "VERIFYING";
  let status;
  if (verifying) status = "Verifying…";
  else if (Array.isArray(problems)) status = problems.length ? `${problems.length} failed verification.` : "All passed verification (a corruption check).";
  else status = (problemError && problemError.message) || "";
  return html`<${Card} title="Registries">
    <p>${versions.length} ${versions.length === 1 ? "version" : "versions"} available. <span role="status"><${Text} value=${status} /></span></p>
    ${Array.isArray(problems) && problems.length
      ? html`<ul class="plain">${problems.map((problem) => html`<li><${Text} value=${typeof problem === "string" ? problem : JSON.stringify(problem)} /></li>`)}</ul>`
      : null}
    <details><summary>Show versions</summary><${Text} block value=${versions.map((row) => row.ref).join("\n")} label="Registry versions" /></details>
  <//>`;
}

/**
 * Registry verification runs in the background (a CLI child, up to 5 minutes): ask again while it
 * says VERIFYING or the answer is stale, backing off from 2 to 10 seconds, for at most 330 s.
 */
function useVerifyFollow(view, reload) {
  const started = useRef(Date.now());
  const delay = useRef(2000);
  useEffect(() => {
    const problems = view.data && view.data.problems;
    const pending = problems && (problems.stale || (problems.error && problems.error.code === "VERIFYING"));
    if (!pending || Date.now() - started.current > 330000) return undefined;
    const timer = setTimeout(reload, delay.current);
    delay.current = Math.min(delay.current * 1.5, 10000);
    return () => clearTimeout(timer);
  }, [view]);
}
