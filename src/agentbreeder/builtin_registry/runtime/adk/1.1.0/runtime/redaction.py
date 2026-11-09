"""Secret values out of text the runtime stores, returns or exports (audit events, error
responses, trace spans). Shared by agent.py, mcp_client.py and telemetry.py."""

from __future__ import annotations

import re

_SECRETS: set[str] = set()
_KEYISH = re.compile(r"(sk-|AIza|Bearer\s+)[A-Za-z0-9_.\-]{8,}")


def remember(value: str) -> None:
    """A value to replace wherever it appears (MCP tokens, the gateway key)."""
    if value and len(value) >= 16:
        _SECRETS.add(value)


def values() -> frozenset[str]:
    return frozenset(_SECRETS)


def redact(text: str) -> str:
    """Known secret values, then key-shaped strings."""
    for value in values():
        text = text.replace(value, "[redacted]")
    return _KEYISH.sub(lambda match: match.group(1) + "[redacted]", text)
