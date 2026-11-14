// Deployments: what runs, on which target, with its identity check; remove and reconcile.

import { isCandidate } from "../lib/format.js";
import { href } from "../lib/router.js";
import { canTry } from "../lib/tryit.js";
import { visible } from "../lib/text.js";
import { Action, Actions, Card, Notice, Table, Text, ViewState, useConfirm, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { NextStep } from "../ui/guide.js";
import { LogsLink } from "./logs.js";
import { html, useState } from "../ui/preact.js";

export function Deployments({ project }) {
  const { jobs, busyReason } = useConsole();
  const [view] = useView(`/api/projects/${project.id}/deployments`, project.id);
  const [notice, setNotice] = useState(null);
  const [dialog, confirm] = useConfirm();
  const busy = busyReason(project.id);
  const rows = Array.isArray(view.data) ? view.data : [];
  const start = async (kind, options) => {
    setNotice(null);
    try {
      await jobs.start(project.id, kind, options);
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  const remove = async (row) => {
    if (await confirm({ title: `Remove ${visible(row.name)}?`, text: `Removes the ${visible(row.target)} workload and revokes its gateway key. History and the audit log are kept.`, confirm: "Remove" })) {
      start("remove", { deployment: row.id, target: row.target });
    }
  };
  return html`
    ${dialog}
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <${Card} title="Running" busy=${view.loading}>
      <${Actions} block disabled=${busy}>
      ${rows.length
        ? html`<${Table} label="Deployments" columns=${["Name", "Target", "State", "Identity check", "Endpoint", ""]}>
            ${rows.map((row) => html`<tr key=${row.id}>
              <td><${Text} value=${row.name} />${isCandidate(row) ? html` <span class="badge candidate" title="Removed when its release ends">candidate, temporary</span>` : null}</td>
              <td><${Text} value=${row.target} /></td>
              <td><${Text} value=${row.state} />${row.error ? html`: <${Text} value=${row.error} />` : null}</td>
              <td><${Text} value=${row.compliance || "not checked"} /></td>
              <td>${row.endpoint ? html`<code><${Text} value=${row.endpoint} /></code>` : html`<span class="muted">none</span>`}</td>
              <td class="row-actions">${canTry(row) ? html`<a class="action secondary small" href=${href("/agents/:name/try", { name: row.name })} aria-label=${`Try ${visible(row.name)}`}>Try it</a>` : null}<${LogsLink} kind="deployment" name=${row.id} label=${`Logs of ${visible(row.name)}`} /><${Action} small tone="danger" onClick=${() => remove(row)}>Remove<//></td>
            </tr>`)}
          <//>`
        : null}
      <${ViewState} view=${view} empty=${!rows.length && !view.loading && !view.error ? "Nothing deployed." : null} />
      ${!rows.length && !view.loading && !view.error ? html`<${NextStep} project=${project} />` : null}
      <p class="muted">Local endpoints are unauthenticated. Cluster deployments have no public address: use <code>agentbreeder forward ID</code>.</p>
      <${Action} tone="secondary" onClick=${() => start("reconcile")}>Re-verify (reconcile)<//>
      <//>
    <//>
    <p class="muted small">A deployment needing approval shows its command in the job log. A second person runs it.</p>
  `;
}
