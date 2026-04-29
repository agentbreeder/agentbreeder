"""Interactive smoke chat against the local agent.

Usage:
    set -a; source .env; set +a
    python scripts/chat.py "Create a microlearning ebook on Zero Trust networking."

The script runs the same code path the production /invoke endpoint uses
(the AgentBreeder runtime wraps Runner + InMemorySessionService the same way).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow `from agent import root_agent` when running from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

load_dotenv()


async def chat(prompt: str) -> None:
    from agent import root_agent

    runner = InMemoryRunner(agent=root_agent, app_name="microlearning-local")
    session = await runner.session_service.create_session(
        app_name="microlearning-local", user_id="local-user"
    )
    msg = genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])

    print(f"\n>>> USER: {prompt}\n")
    print(">>> AGENT:")
    final_text = ""
    async for event in runner.run_async(
        user_id="local-user", session_id=session.id, new_message=msg
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                fc = getattr(part, "function_call", None)
                fr = getattr(part, "function_response", None)
                text = getattr(part, "text", None)
                if fc:
                    print(f"[tool call] {fc.name}({dict(fc.args) if fc.args else {}})")
                elif fr:
                    resp_keys = list(fr.response.keys()) if fr.response else []
                    print(f"[tool result] {fr.name} -> keys={resp_keys}")
                elif text:
                    final_text += text
        if event.is_final_response():
            break
    print(final_text or "[no text]")


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or (
        "Create a short microlearning ebook on the basics of Zero Trust networking. "
        "Limit research to 3 sources to save quota. After drafting, render it as markdown."
    )
    asyncio.run(chat(prompt))
