"""Secret values out of text the runtime stores, returns or exports (audit events, error
responses, trace spans, log lines). Shared by agent.py, mcp_client.py and telemetry.py."""

from __future__ import annotations

import re

_SECRETS: set[str] = set()
_KEYISH = re.compile(r"(sk-|AIza|Bearer\s+)[A-Za-z0-9_.\-]{8,}")
# LiteLLM's masked key in its budget message, `sk-...WXYZ` (too short for _KEYISH), #1034, and
# the same mask written with an ellipsis or asterisks.
_FRAGMENT = re.compile(r"sk-(?:\.{2,}|\u2026+|\*{2,})[A-Za-z0-9_\-]{0,16}")
# A SHA-256 in hex: LiteLLM names a key's token hash in its rate-limit message.
_HASH = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")


def remember(value: str) -> None:
    """A value to replace wherever it appears (MCP tokens, the gateway key)."""
    if value and len(value) >= 16:
        _SECRETS.add(value)


def values() -> frozenset[str]:
    return frozenset(_SECRETS)


def redact(text: str) -> str:
    """Known secret values, then key-shaped strings and masked key fragments."""
    for value in values():
        text = text.replace(value, "[redacted]")
    text = _KEYISH.sub(lambda match: match.group(1) + "[redacted]", text)
    return _FRAGMENT.sub("sk-[redacted]", text)


def upstream(text: str) -> str:
    """Gateway or provider error text, for the log and the stored turn: `redact`, plus 64-hex
    token hashes."""
    return _HASH.sub("[hash]", redact(text))


def one_line(text: str) -> str:
    """Control and other non-printable characters (newlines included) as spaces, so a log line
    stays one line."""
    return "".join(ch if ch.isprintable() else " " for ch in text)
