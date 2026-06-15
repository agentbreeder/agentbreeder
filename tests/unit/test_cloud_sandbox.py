"""Unit tests for CloudSandbox + the SandboxBackend abstraction (no network)."""

from __future__ import annotations

import pytest

from engine.sandbox.backends.base import SandboxBackend
from engine.sandbox.backends.fake import FakeBackend
from engine.sandbox.base import Sandbox
from engine.sandbox.cloud import _MAX_FILE_BYTES, CloudSandbox, _safe_relpath


def test_fake_backend_satisfies_protocol():
    assert isinstance(FakeBackend(), SandboxBackend)


def test_cloud_sandbox_satisfies_protocol():
    assert isinstance(CloudSandbox(FakeBackend()), Sandbox)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../escape", "a/../../b", "\\abs"])
def test_safe_relpath_rejects_escapes(bad):
    with pytest.raises(ValueError):
        _safe_relpath(bad)


def test_safe_relpath_normalizes():
    assert _safe_relpath("./a/b.py") == "a/b.py"


@pytest.mark.asyncio
async def test_cloud_sandbox_write_read_roundtrip_starts_backend():
    backend = FakeBackend()
    sb = CloudSandbox(backend)
    await sb.write("agent.py", "print('hi')")
    assert backend.started is True
    assert await sb.read("agent.py") == "print('hi')"


@pytest.mark.asyncio
async def test_cloud_sandbox_write_rejects_oversize():
    sb = CloudSandbox(FakeBackend())
    with pytest.raises(ValueError):
        await sb.write("big.txt", "x" * (_MAX_FILE_BYTES + 1))


@pytest.mark.asyncio
async def test_cloud_sandbox_close_records_elapsed_and_destroys():
    backend = FakeBackend()
    sb = CloudSandbox(backend)
    await sb.write("a.py", "1")  # starts backend
    await sb.close()
    assert backend.destroyed is True
    assert sb.elapsed_seconds >= 0.0
