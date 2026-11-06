# Agent identity gate — implementation plan

- **Spec:** `docs/superpowers/specs/2026-11-05-agent-identity-gate-design.md`
- **Tracking:** epic #701; #702 (P0), #703 (P1), #704 (P2), #705 (P3), #706 (P4), #707 (P5)

## Module layout

| Module | Responsibility | Phase |
|---|---|---|
| `manifest.py` | `schema_version: 2`, `identity`, scoped `requires`, `capabilities`, `policy.restrictions` (bundle IDs only) | P0 |
| `operator_config.py` | Owner-only operator config, enforcement mode, environments, provider connections, PDP sources, digest | P0 |
| `audit.py` | Canonical JSON, hash chain, verification report | P0 |
| `store.py` | Schema v2 migration: deployment identity columns, `audit`, `approvals`, `blockers`; audit appended in the same transaction as state changes | P0–P3 |
| `identity/` | `IdentityProvider` protocol, grants, registry + entry points, JSON transport, `static`, `entra`, `keycloak`, `okta` (experimental), `aws` | P0, P1, P3 |
| `deployer.py` | OS (unverified) actor, OIDC RS256 token verification against operator-trusted issuers, RFC 8628 device flow | P0, P1 |
| `policy/` | AuthZEN-shaped request, baseline AND restrictions combination, obligations, Cedar (pinned `cedarpy`), OPA, AuthZEN | P2 |
| `gate.py` | `IdentityGate.check()` — checks 1–9, stable codes, implemented/unavailable checks | P0–P2 |
| `deployment.py` | Attempt audit, gate before runtime, approval binding/consumption, disable/enable, reconcile | P0–P3 |
| `cli.py` | `--environment`, `login`, `audit verify/list`, `approve`, `reject`, `disable`, `enable`, `reconcile`, `status` re-verification | P0–P3 |

## Sequencing

1. P0: manifest v2 + schema/docs, operator config, store v2 + audit chain, static adapter, gate checks 1–8, CLI exit 2.
2. P1: OIDC deployer verification (CI token + device flow), Entra and Keycloak adapters from recorded fixtures, status re-verification.
3. P2: PDP contract, Cedar/OPA/AuthZEN engines, pinned bundle digests and verified remote revisions, approvals bound to revision digests.
4. P3: durable blockers, disable/enable, reconcile, AWS and Okta adapters.
5. P4: egress boundary per the phase-4 design (option C, approved 2026-11-05): internal network + sidecar PEP + host decision service, verification probes, held calls, credential broker. Verified on real Docker Desktop (macOS).
6. P5: blocked on non-local targets.

## Decisions

- Cedar obligations: a source adds `require_approval` when it permits the `RequireApproval` action. Restrictions can add obligations, never remove baseline ones. Operator `maker_checker.risk_tiers` adds the obligation from the verified risk attribute.
- Approval resume is a re-run of `deploy`: the full gate runs again and the rebuilt revision must match an approved, unexpired request whose evidence is unchanged.
- Audit-mode overrides apply only to identity checks 1–7 and PDP denies. Blockers, unknown context, unsupported controls, audit failures and approval obligations are never overridden.
- Enabling a blocked principal requires a verified actor and a PDP allow for `enable`; disabling is allowed for any local actor and always audited.
- RS256 signature verification is implemented with integer arithmetic (PKCS#1 v1.5, full encoded-block comparison) so the core package needs no crypto dependency; other algorithms are rejected.
