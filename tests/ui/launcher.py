"""Start a real console server for the UI tests, with the fake CLI as its runner.

Run as `python -I tests/ui/launcher.py STATE_DIR`: `-I` drops the checkout from `sys.path`, so the
server is the installed `agentbreeder` (the wheel in CI). Prints one JSON line
`{"origin": ..., "login": ...}` (the one-time sign-in link) and serves until stdin closes.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import agentbreeder
from agentbreeder.center.server import Center, make_server

FAKE_CLI = Path(__file__).resolve().parent / "fake_cli.py"


def main(argv: list[str]) -> int:
    state = Path(argv[0])
    directory = state / "center"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    center = Center(directory, prefix=[sys.executable, "-I", str(FAKE_CLI)])
    server = make_server(center, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    login, _ = center.mint_login()
    info = {"origin": center.origin, "login": login, "package": agentbreeder.__file__}
    print(json.dumps(info), flush=True)
    sys.stdin.read()  # the test closes stdin to stop the server
    server.shutdown()
    server.server_close()
    center.runner.terminate_all()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
