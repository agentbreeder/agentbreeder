# `agentbreeder up`: local command center, then the same release on Kubernetes

- Status: **r3, implemented and run (§15, §16).** No merge without CI, the different-model review
  and the owner's approval.
- Epic: #783. Slices: §10. Related: #775 (control center), #770/#771/#773/#774 (Kubernetes),
  #718 (older Studio draft, superseded).
- Owner decision 2026-11-10: the first non-local target is **Kubernetes, verified on a local
  cluster**. This replaces GCP Cloud Run in the 2026-11-09 issue text.

## 1. Problem

After `pip`/`uvx`/`npx`, using AgentBreeder means a dozen CLI steps across `example`,
`services up`, `build`, `release`, `status` and `audit`. Nothing shows the state of a stack, a
release or a deployment in one place. Local Docker is the only target that exists: a manifest can
say `target: kubernetes`, but the engine refuses it.

## 2. Goal

`agentbreeder up` starts a loopback command center. From it one user can:

1. onboard an agent project (Northwind first);
2. review and start the project's backing services, and see the registries;
3. build, and run the eval-gated release to local Docker (Northwind stays 61/61);
4. deploy **the same evaluated image** to a Kubernetes cluster (a local `kind` cluster is the only
   one verified);
5. see deployments, the audit chain and drift, and remove what it started.

## 3. Constraints

- **One engine.** The UI starts the same CLI commands a user would type. Identity gate, policy,
  approval, audit and the eval gate cannot be skipped from it. `agentbreeder deploy` stays the
  contract.
- **Loopback, single user.** Multi-user, OIDC login and RBAC stay in #775/#742.
- **Claim rule.** Docs say "verified on a local kind cluster" and nothing about production
  Kubernetes until #770's exit test runs. Cloud Run is not part of this epic.
- **No new runtime dependencies.** `pip install agentbreeder` keeps exactly pydantic and PyYAML.
  The server is the standard library; the UI is static files; `kubectl` and `kind` are external
  tools found on `PATH`, as `docker` is today.

## 4. Non-goals

Helm chart (#772), imported agents and real-cluster certification (#774), multi-replica or rolling
updates, Ingress or any public agent endpoint, authenticated invocation (#742), UI approvals (§6.4),
MCP relays or the egress sidecar on Kubernetes (refused, §7.5), a server-side build, hosted use.

## 5. Architecture

```
Browser ── 127.0.0.1 only ──► center/server.py (ThreadingHTTPServer)
  session cookie, Host + Origin + header checks        │
                                                       ├─ center/store.py   center.sqlite3 (projects, jobs, job log)
                                                       ├─ center/jobs.py    one job at a time: `python -m agentbreeder <fixed argv>`
                                                       └─ reads: `python -m agentbreeder … --json` with a timeout
                                                                    │
                  the unchanged CLI ──► DeploymentService ──┬─► Docker (runtime.py)
                                                            └─► Kubernetes (kubernetes.py, kubectl)
```

- **Why child processes.** A job is literally the CLI, so the UI cannot drift from it and never
  touches `deployments.sqlite3`. `Store` has one connection and a non-blocking lock; a child
  process that gets "busy" reports it. A test asserts that nothing under `center/` imports `Store`,
  `DeploymentService` or `release`.
- **Why no framework.** A loopback tool with about 15 routes does not justify adding a web stack to
  every install. No bundler means no Node step in the wheel and nothing to audit in `node_modules`.
- **Host code safety.** A project is untrusted until built. Children run as
  `sys.executable -I -m agentbreeder`, with the center directory as cwd and absolute paths, so a
  planted `yaml.py` in a project is never imported on the host. A test plants one.
- **Argv is never built from free text.** Each job kind has a builder; user input is limited to a
  registered project ID, an environment name matching the manifest pattern, and a deployment ID
  matching `[0-9a-f]{32}`.

## 6. Command center

### 6.1 `agentbreeder up [DIR] [--environment dev] [--port 8900] [--no-browser] [--yes]`

1. Runs the `doctor` checks and prints any failure.
2. Opens `~/.agentbreeder/center/` (0700). Marks any job left `running` as `interrupted`; it is never
   resumed.
3. Binds `127.0.0.1:<port>`. There is no flag for another address. A busy default port falls back
   to an ephemeral one; an explicit `--port` that is busy is an error.
4. Registers `DIR` if it holds an `agent.yaml` and `stack.yaml`, and prints a one-time login URL,
   opening it unless `--no-browser`.
5. With `--yes`, and only with a project, starts the services job without waiting for the UI. It is
   the same confirmation as `services up --yes`.

Ctrl-C stops the server and sends SIGINT to a running job. Services and deployments keep running,
and the shutdown message says so and names `services down` and `remove`.

### 6.2 Authentication and request checks (loopback)

Carried over from the reviewed Studio draft (#718 §5.3):

- One-time login token: 256-bit, single use, expires in 5 minutes, carried in the URL fragment.
  The browser is opened on a 0600 file in the center directory that redirects, so the token is
  never in browser argv (visible to every local user). A second redemption prints a terminal
  warning. Enter in the terminal mints a new link. The expired page names that fix. Only the
  token's hash is held in memory.
- Session: a per-tab secret in `sessionStorage`, sent in `X-AgentBreeder-Session`, 8 h absolute and
  1 h idle. Not a cookie: browsers send cookies to every port on `127.0.0.1`, including agent
  endpoints and stack services (code review, §14).
- Exact `Host` allow-list (`127.0.0.1:<port>` only) against DNS rebinding. No CORS.
  `Sec-Fetch-Site`, when sent, must be `same-origin` on every `/api` route.
- Every non-GET route needs `X-AgentBreeder-Center: 1`, `Content-Type: application/json`, a body of
  at most 64 KiB and a same-origin `Origin`.
- `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self';
  connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
  plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
  `Cache-Control: no-store`.
- The UI writes server text only through `textContent`.

**The trust statement is shown in the UI:** the local Docker admin has full control; the gate
prevents mistakes and policy drift, not a local admin.

### 6.3 Secrets

Provider keys (`GEMINI_API_KEY`, `TAVILY_API_KEY`, optional `OPENROUTER_API_KEY`) are entered in
write-only fields (`type=password autocomplete=off`). The server writes them to a 0600 env file
used as `--env-file` for that one job and deletes it when the job ends, so Docker grandchildren
never inherit them. `services up` stores them as it does for
a CLI user (0600 files in the stack state). The center never returns a key and redacts the value,
its base64 form and its URL-encoded form before a log line is stored, a best-effort defence. It
keeps the last 100 jobs' logs. Which keys a stack needs, and whether
each is already stored, comes from `services plan --json` (§8), never the values.

### 6.4 Approvals

The UI lists a pending approval and shows the command for it. It has no approve button: a second
person's verified identity is exactly what a single-user loopback session cannot provide.

### 6.5 Grant review before services start

`services plan DIR --environment E --json` (new; a POST in the API, because it records the chosen
host ports and copies content-addressed files into the stack state) returns the grant, its digest,
its text and the needed keys. The UI renders sections, a line diff against the last accepted grant
and the raw text. Confirming starts `services up --yes --expect-grant <digest>`, which is checked
unconditionally and refuses with `GRANT_CHANGED`. `grant_digest` excludes MCP tool pins, so the UI
refuses a stack with `mcp:` entries in this epic and names the CLI command. `up --yes` prints the
grant.

### 6.6 API

| Route | Purpose |
|---|---|
| `POST /api/session` | Exchange the login token for a cookie |
| `GET /api/overview` | Docker, kubectl and kind presence, version, current job |
| `GET/POST /api/projects` | List; onboard `{example}` or `{path}` |
| `POST /api/projects/:id/plan` | The grant and needed keys |
| `GET /api/projects/:id/services` | `services status --json` |
| `GET /api/projects/:id/registry` | `registry list --json` plus `registry verify --json` |
| `GET /api/projects/:id/deployments` | `list --json` |
| `GET /api/projects/:id/audit` | `audit list --json`, `audit verify --json` |
| `POST /api/projects/:id/jobs` | Start `{kind}` |
| `GET /api/jobs/:id?after=N` | Job state and new log lines (polled every 2 s) |
| `POST /api/jobs/:id/cancel` | Request cancel (§6.8) |

A second job while one runs gets 409 and the running job's ID.

Job kinds: `services-up`, `services-down`, `build`, `release`, `rollback`, `cluster-up`,
`cluster-down`, `promote`, `remove`, `reconcile`. `services-down` never deletes volumes from the UI.

### 6.7 UI

Static `index.html`, `app.js`, `style.css` from the package. One screen with a left stepper showing
each step's status, never gating navigation, and a right panel, plus a job log drawer
(`role="log"`). A project switcher sits in the header. A returning user with a running stack lands
on Deployments. During a job, panels show last-known data with a "refreshing after job" note, and
actions are disabled with a link to the running job. Remove, rollback, services-down and cluster-down
use a confirm dialog that says what is deleted and kept. Promote states its cost first. Exit 3 shows
"waiting for approval" with a copyable command. Missing tools appear with the fix and disable the
steps that need them. Status is never signalled by colour alone.

1. **Project.** Empty state: "Onboard the Northwind example". Or a path to an existing project.
2. **Services.** Key fields, the grant text, Confirm and start. Afterwards, each service's state and
   port. Registry versions and the verify result sit here too.
3. **Build and release.** Cost and time estimate for Northwind, measured (about $0.24, about 4
   minutes). Stage messages from the CLI, and the result as `n/61`. On failure, a banner: "Production unchanged", and the failing cases.
4. **Deployments.** Target, state, image, identity check (the `compliance` field), remove. Identifies the local endpoint, labelled
   "local, unauthenticated".
5. **Kubernetes.** Tool checks with the install command for anything missing, Connect local cluster,
   the grant change, Deploy this release to the cluster, and result.
6. **Audit.** Events, Verify, and the exact limitation text from `audit.py`. Never "tamper-evident".

Colours follow `website/brand.css`, copied by hand into `style.css` so the package needs no build
step. Dark and light by `prefers-color-scheme`. Keyboard reachable, visible
focus, `aria-live="polite"` for stage changes and `role="alert"` for failure, usable at 360 px,
`prefers-reduced-motion` honoured.

### 6.8 Cancel and interruption

Jobs start with `start_new_session`, so a terminal Ctrl-C reaches only the server. Cancel sends one
SIGINT and never escalates during `release` or `promote`. `release` and `promote` defer SIGINT across
remove → deploy → restore, so an interrupt cannot leave no production. `interrupted` jobs tell the
user to check Deployments for a leftover candidate and remove it; Re-verify runs `reconcile`.

## 7. Kubernetes target

### 7.1 Runtime

`kubernetes.py` has the methods the lifecycle already calls on `Docker`: `authorize`, `inspect`,
`image_present`, `create`, `start`, `remove`, `endpoint`, `healthy`, and `context`. `inspect` returns
the same `Id` and `State.Running` shape. `DeploymentService` chooses per manifest or stored record
in `deploy`, `_run`, `remove` and `_stop_blocked`. `Boundary`, `_ensure_boundary` and `rollback` stay
Docker-only: the boundary is refused on Kubernetes and `rollback` acts on the local journal. It shells out to
`kubectl`, like the Docker runtime, with a fixed `--context` and `--namespace`, timeouts, and
stdin for every object so that secret values are never in argv.

### 7.2 Which cluster

The operator config gains, per environment:

```yaml
kubernetes:
  kubeconfig: /Users/me/.agentbreeder/stacks/<id>/kubeconfig   # dedicated, 0600
  context: kind-agentbreeder
  cluster_uid: 3f0c...            # metadata.uid of kube-system
  namespace: agentbreeder
  registry: localhost:5001/agentbreeder
  blocked_cidrs: [...]            # required; `cluster up` derives them
```

`KUBECONFIG` and `current-context` are never used. The kubeconfig must be 0600, use static
credentials (no `exec` or `auth-provider`) and point at a loopback API server, which limits this
preview to local clusters and keeps a cluster's credential plugin from running. `kube-system`'s UID
must match; it identifies a cluster, it does not authenticate one. The namespace must already exist
with `app.kubernetes.io/managed-by=agentbreeder` and `pod-security.kubernetes.io/enforce=restricted`.
The record's `context` stores `context|uid|namespace|kubeconfig`, and later operations use it.

### 7.3 Objects

All are labelled `io.agentbreeder/deployment=<id>`, and named `agentbreeder-<id>`:

- **Deployment**, 1 replica, image by digest only, and annotation `io.agentbreeder/spec=<digest>`.
  Pod: `runAsNonRoot`, UID/GID/fsGroup 65532, `seccompProfile: RuntimeDefault`,
  `automountServiceAccountToken: false`. Container: `readOnlyRootFilesystem`, no privilege
  escalation, all capabilities dropped, limits 512Mi and 1 CPU matching Docker, `/tmp` a 64Mi memory
  `emptyDir`, a readiness probe on `health_path`.
- **Secret** (immutable), from the same `SecretMount` list Docker receives, mounted read-only (0440, fsGroup) at
  the same path, with the same `<ENV>_FILE` variables. The minted gateway key takes this path too.
- **Service**, `ClusterIP` only.
- **NetworkPolicy**: no ingress; egress to kube-dns, and to `0.0.0.0/0` except `blocked_cidrs`.
  `cluster up` derives those: pod and service CIDRs, node addresses on every network, the `kind`
  Docker network (which holds the registry) and the metadata address. The stack's agents network
  stays reachable, which is the same boundary local agents have. kindnet enforces this only on a
  recent `kind`, so `cluster up` needs `kind` ≥ 0.33. The node on the agents network also exposes
  its API server and kubelet ports to local agents; both require credentials. Local-only.

Objects go to `kubectl create` as one stream on stdin. `apply` is not used because it copies Secret
data into an annotation. On partial failure everything with the deployment label is deleted before
the error is raised. Secrets are unencrypted in a `kind` etcd; the trust statement says so.

`inspect` verifies the deployment label, the spec annotation and the image, as `Docker.inspect`
verifies labels. `remove` deletes every labelled kind, waits for pods to go, and is verified.

### 7.4 Endpoint and health

The stored endpoint is `kubernetes://<namespace>/<service>:<port>`; there is no public address.
Health is `rollout status` (180 s) and then a real HTTP 200 from `health_path` through an ephemeral
loopback `kubectl port-forward`. `agentbreeder forward ID [--port N]` opens that forward for a
person; evals use the same mechanism. Failure reasons come from pod status fields only (for example
`ImagePullBackOff`).

### 7.5 What stays refused

`_deploy_requirements` denies Kubernetes deployments that use `mcp_access` or runtime enforcement,
with a specific code. A silently weaker boundary is worse than a refusal.

### 7.6 Record identity

One name can now be active once **per target**: `Store.active(name, target="local")`, filtered with
`json_extract(manifest,'$.target')`. There is no schema change. `disable NAME` resolves through either
target, then acts on every record of the principal. `enable` keeps its local context: it only clears
a blocker and starts nothing.

### 7.7 CLI confirmation flag

`deploy` and `remove` take exactly one of `--local` or `--cluster`, and it must match the target.
`reconcile` and `disable` keep `--local` and act on every record in the state, so the kill switch
stops a cluster workload as well.

### 7.8 Local cluster

`agentbreeder cluster up --stack DIR --environment E [--yes]`:

- needs `kind` and `kubectl`, and otherwise stops with install instructions;
- creates `kind` cluster `agentbreeder` from a pinned node image digest, and a registry container
  `agentbreeder-registry` from a pinned digest, bound to `127.0.0.1:5001`, configured as the
  containerd mirror;
- creates and labels the namespace, connects the node to the stack's agents network (so pods
  reach the same service names and URLs as a local container), records the block in the stack
  state, and re-runs `services up` with the same confirmation rule. The grant shows the cluster.

With `--yes` it accepts the grant change; without it (the UI) the user reviews it through `services
plan` and `services up --expect-grant`. `cluster status` reports it; `cluster down` removes only those named resources and refuses while
the state has an active cluster deployment. Because `services down` removes the agents network,
it refuses while a cluster deployment is active and disconnects the node otherwise, and `services up`
reconnects it. The operator writer emits `targets: [local, kubernetes]` and the block; the operator
digest changes, and approvals bound to the old digest must be requested again.

**Entry gate for this slice:** the §15 spike. If pods cannot resolve the stack's names, the named
fallback is a CoreDNS `hosts` block written by `cluster up`.

## 8. Same release on the cluster: `agentbreeder promote`

`agentbreeder promote agent.yaml --stack DIR --environment E --to kubernetes [--skip-cluster-evals]`

1. Take the release lock.
2. Require the audited `release/promoted` event for this image. Extend that event with the
   production manifest digest, eval-set hash, pass/total and threshold. Require the active local
   production to be `ready` with that digest and the threshold to be at least `agent.yaml`'s, so
   `release --threshold 0` is not evidence. There is no rebuild.
3. Tag the image ID and push it to `kubernetes.registry`, select the `RepoDigests` entry for that
   repository, then re-read the manifest from the registry and compare its config digest with the
   evaluated image ID. The `127.0.0.1:5001` registry is unauthenticated, as the docs state.
4. Write `deploy/<name>.kubernetes.deploy.yaml`: the production manifest with `target: kubernetes`
   and `image: repo@sha256:…`, and nothing else changed.
5. Same `preflight`. Deny exits 2; a required approval exits 3 and says how to deploy after it.
   An identical ready deployment is a no-op.
6. Journal the previous cluster deployment in `releases/<name>.kubernetes.json`, separate from the
   release journal, and deploy through the same `DeploymentService.deploy`. `release --rollback`
   stays local, and the UI says the cluster keeps its release.
7. Run the eval set against the live cluster deployment through the port-forward (61 cases, about
   $0.24); this resets the shared eval fixtures, so a release must not run alongside it (the
   release lock covers it). Below threshold: remove it, restore the previous one, exit 5. The UI
   says "the cluster deployment was replaced and rolled back", not "production unchanged". `--skip-cluster-evals` is recorded
   in the audit event. It does not skip the release gate, which has already passed.

Audit: `promote` events carry the local image ID, the registry digest and the eval run IDs, and every
deploy has its normal gate record.

## 9. Acceptance criteria

1. `agentbreeder up` with no extra dependencies serves the UI on loopback only. A token is single
   use and expires. A bad `Host`, a cross-origin POST and a POST with no header all get 403.
2. From a source checkout's command center API, Northwind onboards, services start after a grant review, and the
   release passes **61/61** (at most one retry per case) and promotes.
3. `promote` deploys that image by digest to a local kind cluster and passes **61/61** there under
   the same retry rule, with
   pods running as 65532 under the `restricted` Pod Security level.
4. `disable` stops the cluster workload. `reconcile` reports it. `remove` leaves no labelled object.
   The minted gateway key is revoked.
5. NetworkPolicy measured on the pinned `kind` version, with a negative control (the same probe
   allowed without the policy): an in-cluster pod cannot reach the agent; the agent cannot reach
   the API server (service and node address), kubelet, registry, metadata or another pod; it can
   reach the gateway.
6. No value from `known_secrets()` (`services/state.py`) appears in `ps` argv samples, the job log,
   an API response, `kubectl get deploy -o yaml` or the browser's storage. Grepped on the live run.
   Redaction of an echoed value is best effort.
7. A wrong `cluster_uid`, an unlabelled or non-`restricted` namespace, an unsafe kubeconfig and a
   `--local`/`--cluster` flag that does not match the target are refused before any change.
8. Tests: ≥80% on changed files, the `/launch`-equivalent gate green; a recorded manual run for the
   live paths. Existing Docker runtime argv tests pass unmodified.
9. Docs and website claim only "verified on a local kind cluster", and ROADMAP records how this
   relates to #775 and #770.

## 10. Slices

| # | Issue | Slice |
|---|---|---|
| 1 | #771 + #773 (scoped to local) | `kubernetes.py`, target routing, secrets, NetworkPolicy, `--cluster`, `forward` |
| 2 | new | operator `kubernetes` block, `cluster up/status/down`, `services plan`, `--expect-grant` |
| 3 | new | `promote` |
| 4 | new | `list`, center server, jobs, API, `up` |
| 5 | new | UI |
| 6 | new | Live Northwind run, docs, website, roadmap |

## 11. Relationship to #775 and #770

- **#775** remains the multi-user control center with a versioned API, OIDC and RBAC. This epic's
  API is private to the local UI and unversioned, and may be replaced by #775's. It reuses its
  security model, not its contract.
- **#770** remains the production Kubernetes epic. This epic delivers the deployer (#771) and the
  secrets and network policy (#773) for a local cluster. #772 (Helm) and #774 (exit test with an
  imported agent on a real cluster) stay open there. #770's epic is not closed by this one.

## 12. Cross-repo

| Repo | Change |
|---|---|
| `agentbreeder` | Everything above |
| `agentbreeder-cloud` | No code. Grep for `operator_config` schema use; record in its `CLAUDE.md` that `Environment.kubernetes` exists and that the center API is private |
| `website/` | `what-ships` and a docs page, only after §9 runs |

## 13. Risks

- **Stack services are in Docker, not the cluster.** Only `kind`'s node on the stack's network makes
  this work. It is a local demonstration of the deployer, not a production topology. Stated in docs.
- **`kubectl port-forward` dropping during a long eval run.** The eval fails with a clear error, and
  production is restored.
- **An old `minikube` is on the development machine.** `kind` is the verified path; nothing depends
  on minikube.

## 14. Review log

r2, 2026-11-10, two Fable reviews (architecture and integration; security and UX) of r1. Each
finding was checked against the code before folding in.

- Critical: planted `yaml.py` runs on the host through `python -m` (§5).
- High: login token in browser argv (§6.2); NetworkPolicy gaps (§7.3); `cluster_uid` is not
  authentication, so a dedicated local-only kubeconfig (§7.2); runtime coupling (§7.1); evidence
  chain and `--threshold` (§8); journal collision (§8); SIGINT safety (§6.8); the internal network and
  `services down` (§7.8, §15); returning user, panels during jobs, login recovery (§6.2, §6.7).
- Medium: secrets in child env (§6.3); session lifetime (§6.2); digest re-read (§8); `restricted`
  enforcement and `create` over `apply` (§7.2, §7.3); operator writer (§7.8); `active` with two rows
  (§7.6); grant digest and MCP pins (§6.5); live cluster evals (§8); UI wording.
- Adapted, not taken as written: per-IP allow-listing of stack services (Compose IPs change; the
  agents network stays the boundary); a probe pod inside `cluster up` (replaced by a pinned minimum
  `kind` and the measured AC5).
- Also found while implementing: `Store.get` drops the manifest, so image ownership is checked
  through a Deployment annotation.

Code review 2026-11-10, two Fable reviews (security boundary; engine correctness), all findings
checked and fixed: interrupted promote reporting success; release evidence lost to an interrupt;
Secret orphaned on a failed create; mixed cluster bindings; audit scan limit; `cluster down` on a
machine-wide cluster; `cluster-up` accepting an unreviewed grant (it now goes through `services-up`);
session cookie replaced by a header; request-supplied `env_file`; kubeconfig `proxy-url`; stored
environment winning silently; cluster and registry adopted by name; UI claims; input parsing.
A re-review then found, and the fixes cover: interrupts during the cluster deploy step; the eval set
resolved after the deploy; ownership by a node label set at creation; stale `configured`; the
resumed run's previous deployment; skipped-evals promotes satisfying later ones.

## 15. Network spike

Passed 2026-11-10 on kind 0.33 and Kubernetes 1.37: a pod reached a service on an `internal: true`
Docker network by its bare name through the node. No CoreDNS fallback is needed.

## 16. Acceptance run (2026-11-10, macOS, Docker Desktop, source checkout)

| AC | Result |
|---|---|
| 1 | Loopback only; token single use; bad Host and missing header 403. Cross-origin 403 in tests |
| 2 | Through the API: onboard, grant review, services, release **61/61**, promoted |
| 3 | `promote` by digest, **61/61** on the cluster; UID 65532; `restricted`; privileged pod refused |
| 4 | `disable` stopped both targets; `reconcile` reported `PRINCIPAL_BLOCKED`; `remove` left nothing and revoked the key |
| 5 | With a no-policy control pod: API server, kubelet and registry open to it and blocked to the agent; gateway reachable; pod to agent blocked. Metadata inconclusive on kind |
| 6 | 15 secrets, six surfaces, none found |
| 7 | Tests only |
| 8 | See the PR |
| 9 | Docs claim this run only |

The walkthrough ran a second time after the code review, from an empty command center through
the grant review for the cluster: 61/61 on both targets, then remove, `cluster down` and `services
down` as jobs. The cluster ownership label was checked live after a further review round.

Not run: clicking through every panel (headless Chrome rendered sign-in, Project and Deployments),
Linux, a published package. `up` is not in `3.0.0a1`, so `npx agentbreeder up` needs the next preview.
