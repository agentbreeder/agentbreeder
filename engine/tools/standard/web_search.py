"""Generic web search tool backed by the Tavily API.

Use cases: research synthesis, fact lookup, citation gathering, current-events
context. Domain-agnostic — works for any agent that needs authoritative web
sources.

Required env: ``TAVILY_API_KEY``.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

TAVILY_URL = "https://api.tavily.com/search"

# JSON-Schema (OpenAPI subset) describing the tool's parameters. Stored in the
# registry so the dashboard / agent builders can render a typed editor for it.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query. Be specific.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of sources to return (1-10).",
            "default": 6,
            "minimum": 1,
            "maximum": 10,
        },
        "search_depth": {
            "type": "string",
            "description": "'basic' (fast) or 'advanced' (more thorough, slower).",
            "enum": ["basic", "advanced"],
            "default": "advanced",
        },
    },
    "required": ["query"],
}


def web_search(
    query: str,
    max_results: int = 6,
    search_depth: str = "advanced",
) -> dict[str, Any]:
    """Search the web and return authoritative sources.

    Args:
        query: The search query.
        max_results: Maximum number of sources to return (Tavily caps at 10).
        search_depth: ``"basic"`` (fast) or ``"advanced"`` (thorough).

    Returns:
        A dict with keys:
            sources: list of {url, title, snippet, score}
            answer:  Tavily's distilled one-paragraph synthesized answer.
            query:   the canonical query that was run.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY is not set. Add it to .env or export it before running."
        )

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "include_answer": True,
        "max_results": min(max(max_results, 1), 10),
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TAVILY_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

    sources = [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": r.get("content", ""),
            "score": r.get("score"),
        }
        for r in data.get("results", [])
    ]

    return {
        "sources": sources,
        "answer": data.get("answer", ""),
        "query": query,
    }
