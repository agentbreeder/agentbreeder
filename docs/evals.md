# Saved eval reports (`agentbreeder evals`)

On `main`, not in a published preview (#932).

```
agentbreeder evals list [--project DIR | --output DIR] [--agent NAME] [--json]
agentbreeder evals show [RUN] [--project DIR | --output DIR] [--agent NAME]
                        [--content] [--state-dir DIR | --stack DIR] [--json]
```

Every `release` that runs its eval set saves the report as
`<output>/<agent>.eval-<run>.json`, where `<output>` is `release --output DIR` or, by default,
`deploy/` next to `agent.yaml`. `evals` reads those files back and changes nothing. The file
format belongs to `release` and is unchanged; `evals` is the supported way to read it. The
console's report viewer reads `evals show --json`.

- `evals list` lists the reports in the directory, newest run first, at most 100.
- `evals show RUN` shows one report. Without `RUN`, it shows the newest. A run id saved under
  two agents needs `--agent NAME`.
- The directory is `--output DIR`, else `DIR/deploy` for `--project DIR` (default `.`).

## What is shown

The judge's reason and the agent's reply are model output, and case ids, check names and tool
names come from the eval set. Every string shown is redacted first, with the values the CLI finds
in local state (`--state-dir`, or the state `--stack DIR` names: stored secrets and deployments'
secret copies, as for `logs`), and the password of any `scheme://user:password@host` URL. If a
value cannot be read, nothing is printed (`REDACTION_INCOMPLETE`).

- Header and per-case strings are made one line, with control characters replaced, and capped
  (ids, check and tool names at 80 characters; the judge's reason at 200, as in `release`'s
  failure output). Check names that become equal once capped or redacted get a `#2`, `#3`
  suffix (after the cap), so a failed check is never hidden by a passed one.
- Only the start of each string is redacted: what is shown plus twice the margin, so a 4 MiB
  string costs no more than a short one. The margin is 16 KiB, or the longest form of a stored
  value (literal, base64, URL- or JSON-quoted) when that is longer, so every secret form that
  starts in the shown part is found. If redaction replaces more than the margin in a string
  longer than its window, the field is `[not shown: mostly redacted]`.
- At most 20000 distinct strings are redacted per report (repeats are redacted once; strings
  under 4 characters can hold nothing redaction matches and are not counted). Past that, every
  new string is `[not shown: too many strings to redact]` and `notes` says so.
- The agent's `reply` and the full judge `explanation` are shown only with `--content`. They
  are redacted and capped at 4096 characters, newlines are kept, and in text output control
  characters are shown as `\xNN` escapes.
- `trace_id` is the trace id when it is 32 lowercase hex characters, else `null`; read the
  trace with `agentbreeder traces --trace ID` (once that command is on `main`). `category` is
  `null` when the case has none. Both keys are always present.

Redaction is best effort; see `redaction.py` for what it finds.

## Safety checks

- `RUN` must look like `20261113-213200-1a2b3c` and `--agent` must be an agent name. Only file
  names of the form `<agent>.eval-<run>.json` are listed or opened.
- The directory must be a directory and not a symlink. A report must be a regular file: it is
  opened with `O_NOFOLLOW` (a symlink is refused) and checked with `fstat` (a FIFO or device is
  refused).
- At most 4 MiB is read per report, and `evals list` parses at most 32 MiB of reports in all
  (a report past that is a row with `EVAL_REPORT_TOO_LARGE`; `evals show RUN` still reads it; a
  report over 4 MiB is refused unread and not counted).
  The JSON must be strict: `NaN`, `Infinity`, a repeated key in one object and nesting too deep
  to parse are refused.
- The summary fields are type-checked, and the run id inside must match the file name. A
  malformed summary makes the report invalid. A malformed case (a missing or wrong-typed
  `case_id`, `passed`, `checks`, `attempts`, `latency_ms` or `correctness`, a `tools` that is not
  a list, an `explanation` that is not a string, or more than 100 checks) is skipped and counted
  in `notes`. A missing `tools` is `[]`; a missing or `null` `explanation` is empty; a missing
  `category` or `trace_id` is `null`. A `category` or tool name that is a number is shown as
  text; any other non-string value is dropped (`category` is then `null`). At most 5000 cases
  and 100 tools per case are shown; the summary's `flaky` and `failed` lists keep at most 5000
  string ids.

## `evals list --json`

```json
{
  "schema_version": 1,
  "reports": [
    {
      "agent": "support",
      "run_id": "20261113-213200-1a2b3c",
      "file": "support.eval-20261113-213200-1a2b3c.json",
      "modified": 1763098320,
      "eval_set": "eval/support@1.0.0",
      "judge": "judge-model",
      "cases": 2,
      "passed": 1,
      "pass_rate": 0.5
    },
    {
      "agent": "support",
      "run_id": "20261112-090000-abcdef",
      "file": "support.eval-20261112-090000-abcdef.json",
      "modified": 1763010000,
      "error": {"code": "EVAL_REPORT_INVALID", "message": "… is not valid JSON"}
    }
  ],
  "total": 2,
  "truncated": false
}
```

A report that cannot be read is a row with `error` instead of the summary fields. `modified`
(the file's mtime, epoch seconds) is absent only when the file could not be inspected. `total`
counts every matching report, and `truncated` says whether more than the rows shown exist.

## `evals show --json`

```json
{
  "schema_version": 1,
  "agent": "support",
  "file": "support.eval-20261113-213200-1a2b3c.json",
  "run_id": "20261113-213200-1a2b3c",
  "eval_set": "eval/support@1.0.0",
  "judge": "judge-model",
  "endpoint": "http://127.0.0.1:8080",
  "cases": 2,
  "passed": 1,
  "pass_rate": 0.5,
  "flaky": [],
  "failed": ["c2"],
  "mean_correctness": 0.5,
  "p50_latency_ms": 120,
  "content": false,
  "results": [
    {
      "id": "c2",
      "category": "billing",
      "passed": false,
      "attempts": 1,
      "checks": {"agent_responded": true, "judge_correct": false},
      "failed_checks": ["judge_correct"],
      "correctness": 0.1,
      "reason": "wrong answer",
      "latency_ms": 120,
      "trace_id": "abababababababababababababababab",
      "tools": ["lookup_order"]
    }
  ],
  "notes": []
}
```

The header is the summary `release` computed and saved: `cases`, `passed` and `pass_rate` count
every case in the run, including any skipped as malformed. A case passed after a retry has
`attempts` above 1 and is listed in `flaky`. `endpoint` is the candidate the run evaluated,
which `release` removes afterwards. When the summary's `cases` or `passed` differ from the
results list, `notes` says so. With `--content`, each result also has `reply` and
`explanation`, and `content` is `true`. Fields may be added within schema version 1; none will be
removed or change meaning.

## Errors

Errors print `[CODE] message` on stderr and exit 2. With `--json`, the one-line
`{"schema_version": 1, "error": {"code": …, "message": …}}` also goes to stdout.

| Code | When |
|---|---|
| `EVAL_REPORT_NOT_FOUND` | The directory is missing, or no report matches |
| `EVAL_REPORT_AMBIGUOUS` | The run has reports for two agents; pass `--agent` |
| `EVAL_REPORT_INVALID` | A bad `RUN` or `--agent`, a symlink or non-regular file, bad JSON or a malformed summary |
| `EVAL_REPORT_TOO_LARGE` | The report is over 4 MiB (in `list`, also: past 32 MiB parsed in all) |
| `EVAL_REPORT_UNREADABLE` | Permission or I/O errors on the directory or file |
| `REDACTION_INCOMPLETE` | A value to redact could not be read, so nothing is printed |
| `USAGE` | `--project` and `--output` together |
