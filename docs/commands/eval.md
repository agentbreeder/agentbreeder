# `agentbreeder eval`

Run an agent's eval set against a running endpoint.

Implemented: `eval` (see [`agent-yaml.md`](../agent-yaml.md)).

On `main` (not in a published preview), cases may use the sub-agent and skill rules and carry `tags`, and an unknown `expect` key is refused when the set loads (`EVAL_INVALID`). For a set with tagged cases, the text output ends with one `Tag NAME: passed/cases passed (rate)` line per tag and `--json` has `tags`; `eval` itself exits by the overall threshold only (`evals.tag_threshold` gates `release` and `promote`). See "Eval and release" in [`agent-yaml.md`](../agent-yaml.md#eval-and-release) (#1058).

Flags: `agentbreeder eval --help`.
