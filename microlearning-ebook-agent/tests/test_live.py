"""Live tests for microlearning-ebook-agent.

These tests make REAL external API calls and require:
  - TAVILY_API_KEY for web research
  - GOOGLE_API_KEY for Gemini

Skipped automatically if either key is missing. Run with:
    pytest tests/test_live.py -v -m live
"""

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _load_dotenv():
    """Load .env so the keys are available even when running pytest barefoot."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _has(key: str) -> bool:
    return bool(os.getenv(key))


@pytest.mark.skipif(not _has("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
def test_web_search_returns_real_sources():
    """Verifies Tavily integration via the registry-resolved web_search tool."""
    from engine.tools.standard.web_search import web_search

    result = web_search("Zero Trust networking architecture", max_results=3)
    assert isinstance(result, dict)
    assert "sources" in result
    assert len(result["sources"]) >= 1
    first = result["sources"][0]
    assert first["url"].startswith("http")
    assert first["title"]
    assert first["snippet"]


@pytest.mark.skipif(
    not (_has("TAVILY_API_KEY") and _has("GOOGLE_API_KEY")),
    reason="TAVILY_API_KEY and GOOGLE_API_KEY required for end-to-end agent run",
)
def test_agent_end_to_end_via_runner():
    """Run the agent through ADK's InMemoryRunner with a real Gemini call.

    This is the closest local equivalent to the production /invoke endpoint.
    """
    import asyncio

    from agent import root_agent
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types

    async def _run() -> str:
        runner = InMemoryRunner(agent=root_agent, app_name="microlearning-test")
        session = await runner.session_service.create_session(
            app_name="microlearning-test", user_id="test-user"
        )
        msg = genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    text=(
                        "Just say 'pipeline ready' if you understand your role. "
                        "Do not call any tools yet."
                    )
                )
            ],
        )
        out = ""
        async for event in runner.run_async(
            user_id="test-user", session_id=session.id, new_message=msg
        ):
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    text = getattr(part, "text", None)
                    if text:
                        out += text
        return out

    response = asyncio.run(_run())
    assert response, "Agent returned empty response"
    assert "pipeline ready" in response.lower() or len(response) > 10
