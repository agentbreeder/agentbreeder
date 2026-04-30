"""Tests for agent YAML validation and create_from_yaml."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base
from registry.agents import create_from_yaml, validate_config_yaml

_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

VALID_YAML = """\
name: test-agent
version: 1.0.0
description: A test agent
team: engineering
owner: alice@example.com
framework: langgraph
model:
  primary: claude-sonnet-4
deploy:
  cloud: local
tools:
  - ref: tools/search
prompts:
  system: "You are a helpful assistant."
guardrails:
  - pii_detection
"""

MINIMAL_VALID_YAML = """\
name: my-agent
version: 1.0.0
team: eng
owner: bob@test.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: aws
"""


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionFactory() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


class TestValidateConfigYaml:
    def test_valid_yaml(self) -> None:
        result = validate_config_yaml(VALID_YAML)
        assert result.valid is True
        assert len(result.errors) == 0
        assert result.parsed is not None
        assert result.parsed["name"] == "test-agent"

    def test_minimal_valid(self) -> None:
        result = validate_config_yaml(MINIMAL_VALID_YAML)
        assert result.valid is True
        # Should have warnings for missing tools, prompts, guardrails, description
        assert len(result.warnings) >= 3

    def test_empty_string(self) -> None:
        result = validate_config_yaml("")
        assert result.valid is False
        assert any("Empty" in e.message for e in result.errors)

    def test_whitespace_only(self) -> None:
        result = validate_config_yaml("   \n\n  ")
        assert result.valid is False

    def test_invalid_yaml_syntax(self) -> None:
        result = validate_config_yaml("name: [invalid\nyaml: bad")
        assert result.valid is False
        assert any("parse error" in e.message.lower() for e in result.errors)

    def test_yaml_not_mapping(self) -> None:
        result = validate_config_yaml("- item1\n- item2")
        assert result.valid is False
        assert any("mapping" in e.message.lower() for e in result.errors)

    def test_missing_required_fields(self) -> None:
        result = validate_config_yaml("description: only this")
        assert result.valid is False
        paths = {e.path for e in result.errors}
        assert "name" in paths
        assert "version" in paths
        assert "team" in paths
        assert "owner" in paths
        assert "framework" in paths

    def test_invalid_name_format(self) -> None:
        yaml = MINIMAL_VALID_YAML.replace("my-agent", "My Agent!!")
        result = validate_config_yaml(yaml)
        assert result.valid is False
        assert any(e.path == "name" for e in result.errors)

    def test_invalid_version_format(self) -> None:
        yaml = MINIMAL_VALID_YAML.replace("1.0.0", "v1")
        result = validate_config_yaml(yaml)
        assert result.valid is False
        assert any(e.path == "version" for e in result.errors)

    def test_missing_model_primary(self) -> None:
        yaml = MINIMAL_VALID_YAML.replace(
            "model:\n  primary: gpt-4o", "model:\n  fallback: gpt-4o"
        )
        result = validate_config_yaml(yaml)
        assert result.valid is False
        assert any(e.path == "model.primary" for e in result.errors)

    def test_unknown_framework(self) -> None:
        yaml = MINIMAL_VALID_YAML.replace("framework: langgraph", "framework: pytorch")
        result = validate_config_yaml(yaml)
        assert result.valid is False
        assert any(e.path == "framework" for e in result.errors)

    def test_missing_deploy(self) -> None:
        yaml = (
            "name: test-agent\nversion: 1.0.0\nteam: eng\n"
            "owner: a@b.com\nframework: langgraph\nmodel:\n  primary: gpt-4o\n"
        )
        result = validate_config_yaml(yaml)
        assert result.valid is False
        assert any(e.path == "deploy" for e in result.errors)

    def test_unknown_cloud(self) -> None:
        yaml = MINIMAL_VALID_YAML.replace("cloud: aws", "cloud: azure")
        result = validate_config_yaml(yaml)
        assert result.valid is False
        assert any(e.path == "deploy.cloud" for e in result.errors)

    def test_warnings_for_missing_optional(self) -> None:
        result = validate_config_yaml(MINIMAL_VALID_YAML)
        warning_paths = {w.path for w in result.warnings}
        assert "tools" in warning_paths
        assert "prompts.system" in warning_paths
        assert "guardrails" in warning_paths
        assert "description" in warning_paths

    def test_no_warnings_when_all_present(self) -> None:
        result = validate_config_yaml(VALID_YAML)
        assert len(result.warnings) == 0

    def test_parsed_is_none_on_error(self) -> None:
        result = validate_config_yaml("name: bad name!!")
        assert result.valid is False
        assert result.parsed is None


class TestCreateFromYaml:
    @pytest.mark.asyncio
    async def test_create_valid_agent(self, session: AsyncSession) -> None:
        agent = await create_from_yaml(session, VALID_YAML)
        assert agent.name == "test-agent"
        assert agent.version == "1.0.0"
        assert agent.team == "engineering"
        assert agent.framework == "langgraph"
        assert agent.model_primary == "claude-sonnet-4"

    @pytest.mark.asyncio
    async def test_create_minimal_agent(self, session: AsyncSession) -> None:
        agent = await create_from_yaml(session, MINIMAL_VALID_YAML)
        assert agent.name == "my-agent"
        assert agent.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_create_invalid_raises(self, session: AsyncSession) -> None:
        with pytest.raises(ValueError, match="Validation failed"):
            await create_from_yaml(session, "invalid: true")

    @pytest.mark.asyncio
    async def test_create_empty_raises(self, session: AsyncSession) -> None:
        with pytest.raises(ValueError, match="Validation failed"):
            await create_from_yaml(session, "")


# ---------------------------------------------------------------------------
# Issue #204 — verify the dashboard's agent-builder YAML emit format is
# accepted by the engine parser. The dashboard emits:
#   - python agents:     top-level `framework: <fw>`
#   - typescript agents: top-level `runtime: { language: node, framework: <fw> }`
#   - per-gateway overrides under top-level `gateways:`
# We exercise the deeper engine parser (which reads the JSON Schema and
# applies the framework-XOR-runtime rule) — not just `validate_config_yaml`,
# which still requires `framework`.
# ---------------------------------------------------------------------------


class TestDashboardEmitFormat:
    """The visual builder's YAML must round-trip cleanly through the parser."""

    PYTHON_YAML = """\
name: test-agent
version: "1.0.0"
description: "py agent"
team: engineering
owner: alice@example.com

model:
  primary: claude-sonnet-4
  temperature: 0.7
  max_tokens: 4096

framework: langgraph

tools: []

prompts:
  system: "You are helpful."

guardrails: []

deploy:
  cloud: local
  runtime: docker-compose
  scaling:
    min: 1
    max: 10
"""

    TYPESCRIPT_YAML = """\
name: ts-agent
version: "1.0.0"
description: "ts agent"
team: engineering
owner: alice@example.com

model:
  primary: claude-sonnet-4
  temperature: 0.7
  max_tokens: 4096

runtime:
  language: node
  framework: openai-agents-js

tools: []

prompts:
  system: "You are helpful."

guardrails: []

deploy:
  cloud: local
  runtime: docker-compose
  scaling:
    min: 1
    max: 10
"""

    GATEWAYS_YAML = """\
name: gw-agent
version: "1.0.0"
description: "agent with gateway override"
team: engineering
owner: alice@example.com

model:
  primary: claude-sonnet-4
  temperature: 0.7
  max_tokens: 4096

framework: langgraph

tools: []

prompts:
  system: "You are helpful."

guardrails: []

gateways:
  litellm:
    url: http://litellm.local:4000
    api_key_env: LITELLM_API_KEY
    fallback_policy: fastest

deploy:
  cloud: local
  runtime: docker-compose
  scaling:
    min: 1
    max: 10
"""

    def _parse(self, tmp_path, yaml_str: str):
        from engine.config_parser import parse_config

        path = tmp_path / "agent.yaml"
        path.write_text(yaml_str)
        return parse_config(path)

    def test_python_emit_is_valid(self, tmp_path) -> None:
        """Default python emit (top-level framework) parses cleanly."""
        cfg = self._parse(tmp_path, self.PYTHON_YAML)
        assert cfg.name == "test-agent"
        assert cfg.framework is not None
        assert str(cfg.framework) == "langgraph"
        assert cfg.runtime is None

    def test_typescript_emit_uses_runtime_block(self, tmp_path) -> None:
        """Typescript emit (runtime block with language=node) parses cleanly."""
        cfg = self._parse(tmp_path, self.TYPESCRIPT_YAML)
        assert cfg.name == "ts-agent"
        assert cfg.framework is None
        assert cfg.runtime is not None
        assert str(cfg.runtime.language) == "node"
        assert cfg.runtime.framework == "openai-agents-js"

    def test_gateways_block_round_trips(self, tmp_path) -> None:
        """Top-level gateways: { litellm: {...} } parses cleanly."""
        cfg = self._parse(tmp_path, self.GATEWAYS_YAML)
        assert "litellm" in cfg.gateways
        gw = cfg.gateways["litellm"]
        assert gw.url == "http://litellm.local:4000"
        assert gw.api_key_env == "LITELLM_API_KEY"
        assert gw.fallback_policy == "fastest"

    def test_top_level_language_is_rejected(self, tmp_path) -> None:
        """Confirm the parser still rejects top-level `language:` so the
        emitter must use `runtime: { language }` for non-python agents."""
        from engine.config_parser import ConfigParseError

        bad_yaml = self.PYTHON_YAML + "\nlanguage: typescript\n"
        with pytest.raises(ConfigParseError):
            self._parse(tmp_path, bad_yaml)
