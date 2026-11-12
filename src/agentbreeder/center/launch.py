"""Replace the `agentbreeder up` process with the console server (`python -I -m
agentbreeder.center`).

This is the one place the console starts a process that keeps the terminal: an `exec` of the same
process, same pid, with stdin, stdout and stderr unchanged, so the Enter handler keeps its TTY and
the server prints the sign-in link where `up` was started. It runs no other program and prints
nothing from a child; everything else the console starts is a CLI child with captured output.
"""

from __future__ import annotations

import os
import sys


def exec_server(argv: list[str]) -> int:
    """Never returns on success. On failure (no interpreter), one line and exit code 2."""
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(argv[0], argv)
    except OSError as problem:
        print(f"Cannot start the console with {argv[0]}: {problem}", file=sys.stderr)
        return 2
    return 0  # pragma: no cover
