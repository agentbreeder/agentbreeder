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
}


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


def main(argv: list[str]) -> int:
    data = scenario()
    head = argv[:2]
    if head == ["services", "plan"]:
        print(json.dumps({"digest": DIGEST, "text": GRANT, "keys": data["keys"], "mcp": []}))
    elif head == ["services", "status"]:
        print(json.dumps({"services": data["services"]}))
    elif head == ["services", "env"]:
        print("export AGENTBREEDER_STATE_DIR=/nonexistent/fake-state")
    elif head == ["services", "up"]:
        return job("services-up", data)
    elif head == ["services", "down"]:
        return job("services-down", data)
    elif argv and argv[0] == "registry":
        print("[]")
    elif argv and argv[0] == "list":
        print(json.dumps(data["deployments"]))
    elif head == ["audit", "list"]:
        print(json.dumps(data["audit_rows"]))
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
    elif argv and argv[0] == "doctor":
        print(json.dumps(data["doctor"]))
    elif argv and argv[0] in ("build", "release", "promote", "reconcile", "remove", "rollback"):
        return job(argv[0], data)
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
