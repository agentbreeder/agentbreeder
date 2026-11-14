"""A fake `agentbreeder` CLI for the console UI tests: fixed JSON for every read the console makes,
and short jobs that print a few lines. The launcher passes it as the runner prefix, so the real
console server (from the installed wheel) runs it instead of the CLI. Nothing here touches Docker,
the network or a model.

A test changes the answers by writing `scenario.json` into AGENTBREEDER_FAKE_DIR (the launcher's
state directory) before the console first reads them: the console caches reads by argv.
"""

from __future__ import annotations

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
}


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
    answer = {"schema_version": 1, "deployment": "d" * 32, "name": argv[1], "status": 200}
    answer |= {"endpoint": "http://127.0.0.1:9001", "response": response}
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
    """`registry show` and `registry diff` read only registry files: the real commands."""
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


def logs(argv: list[str], data: dict) -> int:
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
        print(json.dumps({"digest": DIGEST, "text": GRANT, "keys": data["keys"], "mcp": []}))
    elif head == ["services", "status"]:
        print(json.dumps({"services": data["services"]}))
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
    elif argv and argv[0] == "registry":
        print("[]")
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
    elif argv and argv[0] == "doctor":
        print(json.dumps(data["doctor"]))
    elif argv and argv[0] in ("build", "release", "promote", "reconcile", "remove", "rollback"):
        return job(argv[0], data)
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
