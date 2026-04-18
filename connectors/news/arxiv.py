"""ArXiv connector using the public Atom feed API (no API key required)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx

from connectors.base import BaseConnector
from connectors.news.base import NewsItem

logger = logging.getLogger(__name__)

_ARXIV_API_URL = "http://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"


class ArxivConnector(BaseConnector):
    """Discovers recent ArXiv papers in the given subject categories."""

    def __init__(
        self,
        categories: list[str] | None = None,
        limit: int = 5,
        timeout: float = 15.0,
    ) -> None:
        self._categories = categories or ["cs.AI", "cs.LG"]
        self._limit = limit
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "arxiv"

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    _ARXIV_API_URL, params={"search_query": "cat:cs.AI", "max_results": 1}
                )
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def fetch(
        self,
        categories: list[str] | None = None,
        limit: int | None = None,
    ) -> list[NewsItem]:
        """Fetch recent ArXiv papers for the given categories."""
        effective_cats = categories or self._categories
        effective_limit = limit or self._limit

        # ArXiv category query: "cat:cs.AI OR cat:cs.LG"
        search_query = " OR ".join(f"cat:{c}" for c in effective_cats)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    _ARXIV_API_URL,
                    params={
                        "search_query": search_query,
                        "max_results": effective_limit,
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    },
                )
                resp.raise_for_status()
                xml_text = resp.text
        except httpx.HTTPError as e:
            logger.warning("ArXiv fetch failed: %s", e)
            return []

        try:
            root = ET.fromstring(xml_text)  # noqa: S314
        except ET.ParseError as e:
            logger.warning("ArXiv XML parse error: %s", e)
            return []

        items: list[NewsItem] = []
        for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
            title_el = entry.find(f"{{{_ATOM_NS}}}title")
            summary_el = entry.find(f"{{{_ATOM_NS}}}summary")
            published_el = entry.find(f"{{{_ATOM_NS}}}published")

            # The canonical URL is the <id> element for ArXiv entries
            id_el = entry.find(f"{{{_ATOM_NS}}}id")
            url = id_el.text.strip() if id_el is not None and id_el.text else ""

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            summary = summary_el.text.strip() if summary_el is not None and summary_el.text else ""

            published_at = datetime.now(tz=UTC)
            if published_el is not None and published_el.text:
                try:
                    published_at = datetime.fromisoformat(
                        published_el.text.strip().replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            items.append(
                NewsItem(
                    title=title,
                    url=url,
                    summary=summary,
                    source="arxiv",
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
