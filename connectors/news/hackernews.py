"""HackerNews connector using the Algolia search API (no API key required)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from connectors.base import BaseConnector
from connectors.news.base import NewsItem

logger = logging.getLogger(__name__)

_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"


class HackerNewsConnector(BaseConnector):
    """Discovers HackerNews stories matching a query via the Algolia API."""

    def __init__(
        self,
        query: str = "AI OR LLM",
        limit: int = 5,
        timeout: float = 10.0,
    ) -> None:
        self._query = query
        self._limit = limit
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "hackernews"

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(_HN_SEARCH_URL, params={"query": "test", "hitsPerPage": 1})
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def fetch(self, query: str | None = None, limit: int | None = None) -> list[NewsItem]:
        """Fetch HackerNews stories matching the given query."""
        effective_query = query or self._query
        effective_limit = limit or self._limit

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    _HN_SEARCH_URL,
                    params={
                        "query": effective_query,
                        "hitsPerPage": effective_limit,
                        "tags": "story",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("HackerNews fetch failed: %s", e)
            return []
        except (ValueError, KeyError) as e:
            logger.warning("HackerNews response parse error: %s", e)
            return []

        items: list[NewsItem] = []
        for hit in data.get("hits", []):
            object_id = hit.get("objectID", "")
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
            raw_ts = hit.get("created_at_i")
            published_at = (
                datetime.fromtimestamp(raw_ts, tz=UTC) if raw_ts else datetime.now(tz=UTC)
            )
            items.append(
                NewsItem(
                    title=hit.get("title", ""),
                    url=url,
                    summary=hit.get("story_text") or "",
                    source="hackernews",
                    published_at=published_at,
                )
            )

        return items

    async def scan(self) -> list[dict]:
        """Satisfy BaseConnector; delegates to fetch() and converts to dicts."""
        items = await self.fetch()
        return [
            {
                "name": item.title,
                "description": item.summary,
                "source": self.name,
                "url": item.url,
                "published_at": item.published_at.isoformat(),
            }
            for item in items
        ]
