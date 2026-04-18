"""Tests for HackerNews, ArXiv, and RSS news connectors."""

from __future__ import annotations

from datetime import UTC, datetime
from time import struct_time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from connectors.news.base import NewsItem


class TestNewsItem:
    def test_fields(self) -> None:
        now = datetime.now(tz=UTC)
        item = NewsItem(title="T", url="U", summary="S", source="src", published_at=now)
        assert item.title == "T"
        assert item.url == "U"
        assert item.summary == "S"
        assert item.source == "src"
        assert item.published_at == now


class TestHackerNewsConnector:
    def _make_hit(
        self, title: str = "AI News", url: str = "https://example.com", ts: int = 1700000000
    ) -> dict:
        return {
            "title": title,
            "url": url,
            "story_text": "summary",
            "created_at_i": ts,
            "objectID": "123",
        }

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        from connectors.news.hackernews import HackerNewsConnector

        assert HackerNewsConnector().name == "hackernews"

    @pytest.mark.asyncio
    async def test_is_available_true(self) -> None:
        from connectors.news.hackernews import HackerNewsConnector

        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await HackerNewsConnector().is_available()
        assert result is True

    @pytest.mark.asyncio
    async def test_is_available_false_on_http_error(self) -> None:
        import httpx

        from connectors.news.hackernews import HackerNewsConnector

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await HackerNewsConnector().is_available()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_returns_items(self) -> None:
        from connectors.news.hackernews import HackerNewsConnector

        mock_resp = MagicMock(status_code=200)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"hits": [self._make_hit()]})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await HackerNewsConnector().fetch()
        assert len(items) == 1
        assert items[0].title == "AI News"
        assert items[0].source == "hackernews"

    @pytest.mark.asyncio
    async def test_fetch_uses_hn_fallback_url_when_no_external_url(self) -> None:
        from connectors.news.hackernews import HackerNewsConnector

        hit = {
            "title": "Ask HN",
            "url": None,
            "story_text": "",
            "created_at_i": 1700000000,
            "objectID": "999",
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"hits": [hit]})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await HackerNewsConnector().fetch()
        assert "news.ycombinator.com" in items[0].url

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_on_http_error(self) -> None:
        import httpx

        from connectors.news.hackernews import HackerNewsConnector

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=MagicMock())
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await HackerNewsConnector().fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_scan_converts_to_dicts(self) -> None:
        from connectors.news.hackernews import HackerNewsConnector

        mock_resp = MagicMock(status_code=200)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"hits": [self._make_hit()]})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await HackerNewsConnector().scan()
        assert isinstance(results[0], dict)
        assert results[0]["source"] == "hackernews"


class TestArxivConnector:
    _ATOM_STUB = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2501.00001v1</id>
    <title>Test Paper Title</title>
    <summary>Abstract text here.</summary>
    <published>2025-01-01T00:00:00Z</published>
  </entry>
</feed>
"""

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        from connectors.news.arxiv import ArxivConnector

        assert ArxivConnector().name == "arxiv"

    @pytest.mark.asyncio
    async def test_is_available_true(self) -> None:
        from connectors.news.arxiv import ArxivConnector

        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await ArxivConnector().is_available()
        assert result is True

    @pytest.mark.asyncio
    async def test_is_available_false(self) -> None:
        import httpx

        from connectors.news.arxiv import ArxivConnector

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await ArxivConnector().is_available()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_parses_atom(self) -> None:
        from connectors.news.arxiv import ArxivConnector

        mock_resp = MagicMock(status_code=200)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = self._ATOM_STUB
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await ArxivConnector().fetch()
        assert len(items) == 1
        assert items[0].title == "Test Paper Title"
        assert items[0].source == "arxiv"
        assert "arxiv.org" in items[0].url

    @pytest.mark.asyncio
    async def test_fetch_empty_on_http_error(self) -> None:
        import httpx

        from connectors.news.arxiv import ArxivConnector

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await ArxivConnector().fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_empty_on_xml_error(self) -> None:
        from connectors.news.arxiv import ArxivConnector

        mock_resp = MagicMock(status_code=200)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "NOT XML <<<"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await ArxivConnector().fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_scan_returns_dicts(self) -> None:
        from connectors.news.arxiv import ArxivConnector

        mock_resp = MagicMock(status_code=200)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = self._ATOM_STUB
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await ArxivConnector().scan()
        assert isinstance(results[0], dict)
        assert results[0]["source"] == "arxiv"


class TestRSSConnector:
    def _make_entry(
        self, title: str = "RSS Title", link: str = "https://example.com/rss"
    ) -> MagicMock:
        entry = MagicMock()
        entry.get = lambda key, default="": {
            "link": link,
            "title": title,
            "summary": "RSS summary",
            "description": "",
            "published_parsed": None,
            "updated_parsed": None,
        }.get(key, default)
        return entry

    def _make_parsed_feed(self, entries: list | None = None) -> MagicMock:
        parsed = MagicMock()
        parsed.feed.get = lambda key, default="": {"title": "Test Feed"}.get(key, default)
        parsed.entries = entries or [self._make_entry()]
        return parsed

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        from connectors.news.rss import RSSConnector

        assert RSSConnector().name == "rss"

    @pytest.mark.asyncio
    async def test_is_available_when_feedparser_present(self) -> None:
        from connectors.news.rss import RSSConnector

        with patch("connectors.news.rss._FEEDPARSER_AVAILABLE", True):
            result = await RSSConnector().is_available()
        assert result is True

    @pytest.mark.asyncio
    async def test_is_available_false_when_feedparser_missing(self) -> None:
        from connectors.news.rss import RSSConnector

        with patch("connectors.news.rss._FEEDPARSER_AVAILABLE", False):
            result = await RSSConnector().is_available()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_raises_without_feedparser(self) -> None:
        from connectors.news.rss import RSSConnector

        with patch("connectors.news.rss._FEEDPARSER_AVAILABLE", False):
            with pytest.raises(ImportError, match="feedparser"):
                await RSSConnector(feeds=["http://example.com/feed"]).fetch()

    def _with_mock_feedparser(self, parsed_feed: MagicMock, side_effect: Exception | None = None):
        """Context manager: inject a mock feedparser into sys.modules and the rss module."""
        mock_fp = MagicMock()
        if side_effect is None:
            mock_fp.parse = MagicMock(return_value=parsed_feed)
        else:
            mock_fp.parse = MagicMock(side_effect=side_effect)
        return (
            patch.dict("sys.modules", {"feedparser": mock_fp}),
            patch("connectors.news.rss.feedparser", mock_fp, create=True),
            patch("connectors.news.rss._FEEDPARSER_AVAILABLE", True),
            mock_fp,
        )

    @pytest.mark.asyncio
    async def test_fetch_returns_items(self) -> None:
        from connectors.news.rss import RSSConnector

        parsed = self._make_parsed_feed()
        mock_fp = MagicMock()
        mock_fp.parse = MagicMock(return_value=parsed)
        with (
            patch.dict("sys.modules", {"feedparser": mock_fp}),
            patch("connectors.news.rss.feedparser", mock_fp, create=True),
            patch("connectors.news.rss._FEEDPARSER_AVAILABLE", True),
        ):
            items = await RSSConnector(feeds=["http://example.com/feed"]).fetch()
        assert len(items) == 1
        assert items[0].title == "RSS Title"

    @pytest.mark.asyncio
    async def test_fetch_skips_failed_feed(self) -> None:
        from connectors.news.rss import RSSConnector

        mock_fp = MagicMock()
        mock_fp.parse = MagicMock(side_effect=Exception("parse error"))
        with (
            patch.dict("sys.modules", {"feedparser": mock_fp}),
            patch("connectors.news.rss.feedparser", mock_fp, create=True),
            patch("connectors.news.rss._FEEDPARSER_AVAILABLE", True),
        ):
            items = await RSSConnector(feeds=["http://bad.com/feed"]).fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_respects_limit_per_feed(self) -> None:
        from connectors.news.rss import RSSConnector

        entries = [self._make_entry(title=f"Item {i}") for i in range(10)]
        parsed = self._make_parsed_feed(entries=entries)
        mock_fp = MagicMock()
        mock_fp.parse = MagicMock(return_value=parsed)
        with (
            patch.dict("sys.modules", {"feedparser": mock_fp}),
            patch("connectors.news.rss.feedparser", mock_fp, create=True),
            patch("connectors.news.rss._FEEDPARSER_AVAILABLE", True),
        ):
            items = await RSSConnector(feeds=["http://example.com/feed"], limit_per_feed=3).fetch()
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_scan_returns_dicts(self) -> None:
        from connectors.news.rss import RSSConnector

        parsed = self._make_parsed_feed()
        mock_fp = MagicMock()
        mock_fp.parse = MagicMock(return_value=parsed)
        with (
            patch.dict("sys.modules", {"feedparser": mock_fp}),
            patch("connectors.news.rss.feedparser", mock_fp, create=True),
            patch("connectors.news.rss._FEEDPARSER_AVAILABLE", True),
        ):
            results = await RSSConnector(feeds=["http://example.com/feed"]).scan()
        assert isinstance(results[0], dict)
        assert results[0]["source"] == "rss"


class TestParseStructTime:
    def test_valid_struct_time(self) -> None:
        from connectors.news.rss import _parse_struct_time

        t = struct_time((2025, 1, 15, 12, 0, 0, 0, 0, 0))
        result = _parse_struct_time(t)
        assert result.year == 2025
        assert result.tzinfo is not None

    def test_none_returns_utc_now(self) -> None:
        from connectors.news.rss import _parse_struct_time

        result = _parse_struct_time(None)
        assert result.tzinfo is not None
