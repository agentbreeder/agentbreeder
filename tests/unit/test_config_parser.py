"""Tests for engine/config_parser.py — the foundation of the deploy pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from engine.config_parser import (
    CloudType,
    ConfigParseError,
    FrameworkType,
    Visibility,
    parse_config,
    validate_config,
)


def _write_yaml(content: str) -> Path:
    """Write YAML content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


VALID_YAML = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""

FULL_YAML = """\
spec_version: v1
name: full-agent
version: 2.1.0
description: "A fully configured agent"
team: platform
owner: alice@company.com
tags:
  - production
  - support
framework: langgraph
model:
  primary: claude-sonnet-4
  fallback: gpt-4o
  gateway: litellm
  temperature: 0.7
  max_tokens: 4096
tools:
  - ref: tools/zendesk-mcp
  - ref: tools/order-lookup
knowledge_bases:
  - ref: kb/product-docs
prompts:
  system: "You are a helpful assistant"
guardrails:
  - pii_detection
  - hallucination_check
deploy:
  cloud: aws
  runtime: ecs-fargate
  region: us-east-1
  scaling:
    min: 1
    max: 10
    target_cpu: 70
  resources:
    cpu: "1"
    memory: 2Gi
  env_vars:
    LOG_LEVEL: info
  secrets:
    - API_KEY
access:
  visibility: team
  allowed_callers:
    - team:engineering
  require_approval: false
"""


class TestParseConfig:
    def test_valid_minimal_config(self) -> None:
        path = _write_yaml(VALID_YAML)
        config = parse_config(path)
        assert config.name == "test-agent"
        assert config.version == "1.0.0"
        assert config.team == "engineering"
        assert config.owner == "test@example.com"
        assert config.framework == FrameworkType.langgraph
        assert config.model.primary == "gpt-4o"
        assert config.deploy.cloud == CloudType.local

    def test_valid_full_config(self) -> None:
        path = _write_yaml(FULL_YAML)
        config = parse_config(path)
        assert config.name == "full-agent"
        assert config.version == "2.1.0"
        assert config.description == "A fully configured agent"
        assert config.framework == FrameworkType.langgraph
        assert config.model.primary == "claude-sonnet-4"
        assert config.model.fallback == "gpt-4o"
        assert config.model.temperature == 0.7
        assert config.model.max_tokens == 4096
        assert len(config.tools) == 2
        assert config.tools[0].ref == "tools/zendesk-mcp"
        assert len(config.knowledge_bases) == 1
        assert config.deploy.cloud == CloudType.aws
        assert config.deploy.scaling.min == 1
        assert config.deploy.scaling.max == 10
        assert config.deploy.resources.cpu == "1"
        assert config.access.visibility == Visibility.team
        assert config.access.require_approval is False

    def test_defaults_applied(self) -> None:
        path = _write_yaml(VALID_YAML)
        config = parse_config(path)
        assert config.description == ""
        assert config.tags == []
        assert config.tools == []
        assert config.deploy.scaling.min == 1
        assert config.deploy.scaling.max == 10
        assert config.deploy.scaling.target_cpu == 70
        assert config.deploy.resources.cpu == "0.5"
        assert config.deploy.resources.memory == "1Gi"
        assert config.access.visibility == Visibility.team
        assert config.access.require_approval is False
        assert config.spec_version == "v1"

    def test_missing_name_raises(self) -> None:
        yaml = """\
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError) as exc_info:
            parse_config(path)
        assert any("name" in e.message for e in exc_info.value.errors)

    def test_missing_version_raises(self) -> None:
        yaml = """\
name: test-agent
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError) as exc_info:
            parse_config(path)
        assert any("version" in e.message for e in exc_info.value.errors)

    def test_missing_framework_raises(self) -> None:
        config_file = _write_yaml("""\
name: my-agent
version: 1.0.0
team: engineering
owner: test@example.com
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert not result.valid
        assert any(
            "framework" in e.message.lower() or "runtime" in e.message.lower()
            for e in result.errors
        )

    def test_missing_model_raises(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError):
            parse_config(path)

    def test_missing_deploy_raises(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError):
            parse_config(path)

    def test_invalid_framework_raises(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: pytorch
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError) as exc_info:
            parse_config(path)
        errors = exc_info.value.errors
        assert any(
            "enum" in e.suggestion.lower() or "one of" in e.suggestion.lower() for e in errors
        )

    def test_invalid_cloud_raises(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: totally-invalid-cloud
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError):
            parse_config(path)

    def test_azure_cloud_is_valid(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: azure
"""
        path = _write_yaml(yaml)
        config = parse_config(path)
        assert config.deploy.cloud.value == "azure"

    def test_invalid_version_format_raises(self) -> None:
        yaml = """\
name: test-agent
version: v1.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError):
            parse_config(path)

    def test_invalid_name_format_raises(self) -> None:
        yaml = """\
name: Test_Agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        with pytest.raises(ConfigParseError):
            parse_config(path)

    def test_file_not_found(self) -> None:
        path = Path("/nonexistent/agent.yaml")
        with pytest.raises(ConfigParseError) as exc_info:
            parse_config(path)
        assert any("not found" in e.message.lower() for e in exc_info.value.errors)

    def test_empty_file(self) -> None:
        path = _write_yaml("")
        with pytest.raises(ConfigParseError):
            parse_config(path)

    def test_invalid_yaml_syntax(self) -> None:
        path = _write_yaml("name: [invalid\nyaml: {{bad")
        with pytest.raises(ConfigParseError):
            parse_config(path)

    def test_all_frameworks(self) -> None:
        for fw in ["langgraph", "crewai", "claude_sdk", "openai_agents", "google_adk", "custom"]:
            yaml = f"""\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: {fw}
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
            path = _write_yaml(yaml)
            config = parse_config(path)
            assert config.framework.value == fw

    def test_all_cloud_types(self) -> None:
        for cloud in ["aws", "gcp", "kubernetes", "local"]:
            yaml = f"""\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: {cloud}
"""
            path = _write_yaml(yaml)
            config = parse_config(path)
            assert config.deploy.cloud.value == cloud


class TestValidateConfig:
    def test_valid_returns_valid(self) -> None:
        path = _write_yaml(VALID_YAML)
        result = validate_config(path)
        assert result.valid is True
        assert result.config is not None
        assert result.errors == []

    def test_invalid_returns_errors(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
"""
        path = _write_yaml(yaml)
        result = validate_config(path)
        assert result.valid is False
        assert len(result.errors) > 0

    def test_file_not_found_returns_error(self) -> None:
        path = Path("/nonexistent.yaml")
        result = validate_config(path)
        assert result.valid is False
        assert len(result.errors) == 1

    def test_errors_have_suggestions(self) -> None:
        yaml = """\
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        result = validate_config(path)
        assert result.valid is False
        # Should suggest adding the missing 'name' field
        name_errors = [e for e in result.errors if "name" in e.message.lower()]
        assert len(name_errors) > 0

    def test_inline_tool_definition(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
tools:
  - name: search
    type: function
    description: "Search the web"
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        config = parse_config(path)
        assert len(config.tools) == 1
        assert config.tools[0].name == "search"

    def test_mixed_tools(self) -> None:
        yaml = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
tools:
  - ref: tools/zendesk-mcp
  - name: search
    type: function
    description: "Search the web"
deploy:
  cloud: local
"""
        path = _write_yaml(yaml)
        config = parse_config(path)
        assert len(config.tools) == 2
        assert config.tools[0].ref == "tools/zendesk-mcp"
        assert config.tools[1].name == "search"


class TestRuntimeConfig:
    def test_valid_node_runtime(self, tmp_path: Path) -> None:
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("""\
name: my-agent
version: 1.0.0
team: engineering
owner: test@example.com
type: agent
runtime:
  language: node
  framework: vercel-ai
  version: "20"
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert result.valid, result.errors
        assert result.config is not None
        assert result.config.runtime is not None
        assert result.config.runtime.language == "node"
        assert result.config.runtime.framework == "vercel-ai"
        assert result.config.type.value == "agent"

    def test_open_framework_string_accepted(self, tmp_path: Path) -> None:
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("""\
name: my-agent
version: 1.0.0
team: engineering
owner: test@example.com
runtime:
  language: node
  framework: some-future-framework-not-in-schema
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert result.valid, result.errors

    def test_unknown_language_rejected(self, tmp_path: Path) -> None:
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("""\
name: my-agent
version: 1.0.0
team: engineering
owner: test@example.com
runtime:
  language: cobol
  framework: custom
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert not result.valid

    def test_both_framework_and_runtime_rejected(self, tmp_path: Path) -> None:
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("""\
name: my-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
runtime:
  language: node
  framework: vercel-ai
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert not result.valid

    def test_neither_framework_nor_runtime_rejected(self, tmp_path: Path) -> None:
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("""\
name: my-agent
version: 1.0.0
team: engineering
owner: test@example.com
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert not result.valid

    def test_existing_python_framework_still_works(self, tmp_path: Path) -> None:
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("""\
name: my-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert result.valid, result.errors

    def test_mcp_server_type(self, tmp_path: Path) -> None:
        config_file = tmp_path / "agent.yaml"
        config_file.write_text("""\
name: my-tools
version: 1.0.0
team: engineering
owner: test@example.com
type: mcp-server
runtime:
  language: node
  framework: mcp-ts
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
        result = validate_config(config_file)
        assert result.valid, result.errors
        assert result.config.type.value == "mcp-server"
