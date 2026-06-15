"""Sandbox interface — a pluggable scaffold workspace for the coding agent.

LocalSandbox runs on the host (local single-user Studio only). CloudSandbox
(Wave 4) runs in a managed microVM. The AGENTBREEDER_SANDBOX env gate decides
which the server is permitted to construct so the multi-tenant cloud can never
run user code in-process.
"""

from __future__ import annotations

import builtins
import os
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SandboxMode = Literal["local", "cloud", "disabled"]
_VALID_MODES: frozenset[str] = frozenset({"local", "cloud", "disabled"})


class SandboxDisabledError(RuntimeError):
    """Raised when a sandbox is requested but disallowed by configuration."""


@dataclass
class ExecResult:
    """Result of running a command inside a sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def select_sandbox_mode() -> SandboxMode:
    """Return the configured sandbox mode (defaults to ``local``).

    Raises SandboxDisabledError on an unrecognised value rather than silently
    falling back, so a misconfigured cloud deploy fails closed.
    """
    raw = os.environ.get("AGENTBREEDER_SANDBOX", "local").strip().lower()
    if raw not in _VALID_MODES:
        raise SandboxDisabledError(
            f"AGENTBREEDER_SANDBOX={raw!r} is not one of {sorted(_VALID_MODES)}"
        )
    return raw  # type: ignore[return-value]


@runtime_checkable
class Sandbox(Protocol):
    """A scaffold workspace the coding agent reads from and writes into."""

    async def write(self, path: str, content: str) -> None: ...
    async def read(self, path: str) -> str: ...
    async def list(self, directory: str = ".") -> builtins.list[str]: ...
    async def exec(self, cmd: builtins.list[str], timeout: float = 30.0) -> ExecResult: ...
    async def snapshot(self) -> bytes: ...
    async def close(self) -> None: ...
