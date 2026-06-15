"""CloudSandbox — Sandbox Protocol over a managed microVM backend (Wave 4).

SECURITY (design §11.2): this is the single place client-side guards live —
path containment + per-file/byte and exec-timeout caps — applied BEFORE the
backend runs anything. The microVM is defense-in-depth, not a reason to drop
these checks. No long-lived secrets are placed in the workspace.
"""

from __future__ import annotations

import builtins
import time
from pathlib import PurePosixPath

from engine.sandbox.backends.base import SandboxBackend
from engine.sandbox.base import ExecResult

_MAX_EXEC_TIMEOUT = 120.0
_MAX_FILE_BYTES = 1_000_000  # 1 MB per file cap (parity with LocalSandbox)


def _safe_relpath(path: str) -> str:
    """Return a normalized workspace-relative path or raise on escape."""
    if path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    parts: list[str] = []
    for part in PurePosixPath(path).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"path escapes the sandbox workspace: {path!r}")
        parts.append(part)
    return "/".join(parts)


class CloudSandbox:
    """Sandbox backed by a managed microVM (via a pluggable SandboxBackend)."""

    def __init__(self, backend: SandboxBackend) -> None:
        self._backend = backend
        self._started = False
        self._t0: float | None = None
        self.elapsed_seconds: float = 0.0

    async def _ensure_started(self) -> None:
        if not self._started:
            await self._backend.start()
            self._started = True
            self._t0 = time.monotonic()

    async def write(self, path: str, content: str) -> None:
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {_MAX_FILE_BYTES} byte cap: {path!r}")
        await self._ensure_started()
        await self._backend.write_file(_safe_relpath(path), content)

    async def read(self, path: str) -> str:
        await self._ensure_started()
        return await self._backend.read_file(_safe_relpath(path))

    async def list(self, directory: str = ".") -> builtins.list[str]:
        await self._ensure_started()
        norm = _safe_relpath(directory) if directory not in (".", "") else "."
        return await self._backend.list_files(norm)

    async def exec(self, cmd: builtins.list[str], timeout: float = 30.0) -> ExecResult:
        await self._ensure_started()
        return await self._backend.run(cmd, min(timeout, _MAX_EXEC_TIMEOUT))

    async def snapshot(self) -> bytes:
        await self._ensure_started()
        return await self._backend.snapshot()

    async def close(self) -> None:
        if self._t0 is not None:
            self.elapsed_seconds = time.monotonic() - self._t0
        await self._backend.destroy()
