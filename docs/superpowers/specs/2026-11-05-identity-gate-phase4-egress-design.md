# Identity gate phase 4 — enforced MCP egress boundary (design)

- **Status:** Approved 2026-11-05 (option C with the defaults in §6) and implemented; see §8 for decisions, one deviation and verification evidence
- **Date:** 2026-11-05
- **Tracking:** #706 (phase 4) under epic #701
- **Builds on:** `2026-11-05-agent-identity-gate-design.md` §4.7, `docs/identity-gate.md`

## 1. Problem

Phases 0–3 decide *whether* an agent may be deployed. Nothing constrains what the running container
calls. Setting `runtime_enforcement.mode: mcp_http` therefore denies deployment with
`CONTROL_UNSUPPORTED`. Phase 4 must make per-tool-call policy enforceable for MCP Streamable-HTTP
without relying on the agent's cooperation. That needs answers to three open questions:

1. **Isolation:** how does the local runtime guarantee the agent has *no* network path except the
   sidecar, and how is that verified rather than assumed?
2. **TLS:** how does the sidecar read authenticated MCP traffic (method, tool, arguments) without
   becoming an unreviewed man-in-the-middle?
3. **Failure:** what happens when the sidecar, the policy engine or the audit write fails?

## 2. Goals and non-goals

**Goals:** mandatory routing through a policy enforcement point (PEP) for supported MCP HTTP;
body-authoritative JSON-RPC validation; per-call PDP decisions using the deployment's verified
principal; held calls for `require_approval`; a durable audit record before forwarding; a credential
broker so the agent never holds long-lived tool tokens; deny deployment whenever the topology cannot
be verified.

**Non-goals:** STDIO or in-process tools (they cannot be mediated, and the docs must say so); generic
HTTP egress filtering; protecting against a Docker-socket or host administrator; Kubernetes (phase 5).

## 3. Options considered

| Option | Isolation | TLS visibility | Verdict |
|---|---|---|---|
| A. Proxy env vars (`HTTPS_PROXY`) | None — the agent can ignore them | CONNECT tunnel is opaque | **Rejected** (spec forbids proxy-only) |
| B. Opaque CONNECT allow-list proxy on an internal network | Strong (no route out) | Host-level only; cannot see tool or arguments | **Rejected** for tool policy; could serve as a later coarse egress control |
| C. Internal network + **explicit gateway transport**: the agent's MCP client targets `http://mcp-gateway.internal/<server>`; the sidecar originates authenticated TLS upstream | Strong | Full: plaintext JSON-RPC on an isolated, single-tenant internal link | **Recommended** |
| D. Internal network + transparent TLS interception with a per-deployment local CA injected into the container trust store | Strong | Full | Fallback only: wider trust surface (CA key custody, image trust-store mutation, pinned-cert clients break) |

**Recommendation: C**, keeping D as an opt-in only if a supported client cannot be pointed at a
gateway URL. C never asks the agent to trust a forged certificate. The sidecar is the only TLS client
to the real MCP server and validates upstream certificates normally. The plaintext hop exists only
inside a per-deployment internal network that holds two containers.

## 4. Proposed design (option C)

### 4.1 Topology (local Docker)

- Per deployment, create a network with `docker network create --internal agentbreeder-<id>`. An
  internal network has no route off the host, so direct IP, DNS, IPv4/IPv6, alternate ports and
  tunnels all fail by construction.
- The agent container attaches **only** to that internal network. It runs with `--cap-drop ALL`
  (no `NET_ADMIN`/`NET_RAW`), `no-new-privileges` and the existing read-only hardening. It gets
  `--dns` pointed at the sidecar's resolver, which answers only `mcp-gateway.internal`.
- The sidecar container attaches to the internal network and to one egress network. It is an
  AgentBreeder-owned image pinned by digest, labelled with the deployment ID and started before the
  agent.
- Health publication stays as it is today (loopback port on the agent). That is ingress only; the
  publish mapping does not give the agent egress.

### 4.2 Verification before traffic (fail closed)

Deployment proceeds only if every check passes; otherwise the containers are removed, the deny is
audited and the exit code is 2 (`RUNTIME_BOUNDARY_UNVERIFIED`):

1. `docker network inspect` reports `Internal: true` and exactly two attached containers, the agent
   and its sidecar, both carrying ownership labels.
2. `docker container inspect` (agent): `NetworkMode` is the internal network, there are no other
   networks, `CapAdd` is empty, `Privileged` is false, and there is no `--network host`/`container:`
   mode.
3. **Active probes from inside the agent's network namespace**, using a short-lived probe container
   that joins the agent's network namespace, not the agent image:
   - TCP connect to a public IPv4 address, a public IPv6 address and the host gateway on several
     ports must fail;
   - DNS for an arbitrary public name must not resolve;
   - `mcp-gateway.internal` must resolve to the sidecar and answer its health endpoint.
4. The sidecar health endpoint reports the loaded policy revision, the operator config digest and
   the deployment revision that the gate recorded.

Docker contexts that cannot create internal networks (some rootless or remote setups) are rejected.
There is no degraded mode.

### 4.3 Request handling in the sidecar (PEP)

For each `POST /<server>` (Streamable HTTP, MCP revision 2026-07-28):

1. Resolve `<server>` from the operator-approved destinations map (`capabilities.tools[].mcp`
   intersected with the operator allow-list). Unknown server: 403.
2. Parse the JSON-RPC body; it is authoritative. Reject malformed JSON, batches (unless explicitly
   supported), unknown methods and oversize bodies. For `tools/call`, extract `name` and
   `arguments`. Require `Mcp-Method`/`Mcp-Name` headers to equal the body. A missing header or a
   mismatch is a deny, never an inferred allow. Older revisions need an explicit body-only adapter.
3. Build an AuthZEN request (`action: tool.call`, resource = destination + tool, principal = the
   deployment's verified principal snapshot, context = canonical arguments digest + revisions) and
   evaluate it with the same `PolicyEngine` (baseline AND restrictions).
4. Before forwarding, write the allow/deny row to the audit store, through a local append API on
   the host executor; the sidecar never opens the database. If the audit write fails, the call is
   not forwarded (503).
5. Forward over authenticated TLS with normal certificate validation, injecting a short-lived
   token from the credential broker. Redirects are not followed. The response returns unchanged
   (streaming responses pass through; notifications are rate-limited).

### 4.4 Held calls (`require_approval`)

The sidecar persists a held call bound to destination, method/tool, canonical-argument digest,
principal, deployment revision, policy revision and expiry. It returns a JSON-RPC error with a
`retry_after` hint; long-polling is not used. Approval reuses the phase-2 checker rules out of band
(`agentbreeder approve-call <id>`). The agent retries the identical call. The sidecar re-checks
policy and disable state, then atomically consumes the single-use release. Any difference in
arguments or revisions is a new request.

### 4.5 Credential broker (RFC 8693)

Long-lived tool credentials live in the operator's secret store and are visible only to the sidecar.
For each call the sidecar exchanges them for an audience-restricted, short-lived token (token
exchange where the MCP server's authorization server supports it; otherwise an operator-configured
static credential that never enters the agent container). Inbound `Authorization` headers from the
agent are stripped; there is no token passthrough.

### 4.6 Failure semantics

| Failure | Behaviour |
|---|---|
| Sidecar crash/OOM | Agent loses all egress (internal network); reconcile restarts the sidecar first and re-verifies before any traffic |
| PDP error/timeout | Deny the call (fail closed) |
| Audit append failure | Do not forward |
| Disable | Blocker persisted, then sidecar told to deny all, then containers stopped (§4.8 of the main spec) |
| Policy/operator config change | Sidecar refuses to serve once its pinned revision no longer matches the recorded deployment revision; redeploy required |

## 5. Test plan (acceptance for #706)

- Bypass suite run by the probe container: direct IPv4/IPv6, alternate ports, DNS to public
  resolvers, DoH endpoints, `CONNECT` via the gateway, redirect to an unapproved host, and DNS
  rebinding of an approved name to a private address (the sidecar pins resolved addresses and
  rejects private ranges unless allow-listed).
- Sidecar kill test: the agent has no egress; reconcile restores the sidecar and verification
  before traffic.
- JSON-RPC conformance: malformed body, header missing, header/body mismatch, unknown method,
  batch, oversize.
- Policy: allowed call forwarded and audited; denied call blocked and audited; audit failure blocks
  forwarding; held → approved → released once; changed arguments, expired approval or replay
  rejected.
- No long-lived tool token in `docker inspect` env or in the agent filesystem.
- Unsupported Docker context (no internal networks) rejects deployment.

## 6. Open questions for review

1. Is the option-C client requirement (pointing MCP clients at a gateway URL) acceptable for the
   first supported frameworks, or is option D required on day one?
2. Should the sidecar image be built and published by this repo (supply-chain cost) or shipped as a
   Python entry point in a pinned base image?
3. How should the host append API be authenticated: a Unix socket mounted only into the sidecar, or
   a per-deployment token?
4. What rate limits and size limits should apply per tool call by default?

## 7. Out of scope

Kubernetes NetworkPolicy/SPIRE equivalents (phase 5), STDIO/in-process tools, generic HTTP egress
policy, and any compliance claim.

## 8. Decisions and implementation record (2026-11-05)

**Answers to §6** (approved by the maintainer):

1. Option C first. Clients are pointed at the gateway via `AGENTBREEDER_MCP_SERVERS`; option D is
   not built.
2. The sidecar is a Python entry point on a digest-pinned `python:3.12-slim` base. The repo
   publishes no image: deploy builds a thin local layer from a deterministic context (stdlib-only
   `protocol`, `sidecar`, `probe` modules) and labels it with the code digest, which verification
   checks.
3. Host append API: Unix socket, authenticated by a per-deployment bearer token.
4. Defaults: 1 MiB request body (`max_body_bytes`) and 60 calls per minute per server/tool
   (`calls_per_minute`); both operator-configurable.

**Deviation from answer 3.** Probing showed Docker Desktop for macOS cannot bind-mount a host Unix
socket into a container (`connect` fails with `EOPNOTSUPP`). The decision-service transport is
therefore chosen by engine: Linux engines use the Unix socket (mounted read-only into the sidecar
only); Docker Desktop uses a loopback TCP port reached via `host.docker.internal`. Both require the
per-deployment token, and the agent cannot reach either (no route, no `host.docker.internal`
resolution — both asserted by the probes). The Linux socket path is unit-tested only.

**Design refinements made during implementation.**

- The append API became a *decision* service: policy evaluation, held-call state, the audit write
  and the credential broker all run on the host, so the sidecar holds no policy, no database and
  no long-lived tool credential. One request answers allow/deny/held after the audit row commits.
- Ingress moved to the sidecar: containers on an internal network cannot publish ports, so the
  sidecar publishes the agent's HTTP port on host loopback and proxies to it.
- DNS: Docker's embedded resolver does not forward external names for internal networks
  (verified), so no custom resolver is needed; the probe still asserts it.
- Tool names in `capabilities.tools[].actions` are the MCP tool names the PEP allows per server.
- Runtime decisions are always enforced, even in `enforcement: audit`.
- `initialize`, `tools/list`, `ping` and notifications are forwarded after validation, rate
  limiting and kill-switch/pin checks, without PDP evaluation or audit rows.
- Decision-service replies are authenticated (independent review, 2026-11-06). In TCP mode the
  loopback port is reachable by every local user, so a process that bound it after the service
  died could otherwise pass `reconcile`'s health check and answer `/decide`. Every reply now
  carries `HMAC-SHA256(token_sha256, nonce || status || body)` for the caller's random nonce; the
  sidecar derives the key from its token and `reconcile` reads it from the `0600` pin, and also
  requires the recorded service PID. The unauthenticated `GET /healthz` returns the revision only.
  The PEP token reaches the `docker` CLI via its environment (`--env NAME`), not argv, which `ps`
  shows to other users. Each service connection is bounded to 5 s. The probe plan includes the
  decision-service port on every gateway. IPv6 forms embedding private IPv4 (NAT64, 6to4, Teredo,
  mapped) count as private. `GET /mcp/*` returns 405, and forwarded `Mcp-Method`/`Mcp-Name` are
  rebuilt from the parsed body.

**Verification evidence (macOS, Docker Desktop, engine 27.5.1).**

- `tests/test_egress_docker.py` (opt-in, real Docker): deploy behind the boundary (deploy + verify
  ~5 s); ingress through the sidecar; from inside the agent container, an allowed call is forwarded
  with the broker token, a spoofed agent `Authorization` never reaches upstream, a policy deny
  returns 403, a header/body mismatch returns 400, a held call is released once after
  `decide_call` and the identical retry is held again; direct IPv4 connect, public DNS and
  `host.docker.internal` fail; denied/held calls never reach upstream; audit chain verifies;
  removal leaves no containers, networks or decision service.
- Same file: killing the sidecar leaves the agent with no egress; `reconcile` restores and
  re-verifies; connecting the agent to the default bridge makes `reconcile` mark
  `noncompliant:RUNTIME_BOUNDARY_UNVERIFIED`, tear everything down and audit the failure.
- Unit suites `test_egress_pep.py`, `test_egress_service.py`, `test_egress_boundary.py`: JSON-RPC
  conformance, CONNECT (501), redirect refusal, DNS-rebinding refusal, private-destination
  refusal, untrusted certificate, rate limiting, audit failure blocking, held-call checker rules,
  token exchange, and each verification failure mode.

**Not covered.** A Linux-host run (Unix-socket transport, iptables behaviour of internal networks
on a native engine); MCP clients that cannot send `Mcp-Method`/`Mcp-Name` (need a body-only
adapter); server-initiated `GET` streams (405).
