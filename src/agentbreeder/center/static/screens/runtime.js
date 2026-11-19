// Registry ▸ Runtimes and the runtime detail (#1085 S3, console spec §6.3): every `runtime/`
// version with its framework, protocol, kit and status (`registry runtimes --project DIR --json`);
// and, in a runtime version's card, its manifest facts, the packages `requirements.in` pins, its
// newest conformance report (`registry conformance REF --json`) and Run conformance, a job on this
// project's built image of that version (`agentbreeder conformance IMAGE --project DIR`).
//
// Code mode (F8) is not on `main`: there are no code-mode images, so every report shown is a
// declared-mode one, and the guided path has no Conformance step yet.

import { runtimeStatus } from "../lib/builder.js";
import {
  CHECK_WORDS, RESULT_WORDS, checkGroups, countsText, groupText, imageLabel, imagesFor, notCurrent, runProblem, shortDigest,
} from "../lib/conformance.js";
import { capabilitiesText, protocolText } from "../lib/framework.js";
import { ROOT_LABELS } from "../lib/registry.js";
import { href, withQuery } from "../lib/router.js";
import { visible } from "../lib/text.js";
import { Action, Card, Notice, Table, Text, ViewState, nextId, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useState } from "../ui/preact.js";

/** A runtime version's detail address: its name's page, opened on that version. */
export const versionHref = (entry) => withQuery(href("/registry/runtimes/:name", { name: entry.name }), { version: entry.version });

/** Registry ▸ Runtimes: one row per version, run end to end first (the CLI's order). */
export function RuntimeList({ project }) {
  const [view, reload] = useView(`/api/projects/${project.id}/runtimes`, project.id);
  const rows = Array.isArray(view.data) ? view.data : [];
  const usable = rows.filter((entry) => entry.usable);
  const unusable = rows.filter((entry) => !entry.usable);
  return html`<${Card} title="Runtimes" busy=${view.loading}>
    <p class="muted small">Every <code>runtime/</code> version in this project's search roots, as the engine reads it (<code>agentbreeder registry runtimes</code>). Status is whether the version has been run end to end, else what its newest conformance report says.</p>
    ${view.loading && !view.data ? html`<p class="muted" role="status">Reading the registries…</p>` : null}
    ${view.error && !view.stale
      ? html`<div class="notice error" role="alert"><span><${Text} value=${`${view.error.message} [${view.error.code}]`} /></span> <${Action} small tone="secondary" onClick=${reload}>Try again<//></div>`
      : null}
    ${view.stale ? html`<p class="muted">Showing the last answer: the newest read failed.</p>` : null}
    ${usable.length
      ? html`<${Table} label="Runtime versions" columns=${["Framework", "Version", "Protocol", "Kit", "Status", "Root"]}>
          ${usable.map((entry) => html`<tr key=${entry.ref}>
            <td><span class="badge framework"><${Text} value=${entry.display_name} /></span></td>
            <td><a href=${versionHref(entry)}><code><${Text} value=${entry.ref} /></code></a></td>
            <td><${Text} value=${protocolText(entry)} /></td>
            <td>${entry.kit ? html`<${Text} value=${entry.kit} />` : html`<span class="muted">none</span>`}</td>
            <td><${Text} value=${runtimeStatus(entry)} /></td>
            <td><span class="badge">${ROOT_LABELS[entry.root_kind] || entry.root_kind}</span></td>
          </tr>`)}
        <//>`
      : null}
    ${unusable.length
      ? html`<ul class="plain">${unusable.map((entry) => html`<li key=${entry.ref}><${Text} value=${`Cannot be used: ${entry.ref}: ${entry.reason} (${entry.code})`} /></li>`)}</ul>`
      : null}
    ${view.data && !rows.length ? html`<p class="notice error" role="alert">The registries list no runtime version, so nothing can be built. Run <code>agentbreeder registry verify</code>.</p>` : null}
  <//>`;
}

/**
 * The packages the version's `requirements.in` names, with their version specifiers
 * (`[{name, spec}]`, comments dropped), from the version's files; null when it has none shown.
 */
export function pinnedPackages(data) {
  const files = ((data && data.files) || []).filter((item) => typeof item.text === "string" && /(^|\/)requirements\.in$/.test(item.path));
  if (!files.length) return null;
  const rows = [];
  for (const line of files[0].text.split("\n")) {
    const match = /^\s*([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?)\s*((?:==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?\s*(?:#.*)?$/.exec(line);
    if (match) rows.push({ name: match[1], spec: (match[2] || "").replace(/\s+/g, "") });
  }
  return rows;
}

/** The runtime parts of one version's card (manifest facts, packages, conformance); null for any
 * other kind. */
export function RuntimeFacts({ data, project }) {
  if (!data || data.kind !== "runtime" || !project) return null;
  return html`<${RuntimeCard} key=${data.ref} data=${data} project=${project} />`;
}

function RuntimeCard({ data, project }) {
  const [view] = useView(`/api/projects/${project.id}/runtimes`, project.id);
  const entry = Array.isArray(view.data) ? view.data.find((item) => item.ref === data.ref) : null;
  const packages = pinnedPackages(data);
  return html`
    <h3>Runtime</h3>
    ${entry && entry.usable
      ? html`<dl class="facts">
          <dt>Framework</dt><dd><span class="badge framework"><${Text} value=${entry.display_name} /></span> <span class="muted small"><${Text} value=${entry.framework} /></span></dd>
          <dt>Status</dt><dd><${Text} value=${runtimeStatus(entry)} /></dd>
          <dt>Protocol</dt><dd><${Text} value=${protocolText(entry)} /></dd>
          <dt>Capabilities</dt><dd><${Text} value=${capabilitiesText(entry)} /></dd>
          <dt>Limits</dt><dd><${Text} value=${Object.entries(entry.limits || {}).map(([key, value]) => `${key} ${value}`).join(", ") || "none"} /></dd>
          <dt>Reserved tool names</dt><dd>${(entry.reserved_tool_names || []).length ? html`<${Text} value=${entry.reserved_tool_names.join(", ")} />` : html`<span class="muted">none</span>`}</dd>
          ${entry.notes ? html`<dt>Notes</dt><dd><${Text} value=${entry.notes} /></dd>` : null}
          ${entry.docs ? html`<dt>Docs</dt><dd><code><${Text} value=${entry.docs} /></code></dd>` : null}
        </dl>`
      : entry
        ? html`<p class="notice error" role="status"><${Text} value=${`Cannot be used: ${entry.reason} (${entry.code})`} /></p>`
        : html`<${ViewState} view=${view} empty=${view.data ? "The engine does not list this version." : null} />`}
    <h3>Packages</h3>
    <p class="muted small">What the version's <code>requirements.in</code> names; the build installs the hash-locked <code>requirements.lock</code>.</p>
    ${packages === null
      ? html`<p class="muted">This version has no <code>requirements.in</code> shown here.</p>`
      : packages.length
        ? html`<ul class="plain packages">${packages.map((item) => html`<li key=${item.name}><code><${Text} value=${`${item.name}${item.spec}`} /></code></li>`)}</ul>`
        : html`<p class="muted">requirements.in names no package.</p>`}
    <${Conformance} project=${project} version=${data.ref} />`;
}

// `version` is the runtime ref: `ref` is reserved by Preact and never reaches a component.
function Conformance({ project, version: ref }) {
  const { jobs, busyReason, docker } = useConsole();
  const [view] = useView(`/api/projects/${project.id}/conformance?ref=${encodeURIComponent(ref)}`, project.id);
  const heading = useState(() => nextId("conformance"))[0];
  const selectId = useState(() => nextId("conformance-image"))[0];
  const answer = view.data && !view.error ? view.data : view.stale ? view.data : null;
  const report = answer ? answer.report : null;
  const images = answer ? imagesFor(answer.images, ref) : [];
  const [picked, setPicked] = useState(null);
  const chosen = picked && images.some((image) => image.image === picked) ? picked : images[0] ? images[0].image : null;
  const [notice, setNotice] = useState(null);
  // Without an answer there is no image to offer: say why, rather than hiding the button.
  const unread = !answer && view.error ? `The project's build could not be read: ${view.error.message || view.error.code}` : !answer ? "Reading the project's build…" : false;
  const off = unread || (docker && docker.reachable === false ? "Docker is not reachable. Start Docker, then try again." : false) || busyReason(project.id) || runProblem(images, chosen);
  const run = async () => {
    setNotice(null);
    try {
      await jobs.start(project.id, "conformance", { image: chosen, runtime: ref });
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  return html`<section class="conformance" aria-labelledby=${heading} aria-busy=${view.loading ? "true" : "false"}>
    <h3 id=${heading}>Conformance</h3>
    <p class="muted small">The newest report for an image built on this version, from <code>agentbreeder conformance</code> (agent protocol v1, local Docker). A report is evidence about the runtime, not a security review. While any check is not built yet, the best result is "incomplete", which is not a pass.</p>
    ${!answer ? html`<${ViewState} view=${view} />` : null}
    ${answer && !report ? html`<p>Not run for this runtime version.</p>` : null}
    ${report ? html`<${Report} report=${report} stale=${notCurrent(report, images)} />` : null}
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <div class="row conformance-run">
      ${images.length > 1
        ? html`<label for=${selectId}>Image</label>
          <select id=${selectId} value=${chosen} onChange=${(event) => setPicked(event.currentTarget.value)}>
            ${images.map((image) => html`<option key=${image.image} value=${image.image}>${visible(imageLabel(image))}</option>`)}
          </select>`
        : images.length
          ? html`<span class="small">Image: <${Text} value=${imageLabel(images[0])} /></span>`
          : null}
      <${Action} tone="secondary" onClick=${run} disabled=${off}>Run conformance<//>
    </div>
    <p class="muted small">Starts the agent, a throwaway database and scripted fakes on an internal Docker network, and removes them afterwards. No model is called.</p>
  </section>`;
}

function Report({ report, stale }) {
  const groups = checkGroups(report);
  return html`
    <dl class="facts">
      <dt>Result</dt><dd><span class=${`result ${report.result}`}><${Text} value=${RESULT_WORDS[report.result]} /></span> <span class="muted small"><${Text} value=${countsText(report.counts)} /></span></dd>
      <dt>Image</dt><dd><code title=${report.image}><${Text} value=${shortDigest(report.image)} /></code> <span class="muted small"><${Text} value=${`agent ${report.agent}`} /></span>${stale ? html` <span class="badge">not the current build</span>` : null}</dd>
      <dt>Finished</dt><dd><${Text} value=${report.finished} /></dd>
      <dt>Profile</dt><dd><${Text} value=${`protocol ${report.protocol}, ${report.profile}`} /></dd>
    </dl>
    ${groups.map((group) => html`<details class="check-group" key=${`${report.image} ${report.finished} ${group.group}`} open=${group.open}>
      <summary><${Text} value=${groupText(group)} /></summary>
      <ul class="plain checks">${group.checks.map((check) => html`<li key=${check.id}>
        <span class=${`check ${check.status}`}><${Text} value=${CHECK_WORDS[check.status]} /></span> <code><${Text} value=${check.id} /></code> <${Text} value=${check.title} />
        ${check.detail ? html`<div class="small muted"><${Text} value=${check.detail} /></div>` : null}
      </li>`)}</ul>
    </details>`)}`;
}
