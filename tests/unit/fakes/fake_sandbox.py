"""In-memory Sandbox double for engine + service tests (no subprocess)."""

from __future__ import annotations

import builtins

from engine.sandbox.base import ExecResult


class FakeSandbox:
    """Deterministic in-memory sandbox. exec() returns scripted results."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.exec_calls: builtins.list[builtins.list[str]] = []
        self.exec_results: dict[str, ExecResult] = {}
        self.closed = False

    async def write(self, path: str, content: str) -> None:
        self.files[path] = content

    async def read(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def list(self, directory: str = ".") -> builtins.list[str]:
        return sorted(self.files)

    async def exec(self, cmd: builtins.list[str], timeout: float = 30.0) -> ExecResult:
        self.exec_calls.append(cmd)
        key = " ".join(cmd)
        return self.exec_results.get(key, ExecResult(stdout="", stderr="", exit_code=0))

    async def snapshot(self) -> bytes:
        return repr(self.files).encode("utf-8")

    async def close(self) -> None:
        self.closed = True
