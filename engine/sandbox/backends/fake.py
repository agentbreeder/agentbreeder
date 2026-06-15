"""In-memory SandboxBackend for unit tests — no network, no subprocess."""

from __future__ import annotations

import builtins
import io
import zipfile

from engine.sandbox.base import ExecResult


class FakeBackend:
    """Records lifecycle; stores files in a dict; run() returns a canned success."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.started = False
        self.destroyed = False
        self.commands: list[list[str]] = []

    async def start(self) -> None:
        self.started = True

    async def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    async def read_file(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def list_files(self, directory: str = ".") -> builtins.list[str]:
        prefix = "" if directory in (".", "") else f"{directory.rstrip('/')}/"
        return sorted(p for p in self.files if p.startswith(prefix))

    async def run(self, cmd: builtins.list[str], timeout: float) -> ExecResult:
        self.commands.append(cmd)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def snapshot(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in sorted(self.files.items()):
                zf.writestr(path, content)
        return buf.getvalue()

    async def destroy(self) -> None:
        self.destroyed = True
