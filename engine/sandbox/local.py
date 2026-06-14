"""LocalSandbox — temp-dir + subprocess scaffold workspace for local Studio.

SECURITY: runs commands on the host. Only constructed when AGENTBREEDER_SANDBOX
is 'local'. The server guards construction (see api/services/builder_session_service).
All paths are contained within the workspace root; absolute paths and '..' escapes
are rejected.
"""

from __future__ import annotations

import asyncio
import builtins
import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from engine.sandbox.base import ExecResult

logger = logging.getLogger(__name__)

_MAX_EXEC_TIMEOUT = 120.0
_MAX_FILE_BYTES = 1_000_000  # 1 MB per file cap


class LocalSandbox:
    """In-process sandbox backed by a temporary directory."""

    def __init__(self, prefix: str = "agentbreeder-builder-") -> None:
        self.root: Path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()

    def _resolve(self, path: str) -> Path:
        """Resolve ``path`` strictly inside the workspace root."""
        if path.startswith("/") or path.startswith("\\"):
            raise ValueError(f"absolute paths are not allowed: {path!r}")
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"path escapes the sandbox workspace: {path!r}")
        return candidate

    async def write(self, path: str, content: str) -> None:
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {_MAX_FILE_BYTES} byte cap: {path!r}")
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return target.read_text(encoding="utf-8")

    async def list(self, directory: str = ".") -> builtins.list[str]:
        base = self._resolve(directory)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    async def exec(  # pragma: no cover - exercised in Task A3
        self, cmd: builtins.list[str], timeout: float = 30.0
    ) -> ExecResult:
        timeout = min(timeout, _MAX_EXEC_TIMEOUT)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return ExecResult(stdout="", stderr=str(exc), exit_code=127)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(stdout="", stderr="timed out", exit_code=124, timed_out=True)
        return ExecResult(
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )

    async def snapshot(self) -> bytes:  # pragma: no cover - exercised in Task A3
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.root.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(self.root)))
        return buf.getvalue()

    async def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
