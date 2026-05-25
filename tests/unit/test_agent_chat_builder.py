"""Unit tests for engine/agent_chat_builder.py.

The AnthropicProvider is mocked throughout — no real network calls, no real API key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.agent_chat_builder import (
    SUBMIT_TOOL,
    SUBMIT_TOOL_NAME,
    ChatTurnResult,
    _AGENT_SCHEMA,
    _SYSTEM_PROMPT,
    run_chat_turn,
)
from engine.providers.models import GenerateResult, ToolCall, UsageInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SPEC: dict[str, Any] = {
    "name": "my-agent",
    "version": "1.0.0",
    "team": "engineering",
    "owner": "alice@example.com",
    "framework": "langgraph",
    "model": {"primary": "claude-sonnet-4-6"},
    "deploy": {"cloud": "aws"},
}


def _text_result(text: str) -> GenerateResult:
    """Fake GenerateResult for a plain-text response."""
    return GenerateResult(
        content=text,
        tool_calls=[],
        finish_reason="stop",
        usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="claude-sonnet-4-6",
        provider="anthropic",
    )


def _tool_use_result(spec: dict[str, Any]) -> GenerateResult:
    """Fake GenerateResult for a submit_agent_spec tool-use call."""
    return GenerateResult(
        content=None,
        tool_calls=[
            ToolCall(
                id="tool_abc123",
                function_name=SUBMIT_TOOL_NAME,
                function_arguments=json.dumps(spec),
            )
        ],
        finish_reason="tool_calls",
        usage=UsageInfo(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        model="claude-sonnet-4-6",
        provider="anthropic",
    )


def _make_provider(result: GenerateResult) -> AsyncMock:
    """Return a mock provider whose generate() coroutine returns *result*."""
    provider = AsyncMock()
    provider.generate = AsyncMock(return_value=result)
    return provider


# ---------------------------------------------------------------------------
# Tests: basic contracts
# ---------------------------------------------------------------------------


class TestChatTurnResultShape:
    def test_defaults(self) -> None:
        r = ChatTurnResult(assistant_message="hello")
        assert r.agent_yaml is None
        assert r.valid is False
        assert r.errors == []

    def test_with_spec(self) -> None:
        r = ChatTurnResult(assistant_message="", agent_yaml="name: x\n", valid=True)
        assert r.valid is True
        assert r.agent_yaml is not None


# ---------------------------------------------------------------------------
# Tests: tool definition
# ---------------------------------------------------------------------------


class TestSubmitTool:
    def test_tool_name(self) -> None:
        assert SUBMIT_TOOL.function.name == SUBMIT_TOOL_NAME

    def test_tool_schema_matches_agent_schema(self) -> None:
        """The tool's input_schema must be exactly the agent JSON schema."""
        assert SUBMIT_TOOL.function.parameters == _AGENT_SCHEMA

    def test_agent_schema_loaded(self) -> None:
        """The schema must have the expected top-level required fields."""
        required = _AGENT_SCHEMA.get("required", [])
        assert "name" in required
        assert "version" in required
        assert "team" in required
        assert "owner" in required

    def test_tool_has_description(self) -> None:
        assert SUBMIT_TOOL.function.description != ""


# ---------------------------------------------------------------------------
# Tests: system prompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_mentions_required_fields(self) -> None:
        prompt = _SYSTEM_PROMPT.lower()
        assert "name" in prompt
        assert "framework" in prompt
        assert "model" in prompt
        assert "cloud" in prompt or "deploy" in prompt

    def test_system_prompt_not_empty(self) -> None:
        assert len(_SYSTEM_PROMPT) > 100


# ---------------------------------------------------------------------------
# Tests: run_chat_turn — text-only response
# ---------------------------------------------------------------------------


class TestRunChatTurnTextTurn:
    @pytest.mark.asyncio
    async def test_text_turn_returns_message(self) -> None:
        """A plain-text reply → assistant_message set, agent_yaml is None."""
        provider = _make_provider(_text_result("What framework do you want to use?"))
        history = [{"role": "user", "content": "I want to build a support agent"}]

        result = await run_chat_turn(provider, history)

        assert isinstance(result, ChatTurnResult)
        assert result.assistant_message == "What framework do you want to use?"
        assert result.agent_yaml is None
        assert result.valid is False
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_provider_called_with_tools(self) -> None:
        """generate() must be called with the submit_agent_spec tool."""
        provider = _make_provider(_text_result("Ok!"))
        history = [{"role": "user", "content": "hello"}]

        await run_chat_turn(provider, history)

        provider.generate.assert_called_once()
        call_kwargs = provider.generate.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs.args[3] if len(call_kwargs.args) > 3 else call_kwargs.kwargs.get("tools")
        # tools may be positional or keyword
        all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        # Flatten: find the tools list
        passed_tools = call_kwargs.kwargs.get("tools")
        assert passed_tools is not None, "tools must be passed to generate()"
        assert len(passed_tools) == 1
        assert passed_tools[0].function.name == SUBMIT_TOOL_NAME

    @pytest.mark.asyncio
    async def test_system_prompt_included(self) -> None:
        """The system message must be present in the messages sent to the provider."""
        provider = _make_provider(_text_result("Sure!"))
        history = [{"role": "user", "content": "hello"}]

        await run_chat_turn(provider, history)

        call_kwargs = provider.generate.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        system_msgs = [m for m in messages if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert len(system_msgs[0]["content"]) > 50

    @pytest.mark.asyncio
    async def test_recommend_hint_injected(self) -> None:
        """When a recommend_hint is supplied, it should appear in the system message."""
        provider = _make_provider(_text_result("Got it!"))
        history = [{"role": "user", "content": "hello"}]
        hint = {"framework": "langgraph", "deploy_target": "aws"}

        await run_chat_turn(provider, history, recommend_hint=hint)

        call_kwargs = provider.generate.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        system_msgs = [m for m in messages if m.get("role") == "system"]
        assert "langgraph" in system_msgs[0]["content"]
        assert "aws" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_model_is_claude_sonnet(self) -> None:
        """Must call generate() with model=claude-sonnet-4-6."""
        provider = _make_provider(_text_result("Hello"))
        await run_chat_turn(provider, [{"role": "user", "content": "hi"}])

        call_kwargs = provider.generate.call_args
        model = call_kwargs.kwargs.get("model")
        assert model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Tests: run_chat_turn — tool-use with valid spec
# ---------------------------------------------------------------------------


class TestRunChatTurnValidSpec:
    @pytest.mark.asyncio
    async def test_valid_spec_sets_agent_yaml(self) -> None:
        """A submit_agent_spec tool-use with valid data → agent_yaml set + valid True."""
        provider = _make_provider(_tool_use_result(VALID_SPEC))
        history = [
            {"role": "user", "content": "I want a support agent"},
            {"role": "assistant", "content": "Ok, which cloud?"},
            {"role": "user", "content": "AWS"},
        ]

        result = await run_chat_turn(provider, history)

        assert result.agent_yaml is not None
        assert "my-agent" in result.agent_yaml
        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_valid_spec_assistant_message_empty(self) -> None:
        """When the tool fires, assistant_message should be empty (no text block)."""
        provider = _make_provider(_tool_use_result(VALID_SPEC))
        result = await run_chat_turn(provider, [{"role": "user", "content": "go"}])
        assert result.assistant_message == ""

    @pytest.mark.asyncio
    async def test_agent_yaml_is_valid_yaml(self) -> None:
        """The returned agent_yaml must be parseable YAML."""
        import yaml

        provider = _make_provider(_tool_use_result(VALID_SPEC))
        result = await run_chat_turn(provider, [{"role": "user", "content": "go"}])
        assert result.agent_yaml is not None
        parsed = yaml.safe_load(result.agent_yaml)
        assert parsed["name"] == "my-agent"


# ---------------------------------------------------------------------------
# Tests: run_chat_turn — tool-use with invalid spec
# ---------------------------------------------------------------------------


class TestRunChatTurnInvalidSpec:
    @pytest.mark.asyncio
    async def test_missing_required_field_sets_valid_false(self) -> None:
        """A spec missing required fields → valid False, errors non-empty."""
        bad_spec = {
            "name": "my-agent",
            # missing: version, team, owner, model, deploy
        }
        provider = _make_provider(_tool_use_result(bad_spec))
        result = await run_chat_turn(provider, [{"role": "user", "content": "go"}])

        assert result.valid is False
        assert len(result.errors) > 0
        # agent_yaml should still be set (we want to show what was generated)
        assert result.agent_yaml is not None

    @pytest.mark.asyncio
    async def test_empty_spec_is_invalid(self) -> None:
        """An empty dict spec is invalid."""
        provider = _make_provider(_tool_use_result({}))
        result = await run_chat_turn(provider, [{"role": "user", "content": "go"}])
        assert result.valid is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_malformed_arguments_json(self) -> None:
        """If the tool call's function_arguments is not valid JSON, return errors."""
        result_with_bad_args = GenerateResult(
            content=None,
            tool_calls=[
                ToolCall(
                    id="tool_bad",
                    function_name=SUBMIT_TOOL_NAME,
                    function_arguments="NOT_JSON{{{{",
                )
            ],
            finish_reason="tool_calls",
            model="claude-sonnet-4-6",
            provider="anthropic",
        )
        provider = _make_provider(result_with_bad_args)
        result = await run_chat_turn(provider, [{"role": "user", "content": "go"}])

        assert result.valid is False
        assert result.agent_yaml is None
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Tests: history truncation
# ---------------------------------------------------------------------------


class TestHistoryTruncation:
    @pytest.mark.asyncio
    async def test_oversized_history_is_truncated(self) -> None:
        """Histories longer than MAX_HISTORY_MESSAGES should be silently truncated."""
        from engine.agent_chat_builder import MAX_HISTORY_MESSAGES

        provider = _make_provider(_text_result("ok"))
        long_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(MAX_HISTORY_MESSAGES + 10)
        ]

        # Should not raise
        result = await run_chat_turn(provider, long_history)
        assert isinstance(result, ChatTurnResult)

        # The messages passed to generate() must include system + at most MAX_HISTORY_MESSAGES user/assistant messages
        call_kwargs = provider.generate.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        non_system = [m for m in messages if m.get("role") != "system"]
        assert len(non_system) <= MAX_HISTORY_MESSAGES
