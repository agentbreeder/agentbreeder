"""How the user launched AgentBreeder, so printed commands can be pasted back (guided §9.2 #9).

The two ephemeral launchers are checked first: uvx may put its temporary environment on the
child's PATH, where a bare `agentbreeder` would resolve now but not in the user's next shell.

- `npx agentbreeder@<ver>` when the npm launcher set `AGENTBREEDER_LAUNCHER=npx` and
  `AGENTBREEDER_LAUNCHER_VERSION` (an enum value and a strict semver; anything else is ignored,
  so the environment cannot inject text into a printed command);
- `uvx --from 'agentbreeder[eval]==<ver>' agentbreeder` when this interpreter's prefix is in uv's
  cache (an ephemeral `uvx` environment);
- bare `agentbreeder` when the `agentbreeder` on PATH runs this interpreter;
- otherwise `python -m agentbreeder` (this interpreter's path when `python` is another one).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

LAUNCHER_ENV = "AGENTBREEDER_LAUNCHER"
VERSION_ENV = "AGENTBREEDER_LAUNCHER_VERSION"
LAUNCHERS = ("npx",)
_NUMBER = r"(?:0|[1-9][0-9]{0,8})"
SEMVER = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-(?:alpha|beta|rc)\.{_NUMBER})?")


def _same_path(a: str | Path, b: str | Path) -> bool:
    """The same path, not merely the same file: a venv's `python` is a symlink to the base
    interpreter but runs with another `sys.prefix`."""
    return os.path.abspath(a) == os.path.abspath(b)


def _runs_this_interpreter(script: str, executable: str) -> bool:
    """The console script is this install's: its shebang names this interpreter, or it sits in
    this interpreter's bin directory (followed through a symlink such as `uv tool`'s)."""
    try:
        real = Path(script).resolve()
        if real.parent == Path(executable).parent.resolve():
            return True
        with open(real, "rb") as handle:
            first = handle.readline(4096)
    except OSError:
        return False
    if not first.startswith(b"#!"):
        return False
    words = first[2:].decode("utf-8", "replace").split()
    return bool(words) and _same_path(words[0], executable)


def _uv_cache(env: Mapping[str, str]) -> Path:
    value = env.get("UV_CACHE_DIR")
    if value:
        return Path(value).expanduser()
    base = env.get("XDG_CACHE_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".cache") / "uv"


def _version() -> str:
    from agentbreeder import __version__

    return __version__


def prefix(
    env: Mapping[str, str] | None = None,
    *,
    executable: str | None = None,
    sys_prefix: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    version: str | None = None,
) -> str:
    """The command prefix to print in front of a subcommand, shell-quoted."""
    env = os.environ if env is None else env
    executable = executable or sys.executable
    sys_prefix = sys_prefix or sys.prefix
    launcher_version = env.get(VERSION_ENV, "")
    if env.get(LAUNCHER_ENV) in LAUNCHERS and SEMVER.fullmatch(launcher_version):
        return f"npx agentbreeder@{launcher_version}"
    try:
        in_cache = Path(sys_prefix).resolve().is_relative_to(_uv_cache(env).resolve())
    except (OSError, RuntimeError):
        in_cache = False
    if in_cache:
        package = shlex.quote(f"agentbreeder[eval]=={version or _version()}")
        return f"uvx --from {package} agentbreeder"
    found = which("agentbreeder")
    if found and _runs_this_interpreter(found, executable):
        return "agentbreeder"
    python = which("python")
    if python and _same_path(python, executable):
        return "python -m agentbreeder"
    return f"{shlex.quote(executable)} -m agentbreeder"
