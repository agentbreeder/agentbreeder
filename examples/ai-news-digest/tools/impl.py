"""Pure Python tool implementations — no framework dependencies.

These functions are used by:
- tools/server.py  (MCP server, via @mcp.tool() wrappers)
- agent.py         (Google ADK agent, passed directly as tools)

Return type is list[dict] throughout — JSON-serialisable, ADK-compatible.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
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
        items.append({
            "title": hit.get("title", ""),
            "url": url,
            "points": hit.get("points", 0),
            "source": "hackernews",
        })
    return items


# ---------------------------------------------------------------------------
# Tool 2: fetch_arxiv
# ---------------------------------------------------------------------------

def fetch_arxiv(limit: int = 5) -> list[dict]:
    """Fetch latest AI/ML research papers from ArXiv.

    Args:
        limit: Maximum number of papers to return.

    Returns:
        List of dicts with keys: title, url, summary, source.
        Returns [] on network error.
    """
    params = {
        "search_query": "cat:cs.AI OR cat:cs.LG",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": limit,
    }
    try:
        resp = httpx.get(_ARXIV_API, params=params, timeout=15.0)
        resp.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("ArXiv API timed out")
        return []
    except httpx.HTTPError as exc:
        logger.warning("ArXiv API error: %s", exc)
        return []

    root = ET.fromstring(resp.text)
    items = []
    for entry in root.findall(f"{{{_ARXIV_NS}}}entry")[:limit]:
        raw_id = entry.findtext(f"{{{_ARXIV_NS}}}id", "")
        url = raw_id.replace("http://", "https://")
        if url:
            url = re.sub(r"v\d+$", "", url)
        summary = entry.findtext(f"{{{_ARXIV_NS}}}summary", "").strip()
        items.append({
            "title": entry.findtext(f"{{{_ARXIV_NS}}}title", "").strip(),
            "url": url,
            "summary": summary[:300],
            "source": "arxiv",
        })
    return items


# ---------------------------------------------------------------------------
# Tool 3: fetch_rss
# ---------------------------------------------------------------------------

def fetch_rss(limit: int = 5) -> list[dict]:
    """Fetch AI industry news from TechCrunch, Wired, and VentureBeat RSS feeds.

    Args:
        limit: Maximum total items to return (spread across feeds).

    Returns:
        List of dicts with keys: title, url, summary, source.
        Deduplicates by URL. Skips feeds that fail -- never raises.
    """
    seen_urls: set[str] = set()
    items: list[dict] = []

    for feed_url in _RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries:
                url = getattr(entry, "link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                raw_summary = getattr(entry, "summary", "")
                items.append({
                    "title": getattr(entry, "title", ""),
                    "url": url,
                    "summary": _strip_html(raw_summary)[:300],
                    "source": "rss",
                })
        except Exception as exc:
            logger.warning("RSS feed %s failed: %s", feed_url, exc)

    return items[:limit]


# ---------------------------------------------------------------------------
# Tool 4: send_email
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str) -> dict:
    """Send the digest email to all configured recipients via Gmail SMTP.

    Reads all configuration from environment variables.

    Args:
        subject: Email subject line.
        body: Plain-text email body (the digest).

    Returns:
        Dict with key 'sent_to' (int) -- number of recipients emailed.

    Raises:
        ValueError: If SMTP_USER or RECIPIENT_EMAILS env vars are not set.
        smtplib.SMTPAuthenticationError: If Gmail credentials are wrong.
    """
    smtp_user = os.environ.get("SMTP_USER")
    if not smtp_user:
        raise ValueError("SMTP_USER env var is required (your Gmail address)")

    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    recipient_str = os.environ.get("RECIPIENT_EMAILS")
    if not recipient_str:
        raise ValueError("RECIPIENT_EMAILS env var is required (comma-separated list)")

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    recipients = [r.strip() for r in recipient_str.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, recipients, msg.as_string())

    logger.info("Digest emailed to %d recipients", len(recipients))
    return {"sent_to": len(recipients)}
