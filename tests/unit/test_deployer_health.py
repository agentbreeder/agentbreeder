"""Tests for engine.deployers._health.poll_until_ready."""

from __future__ import annotations

import asyncio

import pytest

from engine.deployers._health import HealthCheckTimeout, poll_until_ready


@pytest.mark.asyncio
async def test_returns_when_healthy_on_first_check() -> None:
    async def check() -> bool:
        return True

    await poll_until_ready(check, timeout=1.0, initial_interval=0.01)


@pytest.mark.asyncio
async def test_returns_when_healthy_after_retries() -> None:
    calls = {"n": 0}

    async def check() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    await poll_until_ready(check, timeout=5.0, initial_interval=0.01, max_interval=0.05)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_raises_on_timeout() -> None:
    async def check() -> bool:
        return False

    with pytest.raises(HealthCheckTimeout):
        await poll_until_ready(check, timeout=0.05, initial_interval=0.01)


@pytest.mark.asyncio
async def test_passes_through_check_exceptions() -> None:
    async def check() -> bool:
        raise RuntimeError("infra error")

    with pytest.raises(RuntimeError, match="infra error"):
        await poll_until_ready(check, timeout=1.0, initial_interval=0.01)


@pytest.mark.asyncio
async def test_interval_grows_with_backoff_factor() -> None:
    sleeps: list[float] = []

    async def fake_sleep(t: float) -> None:
        sleeps.append(t)

    async def check() -> bool:
        return len(sleeps) >= 3

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fake_sleep)
        await poll_until_ready(
            check,
            timeout=5.0,
            initial_interval=0.1,
            max_interval=10.0,
            backoff_factor=2.0,
        )

    # Should have grown: 0.1, 0.2, 0.4 ... up to when check returned True.
    assert sleeps[0] == pytest.approx(0.1)
    assert sleeps[1] == pytest.approx(0.2)
    assert sleeps[2] == pytest.approx(0.4)
