// A skill version's detail (#1059 UI-R, composition console spec §9.2), mounted inside the
// registry's version card from `registry show --json`'s `skill` object. The CLI's strict loader
// splits the front matter off and cleans every field; the browser parses no YAML. The order is
// the spec's: the description first (it is sent on every model call of every agent that pins the
// version, the stronger injection position), then SKILL.md, references and scripts. Publisher
// Markdown goes through the safe renderer (`ui/markdown.js`), and the source the model reads is
// one click away.

import { codeLabel } from "../lib/registry.js";
import { Text } from "../ui/components.js";
import { Markdown } from "../ui/markdown.js";
import { html, useState } from "../ui/preact.js";

// One choice for the page, kept while the tab is open and never stored: a version switch keeps
// it, a reload starts formatted.
let shownAs = "formatted";

const fileOf = (data, path) => ((data && Array.isArray(data.files) && data.files) || []).find((file) => file && file.path === path) || null;

/** The skill object's fields the screen uses, type-checked; anything else is dropped. */
export function skillView(data) {
  const skill = data && data.skill && typeof data.skill === "object" ? data.skill : null;
  const text = (value) => (typeof value === "string" ? value : "");
  const scripts = skill && Array.isArray(skill.scripts) ? skill.scripts.filter((s) => s && typeof s === "object") : [];
  const references = skill && Array.isArray(skill.references) ? skill.references.filter((r) => typeof r === "string") : [];
  const code = !skill ? "unknown" : scripts.length ? "code" : skill.runs_code === false ? "text" : "unknown";
  return {
    skill,
    error: data && typeof data.skill_error === "string" ? data.skill_error : null,
    description: skill ? text(skill.description) : text(data && data.description),
    license: skill ? text(skill.license) : "",
    compatibility: skill ? text(skill.compatibility) : "",
    body: skill ? text(skill.body) : "",
    references,
    scripts,
    code,
  };
}

function Toggle({ value, onChange }) {
  const option = (key, label) => html`<label class="check"><input type="radio" name="skill-md-view" value=${key} checked=${value === key} onChange=${() => onChange(key)} /> ${label}</label>`;
  return html`<fieldset class="choices">
    <legend>Show SKILL.md as</legend>
    ${option("formatted", "Formatted")}
    ${option("source", "Source (what the model reads)")}
  </fieldset>`;
}

/** One Markdown file in the page's view: formatted, or its source as text. */
function MarkdownFile({ text, view, label, stripped }) {
  if (view === "source") {
    return html`<${Text} block value=${text} label=${label} />
      ${stripped ? html`<p class="muted small">Control or invisible characters removed.</p>` : null}`;
  }
  return html`<${Markdown} source=${text} label=${label} />`;
}

function Reference({ data, path, view }) {
  const file = fileOf(data, path);
  const name = path.replace(/^references\//, "");
  const size = file && Number.isInteger(file.size) ? `${file.size} bytes` : "size unknown";
  let body;
  if (!file || file.binary) body = html`<p class="muted small">Not shown: not readable as text.</p>`;
  else if (file.truncated || file.omitted || typeof file.text !== "string") body = html`<p class="muted small">Not shown: over the 64 KiB cap or the version's text budget.</p>`;
  else if (name.endsWith(".md")) body = html`<${MarkdownFile} text=${file.text} view=${view} label=${path} stripped=${file.stripped} />`;
  else body = html`<${Text} block value=${file.text} label=${path} />`;
  return html`<details>
    <summary><code><${Text} value=${name} /></code> <span class="muted small">${size}</span></summary>
    ${body}
  </details>`;
}

function Script({ data, script }) {
  const source = fileOf(data, typeof script.path === "string" ? script.path : "");
  const parameters = script.parameters && typeof script.parameters === "object" ? JSON.stringify(script.parameters, null, 2) : "";
  return html`<li>
    <h4><code><${Text} value=${String(script.tool || "")} /></code></h4>
    <dl class="facts">
      <dt>Script</dt><dd><code><${Text} value=${String(script.path || script.name || "")} /></code></dd>
      <dt>Description</dt><dd>${script.description ? html`<${Text} value=${String(script.description)} />` : html`<span class="muted">none</span>`}</dd>
      <dt>Parameters</dt><dd>${parameters ? html`<${Text} block value=${parameters} label=${`Parameters of ${script.tool || script.name}`} />` : html`<span class="muted">none</span>`}</dd>
    </dl>
    ${source && typeof source.text === "string"
      ? html`<details><summary>Source</summary><${Text} block value=${source.text} label=${`Source of ${script.path}`} /></details>`
      : html`<p class="muted small">Source not shown here; see Files below.</p>`}
  </li>`;
}

/** The skill parts of one version's card: null for any other kind. */
export function SkillFacts({ data }) {
  const [view, setView] = useState(shownAs);
  if (!data || data.kind !== "skill") return null;
  const skill = skillView(data);
  const choose = (next) => {
    shownAs = next;
    setView(next);
  };
  const md = fileOf(data, "SKILL.md");
  return html`<dl class="facts">
      <dt>Code</dt><dd><${Text} value=${codeLabel({ code: skill.code })} /></dd>
      ${skill.license ? html`<dt>License</dt><dd><${Text} value=${skill.license} /></dd>` : null}
      ${skill.compatibility ? html`<dt>Compatibility</dt><dd><${Text} value=${skill.compatibility} /></dd>` : null}
    </dl>
    ${skill.error ? html`<p class="notice error" role="status"><${Text} value=${`This version breaks the skill rules: ${skill.error}`} /></p>` : null}
    ${skill.description
      ? html`<section class="callout" aria-labelledby="skill-sent-heading">
          <h3 id="skill-sent-heading">Sent on every model call</h3>
          <p>Every agent that pins this version sends this name and description on every model call, before it chooses to load anything.</p>
          <p class="muted small" id="skill-description-label">Description, ${skill.description.length} characters</p>
          <blockquote class="quoted" aria-labelledby="skill-description-label"><${Text} value=${skill.description} /></blockquote>
        </section>`
      : null}
    ${skill.skill
      ? html`<h3>Instructions (<code>SKILL.md</code>)</h3>
        <${Toggle} value=${view} onChange=${choose} />
        <${MarkdownFile} text=${skill.body} view=${view} label="SKILL.md" stripped=${Boolean(md && md.stripped)} />
        ${skill.references.length
          ? html`<h3>References</h3>${skill.references.map((path) => html`<${Reference} key=${path} data=${data} path=${path} view=${view} />`)}`
          : null}
        ${skill.scripts.length
          ? html`<h3>Scripts</h3>
            <p class="notice warn">These scripts are code. Script skills are designed to run each script in a separate container with no network; that is not shipped or verified, and agents built in this console cannot use them.</p>
            <ul class="skill-scripts plain">${skill.scripts.map((script, index) => html`<${Script} key=${index} data=${data} script=${script} />`)}</ul>`
          : null}`
      : html`<p class="muted small">The source is in Files below.</p>`}`;
}

/** What a skill diff says before its hunks (§9.2, CC5): from `registry diff --json`'s `skill`. */
export function SkillDiffNote({ result }) {
  const skill = result && result.skill && typeof result.skill === "object" ? result.skill : null;
  if (!skill) return null;
  return html`${skill.description_changed === true
      ? html`<p class="notice warn">The description changed. It is sent on every model call of every agent that pins this skill.</p>`
      : null}
    ${skill.scripts_changed === true ? html`<p class="notice warn">The scripts changed: this is a code change.</p>` : null}`;
}
