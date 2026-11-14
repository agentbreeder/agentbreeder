// Build and release: the eval-gated release, a plain build, and rollback. When the project's latest
// release failed the eval gate, the gate-failed view (#826) follows: the failed cases with edit
// links and the candidate's saved log tail.

import { releasesFor } from "../lib/agents.js";
import { isAgentName } from "../lib/evals.js";
import { isNorthwind } from "../lib/format.js";
import { gateFailedRun } from "../lib/gate.js";
import { Action, Actions, Card, Notice, useConfirm, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useState } from "../ui/preact.js";
import { GateFailed } from "./gate.js";

/** The gate-failed view of the project's own agent (release rows name no agent), or nothing. */
function LastGate({ project }) {
  const [releases] = useView(`/api/projects/${project.id}/releases`, project.id);
  const [agent] = useView(`/api/projects/${project.id}/agent`, project.id);
  const name = releases.data && isAgentName(releases.data.agent) ? releases.data.agent : null;
  const run = name ? gateFailedRun(releasesFor(releases.data, name).entries) : null;
  return run ? html`<${GateFailed} key=${run} project=${project} name=${name} run=${run} agentFile=${agent.data} />` : null;
}

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
    <${LastGate} project=${project} />
  `;
}
