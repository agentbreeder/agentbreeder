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
import sys
import time
from pathlib import Path

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
    elif argv and argv[0] == "doctor":
        print(json.dumps(data["doctor"]))
    elif argv and argv[0] in ("build", "release", "promote", "reconcile", "remove", "rollback"):
        return job(argv[0], data)
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
