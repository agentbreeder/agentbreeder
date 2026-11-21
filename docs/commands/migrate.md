# `agentbreeder migrate`

Upgrade the local state to this version's schema (no IdP calls).

`agentbreeder migrate [--state-dir DIR | --stack DIR [--environment ENV]]` only upgrades the local state (no identity-provider calls; `status` re-verifies at the IdP and is not a substitute) (on `main`, not in a published preview). [`list`](list.md) says to run it when the state is from an older version or an interrupted write left a journal.

Schema v4 (#763, on `main`, not in a published preview) adds the deployment's `package` column. The first command that opens the state for writing (`deploy`, `release`, `remove`, `status`, `migrate`, …) upgrades a v3 state in place and keeps every row; read-only commands (`list`, `logs`, `invoke`, `watch`) ask for `migrate` until then. AgentBreeder 3.0.0a2 cannot read a v4 state.

Schema v5 (#771 K0, on `main`, not in a published preview) adds the `cluster_bindings` table and tags each Kubernetes deployment's recorded cluster binding with its provider: a record written as `<context>|<uid>|<namespace>|<kubeconfig>` becomes `kind|<context>|<uid>|<namespace>|<kubeconfig>` (only rows whose target is `kubernetes` and that hold exactly four fields; the rewrite is idempotent). Local Docker records are unchanged. It upgrades v4 (and older) state the same way, on the first command that opens the state for writing; until then the read-only commands ask for `migrate` as for v4 (`registry used-by|show|diff` and the console's reads accept v2 to v5 and need none). `status --json` then shows the tagged value in `context`. Earlier versions cannot read a v5 state.

Flags: `agentbreeder migrate --help`.
