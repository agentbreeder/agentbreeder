# `agentbreeder deploy`

Gate and deploy a manifest, or a released agent/NAME@X.Y.Z package.

Implemented: `deploy MANIFEST --local --environment ENV` (needs an operator config; see [`identity-gate.md`](../identity-gate.md) and [`first-deployment.md`](../first-deployment.md)).

`deploy agent/NAME@X.Y.Z --local [--project DIR] [--environment ENV]` deploys a package that [`release`](release.md) wrote (#763, M13 slice 3; on `main`, not in a published preview). The argument is a package ref when it is exactly `agent/<name>@X.Y.Z`; anything else is a manifest path (write `./agent/…` for a file named like a ref). `--project` (default `.`) names the project whose `./registry` holds the package; the built-in registry and `AGENTBREEDER_REGISTRY_PATH` are never searched. Deploy then:

1. copies the package to a private temporary directory, hashes it and loads it from that copy;
2. checks the audit log (`audit verify`) and the `release/promoted` row for that ref in this state directory: the same project (a hash of its path), tree hash, image ID, threshold, eval set and eval run;
3. checks the released image is still in the local Docker engine;
4. binds the deploy template to the environment: identity, secrets and the gateway come from the operator config only, never from the package;
5. runs the identity gate, policy and approvals as for any deploy, records `DEPLOY_PACKAGE` on the allow event, and links the deployment to the package (`status --json` shows `package`).

A package is a record, not a credential, and is not signed. Deploy refuses a package this state directory did not release, or one that changed after release. Anyone who can write the state directory can rewrite the audit log: the hash chain detects corruption, not a database owner who recomputes it. Its evals ran in the release environment only; deploying to another environment prints a note and applies that environment's gate.

| Code | Exit | When |
|---|---|---|
| `REF_NOT_DEPLOYABLE` | 2 | a ref of another kind, such as `tool/x@1.0.0` |
| `PACKAGE_NOT_FOUND` | 2 | the version is not in `<project>/registry` (restore it from git, or check `--project`) |
| `PACKAGE_NOT_HERE` | 2 | found only in another registry root |
| `PACKAGE_INVALID` | 2 | not a valid package (files, `package.json`, a symlink) |
| `PACKAGE_UNRELEASED_HERE` | 2 | no release row for it in this state directory (`--state-dir` / `--stack`?) |
| `PACKAGE_OTHER_PROJECT` | 2 | released from another project with the same agent name, or this project moved; release again from here |
| `PACKAGE_CHANGED` | 2 | its tree hash differs from the release row |
| `PACKAGE_EVIDENCE_MISMATCH`, `PACKAGE_AMBIGUOUS` | 2 | the release row and `package.json` disagree, or two release rows differ (`audit verify`) |
| `AUDIT_CORRUPT` | 1 | the audit chain failed verification |
| `PACKAGE_IMAGE_MISSING` | 1 | the released image was pruned; released images cannot be rebuilt bit for bit, so release a new version |
| `PACKAGE_TARGET_UNSUPPORTED` | 2 | `--cluster`: deploy by ref is local only for now |
| `PROJECT_UNUSED` | 2 | `--project` with a manifest path |

`--json` prints `{"code", "error"}` for these. Each refusal except `AUDIT_CORRUPT` is recorded in the audit log as a `deploy` deny with the ref and code only; the gate's own deny and abort events name the package too. If a deployment of that name is running with a different spec, deploy refuses as for a manifest (remove it, or `release --rollback`); with the same spec it is reused, and a link to another package is kept with a warning. To roll production back to an earlier package: `remove ID`, then `deploy agent/NAME@<previous> --local`.

Flags: `agentbreeder deploy --help`.
