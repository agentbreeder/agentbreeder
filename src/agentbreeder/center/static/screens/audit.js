// Governance ▸ Audit: the deploy audit chain in words, and its latest rows.

import { auditTime, incompleteSentence, reasonCodes, verifySentence } from "../lib/format.js";
import { Card, Table, Text, ViewState } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html } from "../ui/preact.js";

export function Audit({ project }) {
  const [view] = useView(`/api/projects/${project.id}/audit`, project.id);
  if (!view.data) return html`<${Card} title="Audit log"><${ViewState} view=${view} /><//>`;
  const rows = (view.data.rows && Array.isArray(view.data.rows.data) && view.data.rows.data) || [];
  const report = view.data.verify && view.data.verify.data;
  const verifyError = view.data.verify && view.data.verify.error;
  const sentence = verifySentence(report);
  const incomplete = incompleteSentence(report);
  return html`
    <${Card} title="Verify">
      ${report
        ? html`<p class=${`verdict ${sentence.ok ? "ok" : "bad"}`} role="status"><${Text} value=${sentence.text} /></p>
            ${incomplete ? html`<p><${Text} value=${incomplete} /></p>` : null}
            ${report.limitations ? html`<p class="muted small"><${Text} value=${report.limitations} /></p>` : null}`
        : html`<p class="muted"><${Text} value=${(verifyError && verifyError.message) || "Not verified yet."} /></p>`}
    <//>
    <${Card} title="Latest attempts">
      ${view.data.rows && view.data.rows.stale ? html`<p class="muted">Showing last-known data. It refreshes when the running job ends.</p>` : null}
      ${rows.length
        ? html`<${Table} label="Audit rows, newest first" columns=${["Time", "Action", "Decision", "Deployment", "Mode", "Reasons", "Actor"]}>
            ${rows.map((row) => html`<tr key=${row.seq}>
              <td class="nowrap">${auditTime(row.at)}</td>
              <td><${Text} value=${row.action} /></td>
              <td><${Text} value=${row.decision} /></td>
              <td>${row.deployment_id ? html`<code><${Text} value=${String(row.deployment_id).slice(0, 12)} /></code>` : html`<span class="muted">none</span>`}</td>
              <td><${Text} value=${row.enforcement_mode || ""} /></td>
              <td><${Text} value=${reasonCodes(row)} /></td>
              <td><${Text} value=${row.actor || ""} />${row.actor_verified ? "" : html` <span class="muted small">(unverified)</span>`}</td>
            </tr>`)}
          <//>`
        : html`<p class="muted">No audit rows yet. A deploy, release or removal records its attempt here.</p>`}
      <p class="muted small">The chain detects a corrupted or edited row; whoever owns the database can recompute it, so it is evidence, not proof.</p>
    <//>
  `;
}
