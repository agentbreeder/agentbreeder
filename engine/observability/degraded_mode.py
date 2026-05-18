"""Process-wide warn-once dedup + per-call DegradedFlag for response tagging.

Use ``warn_once(component, reason, extra=...)`` to emit a structured WARNING
that fires at most once per (component, reason) per process. Use
``DegradedFlag`` inside request handlers to track whether any sub-step degraded
and surface it to callers via response fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_WARNED: set[tuple[str, str]] = set()
_LOCK = Lock()


def warn_once(component: str, reason: str, extra: dict[str, Any] | None = None) -> None:
    """Emit a WARNING log the first time a (component, reason) pair is seen.

    Args:
        component: stable identifier for the failure surface (e.g. ``rag.embedding``).
        reason: stable identifier for the failure mode (e.g. ``openai-no-api-key``).
        extra: optional additional fields to include in the log record's ``extra``.
    """
    key = (component, reason)
    with _LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    log_extra: dict[str, Any] = {"component": component, "reason": reason}
    if extra:
        log_extra.update(extra)
    logger.warning(
        "%s degraded: %s",
        component,
        reason,
        extra=log_extra,
    )


def clear_degraded_state() -> None:
    """Reset the warn-once dedup set. Tests should call this between cases."""
    with _LOCK:
        _WARNED.clear()


@dataclass
class DegradedFlag:
    """Per-call degraded-mode tracker. Default state is not degraded."""

    is_degraded: bool = False
    first_reason: str | None = None

    def mark(self, reason: str) -> None:
        """Record a degraded event. First reason wins (preserves earliest cause)."""
        if not self.is_degraded:
            self.is_degraded = True
            self.first_reason = reason
