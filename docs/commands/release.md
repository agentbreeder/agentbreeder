# `agentbreeder release`

Build, deploy a candidate, run the eval gate, promote the exact image if it passes.

Implemented: `release` (see [`agent-yaml.md`](../agent-yaml.md)).

A successful `release` (including `--rollback` and an image already released) ends with `Next: … invoke NAME Hello`, with the `--stack` and `--environment` or the `--state-dir` it used, and `--json` carries the same command as `advice.invoke`; a release that did not reach a ready production prints none (on `main`, not in a published preview).

`release` writes an agent package, `./registry/agent/<name>/<X.Y.Z>/`, for each image it
releases. `--version X.Y.Z` picks the version (default: the next free patch, `0.1.0` first); a
version that is taken exits 2 with `VERSION_EXISTS` before any eval runs. See "The agent package"
in [`agent-yaml.md`](../agent-yaml.md#eval-and-release) (on `main`, not in a published preview).

Flags: `agentbreeder release --help`.
