"""Tests for Phase 6 Claude SDK advanced features:
adaptive thinking, prompt caching, provider routing, and version bump.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import (
    AgentConfig,
    ClaudeSDKConfig,
    ClaudeSDKRoutingConfig,
    ClaudeSDKThinkingConfig,
    FrameworkType,
)
from engine.runtimes.claude_sdk import ClaudeSDKRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.claude_sdk,
        "model": {"primary": "claude-sonnet-4-6"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_config_with_sdk(**sdk_kwargs: object) -> AgentConfig:
    return _make_config(claude_sdk=ClaudeSDKConfig(**sdk_kwargs))


# ---------------------------------------------------------------------------
# ClaudeSDKConfig Pydantic model
# ---------------------------------------------------------------------------

class TestClaudeSDKConfig:
    def test_defaults(self) -> None:
        cfg = ClaudeSDKConfig()
        assert cfg.thinking.enabled is False
        assert cfg.thinking.effort == "high"
        assert cfg.prompt_caching is False
        assert cfg.routing.provider == "anthropic"
        assert cfg.routing.project_id is None
        assert cfg.routing.region is None

    def test_thinking_enabled(self) -> None:
        cfg = ClaudeSDKConfig(thinking=ClaudeSDKThinkingConfig(enabled=True, effort="medium"))
        assert cfg.thinking.enabled is True
        assert cfg.thinking.effort == "medium"

    def test_routing_vertex_ai(self) -> None:
        cfg = ClaudeSDKConfig(
            routing=ClaudeSDKRoutingConfig(
                provider="vertex_ai",
                project_id="my-gcp-project",
                region="us-east5",
            )
        )
        assert cfg.routing.provider == "vertex_ai"
        assert cfg.routing.project_id == "my-gcp-project"
        assert cfg.routing.region == "us-east5"

    def test_routing_bedrock(self) -> None:
        cfg = ClaudeSDKConfig(
            routing=ClaudeSDKRoutingConfig(provider="bedrock", region="us-west-2")
        )
        assert cfg.routing.provider == "bedrock"
        assert cfg.routing.region == "us-west-2"

    def test_prompt_caching_flag(self) -> None:
        cfg = ClaudeSDKConfig(prompt_caching=True)
        assert cfg.prompt_caching is True

    def test_agent_config_accepts_claude_sdk_block(self) -> None:
        config = _make_config_with_sdk(
            thinking={"enabled": True, "effort": "high"},
            prompt_caching=True,
            routing={"provider": "vertex_ai", "project_id": "proj", "region": "us-east5"},
        )
        assert config.claude_sdk.thinking.enabled is True
        assert config.claude_sdk.prompt_caching is True
        assert config.claude_sdk.routing.provider == "vertex_ai"

    def test_agent_config_claude_sdk_defaults_when_omitted(self) -> None:
        config = _make_config()
        assert config.claude_sdk.thinking.enabled is False
        assert config.claude_sdk.prompt_caching is False
        assert config.claude_sdk.routing.provider == "anthropic"


# ---------------------------------------------------------------------------
# _build_env_block()
# ---------------------------------------------------------------------------

class TestBuildEnvBlock:
    def test_default_routing_vars(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        block = runtime._build_env_block(config)
        assert "ENV AGENT_ROUTING_PROVIDER=anthropic" in block

    def test_thinking_disabled_by_default(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        block = runtime._build_env_block(config)
        assert "ENV AGENT_THINKING_ENABLED=false" in block

    def test_thinking_enabled_writes_correct_vars(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config_with_sdk(
            thinking=ClaudeSDKThinkingConfig(enabled=True, effort="medium")
        )
        block = runtime._build_env_block(config)
        assert "ENV AGENT_THINKING_ENABLED=true" in block
        assert "ENV AGENT_THINKING_EFFORT=medium" in block

    def test_prompt_caching_enabled_writes_true(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config_with_sdk(prompt_caching=True)
        block = runtime._build_env_block(config)
        assert "ENV AGENT_PROMPT_CACHING=true" in block

    def test_prompt_caching_disabled_writes_false(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        block = runtime._build_env_block(config)
        assert "ENV AGENT_PROMPT_CACHING=false" in block

    def test_vertex_ai_routing_writes_project_and_region(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config_with_sdk(
            routing=ClaudeSDKRoutingConfig(
                provider="vertex_ai", project_id="my-project", region="us-east5"
            )
        )
        block = runtime._build_env_block(config)
        assert "ENV AGENT_ROUTING_PROVIDER=vertex_ai" in block
        assert "ENV AGENT_ROUTING_PROJECT_ID=my-project" in block
        assert "ENV AGENT_ROUTING_REGION=us-east5" in block

    def test_bedrock_routing_writes_region(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config_with_sdk(
            routing=ClaudeSDKRoutingConfig(provider="bedrock", region="us-west-2")
        )
        block = runtime._build_env_block(config)
        assert "ENV AGENT_ROUTING_PROVIDER=bedrock" in block
        assert "ENV AGENT_ROUTING_REGION=us-west-2" in block

    def test_deploy_env_vars_written(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config(deploy={"cloud": "local", "env_vars": {"LOG_LEVEL": "debug"}})
        block = runtime._build_env_block(config)
        assert "ENV LOG_LEVEL=" in block
        assert "debug" in block

    def test_model_max_tokens_written(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config(model={"primary": "claude-sonnet-4-6", "max_tokens": 8192})
        block = runtime._build_env_block(config)
        assert "ENV AGENT_MAX_TOKENS=8192" in block

    def test_dockerfile_contains_env_block(self, tmp_path: Path) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("agent = None")
        (agent_dir / "requirements.txt").write_text("anthropic>=0.50.0")
        config = _make_config_with_sdk(prompt_caching=True)
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "AGENT_PROMPT_CACHING=true" in dockerfile


# ---------------------------------------------------------------------------
# _call_client — thinking, caching, max_tokens fix
# ---------------------------------------------------------------------------

class TestCallClient:
    def _import_server_module(self) -> Any:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "claude_sdk_server_adv",
            Path("engine/runtimes/templates/claude_sdk_server.py"),
        )
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"AGENT_MAX_TOKENS": "2048"}, clear=False)
    async def test_max_tokens_from_env(self) -> None:
        mod = self._import_server_module()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="hello")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mod._prompt_caching_enabled = False
        mod._thinking_config = None

        result = await mod._call_client(mock_client, "claude-sonnet-4-6", "", [{"role": "user", "content": "hi"}])

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] == 2048
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_default_max_tokens_is_4096(self) -> None:
        mod = self._import_server_module()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="hi")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mod._prompt_caching_enabled = False
        mod._thinking_config = None
        os.environ.pop("AGENT_MAX_TOKENS", None)
        await mod._call_client(mock_client, "claude-sonnet-4-6", "", [])
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_thinking_config_applied(self) -> None:
        mod = self._import_server_module()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="thought")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mod._prompt_caching_enabled = False
        mod._thinking_config = {"type": "adaptive", "_effort": "high"}

        await mod._call_client(mock_client, "claude-sonnet-4-6", "", [{"role": "user", "content": "think"}])

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["thinking"] == {"type": "adaptive"}
        assert call_kwargs["output_config"] == {"effort": "high"}
        assert "interleaved-thinking-2025-05-14" in call_kwargs["betas"]
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_prompt_caching_applied_for_long_system_prompt(self) -> None:
        mod = self._import_server_module()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="cached")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mod._prompt_caching_enabled = True
        mod._thinking_config = None

        long_system = "x" * 9000  # > 8192 chars threshold for sonnet

        await mod._call_client(
            mock_client, "claude-sonnet-4-6", long_system, [{"role": "user", "content": "hi"}]
        )

        call_kwargs = mock_client.messages.create.call_args[1]
        system_param = call_kwargs["system"]
        assert isinstance(system_param, list)
        assert system_param[0]["type"] == "text"
        assert system_param[0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_prompt_caching_not_applied_for_short_system_prompt(self) -> None:
        mod = self._import_server_module()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="short")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mod._prompt_caching_enabled = True
        mod._thinking_config = None

        short_system = "You are helpful."

        await mod._call_client(
            mock_client, "claude-sonnet-4-6", short_system, [{"role": "user", "content": "hi"}]
        )

        call_kwargs = mock_client.messages.create.call_args[1]
        system_param = call_kwargs["system"]
        assert isinstance(system_param, str)

    @pytest.mark.asyncio
    async def test_prompt_caching_not_applied_when_disabled(self) -> None:
        mod = self._import_server_module()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="no cache")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mod._prompt_caching_enabled = False
        mod._thinking_config = None

        long_system = "x" * 9000

        await mod._call_client(
            mock_client, "claude-sonnet-4-6", long_system, [{"role": "user", "content": "hi"}]
        )

        call_kwargs = mock_client.messages.create.call_args[1]
        assert isinstance(call_kwargs["system"], str)


# ---------------------------------------------------------------------------
# Cache threshold logic
# ---------------------------------------------------------------------------

class TestCacheThreshold:
    def _get_threshold(self, model: str) -> int:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "claude_sdk_server_thresh",
            Path("engine/runtimes/templates/claude_sdk_server.py"),
        )
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod._get_cache_threshold(model)

    def test_sonnet_threshold_is_lower(self) -> None:
        assert self._get_threshold("claude-sonnet-4-6") == 8192

    def test_opus_threshold_is_higher(self) -> None:
        assert self._get_threshold("claude-opus-4") == 16384

    def test_haiku_threshold_is_higher(self) -> None:
        assert self._get_threshold("claude-haiku-4-5") == 16384


# ---------------------------------------------------------------------------
# Requirements version
# ---------------------------------------------------------------------------

class TestRequirementsVersion:
    def test_anthropic_version_is_050_or_higher(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        anthropic_req = next((r for r in reqs if r.startswith("anthropic")), None)
        assert anthropic_req is not None
        assert "0.50.0" in anthropic_req or "0.50" in anthropic_req
        assert "0.40" not in anthropic_req
