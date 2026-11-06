# Agent identity and authorization gate — design

- **Status:** Phases 0–3 implemented (see `docs/superpowers/plans/2026-11-05-agent-identity-gate.md`, `docs/identity-gate.md`); phase 4 implemented per `2026-11-05-identity-gate-phase4-egress-design.md` (option C); phase 5 blocked on non-local targets. Security review still required
- **Date:** 2026-11-05
- **Tracking:** epic #701; phases #702 (P0), #703 (P1), #704 (P2), #705 (P3), #706 (P4), #707 (P5)
- **Builds on:** `2026-09-15-local-deployment.md`, `ARCHITECTURE.md`, `docs/manifest.md`

## 1. Problem

Today `agentbreeder deploy --local` runs any valid manifest. `DeploymentService.deploy()`
(`src/agentbreeder/deployment.py`) checks the target, secrets and name collisions, then calls
`Runtime.authorize()`, which only verifies that the Docker context is local. There is no agent
identity, no owner, no RBAC/ABAC and no audit record of *who* deployed *what* under *which authority*.

Enterprises already onboard people with a role, a manager, approval limits and a revocation path
before they get access. Agents should be onboarded the same way, using the identity systems the
enterprise already runs (Microsoft Entra / Active Directory, Okta, Keycloak, AWS, GCP, Databricks
Unity Catalog, SPIFFE). Identity vendors now model agents as first-class principals (for example
Entra `agentIdentity`, Okta workload principals, AWS AgentCore workload identities, GCP per-agent
SPIFFE principals), so AgentBreeder does not need to invent its own directory.

## 2. Goal

**Verify, don't mint.** `agentbreeder deploy` must refuse to deploy an agent unless:

1. the agent references an identity that already exists in an external identity provider (IdP);
2. that identity is an agent/workload principal, enabled, sponsored by an active human and not past
   its review date;
3. every role, group and attribute the manifest declares is already granted in the IdP;
4. a policy decision point (PDP) allows the verified deployer to deploy this agent to this
   environment;

and it must durably record the evidence for every allow/deny decision. The local hash-chained audit
log detects corruption and edits that do not recompute the chain; it does not prove integrity against
a user who controls the database (see §4.6).

`agent.yaml` **references and requires**; the IdP **grants**. Nothing in the manifest can grant a
permission.

## 3. Non-goals

- Creating, modifying or deleting identities in an IdP (adapters are read-only; disable in phase 3
  is an explicit opt-in).
- Running or inspecting the agent's LLM loop. AgentBreeder deploys user-supplied images.
- Being an IdP, an approval-UI product or a SIEM.
- Numeric business thresholds in `agent.yaml` (they live in policy).
- Any claim of regulatory compliance. Documentation may say "aligned with" OWASP NHI / OWASP Agentic
  Top 10 / NIST NCCoE guidance; it must not say "compliant".
- Runtime enforcement before phase 4. Until then docs must state that the guarantee is deploy-time
  only.

## 4. Design

### 4.1 Manifest (schema_version 2)

```yaml
schema_version: 2
name: refund-agent
image: ghcr.io/acme/refund-agent@sha256:...
target: local

identity:                       # reference only; never created by AgentBreeder
  provider: entra               # static | entra | keycloak | okta | aws | gcp | databricks
  ref: 4f1c...                  # provider-specific stable ID
  environment: prod             # must match the IdP attribute and the deploy environment
  expect_owner: alice@acme.com  # optional cross-check against sponsors/owners

requires:                       # must be a subset of what the IdP grants
  roles:
    - { authority: acme-tenant-id, resource: payments-api-id, role_id: refunds-role-id }
    - { authority: acme-tenant-id, resource: crm-api-id, role_id: crm-read-role-id }
  groups:
    - { authority: acme-tenant-id, group_id: finance-group-id }
  attributes:
    acme-governance/data_classification: confidential
    acme-governance/risk_tier: high

capabilities:                   # deploy-time ceiling; enforced at runtime in phase 4
  tools:
    - mcp: https://mcp.acme.internal/payments
      actions: [refund.create, refund.read]
  approval_required: [refund.create]

policy:
  restrictions: []              # optional operator-approved bundle IDs; may only restrict
```

- `schema_version: 1` manifests keep validating. Under operator mode `enforce` they are rejected
  by the gate with an actionable message ("add an `identity` block"); under `audit` they deploy
  with a logged warning.
- The model stays `extra="forbid"` and `strict=True`; the JSON schema in
  `docs/manifest.schema.json` and `docs/manifest.md` are updated together.
- `identity.environment` is an expectation, not the source of deployment context. `target` selects
  the runtime adapter (`local` or, later, `kubernetes`), not an environment such as `prod`.
- Role and group requirements use immutable provider IDs with their authority/resource scope.
  Human-readable names are display metadata and cannot satisfy requirements (see §4.3).
- The manifest cannot select or replace the baseline authorization policy. `policy.restrictions`
  names only bundles approved and pinned by the operator; arbitrary paths and OCI references in
  the manifest are rejected. Author restrictions can never broaden an operator allow decision.

### 4.2 Operator configuration

Enforcement settings live **outside** `agent.yaml` so an agent author cannot switch the gate off:
`~/.agentbreeder/operator.yaml` (or `AGENTBREEDER_OPERATOR_CONFIG`), owner-only permissions like the
state directory.

```yaml
enforcement: enforce            # enforce | audit   (default: enforce)
providers:
  entra: { tenant_id: ..., client_id: ..., credential: secret://... }
environments:
  prod: { targets: [local] }    # explicit operator-approved environment/target pairing
pdp:
  engine: cedar                # cedar | opa | authzen
  baseline: { source: /etc/agentbreeder/policy, digest: "sha256:..." }
  approved_restrictions: {}    # bundle ID -> operator-controlled source and pinned digest
require_sponsor: true
maker_checker: { risk_tiers: [high] }
approval_ttl_seconds: 900
```

`audit` exists only for gradual rollout: decisions are still evaluated and logged, and the CLI
prints a prominent warning. There is no "off" mode. Tests use the `static` adapter rather than
skipping the gate.

The deployer selects an environment with `--environment <name>` from this operator-controlled
mapping (or an explicit operator-configured default). The executor checks that the selected target
is allowed there and passes an immutable `DeploymentContext(environment, target)` to the gate.
The manifest cannot create an environment or change its target mapping. Missing or unknown context
fails closed; `prod` is compared with the principal's environment, never with the string `local`.

From phase 2, baseline and restriction sources must be operator-controlled. Resolve each bundle once, verify its
pinned digest, and evaluate the same immutable bytes that are hashed and recorded. Missing baseline,
unapproved restriction, digest mismatch or evaluation error denies deployment. For a remote PDP,
the operator configures the trusted endpoint and expected policy revision; the adapter must obtain
that evaluated revision from the PDP and reject an unverified/mismatched revision rather than treating
a client-supplied digest as evidence of what the server evaluated.

### 4.3 Identity-provider adapters

```python
class IdentityProvider(Protocol):
    name: str
    def resolve(self, ref: str) -> AgentPrincipal: ...

@dataclass(frozen=True)
class RoleGrant:
    authority: str               # tenant/account/realm/issuer, from the configured adapter
    resource: str                # immutable resource/application ID
    role_id: str                 # immutable role ID within that resource

@dataclass(frozen=True)
class GroupGrant:
    authority: str
    group_id: str                # immutable group ID

@dataclass(frozen=True)
class AgentPrincipal:
    provider: str
    authority: str
    id: str
    kind: Literal["agent", "workload", "human", "shared", "unknown"]
    enabled: bool
    sponsors: tuple[Person, ...]   # resolved, with active flag
    owners: tuple[Person, ...]
    roles: frozenset[RoleGrant]
    groups: frozenset[GroupGrant]
    attributes: Mapping[str, str]
    review_by: date | None
    environment: str | None
    evidence_digest: str           # sha256 of the normalized raw response
```

- Adapters are read-only and need only read scopes. They must time out (default 10 s) and raise a
  typed error. Any error, timeout or ambiguity fails closed.
- Plugins register through the entry-point group `agentbreeder.identity_providers`.
- Adapters derive authority from the trusted provider connection and preserve complete grant scope.
  Entra roles retain both `resourceId` and `appRoleId`; Keycloak client roles retain the realm and
  client ID. Realm-wide roles use an explicit realm resource namespace. Identically named roles in
  another resource/authority never match. Group membership uses stable IDs, not display names.
  Attribute keys preserve provider namespaces; comparison is exact key/value containment, not a
  comparison of keys alone. Author-supplied attributes cannot overwrite verified principal facts
  (including `risk_tier`) when building policy inputs. Unsupported or ambiguous mappings deny.
- Build order: `static` (YAML/JSON file, for tests and air-gapped use) → `entra` (Graph v1.0
  `agentIdentity`, `appRoleAssignments`, `transitiveMemberOf`, `sponsors`,
  `customSecurityAttributes`) → `keycloak` / generic OIDC + SCIM → `aws` → `okta` (experimental:
  the AI-agent API is Beta) → `gcp` and `databricks` (identity is created at deploy time, so they need
  an anchor principal or a post-provision, pre-traffic check).
- Entra typed lookup is `GET /v1.0/servicePrincipals/{id}/microsoft.graph.agentIdentity`;
  verify its permissions and response shape in phase 1 fixtures before shipping.
- Provider facts still to verify before each adapter ships: Okta role/assignment APIs for agents;
  AWS `SimulatePrincipalPolicy` for AgentCore;
  Databricks ABAC policy listing endpoint.

### 4.4 Policy decision point

The internal contract is shaped like an **OpenID AuthZEN Authorization API 1.0** access evaluation
(final spec, published 2026-01-11):

```json
{ "subject":  { "type": "user",  "id": "<verified deployer>", "properties": {...} },
  "action":   { "name": "deploy" },
  "resource": { "type": "agent", "id": "refund-agent",
                "properties": { "environment": "prod", "risk_tier": "high",
                                "principal": { ... }, "tools": [ ... ] } },
  "context":  { "image_digest": "sha256:...", "policy_bundle_digest": "sha256:..." } }
```

The response is `{ "decision": bool, "context": { "obligations": [...], "reason": ... } }`.
`obligations` (for example `require_approval`) is an AgentBreeder convention because no standard
defines it.

- Default engine: **Cedar** in-process via `cedarpy` (pinned; community binding). Cedar validation
  runs at deploy against a schema AgentBreeder ships.
- Alternatives: **OPA** over localhost REST; any **AuthZEN** HTTP PDP (Cerbos, Axiomatics,
  Permit.io and others).
- Optional later: OpenFGA for delegation/tenancy relationships.
- Not supported: Topaz (vendor shut down), Oso OSS library (deprecated).
- The PDP evaluates the trusted operator baseline and every selected approved restriction.
  Effective allow requires all of them to allow; any deny or error wins. Required obligations are
  combined, and unknown or unsupported obligations fail closed. An author cannot remove operator
  approval requirements by omitting `capabilities.approval_required` or declaring a lower risk tier.
- Record the evaluated baseline and restriction digests (or verified remote policy revision),
  operator configuration digest, and policy/schema version on every decision and deployment.

### 4.5 Deploy-time gate

`IdentityGate.check(manifest, deployer, context) -> GateDecision` runs inside
`DeploymentService.deploy()` after manifest validation and **before** `runtime.authorize()` or any
Docker operation. Audit/state persistence is explicitly allowed before authorization. Allocate an
attempt ID first and persist its start (see §4.6). Under the lifecycle lock, reject any persisted
administrative disable for the principal/environment before checking:

1. `identity.ref` resolves to exactly one principal.
2. `kind` is `agent` or `workload` (reject human and shared accounts — OWASP NHI9/NHI10).
3. `enabled` is true.
4. At least one sponsor is an active human (if `require_sponsor`); `expect_owner` matches if set.
5. `review_by` has not passed (OWASP NHI1 offboarding).
6. Scoped `requires.roles` ⊆ `roles`, scoped `requires.groups` ⊆ `groups`, and every required
   namespaced attribute has the exact verified value.
7. `identity.environment` equals the principal's environment and the trusted deployment context's
   environment; the context's target is allowed by operator configuration (NHI8).
8. No literal secrets (existing check).
9. PDP allows `deploy`. If obligations include `require_approval`, the deployment enters
   `pending_approval` (phase 2).

Each failure maps to a stable error code and a message that never echoes raw IdP payloads or secret
values. Exit code 2 (authorization), consistent with `docs/local-deployment.md`.

**Deployer identity.** Phase 0 records the OS user and marks it `unverified`. Phase 1 adds a verified
deployer via OIDC device flow (laptop) or the CI platform's OIDC token. `git config` emails are not
accepted as identity.

**Re-verification.** `status` re-resolves the principal and reports `noncompliant` if it no longer
passes. Phase 3 adds a reconcile command.

**Phase boundaries.** Phases 0–1 provide identity preflight only: phase 0's OS actor is unverified,
and a PDP/approval decision is unavailable until phase 2. Audit records and CLI output must identify
the implemented checks and unavailable checks; neither phase can claim the complete authorization
goal in §2. Configuring a PDP or approval requirement that the installed phase cannot enforce denies
deployment rather than silently skipping it. Phase 0's `enforce` applies to checks 1–8; phase 2 adds
check 9 and requires a verified deployer. Administrative disable becomes mandatory in phase 3.

**Approval contract (phase 2).** Persist a pending request before any Docker call. Bind it to a
canonical revision digest covering the complete validated manifest, image digest, scoped principal
reference, deployer identity, environment/target, operator config digest, evaluated policy/schema
versions, and the required obligations. Store verified evidence digests, creation/expiry times and
the request ID alongside it. The operator's approval TTL defaults to 900 seconds.

An approval requires a separately authenticated, policy-authorized human who is neither the deployer
nor the principal's sole sponsor. Persist the checker identity and decision against that exact request
and revision. Approval does not itself start a container. Resume acquires the same lifecycle lock,
checks expiry and administrative disable, rebuilds the revision from current trusted inputs, and
re-resolves identity and reevaluates policy. Changed revision, evidence, obligations, revoked checker
authority or a new deny invalidates approval and requires a fresh request. Atomically consume an
approval once with the durable execution-intent transition before Docker execution. Crash recovery
reconciles only that execution ID/revision and reruns the gate; another attempt cannot reuse approval.
Rejection, expiry, invalidation, consumption and recovery are all audited.

### 4.6 Audit log

A new append-only `audit` table in the existing SQLite store records attempts independently of
deployment creation. Allocate `attempt_id` and durably append `attempt_started` before IdP/PDP calls.
Append the decision before any Docker operation. If the attempt creates/changes deployment state,
append its audit event in the same SQLite transaction as that state transition. Early denies use a
standalone transaction with a nullable `deployment_id`; they never need a container or deployment row.

`seq, at, attempt_id, deployment_id?, actor, actor_verified, action, decision, enforcement_mode,
checks_evaluated, checks_unavailable, reasons, principal_snapshot?, evidence_digest?, context,
revision_digest?, policy_ids, bundle_digests, operator_config_digest, image_digest?, prev_hash, hash`

Use sanitized, bounded snapshots rather than raw IdP payloads; unresolved fields are nullable so
unknown principals and adapter timeouts can be logged. In `audit` mode, distinguish the evaluated deny
from the operator's decision to proceed. A crash may leave an incomplete attempt; verification reports
it as incomplete, never as an allow. Audit write failure (including disk full/locked database) aborts
before Docker execution in both modes. Report a safe persistence error without claiming a durable
denial record exists when the storage itself failed.

`hash = sha256(prev_hash || canonical_json(row_without_hash))`, with a defined genesis hash,
canonical encoding and monotonically ordered sequence; append under the store lock/transaction.
`agentbreeder audit verify` checks links and recomputes hashes. This detects corruption and edits
that do not recompute the chain. It cannot detect a database owner recomputing the whole chain,
replacing the database or deleting a valid tail. Phase 0 explicitly makes no adversarial tamper-evidence
claim. Such a claim requires an independently protected checkpoint or external append-only witness
containing the sequence and chain head; that integration needs a separate design before it is claimed.

### 4.7 Runtime enforcement (phase 4, opt-in)

Opting in creates an enforced network boundary: the agent has no direct external egress, and only
the sidecar can connect to operator-approved MCP destinations. Proxy environment variables alone
are insufficient. Direct IP/DNS routes, IPv4/IPv6, alternate ports, redirects and generic tunnels must
not bypass the boundary; approved DNS resolution must not permit rebinding into unapproved networks.
Container privileges cannot change routing or attach another network. Sidecar failure leaves egress
blocked. If the runtime cannot establish and verify this topology, deployment fails closed.

The sidecar terminates authenticated TLS (or uses a separately reviewed explicit gateway transport)
to inspect MCP Streamable-HTTP. An opaque CONNECT tunnel cannot enforce tool/argument policy.
The TLS trust and certificate approach in §8 must be resolved before phase 4 ships.

Treat the JSON-RPC body as authoritative: parse the method, tool name and arguments, and reject
malformed/unsupported messages, missing required headers and mismatches with `Mcp-Method`/`Mcp-Name`
for the supported MCP revision (2026-07-28). Older revisions require an explicitly supported body-based
adapter; never infer allow from an absent header. Evaluate every supported operation against the
capability ceiling and trusted PDP, using the principal/environment bound to the deployment.

Hold `require_approval` requests for out-of-band (CIBA-style) approval. Bind the approval to the exact
destination, method/tool, canonical arguments, principal, deployment revision and policy revision,
with expiry, cancellation and single-use release; recheck policy and disable state before forwarding.
The RFC 8693 credential broker keeps long-lived tool tokens outside the container, with no token
passthrough. Persist an allow/deny audit record before forwarding; audit failure blocks forwarding.
STDIO and in-process tools cannot be mediated; the docs must say so. The broker is not a substitute
for the enforced network boundary.

### 4.8 Lifecycle and kill switch (phase 3)

`agentbreeder disable <agent>` first persists an administrative disable and audit event under the
lifecycle lock, keyed by provider connection/authority, immutable principal ID and environment.
This blocker is independent of deployment names and survives retries, restarts and removal of a
deployment row. Invalidate pending approvals, block the sidecar when present, and stop all affected
containers. Verify that none remain running; if stopping fails, retain the blocker, record the partial
failure and return nonzero. Reconcile retries cleanup and never restarts disabled workloads.

Deploy, approval resume, recovery and reconcile all consult the persisted blocker under the same
lock before starting anything. Retrying deploy, renaming the agent or removing/recreating its
deployment cannot clear it; `audit` enforcement mode cannot bypass it. A separately authorized,
audited `agentbreeder enable <agent>` clears
the blocker for that principal/environment; it does not start a container or revive old approvals.
The next deployment must pass a fresh gate and, where required, obtain fresh approval.

Optional IdP disable (Entra `accountEnabled=false`, Okta deactivate) requires explicitly opted-in
write credentials and is additional to the local blocker. Local enable never reenables an IdP
principal. Deployments past `review_by` become `noncompliant` and cannot redeploy.

## 5. Phases and acceptance criteria

| Phase | Scope | Acceptance |
|---|---|---|
| 0 | Manifest v2 `identity`/scoped `requires`; `IdentityProvider` protocol; `static` adapter; identity preflight in `DeploymentService`; operator environment config; hash-chained audit table | Checks 1–7 have distinct deny codes; check 8 retains secret validation; no Docker call on deny; unknown environment/invalid target pairing rejected; `prod` context works independently of `local` target; same-name roles/groups in another resource/authority rejected; v1 rejected under `enforce`; unknown principal/timeout logged without deployment row; audit-write failure blocks Docker in both modes; modified row detected, recomputed-chain/tail limits documented; CLI/audit identify partial phase guarantees; ≥ 80 % coverage on changed files |
| 1 | Verified deployer (OIDC device and CI token); `entra` and `keycloak` adapters; `status` re-verification | Recorded fixtures preserve immutable authority/resource/role/group IDs and namespaced attributes; cross-resource and cross-authority collision tests; live Entra test behind a marker; adapter timeout fails closed with an audit decision |
| 2 | AuthZEN-shaped PDP; operator baseline and approved restrictions; Cedar default; OPA/AuthZEN adapters; revision-bound maker-checker `pending_approval` | Cedar/OPA decision parity; author allow-all cannot override baseline deny; unapproved/digest-mismatched bundles, unverified remote revision and unsupported obligations deny; evaluated policy/config digests recorded; self/sole-sponsor approval rejected; changed revision/evidence/policy, expired/replayed approval and revoked checker authority rejected; resume rechecks gate and consumes approval atomically; crash recovery cannot reuse approval for another execution |
| 3 | Durable administrative disable/authorized enable; review-date expiry; reconcile → `noncompliant`; `aws` and `okta` (experimental) adapters | Disable persists before stop and is audited; no affected running container on success; stop failure retains blocker and returns nonzero; deploy/retry/resume/reconcile, rename and remove/recreate cannot bypass blocker; enable is separately authorized/audited and requires fresh gate/approval; expired principal blocks redeploy |
| 4 | Opt-in enforced MCP HTTP egress topology; sidecar PEP; exact-call approvals; RFC 8693 broker | Direct IP/DNS, IPv4/IPv6, alternate-port, redirect, tunnel and DNS-rebinding bypass tests; sidecar failure leaves egress blocked; malformed/missing/mismatched MCP metadata rejected before forwarding; disallowed calls audited/blocked; audit failure blocks forwarding; changed args/destination/policy or expired/replayed approval rejected; no long-lived tool token in container env; inspected authenticated TLS established; unsupported network/TLS setup rejects deployment |
| 5 | `gcp` and `databricks` anchor/post-provision checks; SPIRE on `kubernetes`; ID-JAG and Txn-Tokens behind flags | Depends on non-local targets landing first |

## 6. Security considerations

- **Fail closed** on adapter error, timeout, ambiguity or PDP error. Break-glass is out of scope
  until a design for signed, short-TTL cached evidence with its own audit trail is reviewed.
- **Least privilege for AgentBreeder itself:** read-only IdP scopes; credentials referenced through
  `secret://`, never stored in the manifest.
- **Author vs operator separation:** enforcement mode and provider credentials live in operator
  config, not in the agent repo. The operator also controls environment mappings, baseline policy,
  approved restriction digests and remote PDP trust. Author inputs cannot replace verified facts.
- **Approval and lifecycle integrity:** approvals authorize one exact revision; persistent disable
  takes precedence over prior approval and survives deployment removal. Resume/recovery reverify.
- **Audit availability and integrity:** no Docker/tool execution without a durable decision record.
  The local chain does not detect adversarial rewriting or valid-tail deletion without an independent
  witness; do not describe phase 0 as tamper-evident against the database owner.
- **No sensitive output:** error messages and logs carry stable codes and digests, not raw IdP
  payloads.
- **Local trust boundary is unchanged:** a user with Docker-socket or state-directory access can
  bypass local controls. The gate is meaningful against mistakes and policy drift locally, and
  becomes a hard control once deploys run through a server-side executor (see `ARCHITECTURE.md`).

## 7. Tradeoffs

| Decision | Benefit | Cost |
|---|---|---|
| Verify-only | IdP stays the system of record; no write scopes | More first-run friction; GCP/Databricks need anchors |
| Fail closed | Matches "refuse otherwise" | IdP outage blocks deploys |
| Cedar default | In-process, analysable, strong agent precedent | Community Python binding; no native obligations |
| AuthZEN internal contract | One adapter reaches most enterprise PDPs | Obligations and delegation chains are AgentBreeder conventions |
| Sidecar with mandatory egress boundary | Language-agnostic enforcement of supported MCP HTTP | Blind to STDIO/in-process tools; extra hop; TLS termination and network isolation required |
| Local hash chain | Detects corruption and edits without recomputation | Database owner can rewrite/truncate undetectably; independent witness needed for stronger claims |

## 8. Open questions

1. ~~Does `cedarpy` expose validation and analysis well enough for deploy-time policy checks?~~ Answered (cedarpy 4.12.2): `validate_policies(policies, schema)` returns per-policy errors and is run at every deploy; `is_authorized` reports evaluation errors in diagnostics. No policy-analysis API is exposed.
2. Should break-glass deploys exist at all during an IdP outage?
3. Which authenticated TLS termination/trust and network isolation approach can the local runtime
   verify? This is a phase 4 release blocker, not a proxy-only fallback.
4. Should `schema_version: 1` keep deploying under `audit` mode indefinitely or for a fixed
   deprecation window?

## 9. Cross-repo split

- **agentbreeder (this repo):** everything in phases 0–5.
- **agentbreeder-cloud:** surface identity status, gate decisions and the audit chain once the
  deployment API exists; companion issue when phase 1 lands.
- **website:** docs only after phase 0 ships; wording limited to the deploy-time guarantee.

## 10. References

- OpenID AuthZEN Authorization API 1.0 (Final, published 2026-01-11) — https://openid.net/specs/authorization-api-1_0-final.html
- RFC 8693 OAuth 2.0 Token Exchange — https://www.rfc-editor.org/rfc/rfc8693
- MCP Streamable HTTP, revision 2026-07-28 (body/header validation) — https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- IETF WIMSE / AIMS drafts — https://datatracker.ietf.org/wg/wimse/documents/
- Microsoft Entra Agent ID — https://learn.microsoft.com/entra/agent-id/
- Microsoft Graph agentIdentity lookup — https://learn.microsoft.com/en-us/graph/api/agentidentity-get?view=graph-rest-1.0
- Microsoft Graph appRoleAssignment (resource/role scope) — https://learn.microsoft.com/en-us/graph/api/resources/approleassignment?view=graph-rest-1.0
- Okta for AI Agents — https://developer.okta.com/
- OWASP Non-Human Identities Top 10 — https://owasp.org/www-project-non-human-identities-top-10/
- OWASP LLM06:2025 Excessive Agency — https://genai.owasp.org/
- NIST NCCoE AI agent identity and authorization concept paper (draft, 2026) — https://www.nccoe.nist.gov/
- Cedar — https://www.cedarpolicy.com/ ; OPA — https://www.openpolicyagent.org/
