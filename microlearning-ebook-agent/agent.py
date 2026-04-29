"""microlearning-ebook-agent — Google ADK agent.

Takes a user-supplied topic, researches it via the generic web-search tool, and
produces a structured microlearning ebook (intro / lessons / quizzes / summary).
The synthesis is done by the model in its instruction-driven response. Tools
are resolved from the AgentBreeder tool registry — they are not domain-specific
to this agent:

  - tools/web-search       -> engine.tools.standard.web_search (Tavily)
  - tools/markdown-writer  -> engine.tools.standard.markdown_writer

The system prompt is also resolved from the prompt registry:

  - prompts/microlearning-system  -> ./prompts/microlearning-system.md

The AgentBreeder runtime wrapper (engine/runtimes/templates/google_adk_server.py)
loads ``root_agent`` from this file at startup.

Run interactively:
    adk run

Run via the AgentBreeder runtime wrapper (matches production):
    bash scripts/serve.sh
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import Agent

# --- Resolve prompt + tools from the registries -----------------------------

try:
    from engine.prompt_resolver import resolve_prompt
except ImportError:  # pragma: no cover -- file-only fallback when engine pkg unavailable
    from pathlib import Path as _Path

    def resolve_prompt(value: str, project_root: _Path | str | None = None) -> str:
        if not value.startswith("prompts/"):
            return value
        name = value[len("prompts/") :].split("@", 1)[0]
        root = _Path(project_root) if project_root else _Path.cwd()
        candidate = root / "prompts" / f"{name}.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
        raise LookupError(f"Prompt ref '{value}' not found at {candidate}")


try:
    from engine.tool_resolver import resolve_tool
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "engine.tool_resolver is required. Install the agentbreeder package: "
        "pip install -e <agentbreeder-repo-root>"
    ) from exc


_PROJECT_ROOT = Path(__file__).resolve().parent

INSTRUCTION = resolve_prompt("prompts/microlearning-system", project_root=_PROJECT_ROOT)
web_search = resolve_tool("tools/web-search", project_root=_PROJECT_ROOT)
markdown_writer = resolve_tool("tools/markdown-writer", project_root=_PROJECT_ROOT)


root_agent = Agent(
    name="microlearning_ebook_agent",
    model="gemini-2.5-flash",
    description=(
        "Turns a user-supplied topic into a structured microlearning ebook "
        "(intro, lessons, quizzes, summary) for corporate L&D teams."
    ),
    instruction=INSTRUCTION,
    tools=[web_search, markdown_writer],
)


if __name__ == "__main__":
    print(f"Agent: {root_agent.name}")
    print(f"Model: {root_agent.model}")
    print(f"Tools: {[t.__name__ for t in root_agent.tools]}")
    print("Run `adk run` (interactive) or start the AgentBreeder runtime to chat.")
