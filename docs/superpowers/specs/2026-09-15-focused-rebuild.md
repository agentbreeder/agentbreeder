# Focused rebuild: clean baseline

Status: implementation authorized by the user's request to create a branch, remove unnecessary features and start from scratch following the agreed focused-release plan.

## Scope of this slice

Replace the legacy broad-platform source tree with a small installable Python package. Ship two honest commands: validate a deployment manifest and diagnose local prerequisites. Include a real HTTP sample for subsequent deployment tests. Deployment, authentication, approvals and a web UI are not implemented by this slice and must not be advertised as available.

The previous implementation remains recoverable at Git revision 7b086d9. This is an incompatible development baseline, not an upgrade or release for existing installations. No database, local agent state, deployed workload, public website or published package is modified.

## Inputs and outputs

- Manifest: version 1; DNS-label agent name; immutable container image reference with SHA-256 digest; local or kubernetes target; HTTP port and health path; named secret references.
- Validation: bounded UTF-8 YAML input; reject duplicate keys, unknown fields, multiple documents, invalid values and literal secret values. Return a validated manifest or a useful error and nonzero exit.
- Doctor: read-only Docker client/daemon checks with a bounded timeout. Report missing/unavailable prerequisites without installing software or changing the host.
- Sample: /health returns a real health response, /invoke echoes a bounded JSON payload and explicitly identifies itself as a deployment demonstration rather than an AI agent.

## Architecture

One src/agentbreeder package. Manifest contracts remain independent of the eventual API and runtime adapters. Local and production execution will converge behind one durable deployment service in the next slices. No mock deployment endpoints or synthetic success paths are retained.

Use existing Python tooling and minimal dependencies. Preserve repository licensing, contributor/security policies, local private planning and unrelated user edits. Replace old tests/CI that only exercise removed products with tests of the supported baseline. Disable obsolete release/site deployment workflows on this branch; do not publish a new release.

## Acceptance

- Branch and recoverable snapshot exist before pruning.
- Only explicitly inventoried tracked legacy files are removed. Untracked/ignored user data is untouched.
- Package installs/builds and the CLI help accurately lists only working commands.
- Strict manifest validation handles valid input, unknown/duplicate keys, wrong types, oversized input and malformed YAML safely.
- Doctor is read-only, bounded and testable without Docker.
- Sample handles valid requests, malformed JSON, body-size limits and unsupported paths.
- No legacy broad-platform entry point or release job remains active.
- Local private roadmap and resume cursor record completion and unfinished work truthfully.

## Design review conclusions

- Architecture: establish one package and contract now; real deployment and durable storage are the next milestone, not hidden stubs.
- Security: immutable image references; no plaintext secret configuration; no host modifications by diagnostics; sample binds loopback by default and is explicitly not production authenticated.
- UX: two commands with actionable errors; no visual builder or inactive UI controls. No frontend is shipped in this slice.
- Compatibility: intentionally breaking development branch; existing users must retain their release. Preserve Git recovery and document that there is no migration path yet.

## Remaining decisions

Production target certification, OIDC provider fixture, durable worker/storage details and customer upgrade handling remain future engineering tasks. Kubernetes is a contract option, not proof of working Kubernetes deployment.
