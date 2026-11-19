# Local deployment support

Development-only single-user runtime for macOS/Linux. CLI calls one DeploymentService; there is no management HTTP API or UI yet. See the root README for build, deploy, status and removal commands.

Manifest input must describe a trusted stateless HTTP image. Its process runs as UID/GID 65532, listens on 0.0.0.0 at the manifest port inside the container, and returns HTTP 200 from the health path. Host publication is loopback only. No redirects or HTTP proxies are used for health checks. Images declaring VOLUME and nonlocal Docker transports are rejected. Kubernetes targets use `deploy --cluster`; see the console page, `command-center.md`.

Secrets: `agentbreeder secret set NAME < value` stores a value in `<state-dir>/secrets/NAME` (set `AGENTBREEDER_STATE_DIR` or pass `--state-dir`, the same one you deploy with). Instead of stdin, `--from-env VAR` reads environment variable `VAR`, and `--env-file FILE` also looks in a `KEY=value` file. That file is parsed, never sourced: no shell expansion, and errors name the line number but not the line. Well-known cloud, VCS and CI credentials (`AWS_*`, `GITHUB_TOKEN`, `SSH_*` and similar) are refused (`IMPORT_DENIED`). `--generate` stores a random 48-character hex token and won't replace an existing secret without `--force`. No form prints the value. A manifest entry `DATABASE_URL: secret://NAME` is mounted read-only at `/run/secrets/NAME`, and the container gets `DATABASE_URL_FILE=/run/secrets/NAME`. The value never appears in `docker inspect`, labels, logs or the audit log. Copies are deleted on failure and removal. A running deployment keeps the copy it started
with: to rotate a value, `secret set` it, then `remove` and `deploy` again. Anyone who can run `docker exec` on the container, or who owns the state directory, can read the value.

SQLite state and events commit together; schema initialization is atomic. A process lock serializes local mutations. Repeating the same manifest reconciles the same deterministic owned container after an interruption. A different spec with the same active name fails; remove it before creating a replacement. Removal checks ownership labels, verifies absence and records errors instead of hiding partial cleanup. Events survive removal. Status reads the latest persisted observation and does not promise current availability.

Every deploy first passes the deploy-time identity gate (`identity-gate.md`): pass `--environment <name>` from the operator configuration. Status re-resolves the bound principal and reports `compliant`, `noncompliant:<code>` or `unverifiable:<code>`.

Exit codes: 0 for verified deployment/removal or a status read of a deployment that has not failed; 1 for a recorded runtime failure (including status of a `failed` deployment), an incomplete disable, a blocked cleanup that `reconcile` could not finish, or an invalid audit chain (`audit verify`); 2 for invalid input, gate denial (`[CODE]`), audit-write failure, missing or inaccessible state (including a mistyped `--state-dir`) or prerequisite failure; 3 when deployment is pending a second-person approval; 4 when `reconcile` finds a noncompliant or unverifiable deployment. With `--json`, errors go to stderr only and stdout stays empty. JSON output contains ID, state, endpoint (only on ready), safe error, digests, context, timestamps and event history. Pending/starting/removing states can remain after an interruption; repeat the original operation to reconcile.

The state directory must be private (0700), database private (0600), and both owned by the current OS user. Do not share/copy the database across machines while it manages resources. Backups and migrations are not certified. Docker socket administrators can bypass ownership and runtime controls. Workloads can make outbound connections. Invocation has no application authentication added by this local tool; do not publish the port externally. The identity gate and audit chain are deploy-time controls for a single-user state directory. On their own they are not egress controls (the operator can add the opt-in MCP egress sidecar; see `identity-gate.md`), and the audit chain is not tamper-evident against the database owner.

The local Docker lifecycle and fresh-install demo were verified on macOS with Docker Desktop on 2026-09-15. On 2026-11-05 the same real lifecycle test and fresh-install demo passed again on macOS through the identity gate (static adapter, `enforce` mode). Other platforms and production environments remain unverified. Unit tests with injected runtimes establish service behavior only. The opt-in integration test builds a real image, deploys and invokes it, repeats the request, reopens state, and removes the resource.

## Kit egress

On `main`, not in a published preview (#1083 F1b). **Not yet run on a real engine:** the unit
tests use a scripted Docker and kubectl, and the opt-in real-engine tests
(`AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_kit_egress_docker.py`) are written but have not
been run. No shipped runtime has `profile: kit`, so no manifest built today takes this path, and
every other manifest deploys exactly as before (goldens in `tests/test_kit_egress.py`).

A manifest with `runtime: {profile: kit}` is deployed with default-deny egress.

**Local Docker.** The agent container is not put on the default bridge and publishes no port. It
joins only internal networks:

- its own (`<container>-int`), shared with a forwarding sidecar (`<container>-fwd`);
- the environment's `network`, when the operator config has one (the stack's agents network,
  where the gateway, the MCP relays, the trace collector, the database and the tools proxy are).

The sidecar is the phase-4 boundary's pattern (`identity-gate.md`): the same pinned base image,
hardening flags (with a 128-pid limit) and ownership labels, and no token. Its code is
standard-library only (`egress/forwarder.py`). It copies bytes between fixed pairs set at deploy
time, and has no proxy protocol:

| Listens on | Copies to | When |
|---|---|---|
| a loopback port on the host | the agent's HTTP port | always (this is the deployment's endpoint) |
| `kit-egress.internal:8082`, inside the agent's network | `host.docker.internal:<gateway port>` | the operator's gateway `agent_url` is `http://host.docker.internal[:port]` |

In the second case the agent's `AGENTBREEDER_GATEWAY_URL` names the sidecar. A gateway on the
environment's network is reached directly, with no forward.

Refused before anything is created (`KIT_EGRESS_UNSUPPORTED`): an `https` gateway on the host; a
gateway that is neither on the host nor on an environment network; an
`environments.<env>.services` URL, or a used MCP relay, on `host.docker.internal`. Only the
gateway is forwarded to the host.

Deploy verifies the boundary before the health check, and `reconcile` verifies it again:

- every network of the agent is internal (the environment's network is checked before anything
  is created), the agent publishes nothing, has no host mapping and no added capability, and its
  own network holds only the agent and the sidecar;
- the sidecar runs the expected code and configuration and publishes only the loopback ingress
  port;
- a probe container in the agent's network namespace cannot connect to public IPv4 or IPv6
  addresses, cannot connect to the networks' gateway addresses on a fixed list of ports (and
  the forwarded gateway port), and cannot resolve a public name or `host.docker.internal`. The
  probe samples ports; what keeps the host off those addresses is Docker Desktop's VM, or on
  Linux the isolated gateway checked above it.

A boundary that does not verify fails the deploy with `[RUNTIME_BOUNDARY_UNVERIFIED]` and its
containers and networks are removed. In `reconcile` the deployment becomes `failed` and
`noncompliant:RUNTIME_BOUNDARY_UNVERIFIED`. `reconcile` starts a stopped sidecar again (never
the agent) and records its new loopback port. When the engine cannot be reached, `reconcile`
reports the same compliance and changes nothing; a Docker error after that point counts as an
unverified boundary. An agent that fails its health check behind a
verified boundary is left in place by deploy, as on the open path, so its log can be read (until
the next `reconcile`, below). A kit
container found stopped is removed and created again, never started as found. `reconcile`
does not start an agent: a ready kit deployment whose agent has stopped no longer verifies, so
it is torn down, its gateway key revoked and the record set to `failed`; deploy it again.
`reconcile` also tears down every `failed` kit deployment (so a failed agent's log is readable
only until the next `reconcile`), and retries a teardown that failed. A kit deploy that was
interrupted before verification (left `starting`) is set to `failed`, its secrets, MCP grants
and gateway key are revoked, and it is torn down. `remove` tears down a deployment's kit objects
whenever its recorded manifest is a kit runtime or can no longer be read.

On a Linux engine every network of the agent must also keep the host off its gateway address
(Docker Engine 28 or later, `gateway_mode_ipv4=isolated`). `services up` does not create the
stack's networks that way yet, so a kit deployment with an environment network is refused on
Linux until it does.

**Kubernetes (`kind`).** The deployment's NetworkPolicy allows cluster DNS and
`kubernetes.agents_cidrs` only (minus `blocked_cidrs` inside them), instead of everything except
`blocked_cidrs`. `agentbreeder cluster up` records `agents_cidrs`, the stack's agents network's
private IPv4 ranges of /12 or longer (see the limits below); run it again for a stack whose cluster was recorded by an earlier version.
Without it a kit deployment is refused (`KUBERNETES_KIT_EGRESS_UNCONFIGURED`). `services up`
recreates the agents network and refreshes a recorded `agents_cidrs` with it. A gateway on
`host.docker.internal` is refused for a kit deployment on the cluster (`KIT_EGRESS_UNSUPPORTED`).

**Limits.**

- The manifest field is written by `build` and is not a security boundary: a hand-written
  manifest without it deploys with open egress, as any image does today.
- The environment's network is shared: the agent can reach every service exposed to agents on
  it and the other agents on it, not only its own gateway.
- Both forwards are TCP copies. What the agent may ask of the gateway is the gateway key's
  limit, not the sidecar's. The agent sees a caller's own `Host` header, as it does on a
  directly published port, and a forwarded gateway sees `Host: kit-egress.internal:8082`.
- A gateway URL that is on neither the host nor the environment's network (for example
  `localhost`) is not detected when the environment has a network: the deploy is ready and the
  model calls fail.
- On Kubernetes, DNS queries go to cluster DNS, which forwards names it does not own.
- On Kubernetes the allowed range is the whole agents network. Only the node's own address on
  it is excepted, so the network's gateway address is allowed: on a Linux host that is the
  host itself, and a kit pod can reach host services bound to that address or to every
  interface. Local Docker refuses that case; the cluster policy does not yet (a follow-up).
- `agents_cidrs` takes private IPv4 networks of /12 or longer only (not loopback, link-local,
  multicast, reserved or `0.0.0.0/8`); `cluster up` records nothing else. Within that it is the operator's to
  keep narrow.
- With `runtime_enforcement: mcp_http` a kit manifest is refused like every manifest with
  `model_access` (`MODEL_ACCESS_UNSUPPORTED`).
- Someone who controls the Docker socket or the cluster can change any of this.

