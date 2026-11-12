"""`agentbreeder logs --failed NAME`: the saved log tail of the last failed release candidate.

`release` saves the candidate's last 500 lines (redacted) before removing it, and discards them
when the gate passes. This reads that file only: no Docker, no identity provider, and the
deployment database is not opened. Controls in the text (ESC, CSI and OSC sequences, C1) are
shown as visible escapes unless `--raw` (security finding S-08); `--json` escapes by
construction. (`logs ID` for running deployments comes in a later milestone.)
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

from agentbreeder import cli, safe_text
from agentbreeder.agentfile import NAME_PATTERN

NAMES = ("logs",)
READ_LIMIT = 8 * 1024 * 1024


def register(commands) -> None:
    logs = cli.stateful_parser(
        commands, "logs", "Show the saved log tail of the last failed release candidate"
    )
    logs.add_argument(
        "--failed",
        required=True,
        metavar="NAME",
        help="Agent name: show its last failed release candidate's log tail (redacted)",
    )
    logs.add_argument(
        "--raw", action="store_true", help="Print control characters as they are (unsafe)"
    )
    cli.add_stack(logs, environment=True)


def _read(path: Path) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"{path} must be a regular file") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError(f"{path} must be a 0600 file you own")
        return handle.read(READ_LIMIT).decode("utf-8", "replace")


def run(args) -> int:
    from agentbreeder.release import candidate_tail_path

    name = args.failed
    if not re.fullmatch(NAME_PATTERN, name):
        print("[AGENT_NAME] NAME must be an agent name from agent.yaml", file=sys.stderr)
        return 2
    path = candidate_tail_path(args.state_dir.absolute(), name)
    try:
        text = _read(path)
    except ValueError as error:
        print(f"[STATE_UNSAFE] {error}", file=sys.stderr)
        return 2
    if text is None:
        print(
            f"No failed-candidate log for {name} in {args.state_dir}: none failed yet, or the "
            "last release passed (a pass discards it)",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps({"agent": name, "path": str(path), "lines": text.splitlines()}, indent=2))
    elif args.raw:
        sys.stdout.write(text)
    else:
        sys.stdout.write(safe_text.visible(text))
    return 0
