// Prompt and eval-set editing and "Move agent to this version" (#826, M10), mounted from the
// registry detail. Each kind's spec (lib/edit.js EDIT_KINDS) names its files, its body (the mono
// textarea, or the eval case editor in screens/cases.js) and its confirm tier.
//
// Edit: change a file of a project version → Review (Cmd/Ctrl+S) → the server
// runs `registry edit … --dry-run` with the text on stdin and shows the diff → "Save as X.Y.Z"
// sends the same text with the reviewed digest, so what is written is what was reviewed (S-04). A
// version that appears meanwhile (another tab) is VERSION_EXISTS: pick another and review again.
//
// Fork to edit (#828): a version outside the project registry (built in, or on a shared path) is
// copied into it first: `registry fork REF --version X.Y.Z --dry-run` lists the copy's files and
// digest, then "Fork as X.Y.Z" copies exactly that (--expect-digest), and the copy is edited.
//
// Move: `agent set-ref OLD NEW` the same way (dry run → diff → write with the reviewed agent.yaml
// digest), then "Build and update agent.lock" (a plain build: `release` and Build only use
// --locked and refuse a lock without the new ref), then release from Build and release.

import { caseAtLine, caseProblems, parseCases } from "../lib/cases.js";
import { EDITABLE_KINDS, BUMPS, agentUses, editKind, editProblem, editableFiles, gutterLines, isSaveShortcut, movableFrom, needsNewReview, notEditableReason, saveVersion } from "../lib/edit.js";
import { diffStats } from "../lib/registry.js";
import { href } from "../lib/router.js";
import { Action, Actions, Card, Notice, Text, useConfirm, useConsole } from "../ui/components.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";
import { CasesBody } from "./cases.js";

/** POST that returns {data, problem}: a 4xx (the server's own check) and a CLI refusal alike. */
async function send(client, path, body) {
  try {
    const reply = await client.request(path, { body });
    return { data: reply.error ? null : reply.data, problem: reply.error };
  } catch (error) {
    if (error.kind === "session") throw error;
    return { data: null, problem: { code: error.code, message: error.message } };
  }
}

/**
 * Whether an editor has unsaved changes: the registry detail asks before it shows another
 * version (which would unmount the editor). Navigation to another page is not guarded.
 */
export const editing = { dirty: false };

/** Ask before the tab closes or reloads while there are unsaved changes. */
function useLeaveGuard(dirty) {
  useEffect(() => {
    editing.dirty = dirty;
    if (!dirty) return undefined;
    const guard = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => {
      editing.dirty = false;
      window.removeEventListener("beforeunload", guard);
    };
  }, [dirty]);
}

/**
 * The editor for one version. `entry` is the name's row (its versions), `version` the shown one,
 * `show` its `registry show` answer, `FileDiff` the registry screen's diff renderer, `onSaved(v)`
 * re-reads the registry and shows version v, `onRefresh` re-reads it (a version appeared).
 */
export function Editor({ project, kind, entry, version, show, FileDiff, onSaved, onRefresh }) {
  if (!EDITABLE_KINDS.includes(kind) || !show) return null;
  if (version.rootKind !== "project") {
    return html`<${Fork} key=${version.ref} project=${project} entry=${entry} version=${version} onSaved=${onSaved} onRefresh=${onRefresh} />`;
  }
  return html`<${EditorBody} key=${version.ref} project=${project} kind=${kind} entry=${entry} version=${version} show=${show} FileDiff=${FileDiff} onSaved=${onSaved} onRefresh=${onRefresh} />`;
}

/**
 * Fork to edit: copy a built-in or shared version into the project registry as the next free
 * patch version, reviewed first (the files and digest of the copy), then edit the copy.
 */
function Fork({ project, entry, version, onSaved, onRefresh }) {
  const { client } = useConsole();
  const [review, setReview] = useState(null); // the dry run's answer
  const [busy, setBusy] = useState(null); // "review" | "fork"
  const [notice, setNotice] = useState(null);
  const existing = entry.versions.map((v) => v.version);
  const target = saveVersion(version.version, "patch", existing);
  const where = version.rootKind === "builtin" ? "built in" : "in a shared path";
  const path = `/api/projects/${project.id}/registry/fork`;
  const fail = (problem) => {
    if (needsNewReview(problem)) setReview(null);
    if (problem.code === "VERSION_EXISTS") onRefresh();
    setNotice({ tone: "error", text: editProblem(problem).text });
  };
  const ask = async () => {
    if (busy || !target) return;
    setBusy("review");
    setNotice(null);
    const { data, problem } = await send(client, path, { ref: version.ref, version: target });
    setBusy(null);
    if (problem) return fail(problem);
    setReview({ data, version: target });
  };
  const fork = async () => {
    if (busy || !review || review.version !== target) return;
    setBusy("fork");
    setNotice(null);
    const { data, problem } = await send(client, path, { ref: version.ref, version: review.version, digest: review.data.sha256 });
    setBusy(null);
    if (problem) return fail(problem);
    setReview(null);
    onSaved(data.version, `Forked ${version.ref} into this project as ${data.ref}; edit the copy. ${version.ref} is unchanged, and an agent uses the copy only after you move it there and release.`);
  };
  const files = review && Array.isArray(review.data.files) ? review.data.files.filter((file) => file && file.type === "file") : [];
  return html`<${Card} title="Edit">
    <p class="muted">This version is ${where}, so it is not edited in place. Fork it into this project's registry as a new version the project owns, then edit the copy.</p>
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    ${review && review.version === target
      ? html`<section class="edit-review" aria-label="Review">
          <h3>Review: fork ${version.version} → ${review.version}</h3>
          <p role="status"><${Text} value=${`Copies ${files.length} ${files.length === 1 ? "file" : "files"} into this project's registry as ${review.data.ref}; only the manifest's version line changes.`} /></p>
          <ul class="plain">${files.map((file) => html`<li key=${file.path}><code><${Text} value=${file.path} /></code></li>`)}</ul>
          ${review.data.runs_code ? html`<p class="notice warn">This version holds code that runs in agents. Forking copies it unchanged; review it before you move an agent to the copy.</p>` : null}
          <${Actions}>
            <button type="button" class="action primary" disabled=${Boolean(busy)} onClick=${fork}>${busy === "fork" ? "Forking…" : `Fork as ${review.version}`}</button>
            <${Action} tone="secondary" onClick=${() => setReview(null)} disabled=${busy ? "Waiting for the console." : false}>Cancel<//>
          <//>
        </section>`
      : html`<${Actions}>
          <${Action} onClick=${ask} disabled=${busy ? "Waiting for the console." : !target ? "No free version to fork into." : false}>${busy === "review" ? "Reviewing…" : `Fork to edit as ${target}`}<//>
        <//>`}
    <p class="muted small">In a terminal: <code><${Text} value=${`agentbreeder registry fork ${version.ref} --version ${target || "X.Y.Z"}`} /></code></p>
  <//>`;
}

/** The mono textarea with its line gutter (the "text" body). */
function TextBody({ path, text, onText, onKey }) {
  const gutter = useRef(null);
  return html`<div class="editor">
    <pre class="gutter" ref=${gutter} aria-hidden="true">${gutterLines(text)}</pre>
    <textarea id="edit-text" aria-label=${`Text of ${path}`} spellcheck="false" wrap="off" value=${text}
      onInput=${(event) => onText(event.currentTarget.value)}
      onScroll=${(event) => { if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop; }}
      onKeyDown=${onKey}></textarea>
  </div>`;
}

function EditorBody({ project, kind, entry, version, show, FileDiff, onSaved, onRefresh }) {
  const { client } = useConsole();
  const spec = editKind(kind);
  const files = editableFiles(show, kind);
  const others = (show.files || []).map((file) => [file, notEditableReason(file, kind, show)]).filter(([, why]) => why);
  const [path, setPath] = useState(files.length ? files[0].path : null);
  const original = (files.find((file) => file.path === path) || {}).text ?? "";
  const [text, setText] = useState(original);
  const [part, setPart] = useState("patch");
  const [review, setReview] = useState(null); // {data, text, version, path}
  const [busy, setBusy] = useState(null); // "review" | "save"
  const [notice, setNotice] = useState(null);
  const [invalid, setInvalid] = useState(null); // the CLI's EVAL_INVALID at its case: {index, message}
  const [confirmed, setConfirmed] = useState(false); // the code confirm (spec.confirm === "code")
  const [reset, setReset] = useState(0); // re-made case forms after Discard or another file
  const [dialog, confirm] = useConfirm();
  const dirty = text !== original;
  const cases = spec.body === "cases" ? parseCases(text) : null; // parsed once per render
  const change = (next) => {
    setText(next);
    setInvalid(null); // the CLI's finding was about the text it loaded
  };
  const casesToFix = cases && !cases.error ? caseProblems(cases.entries).count : 0;
  useLeaveGuard(dirty);
  const existing = entry.versions.map((v) => v.version);
  const target = saveVersion(version.version, part, existing);
  const current = review && review.text === text && review.version === target && review.path === path;

  const pick = async (next) => {
    if (next === path) return;
    if (dirty && !(await confirm({ title: "Discard your changes?", text: `Your changes to ${path} are not saved.`, confirm: "Discard" }))) return;
    setPath(next);
    setText((files.find((file) => file.path === next) || {}).text ?? "");
    setReview(null);
    setNotice(null);
    setInvalid(null);
    setReset((n) => n + 1);
  };
  const discard = () => {
    setText(original);
    setReview(null);
    setInvalid(null);
    setReset((n) => n + 1);
  };
  const ask = async () => {
    if (busy || !dirty || !target || casesToFix) return; // Cmd/Ctrl+S does nothing while a request is in flight
    setBusy("review");
    setNotice(null);
    setInvalid(null);
    setConfirmed(false);
    const body = { ref: version.ref, path, text, version: target };
    const { data, problem } = await send(client, `/api/projects/${project.id}/registry/edit`, body);
    setBusy(null);
    if (problem) {
      setReview(null);
      if (problem.code === "VERSION_EXISTS") onRefresh();
      if (problem.code === "EVAL_INVALID" && cases && !cases.error) {
        const index = caseAtLine(cases.entries, problem.message, path);
        if (index !== null) setInvalid({ index, message: editProblem(problem).text });
      }
      setNotice({ tone: "error", text: editProblem(problem).text });
      return;
    }
    setReview({ data, text, version: target, path });
  };
  const save = async () => {
    if (busy || !current) return;
    setBusy("save");
    setNotice(null);
    const body = { ref: version.ref, path, text: review.text, version: review.version, digest: review.data.sha256 };
    if (spec.confirm === "code") body.code_confirmed = confirmed;
    const { data, problem } = await send(client, `/api/projects/${project.id}/registry/edit`, body);
    setBusy(null);
    if (problem) {
      if (needsNewReview(problem)) setReview(null);
      if (problem.code === "VERSION_EXISTS") onRefresh(); // learn the new version: offer the next free one
      setNotice({ tone: "error", text: editProblem(problem).text });
      return;
    }
    setReview(null);
    onSaved(data.version, `Saved ${data.ref}. ${version.ref} is unchanged. An agent uses the new version only after you move it there and release.`);
  };
  const onKey = (event) => {
    if (!isSaveShortcut(event)) return;
    event.preventDefault();
    if (current) document.getElementById("edit-save")?.focus();
    else ask();
  };
  const stats = current ? diffStats(review.data.diff) : null;

  return html`<${Card} title=${`Edit ${version.version}`}>
    ${dialog}
    <p class="muted small">Saving publishes a new version; <code>${version.version}</code> never changes. ${spec.note}</p>
    ${files.length
      ? html`
        <div class="row edit-bar">
          ${files.length > 1
            ? html`<label for="edit-file">File</label>
              <select id="edit-file" value=${path} onChange=${(event) => pick(event.currentTarget.value)}>
                ${files.map((file) => html`<option key=${file.path} value=${file.path}>${file.path}</option>`)}
              </select>`
            : html`<span>File <code><${Text} value=${path} /></code></span>`}
          <label for="edit-bump">New version</label>
          <select id="edit-bump" value=${part} onChange=${(event) => setPart(event.currentTarget.value)}>
            ${BUMPS.map((bump) => html`<option key=${bump} value=${bump}>${bump}: ${saveVersion(version.version, bump, existing)}</option>`)}
          </select>
          <span class="muted small" role="status">${dirty ? "Unsaved changes" : "No changes"}</span>
        </div>
        ${original.includes("\r") ? html`<p class="notice warn">This file has carriage returns (CRLF line endings). The browser edits it with LF endings, so a save changes every such line; use <code>agentbreeder registry edit</code> in a terminal to keep them.</p>` : null}
        ${spec.body === "cases"
          ? html`<${CasesBody} key=${`${path} ${reset}`} parsed=${cases} original=${original} onText=${change} invalid=${invalid}
              fallback=${html`<${TextBody} path=${path} text=${text} onText=${setText} onKey=${onKey} />`} />`
          : html`<${TextBody} path=${path} text=${text} onText=${setText} onKey=${onKey} />`}
        <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
        <${Actions}>
          <${Action} onClick=${ask} disabled=${busy ? "Waiting for the console." : !dirty ? "Change the text first." : casesToFix ? "Fix the cases marked with ! first." : false}>${busy === "review" ? "Reviewing…" : "Review changes"}<//>
          <${Action} tone="secondary" onClick=${async () => { if (await confirm({ title: "Discard your changes?", text: `Your changes to ${path} are not saved.`, confirm: "Discard" })) discard(); }} disabled=${!dirty || busy ? "Nothing to discard." : false}>Discard<//>
        <//>
        ${spec.body === "text" || (cases && cases.error) ? html`<p class="muted small">Cmd+S or Ctrl+S in the text opens the review.</p>` : null}
        ${current
          ? html`<section class="edit-review" aria-label="Review">
              <h3>Review: ${version.version} → ${review.version}</h3>
              <p role="status"><${Text} value=${`${review.data.diff.files.length} ${review.data.diff.files.length === 1 ? "file" : "files"} changed, ${stats.added} lines added, ${stats.removed} removed (the manifest's version line too).`} /></p>
              ${review.data.diff.files.map((file, index) => html`<${FileDiff} key=${`${file.path} ${index}`} file=${file} />`)}
              ${spec.confirm === "code"
                ? html`<fieldset class="code-confirm">
                    <legend>This code runs in agents</legend>
                    <p>Building and releasing an agent that uses the new version runs this code as you, with the stack's model key and database. The eval gate is a quality gate, not a security review.</p>
                    <label class="check"><input type="checkbox" checked=${confirmed} onChange=${(event) => setConfirmed(event.currentTarget.checked)} /> I reviewed this code and trust it to run in agents</label>
                  </fieldset>`
                : null}
              <${Actions}>
                <button type="button" id="edit-save" class="action primary" disabled=${Boolean(busy) || (spec.confirm === "code" && !confirmed)} onClick=${save}>${busy === "save" ? "Saving…" : `Save as ${review.version}`}</button>
              <//>
            </section>`
          : review ? html`<p class="muted small">The text or version changed since the review: review again.</p>` : null}`
      : html`<p class="muted">No file of this version can be edited here.</p>`}
    ${others.length ? html`<p class="muted small">Not editable here: ${others.map(([file, why], i) => html`${i ? ", " : ""}<code><${Text} value=${file.path} /></code> (${why})`)}. Use <code>agentbreeder registry edit</code> in a terminal.</p>` : null}
  <//>`;
}

/**
 * "Move agent to this version": shown when agent.yaml declares another version of this name.
 * `onMoved` re-reads the registry (declared and locked columns change).
 */
export function MoveAgent({ project, kind, entry, version, onMoved }) {
  const { client, jobs, busyReason } = useConsole();
  const olds = movableFrom(entry, version);
  const [plan, setPlan] = useState(null); // {old, data}
  const [moved, setMoved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  if (!EDITABLE_KINDS.includes(kind)) return null;
  if (!olds.length && !moved) {
    if (!agentUses(version)) return null;
    return html`<${Card} title="Agent"><p class="muted">The project's <code>agent.yaml</code> uses this version.</p><//>`;
  }
  const preview = async (old) => {
    setBusy(true);
    setNotice(null);
    const { data, problem } = await send(client, `/api/projects/${project.id}/agent/set-ref`, { old: old.ref, new: version.ref });
    setBusy(false);
    if (problem) setNotice({ tone: "error", text: editProblem(problem).text });
    else setPlan({ old, data });
  };
  const write = async () => {
    setBusy(true);
    setNotice(null);
    const body = { old: plan.old.ref, new: version.ref, base: plan.data.base_sha256 };
    const { data, problem } = await send(client, `/api/projects/${project.id}/agent/set-ref`, body);
    setBusy(false);
    if (problem) {
      if (needsNewReview(problem)) setPlan(null);
      setNotice({ tone: "error", text: editProblem(problem).text });
      return;
    }
    setPlan(null);
    setMoved(data);
    onMoved();
  };
  const build = async () => {
    setNotice(null);
    try {
      await jobs.start(project.id, "build", { lock: "update" });
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  const jobBusy = busyReason(project.id);
  return html`<${Card} title="Move agent to this version">
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    ${moved
      ? html`<p role="status"><${Text} value=${`agent.yaml now uses ${moved.new} at ${moved.at} (was ${moved.old}).`} /></p>
        <p>Next: build to update <code>agent.lock</code>, then release. The agent answers with the new version only after a release passes the eval gate.</p>
        <${Actions} disabled=${jobBusy}>
          <${Action} onClick=${build}>Build and update agent.lock<//>
        <//>
        <p><a href=${href("/release")}>Build and release →</a></p>`
      : plan
        ? html`<p><${Text} value=${`agent.yaml: ${plan.data.at} ${plan.old.ref} → ${version.ref}`} /></p>
          <${Text} block value=${plan.data.diff} label="agent.yaml change" />
          <${Actions}>
            <${Action} onClick=${write} disabled=${busy ? "Writing agent.yaml…" : false}>Write agent.yaml<//>
            <${Action} tone="secondary" onClick=${() => setPlan(null)} disabled=${busy ? "Writing agent.yaml…" : false}>Cancel<//>
          <//>`
        : html`<p>The project's <code>agent.yaml</code> uses ${olds.map((old, i) => html`${i ? ", " : ""}<code>${old.version}</code>`)}. Moving it changes one line, shown first; nothing else in the file changes.</p>
          <${Actions} disabled=${busy ? "Reading agent.yaml…" : false}>
            ${olds.map((old) => html`<${Action} key=${old.ref} onClick=${() => preview(old)}>${olds.length > 1 ? `Move from ${old.version} to ${version.version}` : `Move agent to ${version.version}`}<//>`)}
          <//>`}
  <//>`;
}
