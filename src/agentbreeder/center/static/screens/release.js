// Build and release: the eval-gated release, a plain build, and rollback.

import { isNorthwind } from "../lib/format.js";
import { Action, Actions, Card, Notice, useConfirm, useConsole } from "../ui/components.js";
import { html, useState } from "../ui/preact.js";

export function Release({ project }) {
  const { jobs, busyReason } = useConsole();
  const [notice, setNotice] = useState(null);
  const [dialog, confirm] = useConfirm();
  const busy = busyReason(project.id);
  const start = async (kind) => {
    setNotice(null);
    try {
      await jobs.start(project.id, kind);
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  const rollback = async () => {
    if (await confirm({ title: "Roll back local production?", text: "Redeploys the previous local release. Anything already on the cluster keeps its release.", confirm: "Roll back" })) start("rollback");
  };
  return html`
    ${dialog}
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <${Card} title="Release">
      <p>Builds the image, deploys a candidate, runs the eval gate and promotes the exact evaluated image to local Docker only if every gate passes.</p>
      <p class="muted">The candidate is <span class="badge candidate">temporary</span>: the address the log prints for it stops answering when the release ends.</p>
      ${isNorthwind(project) ? html`<p class="muted">For Northwind: 61 evals, about 4 minutes and about $0.24 in model usage (measured).</p>` : html`<p class="muted">Each eval calls the model, so a release costs money in model usage.</p>`}
      <${Actions} disabled=${busy}>
        <${Action} onClick=${() => start("release")}>Run release<//>
        <${Action} tone="secondary" onClick=${() => start("build")}>Build only<//>
        <${Action} tone="secondary" onClick=${rollback}>Roll back<//>
      <//>
    <//>
  `;
}
