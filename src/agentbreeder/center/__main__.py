"""`python -I -m agentbreeder.center`: the console server process that `agentbreeder up` re-execs.

`up` starts in the full CLI process, which imports the engine (deployment state, Docker, the
operator config). It then replaces itself with this module under `python -I` (spec §8.1 #3), so
the long-running server never has those modules loaded, ignores `PYTHONPATH`, the user site and
the current directory, and keeps the terminal (stdin stays the TTY for the Enter handler).
Everything the server needs is imported here, eagerly, at start.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentbreeder.center import files, jobs, server  # noqa: F401  (eager imports)


def _port(text: str) -> int:
    value = int(text)
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("a port is 0-65535")
    return value


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agentbreeder up")
    parser.add_argument("dir", nargs="?", type=Path)
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--port", type=_port, default=None)
    parser.add_argument("--expect-version", default=None)
    parser.add_argument("--expect-package", default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    import agentbreeder
    from agentbreeder import __version__

    args = parse(argv)
    here = Path(agentbreeder.__file__).resolve().parent
    other_tree = args.expect_package is not None and Path(args.expect_package) != here
    if other_tree or (args.expect_version is not None and args.expect_version != __version__):
        # `-I` ignores PYTHONPATH and the current directory, so a checkout run with
        # PYTHONPATH=src hands over to whatever agentbreeder is installed: refuse a mismatch.
        print(
            f"`agentbreeder up` from {args.expect_package or args.expect_version} would hand "
            f"over to agentbreeder {__version__} installed at {here}, which is not the same "
            "code. Install the package you are running (`pip install -e .` in a checkout) and "
            "try again.",
            file=sys.stderr,
        )
        return 2
    return server.main(args)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
