# Authenticated `/invoke` and caller-derived customer identity (#780): design

- Status: **r4, reviews converged** (architecture and `/security` reviews by Claude Sonnet, three rounds;
  §15).
  Awaiting owner decisions D1–D9 (§13). No code in this PR.
- Issue #780 (epic #742), prerequisite of M19 A2A (#835) in epic #812. Try it (M7 #823) and
  `invoke` (M4 #820) show a "not authenticated (#780)" note until this lands. Plan of record:
  `docs/superpowers/plans/2026-11-11-console-epic.md` (wins where this spec differs).
- Builds on: `runtime/adk@1.1.0` (`runtime/server.py`, `agent.py`, `tools.py`, `memory.py`,
  `redaction.py`, `mcp_client.py`), `deployment.py` (`_mint_key`, `_grant_mcp`),
  `secret_files.py`, `mcp.py` (relay tokens), `egress/` (opt-in `mcp_http` sidecar and host
  decision service), `invoke.py`, `evals.py`, `release.py`, `promote.py`, `kubernetes.py`, and the
  console spec (`2026-11-11-local-console-design.md` S-12, §8.6).

## 1. Problem

An agent built from `agent.yaml` serves `POST /invoke` with no authentication
(`runtime/adk/1.1.0/runtime/server.py`, `do_POST`). Its `Host` check (`local_host()`, which accepts
`127.0.0.1`, `localhost` and `agent`) only stops browser DNS rebinding. The body's `customer_id` is
free text: whoever sends the request names the customer.

What that allows today:

1. **Another container on the stack's agents network** sends `Host: agent` and invokes the agent.
   Since `runtime/adk@1.1.0` the agent can call MCP tools through the relay, and the relay
   authenticates the **agent** (its per-deployment token), not the agent's **caller**. So that
   container can make the agent call every MCP tool it was granted.
2. **Any local process or user** that reaches the loopback port (or a `kubectl port-forward` of a
   `kind` pod) does the same.
3. **Any caller names any customer.** `customer_id` selects long-term notes (`memory.notes`),
   tickets, the ADK session's `user_id` (which in-process tools receive as `ToolContext.customer`),
   audit events and the trace's `user.id`. Northwind's order tools act on the customer they are
   given.
4. **Any caller can stall another customer's conversation.** The session lock is keyed by
   `session_id` alone (`server.py` `session_lock`), so a caller who knows a session id gets that
   session's turns refused with 409.

This is documented as a known limitation in `docs/services.md`, `docs/agent-yaml.md`,
`docs/your-first-agent.md` and `invoke --help`.

## 2. Goal

- An agent image built on `runtime/adk@1.2.0` refuses a `/invoke` request that does not carry a
  valid, current, signed token for **this deployment**, whatever the network path (loopback, the
  agents network, a port-forward).
- The customer that agent acts for comes **only** from that token. A body `customer_id` can no
  longer choose it.
- Legitimate callers (`invoke`, `eval`, `release`, `promote`, the console's Try it, the
  operator's own scripts, later A2A) get tokens without the key or a token landing in images,
  labels, env, logs, traces, audit detail, the console's database or argv.
- In-process tools, the runtime's audit events and traces carry the verified customer.

What this is and is not, in the words docs may use: **"`runtime/adk@1.2.0` agents refuse
unsigned requests; the customer is signed per call with a key from your local AgentBreeder
state. Anyone who can read that state, use the Docker socket or, on `kind`, read the cluster's
Secrets can sign too."** The check is code inside the agent's own container (T7). Images not built from
`runtime/adk@1.2.0` are not protected and show `invocation: none`. Enforcement outside the agent's
container remains only the opt-in `mcp_http` sidecar, for MCP Streamable HTTP; §8.2 sets the
contract for extending it and is not built here.

Acceptance (from #780, AC2 narrowed per review S16):

1. A `runtime/adk@1.2.0` deployment's `/invoke` refuses a request without a valid token,
   including from other containers on the agents network and through a port-forward.
2. In-process tools see only the customer from the verified token. MCP tools (relay) receive no
   customer at all, and no MCP tool call can start without a verified token, because no turn can.
3. Neither the key nor a token appears in images, container labels or env, logs, traces, audit
   detail, store rows, `center.sqlite3`, `--json` output or child argv, tested with canaries, as
   for the relay.
4. Northwind's 61-case release gate passes on `runtime/adk@1.2.0`, with every case's customer
   signed and no body `customer_id`.

## 3. Scope and non-goals

In scope:

- A per-deployment **invoke key**, minted by `deploy` (local and `--cluster`) and mounted as a
  `_FILE` secret.
- A short-lived, single-use, signed **invocation token** that carries the customer, minted on the
  host by the CLI from that key for every call.
- `runtime/adk@1.2.0`: verifies the token, takes the customer from it, locks sessions per
  customer.
- CLI: `invoke`, `eval`, `release` and `promote` sign; a new `token` command for scripts;
  `status` and `list --json` show `invocation`.
- Migration for existing deployments and Northwind.

Not in scope (and not claimed):

- **The `mcp_http` sidecar.** No `runtime/adk` agent can run under `mcp_http` today
  (`model_access` and `mcp_access` are both refused with it, `deployment.py` `MODEL_ACCESS_UNSUPPORTED`,
  `MCP_RELAY_UNSUPPORTED`; #742 gap 2). §8.2 is the contract a later slice follows, after #742
  lifts that; nothing in this epic changes `egress/`.
- **Customer authentication by an external IdP** (end-user OIDC tokens). §6.7 reserves the slot
  (D3).
- **Multi-user** access to one machine's state. Anyone who can read the operator's AgentBreeder
  state, the Docker socket or the `kind` cluster's Secrets can sign (§5).
- **Policies** that use the customer. #742 owns RBAC/ABAC.
- The MCP relay (`mcp_access`) learning the customer (§8.3, D5); per-conversation MCP sessions
  (#747).
- The `http-agent` sample (`quickstart`): not an AI agent, no tools, no grants; unchanged and shown
  as `invocation: none`.

## 4. Today's flow (verified on `90f117c`)

| Piece | Where | What it does |
|---|---|---|
| Runtime `/invoke` | `runtime/adk/1.1.0/runtime/server.py` | `Host` allowlist, JSON-only (forces a CORS preflight), 64 KiB body, `session_id`/`customer_id` match `^[A-Za-z0-9_-]{1,64}$`, default customer `guest`; `ThreadingHTTPServer`, 180 s socket timeout; `GET /health` (status and model alias) open; `GET /` chat page with a customer field, title and description |
| Session lock | `server.py` `session_lock` | keyed by `session_id` only |
| Customer use | `agent.py`, `tools.py`, `memory.py` | ADK `user_id` = customer; `ToolContext.customer` "never a model-supplied value"; notes refused for `guest`; `audit_events.customer`; trace `user.id` |
| Runtime tables | `memory.py` `owner_managed` | when the DB owner pre-created all four tables, the runtime runs no DDL; `audit_events` is append-only by trigger |
| Redaction | `runtime/redaction.py` | remembered secrets (MCP tokens, gateway key) and key-shaped strings (`sk-`, `AIza`, `Bearer `) |
| Relay token | `deployment.py` `_grant_mcp`, `mcp.py` | 256-bit hex per container generation; `secret_files.write_generated` (0444 file in 0700 dirs) mounted as `AGENTBREEDER_MCP_<NAME>_TOKEN_FILE`; the relay sees only `sha256(token)` |
| MCP client | `runtime/mcp_client.py` | one ADK `McpToolset` per server for the whole runtime; `header_provider` adds the relay token |
| CLI caller | `invoke.py` | loopback `http://127.0.0.1:<port>` only, Docker-verified container, no proxies or redirects, 180 s deadline; `--customer`/`--session` with the "not authenticated" note |
| Eval caller | `evals.py`, used by `eval`, `release` (candidate) and `promote` (cluster) | each case in a fresh session; posts `customer_id` from the case (seeded customers) or the session id |
| Release | `release.py` | deploys `<name>-candidate` (its own deployment id), evals it, removes it, then a separate `deploy` of the same image as production |
| Promote | `promote.py` | pushes the released image, gated `deploy --cluster`, then `evals.run` through a forward |
| Kubernetes | `kubernetes.py` | `_FILE` mounts from a per-deployment Secret created from the host copies; `mcp_access` refused |
| Sidecar | `egress/` | only with `mcp_http`: ingress forwards `Authorization`; egress header allowlist `_FORWARD_HEADERS` (no custom headers); `call_tool` uses the deployment's pinned principal; the agent shares an `--internal` network with its sidecar only |

## 5. Threat model

Assets: the agent's granted tools (MCP and in-process), each customer's notes, tickets and
sessions, the model budget, and the integrity of audit events and traces (who acted for whom).

Trust boundary: **the operator's OS user** on a single-user machine. The CLI, the console (which
runs the CLI as a child) and AgentBreeder's state directory are on the trusted side. Containers,
other local users and anything on the agents network are not.

| # | Threat | Today | After (on `runtime/adk@1.2.0`) |
|---|---|---|---|
| T1 | A container on the agents network invokes the agent (`Host: agent`) | open | refused: it has no key, so no token (AC1) |
| T2 | Another local user or process hits the loopback port or a port-forward | open | refused unless it can read the operator's state (0700 directories) or is a T9 reader (never quote this row without T9) |
| T3 | A caller impersonates a customer (`customer_id` in the body) | open | the customer comes only from the signed token; a differing body value is refused |
| T4 | A captured token is replayed later, against another deployment, or after a restart | n/a | single use (`jti`); ≤ 60 s lifetime from the CLI, ≤ 300 s from `token`; audience = one deployment id; body hash bound; tokens issued before the runtime process started (less 60 s skew) are refused, so a restart within 60 s of a token's `iat` still allows one replay (§6.3) |
| T5 | The key or a token leaks (images, labels, env, logs, traces, audit, `center.sqlite3`, argv, exception text) | relay token tested with canaries | same canaries extended to the key and tokens; JWT-shaped strings redacted (§7) |
| T6 | A browser page drives the agent (DNS rebinding, CSRF) | blocked by `Host` + JSON-only | unchanged; a page also has no token |
| T7 | The agent's container is compromised, or the image is not `runtime/adk@1.2.0` | n/a | **not defended**: the check runs inside that container. `status` shows `invocation: none` for other images |
| T8 | The agent signs tokens itself with its copy of the key | n/a | possible with HMAC (D1). Harmless at its own `/invoke`; it is why §8.2 needs a key the agent never holds |
| T9 | A Docker-socket user, root, a `kind` cluster admin, a backup of the state directory, or a pod allowed to read Secrets reads the key | open (as for relay tokens) | open; documented. On `kind`, Secrets are not encrypted at rest in etcd |
| T10 | Unauthenticated floods and slow clients | open (180 s per connection, a thread each) | refused from headers alone; 10 s to send the request line and headers; `Connection: close` on every refusal (§6.3) |
| T11 | A session id known to one customer stalls another's turns | open (lock by session only) | lock keyed by `(customer, session)` |
| T12 | An A2A caller agent gets another agent's key | n/a (M19) | forbidden by design: a host-side broker signs A2A tokens (§9) |

Honest limit for the docs: **locally, the customer is asserted by whoever can read the
operator's state.** The token makes that assertion signed, bound to one deployment and one body,
short-lived and recorded with its `jti`; it does not prove an end user is who they say (§6.7, D3).

## 6. Credentials and tokens

### 6.1 Invoke key

- A 32-byte random **master** per **container generation**, minted inside
  `DeploymentService.deploy` (the path `release`, `promote` and `deploy --cluster` all go through)
  after the identity gate and approval and inside the lifecycle lock, like `_mint_key` and
  `_grant_mcp`. A redeploy or rollback mints a new one. A Docker restart or pod restart keeps it;
  §6.3's start-time check limits what that allows. Rotation is a redeploy; there is no separate
  rotate command.
- The agent never receives the master. It receives only the **invoke key**
  `k_invoke = HKDF-SHA256(master, salt = empty, info = "agentbreeder:invoke:v1:<deployment id>",
  32 bytes)`; the CLI derives the same key to sign. HKDF (RFC 5869) is written over `hmac` (the
  standard library has none) and tested with the RFC's vectors. This is what lets §8.2 later add a
  second derived key (`k_attr`) that the agent cannot derive, without redeploying every 1.2.0
  agent or changing the format.
- The master stays on the host only: `secret_files` writes it as a host-only file
  (`invoke-master`, 0400, never mounted). `k_invoke` is written by
  `secret_files.write_generated(state, deployment_id, "invoke-key", "AGENTBREEDER_INVOKE_KEY",
  key)`: 0444 in 0700 directories, mounted read-only at `/run/secrets/invoke-key`, env
  `AGENTBREEDER_INVOKE_KEY_FILE`. Neither is ever in an env value, label, deploy manifest or store
  row. `remove` deletes both with the deployment's other copies.
- Kubernetes (`kind`): the per-deployment Secret `kubernetes.py` already creates from the host
  copies (by stdin, so no `last-applied` annotation holds it) carries it; a test pins both that and
  that the exported `<agent>.kubernetes.deploy.yaml` holds no key.
- New env `AGENTBREEDER_DEPLOYMENT_ID` (the token audience), set by deploy for `token-v1`
  deployments and listed in the 1.2.0 runtime manifest's environment.
- Algorithm (D1, recommended **HMAC-SHA256**): the CLI's core dependencies are `pydantic` and
  `PyYAML`, and HMAC needs only the standard library on both sides. Ed25519 would let the runtime
  hold only a public key (closing T8) but makes `cryptography` a core CLI dependency. The header
  carries `alg`, so a later move is a new runtime version, not a format break.

### 6.2 Invocation token

A compact JWS (RFC 7515), minted with the standard library:

- Header exactly `{"alg": "HS256", "typ": "JWT"}`. There is one key per deployment, so there is no
  `kid`. Any other member, or any other value (`none`, `HS512`, `RS256`, `jku`, `jwk`, `x5u`,
  `crit`), is refused.
- Claims (exactly these; unknown claims are refused):

| Claim | Value | Checked |
|---|---|---|
| `iss` | `agentbreeder` | equal |
| `aud` | `agentbreeder:invoke:<deployment id>` (a string, never an array) | equal to `AGENTBREEDER_DEPLOYMENT_ID` |
| `sub` | customer id (`^[A-Za-z0-9_-]{1,64}$`); absent means `guest`; the literal `guest` is refused as a `sub` | regex |
| `cal` | `invoke`, `eval`, `console`, `token`, `a2a` | enum; **informational only**: any key holder sets it, so no policy may use it |
| `iat`, `nbf`, `exp` | integers (`type(x) is int`, not bool or float) | `iat ≤ nbf < exp`, `exp - iat ≤ 300`, 60 s skew |
| `jti` | 32 lowercase hex characters | not seen before (§6.3) |
| `bh` | base64url SHA-256 of the exact request body bytes | equal (`hmac.compare_digest`) |

- `act` (A2A delegation, §9) is refused in 1.2.0.
- Strict parsing: the whole `Authorization` value is ≤ 2 KiB, checked before any split or decode;
  exactly one `Authorization` header; scheme `Bearer` (case-insensitive) followed by one space and
  three base64url segments without padding, whitespace or `+`/`/`; JSON objects with duplicate
  keys are refused (`object_pairs_hook`); claim types are exact.
- Sent as `Authorization: Bearer <token>`. One token per request; a retry signs a new one
  (`invoke` already treats a retry as a second turn). The CLI signs with a 60 s lifetime;
  `agentbreeder token` up to 300 s.
- Why not a static bearer key: it is replayable for the deployment's whole life by anyone who sees
  one request, is the same for every customer and binds nothing. Signing costs one HMAC per call
  and keeps the key off the wire.

### 6.3 Verification order in the runtime

1. A 10 s socket timeout until the headers are read (then the existing 180 s). Path and `Host`
   checks as today.
2. `Authorization`: present, one header, ≤ 2 KiB, well formed. Else **401** `AUTH_REQUIRED`.
3. Signature first (`hmac.compare_digest` over the signing input), then the header, then the
   claims. Any failure before the signature verifies is **401** `TOKEN_INVALID`; only a token with
   a valid signature can get **401** `TOKEN_EXPIRED`. A token whose `iat` is earlier than the
   runtime **process** start time minus the 60 s skew is `TOKEN_EXPIRED`: this closes most of the
   replay window a restart opens (the replay cache does not survive one), but not a restart within
   60 s of the token's `iat`.
4. Content-Type and Content-Length checks as today; read the body; `bh` must match, else **401**
   `TOKEN_INVALID`. A token seen with the wrong body is therefore not spent, so whoever sees a
   token cannot burn it for its legitimate sender. Before the signature verifies, nothing reads the
   body; after, the cost is one ≤ 64 KiB read for a key holder.
5. The `jti` is consumed here: insert it under a lock, kept until `exp` plus the 60 s skew, or
   **401** `TOKEN_REPLAYED`. Entries past `exp + 60 s` are dropped. When 10 000 unexpired entries
   are held, the request fails closed with **503** `AUTH_BUSY` (no live entry is ever evicted);
   only a key holder can fill the cache. A refused request after this point (bad JSON, mismatch,
   busy session) has spent its token.
6. Body JSON as today. A body `customer_id` that differs from the token's customer is **400**
   `CUSTOMER_MISMATCH`; an equal one is accepted for compatibility. The CLI never sends it.

**Clocks.** The signer uses the host clock and the verifier the container's. They must agree within
60 s; Docker Desktop's VM clock can drift after the host sleeps. When a freshly signed token gets
`TOKEN_EXPIRED` or `TOKEN_INVALID`, the CLI's message adds "check that the Docker VM's clock
matches this machine's (it can drift after sleep)".

Every refusal carries a fixed message and code, `WWW-Authenticate: Bearer` on 401, and
`Connection: close`; never the token, claim values or which check failed beyond the code. The
runtime prints one line per refusal to stdout (`invoke refused: TOKEN_EXPIRED`) so
`agentbreeder logs` shows it; no token, header or body.

### 6.4 Customer and session

- The customer is `sub`, or `guest` when absent. It becomes the ADK `user_id`, so
  `ToolContext.customer`, notes, tickets, `audit_events.customer`, `turns.customer` and the trace's
  `user.id` carry the verified value. No code path reads `customer_id` from the body.
- The session lock is keyed by `(customer, session)`. ADK sessions are keyed by `(app, user_id,
  session_id)`, so another customer's `session_id` opens an empty session for the token's customer
  and cannot read, continue or stall theirs. Tests pin both.
- No schema change: the 1.2.0 runtime writes `caller` (`cal`) and `jti` into the `detail` of the
  turn's first audit event, and `agentbreeder.caller` as a span attribute. Owner-managed schemas
  (`memory.owner_managed`) and the append-only trigger are unaffected. The `jti` cannot be reused
  once consumed, so recording it is safe; it lets a turn be matched to the signing call.

### 6.5 Callers

| Caller | Signs with | `cal` | Customer |
|---|---|---|---|
| `invoke` (local) | `k_invoke` derived from the deployment's host-only master | `invoke`, or `console` when run by the console | `--customer`, or none (`guest`) |
| `eval` / `release` (candidate) | the candidate deployment's master; `aud` = the candidate's id | `eval` | the case's `customer_id`, else the session id |
| `promote` (cluster evals through a forward) | the cluster deployment's host-only master (its Secret holds only `k_invoke`) | `eval` | as above |
| `token` | the named deployment's master |

The CLI always reads the host-only master (a new 0400 writer in `secret_files`; `write_generated`
writes 0444) and derives `k_invoke`; it never reads the mounted copy. `remove`, a failed deploy's
cleanup and the Kubernetes path delete both files. `token` | `--customer` |

`evals.run` takes a signer callable; `release` and `promote` pass one bound to the deployment they
evaluate. Evals run against the same image as production but with the candidate's key and
audience: a candidate token is refused by production (tested). Production gets its own key when
`release` deploys it.

### 6.6 The `token` command

`agentbreeder token NAME [--candidate] [--customer ID] [--ttl SECONDS] [--body-file FILE | -]
[--show] [--state-dir DIR | --stack DIR [--environment ENV]]` prints one token and nothing else,
for `curl` and the operator's scripts. The body comes from `--body-file` or stdin, never argv,
because `bh` binds it. `--ttl` default 60, at most 300. The output is a credential: it is printed
to a terminal only with `--show` (D6). The console cannot run it: the jobs allowlist
(`center/jobs.py`) does not include `token`, and a test pins that. Exits 2 with
`INVOKE_UNAUTHENTICATED` for a `none` deployment.

### 6.7 Reserved: external customer identity (D3)

A later operator mode would let the runtime accept an end user's IdP token (pinned issuer,
audience, JWKS) as the customer, next to the operator's signed token as the caller. Nothing in
1.2.0 accepts a token it did not sign.

## 7. Runtime: `runtime/adk@1.2.0`

`runtime/adk@1.1.0` stays byte-for-byte (locked versions are never edited). The new version, built
on 1.1.0 (MCP client included):

- `runtime/auth.py`: the verifier of §6.2–6.3 (standard library only). Shared test vectors in the
  repo must pass against both it and the CLI's signer (`src/agentbreeder/invocation.py`).
- `server.py`: §6.3 order; customer from the token; `(customer, session)` lock; refusal codes and
  lines; pre-header timeout. `GET /health` stays open (Docker and Kubernetes probes, `invoke`'s
  readiness check) and returns only `{"status": "ok"}`; nothing in the CLI or console reads its
  `model` field (checked on `90f117c`).
- `config.py`: reads `AGENTBREEDER_INVOKE_KEY_FILE` and `AGENTBREEDER_DEPLOYMENT_ID` at start; a
  missing, short or unreadable key fails the container at start, never an open `/invoke`.
  `AGENTBREEDER_DEV_PLAIN_SECRETS=1` does **not** apply to the key.
- `redaction.py`: remembers the key; adds JWT-shaped strings
  (`eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`) to the key-shaped patterns, so a token
  in exception text is redacted.
- Chat page (D2, recommended): `GET /` returns 404 with `{"error": "Use agentbreeder invoke or
  the console's Try it"}`. It no longer renders the agent's title or description to unauthenticated
  callers. The change is called out in the CHANGELOG and the runtime's docs.
- `builtin_registry/runtime/adk/1.2.0/manifest.yaml` declares `invocation: token-v1`; `build`
  copies it into the generated deploy manifest, and `Manifest` validates and keeps the field
  through every `model_validate` round trip (release and promote rebuild manifests).

## 8. Engine, CLI, console, sidecar and relay

### 8.1 Engine (`deployment.py`)

- For a deploy manifest with `invocation: token-v1`, mint the key (§6.1) and set
  `AGENTBREEDER_DEPLOYMENT_ID`. Audit event `invoke_key` / `minted` (deployment id only).
- The store records `invocation: token-v1 | none` per deployment.
- Operator config `invocation.require_authenticated: bool` per environment (D4). When true, a
  manifest without `invocation: token-v1` is refused (`INVOKE_UNAUTHENTICATED`, exit 2). The demo
  operator config `services up` writes sets it for stacks with `mcp:` entries. JSON Schema and
  `docs/identity-gate.md` are updated.

### 8.2 Sidecar contract (not built here; after #742 lets `runtime/adk` run under `mcp_http`)

When a later slice brings customer identity to `call_tool`, it follows these rules, so that the
agent cannot name a customer that no caller signed:

- **The agent never holds what proves the customer to the decision service.** The host derives a
  second key from the §6.1 master, `k_attr` (`info = "agentbreeder:attr:v1:<deployment id>"`),
  held only by the host decision service; the agent holds only `k_invoke` and cannot derive it. The CLI signs
  two tokens per call: the invoke token, and an attribution token under `k_attr` with the
  deployment, customer, `jti` and a lifetime no longer than the turn (180 s). The sidecar's
  ingress takes the attribution token from its own header, registers the turn with the decision
  service and strips the header before the agent sees the request.
- **Verification is on the host.** The sidecar holds no key; ingress asks the decision service
  (its existing bearer-authenticated, HMAC-answered channel) to verify. Its code digest and
  `docker inspect` checks change accordingly.
- **Each `tools/call` names its turn.** The runtime passes the turn's `jti` (not a credential)
  from a context variable read by the MCP client's per-request `header_provider` (keyed by the
  ADK invocation, never shared between concurrent turns; to be confirmed against ADK's
  `McpToolset` before that slice is specified). The decision service accepts it only for a turn
  that ingress registered and that is still open, and adds `context.customer` to `call_tool`.
  `cal` is never passed to policy.
- **Residual (T7):** the runtime knows the `jti` of every open turn, so compromised in-process
  code can attribute a tool call to another customer's **open** turn. It cannot invent a customer.
  A slice that wants to close this binds each call to a per-turn nonce that the sidecar issues on
  that turn's own path; until then docs state the residual.
- **Held calls bind the customer:** `binding_digest` includes it, so an approval for customer A's
  held call cannot release B's.
- This changes the PDP request schema (`egress/host.py` context keys), the Cedar schema and the
  policy docs; #742 owns those.
- The header is never forwarded upstream (`_FORWARD_HEADERS` excludes it, and a test pins it) and
  never sent on the relay path.

Until then, docs make no claim about customer identity in `call_tool`.

### 8.3 MCP relay (`mcp_access`)

The relay keeps authenticating the agent by its relay token and is not given invoke keys (its
grant directory is 0755 and holds only token hashes). What changes for relay-granted tools is
upstream: no turn starts without a verified token, so no tool call does. The relay does not learn
the customer; the runtime sends it nothing new, and a test with a recording MCP stub pins that no
token or customer header reaches the relay. Per-customer relay audit or sessions are D5 and #747.

### 8.4 CLI

- `src/agentbreeder/invocation.py` (new): `derive(master, deployment_id)` and `sign(k_invoke,
  deployment_id, customer, caller, body, ttl)`; the master is read only from the deployment's
  host-only file in state through `secret_files`. Neither value is ever printed, logged, put in
  `--json` or passed in argv.
- `invoke` (lane A owns `invoke.py`): signs for `token-v1` deployments and omits `customer_id` from
  the body; never sends `Authorization` to a `none` deployment. Its note becomes "The customer is
  signed with this deployment's key from your local AgentBreeder state; anyone who can read that
  state can sign any customer." For `none` deployments the old note stays. Runtime codes map to
  exit 2 when nothing was processed (`AUTH_REQUIRED`, `TOKEN_*`), as today's "nothing sent".
- `eval`, `release`, `promote`: §6.5.
- `status` and `list --json` add `invocation: token-v1 | none`; `next` and `your-first-agent.md`
  drop the "no authentication" warning only for `token-v1` deployments.
- `invoke` still refuses Kubernetes rows (M4); `forward` is unchanged.

### 8.5 Console (`center/**`, lane B and the owner's M2 session)

- Try it runs `invoke NAME -` as today, with a hidden `--caller console` flag (informational, not
  trusted); the server never reads the key or holds a token; `token` is not runnable.
- The panel's note (S-12(c)) becomes "The customer is signed with your local key. Anyone who can
  use this machine as you can sign any customer." For `none` deployments the old note stays.
- Agent detail shows `Invocation: signed` or `Invocation: open (runtime older than 1.2.0)`.

## 9. A2A (M19 #835) constraints this spec sets

- One agent never receives another agent's key (T12).
- An A2A call from agent A to agent B goes through a host-side broker (beside the relay or the
  decision service) that holds B's key, checks A's grant to call B, and signs B's token with
  `cal: a2a`, `sub` = the customer of A's verified turn, and `act: {"sub": "<A's deployment id>"}`.
  1.2.0 refuses `act`; the A2A runtime version accepts it. The broker's audit row names caller
  agent, callee agent and customer. M19's spec owns the rest, including how A's turn is verified
  to the broker (it needs §8.2's "agent never holds the proof" rule).

## 10. Tests and verification

Tests first (pytest; real Docker behind `AGENTBREEDER_DOCKER_TEST=1`, without `--cov` until #732):

- **Vectors** (signer and verifier): valid; each claim missing, wrong or wrong type (`exp` as
  string, float, bool; `aud` as array; `sub` empty, null, int, `guest`); `iat` in the future,
  `nbf > exp`, `exp - iat = 301`, skew edges; `alg` `none`/`HS512`/`RS256`; a `kid`, `jku` or extra
  header member; duplicate JSON keys; padding, whitespace, `+`/`/`; 2 KiB + 1; two `Authorization`
  headers; scheme case; wrong `aud`; `bh` mismatch; `act`. RFC 5869 HKDF vectors. A property test:
  random bytes only ever produce a refusal.
- **Clocks:** container clock ±59 s from the signer passes and ±61 s is refused, for both `nbf` and
  the start-time check; the CLI shows the clock hint.
- **Runtime:** no header → 401 before the body is read (a test server counts bytes read) with
  `Connection: close`; a 401 or 400 with the body unread still closes the connection; slow headers
  dropped after 10 s; a token issued before the process started → `TOKEN_EXPIRED`; two threads with
  one token → exactly one 200; a `bh` mismatch does **not** spend the `jti` (the right body then
  succeeds); a `jti` is still refused between `exp` and `exp + 60 s`; 10 000 live entries → 503
  `AUTH_BUSY` with no eviction; body `customer_id` mismatch → 400; `ToolContext.customer`, notes,
  `audit_events` and the span carry `sub`; another customer's `session_id` gives an empty session
  and cannot cause a 409; a missing key fails the start; `GET /` is 404; `GET /health` is open and
  returns exactly `{"status": "ok"}`; the agent's mounts hold `k_invoke` and never the master.
- **CLI:** `token` refuses a terminal stdout without `--show` and works into a pipe.
- **AC1 (real Docker):** a container on the stack's agents network posts with `Host: agent` and no
  token, with a token for another deployment, with a candidate token against production and a
  production token against the candidate, and with a replayed token: all 401. Loopback without a
  token: 401. `invoke` succeeds. On `kind`: a pod in the namespace without the key, through the
  Service: 401; the runtime's non-root user can read the key under the Secret's `defaultMode`
  0440 (`kubernetes.py`).
- **AC2:** an in-process tool sees the signed customer; with a relay-granted MCP tool, no tool call
  happens without a token, and the recording MCP stub sees no token or customer header.
- **AC3 canaries:** a test hook mints a canary key; scan image layers, `docker inspect` (env,
  labels; mounts show only the `_FILE` path), container logs including refusal paths, runtime
  stdout, a fake OTLP collector, `audit_events.detail`, the host audit chain, store rows,
  `center.sqlite3`, CLI `--json` output and error objects, exception text, the exported Kubernetes
  manifest, and the CLI's argv and environment during `invoke` (`ps`), for the key and every token,
  in literal, base64, URL and JSON-escaped forms (M3's `redaction.py` forms).
- **Old runtimes:** the CLI sends no `Authorization` to a `none` deployment.
- **AC4:** Northwind on `runtime/adk@1.2.0`: `release` 61/61 on local Docker with signed customers;
  run at implementation time with the coordinator's agreement (Docker disk budget).
  `ops offline_evals.py` is outside the 61/61 gate.
- Verification record `docs/superpowers/verification/780.md` from a wheel built from `main` in a
  clean sandbox: acceptance script, timings, leak scan, teardown to zero.

## 11. Migration

- Existing deployments keep running on their runtime version and stay unauthenticated; `status`
  and the console say so. Nothing is edited in place.
- An agent moves by changing `runtime/adk@1.1.0` (or older) to `@1.2.0` in `agent.yaml`, `build`
  (new `agent.lock` entry) and `release`. `new agent` writes `@1.2.0` once it ships (lane A).
- Northwind: its `agent.yaml` moves from `runtime/adk@1.0.6` to `@1.2.0` (taking 1.1.0's MCP
  client with it) and its `agent.lock` is regenerated. `northwind-ops` gets a new version (1.0.0 is
  not edited) whose `offline_evals.py` takes `--token-command` (runs `agentbreeder token …` per
  turn), since it calls the agent itself.
- Eval datasets that name `customer_id` keep working: the eval runner signs it.
- Raw `curl` users: `agentbreeder token` (§6.6); the 401 `AUTH_REQUIRED` body names it.
- CHANGELOG [Unreleased]: a breaking change for callers of 1.2.0 images, and the removed chat page.

## 12. Delivery slices

1. `invocation.py` signer + `runtime/adk@1.2.0` verifier, shared vectors, runtime unit tests.
2. Engine: mint, mount, `AGENTBREEDER_DEPLOYMENT_ID`, store field, `require_authenticated`, audit
   event; `build` and `Manifest` carry `invocation`; Kubernetes Secret path.
3. CLI: `invoke`, `eval`/`release`/`promote` signers, `token`, `status`/`list`; docs
   (`agent-yaml.md`, `services.md`, `commands.md`, `your-first-agent.md`, `identity-gate.md`,
   `local-deployment.md`); AC1 and AC3 real-Docker tests.
4. Northwind migration and AC4 (61/61); `new agent` template bump (lane A).
5. Console notes and agent-detail field (lane B), after M7.

The §8.2 sidecar contract becomes its own slice under #742 once `runtime/adk` can run under
`mcp_http`. Each slice: tests first, different-model review until approved, the quality gate,
owner approval.

## 13. Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | HMAC-SHA256 (standard library, the agent holds the key) or Ed25519 (`cryptography` becomes a core CLI dependency, the agent holds only a public key) | HMAC for `/invoke`; §8.2's HKDF split keeps the agent away from any customer proof used by policy |
| D2 | Chat page in 1.2.0: remove, keep with a token box, or a browser-session token opened from the CLI | Remove (`GET /` 404 with a pointer); the console's Try it replaces it |
| D3 | External customer identity (OIDC, §6.7): in this epic or later | Later, own spec, after a live IdP run |
| D4 | `require_authenticated` default: off, on for environments with MCP grants, or on everywhere | On for environments with MCP grants; on everywhere at 3.0.0 |
| D5 | Relay learns the customer (per-customer audit, later per-customer sessions with #747) | Not now; revisit with #747 |
| D6 | `token` prints to a terminal only with `--show` | Yes |
| D7 | Build §8.2 under #742 (so policies can use the customer) or leave customer out of `call_tool` | Under #742, after `runtime/adk` runs under `mcp_http` |
| D9 | CLAUDE.md's claim rule ("never claim runtime enforcement beyond MCP Streamable HTTP via the opt-in sidecar") read strictly also covers an in-container `/invoke` check. Amend it to allow exactly §2's wording? | Yes, scoped to `runtime/adk@1.2.0` `/invoke`, with "not protection against a compromised container"; no docs or website use the wording until the amendment and `verification/780.md` exist |
| D8 | Host-side record of each signed call (in the hash-chained audit log) | No: `invoke` opens state read-only so it never blocks a running job; the runtime's audit event records `jti` and `caller`. Revisit with a multi-user console (#775) |

## 14. Cross-repo split

| Repo | Change |
|---|---|
| `agentbreeder` (this) | everything in §6–§12; docs; CHANGELOG; JSON Schema for the operator config's `invocation` block and the deploy manifest's `invocation` field |
| `agentbreeder-cloud` | no code: it has no OSS version pin, and a grep of its tracked files found no `customer_id` or `/invoke` use (2026-11-13). Its `CLAUDE.md` gets a note when slice 2 merges (deploy manifest and operator config fields) |
| `website/` | nothing until release R (#836); then, only after D9's CLAUDE.md amendment and `verification/780.md`, `what-ships` may use §2's wording ("`runtime/adk@1.2.0` agents refuse unsigned requests …") and nothing broader |

## 15. Review log

Round 1 (r1 → r2), 2026-11-13. Reviewers: Claude Sonnet, read-only, one architecture lens and one
`/security` lens (STRIDE), each given the spec and the code, not the author's conclusions. Each
finding was checked against `90f117c` before it was applied.

| Finding | Severity | Resolution |
|---|---|---|
| A1: §8.2 cannot be built: `runtime/adk` cannot run under `mcp_http` (`MODEL_ACCESS_UNSUPPORTED`, `MCP_RELAY_UNSUPPORTED`); the MCP client speaks only to the relay | High | Confirmed. Sidecar moved out of scope; §8.2 is a contract for #742; AC2 narrowed |
| A2: release evals run against the candidate's key and `aud`, not production's | High | Confirmed (`release.py` deploys `<name>-candidate`, then production separately). §6.5 rewritten; candidate-vs-production test added |
| A3 / S16: docs wording overclaims an in-container check | High | §2 states the exact wording, T7 "not defended", `invocation: none` for other images, website row limited to it |
| S1 / A5: HMAC key in the agent lets it forge `call_tool` customer; `cal` fed to policy | High | §8.2 HKDF split (`k_invoke` / `k_attr`), attribution token stripped at ingress; `cal` never used by policy |
| S2: the forwarded token is not bound to a turn | High | §8.2: turn registered at ingress; tool calls name the turn's `jti`; TTL ≤ 180 s |
| S3: session lock keyed by session only (cross-customer 409) | High | Confirmed (`server.py` `session_lock`). Lock by `(customer, session)`, T11, test |
| S4 / A9: `jti` consumption point, restart replay, full cache, concurrency | Medium | Refuse tokens issued before process start; lock and concurrent-replay test; CLI TTL 60 s. (The consumption point and full-cache rule were revised again in round 2: R2-3, R2-4) |
| S5: JWS parsing pitfalls | Medium | Strict parsing rules and the vector list in §6.2 and §10; `kid` dropped |
| S6: slow clients and keep-alive | Medium | 10 s header timeout, `Connection: close` on refusals |
| S7: refusal codes reveal signature validity; `GET /` leaks title | Medium | `TOKEN_INVALID` for anything before a valid signature; chat page removed (D2) |
| S8: shared MCP toolset and per-turn headers | Medium | §8.2 requires a per-invocation context variable and a two-customer concurrency test; to be confirmed against ADK |
| S9: `token` output and the console | Medium | `--show` for terminals; not in the console's jobs allowlist (tested) |
| S10: tokens in exception text not redacted | Medium | JWT-shaped pattern added to runtime redaction; canaries cover exception text |
| S11 / A6: Kubernetes Secret exposure; promote path; "promote mints" | Medium | T9 extended (etcd, Secret readers); §6.1 and §6.5 cover `deploy --cluster` and promote evals; wording fixed |
| A4: where the sidecar's key lives | Medium | §8.2: no key in the sidecar; verification by the host decision service |
| A7: new columns break owner-managed schemas and the append-only table | Medium | Confirmed (`memory.owner_managed`). No schema change: `caller` and `jti` go in the event's `detail` |
| A8: evals post `customer_id`; Northwind gate | Medium | `evals.run` takes a signer; body field omitted; `offline_evals` outside the gate |
| S13: no host-side audit of signing | Low | Not applied: `invoke` opens state read-only (M4 design) so it never blocks jobs; D8 records the choice |
| S14: no key rotation | Low | Rotation is a redeploy (§6.1) |
| S15 / S17 / A10: tests and nits | Low/Info | §10 extended (relay stub, old runtime, `kind` AC1, property test, exception canaries); `AGENTBREEDER_DEPLOYMENT_ID` marked new; `GET /` 404 called out |
| A11: PDP and Cedar schema change | Info | Noted in §8.2 for #742 |
| S12: the sidecar's gateway port is reachable from other containers | Low | Not applied: the agent's `--internal` network holds only the agent and its sidecar (`egress/boundary.py` topology), so no other container reaches it |

Round 2 (r2 → r3), 2026-11-13. One Claude Sonnet reviewer, both lenses, read-only, asked to check
the round-1 resolutions and look for new problems. No High findings.

| Finding | Severity | Resolution |
|---|---|---|
| R2-1: mounting one key in 1.2.0 makes §8.2's HKDF split a later format break | Medium | §6.1: a host-only master; the agent gets only `k_invoke` (HKDF, RFC 5869 vectors); §8.2 adds `k_attr` |
| R2-2: §8.2 "no attribute the agent could forge" overclaims: open turns' `jti`s are visible to the runtime | Medium | Sentence narrowed to "cannot name a customer no caller signed"; residual stated |
| R2-3: consuming `jti` before `bh` lets an observer burn a token | Low/Medium | `jti` consumed after the `bh` check; test that a wrong body does not spend it |
| R2-4: eviction fails open; retention must cover `exp` + skew | Low | Retained to `exp + 60 s`; full cache → 503 `AUTH_BUSY`, no eviction; tests |
| R2-5: start-time check and `nbf` vs Docker Desktop clock drift | Medium | "Clocks" paragraph, CLI hint, ±59/±61 s tests; T4 states the restart residual |
| R2-6: "D8" cited for rotation | Low | Fixed |
| R2-7: missing tests | Low | Added (retention, cache full, unread body close, `/health` shape, `kind` `defaultMode`, production-vs-candidate, `token --show`) |
| R2-8: T2 and §2 need the T9 caveat | Low | §2 wording and T2 carry it |

Round 3 (r3 → r4), 2026-11-13. One Claude Sonnet reviewer, both lenses, read-only. All round-2
resolutions were confirmed consistent, as was the §6.3 order. Two findings, both applied; no
further round was needed for them, since neither changes the design.

| Finding | Severity | Resolution |
|---|---|---|
| R3-1: "refuse unsigned requests" is an in-container enforcement claim that CLAUDE.md's rule, read strictly, forbids | Medium | D9 added; §14's website wording gated on the amendment and the verification record |
| R3-2: ambiguous whether the CLI signs from the master or the mounted `k_invoke` | Low | §6.5 and §8.4: the CLI reads the master and derives; cleanup deletes both files |
