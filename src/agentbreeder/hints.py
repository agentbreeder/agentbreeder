"""The one place a `Next:` line is written (guided §9.2 #2, #9).

Every command that suggests what to run next goes through `next_line`, so the suggestion uses
the launcher the user is running (`launcher.prefix`) and always carries `--stack DIR
--environment ENV` when the command it follows used a stack. Callers print it only in text mode:
a `Next:` line never goes to stdout with `--json`.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from agentbreeder import launcher

_prefix = launcher.prefix  # tests pin this


def command(*argv: str | Path, stack: Path | None = None, environment: str | None = None) -> str:
    """`<launcher> ARGV [--stack DIR] [--environment ENV]`, each word shell-quoted."""
    words = [str(word) for word in argv]
    if stack is not None:
        words += ["--stack", str(stack)]
    if environment:
        words += ["--environment", environment]
    return " ".join([_prefix(), *(shlex.quote(word) for word in words)])


def next_line(*argv: str | Path, stack: Path | None = None, environment: str | None = None) -> str:
    return f"Next: {command(*argv, stack=stack, environment=environment)}"


def next_note(text: str) -> str:
    """A `Next:` step that is not a command (e.g. read a file)."""
    return f"Next: {text}"
