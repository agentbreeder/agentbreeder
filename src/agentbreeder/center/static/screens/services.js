// Services: the project's stack, the grant review that starts it, and its registries.

import { consentMissing, consentNames, driftedServers, pinsCommand, pinsProblem, planServers, repinCommand, repinServers } from "../lib/mcp.js";
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
              <td><a href=${href("/services/:name", { name })}><${Text} value=${name} /></a></td>
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
    ${plan && plan.grant ? html`<${Grant} key=${plan.review} plan=${plan} project=${project} status=${services.data} busy=${busy} client=${client} onStart=${start} onError=${setNotice} />` : null}
    <${Registries} view=${registry} />
  `;
}

function Ports({ info }) {
  const text = portsText(info);
  if (text === "internal only") return html`<span class="muted" title="Reached by agents on the stack network, not from this machine">internal only</span>`;
  return serviceUp(info) ? html`<${Text} value=${text} />` : html`<span class="muted"><${Text} value=${text} /></span>`;
}

function Grant({ plan, project, status, busy, client, onStart, onError }) {
  const grant = plan.grant;
  const needed = grant.keys.filter((key) => !key.stored);
  const stored = grant.keys.filter((key) => key.stored);
  const values = useRef(Object.create(null)); // key names come from the CLI: no prototype
  const servers = planServers(grant);
  // MCP consent (#831): a new review is a new form (the key), so nothing ticked carries over.
  const [ticked, setTicked] = useState(() => new Set());
  const missing = consentMissing(servers, ticked);
  // The repin review (#964): read on request, never carried over (a new review is a new form).
  const [pins, setPins] = useState({ summary: null, reading: false, problem: "", reviewed: false });
  const drifted = driftedServers(status);
  const repinReady = Boolean(drifted.length && pins.reviewed && pins.summary && pins.summary.changes);
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
    // Only while the review is on screen: drift that cleared since unmounts it (#964).
    const repin = repinReady ? { pins_digest: pins.summary.digest } : {};
    onStart("services-up", { digest: grant.digest, ...(allow.length ? { allow_project_mcp: allow } : {}), ...repin }, Object.keys(keys).length ? keys : undefined);
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
      ${servers.length ? html`<${McpReview} servers=${servers} ticked=${ticked} onTick=${tick} drifted=${drifted} project=${project} pins=${pins} setPins=${setPins} client=${client} />` : null}
      <${Action} type="submit" disabled=${busy || missing || (pins.reading && "Reading the repin review…")}>${repinReady ? "Confirm, repin and start" : "Confirm and start"}<//>
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
function McpReview({ servers, ticked, onTick, drifted, project, pins, setPins, client }) {
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
    ${drifted.length ? html`<${Repin} drifted=${drifted} project=${project} pins=${pins} setPins=${setPins} client=${client} />` : null}
  </fieldset>`;
}

/**
 * Repin changed MCP tool definitions (#964): `services pins --json`, run, checked and rendered by
 * the server (each relay probes its upstream; up to 3 minutes per relay). The text shown is the
 * server's; the tick binds this start to that review's digest, and `services up` pins only if
 * the live definitions still match it (PINS_CHANGED otherwise, pins unchanged).
 */
function Repin({ drifted, project, pins, setPins, client }) {
  const read = async () => {
    setPins({ summary: null, reading: true, problem: "", reviewed: false });
    try {
      const reply = await client.request(`/api/projects/${project.id}/mcp/pins`, { body: {} });
      setPins({ summary: reply.error ? null : reply.data, reading: false, problem: reply.error ? pinsProblem(reply.error) : "", reviewed: false });
    } catch (error) {
      if (error.kind === "session") throw error;
      setPins({ summary: null, reading: false, problem: pinsProblem(error), reviewed: false });
    }
  };
  const summary = pins.summary;
  const rows = repinServers(summary);
  return html`<div class="repin">
    <p class="notice warn" role="status">Pinned tools changed upstream on <${Text} value=${drifted.map((name) => `mcp.${name}`).join(", ")} />. The relay hides them until you review and repin them. Starting without a repin keeps them hidden.</p>
    <p><button type="button" class="action secondary small" disabled=${pins.reading} onClick=${read}>${pins.reading ? "Reading the repin review…" : summary ? "Review the repin again" : "Review repin"}</button></p>
    <p class="muted small">Or in a terminal: <code><${Text} value=${pinsCommand(project.path)} /></code>, then <code><${Text} value=${`${repinCommand(project.path, project.environment)} --expect-pins DIGEST`} /></code></p>
    ${pins.problem ? html`<p class="notice error" role="alert"><${Text} value=${pins.problem} /></p>` : null}
    ${summary ? html`<div class="repin-review">
      <h3>Repin review</h3>
      <p class="muted small">A repin pins exactly the definitions below and nothing else. Pins bind the advertised definition, not what a tool does, and a description is upstream text: read it as data.</p>
      ${rows.map((row) => html`<div key=${row.name}>
        <h4><code><${Text} value=${`mcp.${row.name}`} /></code> <span class="muted small">(<${Text} value=${row.origin} />)</span></h4>
        ${row.builtIn ? null : html`<p class="notice warn small"><${Text} value=${`mcp.${row.name} is ${row.origin} code: its effect labels are untrusted.`} /></p>`}
        ${row.tools.map((tool) => html`<div key=${tool.name}>
          <p><code><${Text} value=${tool.heading} /></code></p>
          <${Text} block value=${tool.text} label=${`Definition of ${tool.name}`} />
        </div>`)}
        ${row.drop.map((name) => html`<p key=${`drop ${name}`}>Missing upstream: <code><${Text} value=${name} /></code> (this pin is removed)</p>`)}
        ${row.missing.map((name) => html`<p class="muted" key=${`missing ${name}`}>Missing upstream: <code><${Text} value=${name} /></code> (not pinned)</p>`)}
        ${!row.tools.length && !row.drop.length ? html`<p class="muted">Nothing to repin.</p>` : null}
      </div>`)}
      <p class="muted small">Review digest <code><${Text} value=${summary.digest} /></code></p>
      ${summary.changes
        ? html`<label class="consent"><input type="checkbox" checked=${pins.reviewed} onChange=${(event) => setPins({ ...pins, reviewed: event.currentTarget.checked })} /> I reviewed these tool definitions: pin them at this start.</label>`
        : html`<p class="muted">Nothing to repin: the upstreams serve what stack.lock pinned.</p>`}
    </div>` : null}
  </div>`;
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
