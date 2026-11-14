# `agentbreeder audit`

Inspect and verify the local audit chain.

Implemented: `audit list`/`audit verify`.

`audit list [--limit N] [--before SEQ] [--attempt ID] [--json] [--state-dir DIR]` prints audit
rows newest first (`--limit` default 20, at most 1000). `--before SEQ` keeps only rows older than
that seq, so the next page starts before the last seq printed; it must be a positive whole number
and cannot be combined with `--attempt` (`[BEFORE_INVALID]`, exit 2). `--attempt ID` prints one
attempt's rows, oldest first. `--json` prints every column of each row plus `agent`: the name of
the row's deployment (removed deployments included, so a release candidate shows as
`NAME-candidate`), or null when the row has no deployment (`attempt_started`, `release`,
`idp_disable`). `--before` and `agent` are on `main`, not in a published preview.

`audit verify [--json] [--state-dir DIR]` recomputes every link and hash and names the first
broken row and any attempt without a final row; it exits 1 when the chain is broken. The chain
detects corruption; whoever owns the database can recompute it.

Flags: `agentbreeder audit --help`.
