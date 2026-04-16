"""Pure Python tool implementations — no framework dependencies.

These functions are used by:
- tools/server.py  (MCP server, via @mcp.tool() wrappers)
- agent.py         (Google ADK agent, passed directly as tools)

Return type is list[dict] throughout — JSON-serialisable, ADK-compatible.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)

_HN_API = "https://hn.algolia.com/api/v1/search"
_ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_NS = "http://www.w3.org/2005/Atom"
_RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/tag/artificial-intelligence/latest/rss",
    "https://venturebeat.com/ai/feed/",
]


# ---------------------------------------------------------------------------
# HTML stripping helper
# ---------------------------------------------------------------------------


class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(text: str) -> str:
    parser = _StripHTML()
    parser.feed(text or "")
    return parser.get_text()


# ---------------------------------------------------------------------------
# Tool 1: fetch_hackernews
# ---------------------------------------------------------------------------


def fetch_hackernews(limit: int = 5) -> list[dict]:
    """Fetch top AI stories from Hacker News via the Algolia API.

    Args:
        limit: Maximum number of stories to return.

    Returns:
        List of dicts with keys: title, url, points, source.
        Returns [] on network error so the agent can continue with other sources.
    """
    params = {
        "query": "AI OR LLM OR machine learning OR foundation model",
        "tags": "story",
        "hitsPerPage": limit,
    }
    try:
        resp = httpx.get(_HN_API, params=params, timeout=10.0)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except httpx.TimeoutException:
        logger.warning("HN API timed out")
        return []
    except httpx.HTTPError as exc:
        logger.warning("HN API error: %s", exc)
        return []

    items = []
    for hit in hits[:limit]:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        items.append(
            {
                "title": hit.get("title", ""),
                "url": url,
                "points": hit.get("points", 0),
                "source": "hackernews",
            }
        )
    return items
