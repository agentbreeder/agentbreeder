"""Local override: fetch AI industry news from configured RSS feeds."""

from __future__ import annotations

from typing import Any

from tools.impl import fetch_rss as _impl

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 30},
    },
    "required": [],
}


def fetch_rss(limit: int = 5) -> list[dict]:
    """Fetch AI industry news from TechCrunch, Wired, VentureBeat RSS.

    Args:
        limit: Maximum stories to return.

    Returns:
        List of {title, url, summary, source, published}.
    """
    return _impl(limit=limit)
