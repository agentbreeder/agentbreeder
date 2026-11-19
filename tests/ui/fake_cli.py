"""A fake `agentbreeder` CLI for the console UI tests: fixed JSON for every read the console makes,
and short jobs that print a few lines. The launcher passes it as the runner prefix, so the real
console server (from the installed wheel) runs it instead of the CLI. Nothing here touches Docker,
the network or a model.

A test changes the answers by writing `scenario.json` into AGENTBREEDER_FAKE_DIR (the launcher's
state directory) before the console first reads them: the console caches reads by argv.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import shutil
import sys
import threading
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
    # Seconds `audit list` waits before answering (a slow first read of releases, #1073).
    "audit_seconds": 0,
    # Seconds a project's `list` waits before answering (a slow first read of deployments, #1073).
    "list_seconds": 0,
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
    # `registry runtimes --json` (#1085 S1): null hands it to the real command (the built-in
    # `runtime/adk` versions and any on AGENTBREEDER_REGISTRY_PATH); a list is printed as its
    # `runtimes` (frameworks that have no runtime yet); {"error": {code, message}} fails it.
    "runtimes": None,
    # Seconds `registry runtimes` waits before answering (the Framework group's loading line).
    "runtimes_seconds": 0,
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
    # `... logs ... --follow --json` (M5 PR 2): the tail's generated lines, then one "live N" line
    # every `every` seconds until stdin ends. {"end": "container"} ends by itself after `count`
    # live lines; {"error": {code, message}} fails at once. Each argv is appended to
    # `follow-argv.jsonl` in the fake directory, and a line to `follow-ended.txt` when stdin ends.
    "follow": {},
    # `invoke NAME - --json` (Try it): the message is read from stdin. {"reply", "tools",
    # "sources", "trace_id", "seconds"} change the answer; {"error": {code, message, sent}} fails
    # it (exit 2, or 1 when sent). The reply defaults to an echo of the message. {"fields"} are
    # added to the runtime's answer (runtime/adk@1.3.0 and 1.4.0: fallbacks, served_by,
    # unknown_tools, cached_prompt_tokens, #1062); with {"status": N} other than 200 they are the
    # whole error answer instead (a MODEL_* code, exit 1). Every call's argv and stdin are
    # appended to `invoke-calls.json` in the fake directory.
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
    # to `traces-calls.json` in the fake directory. `traces ID ... --json` (an agent's Traces tab)
    # answers {"list": [trace, ...]} (each given the asked-for deployment ID unless it has one),
    # or two default traces; "error" fails it the same way. {"root": {...}} adds the root span's
    # facts to the `--trace` answer (fallbacks, served_by, cached_prompt_tokens, unknown_tools,
    # guardrail; runtime/adk@1.3.0 and 1.4.0, #1062).
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
    # `services plan --json`'s `grant.mcp` servers (#831 part 3): [{server, ref, origin, upstream,
    # auth, credentials, egress, agents}].
    "plan_mcp": [],
    # `logs --failed NAME --json` (the gate-failed view's saved candidate tail, #826): a list of
    # lines answers with them; null exits 1 (no tail saved); {"error": {code, message}} fails it.
    "failed_tail": None,
    # `knowledge status REF --json` (#830): {"state", "chunks", "notes", ...} change the one
    # version's answer; {"error": {code, message}} refuses it (exit 2). Every call's argv is
    # appended to `knowledge-calls.jsonl` in the fake directory.
    "knowledge_status": {},
    # `cost --json` (#906): keys of the answer to replace ("keys", "models", "total",
    # "truncated", ...); {"error": {code, message}} refuses it (exit 2). The window echoes the
    # argv's --since/--until. Every call's argv is appended to `cost-calls.jsonl`.
    "cost": {},
    # `services pins DIR --json` (#964): an answer is printed as is; {"error": {code, message}}
    # refuses it (exit 2); null refuses with MCP_RELAY_NOT_RUNNING. "seconds" delays the answer.
    "pins": None,
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


def _record(name: str, call) -> list:
    """Append `call` to the JSON list in `name` and return the calls recorded before it. Children
    run concurrently and the tests poll the file, so the read-modify-write holds a lock and the
    new list replaces the file whole: a reader never sees it empty or half written, and no call is
    lost (the console-ui flake of 2026-11-15, a JSONDecodeError in `calls`)."""
    directory = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", ""))
    path = directory / name
    with open(directory / f".{name}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recorded = json.loads(path.read_text()) if path.is_file() else []
        partial = directory / f".{name}.{os.getpid()}"
        partial.write_text(json.dumps([*recorded, call]))
        os.replace(partial, path)
    return recorded


def invoke(argv: list[str], data: dict) -> int:
    message = sys.stdin.buffer.read().decode("utf-8")
    recorded = _record("invoke-calls.json", {"argv": argv, "stdin": message})
    settings = data["invoke"]
    time.sleep(float(settings.get("seconds", 0)))
    if "error" in settings:
        problem = settings["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 1 if problem.get("sent") else 2
    status = settings.get("status", 200)
    if status != 200:  # the agent's error answer: `invoke --json` prints it whole, exit 1
        answer = {"schema_version": 1, "deployment": "d" * 32, "name": argv[1], "status": status}
        answer |= {"endpoint": "http://127.0.0.1:9001", "response": settings.get("fields", {})}
        print(json.dumps(answer, indent=2))
        print(
            f"[{settings['fields'].get('code', 'AGENT_ERROR')}] {argv[1]} answered",
            file=sys.stderr,
        )
        return 1
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
    response |= settings.get("fields", {})
    answer = {"schema_version": 1, "deployment": "d" * 32, "name": argv[1], "status": 200}
    answer |= {"endpoint": "http://127.0.0.1:9001", "response": response}
    print(json.dumps(answer, indent=2))
    return 0


def evals(argv: list[str], data: dict) -> int:
    _record("evals-calls.json", argv)
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
    recorded = _record("traces-calls.json", argv)
    settings = data["traces"]
    problem = settings.get("error")
    if problem is None and len(recorded) < settings.get("missing", 0):
        problem = {"code": "TRACES_NOT_FOUND", "message": "trace is not in Phoenix (yet)"}
    if problem:
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    if "--trace" not in argv:
        return traces_list(argv, settings)
    spans = settings.get("spans", TRACE_SPANS)
    # "arriving": N answers `--trace` with the trace's root not in yet (#1010) while fewer than N
    # `traces` calls (list reads included) were recorded before this one.
    arriving = len(recorded) < settings.get("arriving", 0)
    if arriving:
        spans = [span for span in spans if span["parent_id"] is not None]
    trace = {
        "trace_id": argv[argv.index("--trace") + 1],
        "project": "demo",
        "deployment_id": None if arriving else "d" * 32,
        "attribution": None if arriving else settings.get("attribution", "exact"),
        "incomplete": arriving,
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
    trace |= settings.get("root", {})
    answer = {"schema_version": 1, "phoenix": "http://127.0.0.1:6006", "traces": [trace]}
    answer |= {"overlapping": [], "truncated": False, "notes": settings.get("notes", [])}
    print(json.dumps(answer))
    return 0


def traces_list(argv: list[str], settings: dict) -> int:
    """`traces ID --stack DIR --environment ENV --last N --json`: an agent's Traces tab (#907)."""
    default = [
        {"trace_id": "ab" * 16, "attribution": "exact",
         "start_time": "2026-11-14T03:10:33.358423+00:00", "latency_ms": 41, "status": "error",
         "models": ["gemini-2.5-flash"],
         "tokens": {"prompt": 321, "completion": 70, "total": 391},
         "tool_calls": [{"name": "lookup_order", "status": "error", "latency_ms": 11,
                         "span_id": "c" * 16}],
         "spans": TRACE_SPANS, "spans_truncated": False},
        {"trace_id": "cd" * 16, "attribution": "exact", "start_time": "2026-11-14T03:09:00+00:00",
         "latency_ms": 1500, "status": "ok", "models": ["gemini-2.5-flash"],
         "tokens": {"prompt": 100, "completion": 20, "total": 120}, "tool_calls": [],
         "spans": TRACE_SPANS[:1], "spans_truncated": False},
    ]  # fmt: skip
    rows = [
        {"project": "demo", "session_id": "session-1", "end_time": None, "deployment_id": argv[1]}
        | row
        for row in settings.get("list", default)
    ]
    answer = {"schema_version": 1, "phoenix": "http://127.0.0.1:6006", "traces": rows}
    answer |= {"overlapping": settings.get("overlapping", []), "truncated": False}
    answer["notes"] = settings.get("notes", [])
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


COST_MODEL = {
    "model_group": "chat",
    "model": "gemini/gemini-2.5-flash",
    "calls": 7,
    "failed": 1,
    "prompt_tokens": 770,
    "completion_tokens": 1400,
    "cost_usd": 0.003108,
    "unpriced": 0,
}
COST_KEY = {
    "key_alias": "agentbreeder-" + "d1" * 16, "kind": "deployment", "id": "d1" * 16,
    "name": "demo", "target": "local", "state": "ready", "environment": "dev",
    **{k: v for k, v in COST_MODEL.items() if k not in ("model_group", "model")},
    "models": [COST_MODEL],
}  # fmt: skip


def cost(argv: list[str], data: dict) -> int:
    calls_file = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "cost-calls.jsonl"
    with calls_file.open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(argv) + "\n")
    settings = data["cost"]
    if "error" in settings:
        problem = settings["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    total = {k: v for k, v in COST_KEY.items() if k in COST_MODEL}
    answer = {
        "schema_version": 1,
        "since": argv[argv.index("--since") + 1],
        "until": argv[argv.index("--until") + 1],
        "gateway": "litellm",
        "currency": "USD",
        "source": "litellm spend logs",
        "truncated": False,
        "keys": [COST_KEY],
        "models": [COST_MODEL],
        "total": {k: v for k, v in total.items() if k not in ("model_group", "model")},
        **settings,
    }
    print(json.dumps(answer, indent=2))
    return 0


def knowledge_status(argv: list[str], data: dict) -> int:
    calls_file = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "knowledge-calls.jsonl"
    with calls_file.open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(argv) + "\n")
    settings = data["knowledge_status"]
    if "error" in settings:
        problem = settings["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2
    ref = argv[2]
    name, version = ref.split("/", 1)[1].split("@")
    version_answer = {
        "ref": ref,
        "table": f"kb_{name}_{version}".replace("-", "_").replace(".", "_"),
        "schema": "support_agent",
        "state": "fresh",
        "documents": {"expected": 5, "indexed": 5},
        "chunks": {"expected": 21, "indexed": 21, "fresh": 21, "stale": 0, "missing": 0,
                   "extra": 0},
        "updated": {"oldest": "2026-11-13T09:12:03Z", "newest": "2026-11-13T09:12:04Z"},
        "embedding": {"alias": "support-embedding", "dimensions": 768, "table_dimensions": 768},
        "notes": [],
    }  # fmt: skip
    version_answer.update({k: v for k, v in settings.items() if k != "error"})
    answer = {
        "schema_version": 1,
        "ref": ref,
        "environment": "dev",
        "database": "support",
        "credential": "agent",
        "versions": [version_answer],
    }
    print(json.dumps(answer, indent=2))
    return 0


def scenario() -> dict:
    path = Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "scenario.json"
    data = dict(DEFAULT)
    if path.is_file():
        data.update(json.loads(path.read_text()))
    return data


def service_control(argv: list[str], data: dict) -> int:
    """`services start|stop|restart DIR NAME --json [--yes]` (#822): scenario key
    `service_control` = {"in_use": [deployment, ...], "error": "[CODE] message", "seconds": N}.
    Each call's argv is appended to `service-argv` in the fake directory."""
    settings = data.get("service_control") or {}
    with open(Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "service-argv", "a") as log:
        log.write(json.dumps(argv) + "\n")
    time.sleep(settings.get("seconds", 0))
    action, name = argv[1], argv[3]
    if settings.get("error"):
        print(settings["error"], file=sys.stderr)
        return 2
    in_use = settings.get("in_use") or []
    if in_use and action != "start" and "--yes" not in argv:
        names = ", ".join(in_use)
        print(
            f"[SERVICE_IN_USE] these deployments use the stack: {names}; add --yes to {action} "
            f"{name} anyway",
            file=sys.stderr,
        )
        return 2
    stopped = action == "stop"
    answer = {
        "schema_version": 1,
        "stack": "0123456789ab",
        "service": name,
        "action": action,
        "state": "exited" if stopped else "running",
        "health": "" if stopped else "starting",
        "in_use": [] if action == "start" else in_use,
    }
    print(json.dumps(answer))
    return 0


MIB = 1024 * 1024


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _usage_rows(data: dict, n: int) -> list[dict]:
    """A row per running service, its numbers growing with each sample `n`."""
    rows = []
    for i, (name, info) in enumerate(sorted(data["services"].items())):
        if info.get("state") != "running":
            continue
        rows.append(
            {
                "source": "service",
                "name": name,
                "deployment": None,
                "cpu_percent": round(1.5 + i + (n % 3) * 0.5, 2),
                "memory_used": (64 + 8 * i + n) * MIB,
                "memory_limit": 8 * 1024 * MIB,
                "memory_percent": round((64 + 8 * i + n) / 81.92, 2),
                "network_rx": n * 2048 * (i + 1),
                "network_tx": n * 1024 * (i + 1),
                "block_read": n * 4096,
                "block_write": n * 512,
                "pids": 5 + i,
            }
        )
    return rows


def usage(argv: list[str], data: dict) -> int:
    """`services usage DIR --follow --json` (#822): samples until stdin ends, then the end record.
    Scenario key `usage` = {"error": "[CODE] message" (fail at once), "every": seconds between
    samples (default 1), "rows": fixed sample rows instead of the generated ones ("flap": true uses
    them only in every other sample), "disk": a disk
    record's fields, or null for none}. Each start's argv is appended to `usage-argv`."""
    settings = data.get("usage") or {}
    with open(Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "usage-argv", "a") as log:
        log.write(json.dumps(argv) + "\n")
    if settings.get("error"):
        print(settings["error"], file=sys.stderr)
        return 2
    done = threading.Event()

    def watch_stdin() -> None:
        sys.stdin.read()
        done.set()

    threading.Thread(target=watch_stdin, daemon=True).start()
    running = [n for n, i in sorted(data["services"].items()) if i.get("state") == "running"]
    disk = {
        "as_of": _now(),
        "stale": False,
        "volumes": [{"name": "agentbreeder-0123456789ab_pgdata", "size": 50 * MIB}],
        "containers": [
            {"source": "service", "name": name, "deployment": None, "size": 4096 * (k + 1)}
            for k, name in enumerate(running)
        ],
    }
    if "disk" in settings:
        disk = None if settings["disk"] is None else {**disk, **settings["disk"]}
    n = 0
    try:  # as the CLI: a reader that left (BrokenPipeError) stops it too
        if disk is not None:
            print(json.dumps({"schema_version": 1, "event": "disk", **disk}), flush=True)
        while not done.is_set():
            rows = settings.get("rows")
            sample = {
                "schema_version": 1,
                "event": "sample",
                "time": _now(),
                "containers": rows
                if rows is not None and not (settings.get("flap") and n % 2)
                else _usage_rows(data, n),
            }
            print(json.dumps(sample), flush=True)
            n += 1
            done.wait(settings.get("every", 1))
        print(json.dumps({"schema_version": 1, "event": "end", "reason": "stdin", "dropped": 0}))
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
    return 0


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
    `registry import` (#825), `agent set-ref` (#826) and `mcp grant|revoke` (#831) write only
    the project's files,
    `new agent --stack-for` writes only stack.yaml beside an agent.yaml, and `new agent
    --from-spec` (#833) only the new project's folder: the real commands."""
    from agentbreeder import cli

    return cli.main(argv)


def watch(argv: list[str], data: dict) -> int:
    """`watch --stack DIR --json --record` (#821): a status record, then the scenario's events
    one every `every` seconds (default 0.5), then waits for stdin to end and prints the end record.
    Scenario key `watch` = {"error": "[CODE] message" (fail at once), "events": the event records'
    fields, "every": seconds}. Each start's argv is appended to `watch-argv`."""
    settings = data.get("watch") or {}
    with open(Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "watch-argv", "a") as log:
        log.write(json.dumps(argv) + "\n")
    if settings.get("error"):
        print(settings["error"], file=sys.stderr)
        return 2
    done = threading.Event()

    def watch_stdin() -> None:
        sys.stdin.read()
        done.set()

    threading.Thread(target=watch_stdin, daemon=True).start()
    services = [
        {"name": name, "state": info.get("state") or None, "health": info.get("health") or None}
        for name, info in sorted(data["services"].items())
    ]
    try:
        status = {"event": "status", "time": _now(), "services": services, "deployments": []}
        print(json.dumps({"schema_version": 1, **status}), flush=True)
        for event in settings.get("events") or []:
            if done.wait(settings.get("every", 0.5)):
                break
            print(json.dumps({"schema_version": 1, "time": _now(), **event}), flush=True)
        done.wait()
        print(json.dumps({"schema_version": 1, "event": "end", "reason": "stdin", "dropped": 0}))
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
    return 0


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


def follow_logs(argv: list[str], data: dict) -> int:
    """`services logs DIR NAME --follow --json` / `logs ID ... --follow --json`, as the CLI: NDJSON
    lines, then the end record once stdin ends (the console's stop)."""
    import threading

    service = argv[:2] == ["services", "logs"]
    with (Path(os.environ["AGENTBREEDER_FAKE_DIR"]) / "follow-argv.jsonl").open("a") as log:
        log.write(json.dumps(argv) + "\n")
    if service:
        label = {"source": "service", "target": "local", "name": argv[3]}
    else:
        label = {"source": "agent", "target": "local", "name": "demo", "deployment": argv[1]}
    plan = {"every": 0.3, "count": 5, **data["follow"]}
    if "error" in plan:
        problem = plan["error"]
        print(json.dumps({"schema_version": 1, "error": problem}))
        print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
        return 2

    def say(text: str, level: str | None) -> None:
        print(json.dumps({"schema_version": 1, **label, "line": text, "level": level}), flush=True)

    done = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.read(), done.set()), daemon=True).start()
    tail = int(argv[argv.index("--tail") + 1])
    try:
        for n in range(1, min(tail, data["generated_lines"]) + 1):
            say(f"2026-11-13T10:00:00Z INFO: request {n} handled", "info")
        n = 0
        while not done.wait(plan["every"]):
            n += 1
            word = "ERROR" if n % 3 == 0 else "INFO"
            say(f"2026-11-13T10:00:01Z {word}: live {n}", word.lower())
            if plan.get("end") and n >= plan["count"]:
                break
        reason = "container" if plan.get("end") and not done.is_set() else "stdin"
        if reason == "stdin":  # the console closed stdin: a test sees the follow stopped
            with (Path(os.environ["AGENTBREEDER_FAKE_DIR"]) / "follow-ended.txt").open("a") as f:
                f.write("stdin\n")
        end = {"schema_version": 1, "event": "end", "reason": reason, "dropped": False}
        print(json.dumps(end), flush=True)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
    return 0


def logs(argv: list[str], data: dict) -> int:
    if "--failed" in argv:
        return failed_tail(argv, data)
    if "--follow" in argv:
        return follow_logs(argv, data)
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


def pins(data: dict) -> int:
    """`services pins DIR --json` (#964): the scenario's answer, or its refusal."""
    answer = data["pins"] or {"error": {"code": "MCP_RELAY_NOT_RUNNING", "message": "not running"}}
    time.sleep(float(answer.get("seconds", 0)))
    if "error" in answer:
        print(f"[{answer['error']['code']}] {answer['error']['message']}", file=sys.stderr)
        return 2
    print(json.dumps({key: value for key, value in answer.items() if key != "seconds"}))
    return 0


def main(argv: list[str]) -> int:
    data = scenario()
    head = argv[:2]
    if head == ["services", "plan"]:
        if data.get("plan_busy"):  # another services write holds the stack (#822 S-11)
            message = "another services operation is running on this stack; retry when it finishes"
            print(f"[SERVICES_BUSY] {message}", file=sys.stderr)
            return 2
        servers = data["plan_mcp"]  # the grant's `mcp` servers (#831)
        print(
            json.dumps(
                {
                    "digest": DIGEST,
                    "text": GRANT,
                    "keys": data["keys"],
                    "mcp": [item["server"] for item in servers],
                    "grant": {"mcp": servers},
                }
            )
        )
    elif head == ["services", "status"]:
        with open(Path(os.environ.get("AGENTBREEDER_FAKE_DIR", "")) / "status-calls", "a") as log:
            log.write("status\n")  # #822: Home's status re-reads are counted
        print(json.dumps({"services": data["services"], "mcp": data["mcp_status"]}))
    elif head == ["services", "env"]:
        print("export AGENTBREEDER_STATE_DIR=/nonexistent/fake-state")
    elif head == ["services", "logs"] or (argv and argv[0] == "logs"):
        return logs(argv, data)
    elif head == ["services", "up"]:
        print("argv " + json.dumps(argv[2:]), flush=True)  # the flags the console passed (#831)
        return job("services-up", data)
    elif head == ["services", "down"]:
        return job("services-down", data)
    elif head == ["services", "usage"]:
        return usage(argv, scenario())
    elif argv and argv[0] == "watch":
        return watch(argv, scenario())
    elif head in (["services", "start"], ["services", "stop"], ["services", "restart"]):
        return service_control(argv, data)
    elif head in (["registry", "show"], ["registry", "diff"]):
        return real(argv)
    elif head == ["registry", "used-by"]:
        return used_by(argv, data)
    elif head == ["registry", "verify"]:
        print(json.dumps(data["registry_problems"]))
        return 2 if data["registry_problems"] else 0
    elif head in (["registry", "edit"], ["registry", "fork"], ["agent", "set-ref"]):
        return real(argv)  # #826: the real edit and set-ref on the project's files
    elif head == ["registry", "import"]:
        return real(argv)  # #825: the real import into the project's registry
    elif head == ["registry", "runtimes"]:
        time.sleep(float(data.get("runtimes_seconds", 0)))
        if data["runtimes"] is None:
            return real(argv)
        if isinstance(data["runtimes"], dict):
            problem = data["runtimes"]["error"]
            print(f"[{problem['code']}] {problem['message']}", file=sys.stderr)
            return 2
        print(json.dumps({"schema_version": 1, "runtimes": data["runtimes"]}))
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
        time.sleep(float(data.get("list_seconds", 0)))
        print(json.dumps(data["deployments"]))
    elif head == ["audit", "list"]:
        time.sleep(float(data.get("audit_seconds", 0)))
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
    elif head == ["knowledge", "status"]:
        return knowledge_status(argv, data)
    elif argv and argv[0] == "cost":
        return cost(argv, data)
    elif head == ["services", "pins"]:
        return pins(data)
    elif argv and argv[0] == "doctor":
        checks = data["doctor"]
        if "--only" in argv:  # `--only docker`: the blocker's check
            checks = [check for check in checks if check["name"] == "Docker"]
        print(json.dumps(checks))
        if not all(check["passed"] or not check["required"] for check in checks):
            return 1  # as the real doctor: a required check failed
    elif argv and argv[0] in ("build", "release", "promote", "reconcile", "remove", "rollback"):
        return job(argv[0], data)
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
