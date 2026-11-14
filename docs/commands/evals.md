# `agentbreeder evals`

List or show the eval reports release saved (read-only).

On `main`, not in a published preview (#932): `evals list [--project DIR | --output DIR] [--agent NAME] [--json]` lists the reports `release` saved as `<agent>.eval-<run>.json`, newest run first, and `evals show [RUN] [--project DIR | --output DIR] [--agent NAME] [--content] [--state-dir DIR | --stack DIR] [--json]` shows one report (the newest without `RUN`): passed/total, the judge, and per case pass/fail, failed checks, the judge's reason (one line, 200 characters) and the trace id. `--content` adds each reply and the full judge explanation, redacted and capped. Reports are read from `--output DIR` (as `release --output`), else `<project>/deploy`. Errors are `[CODE] message` (`EVAL_REPORT_NOT_FOUND`, `EVAL_REPORT_AMBIGUOUS`, `EVAL_REPORT_INVALID`, `EVAL_REPORT_TOO_LARGE`, `EVAL_REPORT_UNREADABLE`, `REDACTION_INCOMPLETE`), with `--json` also the error object. See [`evals.md`](../evals.md).

Flags: `agentbreeder evals list --help`, `agentbreeder evals show --help`.
