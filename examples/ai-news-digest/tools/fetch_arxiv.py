"""Local override: fetch latest AI/ML papers from ArXiv."""
from __future__ import annotations

from typing import Any

from tools.impl import fetch_arxiv as _impl

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 30},
    },
    "required": [],
}


def fetch_arxiv(limit: int = 5) -> list[dict]:
    """Fetch latest AI/ML papers from ArXiv cs.AI + cs.LG.

    Args:
        limit: Maximum papers to return.

    Returns:
        List of {title, abstract, url, authors, published}.
    """
    return _impl(limit=limit)
