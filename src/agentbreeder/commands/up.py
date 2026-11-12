"""`agentbreeder up`: start the local command center on 127.0.0.1 (single user, preview).

This process is the full CLI, which has imported the engine. After the Docker checks it replaces
itself (`os.execv`, same pid, same terminal) with `python -I -m agentbreeder.center`, so the
long-running server has none of those modules loaded and ignores `PYTHONPATH`, the user site and
the current directory. The server process takes the single-instance lock (`center/server.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

NAMES = ("up",)


def _port(text: str) -> int:
    import argparse

    value = int(text)
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("a port is 0-65535")
    return value


def register(commands) -> None:
    center = commands.add_parser(
        "up", help="Start the local command center on 127.0.0.1 (single user, preview)"
    )
    center.add_argument("dir", nargs="?", type=Path, help="A project with agent.yaml, stack.yaml")
    center.add_argument("--environment", default="dev")
    center.add_argument("--port", type=_port, default=None)
    center.add_argument("--no-browser", action="store_true")
    center.add_argument(
        "--yes", action="store_true", help="With DIR: confirm the grant and start its services"
    )


def server_argv(args) -> list[str]:
    """The isolated server's command line, rebuilt from the parsed arguments."""
    import agentbreeder
    from agentbreeder import __version__

    argv = [
        sys.executable,
        "-I",
        "-m",
        "agentbreeder.center",
        f"--expect-version={__version__}",
        f"--expect-package={Path(agentbreeder.__file__).resolve().parent}",
        f"--environment={args.environment}",
    ]
    if args.port is not None:
        argv += ["--port", str(args.port)]
    if args.no_browser:
        argv.append("--no-browser")
    if args.yes:
        argv.append("--yes")
    if args.dir is not None:  # after `--`, so a path cannot be read as an option
        argv += ["--", str(Path(args.dir).expanduser().absolute())]
    return argv


def _running_url() -> str | None | bool:
    """A cheap look before the Docker checks: the running console's URL (None while it starts
    or stops), or False when none runs. The server process takes the lock for real."""
    from agentbreeder.center.instance import AlreadyRunning, SingleInstance
    from agentbreeder.center.server import center_directory

    try:
        probe = SingleInstance(center_directory())
        probe.acquire()
    except AlreadyRunning as running:
        return running.url
    except OSError:
        return False  # the server process reports the problem
    probe.release_quietly()
    return False


def run(args) -> int:
    from agentbreeder.doctor import check_compose, check_docker

    url = _running_url()
    if url is not False:
        from agentbreeder.center.server import _already_running

        return _already_running(args, url)
    for check in check_docker() + check_compose():
        if not check.passed and check.required:
            print(f"FAIL {check.name}: {check.message}", file=sys.stderr)
    from agentbreeder.center.launch import exec_server

    return exec_server(server_argv(args))  # the terminal and pid carry over
