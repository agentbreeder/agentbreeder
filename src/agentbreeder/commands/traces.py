"""`agentbreeder traces [ID | --trace TRACE_ID] --stack DIR [--environment ENV] [--last N]
[--since D] [--project NAME] [--json]`: recent agent traces from the stack's Phoenix (#907).

- `traces ID`: the newest traces of one deployment (from `list`).
- `traces`: the newest traces of every deployment in the state whose container ran and is not
  removed (not failed or awaiting approval; `--last` per deployment).
- `traces --trace TRACE_ID`: one trace (the `trace_id` a turn returns, e.g. in the console's Try
  it), searched in the Phoenix projects of this state's deployments, or in `--project NAME`.

Read-only: the deployment state is opened read-only and Phoenix is only read (see
`phoenix_traces` for the key, attribution and what is never shown). Strings are redacted with the
values `redaction.collect` finds; if one of them cannot be read, nothing is printed
(REDACTION_INCOMPLETE). Errors are `[CODE] message` on stderr (with `--json` also the one-line
`{"schema_version": 1, "error": {...}}` on stdout), exit 2.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from agentbreeder import cli, phoenix_traces, safe_text
from agentbreeder.phoenix_traces import TracesError

NAMES = ("traces",)


def _last(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= phoenix_traces.MAX_LAST:
        raise argparse.ArgumentTypeError(f"must be 1 to {phoenix_traces.MAX_LAST}")
    return value


def register(commands) -> None:
    command = cli.stateful_parser(
        commands,
        "traces",
        "Show recent agent traces per deployment from the stack's Phoenix (read-only)",
    )
    command.add_argument(
        "id", nargs="?", metavar="ID", help="Deployment ID (from `list`): its newest traces"
    )
    command.add_argument(
        "--trace", metavar="TRACE_ID", help="One trace (32 hex, as a turn returns it), as a tree"
    )
    cli.add_stack(command, environment=True)
    command.add_argument(
        "--last",
        type=_last,
        default=phoenix_traces.DEFAULT_LAST,
        metavar="N",
        help=f"The newest N traces per deployment (default {phoenix_traces.DEFAULT_LAST}, "
        f"at most {phoenix_traces.MAX_LAST})",
    )
    command.add_argument(
        "--since", metavar="D", help="Only traces started since D (2026-11-13 or an ISO time, UTC)"
    )
    command.add_argument(
        "--project", metavar="NAME", help="With --trace: the Phoenix project (the agent name)"
    )


def run(args) -> int:
    try:
        report = _report(args)
    except TracesError as error:
        return _fail(args, error.code, error)
    except (OSError, sqlite3.Error):
        return _fail(args, "STATE_UNREADABLE", "Cannot read local state; check permissions")
    if args.json:
        print(json.dumps(report.as_json(), indent=2))
    else:
        _print(report, bool(args.trace))
    for note in report.notes:
        print(f"NOTE: {note}", file=sys.stderr)
    if report.overlapping:
        print(
            "WARNING: window-attributed or unattributed traces may belong to any of these "
            "deployments (same Phoenix project, overlapping lifetimes, no deployment ID on their "
            "traces): " + ", ".join(report.overlapping),
            file=sys.stderr,
        )
    if report.truncated:
        print(
            "WARNING: Phoenix returned more than is read; the list is partial. "
            "Use a smaller --last or a later --since.",
            file=sys.stderr,
        )
    return 0


def _fail(args, code: str, error) -> int:
    message = str(error).removeprefix(f"[{code}] ")
    print(f"[{code}] {message}", file=sys.stderr)
    if args.json:
        print(json.dumps({"schema_version": 1, "error": {"code": code, "message": message}}))
    return 2


def _check(args) -> None:
    if args.id is not None and args.trace is not None:
        raise TracesError("USAGE", "pass a deployment ID or --trace, not both")
    if args.id is not None and not phoenix_traces.HEX32.fullmatch(args.id):
        raise TracesError(
            "ID_INVALID", "ID must be a deployment ID (32 hex characters, from `list`)"
        )
    if args.trace is not None and not phoenix_traces.HEX32.fullmatch(args.trace):
        raise TracesError("TRACE_ID_INVALID", "--trace must be a trace ID (32 hex characters)")
    if args.project is not None:
        if args.trace is None:
            raise TracesError("USAGE", "--project is for --trace")
        if not phoenix_traces.valid_project(args.project):
            raise TracesError("PROJECT_INVALID", "--project must be a Phoenix project name")


def _state(state_dir: Path):
    """(deployment lifetimes, redaction values); the state may be absent (no deployments)."""
    from agentbreeder import redaction
    from agentbreeder.store import DeploymentError, StateBusy, StateNeedsMigration, Store

    rows: list[dict] = []
    store = None
    if (state_dir / "deployments.sqlite3").is_file():
        try:
            store = Store.open_readonly(state_dir)
        except StateNeedsMigration as error:
            raise TracesError("STATE_NEEDS_MIGRATION", safe_text.excerpt(error, 400)) from None
        except StateBusy as error:
            raise TracesError("STATE_BUSY", safe_text.excerpt(error, 400)) from None
        except DeploymentError as error:
            raise TracesError("STATE_UNREADABLE", safe_text.excerpt(error, 400)) from None
    try:
        if store is not None:
            rows = store.lifetimes()
        found = redaction.collect(state_dir, store=store)
    finally:
        if store is not None:
            store.close()
    if found.problems:
        raise TracesError(
            "REDACTION_INCOMPLETE",
            "nothing printed, because these values could not be read to redact the traces: "
            + safe_text.excerpt("; ".join(sorted(set(found.problems))), 400),
        )
    return rows, found.values


def _report(args) -> phoenix_traces.Report:
    from agentbreeder import redaction

    _check(args)
    after = phoenix_traces.since(args.since)
    phoenix = phoenix_traces.locate(getattr(args, "stack", None), args.state_dir)
    rows, values = _state(args.state_dir)
    show = phoenix_traces.Shown(redaction.Redactor([*values, phoenix.key]))
    report = phoenix_traces.Report(phoenix.url)
    if args.trace is not None:
        if args.project is not None:
            projects = [args.project]
        else:
            known = phoenix.projects()
            projects = []
            for row in rows:
                project = phoenix_traces._project_for(row["name"], known)
                if project is not None and project not in projects:
                    projects.append(project)
        phoenix_traces.one_trace(phoenix, args.trace, projects, rows, show, report)
        return report
    if args.id is not None:
        chosen = [row for row in rows if row["id"] == args.id]
        if not chosen:
            raise TracesError("NOT_FOUND", f"no deployment {args.id} in this state")
    else:
        chosen = [row for row in rows if row["state"] in phoenix_traces.LISTED]
    known = phoenix.projects() if chosen else set()
    for deployment in chosen:
        phoenix_traces.for_deployment(
            phoenix, deployment, rows, known, show, args.last, after, report
        )
    return report


def _when(value: str | None) -> str:
    return value[:19].replace("T", " ") if value else "-"


def _ms(value: int | None) -> str:
    return "-" if value is None else f"{value} ms"


def _line(trace: dict) -> str:
    tokens = trace["tokens"]
    tools = ", ".join(
        call["name"] + ("(error)" if call["status"] == "error" else "")
        for call in trace["tool_calls"]
    )
    return "  ".join(
        part
        for part in (
            trace["trace_id"],
            _when(trace["start_time"]),
            _ms(trace["latency_ms"]),
            trace["status"],
            f"tokens {tokens['prompt']}/{tokens['completion']}",
            ", ".join(trace["models"]) or "-",
            f"tools: {tools}" if tools else "",
        )
        if part
    )


def _tree(trace: dict) -> list[str]:
    """Indented lines, depth first. Each span is printed once: a parent cycle (Phoenix data is
    untrusted) can't loop, and spans only reachable through one are printed as roots."""
    children: dict[str | None, list[dict]] = {}
    ids = {span["span_id"] for span in trace["spans"]}
    for span in trace["spans"]:
        parent = span["parent_id"] if span["parent_id"] in ids else None
        children.setdefault(parent, []).append(span)
    lines: list[str] = []
    seen: set[str] = set()
    for top in [*children.get(None, []), *trace["spans"]]:
        stack = [(top, 1)]
        while stack:
            span, depth = stack.pop()
            if span["span_id"] in seen:
                continue
            seen.add(span["span_id"])
            lines.append(_tree_line(span, depth))
            stack += [(c, depth + 1) for c in reversed(children.get(span["span_id"], []))]
    return lines


def _tree_line(span: dict, depth: int) -> str:
    detail = [span["kind"]]
    if span["model"]:
        detail.append(span["model"])
    if span["tokens"]:
        detail.append(f"{span['tokens']['prompt']}/{span['tokens']['completion']} tokens")
    text = f"{'  ' * depth}{span['name']} [{', '.join(detail)}] {_ms(span['latency_ms'])}"
    if span["status"] == "error":
        text += " ERROR" + (f": {span['error']}" if span["error"] else "")
    return text


def _print(report: phoenix_traces.Report, tree: bool) -> None:
    print(f"Traces from Phoenix {report.phoenix} (UTC, newest first)")
    if not report.traces:
        print("No traces found.")
        return
    current = object()
    for trace in report.traces:
        if not tree and trace["deployment_id"] != current:
            current = trace["deployment_id"]
            how = f" ({trace['attribution']})" if trace["attribution"] else ""
            print(f"Deployment {current or '(unknown)'}{how}, project {trace['project']}:")
        print(("" if tree else "  ") + _line(trace))
        if tree:
            print(
                f"  project {trace['project']}, deployment {trace['deployment_id'] or '-'}"
                + (f" ({trace['attribution']})" if trace["attribution"] else "")
                + f", session {trace['session_id'] or '-'}, turn {trace['turn_id'] or '-'}"
            )
            for line in _tree(trace):
                print(line)
            if trace["spans_truncated"]:
                print(f"  (only the first {phoenix_traces.MAX_SPANS} spans are shown)")
