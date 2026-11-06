# Local deployment lifecycle

Engineering slice following #648. User authorized continuing the focused rebuild.

## Contract

A single-user CLI calls DeploymentService directly; a future authenticated API must call the same service rather than duplicate execution. No management HTTP listener is introduced. Local OS access to the state directory and Docker socket is the authorization boundary; this is not enterprise RBAC. Require explicit `--local` on mutating commands. Reject remote Docker transports and nonlocal manifests before side effects. Reject secret references until a resolver and its access controls exist.

Inputs: strict v1 manifest, immutable registry image digest or local `sha256:<64 hex>` image ID, local target, unprivileged HTTP service listening on its container port with GET health endpoint returning exactly HTTP 200. No host volumes, privileged mode, host networking or configurable shell commands. Local image IDs are rejected for Kubernetes targets. Dockerfile build stays a developer/CI command outside the service.

Commands: `deploy MANIFEST --local`, `status ID`, `remove ID --local`, each with `--state-dir PATH` and `--json`. Default state is `~/.agentbreeder/local`. Deploy prints its durable ID and status; a loopback endpoint appears only when a running container passes health verification. Status reports the last recorded observation, not a fresh health guarantee. Invocation protocol remains owned by the agent; the sample provides POST /invoke.

## State and recovery

SQLite schema version 1 stores deployment ID, canonical manifest, manifest digest, Docker context, deterministic container name, resource ID, state, endpoint, safe error, creation/update timestamps. Events are appended in the same transaction as state changes. Private state directory is mode 0700, SQLite file 0600. A nonblocking OS advisory lock serializes mutations per state directory and releases on process death. Read operations do not take the worker lock. This is a local worker design, not distributed leases.

State transitions: pending -> starting -> ready or failed; any owned deployment -> removing -> removed. A process interruption leaves an intermediate state. Repeating deploy with the same canonical spec/context resumes the same deployment; it inspects the deterministic container name and verifies deployment/spec labels before reuse. A different spec for an existing nonremoved agent name fails (updates are later work). A new deployment after removal gets a new ID. Docker failures never count as absence unless an explicit container listing establishes absence. Persist intent before container creation. On timeout, retain a failure record and reconcile on retry; never blindly create a second resource.

Docker commands use argument arrays and bounded timeouts. Container publishes only 127.0.0.1 on an assigned host port, drops capabilities, disables privilege escalation, runs UID/GID 65532, has read-only root filesystem and bounded tmpfs, memory, CPU, process count and logs. Uses the selected local Docker context consistently. No health redirect or environment proxy is followed. Failures retain the container for inspection/retry; remove checks ownership labels and deletes only that resource. Docker socket administrators can bypass all controls and modify labels/state.

## Acceptance criteria

- Validation/authorization failure performs no Docker mutations.
- Requests and transitions survive reopening SQLite; events and state commit together.
- Duplicate submission/restart reuses a resource with matching ownership; conflicting labels fail closed.
- Runtime creation and HTTP 200 are both necessary for ready; network errors and redirects fail health checks.
- Failed create/start/inspect/health operations persist failed status with no endpoint.
- Remove is idempotent, checks ownership and persists cleanup failures.
- Concurrent writers receive an actionable busy error.
- Real Docker sample test builds an image, deploys, invokes, repeats deployment, reopens state and removes the owned container. If Docker is unavailable report unverified, never substitute a mock as end-to-end evidence.

## Architecture/security/onboarding review

SQLite plus a process lock avoids a queue, daemon and database installation for local onboarding. Tradeoff: single-host execution; future server workers require transactional leases and server identity. No UI exists to visually review in this slice; CLI output includes ID, state and actionable errors, JSON is stable and failures use nonzero exit status. Local workloads have outbound network access; this slice makes no private-egress, authenticated-invocation or compliance claim. Only trusted images should run against the user's Docker daemon. No dependency on legacy cloud APIs; cloud imports of the removed engine need a separately versioned integration before release.

Production Kubernetes, identity, approvals, secret resolution, updates/rollback, shared tenancy, background worker, UI, automatic registry publishing and migrations from legacy releases remain outside this local slice. M0 production decisions remain open and M1 is not complete until its remaining UI/worker criteria are met.
