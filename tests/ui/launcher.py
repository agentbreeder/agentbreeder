"""Start a real console server for the UI tests, with the fake CLI as its runner.

Run as `python -I tests/ui/launcher.py STATE_DIR [--first-run]`: `-I` drops the checkout from
`sys.path`, so the server is the installed `agentbreeder` (the wheel in CI). Prints one JSON line
`{"origin": ..., "login": ..., "password": ...}` (the one-time sign-in link, and the console
password the launcher set, or null with `--first-run`) and serves until stdin closes.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import agentbreeder
from agentbreeder.center import auth
from agentbreeder.center.jobs import CenterState
from agentbreeder.center.server import Center, make_server

FAKE_CLI = Path(__file__).resolve().parent / "fake_cli.py"
# Set before start unless --first-run, so specs that open the one-time link land on Home as
# before M2 (#818); the sign-in specs use it to sign in with the form.
PASSWORD = "ui test password 1"


def main(argv: list[str]) -> int:
    state = Path(argv[0])
    directory = state / "center"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    first_run = "--first-run" in argv[1:]
    center, server = start(directory, 0)
    if not first_run:
        center.state.set_password(
            auth.EMAIL, auth.hash_password(PASSWORD), "password-set", "terminal"
        )
    login, _ = center.mint_login()
    info = {
        "origin": center.origin,
        "login": login,
        "password": None if first_run else PASSWORD,
        "package": agentbreeder.__file__,
    }
    print(json.dumps(info), flush=True)
    # Each "mint" line on stdin prints a new one-time link (as Enter does in `up`); "restart"
    # stops the server and starts a new one on the same port, with no sessions (as stopping and
    # starting `up` does); "reset NEW" replaces the password as `up --reset-password` does from
    # another process (center.sqlite3 only); closing stdin stops the server.
    for line in sys.stdin:
        if line.startswith("reset "):
            fresh = CenterState(directory)  # its own connection, as another process has
            fresh.set_password(
                auth.EMAIL, auth.hash_password(line[6:].rstrip("\n")), "password-reset", "terminal"
            )
            fresh.db.close()
            print(json.dumps({"reset": True}), flush=True)
        elif line.strip() == "mint":
            print(json.dumps({"login": center.mint_login()[0]}), flush=True)
        elif line.strip() == "restart":
            port = center.port
            stop(center, server)
            center, server = start(directory, port)
            print(json.dumps({"origin": center.origin}), flush=True)
    stop(center, server)
    return 0


def start(directory: Path, port: int) -> tuple[Center, object]:
    center = Center(directory, prefix=[sys.executable, "-I", str(FAKE_CLI)])
    server = make_server(center, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return center, server


def stop(center: Center, server) -> None:
    server.shutdown()
    server.server_close()
    center.runner.terminate_all()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
