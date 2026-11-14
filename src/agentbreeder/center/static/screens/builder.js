// Build a new agent (#833, M17): a form (name, model, prompt, eval cases) beside a sticky preview
// of exactly what `new agent --from-spec` would write. The three-tier confirm, as onboarding's:
// (1) the server's dry-run preview, every file with its text; (2) a "this code runs in agents"
// checkbox when a picked part brings code from outside the built-in registry; (3) Create. Create
// is on only for a combination run end to end (`combination.verified`), and the server re-checks
// all three on a fresh dry run pinned to the previewed plan digest before it writes. A form
// edit marks the preview out of date rather than hiding it; Create waits for a new preview.
// The pickers offer the `tool/` and `mcp/` versions a new project resolves
// (`GET /api/builder/parts`); the CLI resolves every pick again and refuses what it cannot.

import {
  MAX_CASES, SPEC_CHECKS, buildSpec, createBody, createProblem, formProblem, grantTool, initialCases, initialForm, keepOffered, pickGroups, pickServer, pickTool,
  previewBody, unverifiedText,
} from "../lib/builder.js";
import { CHECKS, parseCases } from "../lib/cases.js";
import { PROVIDERS, VERIFIED, codeRefs, rootText } from "../lib/onboard.js";
import { Action, Card, Notice, Table, Text, nextId, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useEffect, useMemo, useRef, useState } from "../ui/preact.js";
import { CasesBody } from "./cases.js";

const BUILDER_CHECKS = CHECKS.filter((check) => SPEC_CHECKS.includes(check.key));

const versionText = (entry) => `${entry.version} (${rootText(entry.root_kind)})`;

/** The version a picker describes: the picked one, else the newest (labelled as such). */
function describe(group, picked) {
  const entry = group.versions.find((item) => item.ref === picked);
  return entry ? { entry, prefix: "" } : { entry: group.versions[0], prefix: `Newest, ${group.versions[0].version}: ` };
}

/** One select per tool name: Not used, or a version. */
function ToolPicker({ entries, form, onForm }) {
  const groups = pickGroups(entries);
  if (!groups.length) {
    return html`<p class="muted small">No <code>tool/</code> versions were found. None ship with AgentBreeder; publish one with <code>agentbreeder registry publish</code> into a registry on AGENTBREEDER_REGISTRY_PATH, then reload this page.</p>`;
  }
  return html`<div class="picks">${groups.map((group) => {
    const id = `build-tool-${group.name}`;
    const picked = form.tools[group.name] || "";
    const { entry, prefix } = describe(group, picked);
    return html`<div class="pick" key=${group.name}>
      <label for=${id}>Tool <code><${Text} value=${group.name} /></code></label>
      <select id=${id} value=${picked} aria-describedby=${entry.description ? `${id}-about` : null} onChange=${(event) => onForm(pickTool(form, group.name, event.currentTarget.value))}>
        <option value="">Not used</option>
        ${group.versions.map((item) => html`<option value=${item.ref}>${versionText(item)}</option>`)}
      </select>
      ${entry.description ? html`<p class="muted small" id=${`${id}-about`}><${Text} value=${prefix + entry.description} /></p>` : null}
    </div>`;
  })}</div>`;
}

/** One select per MCP server name and, once picked, a checkbox per tool it declares. */
function ServerPicker({ entries, form, onForm }) {
  const groups = pickGroups(entries);
  if (!groups.length) return html`<p class="muted small">No <code>mcp/</code> servers were found.</p>`;
  return html`<div class="picks">${groups.map((group) => {
    const id = `build-mcp-${group.name}`;
    const current = form.mcp[group.name];
    const { entry, prefix } = describe(group, current?.ref);
    const usable = group.versions.some((item) => !item.unusable);
    const notes = [entry.unusable && `${id}-why`, entry.description && `${id}-about`].filter(Boolean).join(" ");
    return html`<div class="pick" key=${group.name}>
      <label for=${id}>MCP server <code><${Text} value=${group.name} /></code></label>
      <select id=${id} value=${current ? current.ref : ""} disabled=${!usable && !current} aria-describedby=${notes || null} onChange=${(event) => onForm(pickServer(form, group.name, event.currentTarget.value, entries))}>
        <option value="">Not used</option>
        ${group.versions.map((item) => html`<option value=${item.ref} disabled=${Boolean(item.unusable)}>${versionText(item)}${item.unusable ? " (cannot be picked)" : ""}</option>`)}
      </select>
      ${entry.unusable ? html`<p class="muted small" id=${`${id}-why`}><${Text} value=${entry.unusable} /></p>` : null}
      ${entry.description ? html`<p class="muted small" id=${`${id}-about`}><${Text} value=${prefix + entry.description} /></p>` : null}
      ${current ? html`<fieldset class="grants">
        <legend>Tools granted to <${Text} value=${group.name} /></legend>
        ${entry.tools.map((tool) => html`<label class="check" key=${tool.name}>
          <input type="checkbox" checked=${current.tools.includes(tool.name)} onChange=${(event) => onForm(grantTool(form, group.name, tool.name, event.currentTarget.checked, entries))} />
          <code><${Text} value=${tool.name} /></code>${tool.effect ? html` <span class="muted small">(${tool.effect})</span>` : null}
        </label>`)}
      </fieldset>` : null}
    </div>`;
  })}</div>`;
}

function Parts({ form, onForm }) {
  const [view, reload] = useView("/api/builder/parts");
  const data = view.data;
  // A pick the newest list no longer offers is dropped (a re-read after "Try again").
  useEffect(() => {
    if (!data) return;
    const kept = keepOffered(form, data);
    if (kept !== form) onForm(kept);
  }, [data]);
  return html`
    <h3>Tools</h3>
    <p class="muted">Tools are code that runs inside the agent. A version is pinned and recorded in agent.lock.</p>
    ${view.error ? html`<p class="notice error" role="alert"><${Text} value=${view.error.message} /> <button type="button" class="action secondary small" onClick=${reload}>Try again</button></p>` : null}
    ${!data ? html`<p class="muted small" role="status">${view.loading ? "Reading the registries…" : ""}</p>` : html`
      <${ToolPicker} entries=${data.tools} form=${form} onForm=${onForm} />
      <h3>MCP servers</h3>
      <p class="muted">The stack runs a picked server behind the relay; the agent can call only the tools granted here.</p>
      <${ServerPicker} entries=${data.mcp} form=${form} onForm=${onForm} />
      ${data.skipped ? html`<p class="muted small">${data.skipped} version${data.skipped === 1 ? " is" : "s are"} not offered: unreadable or under a symlink (<code>agentbreeder registry verify</code> says why).</p>` : null}
      ${data.truncated ? html`<p class="muted small">Only the first 500 versions of each kind are offered.</p>` : null}
    `}`;
}

export function Builder() {
  const { client, addProject, setScope } = useConsole();
  const [form, setForm] = useState(initialForm);
  const [casesText, setCasesText] = useState(initialCases);
  const original = useRef(casesText).current;
  const [preview, setPreview] = useState(null); // the server's answer
  const previewed = useRef(null); // the spec that answer is for: Create sends exactly that
  const latest = useRef(0); // only the newest preview request's answer is shown
  const [stale, setStale] = useState(false);
  const [shown, setShown] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const ids = useRef({
    name: nextId("build-name"), model: nextId("build-model"), modelId: nextId("build-model-id"), prompt: nextId("build-prompt"),
    calls: nextId("build-calls"), threshold: nextId("build-threshold"), retries: nextId("build-retries"), code: nextId("build-code"),
    preview: nextId("build-preview"), file: nextId("build-file"),
  }).current;
  const previewHeading = useRef(null);
  const parsed = useMemo(() => parseCases(casesText), [casesText]);

  const changed = () => {
    latest.current += 1; // an answer still on its way is for the form as it was
    setStale(true);
    setConfirmed(false);
  };
  const edit = (key) => (event) => {
    setForm({ ...form, [key]: event.currentTarget.value });
    changed();
  };
  const editForm = (next) => {
    setForm(next);
    changed();
  };
  const editCases = (text) => {
    setCasesText(text);
    changed();
  };

  const run = async (work) => {
    setBusy(true);
    setNotice(null);
    try {
      await work();
    } catch (error) {
      setNotice({ tone: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  };

  const problem = formProblem(form, casesText);
  const showPreview = () => run(async () => {
    const spec = buildSpec(form, casesText);
    const request = (latest.current += 1);
    const reply = await client.request("/api/projects/builder", { body: previewBody(spec) });
    if (request !== latest.current) return;
    previewed.current = spec;
    setPreview(reply.data);
    setStale(false);
    setShown(0);
    setConfirmed(false);
    requestAnimationFrame(() => previewHeading.current?.focus());
  });
  const create = () => run(async () => {
    const added = await addProject(createBody(previewed.current, preview, confirmed), "/api/projects/builder/create");
    setScope(added.id, "/");
  });

  const code = codeRefs(preview);
  const file = preview ? preview.files[Math.min(shown, preview.files.length - 1)] : null;
  const unverified = unverifiedText(preview, VERIFIED);
  return html`<div class="builder">
    <${Card} title="The agent" busy=${busy}>
      <form class="stack" onSubmit=${(event) => { event.preventDefault(); if (!problem && !busy) showPreview(); }}>
        <p class="muted">Writes a new project to ~/agentbreeder-projects/<em>name</em> with <code>agentbreeder new agent --from-spec</code>: the agent, its prompt and eval cases as registry versions, a stack (Postgres and the LiteLLM gateway) and agent.lock, then adds it here. Preview shows every file first; nothing is written until Create.</p>
        <label for=${ids.name}>Name</label>
        <input id=${ids.name} autocomplete="off" spellcheck="false" maxlength="31" placeholder="helper" value=${form.name} aria-describedby=${`${ids.name}-rule`} onInput=${edit("name")} />
        <p class="muted small" id=${`${ids.name}-rule`}>Lowercase letters, digits and dashes, starting with a letter.</p>
        <label for=${ids.model}>Model provider</label>
        <select id=${ids.model} value=${form.model} onChange=${edit("model")}>
          ${PROVIDERS.map((provider) => html`<option value=${provider}>${provider}${provider === VERIFIED ? "" : " (not run end to end)"}</option>`)}
        </select>
        <label for=${ids.modelId}>Model id (optional; OpenRouter needs one)</label>
        <input id=${ids.modelId} autocomplete="off" spellcheck="false" placeholder=${`${form.model}/…`} value=${form.modelId} onInput=${edit("modelId")} />
        <label for=${ids.prompt}>Prompt (the agent's instructions)</label>
        <textarea id=${ids.prompt} rows="8" placeholder="You are helper, an agent that…" value=${form.prompt} onInput=${edit("prompt")}></textarea>
        <h3>Eval cases</h3>
        <p class="muted">Release runs these and refuses a candidate that fails them. Each case is what a user says and what a correct reply says; a judge model grades the reply against it.</p>
        <${CasesBody} parsed=${parsed} original=${original} onText=${editCases} invalid=${null} fallback=${null} checks=${BUILDER_CHECKS} max=${MAX_CASES} />
        <${Parts} form=${form} onForm=${editForm} />
        <details class="builder-more">
          <summary>Model calls and the eval gate</summary>
          <label for=${ids.calls}>Model calls per turn (1 to 20)</label>
          <input id=${ids.calls} inputmode="numeric" value=${form.maxCalls} onInput=${edit("maxCalls")} />
          <label for=${ids.threshold}>Pass threshold (the share of cases that must pass, 0 to 1)</label>
          <input id=${ids.threshold} inputmode="decimal" value=${form.threshold} onInput=${edit("threshold")} />
          <label for=${ids.retries}>Retries for a failed case (0 to 3)</label>
          <input id=${ids.retries} inputmode="numeric" value=${form.retries} onInput=${edit("retries")} />
        </details>
        <p class="muted small">Knowledge is not on this form yet.</p>
        <${Action} type="submit" tone=${preview && !stale ? "secondary" : "primary"} disabled=${busy ? "Working…" : problem}>Preview<//>
      </form>
    <//>
    <aside class="builder-preview" aria-labelledby=${ids.preview}>
      <h2 id=${ids.preview} tabindex="-1" ref=${previewHeading}>${!preview ? "Preview" : stale ? "Preview (out of date)" : "Preview: nothing is written yet"}</h2>
      <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
      ${!preview
        ? html`<p class="muted">Fill in the form, then Preview: the files appear here exactly as they would be written.</p>`
        : html`
          ${stale ? html`<p class="notice warn" role="status">The form changed since this preview. Preview again before you create.</p>` : null}
          <p>Writes in <code><${Text} value=${preview.directory} /></code>. Gateway aliases <${Text} value=${preview.aliases.join(", ")} /> call <code><${Text} value=${preview.model_id} /></code>; the stack needs <code><${Text} value=${preview.keys.join(", ")} /></code>. The gate refuses deploys after review_by <${Text} value=${preview.review_by} />.</p>
          ${unverified ? html`<p class="notice warn" role="status"><${Text} value=${unverified} /></p>` : html`<p class="muted small">Run end to end: <${Text} value=${preview.combination.parts.join(", ")} />.</p>`}
          <div class="file-tabs" role="group" aria-label="Files">
            ${preview.files.map((entry, index) => html`<button type="button" key=${entry.path} class="file-tab" aria-pressed=${index === shown ? "true" : "false"} aria-controls=${ids.file} onClick=${() => setShown(index)}><${Text} value=${entry.path} /></button>`)}
          </div>
          <pre class="text builder-file" id=${ids.file} tabindex="0" aria-label=${file ? `${file.path}, as written` : "File"}><${Text} value=${file ? file.content : ""} /></pre>
          ${preview.refs.length
            ? html`<${Table} label="Parts the agent uses" columns=${["Ref", "From", "Code"]}>
                ${preview.refs.map((ref, index) => html`<tr key=${`${index} ${ref.ref}`}>
                  <td><code><${Text} value=${ref.ref} /></code></td>
                  <td>${rootText(ref.root_kind)}</td>
                  <td>${ref.runs_code ? (ref.root_kind === "builtin" ? "Runs (ships with AgentBreeder)" : html`<strong>Runs in agents</strong>`) : "Text"}</td>
                </tr>`)}
              <//>`
            : null}
          ${preview.runs_code ? html`<fieldset class="code-confirm">
            <legend>This code runs in agents</legend>
            <p><${Text} value=${code.map((ref) => ref.ref).join(", ")} /> ${code.length === 1 ? "is" : "are"} not shipped with AgentBreeder. Building and releasing runs ${code.length === 1 ? "it" : "them"} as you, with the stack's model key and database. The eval gate is a quality gate, not a security review.</p>
            <label class="check"><input type="checkbox" id=${ids.code} checked=${confirmed} onChange=${(event) => setConfirmed(event.currentTarget.checked)} /> I trust this code to run in agents</label>
          </fieldset>` : null}
          <${Action} disabled=${busy ? "Working…" : createProblem(preview, stale, confirmed)} onClick=${create}>Create agent and add project<//>
        `}
    </aside>
  </div>`;
}

export const BUILDER_ROUTES = [
  { pattern: "/build", title: "Build an agent", navAs: "/", screen: Builder },
];
