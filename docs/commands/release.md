# `agentbreeder release`

Build, deploy a candidate, run the eval gate, promote the exact image if it passes.

Implemented: `release` (see [`agent-yaml.md`](../agent-yaml.md)).

A successful `release` (including `--rollback` and an image already released) ends with `Next: … invoke NAME Hello`, with the `--stack` and `--environment` or the `--state-dir` it used, and `--json` carries the same command as `advice.invoke`; a release that did not reach a ready production prints none (on `main`, not in a published preview).

`release` writes an agent package, `./registry/agent/<name>/<X.Y.Z>/`, for each image it
releases. `--version X.Y.Z` picks the version (default: the next free patch, `0.1.0` first); a
version that is taken exits 2 with `VERSION_EXISTS` before any eval runs. See "The agent package"
in [`agent-yaml.md`](../agent-yaml.md#eval-and-release) (on `main`, not in a published preview).

After the `promoted` row, `release` links production to its package (`deploy/package_linked`; `status --json` shows `package`) (#763, on `main`, not in a published preview). If that link cannot be written, the release still stands: it exits 1 with `PACKAGE_LINK_FAILED`, and running `release` again for the same image repairs the link (`PACKAGE_EXISTS`).

Flags: `agentbreeder release --help`.
