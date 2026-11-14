# `agentbreeder migrate`

Upgrade the local state to this version's schema (no IdP calls).

`agentbreeder migrate [--state-dir DIR | --stack DIR [--environment ENV]]` only upgrades the local state (no identity-provider calls; `status` re-verifies at the IdP and is not a substitute) (on `main`, not in a published preview). [`list`](list.md) says to run it when the state is from an older version or an interrupted write left a journal.

Flags: `agentbreeder migrate --help`.
