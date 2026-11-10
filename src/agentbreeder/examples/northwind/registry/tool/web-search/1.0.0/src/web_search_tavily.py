"""Public web search through the operator's tools proxy (POST /tavily/search), which adds the
Tavily key. The agent never holds the key. Never use it to decide Northwind policy.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

OPENER = build_opener(ProxyHandler({}))


def _tavily(proxy: str, query: str) -> dict:
    request = Request(
        f"{proxy}/tavily/search",
        data=json.dumps(
            {"query": query, "search_depth": "basic", "max_results": 5, "include_answer": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with OPENER.open(request, timeout=60) as response:
        return json.load(response)


def web_search(rt, query: str) -> str:
    proxy = rt.services.get("tools_proxy_url") or ""
    if not proxy:
        raise ValueError("web search is not configured (no tools proxy)")
    try:
        data = _tavily(proxy, query[:500])
    except HTTPError as error:
        raise ValueError(f"web search failed (HTTP {error.code})") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise ValueError("web search unavailable") from error
    results = [r for r in data.get("results", []) if str(r.get("url", "")).startswith("http")]
    for r in results[:4]:
        rt.record("web", {"title": r.get("title") or r["url"], "url": r["url"]})
    for r in results:
        rt.evidence("web", r.get("title"), str(r.get("content", ""))[:600], url=r["url"])
    lines = [f"Summary: {data['answer']}"] if data.get("answer") else []
    lines += [
        f"- {r.get('title')} ({r['url']}): {str(r.get('content', ''))[:600]}" for r in results
    ]
    return "\n".join(lines) or "No results."
