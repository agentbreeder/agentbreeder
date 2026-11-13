"""A fake `agentbreeder` CLI for the console UI tests: fixed JSON for every read the console makes,
and short jobs that print a few lines. The launcher passes it as the runner prefix, so the real
console server (from the installed wheel) runs it instead of the CLI. Nothing here touches Docker,
the network or a model."""

from __future__ import annotations

import json
import sys
import time

DIGEST = "c" * 64
GRANT = "services:\n  postgres: postgres-pgvector@1.0.0\n  litellm: litellm@1.0.0\n"


def main(argv: list[str]) -> int:
    head = argv[:2]
    if head == ["services", "plan"]:
        keys = [{"name": "GOOGLE_API_KEY", "optional": False, "stored": False}]
        print(json.dumps({"digest": DIGEST, "text": GRANT, "keys": keys, "mcp": []}))
    elif head == ["services", "status"]:
        services = {
            "postgres": {"state": "running", "health": "healthy", "host_ports": {}},
            "litellm": {"state": "running", "health": "healthy", "host_ports": {"4000": 4000}},
        }
        print(json.dumps({"services": services}))
    elif head == ["services", "env"]:
        print("export AGENTBREEDER_STATE_DIR=/nonexistent/fake-state")
    elif head == ["registry", "verify"]:
        print("[]")
    elif argv and argv[0] == "registry":
        print("[]")
    elif argv and argv[0] == "list":
        print("[]")
    elif head == ["audit", "list"]:
        print("[]")
    elif head == ["audit", "verify"]:
        print(json.dumps({"ok": True, "rows": 0}))
    elif head == ["cluster", "status"]:
        tools = {
            name: {"present": True, "supported": True, "install": f"brew install {name}"}
            for name in ("kind", "kubectl")
        }
        status = {"tools": tools, "cluster": False, "registry": False, "configured": False}
        print(json.dumps(status))
    elif argv and argv[0] == "doctor":
        print("[]")
    elif argv and argv[0] in ("build", "release", "promote", "services", "cluster", "reconcile"):
        for step in ("Starting", "Working", "Done"):
            print(f"{argv[0]}: {step}", flush=True)
            time.sleep(0.2)
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
