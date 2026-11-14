// A knowledge version's index status (#830, M14 console half), mounted inside the registry's
// version card: `knowledge status --json`, read fresh by the server with the agent's own
// credential. Counts, names and times only; chunk text is never read.

import { STATE_LABELS, stateText, statusFacts, statusPath, statusRefusal } from "../lib/knowledge.js";
import { Action, nextId, Skeleton, Text } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useRef } from "../ui/preact.js";

/** The index part of one knowledge version's card; null for any other kind. */
export function KnowledgeFacts({ data, project }) {
  if (!data || data.kind !== "knowledge" || !project) return null;
  const path = statusPath(project.id, data.ref);
  if (!path) return null; // a ref the server would refuse: no read, no endless skeleton
  return html`<${KnowledgeStatus} key=${data.ref} path=${path} project=${project} versionRef=${data.ref} />`;
}

function KnowledgeStatus({ path, project, versionRef }) {
  const heading = useRef(nextId("index")).current;
  const [view, reload] = useView(path, project.id);
  const answer = view.data && !view.error ? view.data : null;
  const version = answer && answer.versions.find((v) => v.ref === versionRef);
  const refusal = view.error ? statusRefusal(view.error, versionRef) : null;
  return html`<section class="knowledge-status" aria-labelledby=${heading} aria-busy=${view.loading ? "true" : "false"}>
    <h3 id=${heading}>Index status</h3>
    <p class="muted small">This version's documents compared with the table the runtime indexed in the stack's database, read with the agent's own credential. Chunk text is never read. Documents change in Edit below (or <code>agentbreeder registry edit</code>) and are re-indexed after the next <code>release</code>.</p>
    <div class="row">
      <${Action} tone="ghost" small onClick=${reload} disabled=${view.loading ? "Reading the index status." : false}>Refresh</${Action}>
    </div>
    ${view.loading && !view.data && !view.error ? html`<${Skeleton} />` : null}
    ${refusal
      ? html`<div class="notice error" role="status"><span><${Text} value=${`Unavailable: ${refusal.reason}`} />${refusal.next ? html`<br /><span class="small"><${Text} value=${`Next: ${refusal.next}.`} /></span>` : null}</span></div>`
      : null}
    ${version
      ? html`
        <p role="status"><span class=${`index-state ${version.state}`}>${STATE_LABELS[version.state]}</span> <span class="muted small"><${Text} value=${stateText(version)} /></span></p>
        <dl class="facts">${statusFacts(version).map(([label, value]) => html`<div key=${label}><dt>${label}</dt><dd><${Text} value=${value} /></dd></div>`)}</dl>
        <p class="muted small"><${Text} value=${`Database ${answer.database || "-"}, environment ${answer.environment || "-"}, ${answer.credential} credential.`} /></p>
        ${version.notes.map((note, index) => html`<p key=${index} class="muted small">Note: <${Text} value=${note} /></p>`)}`
      : null}
  </section>`;
}
