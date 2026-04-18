"""Generic RSS/Atom connector using feedparser (optional dependency)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import struct_time

from connectors.base import BaseConnector
from connectors.news.base import NewsItem

logger = logging.getLogger(__name__)

try:
    import feedparser

    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False


def _require_feedparser() -> None:
    if not _FEEDPARSER_AVAILABLE:
        raise ImportError(
            "feedparser is required for RSSConnector. "
            "Install it with: pip install 'agentbreeder[news]'"
        )


def _parse_struct_time(t: struct_time | None) -> datetime:
    """Convert a feedparser time tuple to a timezone-aware datetime."""
    if t is None:
        return datetime.now(tz=UTC)
    try:
        import calendar

        return datetime.fromtimestamp(calendar.timegm(t), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(tz=UTC)


class RSSConnector(BaseConnector):
    """Fetches items from one or more RSS/Atom feeds using feedparser."""

    def __init__(
        self,
        feeds: list[str] | None = None,
        limit_per_feed: int = 3,
    ) -> None:
        self._feeds = feeds or []
        self._limit_per_feed = limit_per_feed

    @property
    def name(self) -> str:
        return "rss"

    async def is_available(self) -> bool:
        return _FEEDPARSER_AVAILABLE

    async def fetch(
        self,
        feeds: list[str] | None = None,
        limit_per_feed: int | None = None,
    ) -> list[NewsItem]:
        """Fetch items from the given RSS/Atom feed URLs."""
        _require_feedparser()

        effective_feeds = feeds or self._feeds
        effective_limit = limit_per_feed if limit_per_feed is not None else self._limit_per_feed

        items: list[NewsItem] = []
        for feed_url in effective_feeds:
            # feedparser.parse() is synchronous; acceptable for a connector scan operation
            try:
                parsed = feedparser.parse(feed_url)
            except Exception as e:  # noqa: BLE001 — feedparser can raise a wide variety of errors
                logger.warning("Failed to parse feed %s: %s", feed_url, e)
                continue

            feed_title = parsed.feed.get("title", feed_url)

            for entry in parsed.entries[:effective_limit]:
                url = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                published_at = _parse_struct_time(
                    entry.get("published_parsed") or entry.get("updated_parsed")
                )

                items.append(
                    NewsItem(
                        title=title,
                        url=url,
                        summary=summary,
                        source=feed_title,
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
