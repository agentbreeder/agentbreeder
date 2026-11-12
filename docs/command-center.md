# Local command center and the Kubernetes target (preview)

`agentbreeder up` starts a web UI on `127.0.0.1` for one person on one machine. From it you onboard
an agent project, start its backing services, run the eval-gated release to local Docker, and deploy
that same image to a local Kubernetes cluster.

**What has been run.** From a source checkout on macOS with Docker Desktop, on 2026-11-10, against
`kind` 0.33 and Kubernetes 1.37: Northwind onboarded through the command center's API, services
started after a grant review, the release passed 61/61, and `promote` deployed the image by digest
and passed 61/61 on the cluster. Remove, the kill switch, `reconcile`, network policy and secret
handling were measured on that run (below). After code review the whole walkthrough ran again from
an empty command center, 61/61 on both targets, through full teardown. A headless browser rendered
sign-in, the Project page and the Deployments page; the other panels were driven through the same
API but not clicked through.

**From the published `3.0.0a2`**, on macOS with Docker Desktop on 2026-11-11, in a clean sandbox
(fresh `HOME` with `~/.docker` linked in for the Docker context, empty package caches, no checkout; Docker images on the host were not cleared): `up` started through both uvx and npx. Through
`npx agentbreeder@3.0.0-alpha.2 up`, driven by a script through the command center's API as the UI
does, with no browser,
Northwind onboarded from the packaged example, services started after the grant review, the release
passed 61/61, `kind` 0.33 was connected and its grant change reviewed, and `promote` passed 61/61 on
the cluster. Both deployments, the cluster and the services were then removed through the same API,
leaving no containers running, and no provider key value appeared in any job or server log. It has
not run on Linux, and **no production cluster has been tested**.

## Run it

```
npx agentbreeder@3.0.0-alpha.2 up                               # needs Node 18+ and uv
uvx --from "agentbreeder[eval]==3.0.0a2" agentbreeder up        # needs uv
```

Each prints a one-time link and opens it. Pin the version: a bare `pip install agentbreeder` installs
the unmaintained 2.x. With pip, install `"agentbreeder[eval]==3.0.0a2"` in a virtual environment
and run `agentbreeder up` (on `3.0.0a2` this path has run only in the TestPyPI rehearsal, with
`--no-browser`); from a checkout, `pip install -e ".[eval]"`. The Northwind eval gate
needs the `[eval]` extra, which the uvx and npx commands include. The cluster steps also need
`kind` 0.33 or newer and `kubectl` on `PATH`.

Follow the steps in the left column. If the browser did not open or the link expired (5 minutes,
single use), press Enter in the terminal for a new one. `--port`, `--no-browser` and
`--environment` are available; with a project directory, `agentbreeder up DIR --yes` also confirms
the grant and starts its services. Stopping the server leaves services and deployments running; use
`agentbreeder services down` and `agentbreeder remove`.

The same steps work without the UI, with `agentbreeder` on your `PATH` (a pip install or a
checkout; `uv tool install "agentbreeder[eval]==3.0.0a2"` also adds the command, but only
`--version`, `doctor` and `quickstart` have been run that way). `npx` and `uvx` do not install that
command; with them, repeat the prefix for each command. These commands ran from a checkout on
2026-11-10; on `3.0.0a2` they ran only as command-center jobs.

```
agentbreeder services up DIR --environment dev --env-file ~/.env
agentbreeder release DIR/agent.yaml --stack DIR --environment dev
agentbreeder cluster up --stack DIR --environment dev --yes      # needs kind >= 0.33 and kubectl
agentbreeder promote DIR/agent.yaml --stack DIR --environment dev --to kubernetes
agentbreeder list --stack DIR --environment dev
agentbreeder forward ID --stack DIR --environment dev            # loopback port to a cluster deployment
agentbreeder remove ID --cluster --stack DIR --environment dev
agentbreeder cluster down --stack DIR
```

`deploy` and `remove` take `--local` or `--cluster`, which must match the manifest's target.

## How it stays the same engine

Every action starts the CLI in a child process, with a fixed argument list. The UI does not open the
deployment database. The identity gate, policy, approval, audit and the eval gate run exactly as
they do for a CLI user.

- **The eval gate is orchestration.** Someone with deploy rights can still run a plain `deploy`.
  `promote` refuses any image without an audited release at the threshold in `agent.yaml`, but that
  does not stop a person deploying a manifest by hand.
- **Approvals are not in the UI.** A second person's verified identity is what a single-user session
  cannot provide. A job that needs one shows the command.
- **Stacks with MCP servers** are refused from the UI, so their tools are reviewed with the CLI.

## What protects the local server

It binds `127.0.0.1` only, checks the exact `Host`, needs a per-tab session from the one-time link
(a header, because browsers send cookies to every port on `127.0.0.1`), and on every request a
custom header, same-origin `Origin` for writes, JSON bodies and a strict content
security policy. Provider keys go to a private file for the one job that needs it. Job logs go through
the CLI's redactor (`agentbreeder.redaction`; on `main`, not in a published preview): a key's value,
each line of a multi-line value, and its base64, URL-encoded and JSON-escaped forms become
`[redacted]`; another encoding of a value echoed by a tool would not be caught.

### Console foundation (#815): on `main`, not in a published preview

- **One route table.** Every API request passes the same checks, in this order, before any handler
  runs: the exact `Host`; the `X-AgentBreeder-Center` header and `Sec-Fetch-Site: same-origin`
  (so a page on another `127.0.0.1` port, which is same-site, is refused); for any method other
  than `GET`, the console's own `Origin` and `Content-Type: application/json`; the session (only
  sign-in itself skips it). Only then does an unknown path get `404` and a method no route uses
  `405`, and the route's body cap apply (64 KiB for JSON, up to 1 MiB for editor payloads;
  larger is `413`). A job-log poll does not keep an idle session alive.
- **One reply shape.** Every API reply, including the server's own errors for a malformed
  request, is `{"data", "error", "stale"}`. `error` is `null` or `{"code", "message"}`; when a
  CLI read fails, the console forwards only the CLI's final `[CODE] message` line, never other
  stderr. (Job logs still show a job's whole output, redacted as described below.) `stale` marks the last good data shown after a
  failed read.
- **Static files** are an allow-list built at start from the installed `center/static/` tree:
  regular files of known types (HTML, JS, CSS, `woff2` fonts, plain text such as the font
  licence, SVG and PNG), no dotfiles, no links. Nothing else is served.
- **Projects only from directories you alone control.** Adding a project, onboarding an example
  and every job start refuse a project directory, `agent.yaml` or `stack.yaml` that another user
  owns or that is group- or world-writable, and any parent directory up to `/` owned by someone
  other than you or root, writable by others unless it is sticky (like `/tmp`), or a link. A
  job start also refuses a project whose path now resolves somewhere else. The error names the
  path, and for a writable one the `chmod go-w` that fixes it (a project cloned under a umask of
  `002` needs this). On a macOS volume mounted with "Ignore ownership on this volume", every file
  reports your user, so the check passes: keep projects on the system volume.

- **Several jobs at once.** Engine jobs (build, release, rollback, services, remove,
  reconcile) run one at a time per project, so two projects can run at once. Jobs that touch the
  one local cluster (`cluster up`, `cluster down`, promote to kind) also run one at a time on the
  machine. Short writes in later milestones lock their target and time out after 60 s (300 s
  for a tool publish, which re-locks dependencies): registry publish and import and memory edits
  are not blocked by engine jobs; `agent set-ref` also holds its project, since it rewrites
  `agent.yaml`; a policy save is refused while any engine job runs, and no engine job starts
  during one. A refused start names the job holding the lock (`409`, `[BUSY]`); a refused save
  answers `409`, `[WRITE_BUSY]`.
- **These locks only order the console's own jobs.** A command typed in a terminal does not see
  them: `agentbreeder services up` in a terminal can run beside a console job. Correctness comes
  from the CLI's own file locks (the deployment store and `release` have one today; `cluster`
  and `services` get theirs in later milestones).
- **Cancel and shutdown.** Cancel sends one SIGINT to the job's CLI process, so `release` and
  `promote` finish or undo a replace step. Stopping `up` (Ctrl-C, SIGTERM or closing the
  terminal) sends the same SIGINT to every running job and long-lived child, waits 5 seconds
  (Ctrl-C again skips the wait), then sends SIGTERM to each one's process group, which also stops
  the `docker`, `kind` or `kubectl` processes it started, and waits up to 5 more seconds for them
  to end (at most 10 seconds in all). A read or short write in progress is not stopped; it ends
  or times out on its own.

**The limits.** Anyone who controls the local Docker has full control: the gate prevents mistakes and
policy drift, not a local admin. A project is code. Building it runs its Dockerfile in Docker, so add
only projects you trust. Local endpoints are unauthenticated.

## The Kubernetes target

Local clusters only. `kind` is the verified path.

- `kubectl` always gets a dedicated kubeconfig and context; `KUBECONFIG` and `current-context` are
  ignored. The kubeconfig must be private, must use static credentials, must have no proxy, and
  must point at a loopback API server. The cluster's `kube-system` UID must match the operator config. That UID identifies a
  cluster, it does not authenticate one.
- The namespace must be labelled for AgentBreeder and enforce the `restricted` Pod Security level.
- The agent runs as UID 65532 with a read-only root filesystem, no capabilities, resource limits and
  no service account token. Secrets are mounted read-only and go to `kubectl` on stdin. Images are
  referenced by digest.
- Refused rather than weakened: `mcp_access` and runtime enforcement.

### Measured on the 2026-11-10 run

| Check | Result |
|---|---|
| Agent pod to API server (service address, node addresses), kubelet, local registry | Blocked; a control pod with no policy could reach each one |
| Agent to the model gateway | Reachable |
| Another pod to the agent | Blocked |
| Privileged pod in the namespace | Refused by Pod Security admission |
| 15 stack secrets and provider keys in the Deployment, job logs, terminal log, `ps`, `docker inspect`, generated manifest | None found |
| `remove` | No labelled object left; gateway key revoked |
| Kill switch (`disable`) | Stopped the local and cluster workloads |

The metadata address is blocked by policy too, but a `kind` cluster has no metadata service, so the
control pod could not reach it either and that row proves nothing.

### Limits of this topology

The stack's services stay in Docker. The `kind` node is attached to the stack's agents network, so
pods use the same service names as a local container. Attaching it also exposes the node's API
server and kubelet ports to agents on that network; both require credentials. The local registry on
`127.0.0.1:5001` is unauthenticated. Secrets are unencrypted in `kind`'s etcd. `kubectl port-forward`
is the only way in: there is no public endpoint. This shows the deployer working, it is not a
production design. Helm, external secret stores and real-cluster certification are tracked in #770.
