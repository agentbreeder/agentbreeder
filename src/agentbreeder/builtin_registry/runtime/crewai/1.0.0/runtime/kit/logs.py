"""Library log lines as one plain, redacted line each (spec §13.8), as 1.3.0's
`quiet_library_logs`: never a multi-line traceback, which can hold a model's or a provider's
text. A runtime passes its framework's logger names; the kit's own are always included.

Stdlib only.
"""

from __future__ import annotations

import logging

from . import redaction

# Loggers of the packages every kit runtime has.
# `asyncio` logs an unhandled exception in a task or callback with its traceback.
KIT_LOGGERS = ("asyncio", "httpx", "httpcore", "openai", "openinference", "opentelemetry")


class OneLine(logging.Formatter):
    """No tracebacks; one redacted line."""

    def format(self, record):
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return redaction.one_line(redaction.upstream(super().format(record)))


def quiet(names: tuple[str, ...] = ()) -> None:
    """Warnings and above from these loggers (and their children), one redacted line each."""
    handler = logging.StreamHandler()
    handler.setFormatter(OneLine("%(levelname)s %(name)s: %(message)s"))
    for name in (*KIT_LOGGERS, *names):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.WARNING)
        logger.propagate = False
