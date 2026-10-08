"""Tests for sdk/python/agenthub/yaml_utils.py — agent_to_yaml and yaml_to_agent."""

import pytest
import yaml

from sdk.python.agenthub.agent import Agent
from sdk.python.agenthub.deploy import DeployConfig
from sdk.python.agenthub.tool import Tool
from sdk.python.agenthub.yaml_utils import agent_to_yaml, yaml_to_agent


def _make_agent(**kwargs) -> Agent:
    """Helper: create an Agent with sensible defaults."""
    defaults = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "engineering",
        "framework": "langgraph",
    }
    defaults.update(kwargs)
    return Agent(**defaults)


class TestAgentToYaml:
    def test_minimal_agent(self):
        agent = _make_agent()
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert data["name"] == "test-agent"
        assert data["version"] == "1.0.0"
        assert data["team"] == "engineering"
        assert data["framework"] == "langgraph"

    def test_description_included_when_set(self):
        agent = _make_agent(description="A test agent")
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert data["description"] == "A test agent"

    def test_description_omitted_when_empty(self):
        agent = _make_agent(description="")
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert "description" not in data

    def test_owner_included_when_set(self):
        agent = _make_agent(owner="dev@example.com")
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert data["owner"] == "dev@example.com"

    def test_tags_included_when_set(self):
        agent = _make_agent(tags=["production", "support"])
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert data["tags"] == ["production", "support"]

    def test_tags_omitted_when_empty(self):
        agent = _make_agent()
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert "tags" not in data

    def test_model_serialized(self):
        agent = _make_agent()
        agent.with_model(primary="claude-sonnet-4", fallback="gpt-4o", temperature=0.5)
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert data["model"]["primary"] == "claude-sonnet-4"
        assert data["model"]["fallback"] == "gpt-4o"
        assert data["model"]["temperature"] == pytest.approx(0.5)

    def test_tools_serialized(self):
        agent = _make_agent()
        agent.with_tool(Tool(name="search", description="Search the web"))
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert len(data["tools"]) == 1

    def test_tools_omitted_when_none(self):
        agent = _make_agent()
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert "tools" not in data

    def test_guardrails_serialized(self):
        agent = _make_agent(guardrails=["pii_detection", "content_filter"])
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert data["guardrails"] == ["pii_detection", "content_filter"]

    def test_deploy_serialized(self):
        agent = _make_agent(deploy=DeployConfig(cloud="gcp", runtime="cloud-run"))
        result = agent_to_yaml(agent)
        data = yaml.safe_load(result)
        assert data["deploy"]["cloud"] == "gcp"

    def test_output_is_valid_yaml_string(self):
        agent = _make_agent()
        result = agent_to_yaml(agent)
        assert isinstance(result, str)
        parsed = yaml.safe_load(result)
        assert isinstance(parsed, dict)


class TestYamlToAgent:
    def test_minimal_yaml(self):
        yaml_str = """
name: my-agent
version: 1.0.0
team: eng
framework: langgraph
"""
        agent = yaml_to_agent(yaml_str)
        assert agent.config.name == "my-agent"
        assert agent.config.version == "1.0.0"
        assert agent.config.team == "eng"
        assert agent.config.framework == "langgraph"

    def test_invalid_yaml_raises(self):
        with pytest.raises(ValueError, match="Invalid YAML"):
            yaml_to_agent("- not a mapping")

    def test_model_parsed(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
model:
  primary: claude-sonnet-4
  fallback: gpt-4o
  temperature: 0.3
  max_tokens: 2048
"""
        agent = yaml_to_agent(yaml_str)
        assert agent.config.model is not None
        assert agent.config.model.primary == "claude-sonnet-4"
        assert agent.config.model.fallback == "gpt-4o"
        assert agent.config.model.temperature == pytest.approx(0.3)
        assert agent.config.model.max_tokens == 2048

    def test_model_not_dict_skipped(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
model: null
"""
        agent = yaml_to_agent(yaml_str)
        assert agent.config.model is None

    def test_prompts_parsed(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
prompts:
  system: prompts/support-v1
"""
        agent = yaml_to_agent(yaml_str)
        assert agent.config.prompts is not None
        assert agent.config.prompts.system == "prompts/support-v1"

    def test_memory_parsed(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
memory:
  backend: redis
  memory_type: buffer_window
  max_messages: 50
"""
        agent = yaml_to_agent(yaml_str)
        assert agent.config.memory is not None
        assert agent.config.memory.backend == "redis"
        assert agent.config.memory.max_messages == 50

    def test_deploy_parsed(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
deploy:
  cloud: gcp
  runtime: cloud-run
  region: us-central1
"""
        agent = yaml_to_agent(yaml_str)
        assert agent.config.deploy is not None
        assert agent.config.deploy.cloud == "gcp"
        assert agent.config.deploy.runtime == "cloud-run"

    def test_tools_ref_parsed(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
tools:
  - ref: tools/search
"""
        agent = yaml_to_agent(yaml_str)
        assert len(agent._tools) == 1
        assert agent._tools[0].ref == "tools/search"

    def test_tools_inline_parsed(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
tools:
  - name: calculator
    description: Does math
"""
        agent = yaml_to_agent(yaml_str)
        assert len(agent._tools) == 1
        assert agent._tools[0].name == "calculator"

    def test_guardrails_parsed(self):
        yaml_str = """
name: a
version: 1.0.0
team: t
framework: langgraph
guardrails:
  - pii_detection
  - content_filter
"""
        agent = yaml_to_agent(yaml_str)
        assert agent.config.guardrails == ["pii_detection", "content_filter"]

    def test_roundtrip(self):
        """agent_to_yaml → yaml_to_agent should preserve all fields."""
        original = _make_agent(
            description="roundtrip test",
            owner="alice@example.com",
            tags=["test"],
            guardrails=["pii_detection"],
        )
        original.with_model(primary="claude-sonnet-4")
        original.with_tool(Tool(name="search", description="Search the web"))

        yaml_str = agent_to_yaml(original)
        restored = yaml_to_agent(yaml_str)

        assert restored.config.name == original.config.name
        assert restored.config.description == original.config.description
        assert restored.config.owner == original.config.owner
        assert restored.config.tags == original.config.tags
        assert restored.config.guardrails == original.config.guardrails
        assert restored.config.model.primary == original.config.model.primary
        assert len(restored._tools) == len(original._tools)
