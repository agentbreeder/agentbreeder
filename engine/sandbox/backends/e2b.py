"""E2BBackend — managed Firecracker microVM via the e2b async SDK (Wave 4).

SECURITY (design §11.2): no long-lived secrets are injected; egress is a
default-deny allowlist (LLM endpoints + PyPI + npm) configured on the e2b
template; CPU/mem/wall-clock caps come from the template + per-call timeouts.
e2b is a third-party sub-processor — requires a signed DPA + privacy-policy
disclosure before GA (see design §11.2 / Open Q #1).

This backend is not unit-tested (it hits e2b); it is covered by the gated
integration test in tests/integration/test_e2b_backend.py and Part D's live path.
"""

from __future__ import annotations

import builtins
import io
import os
import shlex
import zipfile

from engine.sandbox.base import ExecResult

# Workspace root inside the microVM.
_WORKDIR = "/home/user/project"
_TEMPLATE = os.environ.get("AGENTBREEDER_E2B_TEMPLATE", "base")


class E2BBackend:
    """Thin adapter over e2b's AsyncSandbox. One microVM per builder session."""

    def __init__(self) -> None:
        self._sbx = None  # type: ignore[var-annotated]

    async def start(self) -> None:
        from e2b import AsyncSandbox  # imported lazily so the dep stays optional

        # NB: verify kwargs against the installed e2b version (see Task A3 note).
        self._sbx = await AsyncSandbox.create(template=_TEMPLATE)
        await self._sbx.files.make_dir(_WORKDIR)

    def _abs(self, path: str) -> str:
        return f"{_WORKDIR}/{path}"

    async def write_file(self, path: str, content: str) -> None:
        await self._sbx.files.write(self._abs(path), content)

    async def read_file(self, path: str) -> str:
        return await self._sbx.files.read(self._abs(path))

    async def list_files(self, directory: str = ".") -> builtins.list[str]:
        base = _WORKDIR if directory in (".", "") else self._abs(directory)
        entries = await self._sbx.files.list(base)
        # Return workspace-relative file paths only.
        out: list[str] = []
        for e in entries:
            full = getattr(e, "path", str(e))
            if getattr(e, "is_dir", False):
                continue
            out.append(full.removeprefix(f"{_WORKDIR}/"))
        return sorted(out)

    async def run(self, cmd: builtins.list[str], timeout: float) -> ExecResult:
        joined = " ".join(shlex.quote(c) for c in cmd)
        try:
            res = await self._sbx.commands.run(joined, cwd=_WORKDIR, timeout=int(timeout))
        except Exception as exc:  # e2b raises on non-zero/timeout in some versions
            return ExecResult(stdout="", stderr=str(exc), exit_code=1)
        return ExecResult(
            stdout=getattr(res, "stdout", "") or "",
            stderr=getattr(res, "stderr", "") or "",
            exit_code=int(getattr(res, "exit_code", 0) or 0),
        )

    async def snapshot(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in await self.list_files("."):
                zf.writestr(rel, await self.read_file(rel))
        return buf.getvalue()

    async def destroy(self) -> None:
        if self._sbx is not None:
            await self._sbx.kill()
            self._sbx = None
