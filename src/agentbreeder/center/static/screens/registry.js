// Registry ▸ one page per kind (#824): every name with its latest version, version count, root,
// verify status and where this project uses it; a name's detail has the version timeline, one
// version's manifest and files (`registry show`), the diff between any two versions (`registry
// diff`) and used-by across the registered projects (`registry used-by`). Prompts are edited
// into a new version, and the agent moved to it, from `edit.js` (#826, M10); forking is M9a.

import { href } from "../lib/router.js";
import { ToolFacts } from "./tool.js";
import {
  KINDS, ROOT_LABELS, deployedLines, diffStats, groupByName, hunkHeader, noHunkReason, usedByText, useText, verifyLabel, verifyState,
} from "../lib/registry.js";
import { Action, Card, Notice, Table, Text, ViewState, useConfirm } from "../ui/components.js";
import { Editor, MoveAgent, editing } from "./edit.js";
import { useView } from "../ui/data.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";

/** `kind/name` of a name's row. */
const baseRef = (entry) => entry.latest.ref.split("@")[0];

const query = (params) => Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

/** The `registry` view, re-read while verification is still running (it is a background child). */
function useRegistry(project) {
  const started = useRef(Date.now());
  const delay = useRef(2000);
  const [view, read] = useView(`/api/projects/${project.id}/registry`, project.id);
  const reload = () => {
    started.current = Date.now(); // a save starts a fresh verify run: follow it again
    delay.current = 2000;
    read();
  };
  useEffect(() => {
    const problems = view.data && view.data.problems;
    const pending = problems && (problems.stale || (problems.error && problems.error.code === "VERIFYING"));
    if (!pending || Date.now() - started.current > 330000) return undefined;
    const timer = setTimeout(read, delay.current);
    delay.current = Math.min(delay.current * 1.5, 10000);
    return () => clearTimeout(timer);
  }, [view]);
  return { ...view, reload };
}

function VerifyNote({ view }) {
  const verify = verifyState(view.data && view.data.problems);
  let text;
  if (verify.state === "verifying") text = "Verifying every version (a corruption check that hashes each file)…";
  else if (verify.state === "unknown") text = `Not verified: ${verify.message}`;
  else if (verify.stale) text = "Verify status is from the last run; a new run is going.";
  else text = "Verify re-hashes every version and checks its manifest (a corruption check, not a signature).";
  return html`<p class="muted small" role="status"><${Text} value=${text} /></p>`;
}

function Roots({ roots }) {
  if (!roots || !roots.length) return null;
  return html`<details class="roots">
    <summary>Search roots, in resolve order</summary>
    <ol>${roots.map((root) => html`<li key=${root.path}>
      <span class="badge">${ROOT_LABELS[root.kind] || root.kind}</span> <code><${Text} value=${root.path} /></code>${root.present ? null : html` <span class="muted small">(not created yet)</span>`}
    </li>`)}</ol>
    <p class="muted small">A ref resolves from the first root that holds it.</p>
  </details>`;
}

function RootBadge({ version }) {
  return html`<span class="badge" title=${version.root}>${ROOT_LABELS[version.rootKind] || version.rootKind}</span>`;
}

function VerifyBadge({ entry }) {
  const label = verifyLabel(entry);
  return html`<span class=${`verify ${entry.verify}`}>${entry.verify === "failed" ? "✕ " : entry.verify === "passed" ? "✓ " : ""}${label}</span>`;
}

/** The list for one kind. */
export function registryList(meta) {
  return function RegistryList({ project }) {
    const view = useRegistry(project);
    if (!view.data) return html`<${Card} title=${meta.nav} busy=${view.loading}><${ViewState} view=${view} /><//>`;
    const rows = groupByName(view.data, meta.kind);
    const versions = view.data.versions || {};
    const inUse = view.data.in_use || {};
    const useErrors = (inUse.data && inUse.data.errors) || [];
    return html`<${Card} title=${meta.nav} busy=${view.loading}>
      <${VerifyNote} view=${view} />
      ${versions.error ? html`<p class="notice error" role="alert"><${Text} value=${versions.error.message} /></p>` : null}
      ${inUse.error ? html`<p class="muted">Used by is unavailable: <${Text} value=${inUse.error.message} /></p>` : null}
      ${useErrors.length ? html`<p class="muted">Used by may be incomplete: <${Text} value=${`${useErrors[0].file || "project"}: ${useErrors[0].error}`} /></p>` : null}
      ${rows.length
        ? html`<${Table} label=${meta.nav} columns=${["Name", "Latest", "Versions", "Root", "Verify", "Used by this project"]}>
            ${rows.map((entry) => html`<tr key=${entry.name}>
              <td><a href=${href(`/registry/${meta.path}/:name`, { name: entry.name })}><${Text} value=${entry.name} /></a></td>
              <td class="nowrap"><code>${entry.latest.version}</code></td>
              <td class="num">${entry.versions.length}</td>
              <td><${RootBadge} version=${entry.latest} /></td>
              <td><${VerifyBadge} entry=${entry} /></td>
              <td>${usedByText(entry) ? html`<${Text} value=${usedByText(entry)} />` : html`<span class="muted">not used</span>`}</td>
            </tr>`)}
          <//>`
        : html`<p class="muted">No ${meta.nav.toLowerCase()} in this project's search roots. <code>agentbreeder registry new ${meta.kind} NAME</code> scaffolds one to publish.</p>`}
      <${Roots} roots=${view.data.roots} />
    <//>`;
  };
}

/** One name's detail: timeline, the selected version, the diff and used-by. */
export function registryDetail(meta) {
  // Keyed by name: another name (back, forward, a pasted address) starts with its own latest
  // version and a fresh diff picker.
  const Detail = detailBody(meta);
  return function RegistryDetail({ project, params }) {
    return html`<${Detail} key=${params.name} project=${project} name=${params.name} />`;
  };
}

function detailBody(meta) {
  return function DetailBody({ project, name }) {
    const view = useRegistry(project);
    const entry = view.data ? groupByName(view.data, meta.kind).find((row) => row.name === name) : null;
    const [selected, setSelected] = useState(null);
    const [saved, setSaved] = useState(null);
    const [dialog, confirm] = useConfirm();
    const choose = async (next) => {
      if (editing.dirty && !(await confirm({ title: "Discard your changes?", text: "The prompt you are editing has unsaved changes.", confirm: "Discard" }))) return;
      setSelected(next);
    };
    // Pin the version shown first: a re-read after a version appears (a save here, another tab,
    // a terminal) must not swap it, and an open editor with it (#826).
    const latest = entry ? entry.latest.version : null;
    useEffect(() => {
      if (selected === null && latest) setSelected(latest);
    }, [latest]);
    const back = html`<p><a href=${href(`/registry/${meta.path}`)}>← All ${meta.nav.toLowerCase()}</a></p>`;
    if (!view.data) return html`${back}<${Card} title=${name} busy=${view.loading}><${ViewState} view=${view} /><//>`;
    if (!entry) return html`${back}<${Card} title=${name}><p class="muted">No ${meta.one.toLowerCase()} named <code><${Text} value=${name} /></code> in this project's search roots.</p><//>`;
    const version = entry.versions.find((v) => v.version === selected) || entry.latest;
    return html`${back}
      <${Card} title=${`${meta.kind}/${name}`}>
        <${VerifyNote} view=${view} />
        <${Timeline} entry=${entry} current=${version.version} onSelect=${choose} />
        ${dialog}
      <//>
      <${Notice} notice=${saved} onDismiss=${() => setSaved(null)} />
      <${Version} key=${version.ref} project=${project} version=${version} entry=${entry} kind=${meta.kind}
        onSaved=${(next, text) => { setSaved({ tone: "ok", text }); setSelected(next); view.reload(); }} onMoved=${view.reload} />
      ${entry.versions.length > 1 ? html`<${Diff} key=${entry.name} project=${project} entry=${entry} />` : null}
      <${UsedBy} key=${`used-${version.ref}`} project=${project} version=${version} />`;
  };
}

function Timeline({ entry, current, onSelect }) {
  return html`<${Table} label="Versions, newest first" columns=${["Version", "Root", "Verify", "Used by this project", ""]}>
    ${entry.versions.map((v, index) => html`<tr key=${v.version} aria-current=${v.version === current ? "true" : undefined} class=${v.version === current ? "current" : ""}>
      <td class="nowrap"><code>${v.version}</code>${index === 0 ? html` <span class="muted small">latest</span>` : null}${v.shadowed ? html` <span class="muted small" title="The same version in a later root is never resolved">(+${v.shadowed} shadowed)</span>` : null}</td>
      <td><${RootBadge} version=${v} /></td>
      <td><${VerifyBadge} entry=${v} />${v.problem ? html`<div class="small"><${Text} value=${v.problem} /></div>` : null}</td>
      <td>${useText(v) ? html`<${Text} value=${useText(v)} />` : html`<span class="muted">not used</span>`}</td>
      <td>${v.version === current
        ? html`<span class="muted small">shown below</span>`
        : html`<${Action} tone="secondary" small onClick=${() => onSelect(v.version)}>Show ${v.version}<//>`}</td>
    </tr>`)}
  <//>`;
}

function Version({ project, version, entry, kind, onSaved, onMoved }) {
  const [view] = useView(`/api/projects/${project.id}/registry/show?${query({ ref: version.ref })}`, project.id);
  const data = view.data;
  return html`<${Card} title=${`Version ${version.version}`} busy=${view.loading}>
    ${!data || view.error ? html`<${ViewState} view=${view} />` : null}
    ${data
      ? html`
        ${data.description ? html`<p><${Text} value=${data.description} /></p>` : null}
        <dl class="facts">
          <dt>Ref</dt><dd><code><${Text} value=${data.ref} /></code></dd>
          <dt>Version hash</dt><dd><code><${Text} value=${data.sha256} /></code></dd>
          <dt>Root</dt><dd><span class="badge">${ROOT_LABELS[data.root_kind] || data.root_kind}</span> <code><${Text} value=${data.path} /></code></dd>
        </dl>
        <${ToolFacts} data=${data} project=${project} />
        ${data.omitted ? html`<p class="muted small">Some files are not shown: the version's text budget ran out. <code>agentbreeder registry show</code> prints the same view.</p>` : null}
        <h3>Files</h3>
        <ul class="files">${(data.files || []).map((file) => html`<li key=${file.path}><${FileView} file=${file} /></li>`)}</ul>`
      : null}
  <//>
  <${MoveAgent} key=${`move-${version.ref}`} project=${project} kind=${kind} entry=${entry} version=${version} onMoved=${onMoved} />
  ${data && !view.error ? html`<${Editor} project=${project} kind=${kind} entry=${entry} version=${version} show=${data} FileDiff=${FileDiff} onSaved=${onSaved} onRefresh=${onMoved} />` : null}`;
}

function FileView({ file }) {
  if (file.type === "dir") return html`<span class="muted"><${Text} value=${`${file.path}/`} /></span>`;
  const notes = [`${file.size} bytes`];
  if (file.executable) notes.push("executable");
  if (file.truncated) notes.push("truncated to the size cap");
  if (file.stripped) notes.push("control or invisible characters removed");
  const head = html`<span><code><${Text} value=${file.path} /></code> <span class="muted small">${notes.join(", ")}</span></span>`;
  if (file.binary) return html`${head}<p class="muted small">Binary file: not shown.</p>`;
  if (file.omitted || file.text == null) return html`${head}<p class="muted small">Not shown (over the version's text budget).</p>`;
  return html`<details open=${file.path === "manifest.yaml" || /\.(md|txt)$/.test(file.path)}>
    <summary>${head}</summary>
    <${Text} block value=${file.text} label=${file.path} />
  </details>`;
}

function Diff({ project, entry }) {
  const versions = entry.versions.map((v) => v.version);
  const [from, setFrom] = useState(versions[1]);
  const [to, setTo] = useState(versions[0]);
  const [asked, setAsked] = useState({ from: versions[1], to: versions[0] });
  const same = from === to;
  const base = baseRef(entry);
  const fromRef = `${base}@${asked.from}`;
  const toRef = `${base}@${asked.to}`;
  const [view] = useView(`/api/projects/${project.id}/registry/diff?${query({ from: fromRef, to: toRef })}`, project.id);
  // useView keeps the previous answer while a new pair loads (or fails): show an answer only
  // under the pair it is for, never the last pair's hunks under the new labels.
  const answer = view.data;
  const result = answer && answer.from && answer.to && answer.from.ref === fromRef && answer.to.ref === toRef ? answer : null;
  const shown = { ...view, data: result, stale: Boolean(result) && view.stale };
  const stats = diffStats(result);
  const pick = (label, value, set, id) => html`<label for=${id}>${label}</label>
    <select id=${id} value=${value} onChange=${(event) => set(event.currentTarget.value)}>
      ${versions.map((v) => html`<option key=${v} value=${v}>${v}</option>`)}
    </select>`;
  return html`<${Card} title="Compare versions" busy=${view.loading}>
    <form class="row compare" onSubmit=${(event) => { event.preventDefault(); if (!same) setAsked({ from, to }); }}>
      ${pick("From", from, setFrom, "diff-from")}
      ${pick("To", to, setTo, "diff-to")}
      <${Action} type="submit" tone="secondary" disabled=${same && "Pick two different versions."}>Compare<//>
    </form>
    ${!result || view.error ? html`<${ViewState} view=${shown} />` : null}
    ${result
      ? html`<p role="status">${result.identical
          ? html`<${Text} value=${`${asked.from} and ${asked.to} are identical.`} />`
          : html`<${Text} value=${`${asked.from} → ${asked.to}: ${result.files.length} ${result.files.length === 1 ? "file" : "files"} changed, ${stats.added} lines added, ${stats.removed} removed.`} />`}</p>
        ${(result.files || []).map((file, index) => html`<${FileDiff} key=${`${file.path} ${index}`} file=${file} />`)}`
      : null}
  <//>`;
}

function FileDiff({ file }) {
  const reason = noHunkReason(file);
  const label = `${file.status} ${file.path}${file.type === "dir" ? "/" : ""}`;
  return html`<div class="file-diff">
    <h3 class="small"><span class=${`change ${file.status}`}>${file.status}</span> <code><${Text} value=${file.path} /></code></h3>
    ${reason ? html`<p class="muted small"><${Text} value=${reason} /></p>` : null}
    ${(file.hunks || []).length
      ? html`<pre class="text diff" tabindex="0" aria-label=${`Changes in ${label}`}>${file.hunks.map((hunk) => html`<div class="diff-hunk">${hunkHeader(hunk)}</div>${hunk.lines.map((line) => html`<div class=${line.op === "+" ? "diff-add" : line.op === "-" ? "diff-del" : ""}>${line.op}<${Text} value=${line.text} />${line.no_newline ? html`<span class="muted"> (no newline at end)</span>` : null}</div>`)}`)}</pre>`
      : null}
  </div>`;
}

function UsedBy({ project, version }) {
  const [view] = useView(`/api/projects/${project.id}/registry/used-by?${query({ ref: version.ref })}`, project.id);
  const rows = (view.data && Array.isArray(view.data.projects) && view.data.projects) || [];
  return html`<${Card} title=${`Used by ${version.version}`} busy=${view.loading}>
    <p class="muted small">Among the projects registered in this console for this environment. Declared is <code>agent.yaml</code> or <code>stack.yaml</code>, locked is <code>agent.lock</code> or <code>stack.lock</code>, deployed is recorded local state (the last observation, not a live check).</p>
    ${!view.data || view.error ? html`<${ViewState} view=${view} />` : null}
    ${rows.length
      ? html`<${Table} label=${`Used by ${version.ref}`} columns=${["Project", "Declared", "Locked", "Deployed"]}>
          ${rows.map((row) => html`<tr key=${row.project}>
            <td><${Text} value=${row.agent || row.stack || row.project} /><div class="muted small"><${Text} value=${row.project} /></div></td>
            <td>${(row.declared || []).length ? (row.declared || []).map((d) => html`<div><${Text} value=${`${d.file} at ${d.at}`} /></div>`) : html`<span class="muted">no</span>`}</td>
            <td>${(row.locked || []).length ? (row.locked || []).map((l) => html`<div><${Text} value=${l.file} /> <code class="small">${String(l.sha256 || "").slice(0, 19)}…</code></div>`) : html`<span class="muted">no</span>`}</td>
            <td>${deployedLines(row).length ? deployedLines(row).map((line) => html`<div><${Text} value=${line} /></div>`) : html`<span class="muted">no</span>`}
              ${(row.errors || []).map((e) => html`<div class="small notice error"><${Text} value=${`${e.file || "project"}: ${e.error}`} /></div>`)}</td>
          </tr>`)}
        <//>`
      : null}
  <//>`;
}

/** The routes: a list and a detail per kind, under Registry in the sidebar. */
export const REGISTRY_ROUTES = KINDS.flatMap((meta) => [
  { pattern: `/registry/${meta.path}`, title: meta.nav, nav: meta.nav, group: "Registry", scoped: true, screen: registryList(meta) },
  { pattern: `/registry/${meta.path}/:name`, title: (params) => `${meta.one} ${params.name}`, navAs: `/registry/${meta.path}`, scoped: true, screen: registryDetail(meta) },
]);
