import sys

import pytest

from engine.sandbox.local import LocalSandbox


@pytest.mark.asyncio
async def test_write_then_read_roundtrip():
    sb = LocalSandbox()
    try:
        await sb.write("agent.py", "print('hi')\n")
        assert await sb.read("agent.py") == "print('hi')\n"
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_write_creates_nested_dirs():
    sb = LocalSandbox()
    try:
        await sb.write("tools/search.py", "x = 1\n")
        assert "tools/search.py" in await sb.list(".")
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_read_missing_raises_filenotfound():
    sb = LocalSandbox()
    try:
        with pytest.raises(FileNotFoundError):
            await sb.read("nope.py")
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_path_traversal_is_rejected():
    sb = LocalSandbox()
    try:
        with pytest.raises(ValueError):
            await sb.write("../escape.py", "danger")
        with pytest.raises(ValueError):
            await sb.read("/etc/passwd")
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_write_rejects_oversized_file():
    sb = LocalSandbox()
    try:
        with pytest.raises(ValueError):
            await sb.write("big.txt", "x" * 1_000_001)
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_list_missing_directory_returns_empty():
    sb = LocalSandbox()
    try:
        assert await sb.list("does-not-exist") == []
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_close_removes_workspace():
    sb = LocalSandbox()
    root = sb.root
    await sb.write("a.txt", "1")
    await sb.close()
    assert not root.exists()


@pytest.mark.asyncio
async def test_exec_runs_and_captures_stdout():
    sb = LocalSandbox()
    try:
        await sb.write("hello.py", "print('from sandbox')\n")
        res = await sb.exec([sys.executable, "hello.py"], timeout=10)
        assert res.ok
        assert "from sandbox" in res.stdout
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_exec_timeout_is_flagged():
    sb = LocalSandbox()
    try:
        res = await sb.exec([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5)
        assert res.timed_out is True
        assert res.exit_code == 124
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_exec_missing_command_returns_127():
    sb = LocalSandbox()
    try:
        res = await sb.exec(["this-binary-does-not-exist-xyz"], timeout=5)
        assert res.exit_code == 127
        assert res.ok is False
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_snapshot_contains_written_files():
    import io
    import zipfile

    sb = LocalSandbox()
    try:
        await sb.write("agent.py", "x = 1\n")
        await sb.write("tools/t.py", "y = 2\n")
        data = await sb.snapshot()
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        assert {"agent.py", "tools/t.py"} <= names
    finally:
        await sb.close()
