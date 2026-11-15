# `agentbreeder reject-call`

Reject one held MCP tool call as a second person.

Implemented: `approve-call`/`reject-call`. See also [`approve-call`](approve-call.md).

For a project brought up with `services up`, pass `--stack DIR --environment ENV`: the
command then reads that stack's state and is checked against the stack's generated operator
config (the same as `--state-dir` plus `AGENTBREEDER_OPERATOR_CONFIG`). `--environment` needs
`--stack`, and `--stack` can't be combined with `--state-dir`.

Flags: `agentbreeder reject-call --help`.
