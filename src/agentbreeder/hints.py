"""The one place a `Next:` line is written (guided §9.2 #2, #9).

Every command that suggests what to run next goes through `next_line`, so the suggestion uses
the launcher the user is running (`launcher.prefix`) and always carries `--stack DIR
--environment ENV` when the command it follows used a stack. Callers print it only in text mode:
a `Next:` line never goes to stdout with `--json`.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from agentbreeder import launcher

_prefix = launcher.prefix  # tests pin this


def command(
    *argv: str | Path,
    stack: Path | None = None,
    environment: str | None = None,
    state_dir: Path | None = None,
) -> str:
    """`<launcher> ARGV [--stack DIR] [--environment ENV] [--state-dir DIR]`, shell-quoted."""
    words = [str(word) for word in argv]
    if stack is not None:
        words += ["--stack", str(stack)]
    if environment:
        words += ["--environment", environment]
    if state_dir is not None:
        words += ["--state-dir", str(state_dir)]
    return " ".join([_prefix(), *(shlex.quote(word) for word in words)])


def next_line(*argv: str | Path, stack: Path | None = None, environment: str | None = None) -> str:
    return f"Next: {command(*argv, stack=stack, environment=environment)}"


def next_note(text: str) -> str:
    """A `Next:` step that is not a command (e.g. read a file)."""
    return f"Next: {text}"


# Which of --stack/--environment/--state-dir each suggested command accepts without --stack
# (with --stack, all take --stack DIR --environment ENV).
_WITHOUT_STACK = {
    "build": ("environment",),
    "remove": ("state_dir",),
    "logs": ("state_dir",),
    "release": ("environment", "state_dir"),
    "deploy": ("environment", "state_dir"),
}


@dataclass(frozen=True)
class Where:
    """Where the current command ran: printed follow-ups repeat it so they work as printed.
    `state_dir` is set only when it is not the default (and never together with `stack`)."""

    stack: Path | None = None
    environment: str | None = None
    state_dir: Path | None = None

    def flags(self, subcommand: str) -> dict:
        if self.stack is not None:
            return {"stack": self.stack, "environment": self.environment}
        wanted = _WITHOUT_STACK.get(subcommand, ())
        return {
            "environment": self.environment if "environment" in wanted else None,
            "state_dir": self.state_dir if "state_dir" in wanted else None,
        }

    def command(self, *argv: str | Path) -> str:
        return command(*argv, **self.flags(str(argv[0])))
