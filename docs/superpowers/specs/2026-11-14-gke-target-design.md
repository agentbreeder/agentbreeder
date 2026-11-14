# Google Cloud through GKE: the Kubernetes target on a real cluster (#908): design

- Status: **r4, reviews converged** (architecture and `/security` lenses, Claude Sonnet; review log in §17). No code in
  this PR. Owner decisions in §15 (§owner-decisions).
- Issue #908, part of epic #770 (production target: Kubernetes with Helm). Depends on #771 (deployer
  behind the lifecycle), #772 (Helm chart for the services), #773 (secrets and network policy), and on
  #909 (agent identity at creation, spec merged in #919) through epic #742.
- Builds on: `src/agentbreeder/kubernetes.py` (the bounded kubectl deployer), `cluster.py` (`cluster
  up|status|down` for `kind`), `promote.py` (push, digest check, gated cluster deploy, cluster evals),
  `operator_config.py` (`KubernetesTarget`, `Environment`, `AgentIdentities`), `deployment.py`
  (`_kubernetes_requirements`, the binding check), `build.py` (`docker_platform`), and
  `docs/command-center.md` §"The Kubernetes target".
- Decision of record (#908, following #783): Google Cloud is **GKE through the existing Kubernetes
  target**. Not Cloud Run.

**Nothing here has run.** Every statement about GKE, Artifact Registry, Workload Identity, Docker
Desktop or Google IAM behaviour below is a design assumption from vendor documentation. Where the design
depends on one it is marked *(to measure)*, and each becomes a measured row in the G4 record (§13) or
the design changes. AgentBreeder does not enforce anything at run time on GKE beyond what it enforces on
`kind`: Kubernetes (NetworkPolicy, Pod Security) and Google IAM enforce; AgentBreeder records and checks
the intended configuration at deploy time. The MCP egress sidecar remains the only runtime enforcement
AgentBreeder ships, and it is refused on the cluster target.

## 1. Problem

The Kubernetes target runs on `kind` only, and the code says so in five places:

1. **Loopback only.** `Kubernetes._check_kubeconfig` refuses a non-loopback API server
   (`KUBERNETES_NOT_LOCAL`) and any user entry other than a static `token` or client certificate
   (`_USER_KEYS`). A GKE kubeconfig from `gcloud container clusters get-credentials` uses the
   `gke-gcloud-auth-plugin` exec plugin and a non-loopback endpoint, so it is refused twice.
2. **A recorded binding with no room for a cloud cluster.** Every later operation (`remove`,
   `disable`, `reconcile`, `logs`, `forward`) re-binds through `authorize(context)` from the
   deployment's recorded `"<context>|<uid>|<namespace>|<kubeconfig>"`, independent of the live operator
   config. A GKE cluster needs more than that to authenticate and to be pinned.
3. **Local registry only.** `promote.registry_image_id` reads the pushed manifest over plain HTTP from
   `localhost`/`127.0.0.1` and raises `REGISTRY_NOT_LOCAL` otherwise. Artifact Registry needs HTTPS and
   a bearer token.
4. **`kind`-shaped cluster setup and services.** `cluster.py` creates the cluster, registry and
   namespace, reads `blocked_cidrs` from Docker networks, and attaches the node to the stack's agents
   network so pods reach services that run in Docker on the laptop. A GKE cluster cannot reach them.
5. **No runtime identity.** Pods run with `automountServiceAccountToken: false` and the namespace's
   `default` service account. The agent principal exists only at deploy time, in the gate.

## 2. Goal and acceptance

Deploy and promote an agent to a GKE cluster through the same gate, approval, audit, kill switch and
`reconcile` as `kind`, with images in Artifact Registry and an optional per-agent Workload Identity
derived from the gate's agent principal.

Acceptance (from #908, made checkable):

- A1. `agentbreeder cluster connect gke` (§5) records an existing GKE Autopilot or Standard cluster in
  the operator config after the grant review; `cluster status` reports it. AgentBreeder's config, state,
  store and audit log hold no credential; tokens exist only in a per-operation temporary directory
  (§5.1). The operator's own gcloud login (under `CLOUDSDK_CONFIG`) is long-lived and outside
  AgentBreeder; the design depends on them (threat T1).
- A2. `promote` pushes the released image to Artifact Registry, reads it back over HTTPS, compares
  digests, and deploys by digest; `deploy --cluster` deploys a promoted manifest; both are gated and
  audited.
- A3. For an agent granted a runtime identity (§7.2), the deployment runs as a Kubernetes service
  account whose name is derived from the gate-resolved agent principal (§7.1). Deploy records the
  resulting Workload Identity principal in the audit event and refuses when it differs from the value
  the operator config pins. That is a deploy-time check of names; Google IAM, not AgentBreeder, decides
  what the principal can do at run time.
- A4. Northwind passes 61/61 on the GKE deployment (`promote` cluster evals); then `remove`, `disable`
  and `reconcile` are verified on the cluster as on `kind`.
- A5. The measured rows of §13 are recorded in `docs/gke.md` with the date, mode, region, versions and
  cost.
- A6. Teardown to zero: no AgentBreeder object in the namespace; no image in the run's repository; the
  cluster, repository, service accounts, the TokenCreator grants and every IAM binding the runbook or an
  admin created for the run's principals are deleted; `gcloud` list commands show none left. The billing
  report is checked at +1 and +2 days (export delay).

## 3. Scope and non-goals

In scope: connecting to an existing GKE cluster; short-lived credentials for kubectl and the registry;
a provider-aware recorded binding; Artifact Registry push and digest verification; GKE network policy
inputs; an optional runtime identity; how the CLI reaches in-cluster services; the recorded G4 run;
docs.

Non-goals: Cloud Run; a hosted control plane; multi-cluster; **creating or deleting clusters,
namespaces, repositories or IAM bindings from AgentBreeder** (the runbook does that with `gcloud`;
AgentBreeder prints and checks); a public endpoint, Ingress or LoadBalancer for agents (access stays
`kubectl port-forward`); image signing or Binary Authorization (reported, not required; T5); egress
allow-lists or FQDN policy (a follow-up, T3); GPU node pools; a Linux operator workstation for the run;
EKS/AKS beyond leaving room in §4.

## 4. Shape: a provider inside the Kubernetes target

`target: kubernetes` stays the only cluster target, so `promote`, `deploy --cluster`, `forward`,
`logs`, `remove`, `disable` and `reconcile` keep one path. What differs per cluster kind moves behind a
provider selected by a new field. A stack still has one cluster: the stack state holds one
`kubernetes` key (`services/engine.py`, `services/operator.py`), so a stack is bound to `kind` or to
GKE, not both.

```yaml
environments:
  production:
    targets: [local, kubernetes]
    kubernetes:
      provider: gke                     # new; omitted means "kind" (today's configs stay valid)
      context: gke                      # fixed context name inside the temporary kubeconfig
      cluster_uid: <kube-system UID>    # as today
      namespace: agentbreeder
      registry: us-central1-docker.pkg.dev/ab-run/agentbreeder
      blocked_cidrs: [ … ]              # §6
      gke:                              # required when provider is gke, refused otherwise
        project: ab-run                 # ^[a-z][a-z0-9-]{4,28}[a-z0-9]$
        project_number: "123456789012"  # ^[0-9]{6,20}$, from the API at connect, never typed
        location: us-central1           # region or zone
        cluster: agentbreeder           # ^[a-z]([a-z0-9-]{0,38}[a-z0-9])?$
        endpoint: 203.0.113.10          # IPv4 of the recorded endpoint (D6); loopback refused
        ca_sha256: <64 hex>             # the cluster CA (§5.2)
        deployer: ab-deployer@ab-run.iam.gserviceaccount.com   # impersonated for kubectl
        pusher: ab-pusher@ab-run.iam.gserviceaccount.com       # impersonated for the registry
        workload_pool: ab-run.svc.id.goog
        arch: amd64                     # amd64 | arm64: the nodes agents run on (D11)
```

**Model change.** `KubernetesTarget.kubeconfig` becomes optional with a cross-field validator: `kind`
requires `kubeconfig` and refuses `gke`; `gke` requires the `gke` block and refuses `kubeconfig`.
`context` stays required (for `gke` it is the fixed name inside the session file).
`Kubernetes.select()` stops reading `target.kubeconfig` directly, and `Kubernetes._binary()` and
`Kubernetes._environment()` (`kubernetes.py`, which today hard-wire `--kubeconfig`/`--context` and drop
only `KUBECONFIG`) take their argv prefix and environment from the provider's session.

For `provider: gke` the `kubeconfig` field is refused (there is no persistent kubeconfig; §5.1). For
`provider: kind` (or none) everything behaves exactly as today, including refusing every non-loopback
server: a missing `provider` never selects the GKE path, and `provider: gke` with a loopback endpoint is
refused, so a binding cannot be mislabelled to dodge a check. Service account emails are validated
against `^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$` on load,
since they reach argv. The other fields: `namespace` as today; `ca_sha256` `^[0-9a-f]{64}$`;
`project_number` `^[0-9]{6,20}$`; `location` `^[a-z]+-[a-z]+[0-9]+(-[a-z])?$`; `workload_pool` must
equal `<project>.svc.id.goog`; `registry` must equal `<region of location>-docker.pkg.dev/<project>/
<repo>` with `<repo>` `^[a-z][a-z0-9-]{0,62}$`; `endpoint` is an IPv4 address that is not loopback,
link-local, unspecified, multicast or the metadata address.

**The provider interface** (`kubernetes_providers.py`, new):

| Method | `kind` (today's code, moved) | `gke` (new) |
|---|---|---|
| `session()` | yields the static kubeconfig path `cluster.py` wrote | yields a per-operation temporary kubeconfig with a fresh token (§5.1) |
| `check(session)` | loopback server, static user keys | recorded endpoint, pinned CA, token-only user (§5.2) |
| `registry()` | HTTP to loopback, no auth | HTTPS to the recorded `*-docker.pkg.dev` repository, bearer token (§8) |
| `blocked_cidrs()` at connect | Docker networks (`cluster.blocked_cidrs`) | cluster ranges plus private ranges (§6) |

The admission refusals (`mcp_access`, runtime enforcement, and #909 D4's `tool_access` once that is
built) stay in `DeploymentService._kubernetes_requirements`; GKE adds its own there (§7). `cluster.py`
keeps `kind`'s `up`; `connect gke`, `status` and `down` dispatch on the provider. CLAUDE.md "never
hard-code cloud provider names": names appear only in the provider registry and the `provider` literal.

**The recorded binding (problem 2).** The deployment record's `context` becomes provider-tagged:
`kind|<context>|<uid>|<namespace>|<kubeconfig>` or `gke|<binding id>`, where the binding ID keys a new
store table `cluster_bindings(id, environment, cluster_uid, namespace, provider, document, digest)`,
unique on (environment, `cluster_uid`, namespace), holding the non-secret `gke` block,
`cluster_uid` and `namespace` as they were at deploy. `authorize(context)` rebuilds the provider from
the record, never from the live operator config, so `remove` or `disable` act on the cluster the
deployment was created in even after the environment changed or disappeared. Existing 4-field records
read as `kind` (a store migration with a test). Changing the cluster under a live deployment keeps the
existing `KUBERNETES_BINDING_CHANGED` refusal.

**Ownership of the refactor.** G0 (§14) is the slice of #771 that introduces the provider interface
and the tagged binding with no behaviour change on `kind`; #908 owns G1–G5 on top of it. If #771 lands
differently, G0 adapts to it.

## 5. Connecting a cluster

`agentbreeder cluster connect gke --project P --location L --cluster C --deployer GSA --pusher GSA
[--namespace N] [--environment E]`. `cluster up` stays `kind`-only. `cluster down` on a GKE binding
refuses while cluster deployments are active (as today), then removes the `kubernetes` block from the
stack state; it never deletes the cluster. `connect` needs `kubectl` and `gcloud` on `PATH` (D5). It
takes the stack write lock; the machine-wide `cluster_lock` exists for the single `kind` cluster and is
not taken for GKE. It:

1. Describes the cluster with the deployer's token and **refuses** unless *(field names to measure)*:
   Workload Identity is on with pool `P.svc.id.goog` and **every** node pool runs
   `workloadMetadataConfig.mode: GKE_METADATA` (otherwise a pod can get the node's token); network
   policy is enforced (Autopilot, or Standard with Dataplane V2 or the network policy add-on); legacy
   ABAC, basic auth and client-certificate issuance are off; legacy metadata endpoints are disabled;
   the endpoint is public with master authorized networks enabled and not containing `0.0.0.0/0`, or
   private (D6); a release channel is set; the cluster DNS provider is kube-dns (D7). It reports, but
   does not require: Shielded Nodes, Binary Authorization, application-layer secrets encryption (D9),
   and NodeLocal DNSCache.
2. Reads `project_number` (`projects.get`), the endpoint and the CA; records `ca_sha256` and the node
   architecture agents will run on (`gke.arch: amd64 | arm64`, from D11's node selector).
3. Opens a session (§5.1), reads the `kube-system` UID, and verifies the namespace exists with the
   `managed-by` and `restricted` labels. It does not create it (that needs cluster-wide `create
   namespaces`); the runbook does, and `connect` refuses an absent or unlabelled namespace.
4. Computes `blocked_cidrs` (§6) and checks the Artifact Registry repository exists in project `P`,
   has immutable tags and a cleanup policy (D8); it does not create or change it.
5. Writes the `kubernetes` block for the grant review exactly as `cluster up` does. The review
   (`services plan`, the console) shows the project and number, endpoint, `ca_sha256`, both service
   accounts and the namespace, next to the output of `gcloud container clusters describe P/L/C
   --format='value(endpoint,masterAuth.clusterCaCertificate)'` hashed the same way, so a person
   compares the pin with a second read before `services up --confirm` accepts it.

### 5.1 Sessions: short-lived tokens, no exec plugin, no persistent file

The kubeconfig keeps the rule of no `exec` and no `auth-provider`. For GKE each AgentBreeder operation
(one `deploy`, `remove`, `promote`, `reconcile`, `logs`, `forward` invocation) opens one session:

- **Mint.** `gcloud auth print-access-token --impersonate-service-account=<deployer>
  --project=<project> --account=<operator account>` read from stdout (a pipe, never argv). The
  credential is the operator's **gcloud user login** (`gcloud auth login`, stored under
  `CLOUDSDK_CONFIG`), not application-default credentials; `connect` records the account and the review
  shows it. The mint's environment is built from an allowlist (`PATH`, `HOME`, `CLOUDSDK_CONFIG`,
  `LANG`, `TMPDIR`), so endpoint overrides, proxies, custom CA bundles and credential-file overrides
  never reach it; a unit test passes hostile `CLOUDSDK_*`, proxy and CA variables and checks the child
  environment. `gcloud` and `kubectl` are resolved to absolute paths at `connect`, recorded, and
  `doctor` reports a change. The operator needs `roles/iam.serviceAccountTokenCreator` on the two
  service accounts only (§9).
- **Write.** `tempfile.mkdtemp` under `<state>/kube-sessions/` (0700) holds `config` (0600, created
  `O_CREAT|O_EXCL|O_NOFOLLOW`) with `user.token`, `ca.crt` (from the recorded CA) and a kubectl cache
  directory. The path is unique per session, so the console's child processes and a terminal run never
  share one.
- **Check, then call.** Each kubectl call re-runs `check()` on the file it is about to use (it is small)
  and also passes `--server=https://<endpoint>`, `--certificate-authority=<session>/ca.crt` and
  `--cache-dir=<session>/cache` on argv from the recorded values, so a file swapped after the check
  cannot redirect the token. kubectl's environment drops `KUBECONFIG`, `KUBECACHEDIR`, `HTTP_PROXY`,
  `HTTPS_PROXY`, `NO_PROXY` (and lowercase forms), `SSL_CERT_FILE`, `SSL_CERT_DIR`, and every
  `CLOUDSDK_*`/`GOOGLE_*` variable.
- **Refresh.** The token is re-minted when it is older than 45 minutes *(lifetime to measure; about
  an hour documented)* before the next kubectl call. Long-running processes (`forward`, `logs
  --follow`, the cluster eval forward in `promote`) are bounded: kubectl's own connection keeps the
  token it started with, so `forward` and `--follow` end at 50 minutes with a message to rerun. A
  `promote` whose cluster evals cross 50 minutes reopens the forward with a fresh session between
  cases (the eval runner already retries a case); a case cut mid-request fails with the retryable
  `KUBERNETES_SESSION_EXPIRED` and `promote` resumes as it does after an interrupt.
- **Remove.** The directory is deleted in `finally`, on SIGINT/SIGTERM handlers, and at exit. A crash
  or SIGKILL can leave one behind, so every session first deletes sibling directories older than 70
  minutes (tokens in them have expired) and `doctor` reports any found. The sweep refuses a
  symlinked `kube-sessions`, `lstat`s each entry, removes only directories the user owns, and deletes
  without following symlinks.
- **Ownership.** The session belongs to the CLI command: the handler opens it and passes it down, so
  kubectl, the gateway port-forward and the push share it. `DeploymentService` builds its `Kubernetes`
  in `__init__` today, so G0 makes the provider session injectable there.
- **Console.** `agentbreeder up` stays loopback and single-user. Its jobs are CLI child processes, so
  they inherit the operator's gcloud login and open their own sessions; a GKE binding lets the console act on
  the cluster with the operator's rights, as a terminal does.

### 5.2 What the checks protect, and what they do not

`check()` accepts the session's kubeconfig only if: context, cluster and user are the fixed ones;
`server` is `https://<recorded endpoint>`; `certificate-authority-data` hashes to `ca_sha256`; the user
has `token` only; there is no `proxy-url`, `tls-server-name` or `insecure-skip-tls-verify`. Then
`authorize` runs unchanged: `kube-system` UID, then namespace labels.

The token goes to whatever answers at the recorded endpoint with a certificate that chains to the
pinned CA. **The CA pin is the only control that keeps the token away from a wrong endpoint**; the
`kube-system` UID check runs after the token was sent and identifies the cluster, it does not protect
the token. The pin's trust comes from one read with the operator's credentials at connect, cross-checked by a
person in the grant review (§5 step 5). A CA rotation fails closed with `KUBERNETES_CA_CHANGED` and
`Next: agentbreeder cluster connect gke … (then review the grant)`. The pin applies to an IP endpoint;
a DNS-based endpoint presents a publicly-chained certificate, so it is not supported in the first
delivery (D6).

**The kill switch when the binding no longer authenticates.** Failing closed is right for deploy, but
`disable` must still have a way to stop agents. If the CA rotated, the endpoint changed, or a grant was
removed, `disable` reports `KUBERNETES_UNREACHABLE` with two paths.

1. Re-run `cluster connect gke` and accept the grant. A re-run for the same `cluster_uid` and namespace
   is an update, not a new binding: after the grant review it rewrites the endpoint, CA pin and service
   accounts of the existing `cluster_bindings` row (keyed by environment, `cluster_uid` and namespace;
   audited as `cluster rebind`). Every active deployment's recorded `gke|<binding id>` stays valid,
   `KUBERNETES_BINDING_CHANGED` (which compares binding IDs) does not fire, and `disable` proceeds.
   A rebind may change only the endpoint, `ca_sha256`, the two service accounts, `blocked_cidrs` and
   `arch`. A change to `project`, `project_number`, `location`, `cluster`, `workload_pool` or `registry`
   is refused with `KUBERNETES_BINDING_CHANGED` (a new binding, a new review and a redeploy). The grant
   review shows an old-versus-new diff, the rebind holds the stack write lock, and its audit event
   carries both row digests. The new endpoint and CA come from the same operator-credentialed describe
   as the first connect (T2).
2. Break-glass: the runbook's `kubectl scale --replicas=0` by label with the operator's own
   credentials, which the next `reconcile` reports as an out-of-band change and audits.

`disable` must record its local blocker before it touches the cluster, so a later deploy is refused
even while the cluster is unreachable (G1 verifies the current order and moves it if needed). On GKE
`disable` and `remove` scale the workload to zero and delete it before revoking the per-deployment
gateway key; a failed revoke (gateway or forward down) is reported, never blocks the stop, and
`reconcile` retries it. §13 measures it.

## 6. Network policy and `blocked_cidrs` on GKE

The agent's NetworkPolicy keeps today's shape: no ingress; DNS to `kube-dns`; egress to `0.0.0.0/0`
except `blocked_cidrs`. NetworkPolicy rules are additive, so the extra allow rules below are separate
egress entries next to that `ipBlock`; `_objects` gains them as list items. For GKE, `connect` builds
`blocked_cidrs` from:

- the cluster's own ranges: every pod range (including additional pod ranges of node pools), the
  service range, the node subnets' primary ranges, the control plane range (private) or the public
  endpoint `/32`;
- the private and special ranges, always: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`,
  `100.64.0.0/10`, `169.254.0.0/16`. On `kind` only Docker ranges mattered; on GKE the VPC, peered
  networks, VPNs and internal load balancers are reachable through those ranges, which is a lateral path
  into a customer network.

`KubernetesTarget.blocked_cidrs` is IPv4-only with at most 32 entries; `connect` refuses a dual-stack
cluster or one whose ranges do not fit, rather than truncating.

Allow rules added for GKE, each its own egress entry:

- **DNS.** `kube-dns` in `kube-system` on 53, as today, and NodeLocal DNSCache when `connect` found it
  enabled *(address `169.254.20.10` and whether the kube-dns rule already covers it, to measure)*.
  DNS is an exfiltration channel; accepted (T3).
- **In-cluster services.** The Helm chart's services (#772) by `podSelector` on their labels and ports,
  not by address. #773 owns the rule; §10 says what #908 needs from it.
- **Metadata server, only with a runtime identity.** `169.254.169.254` port 80 for an agent granted
  `runtime.gcp` (§7.2), nothing for others *(to measure: whether Dataplane V2 or the network policy
  add-on serves Workload Identity on another address, such as `169.254.169.252:988`; `connect` refuses
  a configuration that does not match a measured one)*.

Kubelet readiness probes are expected to bypass NetworkPolicy *(to measure; if not, the pod never
becomes ready and the rule set changes)*. `kubectl port-forward` goes through the API server and kubelet,
as on `kind`.

Egress to public addresses stays open (the model provider), as on `kind`. With a runtime identity this
means a prompt-injected agent holding Google credentials can send data to any public host (T3).

## 7. Agent identity on GKE

### 7.1 From the gate's agent principal to a Workload Identity principal

- **Deploy time:** the gate resolves the agent principal in the configured IdP and evaluates policy.
  Unchanged.
- **Run time, opt-in (§7.2):** the deployment runs as a Kubernetes service account named
  `ab-<agent>-<h>`, where `<h>` is the first 12 hex digits of `sha256(agent name | provider name |
  issuer | principal ID)` of the principal the gate resolved for this deploy. The agent name is in the
  hash, so truncating `<agent>` to keep the name within 63 characters cannot make two agents collide. The Workload Identity principal is

  `principal://iam.googleapis.com/projects/<project_number>/locations/global/workloadIdentityPools/<workload_pool>/subject/ns/<namespace>/sa/ab-<agent>-<h>`

  computed from recorded values, never read from the cluster.

So the Google identity is a function of the gate's identity. A replacement deployment of the same agent
with the same principal keeps the same name and therefore the same IAM bindings (stable grants); an
agent whose principal changes (a new client in #909's realm, a different IdP) gets a new name and
inherits nothing, which keeps #909's "never inherit an old principal" (identity spec §5.3).

Checks at deploy, in `_kubernetes_requirements`, which already runs after the gate
(`_deploy_requirements`); it gains a parameter carrying the gate decision's resolved principal
(provider, issuer, ID). *Assumption:* #909's implementation exposes those fields on the decision; G3
adapts if it does not.

1. The audit `deploy allow` event gains `{"code": "RUNTIME_PRINCIPAL", "message": <principal>}`.
2. If `environments.E.agents.NAME.runtime.gcp_principal` is set (a full `principal://` string; the
   `AgentIdentities` block gains `runtime`), deploy refuses with `RUNTIME_PRINCIPAL_MISMATCH` unless the
   computed principal equals it. This is the pin an IAM admin uses: the operator pastes the value the
   admin granted, so a changed principal stops at deploy instead of running with no or other grants.
   `runtime.gcp: true` requires the pin (`RUNTIME_PRINCIPAL_UNPINNED` otherwise); `identity
   runtime-plan` prints the value to paste.
3. For a deploy with a runtime identity, the cluster is described again and deploy refuses unless every
   node pool still runs `GKE_METADATA` (a pool added after `connect` could hand out the node's token).
4. The `deploy allow` event also records the operator's gcloud account, since Cloud Audit Logs show the
   impersonated deployer.
5. Policy input gains `context.runtime = {"provider": "gke", "principal": <computed>}`. That is the
   **intended** principal, not an attestation of what the pod runs as. This input is new to the PDP
   contract; #909's I-slices or G3 add it to `docs/identity-gate.md`.

The service account object is created with the deployment's other objects over stdin, labelled with
the deployment label and `io.agentbreeder/agent`, and deleted with them (`serviceaccounts` joins
`_KINDS`). Replacing a deployment deletes and recreates an object with the same name; IAM bindings
attach to the principal string, not the object, so they persist. `reconcile` compares the live
Deployment's `serviceAccountName` with the computed one (it is part of the pod spec AgentBreeder wrote,
checked like the image annotation today).

Pod spec changes for GKE: always `nodeSelector: {kubernetes.io/arch: <gke.arch>}` (and, for
`arm64` on Autopilot, the Arm compute class *(selector to measure)*), added by `_objects` in G1; with a
runtime identity, also `serviceAccountName: ab-<agent>-<h>`. `automountServiceAccountToken` stays
`false`, because the token comes from the GKE metadata server *(to measure)*. Without a runtime
identity the pod keeps today's spec: the namespace's `default` service account, no token mounted,
metadata blocked; no service account object is created, so `kind` and GKE stay the same there. Any IAM
grant to `…/ns/<namespace>/sa/default` or a namespace-wide `principalSet` would apply to all of these
agents if the metadata block ever failed, so `connect` and `reconcile` (when D4 allows reading project
IAM) refuse/report such a binding, and `reconcile` reports a live Deployment that names a
`serviceAccountName` the record does not.

Some Google APIs do not accept a federated `principal://` directly and need the Kubernetes service
account to impersonate a Google service account *(to measure for the APIs the run uses)*. The first
delivery supports direct principals only and documents the unsupported case.

### 7.2 Who grants a runtime identity

The operator config, not the agent: `environments.E.agents.NAME.runtime.gcp: true`. `build` copies it
into the generated manifest as `runtime_identity: gcp` (as it copies the identity reference), so it is
inside the manifest digest and the release hash-lock (#763); deploy refuses a manifest that asks for it
when the operator config does not grant it (`RUNTIME_IDENTITY_NOT_GRANTED`), and on `provider: kind`
and `target: local` (`RUNTIME_IDENTITY_UNSUPPORTED`). This is a manifest schema change: JSON Schema, `docs/manifest.md`,
and the cross-repo grep of `agentbreeder-cloud` (§16). `agent.yaml` does not change.

**Trust boundary.** A runtime identity is given to the whole container: the agent's code, its tools, and
anything a prompt injection makes them do. Admins should grant that principal the narrowest roles on
specific resources. `docs/gke.md` says so.

**Who else can use the identity.** Anyone who can create pods in the namespace can name
`ab-<agent>-<h>` as a pod's service account and run as it. The namespace is therefore dedicated to
AgentBreeder, and only the deployer service account may create pods, deployments or service accounts
in it (§9). The deployer is as powerful as the most powerful runtime principal in its namespace.

### 7.3 IAM: printed, never applied

AgentBreeder never calls `setIamPolicy`. New read-only command `agentbreeder identity runtime-plan
AGENT --environment E [--json]` (separate from #909's `identity plan --provider`, whose `--provider`
names an IdP) prints the principal and the `gcloud` commands an admin runs to grant or remove roles for
it. `reconcile` (with D4's optional read grant) reads the **project-level** IAM policy and reports
bindings that name an `ab-*` principal with no active deployment, and bindings outside
`runtime.gcp_roles`. Bindings on individual resources (buckets, secrets) are not read; drift detection
covers project-level bindings only, and the docs say so. It reports; it does not remove (as #909 D5).
`remove` of an agent's last cluster deployment prints the removal commands for any bindings it found.

## 8. Artifact Registry and image architecture

- **Push identity.** A separate pusher service account with `roles/artifactregistry.writer` on the one
  repository, minted like §5.1 when `promote` pushes; the deployer cannot push.
- **Push.** A temporary Docker config directory (0700) containing only `auths`, with `credsStore` and
  `credHelpers` unset, and the active context's metadata copied in so `docker --config <tmp> --context
  <pinned>` reaches the same daemon `docker.authorize()` pinned. `docker login -u oauth2accesstoken
  --password-stdin <host>`, push, then delete the directory in `finally`. The user's
  `~/.docker/config.json` is never changed (no `gcloud auth configure-docker`). *(To measure: Docker
  Desktop keeps the token out of the macOS keychain with that config; if it does not, push is refused
  with `REGISTRY_CREDENTIAL_LEAK` rather than leaving a token behind.)*
- **Digest check.** `registry_image_id` for `gke` fetches `https://<host>/v2/<path>/manifests/<digest>`
  with the bearer token: TLS verified against the system store; no proxy (as today); no redirects
  followed (an opener without the redirect handler, so the token never follows a redirect to another
  host); host equal to the recorded registry host and matching `^[a-z0-9-]+-docker\.pkg\.dev$`; path
  equal to `<project>/<repo>/<agent>`, no `..` or percent-escapes. The same config-digest comparison
  then runs. `REGISTRY_NOT_LOCAL` stays for `kind`.
- **Every cluster deploy, not only `promote`.** `_kubernetes_requirements` for `gke` refuses
  (`IMAGE_NOT_IN_REGISTRY`) a manifest whose image is not `<recorded registry>/<agent>@sha256:<64 hex>`
  with host and path equal to the binding's, so a hand-edited manifest for `deploy --cluster` cannot
  name another repository or a tag. Whether the digest was promoted is the existing audit evidence
  (`PROMOTE_IMAGE`), checked as on `kind`.
- **What the digest proves.** The registry holds the image whose config matches the evaluated image,
  and the cluster runs it by digest. It does not stop another principal with writer rights pushing
  other images; tags are immutable in the repository (D8), and signing is a non-goal (T5).
- **Architecture.** A release built on Apple Silicon is `linux/arm64`; GKE nodes default to `amd64`.
  `promote` already refuses to rebuild (the evaluated image is what runs), and `registry_image_id`
  takes the first image of an index, which can be the wrong platform. `promote` therefore reads the
  image's platform (`build.docker_platform`) and refuses with `IMAGE_PLATFORM_MISMATCH` when the cluster
  binding's recorded node architecture differs, and the index branch selects by platform instead of
  taking the first. How the platforms meet is D11.
- **Pull.** GKE nodes pull with the node service account. The runbook uses a dedicated minimal node
  service account (`logging.logWriter`, `monitoring.metricWriter`, and `artifactregistry.reader` on the
  repository) instead of the default Compute Engine account, which older projects grant Editor
  *(to measure for Autopilot: whether a custom node service account is accepted at cluster creation)*.
- **Retention.** Tagged `<release eval id>` and deployed by digest. `remove` does not delete images:
  the rollback needs the previous one. The cleanup policy keeps the last N (D8).

## 9. Secrets, RBAC and IAM

**Secrets.** The first delivery keeps today's mechanism: an immutable `Secret` per deployment created
over stdin. `_delete` and `inspect` list objects by label, and `kubectl delete -l` resolves the
selector with `list`; `list` on secrets returns their data. So the deployer **can read every Secret in
the namespace**. That is acceptable only because the namespace is dedicated and the deployer already
holds every secret it deploys. Anyone with project-level `container.admin`/Owner, or `get secrets` in
the namespace, can read the stack's provider keys too; etcd is encrypted at rest by Google *(documented;
not measured)* and application-layer encryption with Cloud KMS is reported, not required (D9).
Moving to Secret Manager through the CSI driver is #773's scope; when it lands, the GKE provider uses it
and the deployer loses `list secrets` (D12 decides whether G4 waits for it).

**Logs.** Container output on GKE goes to Cloud Logging by default, a store outside the cluster,
retained after teardown and readable by project roles. `agentbreeder logs` redaction does not apply
there. Agents must not log secrets; `docs/gke.md` says so.

**Kubernetes RBAC for the deployer** (the runbook applies it). `connect` runs a fixed list of `kubectl
auth can-i` probes and refuses on any yes: `create pods` and `get secrets` in other namespaces,
`create clusterrolebindings`, `impersonate users`, `* *` cluster-wide; it also runs `kubectl auth
can-i --list` in the namespace and refuses any rule beyond §9's table, where IAM-granted rules appear as
wildcards *(the listing's shape under IAM roles is to measure)*. Probes cannot prove the absence
of other rights, and GKE rights granted through IAM (`container.developer`, `container.admin`) add to
RBAC, so the runbook grants the deployer only the IAM roles below.

| Scope | Resources | Verbs |
|---|---|---|
| namespace | deployments, services, networkpolicies, secrets, serviceaccounts | create, delete, get, list |
| namespace | replicasets, pods | get, list |
| namespace | pods/log | get |
| namespace | pods/portforward | create |
| cluster | namespaces (`resourceNames`: `kube-system`, the namespace) | get |

**IAM** (granted by the runbook; AgentBreeder grants nothing):

| Principal | Role | On | Why |
|---|---|---|---|
| operator user | `iam.serviceAccountTokenCreator` | deployer SA, pusher SA (not the project) | mint tokens |
| deployer SA | `container.clusterViewer` | project *(to measure: what it exposes cluster-wide)* | authenticate to the cluster; K8s RBAC above grants the rest |
| deployer SA | `roles/browser` (includes `resourcemanager.projects.get`) | project | project number at `connect` |
| deployer SA (optional, D4) | `iam.securityReviewer` or a custom role with `resourcemanager.projects.getIamPolicy` | project | `reconcile` IAM drift; discloses all project bindings |
| pusher SA | `artifactregistry.writer` | the repository | push |
| node SA | as §8 | project / repository | logs, metrics, pull |

The effective blast radius of an operator workstation is: write in the namespace (so act as any runtime
principal there), push to the repository, and read project IAM if D4 grants it.

## 10. Services and the gateway on GKE

On `kind` pods reach services in Docker because the node joins the stack's agents network. On GKE the
services run in the cluster from the Helm chart (#772), and the laptop reaches them only through
bounded port-forwards. What #908 needs from #772/#773, and what changes:

| Need | On `kind` | On GKE |
|---|---|---|
| Model gateway for agents | stack's LiteLLM in Docker, by service name | LiteLLM from the chart, by Service DNS name; `Environment.services` and `gateway` hold cluster DNS URLs |
| Per-deployment gateway key (minted by the CLI) | `gateway_keys.client(operator, gateway)` calls the fixed `gateways.X` URL with the `master_key` secret file | a new `gateways.X.cluster_service: <name>:<port>` (instead of a URL) makes `gateway_keys.client` open a port-forward in the **same session** as the deploy (or the `remove`'s session for revoke) and use `http://127.0.0.1:<ephemeral port>` for that call only |
| Gateway master key | local secret file (`master_key: secret://…`) | a new reference form `kubernetes-secret://<name>/<key>` read in the session from the chart's Secret in the namespace (the deployer can read every Secret there, §9); never written to disk |
| `eval_database` (host-reachable, fixture resets) | Postgres in Docker | the same `cluster_service` form for the database; the eval runner gets a port-forwarded DSN for the eval run; the password via `kubernetes-secret://` |
| `network` field | the stack's agents network | unset; refused with `provider: gke` |
| Service secrets | stack secret files | chart-managed Secrets or #773's store; never in values files |

The GKE environment is a separate environment from the local one, with its own services. The local
`services up` stack and the in-cluster chart are two stacks with two databases; `promote` releases on
local Docker and runs the cluster evals against the cluster's services. #772 must expose: chart values
for these services, stable Service names and labels (for §6's allow rules), an admin Service for the
gateway, and the master key and database password in named Secrets. `gateways` are global in the
operator config, so `gateway_keys.client` gains the deploying environment's session as a parameter and a
`cluster_service` gateway resolves against that environment's binding. A `kubernetes-secret://` value is
read with `kubectl get secret … -o jsonpath` through a pipe (never argv), held in memory only and added to
the redaction set like any other secret; `eval_database`'s pattern (today `^secret://` only) is relaxed
to accept it.

Handling rules for these paths (G1b acceptance):

- **Forwards.** `kubectl port-forward --address 127.0.0.1 svc/<name> 0:<port>`, as `Kubernetes.forward`
  already does: kubectl picks the port and the CLI uses only the port its own child reports, after
  reading the `Forwarding from 127.0.0.1:N` line, and checks the child is alive before each call. The
  CLI never pre-picks a port, so a local squatter cannot receive the master key. The forward is open to
  any local user while it lives; the master key and the database password are its only protection. It
  is closed in `finally` and by the session's signal handlers, the child is killed, never orphaned, and
  `doctor` reports a leftover `kubectl port-forward` whose parent is gone.
- **References.** `cluster_service` is `<name>:<port>` with the DNS-1123 label pattern and a port in
  1–65535; `kubernetes-secret://<name>/<key>` uses the same name pattern and `^[A-Za-z0-9._-]{1,253}$`
  for the key, refusing `/`, `..` and anything else. The namespace always comes from the binding. A
  reference may only name a Secret labelled as the chart's (`app.kubernetes.io/part-of:
  agentbreeder-services`, from #772) and without the `io.agentbreeder/deployment` label, checked before
  the value is read, so it cannot point at an agent's provider keys. Only the one key is read
  (`-o jsonpath={.data.<validated key>}`), never the whole Secret.
- **Values.** Decoded values stay in process memory, are added to the redaction set, and never appear
  in argv, a child environment, the job log, the store, the audit log or the eval record. The eval DSN is
  built in process and passed to the driver as connection parameters. A unit test checks that a failing
  read's error carries no value.
- **Eval database.** Fixture resets run destructive SQL. `eval_database` must name a database used
  only for evals, distinct from the services' database; G1b checks what guard exists for the local
  stack today and applies the same, or adds one, before G4.
- **Agents cannot reach them.** §6's allow rule names the gateway Service and its inference port only.
  Agents get no route to Postgres, and the chart puts each service behind an ingress NetworkPolicy that
  admits agent pods on the inference port only (port-forward goes through the kubelet *(to measure that
  it is unaffected)*). LiteLLM serves admin and inference on one port, so the master key is the only
  barrier between an injected agent and key management; the record says so.

The two new config forms are an operator config schema change (G1b in §14); D13 asks whether to build
them or to run G4 with the gateway outside the cluster. Until #772 and #773 land, G4 cannot run (§14).

## 11. Threat model

| | Threat | Mitigation | Residual |
|---|---|---|---|
| T1 | Operator workstation compromised (gcloud login theft, or a replaced `gcloud`/`kubectl` binary) | Impersonation only, no keys; TokenCreator on two SAs only; namespace-scoped RBAC; allowlisted mint environment; binary paths recorded and checked by `doctor` | Full deployer and pusher rights for the login's lifetime; outside AgentBreeder |
| T2 | Token sent to a rogue endpoint (DNS, routing, swapped file) | IP endpoint, CA pin, argv `--server`/`--certificate-authority`, per-call check, proxy variables dropped | A CA the operator accepted at review |
| T3 | Malicious or injected agent code exfiltrates, with or without Google credentials | Private ranges and metadata blocked by default; runtime identity opt-in by operator; least-privilege bindings | Open public egress and DNS (FQDN policy is a follow-up) |
| T4 | Another namespace user runs a pod as an agent's KSA | Dedicated namespace; only the deployer creates pods/SAs (`connect` checks) | A cluster admin can |
| T5 | Registry poisoning by another writer | Deploy by digest, digest compared with the evaluated image, immutable tags | No signing; Binary Authorization reported only |
| T6 | Node token via metadata (WI misconfigured on a pool) | `connect` refuses unless every pool runs `GKE_METADATA`; metadata blocked unless granted | A pool added after connect, for agents without a runtime identity: only the metadata block guards them; `reconcile` re-checks the cluster description |
| T7 | Stale IAM bindings inherited by a new agent | Principal name derived from the gate principal; `reconcile` reports orphans; `remove` prints removal | Resource-level bindings are not read |
| T8 | Secrets and logs outside AgentBreeder's view | Stdin-only Secrets; no secrets in env; dedicated namespace | Readable by project admins; Cloud Logging retention |
| T9 | Tokens left on disk after a crash | Per-session temp dir, signal cleanup, sweep of dirs older than 70 min, `doctor` | Up to one hour of a valid token in a 0700 directory after SIGKILL; any same-user process can read a live session; an orphaned port-forward until `doctor` finds it |

## 12. The live run (G4) and the runbook

`docs/gke.md` (new, written in G5 from what ran) holds the runbook. The run:

1. The owner creates the project (D1), a budget alert at the cap (D3), the repository (immutable tags,
   cleanup policy), the node, deployer and pusher service accounts, the TokenCreator grants, the
   cluster per D2/D6 in the D3 region, the namespace with its labels, and the RBAC of §9, all with
   `gcloud`/`kubectl` commands from the runbook.
2. `cluster connect gke`, grant review, the Helm services (#772) installed into the namespace.
3. Northwind: release locally (61/61), `promote` to GKE (61/61 on the cluster), the §13 probes,
   `remove`, `disable`, `reconcile`, a replacement promote, and a rollback through failing evals.
4. Teardown (A6): `remove` all; `cluster down`; delete images; then the runbook's delete commands for
   the cluster, repository, IAM bindings (including any an admin added for `ab-*` principals), the
   TokenCreator grants and the service accounts (deployer last); local `kube-sessions` empty; Cloud
   Logging data is left to its retention or deleted with the project. `gcloud` list commands show
   nothing left; the billing report at +1 and +2 days.

## 13. What G4 measures (the record's rows)

| Check | Expected |
|---|---|
| Tokens: state, store, audit, job logs, `ps`, `~/.kube/cache`, `~/.docker`, keychain | none; `kube-sessions` empty after each operation |
| Session directory after SIGKILL during `deploy`; next run | left; swept by the next session; `doctor` reported it |
| Swapped session kubeconfig server mid-operation | kubectl still talks to the recorded endpoint (argv) |
| Edited `ca_sha256`; wrong cluster UID | `KUBERNETES_CA_CHANGED`; `KUBERNETES_CLUSTER_MISMATCH`; nothing changed |
| Agent pod to API endpoint, kubelet, node range, an RFC 1918 VPC address, metadata (no runtime identity) | blocked; reachable from a control pod |
| Agent pod with a runtime identity to metadata | token for `ab-<agent>-<h>` only; no other SA listed or obtainable; no node attributes beyond what WI exposes |
| Metadata addresses used by Workload Identity on this dataplane | recorded (`.254:80`, `.252:988` or other) |
| DNS resolution with and without NodeLocal DNSCache | works with the rules of §6 |
| Readiness probe with no-ingress policy | pod becomes ready |
| Another pod to the agent; privileged pod | blocked; refused by Pod Security admission |
| Deployer `auth can-i`, the fixed probe list of §9 | all no |
| Mint with hostile `CLOUDSDK_*`, proxy and CA variables set | none reach `gcloud`; token minted from the recorded account |
| CA rotated (or endpoint changed), then `disable` | `KUBERNETES_UNREACHABLE`; the reviewed `connect` re-run updates the binding in place and `disable` stops the agent; break-glass scale reported by `reconcile` |
| `deploy --cluster` of a manifest naming another repository or a tag | `IMAGE_NOT_IN_REGISTRY`, nothing created |
| Runtime identity without a pin; node pool without `GKE_METADATA` | `RUNTIME_PRINCIPAL_UNPINNED`; refused at deploy |
| Pushed digest equals registry config digest; deploy by digest; arm64/amd64 per D11 | yes; `IMAGE_PLATFORM_MISMATCH` on a mismatch |
| Registry redirect carrying the bearer token | none followed |
| Re-push of an existing tag | refused by the repository |
| `RUNTIME_PRINCIPAL_MISMATCH` with a wrong pin; `RUNTIME_IDENTITY_NOT_GRANTED` | refused before any object is created |
| Stack secrets in the Deployment spec, env, events, `kubectl describe`, audit, job logs, manifest | none found |
| `remove`, `disable`, `reconcile` (incl. edited `serviceAccountName`) | as on `kind`; drift reported; no labelled object left |
| Pods scheduled on `gke.arch` nodes | `nodeSelector` honoured; arm64 image runs as evaluated (D11) |
| Gateway key mint/revoke and eval DB through session port-forwards | key minted and revoked; fixture reset works; master key never on disk |
| A local listener squatting a port during a gateway forward | does not receive the master key |
| Agent pod to Postgres and to the gateway beyond the inference port; another pod to the gateway | blocked |
| `kubernetes-secret://` naming an agent's Secret or an unlabelled one | refused before reading |
| Eval DSN or password in `ps`, store, audit, eval record, job logs | none found |
| Rebind that changes the project or the registry | `KUBERNETES_BINDING_CHANGED` |
| Pod without a runtime identity tries the metadata server | no token |
| Count of `blocked_cidrs` on the run cluster | within 32; a synthetic cluster over 32 refused in unit tests |
| Autopilot resource adjustment | requests and limits after admission recorded |
| Teardown | A6 |

## 14. Delivery slices (sub-issues of #908)

| Slice | What | Depends on | Verifiable without GCP |
|---|---|---|---|
| G0 | Provider interface and tagged binding (`cluster_bindings`, migration); `kind` unchanged; a sub-issue of #771 | — | yes: existing tests and a `kind` run |
| G1 | `provider: gke` config and validators, `cluster connect gke`, sessions (§5.1), §5.2 checks, `blocked_cidrs`, `doctor` sweep report | G0 | unit tests with recorded API fixtures and a fake `gcloud` |
| G1b | `cluster_service` and `kubernetes-secret://` for gateways and `eval_database` (§10) | G1, #772 | unit tests with a fake forward |
| G2 | Pusher session, temporary Docker config, HTTPS digest check without redirects, platform check | G1 | unit tests; a local TLS registry fixture |
| G3 | Runtime identity: operator grant, manifest field and schema, KSA naming, `RUNTIME_*` checks, policy input, `identity runtime-plan`, `reconcile` IAM read | G1; #909's resolved-principal fields | unit tests |
| G4 | The live run (§12) and the §13 table | G1–G3, #772, #773, D1–D4, D11–D13 | no |
| G5 | `docs/gke.md`, `command-center.md` section, changelog; claims limited to G4's record | G4 | — |

Until G4 has run, docs say "GKE: designed, not run" and the website says nothing (release R, #836).

## 15. Owner decisions {#owner-decisions}

| | Question | Recommendation |
|---|---|---|
| D1 | Project and credentials for the run: a new dedicated GCP project under your billing account, or an existing one? Whose credentials? | New project, deleted after A6. Your gcloud user login impersonating the deployer and pusher SAs; no service account keys anywhere |
| D2 | Cluster mode for the run: Autopilot or Standard? | Autopilot: Workload Identity, node pool metadata mode and network policy enforcement are documented defaults *(to measure)*, and there are no node pools to tear down. Standard later if a pilot needs it |
| D3 | Region and cost cap | `us-central1` *(any Autopilot region works)*. Budget alert at **$25** for the run; expected spend a few dollars for one day *(estimate, to measure)*. A per-cluster management fee may apply unless the account's free-tier credit covers one cluster *(check the account)* |
| D4 | Let `reconcile` read the project IAM policy (discloses every binding in the project to the deployer)? | Yes for the run project (it is dedicated); optional per environment |
| D5 | `gcloud` on `PATH`, or an `agentbreeder[gcp]` extra (`google-auth`, `google-cloud-container`)? | `gcloud` first: no new dependency, as with `kind`/`kubectl`. The extra later if the console needs it |
| D6 | Endpoint: public IP with authorized networks limited to the operator's address, or a private endpoint (needs a VPN/bastion), or the DNS-based endpoint? | Public IP with authorized networks set to the operator's `/32`; the endpoint is in `blocked_cidrs`. When the operator's address changes, kubectl times out and AgentBreeder reports `KUBERNETES_UNREACHABLE` with `Next:` the runbook's authorized-networks update. DNS-based endpoints wait until a pin for a publicly-chained certificate and pod reachability are designed and measured |
| D7 | Cloud DNS for GKE: refuse in the first delivery, or support both DNS rules? | Refuse first; record which provider the D2 mode uses |
| D8 | Artifact Registry policy | Immutable tags; keep the last 5 versions per agent; delete untagged after 7 days |
| D9 | Require Cloud KMS application-layer secrets encryption at `connect`, or report it? | Report for the preview run (KMS keys add cost and a scheduled-deletion delay that conflicts with teardown to zero); require it before any production claim |
| D10 | Runtime identity naming: derived from the gate principal (§7.1), or one fixed name per agent? | Derived from the gate principal: stable across replacements, and no inheritance when the principal changes |
| D11 | Image architecture: (a) GKE Arm nodes so the arm64 image built on a Mac runs as evaluated; (b) build `linux/amd64` for releases bound for the cluster and evaluate that image locally under emulation; (c) multi-platform release images, each platform evaluated | (a) for the run if the D3 region offers Arm on Autopilot *(to measure)*, else (b); (c) later |
| D13 | How the CLI reaches the in-cluster gateway admin API and eval database for G4: build `cluster_service` and `kubernetes-secret://` (G1b), or run the gateway outside the cluster on a host-reachable URL for the first run | Build G1b; the outside-gateway shortcut would make the record test a topology nobody deploys, and would put the master key on the operator's machine with agents reaching the gateway over the internet (a threat row if chosen) |
| D12 | Secrets for G4: run with namespace Secrets (the deployer can read them, §9), or wait for #773's Secret Manager CSI? | Namespace Secrets for the preview run, stated in the record; #773 before any non-preview claim |

## 16. Cross-repo split and files

- **OSS (this repo):** everything above. `ARCHITECTURE.md`'s "one certified production target" stays
  unclaimed until #774 and G4 both pass. Touched in G0–G5: `kubernetes.py`, `kubernetes_providers.py`
  (new), `cluster.py`, `promote.py`, `operator_config.py`, `deployment.py`, `store.py` (migration),
  `build.py` (runtime identity copy), `gateway_keys.py` and `evals.py` (G1b), `manifest.py` and its JSON Schema, `commands/cluster.py`,
  `commands/identity.py`, `doctor.py`, `docs/manifest.md`, `docs/command-center.md`, `docs/gke.md`
  (new), `docs/commands/cluster.md`, `ARCHITECTURE.md` (only after G4).
- **Website:** nothing until release R (#836); then only G4's record, if it ran.
- **Cloud (`agentbreeder-cloud`):** grep for the manifest fields and operator config shape when G1 and
  G3 merge; expected no code (it pins no OSS version and the console API is private). A `CLAUDE.md`
  note per side when G1 merges: OSS gains `kubernetes.provider`, `kubernetes.gke`,
  `agents.NAME.runtime` and the manifest's `runtime_identity`; Cloud records it.

## 17. Review log

| Round | Reviewer | Lens | Head reviewed | Findings | Outcome |
|---|---|---|---|---|---|
| 1 | Claude Sonnet | architecture | r1 (uncommitted) | 0 Blocker, 9 Major, 11 Minor, 4 Nit: recorded binding has no room for GKE; per-operation kubeconfig vs `read_private`, refresh, races; `_delete` needs `list` on secrets; A3 mapping a tautology; per-deployment vs per-agent KSA inconsistent; runtime identity authority and schema; services/gateway/eval DB reachability; temporary `DOCKER_CONFIG` loses the context; image architecture; plus admit location, refactor ownership, lock, DNS-endpoint CA, project number, direct-principal APIs, IAM table, CIDR validator, additive rules, PDP input, `--provider` overload, billing delay | Applied in r2 (§2, §4, §5, §5.1, §7, §8, §9, §10, §14–§16) |
| 1 | Claude Sonnet | security | r1 (uncommitted) | 0 Critical, 3 High, 6 Medium, 6 Low, 4 Nit: session-file TOCTOU, caches and env hygiene; what the CA pin protects; metadata server and cross-KSA identity (node pool metadata mode, trust boundary); deployer/pusher privilege; secrets readable and Cloud Logging; registry redirects, path pinning, immutable tags, keychain, node SA; VPC/RFC 1918 egress, NodeLocal DNS, DNS-endpoint reachability; A3 overclaim; unmarked facts; connect refusals; namespace creation; teardown leftovers; env and config validation; console surface; no threat model | Applied in r2 (§2, §5–§13, §15) |
| 2 | Claude Sonnet | architecture | r2 (uncommitted) | 4 Major, 7 Minor, 3 Nit: no recovery when the binding's CA or endpoint changes (kill switch); `KubernetesTarget` model change and `_binary`/`_environment` unspecified; gateway key mint and master key, eval DB unreachable through static URLs; arm64 nodes need a `nodeSelector` and an `arch` field; plus no-identity service account, gate principal into `_kubernetes_requirements`, `target: local` refusal, RBAC resource names and `can-i --list`, `blocked_cidrs` counting, eval forward across token expiry, operator IP change | Applied in r3 (§4, §5.1, §5.2, §7, §9, §10, §13–§16; D13 added) |
| 2 | Claude Sonnet | security | r2 (uncommitted) | 0 Critical/High, 3 Medium, 5 Low, 4 Nit: mint environment is a denylist (now an allowlist, gcloud login not ADC); kill switch can fail closed; `deploy --cluster` image not pinned to the registry; agent name in the KSA hash and a required pin; node pools re-checked at deploy; sweep symlink safety; `can-i` cannot prove absence; config validators; operator account in audit; binary paths | APPROVED; all applied in r3 (§4, §5.1, §5.2, §7.1, §8, §9, §11, §13) |
| 3 | Claude Sonnet | architecture | r3 (uncommitted) | 0 Blocker/Major, 4 Minor, 2 Nit: `gateways` are global (session per environment); `kubernetes-secret://` off argv and `eval_database` pattern; §7.1 numbering; session ownership and injection | APPROVED; applied in r4 (§5.1, §7.1, §10, D13) |
| 3 | Claude Sonnet | security | r3 (uncommitted) | 0 Critical/High, 5 Medium, 3 Low, 5 Nit: master key over a squattable forward port; agents' route to Postgres and the gateway admin; reference validators and scope; eval DSN handling and a dedicated eval database; rebind bounds; `default` KSA IAM; revoke order in the kill switch; orphaned forwards | APPROVED; applied in r4 (§5.2, §7.1, §10, §11, §13, D13) |
