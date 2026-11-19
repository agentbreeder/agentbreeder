// The safe renderer for a skill's Markdown (#1059 UI-R, composition console spec §9.3, CC1):
// `lib/markdown.js`'s token tree to a fixed set of elements with htm. Every piece of publisher
// text goes through <Text> (control, bidi and invisible characters shown as visible escapes),
// and the only attributes set are `href`, `rel`, `target`, `class` and an ordered list's numeric
// `start`, plus the fixed ones <Text> sets itself. There is no HTML string, image or fetch here.

import { parse, plainText } from "../lib/markdown.js";
import { Text } from "./components.js";
import { html, useMemo } from "./preact.js";

function Inline({ nodes }) {
  return (nodes || []).map((node, index) => html`<${InlineNode} key=${index} node=${node} />`);
}

function InlineNode({ node }) {
  switch (node.type) {
    case "text":
      return html`<${Text} value=${node.text} />`;
    case "code":
      return html`<code><${Text} value=${node.text} /></code>`;
    case "em":
      return html`<em><${Inline} nodes=${node.children} /></em>`;
    case "strong":
      return html`<strong><${Inline} nodes=${node.children} /></strong>`;
    case "br":
      return html`<br />`;
    case "link":
      return html`<${Link} node=${node} />`;
    default:
      return null;
  }
}

/**
 * A secure web link opens in a new tab and shows its full destination after its text (cut at
 * 80 characters), so link text that names another host cannot hide where it goes. An autolink's
 * text is that destination already. A cut destination says "full address in Source" in its
 * accessible name, and visibly once per file. A mail link shows its address.
 */
function Link({ node }) {
  if (node.scheme === "mailto") {
    const same = node.autolink || plainText(node.children) === node.shown;
    return html`<a href=${node.href}><${Inline} nodes=${node.children} /></a>${same ? null : html` <span class="muted">(<${Text} value=${node.shown} />)</span>`}`;
  }
  const note = node.cut && node.note ? html`<span>, full address in Source</span>` : null;
  const shown = node.autolink
    ? (node.cut && node.note ? html` <span class="muted">(full address in Source)</span>` : null)
    : html` <span class="muted">(<${Text} value=${node.shown} />${note})</span>`;
  return html`<a href=${node.href} rel="noopener noreferrer" target="_blank"><${Inline} nodes=${node.children} /><span class="visually-hidden">${node.cut ? " (full address in Source)" : ""} (opens in a new tab)</span></a>${shown}`;
}

function Blocks({ blocks }) {
  return (blocks || []).map((block, index) => html`<${Block} key=${index} block=${block} />`);
}

function Block({ block }) {
  switch (block.type) {
    case "heading": {
      const inner = html`<${Inline} nodes=${block.children} />`;
      if (block.level === 4) return html`<h4>${inner}</h4>`;
      if (block.level === 5) return html`<h5>${inner}</h5>`;
      return html`<h6>${inner}</h6>`;
    }
    case "paragraph":
      return html`<p><${Inline} nodes=${block.children} /></p>`;
    case "code":
      return html`<pre class="text"><${Text} value=${block.text} /></pre>`;
    case "literal":
      return block.block ? html`<pre class="text"><${Text} value=${block.text} /></pre>` : html`<p><${Text} value=${block.text} /></p>`;
    case "hr":
      return html`<hr />`;
    case "quote":
      return html`<blockquote><${Blocks} blocks=${block.children} /></blockquote>`;
    case "list": {
      const items = block.items.map((item, index) => html`<li key=${index}><${Blocks} blocks=${item} /></li>`);
      if (!block.ordered) return html`<ul>${items}</ul>`;
      return Number.isSafeInteger(block.start) && block.start !== 1 ? html`<ol start=${String(block.start)}>${items}</ol>` : html`<ol>${items}</ol>`;
    }
    default:
      return null;
  }
}

/**
 * Publisher Markdown, formatted: inside a labelled frame (`div.md`) that says the model reads the
 * source. Over the token cap, the source with the reason instead. `label` names the file.
 */
export function Markdown({ source, label }) {
  const parsed = useMemo(() => parse(source), [source]);
  const frame = `Text from the skill's publisher, shown formatted. The model reads the source.`;
  if (parsed.fallback) {
    return html`<p class="muted small"><${Text} value=${parsed.fallback} /></p>
      <${Text} block value=${source} label=${label} />`;
  }
  if (!parsed.blocks.length) return html`<p class="muted">This file has no body.</p>`;
  return html`<p class="muted small">${frame}</p>
    <div class="md" role="group" tabindex="0" aria-label=${label}><${Blocks} blocks=${parsed.blocks} /></div>`;
}
