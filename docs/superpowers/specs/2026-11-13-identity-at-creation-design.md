# Agent identity at creation, with RBAC and ABAC before deploy and at tool calls (#909): design

- Status: **r5, reviews converged** (architecture and `/security`, Claude Sonnet, five rounds; §17) (architecture and `/security` lenses, Claude Sonnet;
  §17). No code in this PR. Owner decisions D1–D8 (§14).
- Issue #909 (part of epic #742; feeds M7 #823, M17 #833, M18 #834 in epic #812). Plan of record:
  `docs/superpowers/plans/2026-11-11-console-epic.md` (wins where this spec differs).
- Builds on: the identity gate (`gate.py`, `identity/`, `policy/`, `docs/identity-gate.md`, epic #701),
  the stack's generated operator config (`services/operator.py`), `services/schema.py`,
  `services/engine.py`, `services/state.py`, `build.py` (`_manifest`, `_config`,
  `_runtime_capabilities`), `manifest.py`, `operator_config.py` (`AgentIdentities`,
  `TrustedIssuer`), `deployer.py` (`verify_token`, `device_login`, `current_token`),
  `deployment.py` (`verify`, `reconcile`, `_mcp_servers`, `_grant_mcp`), `egress/host.py`,
  `egress/protocol.py`, `egress/boundary.py`, `newagent.py`, `stackfor.py` (`_agent_entry`, the
  seam #911 left), and the M12 delta's #909 seams (`2026-11-13-m12-tools-console-role-design.md` §6).

## 1. Problem

The identity gate checks a principal that already exists. Nothing creates one:

1. **Local stacks use a static demo principal.** `services up` writes an operator config with one
   `static` provider (`demo`) whose principals are the stack's agents, each enabled and sponsored
   by the e-mail in `stack.yaml` (`services/operator.py` `documents`). It writes no PDP and no
   `expect_owner`, so every local deploy reports **identity preflight only** (no RBAC, no ABAC).
2. **New and onboarded agents carry no grants.** `new agent` and `new agent --stack-for` write
   `environment.agents.NAME: {sponsor, review_by, candidate}` (`newagent.py`,
   `stackfor._agent_entry`). `build` copies the operator config's identity reference into the
   manifest (`build._manifest`, `expect_owner` included when set) and writes no `requires`,
   `capabilities` or `policy` (#742 gap 2).
3. **In-process tools are never authorized.** ADK tools run inside the agent's process
   (`runtime/adk` `agent.py` `before_tool_callback` only records the name). MCP tools are limited
   by the relay grant, which `deployment._mcp_servers` checks against the operator config's
   `agents.NAME.mcp`, copied from `stack.yaml`.
4. **Operator minimums are optional** (#742 gaps 1, 6, 7).

Only the static adapter is live-verified. The Keycloak adapter is fixture-tested, and every
identity transport refuses plain HTTP except the remote PDP (`identity/transport.py`,
`policy/remote.py`).

## 2. Goal and acceptance

Every agent gets **its own identity principal when it is created or onboarded**, with roles
(RBAC) and attributes (ABAC), checked before deploy, and in-process tool calls are decided by the
host against the same grants.

- **Local:** a built-in `service/keycloak` in the stack. The principal is **declared** when the
  agent is created (`new agent`, `new agent --stack-for`, later `import` #757) and **created in
  Keycloak by the next `services up`**, after the operator approves the identity change (§5.1,
  D7). It is unique per environment, has a human sponsor/owner by ID, roles, and attributes
  (environment, data class, owner, review_by).
- **Entra, Okta, AWS:** reference-only. AgentBreeder prints the principal the IdP admin must
  create and refuses deploy until it resolves (§9).
- **Before deploy:** operator minimums from #742 (`require_pdp`, owner by ID, unique principal,
  mandatory `review_by`, `tool_access` present), all on in the stack's generated config (§7).
- **At tool calls:** the runtime asks the host decision service before every in-process tool call.
  This is a **cooperative in-process check, audited on the host, not a boundary** (§6 T4): code in
  the agent's container can skip it. MCP tools stay limited by the relay grant, which deploy now
  requires the principal to hold as roles. #909 adds **no** sidecar enforcement: built agents
  cannot run under `mcp_http` (`deployment.py`, `MCP_RELAY_UNSUPPORTED`,
  `MODEL_ACCESS_UNSUPPORTED`).

Acceptance (from #909, worded to the claim rule; each is a CLI end-to-end test with real Docker and
a real Keycloak):

1. `new agent X`, then an approved `services up`, gives an agent whose principal exists in the
   stack's Keycloak, whose generated manifest names it (`identity.provider: local`, `ref` = the
   client's UUID, `expect_owner` = the owner's user UUID), and whose deploy by a signed-in owner
   passes the gate with `enforcement: enforce` and a Cedar PDP, with no `checks_unavailable`.
2. Removing the agent's environment role in Keycloak makes the next deploy fail with
   `ROLE_NOT_GRANTED`, and `reconcile` marks the running deployment
   `noncompliant:ROLE_NOT_GRANTED` (exit 4). A later `services up` with an unchanged declaration
   does not restore the role.
3. With a runtime that has the decision call (I4), an in-process tool call the principal is not
   granted does not run: the runtime returns `TOOL_DENIED` to the model, the host writes a
   `tool_call` deny row to the hash chain, and the runtime writes its `tool:<function>` row with
   `ok: false, error: "TOOL_DENIED"` (M12 seam 5). Removing a tool role in Keycloak denies that
   tool's next call within the 60-second re-resolution window (§8.4).
4. Console: the identity strip on the agent (M7) and the policy view (M18) read the fields in §10.

Acceptance 1 and 2 ship with I1–I3; acceptance 3 with I4 (§13).

## 3. Scope and non-goals

In scope: `service/keycloak@1.0.0`; the identity declaration in `stack.yaml`; approved
provisioning by `services up`; loopback-HTTP plumbing for a pinned local realm; the generated
operator config; `build` manifests with `requires` and `tool_access`; the #742 operator minimums;
the runtime decision call and decision protocol v2; the reference-only path for enterprise IdPs;
`--json` fields for the console.

Not in scope:

- **Production identity or separation of duties.** The local realm is a development IdP on
  loopback HTTP. Locally one person declares, approves, grants and deploys; docs never call the
  approval step second-person control.
- **People RBAC** (who may use the console or edit policy): #742/#775, after epic #812.
- **Enforcement outside the agent's process** beyond what exists (relay grant, gateway key).
- **Argument-level ABAC.** Tool decisions see the principal, environment, tool and an arguments
  digest the agent computes; policy cannot inspect arguments.
- **`kind` runtime tool decisions** in the first delivery (§8.3, D4).
- **Northwind** keeps the static demo provider until the owner opts it in (D6).
- `import` (#757) and A2A principals (M19) only get the seam (§5.4).

## 4. The principle change: mint only in the local realm

`docs/identity-gate.md` says **"Verify, don't mint."** This spec keeps that for every IdP the
operator configures and makes one bounded exception:

- AgentBreeder **creates** principals only in the realm `services up` started for the stack
  (`service/keycloak`), whose signing key AgentBreeder generated and pinned (§5.3), through the
  provisioner key it generated.
- The **gate** still only verifies, through a separate read-only client, with the same
  `KeycloakProvider` and the same checks as any IdP. The provisioner key never appears in an
  operator config.
- Adapters for Entra, Okta, AWS and operator-configured Keycloak stay read-only.

`docs/identity-gate.md` (lines 4–5) and `IdentityReference`'s docstring change to say this.

## 5. Creating the principal

### 5.1 Declare at creation, create at an approved `services up` (D7)

`new agent` and `--stack-for` only write files; Keycloak runs only after `services up`. So:

- **Creation declares** the identity in `environment.agents.NAME` (§5.2), through the shared
  function that replaces `stackfor._agent_entry`. `new agent --json` gains
  `identity: {provider: "local", created: false, next: "services up"}`.
- **`services up --environment ENV` creates**, for that one environment, only an **approved
  identity diff**:
  - **Offline preview.** `services plan --json` (which starts nothing, `engine.py`) diffs the
    declaration against the last-applied record in stack state and prints `identity_diff` with a
    `preview_digest`. It never generates keys.
  - **Apply-time diff.** `up` starts the services, then, under the stack lock, computes the diff
    against the **realm** (each client's applied record, §5.3). Where the realm and stack state
    disagree, the realm wins and the disagreement is drift (§5.3). If the realm cannot be read, `up`
    fails; it never treats an unreadable realm as an empty diff.
  - **The digest** is the SHA-256 of the stack ID, the environment and the canonical apply-time diff (sorted entries with attribute
    values, UUIDs, e-mails and role names), the applied-record fingerprints of the affected clients,
    the pinned realm key thumbprint and an expiry 15 minutes after it was shown.
  - **Approval.** Interactively, `up` shows the diff and needs a typed `yes`. Non-interactively it
    needs `--approve-identity <digest>`; the digest comes from a previous `up` that stopped with
    `IDENTITY_APPROVAL_REQUIRED` and printed the diff and digest (exit 2, realm unchanged, previous
    generated config kept). `up` applies only the plan object it computed and approved in that run;
    a digest that does not match the recomputed diff, or has expired, is refused. So a
    non-interactive change takes two `up` runs by design; `docs/services.md` and the `--json` shape
    say so. For scripts, `--approve-identity-file FILE` takes a reviewed diff (the JSON the first run
    printed) instead of a digest: `up` applies it only if the recomputed diff is identical, with no
    expiry, since the content itself was reviewed (it carries the stack ID, environment,
    applied-record fingerprints and realm key thumbprint, so it goes stale when the realm changes;
    it is a reviewed artifact, not a secret). The docs say approval is a review step for the
    person running `up`, not a barrier against a process or agent with that user's shell.
  - The console never supplies the flag itself: its `services up` action shows the diff and needs
    the typed confirm (M7/M18 task).
- An empty diff needs no approval, so routine `up` runs stay non-interactive.

### 5.2 The declaration in `stack.yaml`

```yaml
services:
  keycloak:
    ref: service/keycloak@1.0.0
environment:
  identity: keycloak            # new; the service that is this stack's IdP (absent = static demo)
  agents:
    support:
      sponsor: you@example.com  # the human owner; a realm user (§5.3)
      review_by: "2027-05-12"
      data_class: internal      # new; public | internal | confidential | restricted (default internal)
      candidate: true
      tools: [orders.lookup_order, orders.open_ticket]   # new; in-process grants, <tool>.<function>
      mcp: {github: [search_issues]}                     # existing MCP grants
```

- `environment.identity` names a service with the new `provides: identity/keycloak`
  (`schema.py`'s `provides` literal gains it, with validator branches like `gateway/litellm`).
  Absent: today's static `demo` output, byte-identical, unless the stack was ever provisioned with
  Keycloak (downgrade rule, §5.3).
- `tools` entries are `<tool-name>.<function>` (tool name from the `tool/<name>` ref, function
  from its `tool.yaml`), at most 100, unique. `new agent` writes the starter's (none today);
  `--stack-for` writes every function of every tool the agent references, from the locked
  registry. `agent set-ref` that adds a tool prints a `Next:` line naming the grant to add; it never
  edits the grant.
- `stack.yaml` is the **request**; the realm holds the **grant**; the gate reads only the realm.

### 5.3 `service/keycloak@1.0.0` and provisioning

**The service** (`builtin_registry/service/keycloak/1.0.0/`; items marked *spike* are verified in I1
before the rest is built):

- `build:` a Dockerfile from an official Keycloak 26.x image pinned by digest that runs `kc.sh
  build` (features limited to what is used, health on), so the server starts with `start
  --optimized` on a read-only root (`compose.py` runs services `--read-only --cap-drop ALL`);
  `/opt/keycloak/data` is the one volume (the `dev-file` store), `/tmp` a tmpfs. *Spike:* read-only
  root, uid, cold start, memory and image size, recorded in `docs/services.md`.
- **Start sequence** (`command: [/files/start, …]`, a fixed entrypoint shipped in the service's
  `/files`, within the schema's no-shell rule; *spike*): init is one offline sequence (`kc.sh import
  --file /run/params/realm`, both realms in one file), and only after it completes does the
  entrypoint write a marker file in the volume. With the marker present it only starts the server
  (`kc.sh start --optimized`). Without the marker it discards any partial store and reruns the
  whole init; if `/run/params/realm` is absent (it is mounted only for init, below) it refuses to
  start. So a crash mid-init never leaves a server running on a half-initialized realm. *Live
  test:* kill the container during init; the next start repairs or refuses.
- **No first-admin path.** `realm.json` also carries the `master` realm with exactly one user, an
  admin that is **disabled** from creation and has a random credential that is never written
  anywhere, so the server never offers initial-admin creation and no enabled admin exists at any
  moment. No secret is passed through the container's environment. *Live tests:* from the host,
  `/`, `/admin/master/console/`, `/realms/master/` and the initial-admin endpoint return 403 or 404,
  and `master` has no enabled user, also after a restart. If Keycloak refuses a `master` import,
  the spike fails and this sequence is revised before I1 continues.
- `ports`: `http: {port: 8080, expose: host, guard: host-allowlist}` published on host loopback;
  `management: {port: 9000}` internal only, used for `health: {http: /health/ready, port:
  management}`. Metrics off. Never on the agents network.
- **`realm.json`** is generated by `up` (never by `plan`) into a `state.mount_dir`-style leaf of
  the stack's private state (a 0444 file in a 0755 leaf under a 0700 parent, the existing pattern
  for content a service's fixed uid must read; the 0700 parent keeps other host users out; not
  through `state.copy_file`, whose copies sit in a world-readable tree) and bind-mounted
  read-only at `/run/params/realm`. **Lifecycle:** the realm signing private key lives in its own
  0600 file under the 0700 identity directory; `realm.json` is assembled only when the volume has
  no initialized store (first start or after purge), mounted only for that start, and deleted after
  the import and a health check succeed. Its content never appears in `plan --json`, status
  output or logs (canary test). The engine change is small: after `plan()`, set
  `plan.files[<identity service>]["realm"]` to that file, as `engine.py` already does for the relay
  config; the service declares `realm` as an optional `type: file` param that a stack cannot set in
  `with:`. It contains:
  - realm settings: registration, reset-password, remember-me and direct grants off realm-wide;
    `bruteForceProtected: true`; ID and access tokens 15 minutes; SSO session idle and max 8
    hours; refresh tokens rotated (`revokeRefreshToken: true`, `refreshTokenMaxReuse: 0`);
    device-flow codes 5 minutes; `KC_HOSTNAME` set to the pinned loopback URL so `iss` is stable;
  - the realm attribute `agentbreeder.stack=<stack id>`;
  - **the realm's RSA signing key, generated by AgentBreeder** (an `rsa` key provider component).
    Its public half is pinned in stack state before Keycloak first starts, so nothing about the
    realm is trusted on first use. This is the one private key in `realm.json`; the file is as
    private as the other stack secrets;
  - the user profile, which **declares** every AgentBreeder attribute with admin-only edit and
    view, and `unmanagedAttributePolicy: ADMIN_VIEW`, so Keycloak 24+ keeps them and users cannot
    edit them. Attribute names are `agentbreeder.<name>`; the generated provider sets the adapter's
    configurable attribute names to match (`identity/keycloak.py`), and `manifest.py`'s
    `_ATTRIBUTE_KEY` also accepts a dotted `namespace.name` key (JSON Schema and docs updated).
    *Spike:* an attribute round-trip through the adapter;
  - the clients below with their public keys.
- **Clients.** Confidential clients authenticate with **`private_key_jwt`** (assertions with `jti`,
  a 60-second `exp` and the token endpoint as audience; the private key stays on the host at 0600;
  Keycloak's replay cache is per node and lost on restart, so a captured assertion is replayable
  within its 60 seconds after a restart):

  | Client | Kind | Realm-management roles | Used by |
  |---|---|---|---|
  | `agentbreeder-provisioner` | confidential, service account | `manage-clients`, `manage-users`, `view-realm` | `services up` and `services identity …` only; key in stack state, never in an operator config |
  | `agentbreeder-gate` | confidential, service account | `view-clients`, `view-users`, `query-groups` | the gate's `KeycloakProvider` and the decision service's re-resolution (§8.4) |
  | `agentbreeder-cli` | public, device flow only, PKCE, audience mapper `agentbreeder-cli` | none | `agentbreeder login` (§5.5) |
  | `agentbreeder` | bearer-only | none; holds the client roles below | role namespace |

  RSA keys need `cryptography` (or another signing library), which is not a runtime dependency
  today (`pyproject.toml`); D8 decides how it ships. Without it, `up` with `environment.identity`
  fails `IDENTITY_DEPS_MISSING` with the install hint. The adapter verifies tokens with the
  existing RS256 verifier in `deployer.py`, extracted into a shared helper, not copied.

**Checks before the provisioner key is used.** Provisioning runs from host Python over the pinned
loopback port, in one process with the approval flow. Before it sends any provisioner assertion,
`up` (1) confirms with `docker port` that the port belongs to the stack's Keycloak container and
(2) runs the credential-free part of the realm check: the realm's OIDC discovery and served JWKS
must equal the pinned key and issuer. Only then, re-running the `docker port` check in the same
step, does it request the provisioner token with a 30-second assertion, and it verifies that token
against the pinned key before any admin call. Keycloak login events are enabled for the
provisioner client; `up` compares the provisioner's token issuances with its own requests and
reports any extra as `identity_token_unexpected` (warning, audit row). The full realm check follows
(next paragraph). The residual risk is in §6 T7.
A one-shot helper on the stack's internal network would keep the assertion off the published
port, but the existing `oneshot` runs the service's own image (no Python), with read-only mounts
and an 8 KiB output cap (`engine.py` `_step_run`, `compose.py`), so it needs a dedicated pinned
helper image and engine changes. That is recorded as a later hardening, not part of #909. The published port carries only the gate, the
deployer's sign-in and the decision service's re-resolution, none of which hold the provisioner
key.

**Realm check before provisioning** (stale or foreign volumes; its credential-free part runs
before the provisioner key is used, above). Every `up` that will provision first verifies the realm it is talking to: the realm's only active `sig` key provider is the
imported one and its served JWKS equals the pinned key (key ID, `use`, algorithm; a mismatch fails
clearly, not as a later `DEPLOYER_UNVERIFIED`); a token for `agentbreeder-provisioner` signed by the
pinned realm key with the expected `iss` and `azp`; then, through the admin API, the realm
attribute `agentbreeder.stack`, the realm settings above, the four AgentBreeder clients and their
registered key IDs, the `master` admin state, and that every other client is an `agent.*` client
(the expected set comes from stack state; agent clients missing from it are drift for `adopt`, not
foreign) and that no user other than the owners holds a realm-management role. A wrong stack
attribute, a token not signed by the pinned key, or a difference in the fixed parts (a realm left on
the volume by another stack, a pre-seeded realm, an extra admin) fails `IDENTITY_REALM_FOREIGN`
before any write; the remedy is `services down --purge`.

**Port and issuer stability.** The issuer URL embeds the published port. `up` records the port in
stack state on first allocation and reuses it; if it is taken, `up` fails `IDENTITY_PORT_BUSY`
rather than moving the issuer. After the container starts, and before any provisioning, `up`
checks with `docker port` that the port belongs to the stack's Keycloak container; a failure stops
before any write. Because the realm key is pinned, a process that binds the port first cannot pass
the realm check.

**Trust in what answers on the port** (§6 T7). The generated config passes the pinned public key
(a JWKS of the `sig` key only) as the provider's new `jwks_file` setting and the issuer's existing
`jwks_file`. Every gate resolve first obtains an access token and **verifies its signature, `iss`
and `azp` (`agentbreeder-gate`)** before trusting any admin response.

**Key rotation.** `agentbreeder services identity rotate --client gate|provisioner|--realm-key`
is audited and, for the realm key, goes through the approval diff (§5.1) because it re-pins.
Keycloak's `client-jwt` authenticator holds one registered key per client, so a client rotation
is: write the new key as `<name>.new`, register its public half through the provisioner, obtain a
test token with it, rename it into place; on failure, re-register the old key. A failed
provisioner rotation that cannot roll back leaves the realm unmanageable; the documented recovery
is `services down --purge`. *Spike:* the exact Keycloak calls. PEM blocks and JWT assertions are
added to the redaction patterns, not only the key values.

**Provisioning** (new module `services/identity.py`, host Python over loopback; one provisioner
token per run, discarded). For the `--environment` given, for each `environment.agents.NAME` (and
its candidate):

1. **Owner.** A realm user per distinct `sponsor` (username = the e-mail, `email_verified: true`,
   enabled). Human (no `serviceAccountClientId`), so the adapter counts it as an active sponsor.
   Its password is generated, stored as the stack secret `owner-<n>` (0600); `up` prints the file's
   path once, never the value.
2. **Principal.** A confidential client `agent.<env>.<name>` (and `…-candidate`) with
   `serviceAccountsEnabled: true`, every interactive flow off, `clientAuthenticatorType:
   client-jwt` with **no registered key** (nothing can authenticate as the agent; the agent's
   runtime credentials stay the per-deployment gateway key, relay token and, after #780, invoke
   key). Client attribute `agentbreeder.kind=agent`; service-account attributes
   `agentbreeder.sponsors=[<owner user id>]`, `agentbreeder.environment`, `agentbreeder.review_by`,
   `agentbreeder.data_class`, `agentbreeder.owner`, `agentbreeder.stack`.
3. **Roles.** Client roles on `agentbreeder`, created once per realm: `env.<env>`;
   `tool.<tool>.<function>` per granted in-process function; `mcp.<server>.<tool>` per granted MCP
   tool. Granted to the principal's service-account user.
4. **Applied record on the client.** Client attributes hold the digest of the declaration last
   applied (`agentbreeder.applied`) and the role list it granted, split over numbered attributes
   (`agentbreeder.applied.<n>`) so a hundred-tool agent fits Keycloak's attribute-size limit
   (*spike*: the limit). UUIDs (client, owner, roles) go to stack state and the generated config
   (§7).

**Grant only the delta; revocation sticks.** `up` compares the new declaration with the client's
applied record and grants or revokes only the difference. It never re-grants a role or re-enables
a client that was removed out of band: a routine change (`review_by` renewal, a new grant) applies
only its own delta, and roles missing compared with the applied record are reported as
`identity_drift` (warning, audit row, `services status`). A client that exists with no applied
record (lost or reset state) is drift: nothing is granted until the operator runs `agentbreeder
services identity adopt AGENT --environment ENV`, which shows the full grant as an approval diff
(§5.1) and writes the applied record. Removing a grant revokes its role. **Removing an agent**
revokes all its roles, disables the client and renames its `clientId` to
`agent.<env>.<name>.removed.<client uuid>`. Re-adding the name creates a new client with a new
UUID, so a reused name never inherits an old principal or its audit history.

**No silent downgrade.** Stack state records that the stack was provisioned with Keycloak. Removing
`environment.identity` afterwards makes `up` refuse with `IDENTITY_DOWNGRADE` until `services down
--purge`; there is no override flag.

Every provisioning write is audited in the default state's chain (`identity_provisioned`,
`identity_revoked`, `identity_drift`, `identity_adopted`, `identity_rotated`) with UUIDs, never keys
or passwords.

### 5.4 Onboarding and `import`

`--stack-for` derives the declaration from the existing `agent.yaml` (§5.2). `import` (#757,
paused) will write the same entry through the same function, with `tools: []` (an image has no
inspectable tool list). A2A (M19) peers are principals of the same kind; their roles are M19's.

### 5.5 Who deploys: the local realm is the deployer's issuer

The gate requires a **verified deployer whenever a PDP is configured** (`gate.py` `check`,
`DEPLOYER_UNVERIFIED`). With the PDP on (§7) every `deploy`, `release`, `promote`, `quickstart`
deploy and console CLI child needs a signed-in human. The local realm provides one:

- The generated config trusts the realm (`deployer.issuers`): `issuer`, `audience:
  agentbreeder-cli`, `kind: human`, `jwks_file` (pinned), `device_authorization_endpoint`,
  `token_endpoint`, `client_id: agentbreeder-cli`, `loopback_http: true` (§7.1), and the new `azp:
  agentbreeder-cli` and `refresh: true`.
- `agentbreeder login` (RFC 8628 device flow, existing) signs in the owner and prints the realm and
  the full user code.
- **Refresh** (new, only for issuers with `refresh: true`, which only generated configs set; never
  for Entra or Okta): the CLI caches the refresh token beside the ID token (0600) and, in
  `resolve_deployer`, refreshes an expired ID token by calling only that issuer's configured
  loopback token endpoint, with no redirects. Refresh tokens rotate and are single-use, so
  concurrent CLI children serialize refresh with an `flock` on the cache (no stale lock after a
  crash) and write the new refresh token atomically before using the new ID token. A crash or lost
  response after Keycloak rotated the token ends the session (reuse detection): the next command
  asks for `login` (documented). The refresh token
  is in `redaction_values()`. After the 8-hour session, `DEPLOYER_UNVERIFIED` with `Next:
  agentbreeder login`; the console shows the same as a "Sign in" action (M7). An
  `AGENTBREEDER_OIDC_TOKEN` override is used as given and never refreshed.
- New `agentbreeder logout` revokes the refresh token at the issuer and deletes both cache files.
- `verify_token` gains optional `azp` and `max_age` checks on `TrustedIssuer`. The generated config
  requires `azp`, so an agent or gate service-account token is `DEPLOYER_UNVERIFIED` (negative
  test). `approve`, `enable` and `approve-call` require `auth_time` within 15 minutes; refresh does
  not renew `auth_time`, so these commands need a fresh `login` (docs say so).
- **Tests** complete the device flow headlessly through a helper that lives only under `tests/`,
  refuses a realm without the test-realm attribute, and is checked absent from the wheel and sdist
  in CI. No product client gets direct grants.
- Side effect: `enable` (verified human + a PDP that permits `enable`) works locally.

D2 offers the alternative (a loopback-only password grant without a browser).

## 6. Threat model

Assets: the realm signing key, the provisioner key, the gate key, the owner's password and tokens,
the policy baseline, the decision-service tokens, the audit chain.

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| T1 | Another local process, user or container calls Keycloak's admin API | Loopback-only publish; every client authenticates with a host-held private key; `master` has only a disabled admin and no first-admin path (§5.3); keys 0600 under a 0700 directory (the init-only `realm.json` 0444 under a 0700 parent, deleted after import) | A process running as the same OS user can read the keys, like every local secret. On Docker Desktop any container reaches host loopback ports through `host.docker.internal` |
| T2 | The agent container uses Keycloak | Not on the agents network; the agent's client has no usable credential | It can reach the port on Docker Desktop (T1) and start a device flow (T6) |
| T3 | A repo editor or agent edits `stack.yaml` to add owners, grants or data classes | Changes reach the realm only through an approved `up` showing the exact apply-time diff, bound by digest and expiry (§5.1); the console needs a typed confirm; audited | The approver is the local user, or any process of that user able to drive its terminal; no separation of duties is claimed |
| T4 | A compromised agent skips the in-process check | Docs call it a cooperative check, not a boundary; tool code is reviewed through the registry (S-06); effects that leave the container are gated outside it (gateway key, relay grant) | In-process tools that write to the agent's database or call the network directly are not hard-enforced |
| T5 | A forged decision reply, or the agent speaking for another caller | Decision protocol v2 (§8.1): v2-only, token never sent, separate request and reply keys, replies bound to the request; a per-caller token scoped to `tool:*` decisions | The agent's container holds its own token; it can ask only about its own deployment and only for tool decisions |
| T6 | Device-flow phishing: a process starts a flow and asks the owner to enter its code | `login` prints the realm and full code; only `agentbreeder-cli` has device flow; codes live 5 minutes; tokens 15 minutes; `azp` required; approvals need a fresh `auth_time` | An owner who enters a code they did not start gives that process a session of up to 8 hours |
| T7 | A process binds the Keycloak port first, or a stale or foreign realm sits on the volume | Realm key generated and pinned before first start; the realm check before provisioning (`IDENTITY_REALM_FOREIGN`); every resolve verifies a pinned-key token first; `private_key_jwt` sends no reusable secret | Admin responses are unsigned: a listener swap between the token call and an admin call within one resolve is not detected (fails closed on any error). A listener swapped in between the credential-free checks and the token call (gate or provisioner) could capture one 60-second, audience-bound, single-use assertion and replay it once to the real server; the later pinned-key token check makes such a swap fail closed, but a replay is detectable only afterwards, through the provisioner token-issuance comparison (§5.3) |
| T8 | Revocation undone by `up`, lost state or a reused name | Delta-only grants from the applied record on the client; missing record = drift, granted only through `adopt` and its approval; removed clients renamed by UUID, a re-add gets a new UUID | Editing `stack.yaml` and approving the diff re-grants, visibly |
| T9 | Downgrade to static demo principals by deleting one line | `IDENTITY_DOWNGRADE` until purge | — |
| T10 | Principal shared across deployments; role names spoofed | `unique_principal`; one client per agent per environment; role UUIDs with resource scope (`RoleGrant`) | — |
| T11 | Secret leakage | Keys, owner passwords, ID and refresh tokens and decision tokens join `redaction.redaction_values()`, plus PEM and JWT patterns; canary test over logs, `--json`, audit detail, `center.sqlite3`, child argv, container env and labels | — |
| T12 | Refresh-token theft | Rotated, single-use refresh tokens; 0600 cache; sent only to the pinned loopback token endpoint; `logout` revokes | A same-user process can read the cache, like every local credential |
| T13 | An agent floods the audit chain through decisions | Per-deployment decision rate cap and a row-size cap on the host (§8.1) | — |
| T14 | Data at rest | The `dev-file` store lives in a Docker volume readable by anyone with the Docker socket | Same as every stack volume |
| T15 | Plaintext tokens | Plain HTTP only to literal loopback addresses, only with `loopback_http: true`, which requires a pinned `jwks_file` (§7.1) | Loopback traffic is readable by root on the host |

## 7. The generated operator config and the operator minimums

### 7.1 Loopback HTTP (new plumbing)

Today every identity transport refuses HTTP and `TrustedIssuer` URLs must be `https://`
(`operator_config.py`). New: a `loopback_http: true` setting on a provider connection and on
`TrustedIssuer`. When set:

- every URL of that provider or issuer must use a literal loopback address (`127.0.0.1` or
  `[::1]`; `localhost` is refused), and redirects are refused as everywhere;
- a pinned `jwks_file` is **required** (an issuer or provider with `loopback_http` and no pinned key
  is an invalid config), so plain HTTP never fetches a trust anchor;
- `load_provider` and the deployer's JWKS, device and token calls use
  `JsonTransport(allow_loopback_http=True)`.

`TrustedIssuer`'s `https://` patterns become "https, or http on a literal loopback address when
`loopback_http`". Provider settings are a free-form mapping (`providers`), so the rule is checked in
`load_provider` and the adapter. Docs say the setting is for local development realms.

### 7.2 What `services up` writes with `environment.identity`

```yaml
enforcement: enforce
require_pdp: true             # new: no PDP -> every deploy denies POLICY_UNAVAILABLE
require_review_by: true       # new: identity without review_by -> REVIEW_REQUIRED
owner_by_id: true             # new: expect_owner required, matched against sponsor IDs only
unique_principal: true        # new: PRINCIPAL_IN_USE if another active deployment uses it
require_tool_access: true     # new: a manifest without a tool_access key -> TOOL_ACCESS_REQUIRED
require_sponsor: true
providers:
  local:
    type: keycloak
    base_url: http://127.0.0.1:<pinned port>
    loopback_http: true
    realm: agentbreeder
    client_id: agentbreeder-gate
    credential: file:<stack state>/identity/gate.key      # private_key_jwt signing key
    client_auth: private_key_jwt                          # new adapter setting
    jwks_file: <stack state>/identity/realm-jwks.json     # new adapter setting, pinned sig key
    environment_attribute: agentbreeder.environment
    review_attribute: agentbreeder.review_by
    sponsor_attribute: agentbreeder.sponsors
deployer:
  issuers:
    - {issuer: http://127.0.0.1:<port>/realms/agentbreeder, audience: agentbreeder-cli,
       azp: agentbreeder-cli, kind: human, loopback_http: true, refresh: true,
       client_id: agentbreeder-cli, jwks_file: <…>/realm-jwks.json,
       device_authorization_endpoint: …, token_endpoint: …}
pdp:
  engine: cedar
  baseline: {source: <stack state>/identity/baseline.cedar, digest: "sha256:…"}
environments:
  dev:
    agents:
      support:
        identity: {provider: local, ref: <client uuid>, environment: dev, expect_owner: <user uuid>}
        candidate_identity: {…}
        requires:
          roles: [<env.dev>, <tool.orders.lookup_order>, <mcp.github.search_issues>]   # {authority, resource, role_id}
          attributes: {agentbreeder.environment: dev}
        actions: {<role uuid>: "tool:orders.lookup_order", <role uuid>: "mcp:github.search_issues"}
        mcp: {github: [search_issues]}
```

- `AgentIdentities` gains `requires` and `actions` (role UUID → action). `build._manifest` copies
  `requires` into the generated manifest and adds a top-level `tool_access:
  [orders.lookup_order, …]` (every in-process function the agent references, from `agent.lock`;
  an empty list when it has none); `expect_owner` already flows through `identity`. Schema change:
  JSON Schema, `docs/manifest.md`, grep of `agentbreeder-cloud`.
- **Grants before deploy reuse `requires.roles`.** Every `tool.*` and `mcp.*` role the agent
  declares is in `requires.roles`, so a missing grant denies with the existing `ROLE_NOT_GRANTED`.
  The `actions` map only turns verified role UUIDs into the `granted_actions` the PDP and the
  decision service use. `deployment._mcp_servers`' `MCP_TOOL_NOT_GRANTED` stays; since the `mcp.*`
  roles are required, the relay grant can never exceed what the principal holds.
- **Minimums.** The five new settings default to `false` for hand-written configs and are `true`
  in every generated one. `IDENTITY_REQUIRED` becomes non-overridable in audit mode (#742 slice 1).
  New deny codes: `REVIEW_REQUIRED`, `PRINCIPAL_IN_USE`, `OWNER_REQUIRED`, `TOOL_ACCESS_REQUIRED`,
  `TOOL_ACCESS_MISMATCH`.
  Under `require_pdp`, a missing `cedarpy` at deploy (another venv, the npx launcher) denies
  `POLICY_UNAVAILABLE`.
- **`unique_principal`.** Checked inside the gate, which already runs under the lifecycle lock, so
  two concurrent deploys cannot both pass. A principal key backs at most one active deployment per
  environment; a redeploy of the same deployment name replaces it. A release candidate uses its own
  `-candidate` principal; `promote` to another environment uses that environment's principal.
- **The Cedar baseline** is shipped in the package and **copied by `up`** into the stack's private
  state (0600, owned by the user), and that copy's digest is pinned, because the Cedar engine reads
  sources with `read_private`. Default deny. `permit deploy` for a verified human deployer (the
  environment role is enforced by `requires.roles`, which a static file cannot name by UUID);
  `permit call_tool when principal.granted_actions.contains(resource.action)`; `permit approve` and
  `enable` for a verified human (the gate still refuses the deployer and the sole sponsor as
  checker; locally that is not separation of duties). Cedar schema additions, schema version bump:
  `Agent.granted_actions`, `Agent.data_class`, `Agent.review_by`, `Agent.owner`, `Tool.action`,
  `Deployer.human`; `policy_request`, `host._entities` and `host._call_entities` fill them, and the
  deployment pin stores `granted_actions`. `data_class` is input for operator restrictions (a
  `restricted-data` example in the docs); the baseline does not use it.
- **`revision_digest`** still covers the whole operator config, so after an unrelated `up` a
  redeploy records a new revision; a running deployment keeps its recorded revision (`host._record`).

## 8. Tool calls

### 8.1 In-process ADK tools (I4)

- **Runtime.** The first `runtime/adk` version after the 1.2.0 that M12 and #780 share; if #780
  slips, I4 ships its own runtime version (an ordinary registry publish). It adds a call in `before_tool_callback` for every tool that is not an MCP tool
  (`tool.agentbreeder_mcp` unset). The action is `tool:<tool>.<function>`, from the
  function-to-ref map in the runtime config (`build._config`'s `tools`); a function with no mapping
  is denied without a call. Allow: the tool runs. Deny, an unauthenticated or malformed reply, a
  timeout (5 s) or an unreachable service: the callback returns `{"error": "TOOL_DENIED"}`, the tool
  does not run, and the runtime writes its `tool:<function>` row with `ok: false, error:
  "TOOL_DENIED"`. **Fail closed without a service:** when the baked config lists tools and
  `AGENTBREEDER_DECISION_URL` or its token is missing, the runtime refuses to start.
- **Host.** `egress/host.py` gains an in-process mode with its own pin and health fields
  (`policy_digest`, `provider_digest`), which `egress/boundary.py` checks for this mode instead of
  `operator_config_digest`. `_operator()` changes: in this mode it does not require
  `runtime_enforcement.mode == "mcp_http"`, and it compares two digests instead of the whole
  config: the **policy digest** (canonical JSON of the `pdp` section, the baseline and restriction
  digests, and this agent's `identity`, `requires` and `actions`) and the **provider digest**
  (canonical JSON of this agent's provider block: URL, realm, client id, key and JWKS paths).
  Whole-config settings such as `unique_principal` are deploy-time only and not in either digest,
  so another agent's `up` changes do not refuse this agent's calls; a changed provider block
  denies with `RUNTIME_CONFIG_CHANGED` until redeploy. Key and JWKS file contents are re-read on
  each resolve, so a key rotation does not need a redeploy. It re-checks the digests, the
  deployment revision and the kill switch, evaluates `call_tool` with baseline and restrictions,
  writes the `tool_call` audit row (arguments digest only) **before** answering, and answers deny
  if that write fails. Held calls and `approve-call` work as for MCP. A per-deployment rate cap
  (default 600 decisions per minute) and a 4 KiB request cap bound what an agent can add to the
  chain. The server becomes threaded with decisions serialized on its one SQLite handle;
  verification records decision latency under concurrent turns.
- **Decision protocol v2.** v2-only in this mode (no fallback to an older form):
  - The token has at least 256 bits of entropy. Both sides derive `K_req` and `K_resp` with
    HKDF-SHA256, salt = deployment ID ‖ revision, and distinct info labels. The token is never
    sent; the host keeps only the derived keys in its 0600 pin.
  - A request carries a random nonce, a timestamp and `HMAC(K_req, nonce‖timestamp‖method‖path‖body)`.
    The host verifies the MAC first, then refuses a timestamp outside ±30 seconds or a nonce seen
    in the window; the nonce cache holds only the window's nonces, so invalid requests never grow
    it, and a host restart is covered by the window rule.
  - A reply carries `HMAC(K_resp, request MAC‖status‖body)`, binding it to its request.
  - Tests: replay within the window and after a host restart, a version downgrade, an impostor that
    records full exchanges, and a flood of invalid MACs with bounded memory.
  - Only the in-process mode uses v2 in #909; moving the sidecar to it is tracked separately.
- **One token per caller.** The runtime gets its own decision token, scoped on the host to `tool:*`
  decisions for its deployment; it can never use the sidecar's endpoints (held-call polling, MCP
  decisions).
- **Started** for every deployment whose manifest has a non-empty `tool_access`. The agent reaches
  it at `AGENTBREEDER_DECISION_URL` with its token mounted as a `_FILE` secret: on Docker Desktop
  through `host.docker.internal` on a loopback port (a different posture from the `mcp_http`
  boundary, which forbids that name; documented), on Linux through a read-only Unix socket mount.
- **Old runtimes.** `build` writes the runtime's capabilities (`_runtime_capabilities`) and the
  baked tool-function list as image labels. `deploy` reads them with `docker inspect` (as
  `runtime.py` already inspects images): the label's function list must equal the manifest's
  `tool_access` (`TOOL_ACCESS_MISMATCH`), so a hand-edited `tool_access: []` cannot hide baked
  tools, and a non-empty list needs `tool_decision` in the capabilities
  (`TOOL_DECISION_UNSUPPORTED`).
  With `require_tool_access`, a manifest without a `tool_access` key is refused, so dropping the
  key does not skip the decision service. Images not built from `runtime/adk` have no in-process
  check; docs say so.

### 8.2 MCP tools

Unchanged path: the relay grant from the operator config's `agents.NAME.mcp`, now bounded by the
required `mcp.*` roles (§7.2).

### 8.3 `kind` clusters

The host decision service is not reachable from `kind` pods in the first delivery. `deploy
--cluster` and `promote` of a manifest with non-empty `tool_access` are refused with
`TOOL_DECISION_UNSUPPORTED` (D4).

### 8.4 Freshness

The decision service re-resolves the principal with the gate client at most every 60 seconds
(the stated revocation latency). Granted actions are the **intersection** of the deploy-time
`actions` with the live roles, never a union, so a role granted after deploy takes effect only at
the next deploy. A failed or negative resolve denies until a later resolve succeeds; a disabled
principal, an expired `review_by` or an unresolvable principal denies every call. Keycloak being
down therefore stops tool calls (documented availability trade-off). `reconcile` and `status`
already re-resolve (`deployment.verify`); D5 asks whether `reconcile` should also stop
noncompliant agents (#742 gap 5).

## 9. Enterprise IdPs: reference-only

For an operator-configured provider nothing is created:

- New read-only command `agentbreeder identity plan AGENT --provider NAME [--environment ENV]
  [--json]` prints the principal the IdP admin must create: kind, the provider-specific object
  (Entra agent identity, Okta service app, AWS workload identity, Keycloak client with a service
  account), the attributes with the provider's configured keys, the roles (`env.<env>`, `tool.*`,
  `mcp.*` from the agent's references), the sponsor, and the `environments.<env>.agents.<name>`
  block (with `requires` and `actions`) to paste into the operator config once the IDs exist.
- Deploy refuses until it resolves (existing `PRINCIPAL_NOT_FOUND`, plus `Next: agentbreeder
  identity plan NAME --provider P`).

## 10. Console and `--json`

- `status --json`/`list --json` gain `identity: {provider, ref, owner, environment, review_by,
  data_class, roles: [names], granted_actions, compliance, as_of}`; names are display only. M7's
  strip shows it "as of last reconcile" and the "Sign in" action (§5.5).
- `services status --json` gains `identity: {realm, environment, agents: [{name, client,
  provisioned_at, drift: [...]}]}`; `services plan --json` gains `identity_diff` and
  `preview_digest`; a stopped `up --json` reports the apply-time diff and digest.
- M18's policy view shows the baseline copy (read-only, digest) and the operator's restrictions.
  The generated config stays "do not edit" (digest rule).

## 11. Tests and verification

- Unit: declaration schema; the provisioning planner against a fake admin API (delta grants,
  drift, missing applied record and `adopt`, agent removal and name reuse, downgrade refusal);
  the approval digest (`stack.yaml` changed after the diff was shown, realm changed after, expiry,
  unreadable realm); the realm check (foreign stack ID, extra client, enabled `master` user);
  `loopback_http` validation (non-loopback, `localhost`, missing `jwks_file`); `verify_token`
  `azp`/`max_age`; refresh (rotation, lock, override not refreshed, `logout`); generated config
  (golden); minimums and new deny codes (audit-mode non-overridability); the Cedar baseline
  decision table; `build` manifests and the capability label; decision protocol v2 (§8.1 list);
  caller-scoped tokens; rate and size caps; `identity plan` per provider; redaction canaries
  including refresh tokens, PEM and JWT patterns.
- Live (`AGENTBREEDER_DOCKER_TEST=1`, marker `keycloak_live`): the I1 spikes; no first-admin path
  and no enabled `master` user, also after a restart; a pre-seeded realm on the volume fails
  `IDENTITY_REALM_FOREIGN`; the adapter against the real realm (so it becomes live-verified for
  this setup); the gate key gets 403 on every write; an agent service-account token is
  `DEPLOYER_UNVERIFIED`; acceptance 1–3 end to end with the headless test helper; the wheel and
  sdist do not contain that helper.
- 3.11, 3.12, 3.13 for touched tests and the help goldens; Playwright for the M7 strip.
- Verification record `docs/superpowers/verification/909.md`: cold start, `up` overhead, decision
  latency, image size and memory, leak scan, teardown to zero.

## 12. Migration

- Stacks without `environment.identity` (and never provisioned) behave exactly as today.
- New starters get the service and `environment.identity` (D1), with the runtime bump of I5.
- Existing stacks opt in by adding both; the first `up` shows the full identity diff for approval.
  From then on deploys need `agentbreeder login` and the 8-hour session (§5.5), including
  `release`, `promote` and the console.
- An agent onboarded with `--stack-for` on an older runtime gets `tool_access` but no decision
  call; once I4 lands its next `build` fails `TOOL_DECISION_UNSUPPORTED` with `Next: agentbreeder
  agent set-ref NAME runtime/adk@<version>`.
- Existing deployment records keep their `demo` principal key; `reconcile` reports them
  `noncompliant:PRINCIPAL_NOT_FOUND` once the static provider is gone, and the docs say to redeploy.

## 13. Delivery slices (sub-issues of #909)

| Slice | Content | Depends | Acceptance |
|---|---|---|---|
| I1 | `service/keycloak@1.0.0` (Dockerfile, start sequence, realm file param, no first-admin path, spikes), `loopback_http`, adapter `client_auth`/`jwks_file`, shared RS256 helper, D8 dependency, `keycloak_live` adapter tests | — | — |
| I2 | Declaration schema, `services/identity.py` (two-stage approval, realm check, delta, drift, `adopt`, removal, downgrade), generated config, deployer issuer with `azp`/`max_age`/`refresh`, `login` refresh and `logout`, `services identity rotate` | I1 | — |
| I3 | Operator minimums and deny codes, Cedar baseline copy and schema bump, `build` manifest fields, JSON Schema, `new agent`/`--stack-for` writing the declaration, `identity plan` | I2 | 1, 2 |
| I4 | Decision protocol v2 (in-process mode only), host in-process mode, caller-scoped tokens, caps, runtime decision call (its own runtime version if #780 slips), capability label, TTL re-resolution, `kind` refusal, `set-ref` hint | I3 | 3 |
| I5 | Starter runtime bump, onboarding path for `--stack-for` agents, docs, verification record | I4 | all |
| I6 | Console fields (M7 strip and Sign in, M18 view, typed confirm for the identity diff) | I3, M7, M18 | 4 |

Until I4 lands, a granted `tool.*` role is required at deploy but not checked per call; the docs
say so.

## 14. Owner decisions

| | Question | Recommendation |
|---|---|---|
| D1 | Put `service/keycloak` in every `new agent` starter by default? Cost: one more image and a JVM with a cold start measured in I1 | Yes, as #909 decided, gated on the I1 numbers; `new agent --identity static` keeps the light starter |
| D2 | Local deployer verification: device flow with refresh in an 8-hour session (§5.5), or a loopback-only password grant | Device flow; the password grant is deprecated in OAuth 2.1 and would put a reusable password in every CLI run |
| D3 | Make `cedarpy` a default dependency so the local PDP works on a bare install (affects `pip`, `uvx`, `npx` and the `quickstart` footprint) | Decide with D8 |
| D4 | `kind`: refuse manifests with non-empty `tool_access` until a later slice, or run the decision service in-cluster | Refuse first |
| D5 | Should `reconcile` stop agents that fail identity checks (#742 gap 5)? | Not by default; an opt-in `reconcile --stop-noncompliant` later |
| D6 | Move Northwind to the Keycloak identity? | After I5, as its own PR with the 61/61 rerun |
| D7 | #909 says `new agent` creates the principal; this spec declares at `new agent` and creates at an approved `services up`, because nothing runs Keycloak at `new agent` time | Accept the split |
| D8 | RSA keys for `private_key_jwt` and the realm signing key need `cryptography`. Ship it how? (a) an `agentbreeder[identity]` extra with `cedarpy`; (b) default dependencies (with D3); (c) `client-secret-jwt` (HMAC, stdlib) instead, which puts client secrets in `realm.json` and still needs a realm key generator | (b): one install path for the local default, and both libraries ship wheels for the supported platforms; `docs/install` updated |

## 15. Cross-repo split

- **OSS:** everything above. Manifest (`tool_access`, `requires`, dotted attribute keys) and
  operator-config changes update the JSON Schema, `docs/manifest.md`, `docs/identity-gate.md`,
  `docs/services.md`, `docs/install` (D8) and the command pages.
- **Cloud:** no code (no OSS version pin); a `CLAUDE.md` note when I3 lands, after a grep for the
  manifest fields.
- **Website:** nothing until release R. Any public wording uses "cooperative in-process check" for
  tool decisions.

## 16. Files touched by implementation (for reviewers)

`services/schema.py`, `services/engine.py`, `services/state.py`, `services/operator.py`, new
`services/identity.py`, `builtin_registry/service/keycloak/1.0.0/`, `operator_config.py`,
`identity/keycloak.py`, `identity/__init__.py`, `identity/transport.py` callers, `deployer.py`,
`gate.py`, `policy/cedar.py`, `policy/cedar_schema.json`, new `policy/local_baseline.cedar`,
`build.py`, `manifest.py`, `runtime.py`, `deployment.py`, `egress/host.py`, `egress/protocol.py`,
`egress/boundary.py`, `newagent.py`, `stackfor.py`, `commands/` (`services`, `identity`, `login`,
`logout`), the next `runtime/adk`, `redaction.py`, `pyproject.toml` (D8).

## 17. Review log

| Round | Reviewer | Lens | Head reviewed | Findings | Outcome |
|---|---|---|---|---|---|
| 1 | Claude Sonnet | architecture | r1 (uncommitted) | 3 Blocker, 6 Major, 7 Minor, 3 Nit: loopback HTTP plumbing; bootstrap cannot provision; whole-config digest pin and `mcp_http` requirement in `host._operator`; service schema (guard, read-only root, management port); user-profile attributes; Cedar baseline (`read_private`, UUIDs, `Tool.action`, `Deployer.human`); no sidecar for built agents; headless login; slice order; D7; `requires.roles` reuse; gate client rights; `unique_principal` scope; tool naming; decision concurrency | Applied in r2 |
| 1 | Claude Sonnet | security | r1 (uncommitted) | 5 High, 5 Medium, 3 Low, 1 Nit: port squatting (unsigned admin JSON, secret sent); bootstrap admin lifecycle; unreviewed grants via `stack.yaml`; revocation undone by digest changes or lost state; downgrade to static; device-flow phishing and token lifespan; decision protocol hardening; fail-open edges; overclaim; minimums gaps; provisioner scope and rotation; port stability; missing threats | Applied in r2 |
| 2 | Claude Sonnet | architecture | r2 (uncommitted) | 0 Blocker, 3 Major, 6 Minor, 1 Nit: dotted attribute keys fail `_ATTRIBUTE_KEY`; RSA signing needs a dependency (D8); the approval diff cannot come from offline `plan`; realm import path and the generated file param; `sig`-only JWKS and stable `iss`; in-process pin and health fields; capability check bypass and fail-open without a decision URL; slice dependencies and `--stack-for` agents; refresh-token races | Applied in r3 (§5.1, §5.3, §5.5, §7, §8.1, §12–§14) |
| 2 | Claude Sonnet | security | r2 (uncommitted) | 2 High, 7 Medium, 4 Low: `master` first-admin path; stale realm on the volume and trust-on-first-use of the JWKS; approval digest TOCTOU and replay; protocol v2 details; one token for several callers and audit flooding; `loopback_http` without a pinned key; re-resolution pinning and semantics; refresh-token handling; test helper packaging; `client-jwt` rotation; port race; applied-record size and `adopt`; missing threats | Applied in r3 (§5.1, §5.3, §5.5, §6, §7.1, §8.1, §8.4, §11) |
| 3 | Claude Sonnet | architecture | r3 (uncommitted) | 0 Blocker, 2 Major, 4 Minor: start sequence contradicted the no-first-admin rule; `realm.json` unreadable by the service uid at 0600; empty `tool_access` could hide baked tools; sidecar v2 move out of scope; realm-check baseline vs `adopt`; two-run non-interactive approval | Applied in r4 (§5.1, §5.3, §8.1) |
| 3 | Claude Sonnet | security | r3 (uncommitted) | 0 Critical/High, 3 Medium, 2 Low, 1 Nit: half-initialized volume; realm key lifecycle and single `sig` provider; provisioner assertion over the published port; refresh lock and reuse detection; approval is not a barrier against the user's processes; runtime JWKS check | APPROVED; findings applied in r4 (§5.1, §5.3, §5.5, §6) |
| 4 | Claude Sonnet | architecture | r4 (uncommitted) | 1 Major: the internal-network provisioning helper is not feasible with the existing `oneshot` (service image, read-only mounts, 8 KiB output) | Applied in r5: host provisioning after credential-free port and pinned-key checks, residual stated in T7, helper image recorded as later hardening |
| 4 | Claude Sonnet | security | r4 (uncommitted) | 0 Critical/High, 2 Low: bind stack ID and environment into the approval diff; helper container hardening | APPROVED; both applied (the helper later replaced, see architecture round 4) |
| 5 | Claude Sonnet | architecture | r5 (uncommitted) | 0 Blocker/Major, 1 Nit: cross-reference the two realm-check paragraphs | APPROVED; nit applied |
| 5 | Claude Sonnet | security | r5 (uncommitted) | 0 Critical/High, 3 Low: 30-second provisioner assertion and `docker port` re-check in the same step; detect replays by comparing token issuances; track the helper-image hardening as an issue | APPROVED; first two applied, the third is in the PR's open items |
