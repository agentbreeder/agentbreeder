# `agentbreeder migrate`

Upgrade the local state to this version's schema (no IdP calls).

`agentbreeder migrate [--state-dir DIR | --stack DIR [--environment ENV]]` only upgrades the local state (no identity-provider calls; `status` re-verifies at the IdP and is not a substitute) (on `main`, not in a published preview). [`list`](list.md) says to run it when the state is from an older version or an interrupted write left a journal.

Schema v4 (#763, on `main`, not in a published preview) adds the deployment's `package` column. The first command that opens the state for writing (`deploy`, `release`, `remove`, `status`, `migrate`, …) upgrades a v3 state in place and keeps every row; read-only commands (`list`, `logs`, `invoke`, `watch`) ask for `migrate` until then. AgentBreeder 3.0.0a2 cannot read a v4 state.

Flags: `agentbreeder migrate --help`.
