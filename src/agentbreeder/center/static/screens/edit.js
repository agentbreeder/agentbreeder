// Prompt and eval-set editing and "Move agent to this version" (#826, M10), mounted from the
// registry detail. Each kind's spec (lib/edit.js EDIT_KINDS) names its files, its body (the mono
// textarea, or the eval case editor in screens/cases.js) and its confirm tier.
//
// Edit: change a file of a project version → Review (Cmd/Ctrl+S) → the server
// runs `registry edit … --dry-run` with the text on stdin and shows the diff → "Save as X.Y.Z"
// sends the same text with the reviewed digest, so what is written is what was reviewed (S-04). A
// version that appears meanwhile (another tab) is VERSION_EXISTS: pick another and review again.
//
// Knowledge documents (#830, M14): the same review and save for docs/NAME.md|txt, plus "New
// document" (a new path, saved with `--set`) and "Remove this document" (`--remove PATH`, the
// same dry run → diff → write bound to the digest). The review says the documents land in a
// git-tracked registry before Save.
//
// Fork to edit (#828): a version outside the project registry (built in, or on a shared path) is
// copied into it first: `registry fork REF --version X.Y.Z --dry-run` lists the copy's files and
// digest, then "Fork as X.Y.Z" copies exactly that (--expect-digest), and the copy is edited.
//
// Move: `agent set-ref OLD NEW` the same way (dry run → diff → write with the reviewed agent.yaml
// digest), then "Build and update agent.lock" (a plain build: `release` and Build only use
// --locked and refuse a lock without the new ref), then release from Build and release.

import { caseAtLine, caseProblems, parseCases } from "../lib/cases.js";
import { EDITABLE_KINDS, BUMPS, agentUses, editKind, editProblem, editableFiles, forkPlan, gutterLines, isSaveShortcut, movableFrom, needsNewReview, newFile, notEditableReason, saveVersion } from "../lib/edit.js";
import { KINDS, diffStats } from "../lib/registry.js";
import { href } from "../lib/router.js";
import { Action, Actions, Card, Notice, Text, useConfirm, useConsole } from "../ui/components.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";
import { CasesBody } from "./cases.js";

/** The registry browser's labels for `kind` (its route segment), or null. */
const registryMeta = (kind) => KINDS.find((meta) => meta.kind === kind) || null;

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
export function Editor({ project, kind, entry, version, show, focusCase = null, FileDiff, onSaved, onRefresh }) {
  if (!EDITABLE_KINDS.includes(kind) || !show) return null;
  if (version.rootKind !== "project") {
    return html`<${Fork} key=${version.ref} project=${project} kind=${kind} entry=${entry} version=${version} onSaved=${onSaved} onRefresh=${onRefresh} />`;
  }
  return html`<${EditorBody} key=${version.ref} project=${project} kind=${kind} entry=${entry} version=${version} show=${show} focusCase=${focusCase} FileDiff=${FileDiff} onSaved=${onSaved} onRefresh=${onRefresh} />`;
}

/**
 * Fork: copy a version into the project registry, reviewed first (the files and digest of the
 * copy), as the next free patch version or, with a new name, under that name at the same
 * version (M9a, #825). `mode` "edit" is "Fork to edit" (#828) for the kinds the console edits;
 * "copy" is the registry browser's Fork for the others (services, MCP servers, runtimes). A
 * code-bearing copy takes the three-tier confirm, which the server checks again.
 */
export function Fork({ project, kind, entry, version, mode = "edit", onSaved, onRefresh }) {
  const { client, navigate } = useConsole();
  const [review, setReview] = useState(null); // {data, version, name}
  const [busy, setBusy] = useState(null); // "review" | "fork"
  const [notice, setNotice] = useState(null);
  const [name, setName] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const existing = entry.versions.map((v) => v.version);
  const plan = forkPlan(entry.name, version.version, name, existing);
  const where = version.rootKind === "builtin" ? "built in" : version.rootKind === "path" ? "in a shared path" : "in this project";
  const path = `/api/projects/${project.id}/registry/fork`;
  const current = review && review.version === plan.version && review.name === plan.name ? review : null;
  // A confirm belongs to the review it was given for: a changed name or version clears it.
  useEffect(() => {
    if (!current) setConfirmed(false);
  }, [current]);
  const fail = (problem) => {
    if (needsNewReview(problem)) setReview(null);
    if (problem.code === "VERSION_EXISTS") onRefresh();
    setNotice({ tone: "error", text: editProblem(problem).text });
  };
  const body = () => ({ ref: version.ref, ...(plan.name ? { as: plan.name } : {}), version: plan.version });
  const ask = async () => {
    if (busy || plan.problem) return;
    setBusy("review");
    setNotice(null);
    setConfirmed(false);
    const { data, problem } = await send(client, path, body());
    setBusy(null);
    if (problem) return fail(problem);
    setReview({ data, version: plan.version, name: plan.name });
  };
  const fork = async () => {
    if (busy || !current) return;
    const code = Boolean(current.data.runs_code);
    if (code && !confirmed) return;
    setBusy("fork");
    setNotice(null);
    const { data, problem } = await send(client, path, { ...body(), digest: current.data.sha256, ...(code ? { code_confirmed: true } : {}) });
    setBusy(null);
    if (problem) return fail(problem);
    setReview(null);
    setConfirmed(false);
    if (plan.name) {
      // Another name is another registry page: open it.
      const meta = registryMeta(kind);
      if (meta) return navigate(href(`/registry/${meta.path}/:name`, { name: data.name }).replace(/^#/, ""));
      onRefresh();
      return setNotice({ tone: "ok", text: `Forked ${version.ref} into this project as ${data.ref}.` });
    }
    const next = mode === "edit" ? "edit the copy" : "change it with agentbreeder registry edit or publish a new version";
    onSaved(data.version, `Forked ${version.ref} into this project as ${data.ref}; ${next}. ${version.ref} is unchanged, and an agent uses the copy only after you move it there and release.`);
  };
  const files = current && Array.isArray(current.data.files) ? current.data.files.filter((file) => file && file.type === "file") : [];
  const target = plan.name ? `${kind}/${plan.name}@${plan.version}` : plan.version;
  const intro = mode === "edit"
    ? html`This version is ${where}, so it is not edited in place. Fork it into this project's registry as a new version the project owns, then edit the copy.`
    : html`This version is ${where}. Fork it into this project's registry as a version the project owns; the console does not edit ${kind} files, so change the copy with <code>agentbreeder registry edit</code>.`;
  return html`<${Card} title=${mode === "edit" ? "Edit" : "Fork"}>
    <p class="muted">${intro}</p>
    <div class="stack fork-name">
      <label for=${`fork-as-${version.ref}`}>Fork under another name <span class="muted small">(optional)</span></label>
      <input id=${`fork-as-${version.ref}`} autocomplete="off" spellcheck="false" maxlength="63" placeholder=${entry.name} value=${name} onInput=${(event) => { setName(event.currentTarget.value); setNotice(null); }} />
    </div>
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    ${current
      ? html`<section class="edit-review" aria-label="Review">
          <h3>Review: fork ${version.version} → <${Text} value=${target} /></h3>
          <p role="status"><${Text} value=${`Copies ${files.length} ${files.length === 1 ? "file" : "files"} into this project's registry as ${current.data.ref}; only the manifest's ${plan.name ? "name" : "version"} line changes.`} /></p>
          <ul class="plain">${files.map((file) => html`<li key=${file.path}><code><${Text} value=${file.path} /></code></li>`)}</ul>
          ${current.data.runs_code
            ? html`<fieldset class="code-confirm">
                <legend>This code runs in agents</legend>
                <p>Forking copies this ${kind} version unchanged into a version the project owns. Building and releasing an agent that uses the copy runs this code as you, with the stack's model key and database. The eval gate is a quality gate, not a security review.</p>
                <label class="check"><input type="checkbox" checked=${confirmed} onChange=${(event) => setConfirmed(event.currentTarget.checked)} /> I reviewed this code and trust it to run in agents</label>
              </fieldset>`
            : null}
          <${Actions}>
            <button type="button" class="action primary" disabled=${Boolean(busy) || (Boolean(current.data.runs_code) && !confirmed)} onClick=${fork}>${busy === "fork" ? "Forking…" : html`Fork as <${Text} value=${target} />`}</button>
            <${Action} tone="secondary" onClick=${() => setReview(null)} disabled=${busy ? "Waiting for the console." : false}>Cancel<//>
          <//>
        </section>`
      : html`<${Actions}>
          <${Action} onClick=${ask} disabled=${busy ? "Waiting for the console." : plan.problem}>${busy === "review" ? "Reviewing…" : html`${mode === "edit" ? "Fork to edit as" : "Fork into this project as"} <${Text} value=${target || "X.Y.Z"} />`}<//>
        <//>`}
    <p class="muted small">In a terminal: <code><${Text} value=${`agentbreeder registry fork ${version.ref}${plan.name ? ` --as ${plan.name}` : ""} --version ${plan.version || "X.Y.Z"}`} /></code></p>
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

function EditorBody({ project, kind, entry, version, show, focusCase, FileDiff, onSaved, onRefresh }) {
  const { client } = useConsole();
  const spec = editKind(kind);
  const files = editableFiles(show, kind);
  const others = (show.files || []).map((file) => [file, notEditableReason(file, kind, show)]).filter(([, why]) => why);
  const [added, setAdded] = useState(null); // a new file's path (`add` kinds), not saved yet
  const [path, setPath] = useState(files.length ? files[0].path : null);
  const paths = [...files.map((file) => file.path), ...(added && !files.some((file) => file.path === added) ? [added] : [])];
  const isNew = Boolean(path) && path === added && !files.some((file) => file.path === added);
  const original = (files.find((file) => file.path === path) || {}).text ?? "";
  const [text, setText] = useState(original);
  const [part, setPart] = useState("patch");
  const [review, setReview] = useState(null); // {data, text, version, path, remove}
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
  const current = review && review.version === target && review.path === path && (review.remove ? !dirty : review.text === text);

  const pick = async (next) => {
    if (next === path) return;
    if (dirty && !(await confirm({ title: "Discard your changes?", text: `Your changes to ${path} are not saved.`, confirm: "Discard" }))) return;
    if (isNew && next !== added) setAdded(null); // an empty new file is dropped when left
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
  const create = async (name) => {
    const made = newFile(kind, name, show);
    if (made.error) return made.error;
    if (dirty && !(await confirm({ title: "Discard your changes?", text: `Your changes to ${path} are not saved.`, confirm: "Discard" }))) return "";
    setAdded(made.path);
    setPath(made.path);
    setText("");
    setReview(null);
    setNotice(null);
    setInvalid(null);
    setReset((n) => n + 1);
    return "";
  };
  const ask = async (remove = false) => {
    if (busy || !target) return; // Cmd/Ctrl+S does nothing while a request is in flight
    if (remove ? dirty || isNew : !dirty || casesToFix) return;
    setBusy("review");
    setNotice(null);
    setInvalid(null);
    setConfirmed(false);
    const body = remove ? { ref: version.ref, path, remove: true, version: target } : { ref: version.ref, path, text, version: target };
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
    setReview({ data, text: remove ? null : text, version: target, path, remove });
  };
  const save = async () => {
    if (busy || !current) return;
    setBusy("save");
    setNotice(null);
    const change = review.remove ? { remove: true } : { text: review.text };
    const body = { ref: version.ref, path, ...change, version: review.version, digest: review.data.sha256 };
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
    if (review.path === added) setAdded(null);
    const what = review.remove ? `Saved ${data.ref} without ${review.path}.` : `Saved ${data.ref}.`;
    onSaved(data.version, `${what} ${version.ref} is unchanged. An agent uses the new version only after you move it there and release.`);
  };
  const onKey = (event) => {
    if (!isSaveShortcut(event)) return;
    event.preventDefault();
    if (current) document.getElementById("edit-save")?.focus();
    else ask(false);
  };
  const stats = current ? diffStats(review.data.diff) : null;

  return html`<${Card} title=${`Edit ${version.version}`}>
    ${dialog}
    <p class="muted small">Saving publishes a new version; <code>${version.version}</code> never changes. ${spec.note}</p>
    ${spec.add ? html`<${NewFile} spec=${spec} busy=${Boolean(busy)} onCreate=${create} />` : null}
    ${paths.length
      ? html`
        <div class="row edit-bar">
          ${paths.length > 1
            ? html`<label for="edit-file">File</label>
              <select id="edit-file" value=${path} onChange=${(event) => pick(event.currentTarget.value)}>
                ${paths.map((each) => html`<option key=${each} value=${each}>${each === added && isNew ? `${each} (new)` : each}</option>`)}
              </select>`
            : html`<span>File <code><${Text} value=${path} /></code>${isNew ? " (new)" : ""}</span>`}
          <label for="edit-bump">New version</label>
          <select id="edit-bump" value=${part} onChange=${(event) => setPart(event.currentTarget.value)}>
            ${BUMPS.map((bump) => html`<option key=${bump} value=${bump}>${bump}: ${saveVersion(version.version, bump, existing)}</option>`)}
          </select>
          <span class="muted small" role="status">${dirty ? "Unsaved changes" : "No changes"}</span>
        </div>
        ${original.includes("\r") ? html`<p class="notice warn">This file has carriage returns (CRLF line endings). The browser edits it with LF endings, so a save changes every such line; use <code>agentbreeder registry edit</code> in a terminal to keep them.</p>` : null}
        ${spec.body === "cases"
          ? html`<${CasesBody} key=${`${path} ${reset}`} parsed=${cases} original=${original} onText=${change} invalid=${invalid} focus=${reset ? null : focusCase}
              fallback=${html`<${TextBody} path=${path} text=${text} onText=${setText} onKey=${onKey} />`} />`
          : html`<${TextBody} path=${path} text=${text} onText=${setText} onKey=${onKey} />`}
        <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
        <${Actions}>
          <${Action} onClick=${() => ask(false)} disabled=${busy ? "Waiting for the console." : !dirty ? (isNew ? "Write the document first." : "Change the text first.") : casesToFix ? "Fix the cases marked with ! first." : false}>${busy === "review" ? "Reviewing…" : "Review changes"}<//>
          <${Action} tone="secondary" onClick=${async () => { if (await confirm({ title: "Discard your changes?", text: `Your changes to ${path} are not saved.`, confirm: "Discard" })) discard(); }} disabled=${!dirty || busy ? "Nothing to discard." : false}>Discard<//>
          ${spec.remove && !isNew
            ? html`<${Action} tone="secondary" onClick=${() => ask(true)} disabled=${busy ? "Waiting for the console." : dirty ? "Discard your changes first." : false}>Remove this document<//>`
            : null}
        <//>
        ${spec.body === "text" || (cases && cases.error) ? html`<p class="muted small">Cmd+S or Ctrl+S in the text opens the review.</p>` : null}
        ${current
          ? html`<section class="edit-review" aria-label="Review">
              <h3>Review: ${version.version} → ${review.version}${review.remove ? `, without ${review.path}` : ""}</h3>
              <p role="status"><${Text} value=${`${review.data.diff.files.length} ${review.data.diff.files.length === 1 ? "file" : "files"} changed, ${stats.added} lines added, ${stats.removed} removed (the manifest's version line too).`} /></p>
              ${spec.warning ? html`<p class="notice warn" role="note"><${Text} value=${spec.warning} /></p>` : null}
              ${review.remove && files.length === 1 ? html`<p class="notice warn">This removes the version's last document: the agent's knowledge search finds nothing in the new version.</p>` : null}
              ${review.data.diff.files.map((file, index) => html`<${FileDiff} key=${`${file.path} ${index}`} file=${file} />`)}
              ${spec.confirm === "code"
                ? html`<fieldset class="code-confirm">
                    <legend>This code runs in agents</legend>
                    <p>Building and releasing an agent that uses the new version runs this code as you, with the stack's model key and database. The eval gate is a quality gate, not a security review.</p>
                    <label class="check"><input type="checkbox" checked=${confirmed} onChange=${(event) => setConfirmed(event.currentTarget.checked)} /> I reviewed this code and trust it to run in agents</label>
                  </fieldset>`
                : null}
              <${Actions}>
                <button type="button" id="edit-save" class="action primary" disabled=${Boolean(busy) || (spec.confirm === "code" && !confirmed)} onClick=${save}>${busy === "save" ? "Saving…" : review.remove ? `Remove and save as ${review.version}` : `Save as ${review.version}`}</button>
              <//>
            </section>`
          : review ? html`<p class="muted small">The text or version changed since the review: review again.</p>` : null}`
      : html`<p class="muted">No file of this version can be edited here.</p>`}
    ${others.length ? html`<p class="muted small">Not editable here: ${others.map(([file, why], i) => html`${i ? ", " : ""}<code><${Text} value=${file.path} /></code> (${why})`)}. Use <code>agentbreeder registry edit</code> in a terminal.</p>` : null}
  <//>`;
}

/** "New document": a name for a new file of an `add` kind; `onCreate(name)` answers an error or "". */
function NewFile({ spec, busy, onCreate }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    const problem = await onCreate(name);
    setError(problem || "");
    if (!problem) setName("");
  };
  return html`<form class="row new-file" onSubmit=${submit}>
    <label for="new-file-name">${spec.add.label}</label>
    <span class="muted"><code>${spec.add.folder}</code></span>
    <input id="new-file-name" value=${name} placeholder=${spec.add.hint} autocomplete="off" spellcheck="false"
      aria-describedby=${error ? "new-file-error" : undefined} aria-invalid=${error ? "true" : undefined}
      onInput=${(event) => setName(event.currentTarget.value)} />
    <${Action} type="submit" tone="secondary" disabled=${busy ? "Waiting for the console." : false}>Add<//>
    ${error ? html`<p id="new-file-error" class="small notice error" role="alert"><${Text} value=${error} /></p>` : null}
  </form>`;
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
