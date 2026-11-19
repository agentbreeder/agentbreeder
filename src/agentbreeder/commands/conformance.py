"""`agentbreeder conformance IMAGE`: run the agent protocol checks against a built image.

Exit 0 when every check that applies passed, 1 when one failed or did not run, 2 when the run
could not start (a coded refusal). See docs/commands/conformance.md and docs/agent-protocol.md.
"""

from __future__ import annotations

import json
import signal
from pathlib import Path

from agentbreeder import cli

NAMES = ("conformance",)


def register(commands) -> None:
    command = commands.add_parser(
        "conformance",
        help="Check a built agent image against agent protocol v1 (local Docker, preview)",
    )
    command.add_argument(
        "image", metavar="IMAGE", help="The image ID `agentbreeder build` printed (sha256:…)"
    )
    command.add_argument(
        "--project",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="The agent's project, whose deploy/ holds the image's build record (default: .)",
    )
    command.add_argument("--json", action="store_true")


def run(args) -> int:
    from agentbreeder.conformance import checks, environment, report

    def stop(number, frame):
        raise SystemExit(128 + number)  # unwinds through the environment, which removes itself

    previous = {}
    for number in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[number] = signal.signal(number, stop)
        except ValueError:  # not the main thread (the console runs commands as processes)
            pass
    try:
        result = environment.run(args.image, args.project)
    except environment.ConformanceError as error:
        return cli.fail(args, error.code, str(error))
    except OSError as error:  # the run's files under ~/.agentbreeder/conformance/.runs
        return cli.fail(
            args,
            "CONFORMANCE_ENVIRONMENT",
            f"the run could not prepare its files ({type(error).__name__}); check the owner "
            "and permissions of ~/.agentbreeder/conformance",
        )
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    try:
        path = report.write(result)
    except (OSError, ValueError):
        return cli.fail(
            args,
            "CONFORMANCE_REPORT",
            "the checks ran, but the report could not be written under ~/.agentbreeder/"
            "conformance; check that directory's owner and permissions",
        )
    if args.json:
        print(json.dumps({**result, "report_path": str(path)}, indent=2))
    else:
        print(report.text(result))
        print(f"Report: {path}")
    return 0 if result["result"] == checks.PASS else 1
