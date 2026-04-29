"""Google ADK example agent for AgentBreeder.

Demonstrates the **registry pattern** end-to-end:
  - Prompt resolved via ``engine.prompt_resolver`` from
    ``./prompts/gemini-assistant-system.md`` (or the registry API as fallback).
  - Tools resolved via ``engine.tool_resolver`` from ``./tools/get_current_time.py``
    (local override) — falls back to ``engine.tools.standard.<name>`` if the
    local file is missing.

The ``root_agent`` export is picked up by the AgentBreeder server wrapper at
runtime.

Push the prompt + tool + agent to the registry with:
    bash scripts/register.sh
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import Agent

try:
    from engine.prompt_resolver import resolve_prompt
    from engine.tool_resolver import resolve_tool
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "engine.prompt_resolver and engine.tool_resolver are required. "
        "Install the agentbreeder package: pip install -e <agentbreeder-repo>"
    ) from exc


_PROJECT_ROOT = Path(__file__).resolve().parent

INSTRUCTION = resolve_prompt("prompts/gemini-assistant-system", project_root=_PROJECT_ROOT)
get_current_time = resolve_tool("tools/get-current-time", project_root=_PROJECT_ROOT)


root_agent = Agent(
    name="gemini_assistant",
    model="gemini-2.5-flash",
    description="A helpful assistant that can answer questions and check the current time.",
    instruction=INSTRUCTION,
    tools=[get_current_time],
)
