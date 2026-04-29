"""Local override: fetch top AI stories from Hacker News (Algolia API)."""

from __future__ import annotations

from typing import Any

from tools.impl import fetch_hackernews as _impl

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 30},
    },
    "required": [],
}


def fetch_hackernews(limit: int = 5) -> list[dict]:
    """Fetch top AI stories from Hacker News.

    Args:
        limit: Maximum stories to return.

    Returns:
        List of {title, url, points, author, created_at}.
    """
    return _impl(limit=limit)
