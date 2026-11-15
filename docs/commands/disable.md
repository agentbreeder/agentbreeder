# `agentbreeder disable`

Persist a principal blocker and stop its workloads.

Implemented: `disable`/`enable`. See also [`enable`](enable.md).

For a project brought up with `services up`, pass `--stack DIR --environment ENV`: the
command then reads that stack's state and is checked against the stack's generated operator
config. Without `--stack`, `--environment` names the principal's environment (needed with
`--provider`/`--ref`); with `--stack` it is the stack environment. `--stack` can't be combined
with `--state-dir`.

Flags: `agentbreeder disable --help`.
