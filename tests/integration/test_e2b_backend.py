"""Gated integration test for E2BBackend. Skipped unless E2B_API_KEY is set."""

from __future__ import annotations

import os

import pytest

_KEY = os.environ.get("E2B_API_KEY")
pytestmark = pytest.mark.skipif(not _KEY, reason="E2B_API_KEY not set")


@pytest.mark.asyncio
async def test_e2b_write_read_exec_roundtrip():
    from engine.sandbox.backends.e2b import E2BBackend

    backend = E2BBackend()
    try:
        await backend.start()
        await backend.write_file("hello.txt", "world")
        assert await backend.read_file("hello.txt") == "world"
        res = await backend.run(["echo", "ok"], timeout=30.0)
        assert res.exit_code == 0
    finally:
        await backend.destroy()
