// Onboard an agent.yaml alone (#823, M7), on Home. The three-tier confirm: (1) Preview shows what
// `new agent --stack-for` would write and the registry refs the agent uses, from the server's dry
// run; (2) when the project brings code from outside the built-in registry, a "this code runs in
// agents" checkbox; (3) the confirm button writes stack.yaml and adds the project. Any edit to the
// form drops the preview, so what is confirmed is what was previewed (the server also pins the
// write to the previewed agent.yaml digest).

import { Action, Notice, Table, Text, nextId, useConsole } from "./components.js";
import { html, useRef, useState } from "./preact.js";
import {
  PROVIDERS, VERIFIED, codeRefs, confirmProblem, formProblem, previewBody, rootText, shortDigest, writeBody,
} from "../lib/onboard.js";

export function OnboardAgentFile({ busy: outerBusy, onAdded }) {
  const { client, addProject } = useConsole();
  const [form, setForm] = useState({ path: "", name: "", model: VERIFIED, modelId: "" });
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const ids = useRef({ path: nextId("onboard-path"), name: nextId("onboard-name"), model: nextId("onboard-model"), modelId: nextId("onboard-model-id"), code: nextId("onboard-code") }).current;
  const previewHeading = useRef(null);

  const edit = (key) => (event) => {
    setForm({ ...form, [key]: event.currentTarget.value });
    setPreview(null);
    setConfirmed(false);
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

  const showPreview = () => run(async () => {
    const reply = await client.request("/api/projects/stack-for", { body: previewBody(form) });
    setPreview(reply.data);
    setConfirmed(false);
    requestAnimationFrame(() => previewHeading.current?.focus());
  });

  const write = () => run(async () => {
    const added = await addProject(writeBody(form, preview, confirmed));
    onAdded(added);
  });

  const working = outerBusy || busy;
  const problem = formProblem(form);
  const code = codeRefs(preview);
  return html`<form class="stack onboard" onSubmit=${(event) => { event.preventDefault(); if (!problem && !working) showPreview(); }}>
    <h3>An agent.yaml alone</h3>
    <p class="muted">Writes a matching stack.yaml (Postgres and the LiteLLM gateway) beside an agent.yaml you already have, with <code>agentbreeder new agent --stack-for</code>, then adds its directory. Agents with tools, knowledge or MCP servers need a hand-written stack.yaml.</p>
    <label for=${ids.path}>Path to agent.yaml</label>
    <input id=${ids.path} autocomplete="off" spellcheck="false" placeholder="/path/to/agent.yaml" value=${form.path} onInput=${edit("path")} />
    <label for=${ids.name}>The <code>name:</code> in agent.yaml</label>
    <input id=${ids.name} autocomplete="off" spellcheck="false" maxlength="31" placeholder="support" value=${form.name} onInput=${edit("name")} />
    <label for=${ids.model}>Model provider</label>
    <select id=${ids.model} value=${form.model} onChange=${edit("model")}>
      ${PROVIDERS.map((provider) => html`<option value=${provider}>${provider}${provider === VERIFIED ? "" : " (config only)"}</option>`)}
    </select>
    <label for=${ids.modelId}>Model id (optional; OpenRouter needs one)</label>
    <input id=${ids.modelId} autocomplete="off" spellcheck="false" placeholder=${`${form.model}/…`} value=${form.modelId} onInput=${edit("modelId")} />
    <${Action} type="submit" tone="secondary" disabled=${working ? "Working…" : problem}>Preview<//>
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    ${preview ? html`<div class="onboard-preview" role="region" aria-labelledby=${`${ids.path}-preview`}>
      <h4 id=${`${ids.path}-preview`} tabindex="-1" ref=${previewHeading}>Preview: nothing is written yet</h4>
      <p>Writes in <code><${Text} value=${preview.directory} /></code>:</p>
      <ul class="plain">${preview.files.map((file) => html`<li key=${file.path}><code><${Text} value=${file.path} /></code> <span class="muted small" title=${file.sha256}>${shortDigest(file.sha256)}</span></li>`)}</ul>
      <p>Gateway aliases <${Text} value=${preview.aliases.join(", ")} /> call <code><${Text} value=${preview.model_id} /></code>; the stack needs <code><${Text} value=${preview.key} /></code>. The gate refuses deploys after review_by <${Text} value=${preview.review_by} />.</p>
      ${preview.verified ? null : html`<p class="muted"><${Text} value=${preview.model} /> is config-only: it has not been run end to end.</p>`}
      ${preview.warnings.map((warning) => html`<p class="notice warn"><${Text} value=${warning} /></p>`)}
      <${Table} label="What the agent uses" columns=${["Ref", "From", "Code"]}>
        ${preview.refs.map((ref, index) => html`<tr key=${`${index} ${ref.ref}`}>
          <td><code><${Text} value=${ref.ref} /></code></td>
          <td>${rootText(ref.root_kind)}</td>
          <td>${ref.runs_code ? (ref.root_kind === "builtin" ? "Runs (ships with AgentBreeder)" : html`<strong>Runs in agents</strong>`) : "Text"}</td>
        </tr>`)}
      <//>
      ${preview.runs_code ? html`<fieldset class="code-confirm">
        <legend>This code runs in agents</legend>
        <p><${Text} value=${code.map((ref) => ref.ref).join(", ")} /> ${code.length === 1 ? "is" : "are"} not shipped with AgentBreeder. Building and releasing runs ${code.length === 1 ? "it" : "them"} as you, with the stack's model key and database. The eval gate is a quality gate, not a security review.</p>
        <label class="check"><input type="checkbox" id=${ids.code} checked=${confirmed} onChange=${(event) => setConfirmed(event.currentTarget.checked)} /> I trust this code to run in agents</label>
      </fieldset>` : null}
      <${Action} disabled=${working ? "Working…" : confirmProblem(preview, confirmed)} onClick=${write}>Write stack.yaml and add project<//>
    </div>` : null}
  </form>`;
}
