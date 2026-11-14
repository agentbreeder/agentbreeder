# Production Kubernetes: the deployer, a Helm chart for the services, secrets and network policy (#770): design

- Status: **r6, reviews converged** (architecture and `/security` lenses, Claude Sonnet; review log in §16). No
  code in this PR. Owner decisions in §14.
- Epic #770 (production target: Kubernetes with Helm), covering #771 (deployer behind the lifecycle),
  #772 (Helm chart for the services), #773 (secrets and network policy) and #774 (Northwind and an
  imported agent on a real cluster).
- A **delta spec**: it states only what changes beyond the local `kind` target that ships in
  `3.0.0a2` (#783). Read first: `src/agentbreeder/kubernetes.py`, `cluster.py`, `promote.py`,
  `operator_config.py` (`KubernetesTarget`, `Environment`, `Gateway`, `McpRelay`), `deployment.py`
  (`_kubernetes_requirements`, the binding check, `disable`), `services/` (the stack plan, the Compose
  renderer and `engine.py`'s init/bootstrap steps), `docs/command-center.md` §"The Kubernetes target"
  and `docs/services.md`.
- Sibling spec: #908 GKE (`docs/superpowers/specs/2026-11-14-gke-target-design.md`, PR #946). That
  spec adds a cloud provider on top of this one. Where the two overlap this spec owns the
  provider-neutral part and §12 lists every hand-over.

**Nothing here has run.** Statements about Helm, CNI plugins, admission policies, the Secrets Store CSI
driver or a managed cluster's behaviour are design assumptions from upstream documentation, marked
*(to measure)* where the design depends on them; each becomes a row of the #774 record (§11) or the
design changes. AgentBreeder enforces nothing at run time on a cluster: Kubernetes RBAC, admission
(including the policy of §7.2, which the runbook installs) and the CNI's NetworkPolicy enforce;
AgentBreeder writes the intended configuration at deploy time and checks it at `connect`, deploy and
`reconcile`. The MCP egress sidecar stays the only runtime enforcement AgentBreeder ships, and it stays
refused on the cluster target.

## 1. Problem: what `kind` covers, and what a real cluster needs

| Area | Local `kind` today | A customer cluster needs |
|---|---|---|
| Reaching the API server | static kubeconfig file, loopback server only (`KUBERNETES_NOT_LOCAL`), no exec plugin | a non-loopback endpoint, short-lived credentials, a pinned CA |
| Recorded binding | `"<context>\|<uid>\|<namespace>\|<kubeconfig>"`, re-read by `authorize(context)` | room for a provider and its non-secret pins (#908 §4) |
| Services | stay in Docker; the `kind` node joins the stack's agents network; pods use Docker service names | run in the cluster, installed and upgraded as a unit: the Helm chart (#772) |
| Images | unauthenticated `localhost:5001` registry over HTTP | an HTTPS registry with authentication for push, and pull credentials for nodes |
| Secrets | an immutable per-deployment `Secret` over stdin; the deployer can `list` every Secret, and could mount any Secret into a pod it creates | a deployer bounded by the cluster, not by the CLI: no Secret read verb, a pod template fixed by the cluster, mounts limited to the Deployment's own Secret and granted bindings (§7.2 states what a deployer token can still reach) |
| Network policy | no ingress; DNS; `0.0.0.0/0` minus Docker ranges | the same, plus allow rules to in-cluster services by label, service-side ingress policies, enforcement measured rather than assumed |
| Operator-only access (gateway admin, eval DB, console role) | host ports on the laptop | bounded `kubectl port-forward` (#908 §10, G1b) under a separate identity |
| Exit evidence | Northwind 61/61 on `kind`, 2026-11-10 | Northwind and an imported agent on a real cluster, with `remove`, `disable`, `reconcile` |

## 2. Goal and acceptance

Deploy to a customer Kubernetes cluster through the same gate, approvals, audit, kill switch and
`reconcile` as local, with the stack's services in the same cluster from a Helm chart, a deploying
identity that cannot read Secrets and whose pods, Services and NetworkPolicies the cluster restricts to
fixed shapes, and network policy derived from the local agents network.

Acceptance (from #770, made checkable). Each holds **only on a cluster where `connect` verified the
§7.2 admission policy**; without it AgentBreeder refuses to connect, because the RBAC split alone does
not stop a deployer-token holder from mounting any Secret into a pod it creates.

- **P-A1 (#771).** `agentbreeder cluster connect token` (§4.2) records an existing conformant cluster
  (Kubernetes ≥ 1.30) after the grant review; `deploy --cluster`, `promote`, `status`, `logs`,
  `forward`, `remove`, `disable`, `reconcile` work on it unchanged in shape. AgentBreeder's config,
  state, store and audit hold no cluster credential (the operator's own mint kubeconfig and the
  registry credential file are referenced by path, as secret files are today).
- **P-A2 (#771, #773).** With the §7.2 policy in place, the deployer identity has no Secret read verb,
  and the cluster rejects (server-side dry-run probes at `connect`, rows in §11) any Deployment,
  Service, NetworkPolicy or Secret it creates, changes or deletes outside the fixed shapes of §7.2: a pod
  differs from AgentBreeder's own template: a pod mounting a Secret other than its own Deployment's or a
  binding granted to the agent the pod names, another service account, a field or volume type outside
  the template, an image outside that agent's repository by digest, a label outside the fixed set, a
  widened NetworkPolicy, a non-ClusterIP Service, or any update or any delete of a non-deployment object.
  **Not** claimed: the agent name is a label the deployer writes, and environment variables and its own
  Secret's files are the deployer's choice, so a deployer-token holder can run code of its choosing inside
  any granted agent's container, with that agent's grants and binding values; §7.2's residual lists
  exactly what a deployer token can and cannot do.
- **P-A3 (#772).** `agentbreeder services render --cluster` produces a Helm chart from the locked
  `stack.yaml` (§5); `services up --cluster` installs it in dependency order with the init and
  bootstrap steps the local engine runs; every Pod is admitted under the namespace's
  `pod-security.kubernetes.io/enforce: restricted` label (enforce, not warn or audit); no
  secret value appears in the chart, its values, the Helm release record, argv or the laptop's disk.
- **P-A4 (#773).** AgentBreeder writes NetworkPolicy objects under which agent pods reach the
  in-cluster services their operator grant names, on those services' ports, and nothing else in the
  cluster; every chart service has ingress and egress policies; the CNI's enforcement of the rule
  shapes used (ingress deny, egress deny, `ipBlock` `except`, `podSelector` union) is measured on the
  cluster by the canary (§6.5). AgentBreeder does not enforce these at run time; the CNI does.
- **P-A5 (#773).** An agent secret can come from an external store through the Secrets Store CSI driver
  (§6.2), with the agent reading it through the same `<ENV>_FILE` variable as today.
- **P-A6 (#774).** Northwind passes 61/61 on the real cluster through `promote`, an imported agent (§11)
  deploys and answers, and `remove`, `disable` and `reconcile` are verified there. The §11 rows are
  recorded in `docs/kubernetes.md` with the date, cluster, versions and cost.
- **P-A7.** Teardown to zero: no AgentBreeder object, release, PVC, PV or Secret left in the namespace;
  service account tokens invalidated by deleting the service accounts; images deleted per D9.

## 3. Scope and non-goals

In scope: the provider interface and a provider-neutral `token` provider for an existing cluster;
identities, RBAC and a required admission policy; Secret hardening; a Helm chart rendered from
`stack.yaml` and a staged install path; secret delivery classes including an external store through
CSI; NetworkPolicy derivation for agents and services with an enforcement canary; registry
authentication for a generic HTTPS registry; the #774 run and its record.

Non-goals: creating or deleting clusters, namespaces, cluster-wide RBAC, admission policies or CNI from
AgentBreeder (the runbook does that; AgentBreeder prints, checks and probes, as in #908); a durable
server-side executor or multi-user control plane (`ARCHITECTURE.md` "Next implementation" stays open);
high availability or backup of the in-cluster Postgres (single replica; D4 offers an external database);
Ingress, LoadBalancer or any public endpoint for agents or services; publishing the chart (D2); a
Kubernetes operator/CRD; egress FQDN allow-lists and exfiltration controls (T11); multi-cluster; the MCP
relay on the cluster (§5.6, D6); runtime identity (cloud-specific, #908 §7); in-cluster TLS between
agents and services (T13).

## 4. The deployer on a real cluster (#771)

### 4.1 The provider interface and tagged binding (K0)

K0 is #908's G0, owned here: `kubernetes_providers.py` with `session()`, `check(session)`,
`registry()` and `blocked_cidrs()` per provider; `KubernetesTarget.provider` (omitted = `kind`); the
deployment record's `context` becomes `kind|<context>|<uid>|<namespace>|<kubeconfig>` or
`<provider>|<binding id>` keyed into a `cluster_bindings` table (unique on environment, cluster UID,
namespace), with a store migration that reads 4-field records as `kind`. `authorize(context)` rebuilds
the provider from the record, never from the live operator config, and checks the row's digest against
the one in the deployment's audit event, so an edited `cluster_bindings` row (a new server or CA pin)
is refused with `KUBERNETES_BINDING_CHANGED` rather than receiving a token (T12). `DeploymentService`
takes an injectable provider session. `kind` behaviour is byte-identical in K0 (the existing tests and the
help goldens pass unchanged); the deployer changes of §4.3 come later (K2, K8) and apply to `kind` too. #908 §4 has the full shape.

### 4.2 The `token` provider: any conformant cluster

A provider for a cluster AgentBreeder has no cloud API for (EKS, AKS, on-premises, k3s), so the target is
not GKE-only and no cloud name is hard-coded (CLAUDE.md rule 3):

```yaml
kubernetes:
  provider: token
  context: cluster                      # fixed name inside the session kubeconfig
  cluster_uid: <kube-system UID>
  namespace: agentbreeder
  registry: registry.example.com/agentbreeder
  blocked_cidrs: [ … ]                  # §6.4, computed at connect
  token:
    server: https://203.0.113.20:6443   # IP or DNS name; loopback, link-local, unspecified, metadata refused
    tls_server_name: kubernetes.default # optional; passed as --tls-server-name when server is an IP
    ca_pem: |                           # the reviewed cluster CA bundle, non-secret, kept in the binding
      -----BEGIN CERTIFICATE----- …
    ca_sha256: <64 hex>                 # of ca_pem; checked on every use
    mint_kubeconfig: /abs/path          # a dedicated minimal file (below), never a shared ~/.kube/config
    mint_kubeconfig_sha256: <64 hex>
    mint_exec_sha256: <64 hex>          # of the exec plugin binary, when the file has one
    service_accounts: {deployer: ab-deployer, installer: ab-installer, operator: ab-operator, canary: ab-canary}
    token_minutes: 30                   # 10..60
    registry_credential: secret://registry-push    # §8
    registry_ca_sha256: <64 hex>        # optional; else the system trust store
    pull_secret: ab-pull                # optional, runbook-created (§8)
  tools: {kubectl: /abs/kubectl, helm: /abs/helm}  # resolved and hashed at connect; re-hashed before each exec
```

**Mint.** The only call that runs the operator's own credentials:
`kubectl --kubeconfig <session>/mint --context <mint context> --namespace <ns> create token <sa>
--duration <token_minutes>m`, stdout to a pipe.

- The mint file is **dedicated** (the runbook writes it with one cluster, one context, one user), so a
  tool that rewrites `~/.kube/config` does not break the pin. At each mint AgentBreeder copies it into
  the session directory, hashes the copy against `mint_kubeconfig_sha256` and runs kubectl on the copy
  (no check/use race).
- The file may not reference other files (`client-certificate`, `client-key`, `tokenFile`,
  `certificate-authority` paths) or use `auth-provider`; data fields only. An `exec` entry must name an
  absolute `command` in a directory, and with parents, that the operator's user cannot write (a
  root-owned install such as `/usr/local/bin`), otherwise `connect` refuses it
  (`MINT_EXEC_WRITABLE`); the binary is hashed against `mint_exec_sha256` before each mint; `PATH` is not
  consulted. Its `env` entries come from the pinned file. The hash covers that binary only, not a
  wrapper's interpreter, its libraries or its configuration under the real `HOME` (exec plugins need
  it), so a compromised user account can still change what a plugin does (T1).
- The mint file's cluster `server` and CA must equal `token.server` and `ca_pem`, and after the first
  call with the minted token the `kube-system` UID must equal `cluster_uid`, so a token from cluster A is
  never sent to B.
- The mint environment is an allowlist (`PATH` set to the tool directories only, `HOME`, `LANG`,
  `TMPDIR`) plus the pinned exec `env`; proxies come only from the file's `proxy-url` (L3); every
  `KUBECTL_*`, `KUBECONFIG`, `KUBECACHEDIR`, `*_PROXY`, `SSL_CERT_*` variable is dropped. A unit test
  passes hostile values and checks the child environment.
- Which service account is minted depends on the command: `deployer` for every lifecycle call,
  `installer` for `connect` and `services up/down --cluster`, `canary` for the canary, `operator` for
  the operator-only consumers of §6.1 (gateway key mint, renewal and revoke in `deploy`, `remove`,
  `disable` and `reconcile`; eval and console databases). The operator's identity needs
  only `create serviceaccounts/token` on those four accounts (§7.1). Whoever holds the mint file can mint
  any of them; the split bounds what one leaked session token can do, not what a compromised operator
  account can (T1).

**Use.** Every other kubectl and helm call runs with a per-operation session directory as #908 §5.1
(0700; config `O_CREAT|O_EXCL|O_NOFOLLOW` holding the token and `ca_pem`; `--server`,
`--certificate-authority` and `--tls-server-name` on argv from recorded values; proxies, `KUBECONFIG`,
`KUBECACHEDIR`, `KUBECTL_*`, `HELM_*` dropped, with session-local Helm cache/config/data directories so no
plugin or repository config is read; removed in `finally` and on signals; directories older than 70
minutes swept; `doctor` reports leftovers). The token is a bound service account token whose expiry
the API server enforces *(to measure: `--service-account-max-token-expiration` may shorten it; `connect`
records the observed expiry)*. Tokens cannot be revoked individually; the incident step is deleting and
recreating the service account (runbook, T1).

**Endpoints.** A DNS `server` is allowed because the pin is the cluster's own CA. A server whose
certificate chains to a public CA is refused at `connect` (`KUBERNETES_CA_PUBLIC`): a pin on a public CA
would accept any host it ever signed.

**Long-running calls.** `forward`, `logs --follow` and the cluster eval forward end at `token_minutes −
5` with the rerun message, and `promote` reopens between eval cases (#908 §5.1).

`cluster connect token --server … --mint-kubeconfig … --pod-cidr C… --service-cidr C [--node-cidr C…]
[--node-local-dns ADDR] [--namespace N] [--environment E]` cross-checks the CA in the mint file with the
chain the server presents, records `ca_pem`/`ca_sha256` and the tool paths and hashes, then runs the
steps of §7.4 (reads, `blocked_cidrs`, the `echo` mirror, `ab-baseline` and the initial `ab-grants`, the
paired policy probes, the RBAC probes, the canary), each under the identity §7.4 names, and writes the
`kubernetes` block for the grant review. The review shows server, CA hash, service
accounts and the policy's digest, next to commands that print the CA hash a second way (`openssl
s_client … | openssl x509 -fingerprint -sha256`), so a person compares the pin before `services up
--confirm` accepts it.

The `kind` provider keeps its static kubeconfig and loopback rule. A `token` binding with a loopback
server is refused, so a binding cannot be relabelled to skip the checks.

### 4.3 Deployer behaviour changes on every provider

These apply to `kind` too (K2 makes `cluster up` write restricted service-account kubeconfigs for
`kind` instead of the admin one, and install the §7.2 policy; that needs a `kind` node image ≥ 1.30), so
the refusals and the RBAC are the same on a laptop and a cluster:

1. **A deployment's Secret is owned by its Deployment.** `create` first creates the Deployment, Service
   and NetworkPolicy, reads the Deployment's UID, then creates the Secret over stdin with an
   `ownerReferences` entry to that Deployment with **`blockOwnerDeletion: true`** (which needs `update`
   on the `deployments/finalizers` subresource, an authorization-only check that grants no write path,
   §7.1). Removal is `kubectl delete deployment,service,networkpolicy -l <deployment label>
   --cascade=foreground`; with `blockOwnerDeletion` the Deployment object stays (with a deletion
   timestamp) until the GC has deleted the Secret, so the name `agentbreeder-<id>` cannot be reused while
   its Secret exists. The deployer needs **no `delete`, `get` or `list` on Secrets**. `_KINDS` loses
   `secret`. A Secret create that fails after the Deployment exists deletes the Deployment (the pod is
   pending on its missing volume until then). §7.2 refuses a Deployment delete whose propagation is not
   `Foreground` (so `--cascade=orphan` or `background`, which would let the Secret outlive its owner and
   be mounted by a recreated Deployment of the same name, cannot be used by the deployer). *(To measure:
   foreground deletion and GC latency on the run cluster; the `OwnerReferencesPermissionEnforcement`
   admission plugin with the `deployments/finalizers` grant.)* An orphan therefore exists only when
   someone else deletes the Deployment with `--cascade=orphan`. K2 changes `_delete` to pass
   `--cascade=foreground`; its existing "nothing left" poll no longer lists Secrets, but the Deployment
   persists until its Secret is gone, so an empty poll still implies the Secret is gone (P-A7's row
   measures it, with the 120 s delete and 90 s poll bounds checked against GC and termination latency). `reconcile` cannot
   list Secrets, so such an orphan is invisible to it (D7). A same-ID retry waits for the previous
   foreground delete to finish (`_delete` already waits), so it does not meet the old Secret. A Secret
   pre-created under another deployment's name makes that deployment's create fail (a denial of service
   by a deployer-token holder, not a disclosure; §7.2's residual).
2. **Bindings and the own Secret in one projected volume** at `MOUNT_ROOT` (§6.1), so neither nests in
   the other; deploy refuses duplicate file paths across the sources (`SECRET_PATH_CONFLICT`). Store
   secrets use separate CSI volumes (§6.2), since CSI volumes cannot be projected sources. *(`defaultMode`
   and `fsGroup` on projected files, to measure.)*
3. **`imagePullSecrets`** exactly `[{name: <pull_secret>}]` when the binding names one (runbook-created;
   §7.2 admits only that name, from `ab-grants`), on agent and chart pods alike; else nodes pull with
   their own identity (§8).
4. **Rollout bounds.** `replicas: 1` stays; no `PodDisruptionBudget` (a single replica with a PDB blocks
   node drains). The record says the agent is not highly available.
5. **The kill switch and the gateway key.** Today `disable` writes its store blocker in a transaction
   before touching any runtime (`deployment.py` `disable`), then per record calls `runtime.authorize`,
   removes, and only then `_revoke_key`, so a failed `authorize` skips the revoke until `reconcile`. K1
   moves the revoke into a `finally` per record. That helps whenever the gateway is reachable but the
   workload step failed: a local gateway, or a cluster whose API answers but whose deployer step failed
   (expired token, failed mint, refused `authorize`). It does **not** help when the cluster's API server
   is unreachable: the in-cluster gateway is reached only through a port-forward, which needs the API
   server, so the revoke is reported as failed and retried by `reconcile`. For that case K5 mints every
   per-deployment key on a cluster with a bounded lifetime (LiteLLM key `duration`, default 30 days;
   *to measure*) besides its budget, and `reconcile` extends the keys of active deployments under the
   operator identity (`/key/update`), so a running agent keeps a valid key as long as `reconcile` runs
   (the runbook schedules it at least weekly) and a key nobody can revoke still expires. A failed
   revoke in the `finally` is reported beside the original error, never in place of it. The record names
   the operator's break-glass (`kubectl scale --replicas=0` with their own credentials).
6. **`reconcile`** additionally compares the pod template's `uses-*` labels with the ones the operator
   config computes now, re-reads the namespace labels and the admission policy's presence and digest
   (§7.2), the chart lock ConfigMap (§5.4) and the `SecretProviderClass` objects (§6.2), and reports
   drift; it never edits them.

### 4.4 Admission refusals in AgentBreeder (`_kubernetes_requirements`)

These are early, readable refusals; the cluster's own refusals are §7.2's. Unchanged: runtime
enforcement (`KUBERNETES_ENFORCEMENT_UNSUPPORTED`) and `mcp_access` (`KUBERNETES_MCP_UNSUPPORTED`, until
D6). New for a non-`kind` provider:

- `IMAGE_NOT_IN_REGISTRY`: the image is not `<binding registry>/<agent>@sha256:<64 hex>` (#908 §8).
- `KUBERNETES_SERVICE_UNKNOWN`: a gateway `agent_url`, an `Environment.services` URL or a binding names an
  in-cluster Service the installed chart does not provide (§6.3).
- `KUBERNETES_CHART_STALE`: the chart lock ConfigMap in the namespace (§5.4) differs from the
  environment's current lock.
- `KUBERNETES_POLICY_MISSING`: the §7.2 policy or its binding is absent or its digest changed since
  `connect` (re-read at every deploy).
- `KUBERNETES_BASELINE_MISSING`: `ab-baseline` is absent or its digest changed (§6.3).
- `KUBERNETES_GRANTS_CHANGED`: the live `ab-grants` differs from the digest the last `services up
  --cluster` recorded (someone other than that run rewrote the grants).
- `SECRET_STORE_UNKNOWN`: a `secret-store://` reference names a `SecretProviderClass` that is absent,
  unlabelled, not granted to this agent, or has `secretObjects` (re-read at every deploy, §6.2).
- `EVAL_DATABASE_SHARED`: a cluster environment's `eval_database` is a database an agent binding also
  uses, without the operator config's explicit `eval_resets: allowed` (§6.1, D13).

## 5. The Helm chart for the services (#772)

### 5.1 Shape: a chart rendered from the locked stack

The local path turns `stack.yaml` + `stack.lock` into a plan (`services/plan.py`) and renders it as a
hardened Compose file (`compose.py`); `engine.py` then starts services in dependency order, running each
service's `init` and `bootstrap` steps after it is healthy and before its dependents. The cluster path
renders the **same plan** as a Helm chart directory and drives the **same order** (§5.4):
`agentbreeder services render --cluster --environment E --out DIR`. One chart per stack and cluster
environment, generated, not hand-written and not published (D2):

```
DIR/
  Chart.yaml        # name: ab-<stack>, version: 0.0.0+<lock digest[:12]>, appVersion = stack version
  values.yaml       # non-secret knobs only (§5.3); per-tier enable flags (§5.4)
  templates/        # per service: StatefulSet (volumes) or Deployment, Service, NetworkPolicy,
                    # ServiceAccount (automount off), ConfigMaps for file params and the service's files/
                    # directory; chart-wide: the lock ConfigMap
  jobs/             # init/bootstrap Jobs: NOT part of the release (§5.4), applied by the installer
  agentbreeder.lock # renderer version, stack.lock digest, every image digest (incl. built project
                    # services), the operator-only Secret names
```

Why rendered from the plan, not a static chart: the stack decides which services exist, their wiring,
roles, files and file params; a static chart would duplicate `plan.py` and drift from Compose. Why Helm:
the issue asks for it, customers install and roll back with it, and `helm template` gives a GitOps
path. Helm 3 (≥ 3.15) is the supported major; Helm 4 renamed `--atomic` *(to measure before supporting
it)*. `helm` is an external tool like `kubectl`, resolved to an absolute path at `connect`; no Python
dependency. `services/**` is lane A's code: K3/K4 are coordinated with lane A.

### 5.2 What it contains

| Stack service | In the chart | Notes |
|---|---|---|
| `service/postgres-pgvector@1.x` | StatefulSet (1 replica, `volumeClaimTemplates`), headless + ClusterIP Service `postgres`, init Job | Same image digest, uid 999; `init.sh` and `init_sql` from ConfigMaps; roles and databases as `plan.py` computes. D4: external managed Postgres instead |
| `service/litellm@1.0.0` | Deployment + Service `litellm` | `/files/start.py` and the reviewed config as ConfigMaps; provider keys from Secrets (§5.3); admin and inference share one port (#908 §10) |
| `service/phoenix@1.0.0` | Deployment + Service `phoenix`, bootstrap Job | Auth on; the bootstrap Job mints the ingest key (§5.4) |
| Project services (`northwind-proxy`, `northwind-ops`) | Deployment + Service each | Built from their Dockerfile for the binding's node architecture (§8), pushed by digest; digests recorded in `agentbreeder.lock` (not `stack.lock`, which stays local) |
| `service/mcp-relay` | not in the first delivery | D6 |
| `service/echo` | Deployment + Service | the canary's server (§6.5) and the chart's own tests |

- **Files.** Each service's `files/` directory and file params become ConfigMaps mounted read-only at
  `/files` with `defaultMode: 0555` for scripts; render refuses a service whose files exceed 900 KiB
  (`CHART_FILES_TOO_LARGE`; the ConfigMap limit is 1 MiB) *(Northwind's largest, `commerce.sql`, to
  measure)*.
- **Images.** Third-party images (Postgres, LiteLLM, Phoenix, echo) are mirrored into the binding's
  registry under `<registry>/mirror/` by `services up --cluster` with the pusher credential (pull by digest locally, push, read the
  pushed manifest back and compare the config digest as `promote` does), so the cluster needs no egress
  to Docker Hub and nodes pull from one registry. Multi-platform indexes are mirrored for the binding's
  architecture only.
- **Names and labels.** Service names equal the stack service names (`postgres`, `litellm`, `phoenix`,
  `proxy`), so the URLs `services up` writes (`http://litellm:4000`, `postgres:5432`) resolve unchanged in
  the namespace and generated manifests do not change between local and cluster. Every object carries
  `app.kubernetes.io/part-of: agentbreeder-services`, `app.kubernetes.io/managed-by: agentbreeder`,
  `io.agentbreeder/stack: <name>` and `io.agentbreeder/service: <service>`; chart pods also carry
  `io.agentbreeder/chart-service: <service>`, the only label chart Services select on and chart policies
  peer with, which §7.2 forbids on any deployer or canary pod. None carries `io.agentbreeder/deployment`,
  so the deployer's deletes never touch a chart object (and §7.2 refuses them anyway).
- **Hardening, every Pod:** `restricted` Pod Security (non-root, `RuntimeDefault` seccomp, no privilege
  escalation, all capabilities dropped, read-only root filesystem with `emptyDir` scratch where the
  image needs it *(to measure per image)*), `automountServiceAccountToken: false`, `enableServiceLinks:
  false`, requests and limits from `service.yaml` (a new optional `resources` field; a `service.yaml`
  schema change, versioned as such), images by digest only. Services are `ClusterIP`; headless only for
  the Postgres StatefulSet; render refuses `NodePort`, `LoadBalancer`, `Ingress` and `externalIPs`
  (`CHART_EXPOSURE_REFUSED`). Egress for services whose `service.yaml` sets `egress: true` (LiteLLM,
  the Northwind proxy) is §6.3's public rule.
- **`expose: host` outputs** (`northwind-ops`, Phoenix's UI) become forward-only through `agentbreeder
  forward --service NAME` under the operator identity. *(K4 checks whether `release` or any of the 61
  cases calls `ops`; the local run says the eval gate does not.)*

### 5.3 Secrets in the chart: references only, values never on the laptop

Helm stores each release's rendered manifests and values in a Secret in the namespace. So no value
enters the chart: templates reference Secrets by name, and the values file holds names, sizes and flags
only. Render scans the chart and values for every known secret value of the environment and refuses
with `CHART_SECRET_LEAK`; a unit test plants one. That scan finds only known values (T5).

**Cluster state is per environment and holds no secret.** The local stack state
(`~/.agentbreeder/stacks/<stack>-<hash>/`, `service-secrets/`) belongs to the local Docker environment.
A cluster environment gets `envs/<env>/cluster/` beside it with non-secret files only (the rendered
chart's lock, the last installed revision). `services up --cluster` generates service secrets
(passwords, the LiteLLM master key, Phoenix admin) **in process**, creates them as Secrets, and never
writes them to disk; on a re-run it reads the existing ones back through the installer (which has `get`
on secrets, §7.1) instead of regenerating. Imported values (provider API keys from `--env-file`) are
read from the operator's file in memory, as locally. So dev and prod never share a generated credential,
and the operator-only values exist only in the cluster.

**Naming and ownership.** One immutable Secret per **resolved value** a consumer needs (a service output
is a template such as `postgresql://{role}:{secret.role}@{address}/{db}`; each resolved value is its
own Secret with one key, `value`), named `ab-svc-<service>-<output path>-g<generation>` (DNS-1123, ≤ 63
characters, a hashed middle part when longer), labelled `part-of: agentbreeder-services` and annotated
`io.agentbreeder/owner: installer` and `io.agentbreeder/value-sha256` (an HMAC of the value under a
per-environment key held in the cluster as `ab-svc-hmac`, so the annotation cannot be used to guess a
weak value). Before using an existing Secret the installer checks the label, the annotation and the
HMAC of its value; a Secret with that name it did not create (pre-created by another identity) is
refused with `CHART_SECRET_FOREIGN` and never consumed (T3). A rotated value gets the next generation;
the chart is upgraded to it and the previous generation is kept until the next successful upgrade, so
`helm rollback` still finds it; older ones are deleted by name only after `services up --cluster` has
checked (the installer reads Deployments) that no agent Deployment still references them, else it
keeps them and prints the agents to redeploy (`SECRET_GENERATION_IN_USE`). A bootstrap value the service
itself mounts restarts that service after its Secret is created, as `engine._steps` does locally.

**Migrating the existing state key.** Today the stack state holds one `kubernetes` key (`engine.py`
`_attach_cluster`, `services/operator.py` copies it into every environment's operator config). K4 moves
it under the environment that `cluster up` granted (`envs/<env>/cluster/`), reads the old key once as that
environment's, and `operator.py` copies a binding only into its own environment. The `--cluster` paths
are a new executor beside the Docker-bound `engine.py`, sharing `plan.py`, not a mode of it.

### 5.4 Install, upgrade, remove: staged, in the engine's order

`agentbreeder services up --cluster --environment E [--yes]`:

1. Takes the stack write lock (#822). Opens a provider session with the **installer** identity, and
   checks `ab-baseline` and the `connect`-written `ab-grants` keys exist (`KUBERNETES_NOT_CONNECTED`
   otherwise; §7.4).
2. Renders (§5.1), runs `helm lint`, shows the grant review: services, images by digest, Secret names,
   PVC sizes, NetworkPolicies, Jobs, and a diff against the installed release (`helm get manifest`,
   which holds no values by §5.3).
3. Mirrors images (§5.2), creates every `ab-svc-*` Secret the next tier's templates reference (§5.3; a
   bootstrap value is created in step 4 before the tier that consumes it), then installs **tier by
   tier**, a tier being the services whose upstreams are
   all ready, the same order `engine._start` uses: `helm upgrade --install ab-<stack> DIR` with the
   enable flags of this tier **and every earlier one** (flags accumulate, so a later upgrade never removes
   an earlier tier), `--wait --timeout 10m`, no Helm hooks. Each tier is one release revision, so `helm
   rollback` undoes one tier.
4. After a tier is ready, for each of its services with an `init` step: applies the init Job from `jobs/`
   with `kubectl create` (outside the release, so a later upgrade neither recreates nor reruns it), waits
   for it, deletes it. For a `bootstrap` step (Phoenix's ingest key): skips it when the stored marker
   matches (the StatefulSet PVCs' UIDs of the service and its upstreams, mirroring `engine.py`'s
   volume-identity marker, kept in the lock ConfigMap); otherwise creates the Job the same way, reads its
   single stdout line through the installer's `pods/log` into process memory only, deletes the Job at
   once, and creates the Secret (§5.3). The Jobs run with automount off and write nothing to the API.
   Until the Job is deleted the deployer's `pods/log` could read the line too, and the cluster's log
   shipper may keep it *(to measure; the runbook excludes the namespace's Jobs from shipping, or the record
   says the ingest key reached the log store)* (T8).
5. Then the next tier. A failure stops with the release at the last good tier and `Next:` naming
   `services up --cluster` (idempotent) or `helm rollback`; no automatic rollback across tiers.
6. Writes the per-agent keys of `ab-grants` (§7.2; `connect` owns the others) from the operator config's agent grants, then the chart lock ConfigMap
   `ab-chart-lock` (lock digest, renderer version, operator-only Secret names, bootstrap markers, the
   `ab-grants` digest), readable by the deployer, and the environment's operator config fields it owns
   (§5.5). The cluster-side `ab-chart-lock` is authoritative: deploy and `reconcile` compare the live
   `ab-grants` digest with the one in `ab-chart-lock` and the chart lock with the environment's
   (`KUBERNETES_GRANTS_CHANGED`, `KUBERNETES_CHART_STALE`), so a second operator or CI needs no local
   copy. Granting a new agent, or changing a grant, therefore needs a `services up --cluster` run
   (an installer token); there is no narrower writer in the first delivery.

`services status --cluster` reads pod readiness and the lock ConfigMap with the **deployer** token, so
the console's routine polling never mints the installer. `services logs --cluster NAME` uses the
deployer's `pods/log`. `services down --cluster` refuses while agents are deployed in the environment,
then `helm uninstall`, deletes the `ab-svc-*` Secrets by name; PVCs from `volumeClaimTemplates` survive
uninstall and are deleted only with `--purge` (the runbook's storage class must use `Delete` reclaim for
P-A7, T9). A Postgres major-version change is refused (`CHART_MIGRATION_UNSUPPORTED`). A release rendered
by a newer AgentBreeder is refused.

### 5.5 The operator config for a cluster environment

Locally `services up` writes the environment's operator config from `stack.yaml`'s `environment:` block,
which the repository controls; the grant review is the checkpoint. For a cluster environment the same
generation applies, with field ownership fixed: `cluster connect` owns `kubernetes`; `services up
--cluster` owns `services`, `gateway`/`gateways.X` (`agent_url` with in-namespace URLs, `cluster_service`
for the admin path), `bindings` in the cluster form (§6.1) and `eval_database`. A hand-edited field is
refused on the next write (`OPERATOR_CONFIG_MODIFIED`, as today). D12 asks whether a production
environment should instead require a hand-written operator config.

### 5.6 What the chart does not cover

- **MCP relays.** A relay reads per-deployment grant files from a host directory (`grants_dir`). On a
  cluster that becomes a per-agent object and a relay that watches it: a separate design. Until D6,
  `mcp_access` stays refused on clusters.
- **Upgrades of the chart format** across AgentBreeder versions beyond the refusal in §5.4.

## 6. Secrets and network policy (#773)

### 6.1 Secret classes and how each reaches a pod

| Class | Example | Written by | On a cluster |
|---|---|---|---|
| Agent secret files | `manifest.secrets` (`secret://<name>`) | operator, local file | the deployment's own Secret (`agentbreeder-<id>`), over stdin, owned by its Deployment (§4.3) |
| Store secrets | `secret-store://<class>/<object>` | external store | CSI volume (§6.2) |
| Minted gateway key | per-deployment LiteLLM key | CLI at deploy | in the deployment's own Secret, as today |
| Environment bindings | `DATABASE_URL` → `postgres.roles.support_agent` | `services up --cluster` | the chart's `ab-svc-…` Secret, **referenced** from the agent pod, never copied |
| Service secrets | LiteLLM provider keys, Postgres passwords, Phoenix admin | `services up --cluster` | `ab-svc-*` Secrets (§5.3); D5 for an external store |
| Operator-only | gateway master key, eval DB owner, console role | `services up --cluster` | `ab-svc-*` Secrets readable only by `ab-operator` (§7.1), never mounted into agents |

**Binding resolution.** A cluster environment's `bindings` map ENV names to
`kubernetes-secret://<secret name>/value` (a new accepted form beside `secret://`, validated as #908 §10:
DNS-1123 name, key `^[A-Za-z0-9_-]{1,64}$`, no `.`/`..`/`/`). `services up --cluster` writes them from the
resolved outputs. At deploy the agent's pod gets **one projected volume** at `MOUNT_ROOT` combining its
own Secret's keys and, per binding, `secret: {name: ab-svc-…, items: [{key: value, path: <ENV lowercased
name used today>}]}`, so `<ENV>_FILE` points at the same path as locally. The deployer never reads a
binding value; the kubelet reads it for the pod *(no RBAC on the pod's creator is needed for a
same-namespace Secret volume: documented behaviour)*, which is exactly why §7.2 restricts which Secret
names a deployer-created pod may reference. Bindings by reference are verifiable from K4 (the
`ab-svc-*` Secrets exist only once the chart does).

**Operator-only consumers** (all through the `operator` identity's session and port-forwards of #908
§10/G1b, K5 here; `gateways` are global, so `gateway_keys.client` gains the environment's session):

| Consumer | Needs |
|---|---|
| `gateway_keys.client`: mint at deploy, revoke at remove/disable, orphan revoke in `reconcile` | gateway admin forward + master key |
| eval `Judge` (`evals.py`), for every case of `release`/`promote` on the cluster | gateway forward held for the run (re-opened at token expiry) |
| `spend_logs`, `usage`, `cost` | gateway admin forward + master key |
| eval fixture resets | eval database forward + owner password |
| `tool log`, `dbread`, console reads | `agent-database`/`console-database` forwards + passwords |

The reserved names (`eval-database`, `agent-database`, `console-database`, `SECRET_RESERVED`) keep their
meaning; on a cluster they resolve to operator-only `ab-svc-*` Secrets, and §7.2 refuses them in any pod.

**Eval resets on a cluster.** Fixture resets run destructive SQL as the database owner. Northwind's
`eval_database` is its `support` database, the one the agent reads, because evals need the agent to see
the fixtures. On a production cluster that means `promote` resets production data. So a cluster
environment whose `eval_database` is also an agent binding's database is refused
(`EVAL_DATABASE_SHARED`) unless its operator config says `eval_resets: allowed`, which the grant review
shows in red. The #774 run cluster is dedicated, so it sets it (D13).

### 6.2 External secret stores through the Secrets Store CSI driver

Provider-neutral: the Secrets Store CSI driver with a provider plugin (Vault, a cloud's store), installed
by the runbook.

- A manifest secret may be `secret-store://<class>/<object>`: `<class>` names a `SecretProviderClass` in
  the namespace labelled `io.agentbreeder/secret-store: "true"`; `<object>` matches
  `^[A-Za-z0-9_-][A-Za-z0-9._-]{0,252}$` and is not `.`/`..` *(how each provider declares object names is
  provider-specific, to measure; AgentBreeder checks the reference shape and that the class exists, not
  that the object resolves)*.
- **Grant.** The operator config grants classes per agent (`agents.NAME.secret_stores: [class]`); the
  §7.2 policy enforces the same list server-side (`<agent>.stores`), so a Deployment naming one agent
  cannot mount another's class (a deployer token can still name any agent, §7.2 residual).
- **Volume.** A CSI volume (`driver: secrets-store.csi.k8s.io`, `readOnly: true`) mounted at
  `/run/secret-store/<class>` (a separate root, so nothing nests in the projected volume);
  AgentBreeder sets `<ENV>_FILE` to the object's file there, so the agent cannot tell the difference.
- **No sync to Secrets.** A labelled class with `secretObjects` is refused at deploy and reported by
  `reconcile` (classes are mutable after `connect`), because sync recreates a namespace Secret.
- **Pod identity.** The store authenticates the pod by its service account *(cloud-specific; #908 §7 for
  GKE)*. On the `token` provider, a store that needs a pod identity uses a runbook-created service
  account named in `agents.NAME.secret_store_service_account`; the §7.2 policy allows that name for that
  agent only (D8).
- A missing object fails the mount and the rollout reports a fixed reason (`FailedMount` or
  `CreateContainerConfigError`, to measure). Not on local Docker (`SECRET_STORE_UNSUPPORTED`); on `kind`
  only with the driver installed (K7's test).

### 6.3 The network model: derived from the local agents network

Locally an agent container joins the stack's agents network: it reaches services by name, nothing
reaches it except through the host port the CLI uses. The cluster model is derived from that, not
identical to it (agents reach only granted services; agents never reach each other). Three layers.

**Baseline (fail closed).** `ab-baseline` is a NetworkPolicy with `podSelector: {}`, `policyTypes:
[Ingress, Egress]` and no rules: every pod in the namespace is isolated both ways unless another policy
allows a flow. `cluster connect` creates it (installer) before anything else runs in the namespace
(§7.4); `services up --cluster` verifies it as its first step; `deploy` refuses without it
(`KUBERNETES_BASELINE_MISSING`, §4.4); `reconcile` reports it missing or changed and reports a
deployment without its own policy. Agent, chart, Job and canary policies are additive on top. The
deployer and canary cannot delete or change it (§7.2). So a Deployment whose own NetworkPolicy was never
created, or was deleted, runs with no network at all, not an open one. Only `cluster down` deletes it
(installer), after every deployment and the chart release are gone.

**Agent pods** (the per-deployment NetworkPolicy):

- Ingress: none. `kubectl port-forward` reaches the pod through the API server and kubelet, not the pod
  network, so NetworkPolicy does not apply to it: on the 2026-11-10 `kind` run every health check and
  invoke went through a forward to pods with no ingress allowed. Kubelet readiness probes come from the
  node; the run CNI must exempt them as `kind`'s did *(both to measure on the #774 CNI, with
  `ab-baseline` in place)*; T4 names what forwards mean for the services.
- Egress, each its own rule (rules are additive); every port entry is `{protocol: TCP|UDP, port: N}`
  with no `endPort`:
  1. DNS: peer `{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}},
     podSelector: {matchLabels: {k8s-app: kube-dns}}}`, ports UDP and TCP 53 (as today). When the
     operator gives `--node-local-dns ADDR` at `connect`, a second rule `ipBlock: {cidr: ADDR/32}` on UDP
     and TCP 53 *(`169.254.20.10` by default; to measure)*. It falls inside the blocked
     `169.254.0.0/16`; it is a separate, narrower allow, and rules are additive.
  2. Per granted in-cluster service, a rule with one peer `podSelector: {matchLabels:
     {io.agentbreeder/chart-service: <service>}}` (no `namespaceSelector`, no `matchExpressions`) and
     ports exactly `[{protocol: TCP, port: <port>}]`. The grant is derived: the hosts of
     `gateway.agent_url`, of each `Environment.services` URL and of each binding's resolved URL, matched
     against the chart lock's Service names and ports. `KUBERNETES_SERVICE_UNKNOWN` otherwise.
  3. One rule with one peer `ipBlock: {cidr: 0.0.0.0/0, except: blocked_cidrs}` (§6.4) and no `ports`,
     for the model provider and public tools.
- Labels: `io.agentbreeder/uses-<service>: "true"` per granted service, from the same derivation.

**Chart pods** carry `io.agentbreeder/chart-service: <service>`, and chart Services select on that label
only. Chart Jobs carry `io.agentbreeder/chart-job: <service>` and never `chart-service`, so they never
join a Service's endpoints. §7.2 forbids both labels (and every other label outside a fixed set) on any
pod the deployer or the canary creates.

This replaces #908 §10's "agents get no route to Postgres": an agent whose environment binds a database
URL (Northwind does) must reach Postgres on 5432. The role's grants (`commerce.sql`) are the boundary
there, as locally; §12 records the amendment.

**Chart services** (rendered by §5): one NetworkPolicy per service selecting its `chart-service` pods,
`policyTypes: [Ingress, Egress]`.

- Ingress: from pods labelled `uses-<service>` on the service port (agents); from the `chart-service`
  pods of each service `wire:`d to it, on its port; from its own `chart-job` pods and the `chart-job`
  pods of each dependent whose step uses it (from the plan). Nothing else.
- Egress: DNS; the services it is wired to, on their ports; for `egress: true` services, `0.0.0.0/0`
  except `blocked_cidrs`; nothing else (Postgres and Phoenix get no public egress).

**Chart Jobs**: one NetworkPolicy per Job selecting its `chart-job: <service>` pods: no ingress; egress
DNS and the Job's service (and that service's upstreams the Job's step uses, from the plan) on their
ports only. The `kind` rehearsal (K4) runs the Northwind init and bootstrap Jobs under `ab-baseline`.

**Agent to agent:** blocked both ways, as on `kind`. Locally two agents on one agents network can reach
each other; the cluster is stricter.

### 6.4 `blocked_cidrs` for the `token` provider

`connect` (K1) computes it from what the named identities may read (§7.1, §7.4), plus operator input that
it cross-checks rather than trusts. `--pod-cidr` (one or more) and `--service-cidr` are **required** for
the `token` provider; there is no discovery fallback.

- pod ranges: `--pod-cidr`, cross-checked against every node's `spec.podCIDRs` (installer `nodes list`)
  and every pod IP in the namespace; a pod IP outside the ranges is `KUBERNETES_RANGES_INCOMPLETE`;
- the service range: `--service-cidr`, cross-checked against every ClusterIP in the namespace;
- every address the API server host resolves to (all A records, resolved by AgentBreeder) as `/32`;
- node addresses: `--node-cidr` (one or more) when given, else every node `InternalIP` and `ExternalIP`
  as `/32`; and `169.254.169.254/32`;
- always `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`, `169.254.0.0/16` (a
  customer network is reachable through them, #908 §6).

IPv4 only, at most 32 entries, refused rather than truncated (`KUBERNETES_RANGES_TOO_MANY`; a cluster
whose public node addresses do not fit needs `--node-cidr`); dual-stack clusters are refused
(`KUBERNETES_DUAL_STACK_UNSUPPORTED`). Node addresses added later (autoscaling) are open to agents until
`reconcile` recomputes, reports the difference, and the operator reruns `connect` (which rewrites
`blocked` in `ab-grants`); `--node-cidr` avoids that gap. AgentBreeder reads nothing in `kube-system` or
`default` beyond the `kube-system` namespace object it reads today; ranges it cannot see are the
operator's input, and the record says so. Allow rules to chart services by label still apply (a
`podSelector` peer admits by identity; the union is the canary's last case).

### 6.5 Enforcement is measured, not assumed: the canary

A cluster without an enforcing CNI accepts NetworkPolicy objects and ignores them. `connect` (and
`doctor --cluster`, `reconcile --canary`) runs a canary in the namespace **as `ab-canary`** (§7.1), whose
pods and policies §7.2 holds to fixed templates, so a health check never mints the installer. Three pods
from the built-in `echo` image (pinned digest, mirrored to the registry by `connect`; `service/echo` gains
a `probe` mode in K6): a server and a "target", each `serve` on ports 8080 and 8081, and a client running `probe
<target IP> <port> …`, which tries each address three times (starting 3 s after it starts, 5 s timeout
each) and reports only through its exit code and termination reason (a fixed bitmask word), never
printing a response, so the canary needs no `pods/log`. The canary cannot update objects, so each step
deletes and recreates its policies and client pod. Every canary policy selects only canary pods and
peers only with canary pods, or is the fixed `0.0.0.0/0 except ⊇ blocked` shape.

1. **Baseline:** no canary policy: client reaches neither server nor target. A success is
   `KUBERNETES_POLICY_NOT_ENFORCED`.
2. **Positive control:** client egress to both by `podSelector`, server and target ingress from the
   client: client reaches both. If not, `KUBERNETES_CANARY_INCONCLUSIVE`, never a pass.
3. **Ingress deny:** as 2 without the server's ingress allow: client fails the server, reaches the target.
4. **`ipBlock` except and union:** the client's egress is only the fixed `0.0.0.0/0 except ⊇ blocked`
   rule (the pod range is in `blocked`) plus a `podSelector` rule to the server on 8080; ingress allows
   as 2: client reaches the server on 8080 and fails the target. (The server also listens on 8081; the
   probe's bitmask separates "refused" from "timed out", and a policy drop on 8081 must time out, not be
   refused.)

Any other result is `KUBERNETES_POLICY_NOT_ENFORCED`. The canary objects are deleted by name in
`finally`. It proves these rule shapes were enforced when it ran, not that every later rule is; the
runbook reruns `reconcile --canary` after a CNI or node-pool change.

## 7. Identities, RBAC and the required admission policies

### 7.1 Five identities, least power on the most-used one

| Identity | Used by | Namespace rights | Cluster rights |
|---|---|---|---|
| operator (their own mint file) | minting only | `create serviceaccounts/token` on the four accounts below | none needed by AgentBreeder |
| `ab-deployer` | deploy, promote, status, logs, forward (agents), remove, disable, reconcile, `services status/logs --cluster`; the read checks and deployer probes of `connect` (§7.4) | deployments, services, networkpolicies: create, delete, get, list, watch (shapes bounded by §7.2); deployments/finalizers: update (for `blockOwnerDeletion` only); secrets: **create only**; replicasets, pods: get, list, watch (**no create**); pods/log: get; pods/portforward: create; configmaps (`resourceNames: [ab-chart-lock, ab-grants]`): get; secretproviderclasses: get, list; resourcequotas, limitranges: get, list | namespaces (`resourceNames: [kube-system, <ns>]`): get; validatingadmissionpolicies and bindings (`resourceNames` of §7.2): get; nodes: list (`reconcile`'s range recompute, §6.4) |
| `ab-installer` | `services up/down --cluster`; the writes of `connect` and `cluster down` (§7.4) | deployments, statefulsets, services, networkpolicies, configmaps, serviceaccounts, persistentvolumeclaims, jobs: create, get, list, watch, patch, update, delete; secrets: create, get, list, watch, update, patch, delete (Helm's release storage updates the previous release record on every upgrade); pods, replicasets: get, list, watch; pods/log: get | nodes: list |
| `ab-operator` | the §6.1 operator-only consumers | secrets (`resourceNames`: the operator-only `ab-svc-*` names from the chart lock): get; pods/portforward: create; services, pods: get, list | none |
| `ab-canary` | the canary (§6.5) and its probes | pods, networkpolicies: create, get, list, watch, delete (bounded by §7.2 to the canary templates); **no** `pods/log` | none |

- The deployer cannot read Secrets through the API, and §7.2 holds what it creates, changes, deletes and
  mounts to fixed shapes; what it can still reach through a pod is §7.2's residual (T3). The installer can
  read every Secret in the namespace (Helm) and is minted only for `connect`, `cluster down` and
  `services up/down --cluster`; within the namespace it can also run a pod as any service account and
  rewrite `ab-grants`, so it equals a namespace admin (T7, T10). The operator identity holds the gateway
  master key and the database owners' passwords and is minted by commands that need them, which include
  `deploy`, `remove`, `disable` and `reconcile` for agents with model access (key mint, renewal and
  revoke), so it is on the common path.
- The operator identity's `resourceNames` change when operator-only Secret names change (a new
  generation): `services up --cluster` prints the new Role and refuses operator-only reads until
  `connect` re-probes it (D3).
- `pods/portforward` and `pods/log` cannot be scoped by pod in RBAC: a deployer-token holder can forward
  to any pod in the namespace, Postgres and LiteLLM included, bypassing NetworkPolicy, and read any pod's
  log, service and Job logs included. It still needs passwords to use the services (T4, T8).
- Verb audit: K2 delivers a table of every kubectl and helm call AgentBreeder makes and the verbs it needs,
  generated from a `kind` run under the restricted accounts (`kubectl delete -l` lists then deletes;
  `--wait` needs `get`, `list` and `watch`; `--cascade=foreground` waits on the GC; Helm's Secrets driver
  updates release records), and the Roles above are corrected from it before K9.

### 7.2 The admission policies the runbook must install

RBAC cannot stop an identity that may create Deployments from mounting any Secret, naming any service
account (and so receiving its token), labelling its pods into a chart Service, or creating a
`kubernetes.io/service-account-token` Secret; nor can it limit the shape of the Services and
NetworkPolicies it may create or the objects it may delete. So the runbook installs
`ValidatingAdmissionPolicy` objects (GA in Kubernetes 1.30), **one per kind** so each policy's CEL is typed
for one kind (`ab-deployments`, `ab-services`, `ab-networkpolicies`, `ab-secrets`, `ab-pods`,
`ab-deny-other`), each with its binding. AgentBreeder renders them for the namespace (`agentbreeder
cluster policy print --namespace N`). The recorded digest is of the rendered objects' `spec` fields after
normalisation (sorted keys, API-server defaults filled in, `metadata` other than names dropped), so
server-side defaulting and `managedFields` do not make it flap; it is namespace-specific.

**Configuration** (each value fails closed): `failurePolicy: Fail`; `matchConstraints` with
`matchPolicy: Equivalent`, `namespaceSelector` for the one namespace, and the policy's kind (plus
`deployments/scale`, `pods/ephemeralcontainers` where relevant; `ab-deny-other` matches every other
resource) on `CREATE`, `UPDATE` and `DELETE`; `matchConditions` limiting each to
`request.userInfo.username` in {`system:serviceaccount:<ns>:ab-deployer`,
`system:serviceaccount:<ns>:ab-canary`} (bindings have no user selector); `paramKind: ConfigMap` and
bindings with `paramRef: {name: ab-grants, namespace: <ns>, parameterNotFoundAction: Deny}` and
`validationActions: [Deny]`. Pods and ReplicaSets created by the Deployment controller run as the
controller's account and are not matched; that is safe only because the deployer may not create Pods or
ReplicaSets at all and the canary only Pods (RBAC, probed in §7.3), so the Deployment's pod template and
the canary's Pods are the gates. Names are compared positively, so a `generateName` request, whose name is
empty at validation, fails closed. **Validation sees the defaulted object** (the API server fills
`restartPolicy`, `dnsPolicy`, `schedulerName`, `terminationGracePeriodSeconds`, `strategy`,
`revisionHistoryLimit`, `progressDeadlineSeconds`, `terminationMessagePath`/`Policy`, probe
`timeoutSeconds`/`successThreshold`, port `protocol`, `httpGet.scheme`, Service `sessionAffinity`,
`ipFamilyPolicy`, `internalTrafficPolicy` and similar before validation): every such key is admitted
only with its default value (the controller-created Pods are not matched; the canary's Pods are, see
below), and K2's first test is that AgentBreeder's own Deployment, Service,
NetworkPolicy and Secret are admitted. *(To measure: the exact defaulted key set on the run cluster's
version; DELETE admission per item for `kubectl delete -l`.)*

**`ab-grants` encoding.** CEL cannot parse JSON, so the ConfigMap's `data` is flat strings, written only
by the installer: `registry` (the binding's registry prefix), `echo` (the pinned echo image reference),
`pull` (the runbook's pull Secret name, or empty), `blocked` (comma-separated canonical `blocked_cidrs`),
`nodedns` (the node-local DNS address, or empty), and per agent name `<agent>.bindings`, `<agent>.uses`
(`<service>:<port>` items), `<agent>.stores`, `<agent>.sa`, each a comma-separated list, present for every
granted agent even when empty. An `io.agentbreeder/name` without a `<name>.bindings` key is refused
(an absent entry never means "empty"), and so is a name that fails `^[a-z0-9][a-z0-9-]{0,62}$`. Rules
read keys with `has(params.data[k])` guards, treat `''` as the empty list (`''.split(',')` is `['']`),
strip the `:<port>` part when matching `uses-<service>` labels (whose value must be `"true"`), and keep
lookups in `variables`. Membership in `blocked` is string equality over canonical CIDRs, not CIDR
arithmetic. *(The CEL cost estimate for ConfigMap values with no declared maximum length is to measure
in K2; if it exceeds the budget, the lists move into one key per item.)* The agent name `ab-probe` is
reserved (`AGENT_NAME_RESERVED`); `connect` writes its entry for the probes (§7.4).

**The deployer's Deployment is AgentBreeder's Deployment.** `_objects` writes one fixed shape; the policy
admits that shape (after defaulting) and nothing else, with only these fields varying:

| Field | Admitted value |
|---|---|
| `metadata` | `name` `agentbreeder-<32 hex>` equal to `agentbreeder-` + the `io.agentbreeder/deployment` label; `labels` as the template's; `annotations` exactly `{io.agentbreeder/spec, io.agentbreeder/image}` (what `_objects` writes); no `finalizers`, no `ownerReferences` |
| `spec` keys | `replicas` (1), `selector`, `template`, and the defaulted `strategy`, `revisionHistoryLimit`, `progressDeadlineSeconds` at their defaults; nothing else (`paused`, `minReadySeconds` refused) |
| `selector` | exactly `{matchLabels: {io.agentbreeder/deployment: <id>}}` |
| template `metadata` | label keys exactly {`app.kubernetes.io/managed-by` (= `agentbreeder`), `io.agentbreeder/deployment` (= `<id>`), `io.agentbreeder/name`, and `io.agentbreeder/uses-<s>` (= `"true"`) for `s` in `<name>.uses`}; any other key refused, `chart-service`, `chart-job`, `canary`, `stack` and `part-of` included; **no annotations** (so no add-on acts on a deployer-chosen annotation: Multus networks, Calico, mesh injection) |
| pod spec keys | `automountServiceAccountToken` (false), `enableServiceLinks` (false), `securityContext` (equal to `_objects`'), `containers`, `volumes`, `serviceAccountName` (absent, `default`, or `<name>.sa`), `imagePullSecrets` (absent, or exactly `[{name: <pull>}]` when `pull` is set), `nodeSelector` (absent, or only `kubernetes.io/arch` equal to the binding's), and the defaulted `restartPolicy`, `dnsPolicy`, `schedulerName`, `terminationGracePeriodSeconds` at their defaults; everything else (`nodeName`, `tolerations`, `affinity`, `priorityClassName`, `runtimeClassName`, `dnsConfig`, `hostAliases`, `shareProcessNamespace`, host namespaces, `initContainers`, `ephemeralContainers`, …) refused |
| `containers` | exactly one, named `agent`, keys only `name`, `image`, `imagePullPolicy` (`IfNotPresent`), `ports`, `env`, `securityContext` (equal to `_objects`'), `resources`, `readinessProbe`, `volumeMounts`, and the defaulted `terminationMessagePath`/`Policy`; no `command`, `args`, `envFrom`, `lifecycle`, `livenessProbe`, `startupProbe`, `workingDir`, `stdin`, `tty` |
| `ports` | exactly one `{containerPort: <p>, protocol: TCP}`; no `hostPort`, `hostIP`, `name` |
| `readinessProbe` | `httpGet` with only `path` and `port` (= `<p>`) plus the defaulted `scheme: HTTP`; no `host`, `httpHeaders`; `periodSeconds` and `failureThreshold` as `_objects`; defaulted `timeoutSeconds`/`successThreshold` at defaults |
| `resources` | exactly `_objects`' requests and limits, keys `cpu` and `memory` only |
| `image` | a string check, not a regex built from a label: starts with `registry + '/' + name + '@sha256:'` and the rest matches `^[0-9a-f]{64}$`; mirrored images live under `<registry>/mirror/`, which the name pattern cannot reach |
| `env` | `name` and literal `value` only, no `valueFrom` |
| `volumes` | `tmp` (`emptyDir`, `medium: Memory`, `sizeLimit` present and ≤ 64Mi); `secrets` (`projected`, sources only: a `secret` named exactly the Deployment's own `metadata.name`, `secret`s named in `<name>.bindings`, `downwardAPI` with items only `fieldRef` on `metadata.name` or `metadata.labels`); `csi` volumes with driver `secrets-store.csi.k8s.io`, `readOnly: true`, a class in `<name>.stores`, no `nodePublishSecretRef` |
| `volumeMounts` | exactly: `tmp` at `/tmp`; `secrets` at `MOUNT_ROOT`, `readOnly: true`; each `csi` volume at `/run/secret-store/<class>`, `readOnly: true`; no `subPath`, `subPathExpr`, `mountPropagation` |

Because the own-Secret source must equal the Deployment's own name and a Deployment's name must equal its
deployment label, a Deployment can mount only its own Secret, not another deployment's.

**Other objects the deployer may create:**

- *Services*: name equal to a deployment name; labels exactly the deployment's; no annotations,
  finalizers or ownerReferences; `spec` keys only `type` (`ClusterIP`), `selector` (exactly
  `{io.agentbreeder/deployment: <id>}`), `ports` (one `{port: p, targetPort: p, protocol: TCP}`), and
  the defaulted `sessionAffinity`, `ipFamilyPolicy`, `ipFamilies`, `internalTrafficPolicy`, `clusterIP`
  /`clusterIPs` (absent or allocator-assigned, never request-set *(to measure on create)*) at defaults; no
  `externalIPs`, `externalName`, `loadBalancerIP`, `nodePort`, `publishNotReadyAddresses`,
  `trafficDistribution`.
- *NetworkPolicies*: name equal to a deployment name; labels exactly the deployment's; no annotations,
  finalizers or ownerReferences; `podSelector` exactly `{matchLabels: {io.agentbreeder/deployment:
  <id>}}`; `policyTypes` exactly both; `ingress` empty; each egress rule has a non-empty `to` with exactly
  one peer, every port entry `{protocol, port}` without `endPort`, and is one of: the DNS peer of §6.3 with
  ports exactly UDP/TCP 53; `ipBlock {cidr: <nodedns>/32}` with the same ports (only when `nodedns` is
  set); `podSelector: {matchLabels: {io.agentbreeder/chart-service: s}}` (no `namespaceSelector`, no
  `matchExpressions`) with ports exactly `[{protocol: TCP, port: <port>}]` where `s:<port>` is in the
  `<name>.uses` of the policy's `io.agentbreeder/name` label; `ipBlock` with `cidr` exactly `0.0.0.0/0`,
  `except` containing every entry of `blocked`, no `ports`. A rule with ports but no peers is refused.
- *Secrets*: create only; `type: Opaque`; name `agentbreeder-<32 hex>`; no annotations or finalizers;
  exactly one `ownerReferences` entry with `apiVersion: apps/v1`, `kind: Deployment`, the same name,
  `blockOwnerDeletion: true` and no `controller` *(CEL checks the name, not that it exists or the
  UID: a wrong UID gets the Secret collected as an absent owner and the victim's pod pending, a denial
  of service only)*. The reservation of the name works through the GC's foreground deletion alone; the
  `deployments/finalizers` grant exists for clusters with the `OwnerReferencesPermissionEnforcement`
  plugin and is not the enforcement mechanism.
- *Deletes*: `oldObject` must carry `app.kubernetes.io/managed-by: agentbreeder`, an
  `io.agentbreeder/deployment` label and a name `agentbreeder-<32 hex>`, so no chart object,
  `ab-baseline`, canary object or foreign object can be deleted, whatever selector `kubectl delete -l`
  was given. A Deployment delete must carry `request.options.propagationPolicy: Foreground` and no
  `orphanDependents`, written as `has(request.options.propagationPolicy) &&
  request.options.propagationPolicy == 'Foreground'`, so a body-less DELETE (whose default for
  `apps/v1` is `Background`) fails closed; anything else is refused. *(The `request.options` field in
  CEL and kubectl's encoding of `--cascade` are to measure; K2's fixture covers a body-less DELETE and a
  body with only `gracePeriodSeconds`, and K2 does not close until both are refused.)*
- *Updates and patches* of Deployments (including `deployments/scale`), Services and NetworkPolicies:
  refused. AgentBreeder replaces, it never patches (`promote` creates a new record; `disable` deletes).
- *Everything else* (ConfigMaps, Pods, ReplicaSets, other namespaced kinds): refused (`ab-deny-other`,
  whose `matchConstraints` list every namespaced resource with `excludeResourceRules` for the five typed
  policies' kinds and subresources). Cluster-scoped objects are outside any `namespaceSelector`; the
  deployer and canary have no cluster-scoped write in RBAC, and `selfsubjectaccessreviews` (from `kubectl
  auth can-i`) are cluster-scoped and harmless. `CONNECT` (`pods/portforward`, `pods/log` reads) is
  outside the matched operations. Each policy shared by the deployer and the canary (`ab-pods`,
  `ab-networkpolicies`) branches on `request.userInfo.username`.

**`ab-canary`'s objects** have their own fixed templates. Pods: name `ab-canary-<server|target|client>`,
labels exactly {`app.kubernetes.io/managed-by`, `io.agentbreeder/canary: <role>`}, no annotations; one
container `echo`, image exactly `echo` from `ab-grants`, `args` exactly `["serve", "8080", "8081"]` for server
and target, or `["probe", <IPv4>, <port>, …]` (at most 6 address/port pairs) for the client; no `env`,
`envFrom`, `command`, probes or volumes but a `tmp` `emptyDir`; automount false, `restartPolicy: Never`
for the client. **The Pod is validated after built-in mutating admission**, so these defaulted keys are
admitted at their default values only: `serviceAccountName` and `serviceAccount` equal to `default`
(ServiceAccount admission), the not-ready and unreachable `tolerations` with the default
`tolerationSeconds` (DefaultTolerationSeconds), `priority` 0 and `preemptionPolicy` (Priority),
`enableServiceLinks`, `dnsPolicy`, `schedulerName`, `terminationGracePeriodSeconds`, container
`terminationMessagePath`/`Policy` and `imagePullPolicy` *(the full set on the run cluster's version, to
measure)*; every other pod-spec field refused as for the deployer. K2's own-objects test admits the three
canary Pods and a canary NetworkPolicy too. NetworkPolicies: name `ab-canary-<n>`, labels exactly
{`app.kubernetes.io/managed-by`, `io.agentbreeder/canary: policy`}, `podSelector` on a canary role label only, `policyTypes` both,
peers only `podSelector` on canary role labels, and egress `ipBlock` only in the fixed `0.0.0.0/0 except
⊇ blocked` shape. Deletes only of canary-labelled objects. So a canary token can probe only what an
agent's egress could reach (public addresses) plus canary pods, and learns only a pass/fail word.

**What a deployer token can still do (residual, T3, T7).** CEL cannot verify who the agent named in a
label really is, cannot join a Deployment with its NetworkPolicy, and cannot inspect what a container
does with its environment. So a deployer-token holder can create a Deployment naming any agent in
`ab-grants`, running that agent's own image, with literal environment variables of its choosing
(`PYTHONPATH`, `NODE_OPTIONS`, `LD_PRELOAD` and the like) and files of its choosing in its own Secret at
`MOUNT_ROOT`. That is **arbitrary code of the deployer's choosing inside a granted agent's container**,
with that agent's binding values, store objects and service reach, readable back through `pods/log`. It
can also pair one agent's pod labels with another granted agent's NetworkPolicy (the union of two agents'
grants), forward to any pod in the namespace (T4), read any pod's log (T8), delete any AgentBreeder
deployment's objects (a cross-agent denial of service), and pre-create a Secret under a future
deployment's name so that deploy fails. It can run any digest in that agent's repository, including
images that never passed `release`'s eval gate, and it can list nodes (names and addresses). It cannot
read or mount another deployment's own Secret (its
secret files and gateway key), the chart's own Secrets, the operator-only Secrets or Helm's records, run
an image outside that agent's repository, run as another service account, set annotations, place pods
on chosen nodes, join chart Services, open its pods' network beyond the union of granted agents' rules,
or touch the chart, `ab-baseline` or `ab-grants`. Binding identity to something the deployer cannot
choose (a namespace or a deployer account per agent) is D15.

### 7.3 RBAC probes

`connect` also probes with `kubectl auth can-i` as each account and refuses on any yes outside §7.1:
deployer `get`/`list` secrets, `create pods`, `create replicasets`, `create pods` in another namespace,
`create`/`patch` configmaps, `create rolebindings`, `escalate`, `bind`, `impersonate`, `create
serviceaccounts/token`, `* *`; installer and canary anything cluster-scoped beyond `nodes list`; canary
`create deployments` and `get pods/log`; operator anything beyond its rows. Then `auth can-i --list` in
the namespace for each, refused on any rule beyond the table. Probes cannot prove the absence of rights
granted by other authorizers (webhooks, cloud IAM); the runbook checks that the four accounts have no
cloud IAM bindings through workload identity, that no other RoleBinding in the namespace names them, and
that no mutating webhook selects the namespace (it would act on pods the controller creates, which the
policies do not see), and the record says what was checked.

### 7.4 `connect`, step by step, and who does each step

`connect` runs in this order; a failure stops it with nothing left half-made except what the step names.

1. **Mint** each account once (operator's mint file).
2. **Read** as the deployer: the `kube-system` UID, the namespace and its labels (`managed-by`,
   `enforce: restricted`), the Kubernetes version (≥ 1.30), the admission policies and bindings and their
   digests, `ResourceQuota` and `LimitRange`. Refuse on any gap (§7.2).
3. **Compute** `blocked_cidrs` (§6.4) as the installer (`nodes list`, pod and Service IPs).
4. **Mirror** the `echo` image to `<registry>/mirror/` (pusher credential, §8).
5. **Write** as the installer, idempotently: `ab-baseline` (§6.3; refused with
   `KUBERNETES_NAMESPACE_NOT_EMPTY` when the namespace holds pods that are not AgentBreeder's, since the
   baseline would cut them off), and `ab-grants` with `registry`,
   `echo`, `pull`, `blocked`, `nodedns` and the `ab-probe` entry. If `ab-grants` already exists (a
   `services up --cluster` ran), only those keys are updated and the per-agent keys are kept.
6. **Probe** the policies with paired server-side dry-runs (below), as the deployer and as the canary.
7. **Probe** RBAC (§7.3).
8. **Canary** (§6.5) as the canary.
9. **Record** the `kubernetes` block for the grant review (§4.2).

`services up --cluster` takes over `ab-grants` from there (its first step checks `ab-baseline` and the
`connect`-written keys), and writes `ab-chart-lock` with the `ab-grants` digest. Until a chart is
installed there is no `ab-chart-lock`, and deploy refuses with `KUBERNETES_CHART_STALE` before
`KUBERNETES_GRANTS_CHANGED` could be evaluated. `cluster down` (installer) deletes `ab-baseline`,
`ab-grants` and the mirrored `echo` image only after `services down --cluster` and with no deployment
left.

**Paired probes.** For each refusal, a request that differs from an admitted one only in the refused
field, so a refusal for another reason (RBAC, an unrelated rule) cannot pass for the policy's; the
refusal message must name the policy. As the deployer, all of: a Deployment mounting another deployment's
Secret, an operator-only `ab-svc-*` Secret, or a Helm release Secret; `serviceAccountName: ab-installer`;
a `persistentVolumeClaim` volume; `envFrom`; `env.valueFrom`; an `initContainer`; `command`; a
`livenessProbe`; `nodeName`; `priorityClassName`; `tolerations`; a pod annotation; a Deployment
annotation outside the two; a `hostPort`; a readiness probe with `host`; a `volumeMount` with `subPath` or
at another path; `resources` other than `_objects`'; an image by tag, from another agent's repository,
from `mirror/`, and with a name label of an ungranted agent; a foreign pull Secret; an ungranted
`uses-postgres` label; a `chart-service: litellm` label; a Service of `type: LoadBalancer` and one with
`externalIPs`; a NetworkPolicy with `egress: [{}]`, a port-less service rule, an `endPort`, a
`namespaceSelector` peer, an `ipBlock` other than the fixed shape, or a policy selecting `chart-service:
postgres`; a service-account-token Secret; a Secret named like an `ab-svc-*` Secret; a delete of the
chart's `litellm` Service and of `ab-baseline`; a Deployment delete with `--cascade=orphan` and with
`background`. (A Pod and a Deployment update as the deployer are refused by RBAC first; §7.3 probes
them.) As the canary: a Pod with
a Secret volume, `serviceAccountName: ab-installer`, `env.valueFrom`, a `uses-postgres` label, another
image, `args` outside the template; a policy with a free `ipBlock` or a non-canary peer. Each pair's
admitted request must be admitted.

## 8. Registry and images, provider-neutral

- **Push.** `promote` (agents) and `services up --cluster` (project services and mirrored images) push
  with a temporary Docker config (0700, `auths` only, no `credsStore`/`credHelpers`, the pinned context
  copied in), logged in with `token.registry_credential` (a local secret file with a username and token,
  read into memory, given on stdin to `docker login --password-stdin`), deleted in `finally`; the user's
  `~/.docker/config.json` is never changed (#908 §8 for the GKE token source).
- **Before any login.** The Docker client follows the registry's `WWW-Authenticate` realm on its own and
  sends the credential there. So before `docker login`, AgentBreeder requests `/v2/` unauthenticated and
  checks the realm: HTTPS, and on the registry host or an allow-list recorded at `connect`, else
  `REGISTRY_REALM_REFUSED` and no login; the check repeats before each push. The registry host is pinned
  in the binding. The Docker daemon verifies the registry against its own trust store, not
  `registry_ca_sha256`, and follows the realm on its own between checks (T16).
- **Digest check.** AgentBreeder's own client: HTTPS to the binding's registry host, system trust store
  or `registry_ca_sha256`, no proxy, the same realm rule. Redirects are followed only for manifest and
  blob GETs, only to HTTPS, never to an address that resolves to a private, loopback, link-local or
  metadata range (the connection goes to the address that was checked, so DNS rebinding cannot swap it), and `Authorization` is dropped on any hop to another host (registries redirect blobs
  to a CDN). Manifest and config bytes are hashed and compared with the requested digests, with size caps
  (4 MiB manifest, 1 MiB config); then the config digest is compared with the evaluated image's, as on
  `kind`. Plain HTTP stays loopback-only.
- **Pull.** Nodes pull with their own identity or the runbook-created `pull_secret`; AgentBreeder never
  creates a pull secret, and §7.2 does not let a deployer pod mount one.
- **Architecture.** `IMAGE_PLATFORM_MISMATCH` and platform-aware index selection as #908 §8, with the node
  architecture recorded at `connect` from the nodes' `kubernetes.io/arch` labels (mixed clusters record
  one, and `nodeSelector` pins it). Project service images are built for that architecture
  (`docker build --platform`), so an Apple Silicon laptop builds `linux/amd64` for amd64 nodes under
  emulation *(time to measure)*.

## 9. Threat model (delta over `kind` and #908 §11)

| | Threat | Mitigation | Residual |
|---|---|---|---|
| T1 | Operator workstation compromised (mint file, tools, login) | Dedicated mint file copied and hashed per mint; exec binary in a non-writable directory and hashed; `kubectl`/`helm` paths hashed; bound tokens ≤ 60 min; accounts split; allowlisted env | The mint file can mint every account; an exec plugin's interpreter, libraries and `HOME` config are not pinned; tokens are not individually revocable (delete and recreate the account) |
| T2 | Token sent to a wrong endpoint | CA pin with PEM in the binding; argv `--server`/`--certificate-authority`/`--tls-server-name`; mint server must equal the binding; public-CA endpoints refused | A CA the operator accepted at review |
| T3 | Deployer token used to read or plant secrets, or to run its own code | No Secret read verb; §7.2 fixes the Deployment template (labels, annotations, fields, mounts, image path), ties the own-Secret mount to the Deployment's name, limits Secret types and names, and deletes; installer refuses foreign `ab-svc-*` Secrets | Code of its choosing (through env and its own Secret's files) inside any granted agent's container, with that agent's binding values, store objects and service reach (§7.2 residual); not other deployments' own Secrets or gateway keys |
| T4 | Deployer token forwards to or reads logs of service pods | Services need passwords the deployer cannot read; operator-only secrets on a separate identity | Network access to every pod in the namespace through `pods/portforward`, and every pod's log; LiteLLM's admin API shares the inference port (master key only) |
| T5 | Helm release record leaks values | No values in the chart; leak scan of known values; only the installer reads secrets | A secret placed in `service.yaml` defaults by a service author is caught only if it is a known value |
| T6 | NetworkPolicy silently not enforced, or a pod with no policy | `ab-baseline` default deny (a missing policy fails closed); four-case canary with a baseline case and a positive control at `connect`, `doctor`, `reconcile --canary` | A CNI change between canaries |
| T7 | Another identity mounts secrets or runs as an account | Dedicated namespace; §7.2 for the deployer and canary; only the installer (and the Deployment controller) creates other pods; canary probes report pass/fail only, within agent-shaped egress | A deployer-token holder acts as any agent in `ab-grants` (T3) and can delete any deployment's objects; the installer equals a namespace admin (any service account, `ab-grants`); a cluster admin can do anything; a mutating webhook on the namespace would act on controller-created pods (runbook check) |
| T8 | Secrets and payloads in logs | No secrets in env or argv; bootstrap Job deleted as soon as its value is read | The deployer's `pods/log` can read the bootstrap line before deletion and any service log; the cluster's log shipper may keep them; service log levels set to omit statements and keys *(to measure per image)*; recorded |
| T9 | Stateful data left behind | `down --purge`; storage class `Delete` reclaim checked by the runbook; teardown record | A `Retain` storage class keeps volumes after PVC deletion |
| T10 | Pod label spoofing (chart Services, policy peers); widened policies; Service hijack | §7.2 fixes the label set (no `chart-service`, `chart-job`, `canary`), NetworkPolicy and Service shapes, and refuses deletes of non-deployment objects; `ab-grants` digest checked at deploy and `reconcile` against `ab-chart-lock` | The installer or an admin can rewrite `ab-grants` (detected, not prevented); a deployer token can claim any granted agent's labels (T7) |
| T11 | An injected agent exfiltrates what it can read | Private ranges and metadata blocked; services reachable only per grant | Open public egress and DNS: AgentBreeder enforces nothing at run time here; a granted DB role's data is exfiltratable |
| T12 | Local binding record edited to repoint server/CA | Binding row digest checked against the deployment's audit event | Whoever can write both the store and the audit log (the audit chain detects corruption; a DB owner can recompute it) |
| T13 | Cleartext in-cluster traffic | Namespace-local traffic only, by policy | Agent↔service and service↔service traffic is cleartext unless the CNI encrypts it; recorded |
| T14 | Resource starvation, object flooding | Per-pod requests and limits; `connect` requires a `ResourceQuota` with `count/secrets` and `count/deployments.apps` | Within the quota |
| T15 | Secrets at rest in etcd | — | Encryption at rest is the cluster's; `connect` cannot see it; the record states what the run cluster uses |
| T16 | Supply chain of tools, images and the registry path | Tools re-hashed before each exec; images by digest, mirrored, manifest and config bytes hashed; realm checked before login and push | No signing; the renderer and Helm are trusted; the Docker daemon's own trust store and realm handling |

## 10. Operator experience

- `agentbreeder cluster connect token …` → grant review → `services up --cluster --environment prod` →
  grant review → `promote` / `deploy --cluster` as on `kind`. `Next:` lines name the next command (M3).
- `agentbreeder cluster policy print` and `cluster rbac print` emit the runbook's YAML (§7).
- `doctor --cluster` reports: tool paths and hashes, the binding, session and forward leftovers, the
  canary, the chart lock vs. the environment's lock, the admission policy digest, RBAC probes, and quota
  presence.
- The console (`agentbreeder up`) runs these as CLI child processes; it gains no rights and no credential
  of its own. Console views for cluster services are a lane B follow-up, not in this epic's first
  delivery.

## 11. The exit run (#774)

1. Runbook (in `docs/kubernetes.md`, written from what ran): the cluster (D1), namespace with the
   `managed-by` and `restricted` labels, the four service accounts and §7.1's Roles, the §7.2 policy,
   the operator's token-request rights and dedicated mint file, a `Delete` storage class, quota and
   limit range, registry and pull credentials, the CSI driver and a provider for P-A5 (D8), a budget
   alert if cloud-hosted.
2. `cluster connect token`, review, `services up --cluster` for Northwind, review.
3. Northwind: `release` locally (61/61) → `promote` (61/61 on the cluster); a replacement promote; a
   rollback through a failing eval set; `remove`, `disable`, `reconcile` (including edited labels, a
   scaled-down workload and a deleted policy).
4. **Imported agent.** `agentbreeder import` (#757) is paused, so "imported" means an existing image
   AgentBreeder did not build: `src/agentbreeder/examples/http-agent` pushed to the registry and deployed
   with a hand-written v2 manifest (`target: kubernetes`) through `deploy --cluster`, with one
   `secret-store://` secret it reads at `/invoke` (P-A5). D10 may swap in a third-party image.
5. Teardown (P-A7): `remove` all, `services down --cluster --purge`, delete the four service accounts, the
   policy, the namespace, the images; list commands show nothing left.

Rows the record must fill:

| Check | Expected |
|---|---|
| Tokens in state, store, audit, job logs, `ps`, `~/.kube/cache`, `~/.docker` | none; session dirs empty after each command |
| Service secret values on the laptop's disk after `services up --cluster` | none |
| Deployer `auth can-i get/list secrets`, create pods/replicasets, cross-namespace pods, configmap writes, RBAC escalation | all no |
| Every §7.4 paired probe, as the deployer and as the canary | each refused request rejected by the named policy; each paired request admitted |
| AgentBreeder's own Deployment, Service, NetworkPolicy and Secret after API-server defaulting | admitted |
| The canary's three Pods and its NetworkPolicies after built-in mutating admission | admitted |
| Deployer: delete a deployment with `--cascade=orphan`, then recreate its name and mount its Secret | the delete refused; while a foreground delete is pending the name cannot be recreated |
| `kubectl delete -l` with a selector matching a chart object, as the deployer | the chart object's DELETE rejected (admission per item) |
| A deployer-token Deployment mounting another deployment's `agentbreeder-<id>` Secret | rejected |
| §7.2 residual, measured: a deployer-token Deployment naming a granted agent, with an env variable and a file in its own Secret that make the agent's runtime print its binding value | possible (recorded as the documented residual) |
| `ab-baseline` present; a Deployment whose NetworkPolicy was deleted | the pod reaches nothing (DNS included); `reconcile` reports the missing policy |
| Canary client probing the metadata address and the API server | fails (agent-shaped egress); only a pass/fail word is observable |
| Readiness probes of agents and chart pods under `ab-baseline` | pods become ready |
| Northwind init and bootstrap Jobs under `ab-baseline` | complete through their Job policies |
| A per-deployment key past half its lifetime after `reconcile` | extended |
| `ab-grants` edited by an admin, then `deploy` | `KUBERNETES_GRANTS_CHANGED` |
| Same probes as real creates, policy deleted, then `deploy` | deploy refused `KUBERNETES_POLICY_MISSING` |
| Pre-created `ab-svc-*` Secret (by an admin) before `services up --cluster` | `CHART_SECRET_FOREIGN`, not consumed |
| Canary on the run cluster; with enforcement off (a namespace or cluster without an enforcing CNI) | four cases pass; `KUBERNETES_POLICY_NOT_ENFORCED` |
| Agent pod to API server addresses, nodes (internal and external), kubelet, RFC 1918 outside the cluster, metadata | blocked; reachable from a control pod |
| Northwind agent to Postgres 5432, LiteLLM, proxy; to Postgres and Phoenix on other ports | allowed; blocked |
| A pod without `uses-*` to Postgres, LiteLLM | blocked |
| Agent to agent | blocked |
| Deployer-token `port-forward` to Postgres | connects (T4); no credential obtainable |
| Chart: secret values in `helm get values/manifest`, the chart dir, argv | none |
| Bootstrap ingest key in the cluster's log store | recorded |
| Chart pods under `restricted`; read-only root per image | admitted; exceptions recorded |
| Deployment Secret after `remove` (owner GC) | gone |
| `secret-store://` value at the agent's `_FILE` path; no Kubernetes Secret created for it; a class with `secretObjects` | read; none; refused |
| `deploy --cluster` naming another repository or a tag | `IMAGE_NOT_IN_REGISTRY` (and §7.2 if bypassed) |
| Registry redirect to another host | followed without `Authorization` |
| Stale chart then deploy | `KUBERNETES_CHART_STALE` |
| `promote` with a shared eval database and no `eval_resets` | `EVAL_DATABASE_SHARED` |
| `services down --cluster` with agents; without; `--purge` | refused; release gone, PVC kept; PVC and PV gone |
| `disable` with the deployer token refused but the API reachable | blocker recorded; gateway key revoked |
| `disable` with the API server unreachable | blocker recorded; revoke reported failed; key expiry recorded; once the API is back, `reconcile` revokes the key and reports any break-glass scale |
| Token expiry during a long `promote` eval | reopened; no failed case from expiry |
| Cost | recorded |

## 12. Relationship to #908 (GKE)

- **#908 G0 = K0** (§4.1), owned by #771.
- **#908 G1b = K5**: `cluster_service` and `kubernetes-secret://` are needed by every real cluster. #908
  consumes them; K5 now runs under the `operator` identity (§7.1).
- **#908 §6** uses the labels of §6.3 (`io.agentbreeder/stack`, `…/service`, `…/uses-*`).
- **Amendments to #908**, made in #946 while it is open, else a follow-up PR by lane C:
  - §10 "agents get no route to Postgres" → §6.3 (agents reach the services the grant names);
  - §9 "the deployer can read every Secret" → §4.3/§7 (no Secret read; owner GC); the §7.2 admission
    policy and the `operator` identity become G4 prerequisites;
  - §4 "one `kubernetes` state key per stack" → per-environment cluster state (§5.3): a stack may have a
    local environment and a cluster environment, each with its own state;
  - G1b's dependency on #772 becomes a dependency on K4/K5.
- **#908 G4 depends on K0–K8.** If D1 picks GKE for #774, one live run fills both records.

## 13. Delivery slices (sub-issues)

| Slice | Issue | What | Depends on | Verifiable without a real cluster |
|---|---|---|---|---|
| K0 | #771 | Provider interface, tagged binding, migration, injectable session, binding digest check (= #908 G0) | — | yes: existing tests, a `kind` run |
| K1 | #771 | `token` provider: dedicated mint file, sessions, CA pin, `connect token` with `blocked_cidrs` (§6.4) but without the canary (runs against a fake policy check until K2), kill-switch revoke in `finally` (local gateway; the cluster path is K5), `doctor` | K0 | unit tests with a fake `kubectl`; `kind` with `networking.apiServerAddress` on a non-loopback host address and that address in the API server certificate SANs |
| K2 | #771/#773 | `_objects` changes (owner-GC Secret, projected volume, fixed annotations); restricted `kind` accounts from `cluster up`; the verb audit; §7.1 Roles; §7.2 policies with defaulting, `cluster policy print`, paired probes against an `ab-grants` fixture | K0 | yes: `kind` with a node image ≥ 1.30, the Roles and the policy |
| K3 | #772 | `services render --cluster`: chart from the plan, files ConfigMaps, leak scan, hardening, golden charts for Northwind and the built-ins | — | yes |
| K4 | #772 | `services up/status/logs/down --cluster`: installer, staged tiers, init/bootstrap Jobs and their policies, in-process secrets, foreign-Secret check, image mirroring, lock ConfigMap, `ab-grants` takeover, `ab-baseline` (created by `connect`), the `kubernetes-secret://` binding form, per-environment state | K1, K2, K3, K8 | yes: on `kind` with services in-cluster (the node no longer joins the agents network) |
| K5 | #772/#773 | `operator` identity; `cluster_service` and operator-only `kubernetes-secret://` reads; bindings by reference in deploy; every §6.1 consumer; the cluster kill-switch revoke and key lifetimes; `EVAL_DATABASE_SHARED` (= #908 G1b) | K4 | unit tests with a fake forward; `kind` |
| K6 | #773 | NetworkPolicy derivation for agents and services, `uses-*` and `chart-service` labels, the `ab-canary` identity, `service/echo` probe mode and the canary, `reconcile` drift | K4 | yes: the `kind` tool ≥ 0.33 (its default CNI enforced policy on the 2026-11-10 run) plus a cluster without an enforcing CNI for the negative case |
| K7 | #773 | `secret-store://` through the CSI driver, per-agent class grants | K2, K4 | `kind` with the CSI driver and the Vault provider |
| K8 | #771 | Generic registry auth, redirect and realm rules, HTTPS digest check; `imagePullSecrets` and the `nodeSelector` arch pin in `_objects`; the `mirror/` path | K1 | a local TLS registry fixture |
| K9 | #774 | The live run (§11) and the record | K0–K8, D1 | no |
| K10 | #774 | `docs/kubernetes.md`, `command-center.md`, `docs/services.md` cluster section, changelog; claims limited to K9's record | K9 | — |

K3–K7 can be built and shown on `kind` first: the chart in `kind` instead of Docker is the local
rehearsal of the production topology, and the 2026-11-10 measurements are repeated there. Until K9 has
run, docs say "production Kubernetes: designed, not run"; `ARCHITECTURE.md`'s "one certified production
target" stays unclaimed; nothing on the website until release R (#836).

## 14. Owner decisions {#owner-decisions}

| | Question | Recommendation |
|---|---|---|
| D1 | Which real cluster for #774? (a) the #908 GKE cluster, one run for both; (b) a managed cluster on another cloud through `token`; (c) a self-managed k3s/kubeadm VM | (a), run through `provider: gke` **and** a second `token` connect to the same cluster, so the provider-neutral path is measured too |
| D2 | Chart: rendered per stack and environment (local directory), or a static published chart with per-stack values? | Rendered now; an OCI chart only after a pilot asks |
| D3 | Who updates the `operator` Role's `resourceNames` when operator-only Secret names change: the runbook by hand, or the installer (needs `create roles`/`rolebindings`, an escalation path)? | The runbook; AgentBreeder prints the Role and refuses operator-only reads until `connect` re-probes |
| D4 | Postgres in the chart (single replica, PVC) for the run, or an external managed database? | In-chart for #774; external database before any production claim (backup and HA are not ours) |
| D5 | Service secrets (LiteLLM provider keys) from an external store too? | Not in the first delivery: `ab-svc-*` Secrets readable only by the installer |
| D6 | MCP relay on the cluster in #770, or a follow-up epic? | Follow-up; `mcp_access` stays refused on clusters; Northwind does not need it |
| D7 | Give `reconcile` an optional Secret `list` (orphan detection)? | No by default; a separately minted read token behind `reconcile --secrets` later if pilots ask |
| D8 | Secret store for the P-A5 run | The D1 cloud's store if D1 is (a) (Secret Manager through Workload Identity, which #908 G3 needs anyway); else Vault in the cluster |
| D9 | Registry for the run and image retention | The D1 cloud's registry; immutable tags; keep the last 5 per agent; delete after the run |
| D10 | The imported agent | `examples/http-agent` with a hand-written manifest now; a third-party image when #757 resumes |
| D11 | Helm required for `--cluster`, or plain manifests applied with `kubectl`? | Helm 3 (issue scope; `helm template` for GitOps) |
| D12 | A production cluster environment's operator config: generated by `services up --cluster` from the repository's `stack.yaml` (the grant review is the checkpoint), or hand-written and only checked? | Generated for #774 (a dedicated run cluster); hand-written required before any production claim |
| D13 | Cluster evals reset the eval database as its owner. Allow it on the #774 run cluster with `eval_resets: allowed`, and require a separate staging environment for production `promote`? | Yes to both |
| D14 | Minimum Kubernetes 1.30 (for `ValidatingAdmissionPolicy` GA), with no mode that skips §7.2? | Yes; an older cluster is refused |
| D15 | Accept §7.2's residual (a deployer token can run code of its choosing inside any granted agent's container, with that agent's grants and binding values; not other deployments' own Secrets or gateway keys), or bind identity to something the deployer cannot choose: one namespace per agent, or one deployer account per agent? | Accept and state it for #774 (one stack, one team); per-agent namespaces as a follow-up when a pilot puts agents of different trust in one environment |

## 15. Cross-repo split and files

- **OSS:** `kubernetes.py`, `kubernetes_providers.py` (new), `cluster.py`, `promote.py`,
  `operator_config.py` (`provider`, `token`, `cluster_service`, `kubernetes-secret://` bindings,
  `secret_stores`, `secret_store_service_account`, `eval_resets`), `deployment.py` (`disable` revoke,
  admission refusals), `store.py` (migration), `secret_files.py` (`secret-store://`, projected volume),
  `gateway_keys.py`, `evals.py`, `services/` (`chart.py` beside `compose.py`, `engine.py` `--cluster`, per-
  environment cluster state), the `service.yaml` schema (`resources`), `manifest.py` and its JSON Schema
  (`secret-store://` values), `commands/cluster.py` (`connect`, `policy print`, `rbac print`),
  `commands/services.py`, `doctor.py`; `builtin_registry/service/echo` (a new version with `probe` mode);
  the shipped admission policy, baseline policy and Roles as package data; docs `docs/kubernetes.md` (new), `docs/services.md`,
  `docs/command-center.md`, `docs/commands/cluster.md`, `docs/commands/services.md`, `docs/manifest.md`.
  File ownership: `services/**` is lane A's; K3/K4 are coordinated with lane A.
- **Website:** nothing until release R (#836); then only K9's record.
- **Cloud (`agentbreeder-cloud`):** grep for the operator config shape and manifest secret values when K1,
  K5 and K7 merge; expected no code (no OSS pin, private console API). A `CLAUDE.md` note per side when
  K1 merges.

## 16. Review log

| Round | Reviewer | Lens | Head reviewed | Findings | Outcome |
|---|---|---|---|---|---|
| 1 | Claude Sonnet | architecture | r1 (uncommitted) | 2 Blocker, 6 Major, 9 Minor, 3 Nit: deployer can still read every Secret through pods it creates; Helm post-install hooks deadlock behind `--wait` and the bootstrap value has nowhere to go; cluster install reused the local stack's secret state; RBAC table did not match kubectl's verbs (`delete --wait`); bindings by reference undefined against `secret://` bindings and projected volumes; kill-switch order already exists, the real gap is the revoke skipped when the cluster is unreachable; chart missed `/files`, project-service builds, third-party image mirroring, `ops`; eval resets on the agent's own database and the gateway's other consumers; plus CSI layout, CA PEM storage, mint file pin, Helm 4 and PVC behaviour, status polling with the installer, cluster-side chart lock, slice order, operator config ownership, nits | Applied in r2 (§1, §2, §4.2–§4.4, §5.1–§5.5, §6.1–§6.2, §7, §12–§15; D12–D14 added) |
| 1 | Claude Sonnet | security | r1 (uncommitted) | 1 Critical, 5 High, 7 Medium, 6 Low, 5 Nit: deployer escalation by mounting any Secret, running as the installer, or minting a token Secret (needs an admission policy); `ab-svc-*` pre-creation and Secret deletion; mint pin did not cover the exec binary, external files, the check/use race or the server; `blocked_cidrs` holes (pod ranges, external IPs, API addresses, NodeLocal DNS); canary unsound (no positive control, no egress/`ipBlock`/union cases, wrong identity); bootstrap Job privileges; plus operator-only secrets on the deployer, port-forward and log exposure, registry redirects and realm, posture re-checks, CSI cross-agent access, missing threat rows, overclaims, rotation naming, rollback, proxies, TLS server name, `KUBECTL_*` | Applied in r2 (§2, §4.2, §4.3, §5.3, §5.4, §6.2–§6.5, §7, §8, §9, §11) |
| 2 | Claude Sonnet | architecture | r2 (uncommitted) | 0 Blocker, 4 Major, 9 Minor, 2 Nit: §7.2 left NetworkPolicy and Service unconstrained; Helm needs `update` on Secrets, in-release Jobs rerun, tier flags must accumulate; kill switch overclaimed when the API server is unreachable; CEL encoding of `ab-grants`, controller-created objects, probe ordering before `ab-grants` exists, missing reference paths; plus CSI vs projected wording, generation deletion while in use, path collisions, orphan semantics, state-key migration, operator token on the hot path, slice dependencies, `kind` fixtures, `blockOwnerDeletion` | Applied in r3 (§2, §4.2, §4.3, §5.1, §5.3, §5.4, §6.2, §6.3, §7, §11, §13) |
| 2 | Claude Sonnet | security | r2 (uncommitted) | 0 Critical, 3 High, 5 Medium, 6 Low, 2 Nit: deployer could create widened NetworkPolicies, hijack Services and delete chart objects; agent identity is a deployer-chosen label (cross-deployment Secrets, overclaims in P-A2/T3); volume and env allow-list incomplete (PVC, `envFrom`, init containers, PSA enforce); VAP fail-open defaults and `ab-grants` integrity; deployer `pods/log`; Docker client follows the realm itself, redirect SSRF, byte digests; exec plugin residuals; installer breadth and canary identity; plus pre-create DoS, quota, canary selectors, `HOME`, rerun after CNI changes | Applied in r3, partly (NetworkPolicy shapes, canary rules and `pods/log` completed in r4) |
| 3 | Claude Sonnet | architecture | r3 (uncommitted) | 0 Blocker, 5 Major, 9 Minor, 3 Nit: pod labels not an exact set (chart selector spoofing); `imagePullSecrets` contradiction; own-Secret mount can be tied to the Deployment's name, removing the cross-deployment residual; key `duration` with no renewal; `connect` reads objects no identity may read; plus `ab-grants` keys and empty lists, unconstrained pod fields, egress shapes and ports, canary verbs, same-ID retry race, minting text, Secret creation order, grant writer and authoritative digest, slice dependencies | Applied in r4 (§2, §4.2, §4.3, §5.2, §5.4, §6.3–§6.5, §7, §11, §13, §15) |
| 3 | Claude Sonnet | security | r3 (uncommitted) | 0 Critical, 3 High, 5 Medium, 6 Low, 2 Nit: pod labels spoof chart Services and peers; a pod without a NetworkPolicy is unrestricted (no baseline); canary rules far thinner than the deployer's; plus NetworkPolicy shapes not precise, pod-spec allow-list incomplete (deny by default instead), probes can pass on RBAC denials, overclaim of "no Secret read" vs obtaining values, residual list incomplete, quota scope, DELETE per item, realm before each push, DNS rebinding and daemon trust store, tool hash per exec, reserved `ab-probe` | Applied in r4 (§2, §4.2, §4.3, §6.3–§6.5, §7, §8, §9, §11) |
| 4 | Claude Sonnet | architecture | r4 (uncommitted) | 0 Blocker, 3 Major, 6 Minor, 3 Nit: the policy ignored API-server defaulting (would refuse AgentBreeder's own Deployment); `connect` needed `ab-baseline`, `ab-grants` and the echo mirror before `services up` wrote them; chart Jobs had no egress under the baseline; plus identity per `connect` read, required range flags, `nodedns` key, `blocked_cidrs` size with node IPs, metadata/mount/probe sub-keys, slice dependencies and `_objects` changes, canary delete-and-recreate, §11 probe coverage | Applied in r5 (§2, §4.1, §4.2, §4.4, §5.2, §5.4, §6.3–§6.5, §7.1–§7.4, §11, §13) |
| 4 | Claude Sonnet | security | r4 (uncommitted) | 0 Critical, 1 High, 5 Medium, 7 Low, 4 Nit: canary could probe any address with a free `ipBlock` and read results; annotations, finalizers and Deployment spec keys open; residual understated (env and own-Secret files give arbitrary code in an agent container); probe `host`, `hostPort`, unimplementable `LimitRange` check; absent agent entries and a regex built from a label; baseline ordering and `connect` circularity; plus Service spec keys, mixed grants, owner reference shape, Job egress, `nodedns`, `except` semantics, `tmp`/`downwardAPI`/digest normalisation, one policy per kind | Applied in r5 (§2, §4.4, §6.3–§6.5, §7.1–§7.4, §9, §11, §14 D15) |
| 5 | Claude Sonnet | architecture | r5 (uncommitted) | 0 Blocker, 1 Major, 6 Minor, 1 Nit: the canary Pod is validated after built-in mutation (service account, default tolerations, priority) and would be refused by its own template; plus deployer probes refused by RBAC first, per-identity branches and canary policy labels, the canary's other-port case cannot detect non-enforcement, upstream ingress for Jobs, deployer `nodes list` use, probe constants | Applied in r6 (§4.3, §6.3, §6.5, §7.1, §7.2, §7.4, §11) |
| 5 | Claude Sonnet | security | r5 (uncommitted) | 0 Critical, 1 High, 7 Medium/Low: an orphan-cascade delete let the deployer keep a victim's Secret and remount it from a recreated same-name Deployment; plus the residual's "own image" (any digest in the repository), `ab-deny-other` scope and cluster-scoped requests, public scanning by the canary (agent-equivalent), the `mirror` name, deployer `nodes list`, baseline on a non-empty namespace | Applied in r6 (§4.3, §7.1, §7.2, §7.4, §11): foreground-only deletes and `blockOwnerDeletion` |
| 6 | Claude Sonnet | architecture | r6 (uncommitted) | 0 Blocker, 0 Major; small items: `_delete`'s poll still proves the Secret is gone because the Deployment persists, foreground delete and poll bounds | APPROVED; applied in r6 (§4.3) |
| 6 | Claude Sonnet | security | r6 (uncommitted) | 0 Critical, 0 High; Medium/Low: state the fail-closed CEL form for a body-less DELETE, make the K2 fixture blocking, owner UID unchecked (denial of service only), `OwnerReferencesPermissionEnforcement` is not the mechanism | APPROVED; applied in r6 (§7.2) |
