"""A fake `agentbreeder` CLI for the console UI tests: fixed JSON for every read the console makes,
and short jobs that print a few lines. The launcher passes it as the runner prefix, so the real
console server (from the installed wheel) runs it instead of the CLI. Nothing here touches Docker,
the network or a model.

A test changes the answers by writing `scenario.json` into AGENTBREEDER_FAKE_DIR (the launcher's
state directory) before the console first reads them: the console caches reads by argv.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

DIGEST = "c" * 64
GRANT = "services:\n  postgres: postgres-pgvector@1.0.0\n  litellm: litellm@1.0.0\n"
DEFAULT = {
    "services": {
        "postgres": {"state": "running", "health": "healthy", "host_ports": {}},
        "litellm": {"state": "running", "health": "healthy", "host_ports": {"4000": 4000}},
    },
    "keys": [{"name": "GOOGLE_API_KEY", "optional": False, "stored": False}],
    "deployments": [],
    "audit_rows": [],
    "audit_verify": {
        "ok": True,
        "rows": 0,
        "head": "0" * 64,
        "first_invalid_seq": None,
        "problem": None,
        "incomplete_attempts": [],
        "limitations": "The chain detects corruption; whoever owns the database can recompute it.",
    },
    "cluster": {
        "tools": {
            "kind": {"present": True, "supported": True, "install": "brew install kind"},
            "kubectl": {"present": True, "supported": True, "install": "brew install kubectl"},
        },
        "cluster": False,
        "registry": False,
        "configured": False,
    },
    "doctor": [
        {"name": "Docker", "passed": True, "message": "ok", "required": True, "info": False}
    ],
    "job_lines": {},
    "job_exit": {},
    "job_seconds": 0.6,
    # `next --json`: a file under fixtures/guide (the project path is filled in), or null for a
    # failed run (exit 2, a coded line).
    "guide": "services",
    # `services plan`: true fails it with SERVICES_BUSY (another services write holds the stack).
    "plan_busy": False,
    # `new agent`: null writes the demo project into --dir; a {code, message} refuses.
    "new_error": None,
    # `registry used-by`: the deployments shown for a project that uses the ref.
    "used_by_deployed": [],
    # `registry verify`: the problem rows ({ref, path, error}); it exits 2 when there are any.
    "registry_problems": [],
    # `services logs DIR NAME` / `logs ID`, by NAME or ID: {"lines", "levels", "format",
    # "truncated", "pod_reason"} replaces the generated answer; {"error": {code, message}} fails
    # it. Without an entry, `generated_lines` lines are made (every 100th an ERROR).
    "logs": {},
    "generated_lines": 5000,
    # `invoke NAME - --json` (Try it): the message is read from stdin. {"reply", "tools",
    # "sources", "trace_id", "seconds"} change the answer; {"error": {code, message, sent}} fails
    # it (exit 2, or 1 when sent). The reply defaults to an echo of the message. Every call's
    # argv and stdin are appended to `invoke-calls.json` in the fake directory.
    "invoke": {},
    # `list --all --json` (Agents, #823): {"deployments", "skipped"}; null lists `deployments`
    # as rows of a stack whose project is not registered.
    "list_all": None,
    # `evals list|show --json` (the eval report viewer, #932): {"report": the `show` answer,
    # "reports": the `list` rows, "error": {code, message}, "content_error": the same, only for
    # `show --content`}. `show --content` adds each case's
    # `reply` and `explanation`. Every call's argv is appended to `evals-calls.json`.
    "evals": {},
    # `traces --trace ID ... --json` (Try it's trace panel, #907): {"spans", "status",
    # "attribution", "notes"} change the answer; {"error": {code, message}} fails it (exit 2);
    # {"missing": N} answers TRACES_NOT_FOUND to the first N calls. Every call's argv is appended
    # to `traces-calls.json` in the fake directory.
    "traces": {},
    # `tool log REF ... --json` (a tool's execution log, #828): {"calls", "notes", "credential",
    # "truncated", "phoenix_url"} change the answer and "seconds" slows a read without
    # `--content`; {"error": {code, message}} refuses it (exit 2). `--content` adds each call's
    # `args` and `error`. Every call's argv is appended to `tool-log-calls.jsonl` in the fake
    # directory, one line per call (reads run concurrently, so no read-modify-write).
    "tool_log": {},
    # `services status --json`'s `mcp` part (the MCP screen, #831): {server: {state, pinned,
    # changed, missing, unpinned, error}}, as `Engine._mcp_status` answers it.
    "mcp_status": {},
    # `mcp grant|revoke --json` (#831): null runs the real command on the project's stack.yaml;
    # an object is printed as the answer instead (to show an `in_use` revoke).
    "mcp_grant": None,
    # ...and for the write only (`--expect-base`): the real write runs, then this is printed.
    "mcp_grant_write": None,
    # `logs --failed NAME --json` (the gate-failed view's saved candidate tail, #826): a list of
    # lines answers with them; null exits 1 (no tail saved); {"error": {code, message}} fails it.
    "failed_tail": None,
}

TOOL_CALLS = [
    {"at": "2026-11-13T16:02:11.123Z", "agent": "northwind-support", "tool": "tool/orders@1.0.0",
     "function": "lookup_orders", "status": "ok", "latency_ms": 41, "turn_id": "9f1c" * 8,
     "trace_id": "4bf9" * 8, "db_role": "support_agent",
     "args": {"order_id": "NW-10021"}, "error": None},
    {"at": "2026-11-13T16:01:02.004Z", "agent": None, "tool": None,
     "function": "lookup_orders", "status": "error", "latency_ms": None, "turn_id": "77aa" * 8,
     "trace_id": None, "db_role": "agentbreeder_console",
     "args": {"order_id": "NW-0"}, "error": "order not found"},
]  # fmt: skip

TRACE_SPANS = [
    {"span_id": "a" * 16, "parent_id": None, "name": "agent_turn", "kind": "AGENT",
     "status": "unset", "latency_ms": 41, "model": None, "tokens": None, "tool": None,
     "error": None},
    {"span_id": "b" * 16, "parent_id": "a" * 16, "name": "litellm.completion", "kind": "LLM",
     "status": "ok", "latency_ms": 20, "model": "gemini-2.5-flash",
     "tokens": {"prompt": 321, "completion": 70}, "tool": None, "error": None},
    {"span_id": "c" * 16, "parent_id": "a" * 16, "name": "lookup_order", "kind": "TOOL",
     "status": "error", "latency_ms": 11, "model": None, "tokens": None, "tool": "lookup_order",
     "error": "order not found"},
]  # fmt: skip


def invoke(argv: list[str], data: dict) -> int:
    message = sys.stdin.buffer.read().decode("utf-8")
    calls = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "invoke-calls.json"
    recorded = json.loads(calls.read_text()) if calls.is_file() else []
    calls.write_text(json.dumps([*recorded, {"argv": argv, "stdin": message}]))
    settings = data["invoke"]
    time.sleep(float(settings.get("seconds", 0)))
    if "error" in settings:
        problem = settings["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 1 if problem.get("sent") else 2
    session = argv[argv.index("--session") + 1] if "--session" in argv else "session-1"
    response = {
        "reply": settings.get("reply", f"You said: {message}"),
        "session_id": session,
        "customer_id": "anonymous",
        "model": "chat",
        "framework": "google-adk",
        "runtime": "runtime/adk@1.0.6",
        "turn_id": f"{len(recorded) + 1:032x}",
        "trace_id": settings.get("trace_id", "ab" * 16),
        "tools": settings.get("tools", ["orders"]),
        "sources": settings.get("sources", ["Returns policy"]),
        "model_calls": 2,
        "prompt_tokens": 1812,
        "output_tokens": 64,
    }
    if "cost_usd" in settings:  # runtime/adk@1.2.0 and later; older runtimes send no field
        response["cost_usd"] = settings["cost_usd"]
    answer = {"schema_version": 1, "deployment": "d" * 32, "name": argv[1], "status": 200}
    answer |= {"endpoint": "http://127.0.0.1:9001", "response": response}
    print(json.dumps(answer, indent=2))
    return 0


def evals(argv: list[str], data: dict) -> int:
    calls = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "evals-calls.json"
    previous = json.loads(calls.read_text()) if calls.is_file() else []
    calls.write_text(json.dumps([*previous, argv]))
    spec = data["evals"]
    problem = spec.get("error") or ("--content" in argv and spec.get("content_error"))
    if problem:
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    if argv[1] == "list":
        rows = spec.get("reports", [])
        print(json.dumps({"schema_version": 1, "reports": rows, "total": len(rows)}))
        return 0
    report = dict(spec["report"])
    if "--content" in argv:
        report["content"] = True
        report["results"] = [
            {"reply": f"Reply to {row['id']}", "explanation": f"Why {row['id']}", **row}
            for row in report["results"]
        ]
    print(json.dumps(report))
    return 0


def traces(argv: list[str], data: dict) -> int:
    calls = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "traces-calls.json"
    recorded = json.loads(calls.read_text()) if calls.is_file() else []
    calls.write_text(json.dumps([*recorded, argv]))
    settings = data["traces"]
    problem = settings.get("error")
    if problem is None and len(recorded) < settings.get("missing", 0):
        problem = {"code": "TRACES_NOT_FOUND", "message": "trace is not in Phoenix (yet)"}
    if problem:
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    spans = settings.get("spans", TRACE_SPANS)
    trace = {
        "trace_id": argv[argv.index("--trace") + 1],
        "project": "demo",
        "deployment_id": "d" * 32,
        "attribution": settings.get("attribution", "exact"),
        "latency_ms": 41,
        "status": settings.get("status", "error"),
        "session_id": "session-1",
        "models": ["gemini-2.5-flash"],
        "tokens": {"prompt": 321, "completion": 70, "total": 391},
        "tool_calls": [{"name": "lookup_order", "status": "error", "latency_ms": 11,
                        "span_id": "c" * 16}],
        "spans": spans,
        "spans_truncated": False,
    }  # fmt: skip
    answer = {"schema_version": 1, "phoenix": "http://127.0.0.1:6006", "traces": [trace]}
    answer |= {"overlapping": [], "truncated": False, "notes": settings.get("notes", [])}
    print(json.dumps(answer))
    return 0


def tool_log(argv: list[str], data: dict) -> int:
    calls_file = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "tool-log-calls.jsonl"
    with calls_file.open("a", encoding="utf-8") as calls:  # one O_APPEND write per call
        calls.write(json.dumps(argv) + "\n")
    settings = data["tool_log"]
    if "--content" not in argv:  # a slow plain read, for "Hide arguments" while one runs
        time.sleep(float(settings.get("seconds", 0)))
    if "error" in settings:
        problem = settings["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    content = "--content" in argv
    limit = int(argv[argv.index("--limit") + 1])
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv else None
    calls = [dict(call) for call in settings.get("calls", TOOL_CALLS)]
    if agent:
        calls = [call for call in calls if call["agent"] == agent]
    for call in calls:
        if not content:
            call.pop("args", None)
            call.pop("error", None)
    answer = {
        "schema_version": 1,
        "ref": argv[2],
        "environment": "dev",
        "database": "support",
        "credential": settings.get("credential", "console"),
        "schema": "public",
        "window": {"since": "2026-11-12T17:00:00Z", "until": "2026-11-13T17:00:00Z"},
        "limit": limit,
        "truncated": settings.get("truncated", len(calls) > limit),
        "content": content,
        "db_role_verified": True,
        "phoenix_url": settings.get("phoenix_url", "http://127.0.0.1:6006"),
        "phantom_rows": 0,
        "notes": settings.get("notes", []),
        "calls": calls[:limit],
    }
    print(json.dumps(answer, indent=2))
    return 0


def scenario() -> dict:
    path = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "scenario.json"
    data = dict(DEFAULT)
    if path.is_file():
        data.update(json.loads(path.read_text()))
    return data


def job(name: str, data: dict) -> int:
    lines = data["job_lines"].get(name) or [
        f"{name}: Starting",
        f"{name}: Working",
        f"{name}: Done",
    ]
    pause = data["job_seconds"] / max(len(lines), 1)
    try:
        for line in lines:
            print(line, flush=True)
            time.sleep(pause)
    except KeyboardInterrupt:
        print(f"{name}: interrupted", flush=True)
        return 130
    return int(data["job_exit"].get(name, 0))


def real(argv: list[str]) -> int:
    """`registry show` and `registry diff` read only registry files; `registry edit`,
    `agent set-ref` (#826) and `mcp grant|revoke` (#831) write only the project's files,
    `new agent --stack-for` writes only stack.yaml beside an agent.yaml, and `new agent
    --from-spec` (#833) only the new project's folder: the real commands."""
    from agentbreeder import cli

    return cli.main(argv)


def used_by(argv: list[str], data: dict) -> int:
    """`registry used-by`: the real declared and locked parts (`registry.usage`); deployed is
    the scenario's `used_by_deployed` for every project that declares or locks the ref."""
    from agentbreeder import registry

    ref = registry.Ref.parse(argv[2])
    projects = [Path(argv[i + 1]) for i, arg in enumerate(argv) if arg == "--project"]
    rows = []
    for project in projects:
        row = registry.usage(ref, project)
        used = row["declared"] or row["locked"]
        row["deployed"] = data["used_by_deployed"] if used else []
        row["recorded_not_running"], row["deployed_undetermined"] = [], []
        rows.append(row)
    print(json.dumps({"ref": str(ref), "projects": rows}))
    return 0


def failed_tail(argv: list[str], data: dict) -> int:
    name = argv[argv.index("--failed") + 1]
    answer = data["failed_tail"]
    if answer is None:
        print(f"No failed-candidate log for {name}: none failed yet", file=sys.stderr)
        return 1
    if isinstance(answer, dict):
        problem = answer["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    path = f"/nonexistent/fake-state/releases/{name}.candidate.log"
    print(json.dumps({"schema_version": 1, "agent": name, "path": path, "lines": answer}))
    return 0


def logs(argv: list[str], data: dict) -> int:
    if "--failed" in argv:
        return failed_tail(argv, data)
    service = argv[:2] == ["services", "logs"]
    name = argv[3] if service else argv[1]
    tail = int(argv[argv.index("--tail") + 1])
    answer = data["logs"].get(name, {})
    if "error" in answer:
        problem = answer["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    if "lines" not in answer:
        count = min(tail, data["generated_lines"])
        lines, levels = [], []
        for n in range(1, count + 1):
            word = "ERROR" if n % 100 == 0 else "WARNING" if n % 50 == 0 else "INFO"
            lines.append(f"2026-11-13T10:00:00Z {word}: request {n} handled")
            levels.append(word.lower())
        answer = {"lines": lines, "levels": levels, "format": "python", **answer}
    body = {"schema_version": 1, "target": "local", "format": None, "level": None}
    body |= {"truncated": False}
    if service:
        body |= {"source": "service", "name": name}
    else:
        body |= {
            "source": "agent",
            "deployment": name,
            "name": "demo",
            "previous": "--previous" in argv,
        }
        body |= {"pod_reason": None}
    body |= answer
    print(json.dumps(body))
    return 0


def main(argv: list[str]) -> int:
    data = scenario()
    head = argv[:2]
    if head == ["services", "plan"]:
        if data.get("plan_busy"):  # another services write holds the stack (#822 S-11)
            message = "another services operation is running on this stack; retry when it finishes"
            print(f"[SERVICES_BUSY] {message}", file=sys.stderr)
            return 2
        print(json.dumps({"digest": DIGEST, "text": GRANT, "keys": data["keys"], "mcp": []}))
    elif head == ["services", "status"]:
        print(json.dumps({"services": data["services"], "mcp": data["mcp_status"]}))
    elif head == ["services", "env"]:
        print("export AGENTBREEDER_STATE_DIR=/nonexistent/fake-state")
    elif head == ["services", "logs"] or (argv and argv[0] == "logs"):
        return logs(argv, data)
    elif head == ["services", "up"]:
        return job("services-up", data)
    elif head == ["services", "down"]:
        return job("services-down", data)
    elif head in (["registry", "show"], ["registry", "diff"]):
        return real(argv)
    elif head == ["registry", "used-by"]:
        return used_by(argv, data)
    elif head == ["registry", "verify"]:
        print(json.dumps(data["registry_problems"]))
        return 2 if data["registry_problems"] else 0
    elif head in (["registry", "edit"], ["registry", "fork"], ["agent", "set-ref"]):
        return real(argv)  # #826: the real edit and set-ref on the project's files
    elif argv and argv[0] == "registry":
        print("[]")
    elif argv[:2] == ["list", "--all"]:
        listing = data["list_all"] or {
            "deployments": [
                {**row, "stack": "demo-0123abcd", "project": None, "environment": "dev"}
                for row in data["deployments"]
            ],
            "skipped": [],
        }
        print(json.dumps({"schema_version": 1, **listing}))
    elif argv and argv[0] == "list":
        print(json.dumps(data["deployments"]))
    elif head == ["audit", "list"]:
        # As the CLI: newest first, `--before SEQ` keeps older rows, then `--limit`.
        rows = sorted(data["audit_rows"], key=lambda row: -row["seq"])
        if "--before" in argv:
            before = int(argv[argv.index("--before") + 1])
            rows = [row for row in rows if row["seq"] < before]
        limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 20
        print(json.dumps(rows[:limit]))
    elif head == ["audit", "verify"]:
        print(json.dumps(data["audit_verify"]))
    elif head == ["cluster", "status"]:
        print(json.dumps(data["cluster"]))
    elif head == ["cluster", "up"]:
        return job("cluster-up", data)
    elif head == ["cluster", "down"]:
        return job("cluster-down", data)
    elif argv and argv[0] == "next":
        if not data["guide"]:
            print("[NEXT_FAILED] The checklist could not be computed", file=sys.stderr)
            return 2
        text = (FIXTURES / "guide" / f"{data['guide']}.json").read_text()
        print(text.replace("@PROJECT@", argv[1]))
    elif head == ["new", "agent"] and any(arg.startswith("--stack-for=") for arg in argv):
        return real(argv)  # onboarding an agent.yaml alone (#823) only writes beside it
    elif head == ["new", "agent"] and any(arg.startswith("--from-spec=") for arg in argv):
        return real(argv)  # the agent builder (#833) writes only the new project's folder
    elif head in (["mcp", "grant"], ["mcp", "revoke"]):
        if "--expect-base" in argv and data["mcp_grant_write"] is not None:
            with contextlib.redirect_stdout(io.StringIO()):
                real(argv)  # the file changes; only the scripted answer is printed
            print(json.dumps(data["mcp_grant_write"]))
            return 0
        if data["mcp_grant"] is None:
            return real(argv)  # #831: the real grant and revoke on the project's stack.yaml
        print(json.dumps(data["mcp_grant"]))  # a scripted answer (an active deployment's in_use)
    elif head == ["new", "agent"]:
        if data["new_error"]:
            print(json.dumps({"schema_version": 1, "error": data["new_error"]}))
            print(f"[{data['new_error']['code']}] {data['new_error']['message']}", file=sys.stderr)
            return 2
        target = Path(argv[argv.index("--dir") + 1])
        shutil.copytree(FIXTURES / "projects" / "demo", target)
        summary = {"schema_version": 1, "dry_run": False, "name": argv[2]}
        summary["directory"] = str(target)
        print(json.dumps(summary))
    elif argv and argv[0] == "invoke":
        return invoke(argv, data)
    elif argv and argv[0] == "evals":
        return evals(argv, data)
    elif argv and argv[0] == "traces":
        return traces(argv, data)
    elif head == ["tool", "log"]:
        return tool_log(argv, data)
    elif argv and argv[0] == "doctor":
        print(json.dumps(data["doctor"]))
    elif argv and argv[0] in ("build", "release", "promote", "reconcile", "remove", "rollback"):
        return job(argv[0], data)
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
