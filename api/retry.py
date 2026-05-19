"""Async retry helper with exponential backoff + jitter.

Use ``async_retry(fn, max_attempts=..., retry_on=...)`` to wrap an async call.
On exhaustion, ``RetryExhaustedError`` is raised with the last exception
attached.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryExhaustedError(Exception):
    """Raised when async_retry exhausts max_attempts without success."""

    def __init__(self, attempts: int, last_exception: BaseException) -> None:
        super().__init__(f"Retry exhausted after {attempts} attempts: {last_exception}")
        self.attempts = attempts
        self.last_exception = last_exception


async def async_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Retry an async callable with exponential backoff.

    Args:
        fn: zero-arg async callable to retry.
        max_attempts: total attempts including the first call. Must be >= 1.
        initial_delay: delay (seconds) before the 2nd attempt.
        max_delay: cap on the delay between attempts.
        backoff_factor: multiplier applied each retry.
        jitter: if True, multiply each delay by a random factor in [0.5, 1.5].
        retry_on: tuple of exception types to retry on. Other exceptions
            propagate immediately.

    Returns the result of ``fn()`` on the first successful attempt.

    Raises:
        RetryExhaustedError: after ``max_attempts`` failed retries.
        Exception: any exception not matching ``retry_on`` is re-raised as-is.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_exc: BaseException | None = None
    delay = initial_delay

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except retry_on as exc:  # noqa: PERF203 — retries by design
            last_exc = exc
            if attempt >= max_attempts:
                break
            sleep_for = min(delay, max_delay)
            if jitter:
                sleep_for *= 0.5 + random.random()
            logger.debug(
                "async_retry attempt %d/%d failed: %s — sleeping %.3fs",
                attempt,
                max_attempts,
                exc,
                sleep_for,
            )
            await asyncio.sleep(sleep_for)
            delay *= backoff_factor

    assert last_exc is not None
    raise RetryExhaustedError(max_attempts, last_exc)
