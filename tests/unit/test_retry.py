"""Tests for api.retry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from api.retry import RetryExhaustedError, async_retry


@pytest.mark.asyncio
async def test_success_on_first_try() -> None:
    fn = AsyncMock(return_value="ok")
    result = await async_retry(fn, max_attempts=3)
    assert result == "ok"
    assert fn.await_count == 1


@pytest.mark.asyncio
async def test_retries_on_exception() -> None:
    fn = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom"), "ok"])
    result = await async_retry(fn, max_attempts=3, initial_delay=0.001)
    assert result == "ok"
    assert fn.await_count == 3


@pytest.mark.asyncio
async def test_raises_retry_exhausted_after_max_attempts() -> None:
    fn = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RetryExhaustedError) as exc:
        await async_retry(fn, max_attempts=3, initial_delay=0.001)
    assert exc.value.attempts == 3
    assert isinstance(exc.value.last_exception, RuntimeError)


@pytest.mark.asyncio
async def test_only_retries_listed_exceptions() -> None:
    fn = AsyncMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError):
        await async_retry(fn, max_attempts=3, initial_delay=0.001, retry_on=(RuntimeError,))
    assert fn.await_count == 1  # not retried


@pytest.mark.asyncio
async def test_exponential_backoff_delays() -> None:
    """Sleep between attempts approximates exponential growth (with jitter)."""
    sleeps: list[float] = []

    async def fake_sleep(t: float) -> None:
        sleeps.append(t)

    fn = AsyncMock(side_effect=[RuntimeError(), RuntimeError(), RuntimeError(), "ok"])
    # Patch asyncio.sleep with our recorder.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fake_sleep)
        result = await async_retry(
            fn,
            max_attempts=4,
            initial_delay=0.1,
            max_delay=10.0,
            backoff_factor=2.0,
            jitter=False,
        )
    assert result == "ok"
    # 3 sleeps between 4 attempts; with backoff_factor=2 and jitter off:
    # 0.1, 0.2, 0.4
    assert sleeps == pytest.approx([0.1, 0.2, 0.4])


@pytest.mark.asyncio
async def test_max_delay_caps_backoff() -> None:
    sleeps: list[float] = []

    async def fake_sleep(t: float) -> None:
        sleeps.append(t)

    fn = AsyncMock(side_effect=[RuntimeError()] * 5 + ["ok"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fake_sleep)
        await async_retry(
            fn,
            max_attempts=6,
            initial_delay=1.0,
            max_delay=2.0,
            backoff_factor=2.0,
            jitter=False,
        )
    # 1.0, 2.0 (capped), 2.0, 2.0, 2.0
    assert sleeps == pytest.approx([1.0, 2.0, 2.0, 2.0, 2.0])
