"""SandboxBackend — the execution substrate behind CloudSandbox.

The backend deals in already-validated, workspace-relative paths; CloudSandbox
applies path containment + size/timeout caps before delegating here. This split
keeps the security boundary in one place (cloud.py) and lets us swap e2b for a
self-hosted Firecracker backend without touching callers.
"""

from __future__ import annotations

import builtins
from typing import Protocol, runtime_checkable

from engine.sandbox.base import ExecResult


@runtime_checkable
class SandboxBackend(Protocol):
    async def start(self) -> None: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def read_file(self, path: str) -> str: ...
    async def list_files(self, directory: str = ".") -> builtins.list[str]: ...
    async def run(self, cmd: builtins.list[str], timeout: float) -> ExecResult: ...
    async def snapshot(self) -> bytes: ...
    async def destroy(self) -> None: ...
