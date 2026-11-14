"""`agentbreeder evals list|show`: read the eval reports `release` saved (#932, read-only).

- `evals list [--project DIR | --output DIR] [--agent NAME] [--json]`: one row per saved report,
  newest run first (at most `eval_reports.MAX_LIST`).
- `evals show [RUN] [--project DIR | --output DIR] [--agent NAME] [--content] [--json]`: one
  report (the newest without RUN): passed/total, the judge, and per case pass/fail, failed
  checks, the judge's reason (one line, 200 characters) and the trace id. `--content` adds the
  agent's reply and the full judge explanation (each capped).

Reports are read from `--output DIR` (as `release --output`), else `<project>/deploy`. Strings
from a report are redacted with the values `redaction.collect` finds in the local state
(`--state-dir`, or `--stack DIR`); if one cannot be read, nothing is printed
(REDACTION_INCOMPLETE). Errors are `[CODE] message` on stderr (with `--json` also the one-line
`{"schema_version": 1, "error": {...}}` on stdout), exit 2. The contract is docs/evals.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import cli, eval_reports, safe_text
from agentbreeder.eval_reports import ReportError

NAMES = ("evals",)


def _where(command) -> None:
    command.add_argument(
        "--project",
        type=Path,
        metavar="DIR",
        help="The agent project: reports are read from DIR/deploy (default: .)",
    )
    command.add_argument(
        "--output", type=Path, metavar="DIR", help="Read reports from DIR (as release --output)"
    )
    command.add_argument("--agent", metavar="NAME", help="Only this agent's reports")
    command.add_argument("--state-dir", type=Path, default=cli.DEFAULT_STATE)
    cli.add_stack(command)
    command.add_argument("--json", action="store_true")


def register(commands) -> None:
    parser = commands.add_parser(
        "evals", help="List or show the eval reports release saved (read-only)"
    )
    sub = parser.add_subparsers(dest="evals_command", required=True)
    listing = sub.add_parser("list", help="List saved eval reports, newest first")
    _where(listing)
    show = sub.add_parser("show", help="Show one saved eval report, per case")
    show.add_argument("run", nargs="?", metavar="RUN", help="Run id (default: the newest)")
    _where(show)
    show.add_argument(
        "--content",
        action="store_true",
        help="Also show each reply and the full judge explanation (redacted, capped)",
    )


def run(args) -> int:
    try:
        if args.project is not None and args.output is not None:
            raise ReportError("USAGE", "pass --project or --output, not both")
        folder = eval_reports.directory(args.project, args.output)
        redact = _redactor(args.state_dir)
        if args.evals_command == "list":
            rows, total = eval_reports.summaries(folder, args.agent, redact)
            _list(args, rows, total)
        else:
            entry = eval_reports.find(folder, args.run, args.agent)
            report = eval_reports.load(entry, redact, content=args.content)
            _show(args, report)
    except ReportError as error:
        return _fail(args, error.code, str(error))
    except OSError as error:  # a backstop: every expected I/O error is a ReportError
        return _fail(args, "EVAL_REPORT_UNREADABLE", error.strerror or type(error).__name__)
    return 0


def _fail(args, code: str, message: str) -> int:
    message = safe_text.excerpt(message, 600)
    print(f"[{code}] {message}", file=sys.stderr)
    if args.json:
        print(json.dumps({"schema_version": 1, "error": {"code": code, "message": message}}))
    return 2


def _redactor(state_dir: Path):
    from agentbreeder import redaction

    found = redaction.collect(state_dir)
    if found.problems:
        raise ReportError(
            "REDACTION_INCOMPLETE",
            "nothing printed, because these values could not be read to redact the report: "
            + safe_text.excerpt("; ".join(sorted(set(found.problems))), 400),
        )
    return redaction.Redactor(found.values)


def _list(args, rows: list[dict], total: int) -> None:
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": eval_reports.SCHEMA_VERSION,
                    "reports": rows,
                    "total": total,
                    "truncated": total > len(rows),
                },
                indent=2,
            )
        )
        return
    if not rows:
        print("No eval reports.")
    for row in rows:
        if "error" in row:
            result = f"[{row['error']['code']}]"
        else:
            result = f"{row['passed']}/{row['cases']} passed  judge {row['judge']}"
        print(f"{row['run_id']}  {row['agent']}  {result}")
    if total > len(rows):
        print(f"WARNING: showing the newest {len(rows)} of {total} reports", file=sys.stderr)


def _show(args, report: eval_reports.Report) -> None:
    for note in report.notes:
        print(f"NOTE: {note}", file=sys.stderr)
    if args.json:
        print(json.dumps(report.as_json(args.content), indent=2))
        return
    top = report.header
    print(
        f"Run {top['run_id']}  agent {report.entry.agent}  eval set {top['eval_set']}  "
        f"judge {top['judge']}"
    )
    print(
        f"{top['passed']}/{top['cases']} passed ({top['pass_rate']:.1%}), mean correctness "
        f"{top['mean_correctness']}, p50 {top['p50_latency_ms']} ms"
    )
    print(f"Endpoint {top['endpoint']}")
    for row in report.results:
        mark = "FAIL" if not row["passed"] else ("FLKY" if row["attempts"] > 1 else "PASS")
        trace = f"  trace {row['trace_id']}" if row["trace_id"] else ""
        print(f"{mark} {row['id']}  {row['correctness']:.2f}  {row['latency_ms']} ms{trace}")
        if row["failed_checks"]:
            print("     failed: " + ", ".join(row["failed_checks"]))
        if row["reason"] and not row["passed"]:
            print(f"     reason: {row['reason']}")
        if args.content:
            for label in ("reply", "explanation"):
                text = safe_text.visible(row[label]).replace("\n", "\n       ")
                print(f"     {label}: {text}")
