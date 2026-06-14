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
