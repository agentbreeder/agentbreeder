// Registry ▸ Import (#825, M9a): copy a version directory from anywhere on this machine (another
// project, a shared folder, a download) into the project's registry as an immutable version.
//
// Review runs `registry import … --dry-run -- PATH` and lists the staged copy's files (the CLI
// refuses anything that is not a registry version directory before it lists one) and its digest;
// "Import as REF" sends the same request with that digest, so what is written is what was reviewed
// (S-04). Tools, services, eval sets, MCP servers and runtimes run in agents: their import takes
// the three-tier confirm (the preview, the "this code runs in agents" checkbox, the button), and
// the server refuses the write without it.

import { detailRoute, formProblem, importBody, importProblem, needsNewReview, reviewFiles, reviewMatches, reviewSummary, runsCode, terminalCommand } from "../lib/import.js";
import { ROOT_LABELS } from "../lib/registry.js";
import { href } from "../lib/router.js";
import { Action, Actions, Card, Notice, Text, useConsole } from "../ui/components.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";
import { FileView } from "./registry.js";

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

export function ImportVersion({ project }) {
  const { client } = useConsole();
  const [form, setForm] = useState({ path: "", name: "", version: "" });
  const [review, setReview] = useState(null); // {body, data}
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(null); // "review" | "import"
  const [notice, setNotice] = useState(null);
  const [done, setDone] = useState(null); // the write's answer
  const route = `/api/projects/${project.id}/registry/import`;
  const doneRef = useRef(null);
  // The Import button unmounts on success: keyboard focus moves to the result instead.
  useEffect(() => {
    if (done && doneRef.current) doneRef.current.focus();
  }, [done]);
  const field = (key) => (event) => {
    setForm({ ...form, [key]: event.currentTarget.value });
    setDone(null); // the last result is not about the edited form
  };
  const problem = formProblem(form);
  const current = reviewMatches(review, form) ? review : null;
  const fail = (refusal) => {
    if (needsNewReview(refusal)) setReview(null);
    setNotice({ tone: "error", text: importProblem(refusal).text });
  };
  const ask = async (event) => {
    event.preventDefault();
    if (busy || problem) return;
    const body = importBody(form);
    setBusy("review");
    setNotice(null);
    setDone(null);
    setConfirmed(false);
    const { data, problem: refusal } = await send(client, route, body);
    setBusy(null);
    if (refusal) {
      setReview(null);
      return fail(refusal);
    }
    setReview({ body, data });
  };
  const write = async () => {
    if (busy || !current) return;
    const code = runsCode(current.data);
    if (code && !confirmed) return;
    setBusy("import");
    setNotice(null);
    const body = { ...current.body, digest: current.data.sha256, ...(code ? { code_confirmed: true } : {}) };
    const { data, problem: refusal } = await send(client, route, body);
    setBusy(null);
    if (refusal) return fail(refusal);
    setReview(null);
    setConfirmed(false);
    setDone(data);
  };
  const files = current ? reviewFiles(current.data) : [];
  const code = current && runsCode(current.data);
  const link = done && detailRoute(done);
  return html`<${Card} title="Import a version">
    <p class="muted">Copy a registry version directory from anywhere on this machine (another project, a shared folder, a download) into this project's registry. The directory must hold a <code>manifest.yaml</code>; links, hard links and special files are refused. Review lists exactly what will be copied.</p>
    <form class="stack import-form" onSubmit=${ask}>
      <label for="import-path">Version directory (absolute path)</label>
      <input id="import-path" autocomplete="off" spellcheck="false" placeholder="/path/to/registry/prompt/name/1.0.0" value=${form.path} onInput=${field("path")} />
      <div class="row">
        <div class="stack">
          <label for="import-name">Import as name <span class="muted small">(optional)</span></label>
          <input id="import-name" autocomplete="off" spellcheck="false" placeholder="as in its manifest" maxlength="63" value=${form.name} onInput=${field("name")} />
        </div>
        <div class="stack">
          <label for="import-version">Import as version <span class="muted small">(optional)</span></label>
          <input id="import-version" autocomplete="off" spellcheck="false" placeholder="as in its manifest" maxlength="20" value=${form.version} onInput=${field("version")} />
        </div>
      </div>
      <${Action} type="submit" tone=${current ? "secondary" : "primary"} disabled=${busy ? "Waiting for the console." : problem}>${busy === "review" ? "Reviewing…" : "Review import"}<//>
    </form>
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    ${done
      ? html`<div class="notice ok" role="status" tabindex="-1" ref=${doneRef}>
          <span><${Text} value=${`Imported ${done.ref} into this project's registry. An agent uses it only after its agent.yaml references it and a release passes.`} />
          ${link ? html` <a href=${href(link.pattern, link.params)}>Open ${done.kind}/${done.name}</a>` : null}</span>
        </div>`
      : null}
    ${current
      ? html`<section class="edit-review" aria-label="Review">
          <h3>Review: <${Text} value=${current.data.ref} /></h3>
          <p role="status"><${Text} value=${reviewSummary(current.data)} /></p>
          <dl class="facts">
            <dt>From</dt><dd><code><${Text} value=${current.data.source} /></code></dd>
            <dt>Into</dt><dd><span class="badge">${ROOT_LABELS[current.data.root_kind] || current.data.root_kind}</span> <code><${Text} value=${current.data.path} /></code></dd>
            <dt>Version hash</dt><dd><code><${Text} value=${current.data.sha256} /></code></dd>
          </dl>
          <ul class="files">${files.map((file) => html`<li key=${file.path}><${FileView} file=${file} /></li>`)}</ul>
          ${code
            ? html`<fieldset class="code-confirm">
                <legend>This code runs in agents</legend>
                <p>A ${current.data.kind} version is code. Building and releasing an agent that uses it runs this code as you, with the stack's model key and database. The eval gate is a quality gate, not a security review.</p>
                <label class="check"><input type="checkbox" checked=${confirmed} onChange=${(event) => setConfirmed(event.currentTarget.checked)} /> I reviewed this code and trust it to run in agents</label>
              </fieldset>`
            : html`<p class="muted small">A ${current.data.kind} version steers the agent: review it before an agent uses it.</p>`}
          <${Actions}>
            <button type="button" id="import-write" class="action primary" disabled=${Boolean(busy) || (code && !confirmed)} onClick=${write}>${busy === "import" ? "Importing…" : html`Import as <${Text} value=${current.data.ref} />`}</button>
            <${Action} tone="secondary" onClick=${() => setReview(null)} disabled=${busy ? "Waiting for the console." : false}>Cancel<//>
          <//>
        </section>`
      : review ? html`<p class="muted small">The form changed since the review: review again.</p>` : null}
    <p class="muted small">In a terminal: <code><${Text} value=${terminalCommand(form)} /></code></p>
  <//>`;
}
