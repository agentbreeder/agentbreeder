# AI News Digest Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working AI news digest agent at `examples/ai-news-digest/` that fetches HN + ArXiv + RSS stories daily, summarises them with Ollama Gemma3:27b, and emails a grouped digest via Gmail SMTP.

**Architecture:** MCP tool server (`tools/impl.py` for pure logic, `tools/server.py` for MCP wiring) plus a Google ADK agent (`agent.py`) that uses LiteLlmModel to route to local Ollama. Tool implementations are shared between the MCP server and the agent's local dev path so there's no duplication.

**Tech Stack:** `google-adk>=1.29.0`, `litellm[ollama]>=1.40.0`, `fastmcp>=1.0.0`, `feedparser>=6.0.0`, `httpx>=0.27.0`, `schedule>=1.2.0`, `pytest`, `agentbreeder` CLI

---

## File Map

| File | Created/Modified | Responsibility |
|------|-----------------|----------------|
| `examples/ai-news-digest/tools/impl.py` | Create | Pure Python tool implementations — no framework deps |
| `examples/ai-news-digest/tools/server.py` | Create | FastMCP wiring — wraps impl.py with `@mcp.tool()` |
| `examples/ai-news-digest/tools/mcp.yaml` | Create | MCP server manifest for AgentBreeder registry |
| `examples/ai-news-digest/tools/requirements.txt` | Create | fastmcp, feedparser, httpx |
| `examples/ai-news-digest/agent.py` | Create | `root_agent` export + scheduling entrypoint |
| `examples/ai-news-digest/agent.yaml` | Create | AgentBreeder manifest (model, secrets, deploy config) |
| `examples/ai-news-digest/root_agent.yaml` | Create | Google ADK native YAML (target Low Code state, #66) |
| `examples/ai-news-digest/requirements.txt` | Create | google-adk, litellm[ollama], schedule |
| `examples/ai-news-digest/.env.example` | Create | All env vars documented with descriptions |
| `examples/ai-news-digest/tests/test_impl.py` | Create | Unit tests for all tool implementations |
| `examples/ai-news-digest/tests/test_agent.py` | Create | agentbreeder validate + agent structure tests |

---

## Task 1: Scaffold project structure

**Files:**
- Create: `examples/ai-news-digest/` (directory tree)
- Create: `examples/ai-news-digest/.env.example`
- Create: `examples/ai-news-digest/tools/requirements.txt`
- Create: `examples/ai-news-digest/requirements.txt`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p examples/ai-news-digest/tools
mkdir -p examples/ai-news-digest/tests
touch examples/ai-news-digest/tests/__init__.py
touch examples/ai-news-digest/tools/__init__.py
```

Run from: `/Users/rajit/personal-github/agentbreeder`

- [ ] **Step 2: Create `.env.example`**

Create `examples/ai-news-digest/.env.example`:

```bash
# Ollama configuration
OLLAMA_BASE_URL=http://localhost:11434
AGENT_MODEL=ollama/gemma3:27b

# News digest settings
NEWS_COUNT=15          # total items, split evenly across 3 sources
DIGEST_HOUR=8          # local hour to send digest (0-23), used with --schedule

# Gmail SMTP (requires App Password — not your regular password)
# Setup: https://myaccount.google.com/apppasswords (needs 2FA enabled)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your-app-password-here

# Recipients (comma-separated, no spaces)
RECIPIENT_EMAILS=person1@example.com,person2@example.com
```

- [ ] **Step 3: Create `tools/requirements.txt`**

Create `examples/ai-news-digest/tools/requirements.txt`:

```
fastmcp>=1.0.0
feedparser>=6.0.0
httpx>=0.27.0
```

- [ ] **Step 4: Create top-level `requirements.txt`**

Create `examples/ai-news-digest/requirements.txt`:

```
google-adk>=1.29.0
litellm[ollama]>=1.40.0
schedule>=1.2.0
httpx>=0.27.0
feedparser>=6.0.0
fastmcp>=1.0.0
pytest>=8.0.0
```

- [ ] **Step 5: Commit scaffold**

```bash
git add examples/ai-news-digest/
git commit -m "feat(ai-news-digest): scaffold project structure"
```

---

## Task 2: Implement `fetch_hackernews` (TDD)

**Files:**
- Create: `examples/ai-news-digest/tools/impl.py` (partial — add this function)
- Create: `examples/ai-news-digest/tests/test_impl.py` (partial — add these tests)

- [ ] **Step 1: Write the failing test**

Create `examples/ai-news-digest/tests/test_impl.py`:

```python
"""Unit tests for tool implementations in tools/impl.py."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# fetch_hackernews
# ---------------------------------------------------------------------------

HN_API_RESPONSE = {
    "hits": [
        {"title": "GPT-5 released", "url": "https://example.com/gpt5", "points": 500},
        {"title": "Ask HN: Best LLM?", "url": None, "objectID": "40000001", "points": 200},
        {"title": "LLaMA 4 beats GPT-4", "url": "https://example.com/llama4", "points": 300},
    ]
}


def test_fetch_hackernews_returns_correct_count():
    mock_response = MagicMock()
    mock_response.json.return_value = HN_API_RESPONSE
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response) as mock_get:
        from tools.impl import fetch_hackernews

        result = fetch_hackernews(limit=2)

    assert len(result) == 2


def test_fetch_hackernews_correct_api_url():
    mock_response = MagicMock()
    mock_response.json.return_value = HN_API_RESPONSE
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response) as mock_get:
        from tools.impl import fetch_hackernews

        fetch_hackernews(limit=3)

    call_url = mock_get.call_args[0][0]
    assert "hn.algolia.com" in call_url
    assert "AI" in call_url or "query" in call_url


def test_fetch_hackernews_fallback_url_for_ask_hn():
    """Posts with url=None should use the HN item URL via objectID."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "hits": [{"title": "Ask HN: LLMs?", "url": None, "objectID": "40000001", "points": 50}]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        from tools.impl import fetch_hackernews

        result = fetch_hackernews(limit=1)

    assert result[0]["url"] == "https://news.ycombinator.com/item?id=40000001"


def test_fetch_hackernews_timeout_returns_empty():
    import httpx

    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        from tools.impl import fetch_hackernews

        result = fetch_hackernews(limit=5)

    assert result == []
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd examples/ai-news-digest
pip install -r requirements.txt
pytest tests/test_impl.py::test_fetch_hackernews_returns_correct_count -v
```

Expected: `ModuleNotFoundError: No module named 'tools.impl'`

- [ ] **Step 3: Implement `fetch_hackernews` in `tools/impl.py`**

Create `examples/ai-news-digest/tools/impl.py`:

```python
"""Pure Python tool implementations — no framework dependencies.

These functions are used by:
- tools/server.py  (MCP server, via @mcp.tool() wrappers)
- agent.py         (Google ADK agent, passed directly as tools)

Return type is list[dict] throughout — JSON-serialisable, ADK-compatible.
"""

from __future__ import annotations

import logging
import os
import smtplib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser

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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_impl.py -k "hackernews" -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add examples/ai-news-digest/tools/impl.py examples/ai-news-digest/tests/test_impl.py
git commit -m "feat(ai-news-digest): fetch_hackernews tool with tests"
```

---

## Task 3: Implement `fetch_arxiv` (TDD)

**Files:**
- Modify: `examples/ai-news-digest/tools/impl.py` (append function)
- Modify: `examples/ai-news-digest/tests/test_impl.py` (append tests)

- [ ] **Step 1: Add failing tests to `tests/test_impl.py`**

Append to `examples/ai-news-digest/tests/test_impl.py`:

```python
# ---------------------------------------------------------------------------
# fetch_arxiv
# ---------------------------------------------------------------------------

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Attention Is All You Need: Revisited</title>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <summary>We revisit the original transformer paper and propose improvements.</summary>
    <published>2024-01-15T18:00:00Z</published>
    <author><name>Jane Doe</name></author>
  </entry>
  <entry>
    <title>Scaling Laws for Neural Language Models v2</title>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <summary>We extend scaling law research to trillion-parameter models.</summary>
    <published>2024-01-14T12:00:00Z</published>
    <author><name>John Smith</name></author>
  </entry>
</feed>"""


def test_fetch_arxiv_returns_correct_count():
    mock_response = MagicMock()
    mock_response.text = ARXIV_XML
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        from tools.impl import fetch_arxiv

        result = fetch_arxiv(limit=1)

    assert len(result) == 1


def test_fetch_arxiv_parses_fields():
    mock_response = MagicMock()
    mock_response.text = ARXIV_XML
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_response):
        from tools.impl import fetch_arxiv

        result = fetch_arxiv(limit=2)

    first = result[0]
    assert first["title"] == "Attention Is All You Need: Revisited"
    assert "arxiv.org/abs/2401.00001" in first["url"]
    assert "revisit" in first["summary"].lower()
    assert first["source"] == "arxiv"


def test_fetch_arxiv_timeout_returns_empty():
    import httpx

    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        from tools.impl import fetch_arxiv

        result = fetch_arxiv(limit=5)

    assert result == []
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_impl.py -k "arxiv" -v
```

Expected: `ImportError` or `AttributeError` — `fetch_arxiv` not defined yet.

- [ ] **Step 3: Append `fetch_arxiv` to `tools/impl.py`**

Append after the `fetch_hackernews` function:

```python
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
        # Normalise versioned IDs: http://arxiv.org/abs/2401.00001v1 → abs URL
        url = raw_id.replace("http://", "https://").split("v")[0] if raw_id else ""
        summary = entry.findtext(f"{{{_ARXIV_NS}}}summary", "").strip()
        items.append({
            "title": entry.findtext(f"{{{_ARXIV_NS}}}title", "").strip(),
            "url": url,
            "summary": summary[:300],  # keep summaries short for context window
            "source": "arxiv",
        })
    return items
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_impl.py -k "arxiv" -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add examples/ai-news-digest/tools/impl.py examples/ai-news-digest/tests/test_impl.py
git commit -m "feat(ai-news-digest): fetch_arxiv tool with tests"
```

---

## Task 4: Implement `fetch_rss` (TDD)

**Files:**
- Modify: `examples/ai-news-digest/tools/impl.py` (append function)
- Modify: `examples/ai-news-digest/tests/test_impl.py` (append tests)

- [ ] **Step 1: Add failing tests**

Append to `examples/ai-news-digest/tests/test_impl.py`:

```python
# ---------------------------------------------------------------------------
# fetch_rss
# ---------------------------------------------------------------------------

def _make_feed(entries: list[dict]) -> MagicMock:
    """Build a mock feedparser result."""
    feed = MagicMock()
    feed.entries = [MagicMock(**e) for e in entries]
    return feed


def test_fetch_rss_merges_multiple_feeds():
    feeds = [
        _make_feed([{"title": "TechCrunch story", "link": "https://tc.com/1", "summary": "TC news"}]),
        _make_feed([{"title": "Wired story", "link": "https://wired.com/1", "summary": "Wired news"}]),
        _make_feed([{"title": "VB story", "link": "https://vb.com/1", "summary": "VB news"}]),
    ]

    with patch("feedparser.parse", side_effect=feeds):
        from tools.impl import fetch_rss

        result = fetch_rss(limit=3)

    assert len(result) == 3
    sources = {r["url"] for r in result}
    assert "https://tc.com/1" in sources
    assert "https://wired.com/1" in sources


def test_fetch_rss_deduplicates_by_url():
    duplicate_entry = {"title": "Dupe", "link": "https://same.com/1", "summary": "x"}
    feeds = [
        _make_feed([duplicate_entry]),
        _make_feed([duplicate_entry]),  # same URL — should be deduplicated
        _make_feed([]),
    ]

    with patch("feedparser.parse", side_effect=feeds):
        from tools.impl import fetch_rss

        result = fetch_rss(limit=5)

    urls = [r["url"] for r in result]
    assert urls.count("https://same.com/1") == 1


def test_fetch_rss_respects_limit():
    many_entries = [
        {"title": f"Story {i}", "link": f"https://tc.com/{i}", "summary": "x"}
        for i in range(10)
    ]
    feeds = [
        _make_feed(many_entries),
        _make_feed([]),
        _make_feed([]),
    ]

    with patch("feedparser.parse", side_effect=feeds):
        from tools.impl import fetch_rss

        result = fetch_rss(limit=3)

    assert len(result) <= 3


def test_fetch_rss_continues_if_one_feed_fails():
    def side_effect(url: str):
        if "techcrunch" in url:
            raise Exception("connection refused")
        return _make_feed([{"title": "Wired story", "link": "https://wired.com/1", "summary": "x"}])

    with patch("feedparser.parse", side_effect=side_effect):
        from tools.impl import fetch_rss

        result = fetch_rss(limit=5)

    assert any(r["url"] == "https://wired.com/1" for r in result)
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_impl.py -k "rss" -v
```

Expected: `AttributeError` — `fetch_rss` not defined.

- [ ] **Step 3: Append `fetch_rss` to `tools/impl.py`**

```python
# ---------------------------------------------------------------------------
# Tool 3: fetch_rss
# ---------------------------------------------------------------------------

def fetch_rss(limit: int = 5) -> list[dict]:
    """Fetch AI industry news from TechCrunch, Wired, and VentureBeat RSS feeds.

    Args:
        limit: Maximum total items to return (spread across feeds).

    Returns:
        List of dicts with keys: title, url, summary, source.
        Deduplicates by URL. Skips feeds that fail — never raises.
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_impl.py -k "rss" -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add examples/ai-news-digest/tools/impl.py examples/ai-news-digest/tests/test_impl.py
git commit -m "feat(ai-news-digest): fetch_rss tool with tests"
```

---

## Task 5: Implement `send_email` (TDD)

**Files:**
- Modify: `examples/ai-news-digest/tools/impl.py` (append function)
- Modify: `examples/ai-news-digest/tests/test_impl.py` (append tests)

- [ ] **Step 1: Add failing tests**

Append to `examples/ai-news-digest/tests/test_impl.py`:

```python
# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

def test_send_email_calls_smtp_correctly():
    with (
        patch.dict("os.environ", {
            "SMTP_USER": "sender@gmail.com",
            "SMTP_PASSWORD": "app-password",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "RECIPIENT_EMAILS": "a@example.com,b@example.com",
        }),
        patch("smtplib.SMTP") as mock_smtp_cls,
    ):
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        from tools.impl import send_email

        result = send_email(subject="Test digest", body="Hello world")

    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("sender@gmail.com", "app-password")
    mock_smtp.sendmail.assert_called_once()
    assert result["sent_to"] == 2


def test_send_email_raises_if_recipient_emails_not_set():
    with (
        patch.dict("os.environ", {
            "SMTP_USER": "sender@gmail.com",
            "SMTP_PASSWORD": "pw",
        }, clear=False),
    ):
        # Remove RECIPIENT_EMAILS if present
        import os
        os.environ.pop("RECIPIENT_EMAILS", None)

        from tools.impl import send_email

        with pytest.raises(ValueError, match="RECIPIENT_EMAILS"):
            send_email(subject="x", body="y")


def test_send_email_raises_if_smtp_user_not_set():
    with patch.dict("os.environ", {}, clear=True):
        from tools.impl import send_email

        with pytest.raises(ValueError, match="SMTP_USER"):
            send_email(subject="x", body="y")
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_impl.py -k "send_email" -v
```

Expected: `AttributeError` — `send_email` not defined.

- [ ] **Step 3: Append `send_email` to `tools/impl.py`**

```python
# ---------------------------------------------------------------------------
# Tool 4: send_email
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str) -> dict:
    """Send the digest email to all configured recipients via Gmail SMTP.

    Reads all configuration from environment variables — no arguments needed
    for credentials or recipient list.

    Args:
        subject: Email subject line.
        body: Plain-text email body (the digest).

    Returns:
        Dict with key 'sent_to' (int) — number of recipients emailed.

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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_impl.py -k "send_email" -v
```

Expected: `3 passed`

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/test_impl.py -v
```

Expected: `14 passed` (4 HN + 3 ArXiv + 4 RSS + 3 email)

- [ ] **Step 6: Commit**

```bash
git add examples/ai-news-digest/tools/impl.py examples/ai-news-digest/tests/test_impl.py
git commit -m "feat(ai-news-digest): send_email tool with tests — all impl tests passing"
```

---

## Task 6: Wire up MCP server

**Files:**
- Create: `examples/ai-news-digest/tools/server.py`
- Create: `examples/ai-news-digest/tools/mcp.yaml`

- [ ] **Step 1: Create `tools/mcp.yaml`**

Create `examples/ai-news-digest/tools/mcp.yaml`:

```yaml
name: news-digest-tools
version: "1.0.0"
description: "News fetching and email delivery tools for the AI news digest agent"
transport: stdio
command: python tools/server.py
tools:
  - fetch_hackernews
  - fetch_arxiv
  - fetch_rss
  - send_email
```

- [ ] **Step 2: Create `tools/server.py`**

Create `examples/ai-news-digest/tools/server.py`:

```python
"""MCP tool server for the AI news digest agent.

Exposes fetch_hackernews, fetch_arxiv, fetch_rss, and send_email as MCP tools
using the stdio transport. AgentBreeder's MCP sidecar manages this process
in production; run directly for local dev:

    python tools/server.py
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from tools.impl import fetch_arxiv, fetch_hackernews, fetch_rss, send_email

mcp = FastMCP(
    name="news-digest-tools",
    version="1.0.0",
)


@mcp.tool()
def fetch_hackernews_tool(limit: int = 5) -> list[dict]:
    """Fetch top AI stories from Hacker News. Returns list of {title, url, points, source}."""
    return fetch_hackernews(limit=limit)


@mcp.tool()
def fetch_arxiv_tool(limit: int = 5) -> list[dict]:
    """Fetch latest AI/ML papers from ArXiv cs.AI + cs.LG. Returns list of {title, url, summary, source}."""
    return fetch_arxiv(limit=limit)


@mcp.tool()
def fetch_rss_tool(limit: int = 5) -> list[dict]:
    """Fetch AI industry news from TechCrunch, Wired, VentureBeat RSS. Returns list of {title, url, summary, source}."""
    return fetch_rss(limit=limit)


@mcp.tool()
def send_email_tool(subject: str, body: str) -> dict:
    """Send digest email to RECIPIENT_EMAILS. Returns {sent_to: int}."""
    return send_email(subject=subject, body=body)


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 3: Smoke-test the MCP server starts without errors**

```bash
cd examples/ai-news-digest
python tools/server.py &
sleep 2
kill %1
```

Expected: process starts and exits cleanly (no import errors).

- [ ] **Step 4: Commit**

```bash
git add examples/ai-news-digest/tools/server.py examples/ai-news-digest/tools/mcp.yaml
git commit -m "feat(ai-news-digest): MCP tool server wiring"
```

---

## Task 7: Write `agent.yaml` and `root_agent.yaml`

**Files:**
- Create: `examples/ai-news-digest/agent.yaml`
- Create: `examples/ai-news-digest/root_agent.yaml`

- [ ] **Step 1: Create `agent.yaml`**

Create `examples/ai-news-digest/agent.yaml`:

```yaml
name: ai-news-digest
version: 1.0.0
description: "Daily AI news digest — HN + ArXiv + RSS, summarised by Gemma3:27b, emailed via Gmail"
team: examples
owner: dev@company.com
tags: [news, digest, ollama, gemma, google-adk, email, example]

framework: google_adk

model:
  primary: ollama/gemma3:27b
  temperature: 0.4
  max_tokens: 4096

tools:
  - ref: tools/news-digest-tools

deploy:
  cloud: local
  env_vars:
    LOG_LEVEL: info
    NEWS_COUNT: "15"
    DIGEST_HOUR: "8"
    SMTP_HOST: smtp.gmail.com
    SMTP_PORT: "587"
    OLLAMA_BASE_URL: http://localhost:11434
  secrets:
    - SMTP_USER
    - SMTP_PASSWORD
    - RECIPIENT_EMAILS

google_adk:
  session_backend: memory
  memory_service: memory
  artifact_service: memory
```

- [ ] **Step 2: Create `root_agent.yaml`**

This is the target Low Code state (works once issue #66 is resolved — LiteLlmModel auto-injected for `ollama/` models):

Create `examples/ai-news-digest/root_agent.yaml`:

```yaml
# Google ADK native agent YAML.
# AgentBreeder auto-generates server_loader.py from this file at build time.
# NOTE: requires AgentBreeder issue #66 to be resolved for ollama/ model support.
name: ai-news-digest
model: ollama/gemma3:27b
description: "Daily AI news digest agent"
instruction: |
  You are an AI news curator. When the user asks for the daily digest, follow these steps:

  1. Call fetch_hackernews_tool, fetch_arxiv_tool, and fetch_rss_tool to gather stories.
     Use limit = NEWS_COUNT / 3 for each (default: 5 each).

  2. Write a digest with exactly three labelled sections:

     ## Hacker News Picks
     ## Research Papers (ArXiv)
     ## Industry News (RSS)

  3. For each item write 2-3 sentences: what it is and why it matters for AI practitioners.
     Include the URL at the end of each item.

  4. Call send_email_tool with:
     - subject: "AI News Digest — {today's date}"
     - body: the full digest text

  Be direct. No preamble. No filler phrases. Prioritise novelty and practical impact.
  If a source returned no results, note it briefly ("No ArXiv papers fetched today") and continue.
```

- [ ] **Step 3: Commit**

```bash
git add examples/ai-news-digest/agent.yaml examples/ai-news-digest/root_agent.yaml
git commit -m "feat(ai-news-digest): agent.yaml and root_agent.yaml manifests"
```

---

## Task 8: Write `agent.py` (workaround for issue #66)

**Files:**
- Create: `examples/ai-news-digest/agent.py`

This file is the workaround until AgentBreeder issue #66 (LiteLlmModel auto-injection for `ollama/` models in `root_agent.yaml`) is resolved. It exports `root_agent` — the variable AgentBreeder's `google_adk_server.py` template looks for.

- [ ] **Step 1: Create `agent.py`**

Create `examples/ai-news-digest/agent.py`:

```python
"""Google ADK agent for the AI news digest.

Exports `root_agent` — picked up by AgentBreeder's server wrapper at runtime.

WORKAROUND: This file exists because AgentBreeder issue #66 is not yet resolved.
Once #66 lands, delete agent.py and use root_agent.yaml alone (Low Code tier).

Run directly for local development:
    python agent.py --once        # fetch and email now
    python agent.py --schedule    # daemon mode, fires daily at DIGEST_HOUR
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date

import schedule

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlmModel

from tools.impl import fetch_arxiv, fetch_hackernews, fetch_rss, send_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (mirrors root_agent.yaml instruction)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an AI news curator. When the user asks for the daily digest, follow these steps:

1. Call fetch_hackernews, fetch_arxiv, and fetch_rss to gather stories.
   Use limit = NEWS_COUNT / 3 for each (default: 5 each).

2. Write a digest with exactly three labelled sections:

   ## Hacker News Picks
   ## Research Papers (ArXiv)
   ## Industry News (RSS)

3. For each item write 2-3 sentences: what it is and why it matters for AI practitioners.
   Include the URL at the end of each item.

4. Call send_email with:
   - subject: "AI News Digest — {today's date}"
   - body: the full digest text

Be direct. No preamble. No filler phrases. Prioritise novelty and practical impact.
If a source returned no results, note it briefly and continue.
"""

# ---------------------------------------------------------------------------
# Tool wrappers (same impl, ADK-compatible signatures)
# ---------------------------------------------------------------------------

def _fetch_hackernews(limit: int = 5) -> list[dict]:
    """Fetch top AI stories from Hacker News."""
    count = int(os.environ.get("NEWS_COUNT", "15")) // 3
    return fetch_hackernews(limit=count)


def _fetch_arxiv(limit: int = 5) -> list[dict]:
    """Fetch latest AI/ML papers from ArXiv cs.AI + cs.LG."""
    count = int(os.environ.get("NEWS_COUNT", "15")) // 3
    return fetch_arxiv(limit=count)


def _fetch_rss(limit: int = 5) -> list[dict]:
    """Fetch AI industry news from TechCrunch, Wired, VentureBeat RSS."""
    count = int(os.environ.get("NEWS_COUNT", "15")) // 3
    return fetch_rss(limit=count)


def _send_email(subject: str, body: str) -> dict:
    """Send digest to RECIPIENT_EMAILS via Gmail SMTP."""
    return send_email(subject=subject, body=body)


# ---------------------------------------------------------------------------
# root_agent — exported for AgentBreeder's server wrapper
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="ai-news-digest",
    model=LiteLlmModel(
        model=os.environ.get("AGENT_MODEL", "ollama/gemma3:27b"),
        api_base=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    ),
    description="Daily AI news digest — HN + ArXiv + RSS, emailed via Gmail",
    instruction=_SYSTEM_PROMPT,
    tools=[_fetch_hackernews, _fetch_arxiv, _fetch_rss, _send_email],
)

# ---------------------------------------------------------------------------
# Local runner (--once / --schedule)
# ---------------------------------------------------------------------------

async def _run_digest() -> None:
    """Invoke the agent with the digest prompt."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part

    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="ai-news-digest", session_service=session_service)
    session = await session_service.create_session(app_name="ai-news-digest", user_id="scheduler")

    prompt = f"Generate today's AI news digest for {date.today().isoformat()} and email it."
    logger.info("Starting digest run: %s", prompt)

    async for event in runner.run_async(
        user_id="scheduler",
        session_id=session.id,
        new_message=Content(parts=[Part(text=prompt)]),
    ):
        if event.is_final_response():
            logger.info("Digest complete: %s", event.content.parts[0].text[:100])


def _run_once() -> None:
    import asyncio
    asyncio.run(_run_digest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI News Digest Agent")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="Run digest immediately and exit")
    group.add_argument("--schedule", action="store_true", help="Run as daemon, fire daily at DIGEST_HOUR")
    args = parser.parse_args()

    if args.once:
        _run_once()
    else:
        hour = int(os.environ.get("DIGEST_HOUR", "8"))
        schedule.every().day.at(f"{hour:02d}:00").do(_run_once)
        logger.info("Scheduler started — digest will run daily at %02d:00", hour)
        while True:
            schedule.run_pending()
            time.sleep(60)
```

- [ ] **Step 2: Verify it imports cleanly (no Ollama needed for import)**

```bash
cd examples/ai-news-digest
python -c "from agent import root_agent; print(root_agent.name)"
```

Expected: `ai-news-digest`

- [ ] **Step 3: Commit**

```bash
git add examples/ai-news-digest/agent.py
git commit -m "feat(ai-news-digest): agent.py with LiteLlmModel + scheduling entrypoint"
```

---

## Task 9: Validate with AgentBreeder CLI

**Files:**
- Create: `examples/ai-news-digest/tests/test_agent.py`

- [ ] **Step 1: Create `tests/test_agent.py`**

Create `examples/ai-news-digest/tests/test_agent.py`:

```python
"""Integration and structural tests for the AI news digest agent."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).parent.parent


def test_agent_yaml_passes_agentbreeder_validate():
    """agentbreeder validate must exit 0 on the agent directory."""
    result = subprocess.run(
        ["agentbreeder", "validate"],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"agentbreeder validate failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_root_agent_exported():
    """agent.py must export a variable named root_agent."""
    sys.path.insert(0, str(AGENT_DIR))
    from agent import root_agent

    assert root_agent is not None
    assert root_agent.name == "ai-news-digest"


def test_root_agent_has_four_tools():
    """root_agent must have exactly 4 tools registered."""
    sys.path.insert(0, str(AGENT_DIR))
    from agent import root_agent

    assert len(root_agent.tools) == 4


def test_agent_yaml_model_is_ollama():
    """agent.yaml model.primary must be an ollama/ prefixed string."""
    import yaml

    config = yaml.safe_load((AGENT_DIR / "agent.yaml").read_text())
    assert config["model"]["primary"].startswith("ollama/")


def test_env_example_documents_all_required_vars():
    """Every required env var must appear in .env.example."""
    env_example = (AGENT_DIR / ".env.example").read_text()
    required = ["SMTP_USER", "SMTP_PASSWORD", "RECIPIENT_EMAILS", "OLLAMA_BASE_URL"]
    for var in required:
        assert var in env_example, f"{var} missing from .env.example"
```

- [ ] **Step 2: Install agentbreeder CLI (if not already installed)**

```bash
pip install agentbreeder
```

- [ ] **Step 3: Run all tests**

```bash
cd examples/ai-news-digest
pytest tests/ -v
```

Expected output:
```
tests/test_impl.py::test_fetch_hackernews_returns_correct_count PASSED
tests/test_impl.py::test_fetch_hackernews_correct_api_url PASSED
tests/test_impl.py::test_fetch_hackernews_fallback_url_for_ask_hn PASSED
tests/test_impl.py::test_fetch_hackernews_timeout_returns_empty PASSED
tests/test_impl.py::test_fetch_arxiv_returns_correct_count PASSED
tests/test_impl.py::test_fetch_arxiv_parses_fields PASSED
tests/test_impl.py::test_fetch_arxiv_timeout_returns_empty PASSED
tests/test_impl.py::test_fetch_rss_merges_multiple_feeds PASSED
tests/test_impl.py::test_fetch_rss_deduplicates_by_url PASSED
tests/test_impl.py::test_fetch_rss_respects_limit PASSED
tests/test_impl.py::test_fetch_rss_continues_if_one_feed_fails PASSED
tests/test_impl.py::test_send_email_calls_smtp_correctly PASSED
tests/test_impl.py::test_send_email_raises_if_recipient_emails_not_set PASSED
tests/test_impl.py::test_send_email_raises_if_smtp_user_not_set PASSED
tests/test_agent.py::test_agent_yaml_passes_agentbreeder_validate PASSED
tests/test_agent.py::test_root_agent_exported PASSED
tests/test_agent.py::test_root_agent_has_four_tools PASSED
tests/test_agent.py::test_agent_yaml_model_is_ollama PASSED
tests/test_agent.py::test_env_example_documents_all_required_vars PASSED
19 passed
```

- [ ] **Step 4: Final commit**

```bash
git add examples/ai-news-digest/tests/test_agent.py
git commit -m "feat(ai-news-digest): integration tests — all 19 passing"
```

---

## Task 10: README and final cleanup

**Files:**
- Create: `examples/ai-news-digest/README.md`

- [ ] **Step 1: Create README**

Create `examples/ai-news-digest/README.md`:

```markdown
# AI News Digest Agent

Daily AI news digest using **Google ADK + Ollama Gemma3:27b**, deployed with AgentBreeder.

Fetches top stories from Hacker News, ArXiv (cs.AI + cs.LG), and industry RSS feeds,
synthesises a grouped digest, and emails it to configured recipients via Gmail.

## What AgentBreeder handles automatically

- Installing `google-adk`, framework deps — injected by the runtime into the container
- Server boilerplate (`server.py`) — auto-copied from AgentBreeder's template
- Dockerfile — auto-generated
- `pip install` inside the container

## What you provide

- `agent.yaml` — model, secrets, env vars
- `root_agent.yaml` — agent identity + instruction (no Python required once #66 resolved)
- `agent.py` — workaround until [AgentBreeder #66](https://github.com/rajitsaha/agentbreeder/issues/66) lands
- `tools/` — MCP tool server (news fetch + email)

## Quick start

### 1. Prerequisites

```bash
# Install Ollama (manages local LLM)
brew install ollama        # macOS
ollama pull gemma3:27b     # ~18GB — grab a coffee

# Start Ollama
ollama serve
```

### 2. Install

```bash
pip install agentbreeder
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env:
#   SMTP_USER      — your Gmail address
#   SMTP_PASSWORD  — Gmail App Password (not your login password)
#                    Setup: https://myaccount.google.com/apppasswords
#   RECIPIENT_EMAILS — comma-separated list
```

### 4. Run

```bash
# Send digest immediately
python agent.py --once

# Run as daily daemon (fires at DIGEST_HOUR, default 8am)
python agent.py --schedule

# Deploy via AgentBreeder (production path)
agentbreeder deploy --target local
```

## Known limitations (open AgentBreeder issues)

| Issue | Impact | Workaround |
|-------|--------|------------|
| [#63](https://github.com/rajitsaha/agentbreeder/issues/63) | Ollama models need manual LiteLlmModel wiring | `agent.py` handles this |
| [#64](https://github.com/rajitsaha/agentbreeder/issues/64) | Ollama not auto-started by `agentbreeder deploy` | Run `ollama serve` manually |
| [#65](https://github.com/rajitsaha/agentbreeder/issues/65) | Model weights not auto-pulled | Run `ollama pull gemma3:27b` manually |
| [#66](https://github.com/rajitsaha/agentbreeder/issues/66) | `root_agent.yaml` can't use Ollama without Python | `agent.py` exists as workaround |
```

- [ ] **Step 2: Run full test suite one final time**

```bash
cd examples/ai-news-digest
pytest tests/ -v --tb=short
```

Expected: `19 passed, 0 failed`

- [ ] **Step 3: Final commit**

```bash
git add examples/ai-news-digest/README.md
git commit -m "docs(ai-news-digest): README with setup guide and known limitations"
```

---

## Self-review checklist

- [x] **Spec §3.1 MCP tools** → Tasks 2-6 cover all four tools with full code
- [x] **Spec §3.2 agent.yaml** → Task 7 Step 1, exact YAML provided
- [x] **Spec §3.2 root_agent.yaml** → Task 7 Step 2, full YAML with instruction
- [x] **Spec §4 data flow** → agent.py `_run_digest()` implements prompt→tool→send chain
- [x] **Spec §5 error handling** → Each tool returns `[]` on network error, `send_email` raises on missing env vars
- [x] **Spec §6 scheduling** → `--once` / `--schedule` in agent.py `__main__`
- [x] **Spec §7 config** → All vars in `.env.example` (Task 1 Step 2) and `agent.yaml` env_vars
- [x] **Spec §8 testing** → 14 unit + 5 integration = 19 tests, all with exact code
- [x] **Type consistency** → `fetch_hackernews/arxiv/rss` return `list[dict]`, `send_email` returns `dict`, consistent across impl.py, server.py, agent.py
- [x] **No placeholders** — every step has complete code
