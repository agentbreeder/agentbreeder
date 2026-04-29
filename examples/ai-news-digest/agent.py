"""Google ADK agent for the AI news digest.

Demonstrates the **registry pattern** with multiple tools:
  - System prompt resolved from prompts/ai-news-digest-system.md
  - Each of the four tools resolved via local override files in tools/
    (fetch_hackernews.py, fetch_arxiv.py, fetch_rss.py, send_email.py).
    Each thin file delegates to tools/impl.py for the actual logic.

Exports `root_agent` — picked up by AgentBreeder's server wrapper at runtime.

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
from pathlib import Path

import schedule
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm as LiteLlmModel

try:
    from engine.prompt_resolver import resolve_prompt
    from engine.tool_resolver import resolve_tool
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "engine.prompt_resolver and engine.tool_resolver are required. "
        "Install the agentbreeder package: pip install -e <agentbreeder-repo>"
    ) from exc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent

INSTRUCTION = resolve_prompt("prompts/ai-news-digest-system", project_root=_PROJECT_ROOT)
fetch_hackernews = resolve_tool("tools/fetch-hackernews", project_root=_PROJECT_ROOT)
fetch_arxiv = resolve_tool("tools/fetch-arxiv", project_root=_PROJECT_ROOT)
fetch_rss = resolve_tool("tools/fetch-rss", project_root=_PROJECT_ROOT)
send_email = resolve_tool("tools/send-email", project_root=_PROJECT_ROOT)

root_agent = Agent(
    name="ai_news_digest",
    model=LiteLlmModel(
        model=os.environ.get("AGENT_MODEL", "ollama/gemma3:27b"),
        api_base=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    ),
    description="Daily AI news digest — HN + ArXiv + RSS, emailed via Gmail",
    instruction=INSTRUCTION,
    tools=[fetch_hackernews, fetch_arxiv, fetch_rss, send_email],
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
    group.add_argument(
        "--schedule", action="store_true", help="Run as daemon, fire daily at DIGEST_HOUR"
    )
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
