# `agentbreeder release`

Build, deploy a candidate, run the eval gate, promote the exact image if it passes.

Implemented: `release` (see [`agent-yaml.md`](../agent-yaml.md)).

A successful `release` (including `--rollback` and an image already released) ends with `Next: … invoke NAME Hello`, with the `--stack` and `--environment` or the `--state-dir` it used, and `--json` carries the same command as `advice.invoke`; a release that did not reach a ready production prints none (on `main`, not in a published preview).

Flags: `agentbreeder release --help`.
