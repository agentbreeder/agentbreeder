// Targets ▸ Kubernetes: the local kind cluster and promoting a release to it.
//
// The console's "Connect" runs `cluster up` without --yes on purpose: attaching the cluster
// changes the stack's grant, and that change is reviewed on Services (`services up` with the
// reviewed digest). Until then `next --json` reports GRANT_PENDING, and this screen shows that
// step instead of offering Deploy to cluster.

import { isCandidate, isNorthwind, toolGuidance } from "../lib/format.js";
import { Action, Card, Notice, Text, ViewState, useConfirm, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useState } from "../ui/preact.js";

export function Kubernetes({ project }) {
  const { jobs, busyReason, navigate } = useConsole();
  const base = `/api/projects/${project.id}`;
  const [cluster] = useView(`${base}/cluster`, project.id);
  const [deployments] = useView(`${base}/deployments`, project.id);
  const [notice, setNotice] = useState(null);
  const [dialog, confirm] = useConfirm();
  const busy = busyReason(project.id);
  const info = cluster.data;
  const rows = Array.isArray(deployments.data) ? deployments.data : [];
  const local = rows.find((row) => row.target === "local" && row.state === "ready" && !isCandidate(row));
  const onCluster = rows.filter((row) => row.target === "kubernetes");
  const tools = info && info.tools ? Object.entries(info.tools) : [];
  const missing = tools.filter(([, tool]) => !tool.present);
  const unsupported = Boolean(info && info.tools && info.tools.kind && info.tools.kind.present && !info.tools.kind.supported);
  const grantPending = Boolean(info && info.cluster && !info.configured);

  const start = async (kind) => {
    setNotice(null);
    try {
      await jobs.start(project.id, kind);
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  const connect = async () => {
    if (await confirm({ title: "Connect a local cluster?", text: "Creates a kind cluster and a registry on 127.0.0.1:5001 and attaches the node to the stack's agents network. You then review the grant change on Services before it applies.", confirm: "Connect", tone: "primary" })) start("cluster-up");
  };
  const remove = async () => {
    if (await confirm({ title: "Delete the local cluster?", text: "Deletes the kind cluster and registry. Refused while something is deployed there.", confirm: "Delete cluster" })) start("cluster-down");
  };

  const connectReason = busy || (!info && "Reading the cluster status…") || (missing.length && `Install ${missing.map(([name]) => name).join(" and ")} first, then restart agentbreeder up.`) || (unsupported && "kind 0.33 or later is required.");
  const deployReason = busy
    || (!info && "Reading the cluster status…")
    || (!info.cluster && "Needs a connected cluster: connect the local cluster first.")
    || (grantPending && "The cluster is not in the stack's accepted grant yet: review the grant change on Services first.")
    || (!local && "Run a release first: only an image that passed the eval gate can be deployed to the cluster.");

  return html`
    ${dialog}
    <p class="muted">Local kind cluster only. Verified there, not on a production cluster. Stack services stay in Docker, and Secrets are unencrypted in kind's etcd.</p>
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <${Card} title="Local cluster" busy=${cluster.loading}>
      <${ViewState} view=${cluster} />
      ${missing.map(([name, tool]) => html`<p class="notice warn"><${Text} value=${toolGuidance(name, tool)} /></p>`)}
      ${unsupported ? html`<p class="notice warn">kind 0.33 or later is required: network policy was measured on that release.</p>` : null}
      ${info ? html`<p>Cluster: ${info.cluster ? "running" : "not created"}. Registry: ${info.registry ? "running" : "not created"}. In the accepted grant: ${info.configured ? "yes" : "no"}.</p>` : null}
      <div class="row">
        <${Action} disabled=${connectReason} onClick=${connect}>Connect local cluster<//>
        ${info && info.cluster ? html`<${Action} tone="danger" disabled=${busy} onClick=${remove}>Delete cluster<//>` : null}
      </div>
    <//>
    ${grantPending
      ? html`<${Card} title="Next: accept the grant change">
          <p>The cluster is attached, but the stack's accepted grant does not include it yet. Review the change on Services and start the services with it (<code>services up</code>); Deploy to cluster unlocks after that.</p>
          <${Action} onClick=${() => navigate("/services")}>Review the grant change<//>
        <//>`
      : null}
    <${Card} title="Deploy this release to the cluster">
      <p>${local ? html`Released locally: <strong><${Text} value=${local.name} /></strong>.` : "Nothing released locally yet."}</p>
      <p class="muted">Pushes the evaluated image, deploys it by digest through the same gate, then runs the eval set on the cluster.${isNorthwind(project) ? " For Northwind: about 4 minutes and about $0.24." : ""}</p>
      <${Action} disabled=${deployReason} onClick=${() => start("promote")}>Deploy to cluster<//>
      ${onCluster.length ? html`<p class="muted">On the cluster: <${Text} value=${onCluster.map((row) => `${row.name} (${row.state})`).join(", ")} />.</p>` : null}
    <//>
  `;
}
