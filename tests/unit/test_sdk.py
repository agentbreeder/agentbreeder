"""Unit tests for the AgentBreeder Python SDK (M28)."""

from __future__ import annotations

from pathlib import Path

import yaml

from sdk.python.agenthub import (
    Agent,
    DeployConfig,
    Memory,
    Model,
    ModelConfig,
    PromptConfig,
    Tool,
    ToolConfig,
)

# ---------------------------------------------------------------
# Agent creation
# ---------------------------------------------------------------


class TestAgentCreation:
    def test_agent_creation_minimal(self) -> None:
        agent = Agent("test-agent")
        assert agent.config.name == "test-agent"
        assert agent.config.version == "1.0.0"
        assert agent.config.team == "default"
        assert agent.config.framework == "custom"

    def test_agent_creation_full(self) -> None:
        agent = Agent(
            "my-agent",
            version="2.0.0",
            description="A test agent",
            team="engineering",
            owner="alice@co.com",
            framework="langgraph",
        )
        assert agent.config.name == "my-agent"
        assert agent.config.version == "2.0.0"
        assert agent.config.description == "A test agent"
        assert agent.config.team == "engineering"
        assert agent.config.owner == "alice@co.com"
        assert agent.config.framework == "langgraph"

    def test_agent_repr(self) -> None:
        agent = Agent("test-agent", version="1.0.0")
        r = repr(agent)
        assert "test-agent" in r
        assert "1.0.0" in r


# ---------------------------------------------------------------
# Builder pattern chaining
# ---------------------------------------------------------------


class TestBuilderPattern:
    def test_chaining_returns_self(self) -> None:
        agent = Agent("chain-test", team="eng")
        result = (
            agent.with_model(primary="claude-sonnet-4")
            .with_prompt(system="Hello")
            .with_guardrail("pii_detection")
            .with_deploy(cloud="aws")
            .tag("test")
        )
        assert result is agent

    def test_with_model(self) -> None:
        agent = Agent("test").with_model(
            primary="claude-sonnet-4",
            fallback="gpt-4o",
            temperature=0.5,
        )
        assert agent.config.model is not None
        assert agent.config.model.primary == "claude-sonnet-4"
        assert agent.config.model.fallback == "gpt-4o"
        assert agent.config.model.temperature == 0.5

    def test_with_tool(self) -> None:
        agent = Agent("test")
        tool = Tool.from_ref("tools/my-tool")
        agent.with_tool(tool)
        assert len(agent._tools) == 1
        assert len(agent.config.tools) == 1
        assert agent._tools[0].ref == "tools/my-tool"

    def test_with_prompt(self) -> None:
        agent = Agent("test").with_prompt(system="Be helpful.")
        assert agent.config.prompts is not None
        assert agent.config.prompts.system == "Be helpful."

    def test_with_memory(self) -> None:
        agent = Agent("test").with_memory(backend="postgresql", max_messages=50)
        assert agent.config.memory is not None
        assert agent.config.memory.backend == "postgresql"
        assert agent.config.memory.max_messages == 50

    def test_with_guardrail(self) -> None:
        agent = Agent("test").with_guardrail("pii_detection").with_guardrail("content_filter")
        assert agent.config.guardrails == ["pii_detection", "content_filter"]

    def test_with_deploy(self) -> None:
        agent = Agent("test").with_deploy(cloud="aws", runtime="ecs-fargate", region="us-east-1")
        assert agent.config.deploy is not None
        assert agent.config.deploy.cloud == "aws"
        assert agent.config.deploy.runtime == "ecs-fargate"
        assert agent.config.deploy.region == "us-east-1"

    def test_tag(self) -> None:
        agent = Agent("test").tag("a", "b", "c")
        assert agent.config.tags == ["a", "b", "c"]

    def test_full_builder_chain(self) -> None:
        agent = (
            Agent("full-test", version="1.0.0", team="support")
            .with_model(primary="claude-sonnet-4", fallback="gpt-4o")
            .with_prompt(system="You are helpful.")
            .with_tool(Tool.from_ref("tools/zendesk-mcp"))
            .with_memory(backend="postgresql")
            .with_guardrail("pii_detection")
            .with_deploy(cloud="aws", runtime="ecs-fargate")
            .tag("support", "production")
        )
        assert agent.config.name == "full-test"
        assert agent.config.model is not None
        assert agent.config.model.primary == "claude-sonnet-4"
        assert len(agent._tools) == 1
        assert agent.config.memory is not None
        assert len(agent.config.guardrails) == 1
        assert agent.config.deploy is not None
        assert agent.config.tags == ["support", "production"]


# ---------------------------------------------------------------
# YAML roundtrip
# ---------------------------------------------------------------


class TestYamlRoundtrip:
    def test_to_yaml_produces_valid_yaml(self) -> None:
        agent = (
            Agent("roundtrip-test", version="1.0.0", team="eng")
            .with_model(primary="claude-sonnet-4")
            .with_prompt(system="Be helpful.")
            .with_deploy(cloud="local")
        )
        yaml_str = agent.to_yaml()
        data = yaml.safe_load(yaml_str)
        assert data["name"] == "roundtrip-test"
        assert data["version"] == "1.0.0"
        assert data["model"]["primary"] == "claude-sonnet-4"
        assert data["framework"] == "custom"

    def test_roundtrip_create_to_yaml_to_agent(self) -> None:
        original = (
            Agent("roundtrip", version="2.1.0", team="platform", owner="bob@co.com")
            .with_model(primary="gpt-4o", fallback="claude-sonnet-4")
            .with_prompt(system="System prompt here")
            .with_tool(Tool.from_ref("tools/search"))
            .with_tool(Tool.from_ref("tools/lookup"))
            .with_guardrail("pii_detection")
            .with_guardrail("content_filter")
            .with_deploy(cloud="aws", runtime="ecs-fargate", region="us-west-2")
            .tag("test", "roundtrip")
        )

        yaml_str = original.to_yaml()
        restored = Agent.from_yaml(yaml_str)

        assert restored.config.name == original.config.name
        assert restored.config.version == original.config.version
        assert restored.config.team == original.config.team
        assert restored.config.owner == original.config.owner
        assert restored.config.model is not None
        assert restored.config.model.primary == "gpt-4o"
        assert restored.config.model.fallback == "claude-sonnet-4"
        assert restored.config.prompts is not None
        assert restored.config.prompts.system == "System prompt here"
        assert len(restored._tools) == 2
        assert restored.config.guardrails == ["pii_detection", "content_filter"]
        assert restored.config.deploy is not None
        assert restored.config.deploy.cloud == "aws"
        assert restored.config.tags == ["test", "roundtrip"]

    def test_yaml_field_ordering(self) -> None:
        """YAML output should follow the canonical field order."""
        agent = (
            Agent("order-test", version="1.0.0", team="eng")
            .with_model(primary="claude-sonnet-4")
            .with_deploy(cloud="local")
        )
        yaml_str = agent.to_yaml()
        lines = yaml_str.strip().split("\n")
        # name should come before model, model before framework, framework before deploy
        keys = [line.split(":")[0] for line in lines if not line.startswith(" ")]
        name_idx = keys.index("name")
        model_idx = keys.index("model")
        framework_idx = keys.index("framework")
        deploy_idx = keys.index("deploy")
        assert name_idx < model_idx < framework_idx < deploy_idx


# ---------------------------------------------------------------
# Tool
# ---------------------------------------------------------------


class TestTool:
    def test_from_function_schema_generation(self) -> None:
        def greet(name: str, excited: bool = False) -> str:
            """Say hello to someone."""
            return f"Hello, {name}{'!' if excited else '.'}"

        tool = Tool.from_function(greet)
        assert tool.name == "greet"
        assert tool.description == "Say hello to someone."
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert "name" in schema["properties"]
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["excited"]["type"] == "boolean"
        assert "name" in schema["required"]
        assert "excited" not in schema["required"]

    def test_from_function_custom_name(self) -> None:
        def calculate(expression: str) -> str:
            """Evaluate expression."""
            return expression

        tool = Tool.from_function(calculate, name="calc", description="A calculator")
        assert tool.name == "calc"
        assert tool.description == "A calculator"

    def test_from_ref(self) -> None:
        tool = Tool.from_ref("tools/zendesk-mcp")
        assert tool.ref == "tools/zendesk-mcp"
        assert tool.name == "zendesk-mcp"

    def test_to_config(self) -> None:
        tool = Tool.from_ref("tools/search")
        config = tool.to_config()
        assert isinstance(config, ToolConfig)
        assert config.ref == "tools/search"

    def test_to_dict_ref(self) -> None:
        tool = Tool.from_ref("tools/search")
        d = tool.to_dict()
        assert d == {"ref": "tools/search"}

    def test_to_dict_inline(self) -> None:
        def my_fn(query: str) -> str:
            """Search stuff."""
            return query

        tool = Tool.from_function(my_fn)
        d = tool.to_dict()
        assert d["name"] == "my_fn"
        assert d["description"] == "Search stuff."
        assert "schema" in d

    def test_from_function_no_hints(self) -> None:
        """Functions without type hints default to string."""

        def untyped(x, y):  # type: ignore[no-untyped-def]
            pass

        tool = Tool.from_function(untyped)
        assert tool.input_schema["properties"]["x"]["type"] == "string"
        assert tool.input_schema["properties"]["y"]["type"] == "string"


# ---------------------------------------------------------------
# Memory
# ---------------------------------------------------------------


class TestMemory:
    def test_buffer_window(self) -> None:
        mem = Memory.buffer_window(max_messages=50)
        config = mem.to_config()
        assert config.memory_type == "buffer_window"
        assert config.max_messages == 50

    def test_buffer(self) -> None:
        mem = Memory.buffer()
        assert mem.to_config().memory_type == "buffer"

    def test_postgresql(self) -> None:
        mem = Memory.postgresql()
        assert mem.to_config().backend == "postgresql"

    def test_to_dict(self) -> None:
        mem = Memory.buffer_window(max_messages=25)
        d = mem.to_dict()
        assert d["backend"] == "in_memory"
        assert d["memory_type"] == "buffer_window"
        assert d["max_messages"] == 25


# ---------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------


class TestModelHelpers:
    def test_claude_sonnet(self) -> None:
        config = Model.claude_sonnet()
        assert config.primary == "claude-sonnet-4"

    def test_claude_opus(self) -> None:
        config = Model.claude_opus()
        assert config.primary == "claude-opus-4"

    def test_gpt4o(self) -> None:
        config = Model.gpt4o()
        assert config.primary == "gpt-4o"

    def test_ollama(self) -> None:
        config = Model.ollama("llama3")
        assert config.primary == "ollama/llama3"
        assert config.gateway == "ollama"

    def test_model_config_to_dict_defaults(self) -> None:
        config = ModelConfig(primary="test-model")
        d = config.to_dict()
        assert d == {"primary": "test-model"}
        assert "temperature" not in d  # defaults omitted

    def test_model_config_to_dict_custom(self) -> None:
        config = ModelConfig(
            primary="test",
            fallback="backup",
            temperature=0.3,
            max_tokens=2048,
        )
        d = config.to_dict()
        assert d["fallback"] == "backup"
        assert d["temperature"] == 0.3
        assert d["max_tokens"] == 2048


# ---------------------------------------------------------------
# Validation
# ---------------------------------------------------------------


class TestValidation:
    def test_valid_agent(self) -> None:
        agent = Agent("valid-agent", version="1.0.0", team="eng").with_model(
            primary="claude-sonnet-4"
        )
        errors = agent.validate()
        assert errors == []

    def test_missing_name(self) -> None:
        agent = Agent("", team="eng")
        agent.with_model(primary="claude-sonnet-4")
        errors = agent.validate()
        assert any("name" in e for e in errors)

    def test_invalid_name(self) -> None:
        agent = Agent("Invalid Name!", team="eng")
        agent.with_model(primary="test")
        errors = agent.validate()
        assert any("slug" in e for e in errors)

    def test_invalid_version(self) -> None:
        agent = Agent("test-agent", version="not-semver", team="eng")
        agent.with_model(primary="test")
        errors = agent.validate()
        assert any("semver" in e for e in errors)

    def test_invalid_framework(self) -> None:
        agent = Agent("test-agent", framework="nonexistent", team="eng")
        agent.with_model(primary="test")
        errors = agent.validate()
        assert any("framework" in e for e in errors)

    def test_missing_model(self) -> None:
        agent = Agent("test-agent", team="eng")
        errors = agent.validate()
        assert any("model" in e for e in errors)

    def test_missing_team(self) -> None:
        agent = Agent("test-agent", team="")
        agent.with_model(primary="test")
        errors = agent.validate()
        assert any("team" in e for e in errors)


# ---------------------------------------------------------------
# Middleware and hooks
# ---------------------------------------------------------------


class TestMiddlewareAndHooks:
    def test_middleware_registration(self) -> None:
        agent = Agent("test")

        def my_middleware(msg: str, ctx: dict) -> dict:  # type: ignore[type-arg]
            return ctx

        agent.use(my_middleware)
        assert len(agent._middleware) == 1
        assert agent._middleware[0] is my_middleware

    def test_middleware_chaining(self) -> None:
        def mw1(msg: str, ctx: dict) -> dict:  # type: ignore[type-arg]
            return ctx

        def mw2(msg: str, ctx: dict) -> dict:  # type: ignore[type-arg]
            return ctx

        agent = Agent("test").use(mw1).use(mw2)
        assert len(agent._middleware) == 2

    def test_hooks_registration(self) -> None:
        agent = Agent("test")

        def on_tool_call(name: str) -> None:
            pass

        def on_error(err: Exception) -> None:
            pass

        agent.on("tool_call", on_tool_call)
        agent.on("error", on_error)
        assert len(agent._hooks["tool_call"]) == 1
        assert len(agent._hooks["error"]) == 1

    def test_multiple_hooks_same_event(self) -> None:
        agent = Agent("test")

        def h1() -> None:
            pass

        def h2() -> None:
            pass

        agent.on("turn_start", h1).on("turn_start", h2)
        assert len(agent._hooks["turn_start"]) == 2


# ---------------------------------------------------------------
# State
# ---------------------------------------------------------------


class TestState:
    def test_state_access(self) -> None:
        agent = Agent("test")
        agent.state["counter"] = 42
        assert agent.state["counter"] == 42

    def test_state_persists(self) -> None:
        agent = Agent("test")
        agent.state["items"] = [1, 2, 3]
        agent.state["items"].append(4)
        assert agent.state["items"] == [1, 2, 3, 4]


# ---------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------


class TestFileIO:
    def test_save_and_load(self, tmp_path: Path) -> None:
        agent = (
            Agent("file-test", version="1.0.0", team="eng")
            .with_model(primary="claude-sonnet-4")
            .with_prompt(system="Hello")
            .with_deploy(cloud="local")
        )

        yaml_path = str(tmp_path / "agent.yaml")
        agent.save(yaml_path)

        loaded = Agent.from_file(yaml_path)
        assert loaded.config.name == "file-test"
        assert loaded.config.model is not None
        assert loaded.config.model.primary == "claude-sonnet-4"
        assert loaded.config.prompts is not None
        assert loaded.config.prompts.system == "Hello"

    def test_from_file_reads_yaml(self, tmp_path: Path) -> None:
        yaml_content = """
name: from-file-test
version: 3.0.0
team: platform
framework: langgraph
model:
  primary: gpt-4o
"""
        path = tmp_path / "agent.yaml"
        path.write_text(yaml_content)

        agent = Agent.from_file(str(path))
        assert agent.config.name == "from-file-test"
        assert agent.config.version == "3.0.0"
        assert agent.config.framework == "langgraph"
        assert agent.config.model is not None
        assert agent.config.model.primary == "gpt-4o"


# ---------------------------------------------------------------
# Deploy and DeployConfig
# ---------------------------------------------------------------


class TestDeployConfig:
    def test_deploy_config_defaults(self) -> None:
        cfg = DeployConfig()
        assert cfg.cloud == "local"
        assert cfg.runtime is None
        assert cfg.env_vars == {}
        assert cfg.secrets == []

    def test_deploy_config_to_dict(self) -> None:
        cfg = DeployConfig(
            cloud="aws",
            runtime="ecs-fargate",
            region="us-east-1",
            env_vars={"LOG_LEVEL": "info"},
            secrets=["API_KEY"],
        )
        d = cfg.to_dict()
        assert d["cloud"] == "aws"
        assert d["runtime"] == "ecs-fargate"
        assert d["region"] == "us-east-1"
        assert d["env_vars"] == {"LOG_LEVEL": "info"}
        assert d["secrets"] == ["API_KEY"]


# ---------------------------------------------------------------
# PromptConfig
# ---------------------------------------------------------------


class TestPromptConfig:
    def test_prompt_config_to_dict(self) -> None:
        cfg = PromptConfig(system="Be helpful.")
        assert cfg.to_dict() == {"system": "Be helpful."}
