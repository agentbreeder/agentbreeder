"""Shared health-check polling loop for deployers.

All cloud deployers must wait for a deployed service to become healthy before
declaring success. AWS, GCP, Azure, and Kubernetes each implemented this loop
differently. ``poll_until_ready`` is the single shared implementation: a
deadline-bounded loop with exponential backoff (capped) between checks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class HealthCheckTimeout(Exception):  # noqa: N818 — semantic name; not an error suffix
    """Raised when poll_until_ready exceeds its timeout."""


async def poll_until_ready(
    check: Callable[[], Awaitable[bool]],
    *,
    timeout: float,
    initial_interval: float = 1.0,
    max_interval: float = 10.0,
    backoff_factor: float = 2.0,
) -> None:
    """Poll ``check`` until it returns True or ``timeout`` seconds elapse.

    Args:
        check: zero-arg async callable returning True when healthy. Exceptions
            from ``check`` propagate (treat them as transport failures the
            caller wants to know about, not as "not yet healthy").
        timeout: total seconds before raising HealthCheckTimeout.
        initial_interval: seconds to wait after the first failed check.
        max_interval: cap on inter-check delay.
        backoff_factor: multiplier each iteration.

    Raises:
        HealthCheckTimeout: if ``timeout`` elapses without ``check`` returning True.
    """
    if timeout <= 0:
        raise ValueError("timeout must be > 0")

    deadline = time.monotonic() + timeout
    interval = initial_interval

    while True:
        ok = await check()
        if ok:
            return

        now = time.monotonic()
        if now >= deadline:
            raise HealthCheckTimeout(f"Health check did not become ready within {timeout}s")

        # Don't sleep past the deadline.
        sleep_for = min(interval, max_interval, deadline - now)
        logger.debug("health-check not ready; sleeping %.3fs", sleep_for)
        await asyncio.sleep(sleep_for)
        interval = min(interval * backoff_factor, max_interval)
