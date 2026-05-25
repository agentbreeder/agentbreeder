"""Conversational agent builder — drives a Claude conversation that ends by emitting
a schema-validated agent.yaml via Anthropic tool-use.

The BYO API key is NEVER stored here.  The caller (API route) reads the key from the
workspace secrets backend, constructs a fresh AnthropicProvider, and injects it.
This module is intentionally pure / side-effect-free (no secrets access, no logging
of sensitive data).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml as pyyaml

from engine.providers.models import ToolDefinition, ToolFunction

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent JSON Schema (loaded once — used as the tool's input_schema so Claude
# must emit a structurally valid spec before the tool-use can be accepted).
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent / "schema" / "agent.schema.json"


def _load_agent_schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return data


_AGENT_SCHEMA: dict[str, Any] = _load_agent_schema()

# ---------------------------------------------------------------------------
# submit_agent_spec tool — Anthropic tool-use forces structured output.
# The input_schema mirrors agent.schema.json so invalid fields are rejected
# at the API level before we even see the result.
# ---------------------------------------------------------------------------

SUBMIT_TOOL_NAME = "submit_agent_spec"

_SUBMIT_TOOL_DEFINITION = ToolDefinition(
    type="function",
    function=ToolFunction(
        name=SUBMIT_TOOL_NAME,
        description=(
            "Submit the finished agent specification. Call this tool once you have "
            "gathered all required fields from the user. The input must conform to the "
            "AgentBreeder agent.yaml schema."
        ),
        parameters=_AGENT_SCHEMA,
    ),
)

# Public accessor so tests can inspect the exact tool definition.
SUBMIT_TOOL: ToolDefinition = _SUBMIT_TOOL_DEFINITION

# ---------------------------------------------------------------------------
# System prompt (large + static — marked for prompt caching).
# We insert it as the first message with cache_control in the payload.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are AgentBreeder's conversational agent builder. Your job is to help the user \
create a valid agent.yaml configuration by asking just the right questions.

## Rules
1. Keep the conversation short — 3–5 turns maximum. Ask only what you truly need.
2. Required fields you MUST collect: name (slug, e.g. "my-agent"), a brief description \
   of the agent's goal, the framework (langgraph | crewai | claude_sdk | openai_agents | \
   google_adk | custom), the primary model (e.g. claude-sonnet-4-6 or gpt-4o), and the \
   deploy cloud (aws | gcp | azure | kubernetes | local).
3. Fill in sensible defaults for everything else: version "1.0.0", team "default", \
   owner "owner@example.com" (unless the user provided a team/owner), min replicas 1 \
   max 3.
4. If you have received a recommendation hint (JSON in the <hint> tag), use it as the \
   preferred framework and deploy target — no need to ask again unless the user disagrees.
5. Once you have all required fields, immediately call the `submit_agent_spec` tool — do \
   NOT ask for confirmation, do NOT emit YAML as plain text.
6. Never reveal or repeat the user's API key or any secret. Never embed secrets in the spec.
7. Be concise and friendly.
"""

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ChatTurnResult:
    """Result of a single run_chat_turn() call."""

    assistant_message: str
    """The assistant's text reply (empty string when it went straight to a tool call)."""

    agent_yaml: str | None = None
    """Set when Claude called submit_agent_spec and the result serialised successfully."""

    valid: bool = False
    """True when agent_yaml is set AND passed validate_config_yaml()."""

    errors: list[str] = field(default_factory=list)
    """Validation error messages when valid is False and agent_yaml is set."""


# ---------------------------------------------------------------------------
# Core conversation driver
# ---------------------------------------------------------------------------

# Maximum number of messages we'll send to the model in a single turn.
# The API layer enforces this at the request boundary; we defensively re-check.
MAX_HISTORY_MESSAGES = 40


async def run_chat_turn(
    provider: Any,
    history: list[dict[str, Any]],
    *,
    recommend_hint: dict[str, Any] | None = None,
) -> ChatTurnResult:
    """Drive one turn of the agent-builder conversation.

    Args:
        provider:         An AnthropicProvider instance (injected — not constructed here).
                          The key is already embedded in the provider's httpx client;
                          this function never reads, logs, or returns it.
        history:          The full conversation so far as OpenAI-format dicts
                          (role + content). Must not exceed MAX_HISTORY_MESSAGES.
        recommend_hint:   Optional dict from the engine's recommend() heuristics
                          (e.g. {"framework": "langgraph", "deploy_target": "aws"}).
                          Embedded in the system prompt so Claude can skip asking.

    Returns:
        ChatTurnResult — see dataclass docstring above.
    """
    if len(history) > MAX_HISTORY_MESSAGES:
        logger.warning(
            "run_chat_turn: history has %d messages (max %d); truncating to last %d",
            len(history),
            MAX_HISTORY_MESSAGES,
            MAX_HISTORY_MESSAGES,
        )
        history = history[-MAX_HISTORY_MESSAGES:]

    # Build system prompt (inject hint if present).
    system_prompt = _SYSTEM_PROMPT
    if recommend_hint:
        hint_json = json.dumps(recommend_hint, separators=(",", ":"))
        system_prompt = system_prompt + f"\n\n<hint>{hint_json}</hint>"

    # Build the full messages list with system first.
    # AnthropicProvider._build_payload() extracts the system message.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *history,
    ]

    # Call the provider — never log the key (it's inside the provider's client).
    result = await provider.generate(
        messages=messages,
        model="claude-sonnet-4-6",
        max_tokens=2048,
        tools=[SUBMIT_TOOL],
    )

    # ── Case A: model called submit_agent_spec ───────────────────────────
    for tool_call in result.tool_calls:
        if tool_call.function_name == SUBMIT_TOOL_NAME:
            return _handle_spec_submission(tool_call.function_arguments)

    # ── Case B: plain text reply ─────────────────────────────────────────
    return ChatTurnResult(
        assistant_message=result.content or "",
        agent_yaml=None,
        valid=False,
        errors=[],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _handle_spec_submission(arguments_json: str) -> ChatTurnResult:
    """Parse the tool-use arguments, serialise to YAML, validate, return result.

    The arguments_json string is model-generated (untrusted) — we validate it
    fully before returning valid=True.
    """
    # 1. Parse JSON arguments (model output — may be malformed).
    try:
        spec_dict = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        logger.warning("submit_agent_spec: JSON decode failed: %s", exc)
        return ChatTurnResult(
            assistant_message="",
            agent_yaml=None,
            valid=False,
            errors=[f"Could not parse spec arguments as JSON: {exc}"],
        )

    if not isinstance(spec_dict, dict):
        return ChatTurnResult(
            assistant_message="",
            agent_yaml=None,
            valid=False,
            errors=["Spec must be a JSON object"],
        )

    # 2. Serialise to YAML.
    try:
        agent_yaml = pyyaml.safe_dump(spec_dict, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        logger.warning("submit_agent_spec: YAML serialisation failed: %s", exc)
        return ChatTurnResult(
            assistant_message="",
            agent_yaml=None,
            valid=False,
            errors=[f"YAML serialisation failed: {exc}"],
        )

    # 3. Re-validate with the registry validator (model output is untrusted).
    from registry.agents import validate_config_yaml  # imported here to avoid circular deps

    validation = validate_config_yaml(agent_yaml)

    error_messages = [f"{e.path}: {e.message}" for e in validation.errors]

    return ChatTurnResult(
        assistant_message="",
        agent_yaml=agent_yaml,
        valid=validation.valid,
        errors=error_messages,
    )
