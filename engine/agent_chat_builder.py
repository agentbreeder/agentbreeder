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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml as pyyaml

from engine.providers.anthropic_provider import DEFAULT_MODEL
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
# request_setup tool — lets the interviewer ask the user for a dependency
# (a secret/API key, a model-provider key, or an MCP server) BEFORE finishing
# the spec. The frontend renders an inline card; once satisfied, the user
# confirms and the model references the dependency by name in the final spec.
# Values never flow through this module — only the *request* for one.
# ---------------------------------------------------------------------------

REQUEST_SETUP_TOOL_NAME = "request_setup"

_REQUEST_SETUP_TOOL_DEFINITION = ToolDefinition(
    type="function",
    function=ToolFunction(
        name=REQUEST_SETUP_TOOL_NAME,
        description=(
            "Request that the user provide a dependency the agent needs before the spec "
            "can be finished: an API key/secret (kind='secret'), a model-provider key "
            "(kind='provider'), or an MCP server (kind='mcp'). Call this BEFORE "
            "submit_agent_spec whenever the agent requires a credential or tool you cannot "
            "supply yourself. After the user confirms it is connected, reference it by name "
            "in the spec (secrets / mcp_servers / tools) and then call submit_agent_spec."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["secret", "mcp", "provider"],
                    "description": "The kind of dependency to collect.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "The dependency name the spec will reference — e.g. "
                        "'ZENDESK_API_KEY' (secret), 'openai' (provider), 'zendesk' (mcp)."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "A short, friendly explanation shown to the user on the card.",
                },
            },
            "required": ["kind", "name"],
        },
    ),
)

# Public accessor so tests / callers can inspect the exact tool definition.
REQUEST_SETUP_TOOL: ToolDefinition = _REQUEST_SETUP_TOOL_DEFINITION

# Both tools are offered on every turn so the model can either finish the spec
# or pause to collect a dependency.
_BUILDER_TOOLS = [SUBMIT_TOOL, REQUEST_SETUP_TOOL]

_VALID_SETUP_KINDS = {"secret", "mcp", "provider"}

# ---------------------------------------------------------------------------
# System prompt (static). The large, static part of each turn is the
# submit_agent_spec tool (its input_schema is the full agent.schema.json);
# AnthropicProvider auto-caches it via cache_control, so it is not reprocessed
# on every turn.
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
4. If the agent needs a dependency you cannot supply yourself — an API key/secret, a \
   model-provider key, or an MCP server/tool — call the `request_setup` tool BEFORE \
   submitting the spec. The user will be shown a secure inline card to provide it. Once \
   they confirm it is connected, reference it by name in the spec (under `secrets`, \
   `mcp_servers`, or `tools`) and then submit. Request only what the agent truly needs.
5. Once you have all required fields and any needed dependencies are connected, \
   immediately call the `submit_agent_spec` tool — do NOT ask for confirmation, do NOT \
   emit YAML as plain text.
6. Never reveal or repeat the user's API key or any secret. Never embed secret values in \
   the spec — only references (names).
7. Be concise and friendly.
"""

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SetupRequest:
    """A request for the user to provide a dependency before the spec can finish.

    Emitted when the model calls the `request_setup` tool. The frontend renders an
    inline card; values are captured straight to the secrets / MCP backends and the
    spec records only a reference (name) — never the value.
    """

    kind: Literal["secret", "mcp", "provider"]
    name: str
    reason: str = ""


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

    setup_request: SetupRequest | None = None
    """Set when the model called request_setup instead of submitting the spec."""


@dataclass
class ChatStreamEvent:
    """One event from run_chat_turn_stream().

    type == "token":         `text` carries an incremental assistant text fragment.
    type == "setup_request": `setup` carries a dependency the user must provide.
    type == "done":          `result` carries the final ChatTurnResult.
    """

    type: Literal["token", "setup_request", "done"]
    text: str = ""
    result: ChatTurnResult | None = None
    setup: SetupRequest | None = None


# ---------------------------------------------------------------------------
# Core conversation driver
# ---------------------------------------------------------------------------

# Maximum number of messages we'll send to the model in a single turn.
# The API layer enforces this at the request boundary; we defensively re-check.
MAX_HISTORY_MESSAGES = 40


def _normalise_history(history: list[dict[str, Any]], caller: str) -> list[dict[str, Any]]:
    """Truncate to MAX_HISTORY_MESSAGES and drop non user/assistant roles, logging both."""
    if len(history) > MAX_HISTORY_MESSAGES:
        logger.warning(
            "%s: history has %d messages (max %d); truncating to last %d",
            caller,
            len(history),
            MAX_HISTORY_MESSAGES,
            MAX_HISTORY_MESSAGES,
        )
        history = history[-MAX_HISTORY_MESSAGES:]
    allowed_roles = {"user", "assistant"}
    filtered = [m for m in history if m.get("role") in allowed_roles]
    if len(filtered) != len(history):
        logger.warning(
            "%s: dropped %d message(s) with invalid role", caller, len(history) - len(filtered)
        )
    return filtered


async def run_chat_turn(
    provider: Any,
    history: list[dict[str, Any]],
) -> ChatTurnResult:
    """Drive one turn of the agent-builder conversation.

    Args:
        provider:  An AnthropicProvider instance (injected — not constructed here).
                   The key is already embedded in the provider's httpx client;
                   this function never reads, logs, or returns it.
        history:   The full conversation so far as OpenAI-format dicts
                   (role + content). Roles must be "user" or "assistant" only
                   (enforced at the API layer by _ChatMessage). Must not exceed
                   MAX_HISTORY_MESSAGES entries.

    Returns:
        ChatTurnResult — see dataclass docstring above.
    """
    # Defensively normalise history: truncate and filter roles.
    # Belt-and-suspenders guard; the API layer already rejects other
    # roles via Pydantic Literal["user", "assistant"].
    history = _normalise_history(history, "run_chat_turn")

    # Build the full messages list with system first.
    # AnthropicProvider._build_payload() extracts the system message.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *history,
    ]

    # Call the provider — never log the key (it's inside the provider's client).
    result = await provider.generate(
        messages=messages,
        model=DEFAULT_MODEL,
        max_tokens=2048,
        tools=_BUILDER_TOOLS,
    )

    # ── Case A: model called submit_agent_spec (always wins) ─────────────
    for tool_call in result.tool_calls:
        if tool_call.function_name == SUBMIT_TOOL_NAME:
            return _handle_spec_submission(tool_call.function_arguments)

    # ── Case B: model called request_setup ───────────────────────────────
    for tool_call in result.tool_calls:
        if tool_call.function_name == REQUEST_SETUP_TOOL_NAME:
            setup = _parse_setup_request(tool_call.function_arguments)
            if setup is not None:
                return ChatTurnResult(
                    assistant_message=result.content or "",
                    agent_yaml=None,
                    valid=False,
                    errors=[],
                    setup_request=setup,
                )

    # ── Case C: plain text reply ─────────────────────────────────────────
    return ChatTurnResult(
        assistant_message=result.content or "",
        agent_yaml=None,
        valid=False,
        errors=[],
    )


async def run_chat_turn_stream(
    provider: Any,
    history: list[dict[str, Any]],
) -> AsyncIterator[ChatStreamEvent]:
    """Streaming variant of run_chat_turn().

    Yields ChatStreamEvent(type="token") for each text fragment as it arrives,
    then exactly one ChatStreamEvent(type="done") with the final ChatTurnResult.
    Security contract is identical to run_chat_turn(): the key lives inside the
    injected provider and is never read, logged, or returned here.
    """
    history = _normalise_history(history, "run_chat_turn_stream")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *history,
    ]

    text_parts: list[str] = []
    submit_args: str | None = None
    setup_args: str | None = None

    async for chunk in provider.generate_stream(
        messages=messages,
        model=DEFAULT_MODEL,
        max_tokens=2048,
        tools=_BUILDER_TOOLS,
    ):
        if chunk.content:
            text_parts.append(chunk.content)
            yield ChatStreamEvent(type="token", text=chunk.content)
        if chunk.tool_calls:
            for tc in chunk.tool_calls:
                if not tc.function_arguments:
                    continue
                if tc.function_name == SUBMIT_TOOL_NAME and submit_args is None:
                    submit_args = tc.function_arguments
                elif tc.function_name == REQUEST_SETUP_TOOL_NAME and setup_args is None:
                    setup_args = tc.function_arguments

    # A real spec submission always wins over a setup request in the same turn.
    if submit_args is not None:
        result = _handle_spec_submission(submit_args)
    elif setup_args is not None and (setup := _parse_setup_request(setup_args)) is not None:
        yield ChatStreamEvent(type="setup_request", setup=setup)
        result = ChatTurnResult(
            assistant_message="".join(text_parts),
            agent_yaml=None,
            valid=False,
            errors=[],
            setup_request=setup,
        )
    else:
        result = ChatTurnResult(
            assistant_message="".join(text_parts),
            agent_yaml=None,
            valid=False,
            errors=[],
        )
    yield ChatStreamEvent(type="done", result=result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_setup_request(arguments_json: str) -> SetupRequest | None:
    """Parse request_setup tool arguments into a SetupRequest.

    The arguments are model-generated (untrusted). Anything malformed — bad JSON,
    missing fields, or an unrecognised kind — degrades to None (logged) so the turn
    falls back to a plain text reply rather than raising.
    """
    try:
        data = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        logger.warning("request_setup: JSON decode failed: %s", exc)
        return None

    if not isinstance(data, dict):
        logger.warning("request_setup: arguments were not a JSON object")
        return None

    kind = data.get("kind")
    name = data.get("name")
    if kind not in _VALID_SETUP_KINDS or not isinstance(name, str) or not name.strip():
        logger.warning("request_setup: invalid kind/name (kind=%r)", kind)
        return None

    reason = data.get("reason")
    return SetupRequest(
        kind=kind,
        name=name.strip(),
        reason=reason.strip() if isinstance(reason, str) else "",
    )


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
