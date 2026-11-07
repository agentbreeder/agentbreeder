# Agent identity gate

`agentbreeder deploy` refuses to start an agent unless it is already onboarded in an external
identity provider (IdP). **Verify, don't mint:** `agent.yaml` references and requires; the IdP
grants. AgentBreeder never creates identities and its adapters are read-only (IdP disable is an
explicit opt-in).

> **Guarantee boundary.** The gate is a deploy-time check. Tool calls are mediated at runtime only
> when the operator enables `runtime_enforcement.mode: mcp_http`, and only for MCP Streamable-HTTP
> servers reached through the sidecar ([Runtime tool enforcement](#runtime-tool-enforcement)).
> STDIO and in-process tools are never mediated. Anyone with Docker-socket or state-directory
> access can bypass local controls; the gate is a hard control only behind a server-side executor.
> These checks are "aligned with" OWASP NHI guidance, not a compliance claim.

Design: `docs/superpowers/specs/2026-11-05-agent-identity-gate-design.md` · Epic #701.

## What runs on every deploy

The service allocates an attempt ID and durably audits `attempt_started` **before** contacting
any IdP or PDP. Under the lifecycle lock it then checks:

| # | Check | Deny code |
|---|---|---|
| — | `--environment` exists and allows the target (operator mapping) | `CONTEXT_ENVIRONMENT_UNKNOWN`, `CONTEXT_TARGET_NOT_ALLOWED` |
| — | Configured controls are enforceable in this installation | `CONTROL_UNSUPPORTED` |
| — | With `mcp_http`, every declared MCP server is an operator destination | `DESTINATION_UNAPPROVED` |
| — | Verified deployer when required (always when a PDP is configured) | `DEPLOYER_UNVERIFIED` |
| — | Manifest restrictions need a configured PDP | `POLICY_UNAVAILABLE` |
| 8 | No secrets (local slice cannot inject them) | `SECRETS_UNSUPPORTED` |
| — | `schema_version: 2` with an `identity` block | `IDENTITY_REQUIRED` |
| 1 | `identity.ref` resolves to exactly one principal | `PRINCIPAL_NOT_FOUND`, `PRINCIPAL_AMBIGUOUS`, `PROVIDER_TIMEOUT`, `PROVIDER_ERROR`, `PROVIDER_UNKNOWN` |
| — | No administrative disable for the principal/environment | `PRINCIPAL_BLOCKED` |
| 2 | Kind is `agent` or `workload` (not human/shared) | `PRINCIPAL_KIND_REJECTED` |
| 3 | Enabled in the IdP | `PRINCIPAL_DISABLED` |
| 4 | An active human sponsor; `expect_owner` matches | `SPONSOR_MISSING`, `OWNER_MISMATCH` |
| 5 | Not past `review_by` | `REVIEW_EXPIRED` |
| 6 | Scoped roles, stable group IDs and exact namespaced attributes are granted | `ROLE_NOT_GRANTED`, `GROUP_NOT_GRANTED`, `ATTRIBUTE_MISMATCH` |
| 7 | `identity.environment` = IdP environment = `--environment` | `ENVIRONMENT_MISMATCH` |
| — | With `maker_checker`, the verified risk attribute must be present | `RISK_UNVERIFIABLE` |
| 9 | PDP baseline AND every selected restriction allow `deploy` | `POLICY_DENIED`, `POLICY_ERROR`, `POLICY_INVALID`, `POLICY_DIGEST_MISMATCH`, `POLICY_REVISION_MISMATCH`, `RESTRICTION_UNAPPROVED`, `OBLIGATION_UNSUPPORTED`, `POLICY_UNAVAILABLE` |

A deny exits with code 2 and prints `[CODE] message (attempt <id>)`. No Docker call happens on
deny. Role and group matching uses immutable IDs with their authority/resource scope; display
names never satisfy a requirement, and a same-named grant in another resource or tenant does not
match.

Every result reports `checks_evaluated` and `checks_unavailable`. Without a PDP the output says
**identity preflight only** and lists `policy`, `approval` (and `deployer_verification` for an
unverified OS user). Treat that as a partial guarantee.

## Operator configuration

Enforcement lives outside the agent repository in `~/.agentbreeder/operator.yaml` or
`$AGENTBREEDER_OPERATOR_CONFIG`. The file must be owned by you with mode `0600` (or owned by root
and not group/world-writable); symlinks are rejected. A missing default file means `enforce` with
no environments, so every deploy fails closed.

```yaml
enforcement: enforce            # enforce | audit (no "off")
default_environment: dev
environments:
  dev:  { targets: [local] }
  prod: { targets: [local] }    # explicit operator pairing; manifests cannot add one
providers:
  corp:                         # the manifest's identity.provider names this connection
    type: entra
    tenant_id: 00000000-0000-0000-0000-000000000000
    client_id: 11111111-1111-1111-1111-111111111111
    credential: env:AGENTBREEDER_ENTRA_SECRET   # env:NAME or file:/path; a literal fails at first use (PROVIDER_ERROR)
  lab: { type: static, authority: lab, file: principals.yaml }
deployer:
  require_verified: false
  issuers:
    - issuer: https://token.actions.githubusercontent.com
      audience: agentbreeder
      jwks_uri: https://token.actions.githubusercontent.com/.well-known/jwks
pdp:
  engine: cedar                 # cedar | opa | authzen
  baseline: { source: baseline.cedar, digest: "sha256:<64 hex>" }
  approved_restrictions:
    pci: { source: pci.cedar, digest: "sha256:<64 hex>" }
require_sponsor: true
risk_attribute: acme-governance/risk_tier   # verified principal attribute
maker_checker: { risk_tiers: [high] }
approval_ttl_seconds: 900
runtime_enforcement:
  mode: mcp_http                            # none | mcp_http (needs a PDP)
  limits: { max_body_bytes: 1048576, calls_per_minute: 60 }
  destinations:
    github:                                 # agent calls http://mcp-gateway.internal:8080/mcp/github
      url: https://mcp.example.com/mcp      # must equal a manifest capabilities.tools[].mcp
      credential: env:GITHUB_MCP_TOKEN      # stays on the host; never enters a container
      # token_exchange: { token_endpoint: https://as.example.com/token, audience: mcp }
      # allow_private: false                # private/loopback resolution refused by default
      # ca_file: private-ca.pem             # 0600; only for a private CA
```

**`audit` mode** evaluates and records everything but proceeds past identity (1–7) and PDP
denies, printing a prominent warning and auditing both the evaluated deny and the override. It
never bypasses blockers, unknown context, unsupported controls, audit-write failures or
approval obligations. After an overridden identity deny it still evaluates policy, so maker-checker
approval still applies. When a PDP or maker-checker is configured, an unresolved principal is not
overridable. Blockers are also checked by provider and reference before resolution, so an IdP outage
cannot hide them.

## Identity providers

| Type | Status | Principal | Notes |
|---|---|---|---|
| `static` | Stable | Operator-controlled YAML file (`0600`) | Tests and air-gapped use |
| `entra` | Fixture-tested | Graph v1.0 `servicePrincipals/{id}/microsoft.graph.agentIdentity` | Roles keep `resourceId` + `appRoleId`; groups via `transitiveMemberOf`; sponsors; custom security attributes (`set/name`). Verify against a live tenant with `pytest -m entra_live` before production |
| `keycloak` | Fixture-tested | Confidential client UUID with a service account | Realm roles use resource `realm`; client roles use the client UUID; sponsors/environment/review from service-account attributes |
| `okta` | **Experimental** (`experimental: true` required) | Service application | Okta's agent API is Beta; role semantics unverified. App group *assignments* are not memberships, so `requires.groups` always denies |
| `aws` | Fixture-tested, needs `agentbreeder[aws]` | AgentCore workload identity | Facts from `agentbreeder:*` tags; no enabled flag (use local disable); IAM role grants are not reported, so required AWS roles deny. Identity Store has no user status, so sponsors count as active only with `trust_identity_store_presence: true` |

Adapters time out after 10 s; any error, timeout, ambiguity or unsupported shape fails closed.
Plugins register under the entry-point group `agentbreeder.identity_providers` and cannot shadow a
built-in type.

## Verified deployers

The OS user is recorded as unverified. To verify, provide an RS256 OIDC ID token from an
operator-trusted issuer: `AGENTBREEDER_OIDC_TOKEN`, `AGENTBREEDER_OIDC_TOKEN_FILE`, or
`agentbreeder login` (RFC 8628 device flow; caches the token at `~/.agentbreeder/oidc-token`,
mode `0600`). Issuer, audience, expiry and signature are checked against the operator's JWKS.
Git configuration is never used as identity. Each trusted issuer has `kind: workload` (the default,
for example CI) or `kind: human`. Only `human` identities may approve or enable.

## Policy decision point

The request is shaped like an OpenID AuthZEN 1.0 access evaluation. The operator's pinned baseline
and every manifest-selected **operator-approved** restriction must allow; any deny or error wins.
Manifests can name only restriction bundle IDs, never paths or OCI references.

- **Cedar** (default, `agentbreeder[cedar]`, pinned `cedarpy`): each source's bytes are read once,
  checked against the pinned digest, validated against the shipped schema
  (`src/agentbreeder/policy/cedar_schema.json`) and evaluated. Actions: `deploy`, `approve`,
  `enable` (principal `Deployer`, resource `Agent`) and `call_tool` (principal `Agent`, resource
  `Tool`). A source permitting `RequireApproval` (or `RequireCallApproval` for tool calls) adds the
  `require_approval` obligation, so restrictions can add approvals but never remove them.
- **OPA**: `POST <url>/v1/data/<path>?provenance=true`; `result` must be
  `{allow: bool, obligations: [...]}` and the provenance revision must equal the configured
  `revision`. Set `bundle` to the bundle name OPA reports (for `--bundle /policy`, that is
  `/policy`); without it the top-level revision is used, which file bundles do not report, so the
  check fails closed. Parity with Cedar was verified against OPA 1.4.2.
- **AuthZEN**: `POST <url>/access/v1/evaluation`; `context.policy_revision` must equal the
  configured `revision`.

Remote PDP URLs must be HTTPS or loopback HTTP. Decisions record policy IDs, digests/revisions,
the operator config digest and the Cedar schema version.

## Maker-checker approvals

A `require_approval` obligation (from policy, or `maker_checker.risk_tiers` matching the verified
risk attribute) persists a pending request and exits with code 3. No Docker call is made. The
request is bound to a revision digest covering the full manifest, image digest, principal, deployer,
environment/target, operator config digest, policy digests, schema version and obligations, plus the
principal evidence digest. It expires after `approval_ttl_seconds`.

```sh
agentbreeder approve <approval-id>   # a second, verified, policy-authorized person
agentbreeder deploy agent.yaml --local --environment prod   # resume = rerun
```

The checker must be a verified `human` identity. It must not be the deployer (compared across
subject, email and username claims) or the principal's sole sponsor. Policy must allow `approve`
without obligations. On resume, checker authority and the sole-sponsor rule are re-checked. On resume the whole gate runs again; a changed revision or evidence,
expiry, revoked checker authority or a new deny invalidates the approval. Consumption is atomic with
the deployment's execution intent. Crash recovery may reuse it only for that same execution.

## Kill switch and lifecycle

```sh
agentbreeder disable <deployment-id|name> --local [--idp]
agentbreeder disable - --local --provider corp --ref <id> --environment prod
agentbreeder enable <deployment-id|name>     # verified human + a PDP that permits `enable`
agentbreeder reconcile --local               # compliance marks + blocked cleanup retries
agentbreeder status <id>                     # re-resolves the principal: compliant/noncompliant
```

`disable` persists a blocker keyed by provider connection, authority, immutable principal ID and
environment, and audits it. It then invalidates open approvals and stops and removes every affected
container (and its runtime boundary); the deployment record stays, in state `disabled`.
The blocker survives retries, restarts, renames and deployment removal/recreation, and audit mode
cannot bypass it. If stopping fails, the blocker stays, the failure is recorded and the command
exits 1. `disable` needs no verified actor. `enable` requires a verified human identity and a
configured PDP that permits `enable`, so without a PDP a blocker cannot be cleared from the CLI.
`enable` clears only the local blocker. It starts nothing, revives no approval and never
re-enables an IdP principal. Disabling clears the approval binding of every affected deployment,
so a later deploy always needs a fresh gate and, where required, fresh approval. Deployments made in
audit mode without a resolved principal are matched by manifest provider and reference.
`reconcile` exits 1 when blocked cleanup still fails, 4 when any deployment is noncompliant or
unverifiable (so scripts and CI can gate on drift), and 0 otherwise. `--idp` additionally disables the principal in Entra/Okta when the
connection sets `write_enabled: true`.

## Audit log

`agentbreeder audit list` and `agentbreeder audit verify` read the append-only, hash-chained
`audit` table in the state database. Denials before a deployment exists have no deployment ID. A
deployment's state transitions and their audit events commit in one transaction. If an audit write
fails before execution, nothing is deployed, in both enforce and audit modes. If it fails after a
container started, the error says so and points to `reconcile`. Removals are audited too. Verification reports incomplete
(crashed) attempts, including executions that started but never reached a terminal state; it never
counts them as allows.

**Integrity limits.** Verification detects corruption and edits that do not recompute the chain.
It does **not** detect a database owner recomputing the whole chain, replacing the database or
deleting a valid tail. This is not adversarial tamper-evidence. That claim needs an independent
checkpoint or external witness, which is not implemented.

## Runtime tool enforcement

`runtime_enforcement.mode: mcp_http` puts each local deployment behind an egress boundary
(design: `docs/superpowers/specs/2026-11-05-identity-gate-phase4-egress-design.md`, option C).
It needs a configured PDP; every MCP server the manifest declares must be an operator destination.

**Topology.** The agent joins only a per-deployment Docker `--internal` network: no route off the
host, no published ports, all capabilities dropped. On Linux engines the network is created with an
isolated gateway (`gateway_mode_ipv4/ipv6=isolated`), so the host has no address on it. Linux
needs Docker Engine 28 or later; older engines, or networks without the option, fail closed. A sidecar (stdlib-only code on a digest-pinned
`python:3.12-slim`, built locally and labelled with its code digest) joins that network as
`mcp-gateway.internal` and one egress network. It publishes the agent's HTTP port on host
loopback (ingress) and is the agent's only way out. The agent finds its servers in
`AGENTBREEDER_MCP_SERVERS` (`{"github": "http://mcp-gateway.internal:8080/mcp/github"}`).

**Verification before traffic.** Deploy proceeds only if `docker inspect` shows the expected
internal network, members, labels, unprivileged containers and sidecar code, and a probe container
sharing the agent's network namespace finds that public IPv4/IPv6 addresses, the network gateway
on common ports, public DNS names and `host.docker.internal` are all unreachable, while the gateway
answers with the recorded deployment, policy revision and operator config digest. Any failure
removes everything, audits the deny and exits 2 with `RUNTIME_BOUNDARY_UNVERIFIED`. There is no
degraded mode, so Docker contexts without internal networks cannot deploy. Until verification
passes, a serving latch makes the decision service refuse every call; the latch is closed again
before any restore.

**Every call** (`POST /mcp/<server>`, MCP revision 2026-07-28):

1. The server must be an approved destination; the JSON-RPC body is authoritative. Malformed JSON,
   duplicate keys, non-finite numbers, batches, client responses, unsupported methods, oversize
   bodies and chunked uploads are refused. `Mcp-Method` (and `Mcp-Name` for `tools/call`) must
   equal the body. What is forwarded is the re-serialized validated message, never the raw bytes,
   and the forwarded `Mcp-Method`/`Mcp-Name` headers are rebuilt from it (`Mcp-Name` only for
   `tools/call`).
   A `tools/call` for a tool the manifest does not declare for that server is refused here too.
   These sidecar refusals forward nothing and are not audited.
2. A per-tool rate limit applies (`calls_per_minute`).
3. The host decision service re-checks the operator config pin, the deployment revision and the
   kill switch, then evaluates `tools/call` as `call_tool` for the deployment's verified principal
   with baseline AND restrictions, using the principal's full roles and groups pinned at deploy
   time (not the truncated audit snapshot). The tool must also be a declared capability action.
   The kill switch and revision are checked again just before the audit write.
4. The audit row (`tool_call`: allow, deny or held, with the canonical arguments digest, never the
   arguments) is written **before** the call is forwarded. If the audit write fails, nothing is
   forwarded (HTTP 503).
5. The sidecar strips the agent's `Authorization` header, adds the broker's credential, and
   originates TLS to the destination with normal certificate validation. Every resolved address
   must be public unless `allow_private` is set; an IPv6 form that embeds an IPv4 address (NAT64,
   6to4, Teredo, IPv4-mapped) must embed a public one. Redirects are refused, not followed.

`initialize`, `tools/list`, `ping` and notifications carry no tool effect: they are validated,
rate-limited, kill-switch checked and forwarded, but not policy-evaluated or audited.

**Held calls.** `require_approval` (from `RequireCallApproval` or `capabilities.approval_required`)
holds the call and returns JSON-RPC error `-32002` with `held_call_id` and `retry_after`. A second
person approves out of band with the same checker rules as deploy approvals:

```sh
agentbreeder approve-call <held-call-id>   # or reject-call
```

The agent then retries the identical call. A release is bound to the deployment, server, tool,
arguments digest, MCP session (`Mcp-Session-Id`), principal and its pinned roles, groups and risk
tier, deployment revision, operator digest and policy digests, expires after
`approval_ttl_seconds` and is consumed exactly once. Different arguments are a new request. The
checker is judged against the attributes pinned at deploy; if the IdP principal has changed since,
`approve-call` denies with `PRINCIPAL_CHANGED` until you redeploy. `Mcp-Session-Id` values over 256
characters are refused.

**Credential broker.** Destination credentials (`env:`/`file:`) are resolved by the host decision
service for each call. With `token_exchange` they are exchanged per call for an audience-restricted
token (RFC 8693). Only the resulting `Authorization` value reaches the sidecar; the agent never holds
tool credentials and its own `Authorization` is never forwarded.

**Failure semantics.** Sidecar crash: the agent loses all egress; `reconcile` restarts the decision
service and sidecar and re-verifies before traffic, and tears the deployment down (`failed`,
`noncompliant:RUNTIME_BOUNDARY_UNVERIFIED`) if it cannot. A failed deployment whose teardown
could not be verified is retried on every `reconcile` (`:cleanup_failed`, exit 1, until it succeeds). A
deploy interrupted before verification (left `starting`) is torn down and marked failed. PDP error: deny. Audit failure: not
forwarded. `disable`: the blocker denies every call immediately, then containers stop. An operator
config change makes the sidecar refuse calls until you redeploy. Runtime decisions are always
enforced, even when `enforcement: audit`.

**Decision-service transport.** Linux engines use a Unix socket mounted read-only into the sidecar
only. Docker Desktop cannot bind-mount host Unix sockets (`EOPNOTSUPP`), so there the sidecar
reaches the host service on a loopback port through `host.docker.internal`. Both paths require a
per-deployment bearer token held only by the sidecar; it reaches the `docker` CLI through its
environment, never its command line. Because any local process can bind a loopback port, every
decision-service reply carries an HMAC over the caller's nonce, keyed by the token hash in the
`0600` pin: the sidecar refuses unauthenticated replies (503, nothing forwarded) and `reconcile`
only trusts a service that answers its own challenge and is still the process it started. The
service bounds each connection to 5 seconds, so an idle client cannot stall it. Verified on Docker
Desktop (macOS); the Linux socket path is unit-tested but not yet verified on a Linux host.

**Limits.** The sidecar trusts the Docker engine and host; a Docker-socket user can still bypass
it. Upstream responses are capped at 16 MiB; a stream that exceeds it ends with an explicit
`RESPONSE_TOO_LARGE` error event. Only MCP Streamable HTTP POST is supported: server-initiated `GET` streams and `DELETE` session
termination get 405.

## Not implemented

- **Phase 5:** GCP/Databricks anchors, SPIRE and draft protocols (ID-JAG, Transaction Tokens).
  These depend on non-local targets.
- Break-glass deploys during an IdP outage (deliberately absent).
