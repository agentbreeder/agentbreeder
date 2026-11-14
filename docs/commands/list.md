# `agentbreeder list`

List deployments that are not removed (read-only, no IdP calls).

`list` opens the local state read-only (no directory or database is created, no migration, no write lock, so it never blocks a running job); when the state is from an older version or an interrupted write left a journal, it says to run `agentbreeder migrate [--state-dir DIR | --stack DIR [--environment ENV]]`, which only upgrades the local state (no identity-provider calls; `status` re-verifies at the IdP and is not a substitute).

Flags: `agentbreeder list --help`.
