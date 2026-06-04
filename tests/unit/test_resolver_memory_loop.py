"""Regression: ``_resolve_memory_config`` must not break inside a running loop.

The deploy pipeline calls ``resolve_dependencies`` (and therefore
``_resolve_memory_config``) from within an already-running asyncio event loop
(the CLI wraps deploy in ``asyncio.run``). The old implementation called
``asyncio.run(_fetch())`` unconditionally, which raises ``RuntimeError`` when a
loop is already running — leaking a ``RuntimeWarning: coroutine '_fetch' was
never awaited`` and silently falling back to the postgresql default without ever
consulting the registry.
"""

from __future__ import annotations

import warnings

import pytest

from engine.resolver import _resolve_memory_config


def test_resolve_memory_config_sync_context_returns_tuple() -> None:
    """In a plain sync context it still returns a (backend, ttl) tuple."""
    backend, ttl = _resolve_memory_config(["memory/some-store"])
    assert isinstance(backend, str)
    assert isinstance(ttl, int)


@pytest.mark.asyncio
async def test_resolve_memory_config_inside_running_loop_no_warning() -> None:
    """Called from within a running event loop, it must not leak an
    un-awaited-coroutine warning and must still return a valid tuple."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        backend, ttl = _resolve_memory_config(["memory/some-store"])

    assert isinstance(backend, str)
    assert isinstance(ttl, int)
    leaked = [w for w in caught if "never awaited" in str(w.message)]
    assert not leaked, f"coroutine was leaked un-awaited: {[str(w.message) for w in leaked]}"
