# `agentbreeder status`

Show a deployment and re-verify its principal.

Implemented: `status ID`.

`status --json` includes `package`: `{"ref", "sha256"}` for a deployment made from, or released as, an agent package, else `null` (#763, on `main`, not in a published preview).

Flags: `agentbreeder status --help`.
