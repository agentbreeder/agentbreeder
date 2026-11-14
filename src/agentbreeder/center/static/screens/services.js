// Services: the project's stack, the grant review that starts it, and its registries.

import { consentMissing, consentNames, driftedServers, planServers, repinCommand } from "../lib/mcp.js";
import { href } from "../lib/router.js";
import { visible } from "../lib/text.js";
import { portsText, serviceState, serviceUp } from "../lib/format.js";
import { Action, Actions, Card, Notice, Table, Text, ViewState, useConfirm, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { LogsLink } from "./logs.js";
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
        ? html`<${Table} label="Stack services" columns=${["Service", "State", "Host ports", ""]}>
            ${rows.map(([name, info]) => html`<tr key=${name}>
              <td><${Text} value=${name} /></td>
              <td><${Text} value=${serviceState(info)} /></td>
              <td><${Ports} info=${info} /></td>
              <td><${LogsLink} kind="service" name=${name} label=${`Logs of ${visible(name)}`} /></td>
            </tr>`)}
          <//>`
        : null}
      <${ViewState} view=${services} empty=${!rows.length && !services.loading && !services.error ? "Not started yet." : null} />
      <${Actions} disabled=${busy}>
        <${Action} onClick=${review}>${rows.length ? "Review and update" : "Review what will run"}<//>
        ${rows.length ? html`<${Action} tone="secondary" disabled=${!busy && stopped && "The services are stopped."} onClick=${stop}>Stop services<//>` : null}
      <//>
    <//>
    ${plan && plan.grant ? html`<${Grant} key=${plan.review} plan=${plan} project=${project} status=${services.data} busy=${busy} onStart=${start} onError=${setNotice} />` : null}
    <${Registries} view=${registry} />
  `;
}

function Ports({ info }) {
  const text = portsText(info);
  if (text === "internal only") return html`<span class="muted" title="Reached by agents on the stack network, not from this machine">internal only</span>`;
  return serviceUp(info) ? html`<${Text} value=${text} />` : html`<span class="muted"><${Text} value=${text} /></span>`;
}

function Grant({ plan, project, status, busy, onStart, onError }) {
  const grant = plan.grant;
  const needed = grant.keys.filter((key) => !key.stored);
  const stored = grant.keys.filter((key) => key.stored);
  const values = useRef(Object.create(null)); // key names come from the CLI: no prototype
  const servers = planServers(grant);
  // MCP consent (#831): a new review is a new form (the key), so nothing ticked carries over.
  const [ticked, setTicked] = useState(() => new Set());
  const missing = consentMissing(servers, ticked);
  const tick = (name, on) => {
    const next = new Set(ticked);
    if (on) next.add(name);
    else next.delete(name);
    setTicked(next);
  };
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
    if (missing) {
      onError({ tone: "error", text: missing });
      return;
    }
    const allow = consentNames(servers, ticked);
    onStart("services-up", { digest: grant.digest, ...(allow.length ? { allow_project_mcp: allow } : {}) }, Object.keys(keys).length ? keys : undefined);
  };
  return html`<${Card} title="What will run">
    <${Changes} before=${plan.accepted_text} after=${grant.text} />
    <${Text} block value=${grant.text} label="Grant" />
    <p class="muted small">Grant digest ${grant.digest.slice(0, 16)}…</p>
    <form class="stack" onSubmit=${submit}>
      ${needed.length ? html`<h3>Provider keys</h3>` : null}
      ${needed.map((key) => html`<label for=${`key-${key.name}`}><${Text} value=${key.name} />${key.optional ? " (optional)" : ""}</label>
        <input type="password" autocomplete="new-password" id=${`key-${key.name}`} onInput=${(event) => { values.current[key.name] = event.currentTarget.value; }} />`)}
      ${stored.length ? html`<p class="muted">Already stored: <${Text} value=${stored.map((key) => key.name).join(", ")} />. Enter a value through the CLI to replace one.</p>` : null}
      <p class="muted small">Keys go to the stack's private state and are never shown again.</p>
      ${servers.length ? html`<${McpReview} servers=${servers} ticked=${ticked} onTick=${tick} drifted=${driftedServers(status)} project=${project} />` : null}
      <${Action} type="submit" disabled=${busy || missing}>Confirm and start<//>
    </form>
  <//>`;
}

/**
 * MCP servers in the services review (#831): each server, its origin and upstream, and which
 * credential it sends where. One that is not built in (project or registry-path code) needs a
 * tick: `--allow-project-mcp NAME`. After the relays start, first pins and new tools are
 * accepted for built-in servers and the allowed ones (trust on first use); a changed definition
 * is never accepted here (#964).
 */
function McpReview({ servers, ticked, onTick, drifted, project }) {
  return html`<fieldset class="mcp-review">
    <legend>MCP servers</legend>
    <p class="muted small">Each runs behind its own relay, which holds the credential; agents never do. After the relays start, the tools each server serves for the first time are pinned as they are now (trust on first use) for built-in servers and the ones allowed below; the job log shows every definition pinned. See <a href=${href("/mcp")}>MCP grants</a> for the pins and grants.</p>
    <ul class="plain">
      ${servers.map((row) => html`<li key=${row.server}>
        <strong><code><${Text} value=${`mcp.${row.server}`} /></code></strong> <code><${Text} value=${row.ref} /></code> <span class="muted small">(<${Text} value=${row.origin} />)</span>
        <div class="small">Upstream <code><${Text} value=${row.upstream || "none"} /></code></div>
        ${row.credentials.length
          ? row.credentials.map((c) => html`<div class="small" key=${c.variable}>Credential <code><${Text} value=${c.variable} /></code> will be sent to <code><${Text} value=${c.destination || "its upstream"} /></code></div>`)
          : html`<div class="small muted">No credential.</div>`}
        ${row.accessTokenTo ? html`<div class="small">The access token it mints will be sent to <code><${Text} value=${row.accessTokenTo} /></code></div>` : null}
        ${row.builtIn
          ? null
          : html`<label class="consent"><input type="checkbox" checked=${ticked.has(row.server)} onChange=${(event) => onTick(row.server, event.currentTarget.checked)} /> <${Text} value=${row.sendsCredential
              ? `Allow mcp.${row.server}, a ${row.origin} version: send ${row.credentials.map((c) => `${c.variable} to ${c.destination || "its upstream"}`).join(", ") || "its credential to its upstream"}${row.accessTokenTo ? ` (and the access token it mints to ${row.accessTokenTo})` : ""}, and pin its tools on first use. Its effect labels are untrusted.`
              : `Allow mcp.${row.server}, a ${row.origin} version: pin its tools on first use. Its effect labels are untrusted.`} /></label>`}
      </li>`)}
    </ul>
    ${drifted.length ? html`<p class="notice warn" role="status">Pinned tools changed upstream on <${Text} value=${drifted.map((name) => `mcp.${name}`).join(", ")} />. Starting keeps them hidden; review the change in a terminal: <code><${Text} value=${repinCommand(project.path, project.environment)} /></code></p>` : null}
  </fieldset>`;
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
