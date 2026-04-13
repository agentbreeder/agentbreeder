"""Tests for Phase 4 CrewAI advanced features."""
import asyncio
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import (
    AgentConfig,
    CrewAIConfig,
    FrameworkType,
    parse_config,
    validate_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides: object) -> AgentConfig:
    defaults: dict = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "eng",
        "owner": "dev@example.com",
        "framework": FrameworkType.crewai,
        "model": {"primary": "claude-sonnet-4"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ---------------------------------------------------------------------------
# Task 1: CrewAIConfig Pydantic model
# ---------------------------------------------------------------------------

class TestCrewAIConfig:
    def test_crewai_config_defaults(self) -> None:
        cfg = CrewAIConfig()
        assert cfg.process == "sequential"
        assert cfg.manager_llm is None
        assert cfg.verbose is False
        assert cfg.memory is False
        assert cfg.memory_config is None

    def test_crewai_config_hierarchical(self) -> None:
        cfg = CrewAIConfig(process="hierarchical", manager_llm="claude-opus-4", verbose=True)
        assert cfg.process == "hierarchical"
        assert cfg.manager_llm == "claude-opus-4"
        assert cfg.verbose is True

    def test_crewai_config_parallel(self) -> None:
        cfg = CrewAIConfig(process="parallel")
        assert cfg.process == "parallel"

    def test_crewai_config_rejects_unknown_process(self) -> None:
        with pytest.raises(Exception):
            CrewAIConfig(process="unknown-mode")

    def test_crewai_config_memory_config_dict(self) -> None:
        cfg = CrewAIConfig(memory=True, memory_config={"provider": "mem0", "config": {"user_id": "u1"}})
        assert cfg.memory is True
        assert cfg.memory_config["provider"] == "mem0"

    def test_agent_config_crewai_field_defaults_none(self) -> None:
        cfg = _make_config()
        assert cfg.crewai is None

    def test_agent_config_crewai_field_accepts_config(self) -> None:
        cfg = _make_config(crewai={"process": "hierarchical", "manager_llm": "claude-opus-4"})
        assert cfg.crewai is not None
        assert cfg.crewai.process == "hierarchical"
        assert cfg.crewai.manager_llm == "claude-opus-4"

    def test_agent_config_crewai_field_coerces_from_dict(self) -> None:
        cfg = _make_config(crewai={"process": "parallel", "verbose": True})
        assert isinstance(cfg.crewai, CrewAIConfig)
        assert cfg.crewai.process == "parallel"
        assert cfg.crewai.verbose is True


class TestAgentSchemaCrewAIBlock:
    def test_schema_accepts_hierarchical_crewai_block(self, tmp_path: Path) -> None:
        yaml_content = """\
name: hier-agent
version: 1.0.0
team: eng
owner: dev@example.com
framework: crewai
model:
  primary: claude-sonnet-4
deploy:
  cloud: local
crewai:
  process: hierarchical
  manager_llm: claude-opus-4
  verbose: true
  memory: true
"""
        p = tmp_path / "agent.yaml"
        p.write_text(yaml_content)
        result = validate_config(p)
        assert result.valid, result.errors

    def test_schema_accepts_parallel_crewai_block(self, tmp_path: Path) -> None:
        yaml_content = """\
name: par-agent
version: 1.0.0
team: eng
owner: dev@example.com
framework: crewai
model:
  primary: claude-sonnet-4
deploy:
  cloud: local
crewai:
  process: parallel
  verbose: false
"""
        p = tmp_path / "agent.yaml"
        p.write_text(yaml_content)
        result = validate_config(p)
        assert result.valid, result.errors

    def test_schema_rejects_invalid_process_value(self, tmp_path: Path) -> None:
        yaml_content = """\
name: bad-agent
version: 1.0.0
team: eng
owner: dev@example.com
framework: crewai
model:
  primary: claude-sonnet-4
deploy:
  cloud: local
crewai:
  process: turbo
"""
        p = tmp_path / "agent.yaml"
        p.write_text(yaml_content)
        result = validate_config(p)
        assert not result.valid
        assert any("process" in str(e).lower() or "turbo" in str(e).lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Task 2: crewai-tools in requirements + process/manager_llm ENV vars
# ---------------------------------------------------------------------------

import tempfile
from engine.runtimes.crewai import CrewAIRuntime


class TestCrewAIRuntimeAdvancedRequirements:
    def test_get_requirements_includes_crewai_tools(self) -> None:
        rt = CrewAIRuntime()
        reqs = rt.get_requirements(_make_config())
        assert any("crewai-tools" in r for r in reqs), f"crewai-tools missing from {reqs}"

    def test_crewai_tools_version_pinned_gte_0_4(self) -> None:
        rt = CrewAIRuntime()
        reqs = rt.get_requirements(_make_config())
        tools_req = next(r for r in reqs if "crewai-tools" in r)
        assert ">=" in tools_req, f"Expected pinned version constraint, got {tools_req!r}"


class TestCrewAIRuntimeBuildEnvVars:
    def _build_and_read_dockerfile(self, crewai_cfg: dict | None) -> str:
        rt = CrewAIRuntime()
        config = _make_config(crewai=crewai_cfg)
        with tempfile.TemporaryDirectory() as d:
            agent_dir = Path(d)
            (agent_dir / "crew.py").write_text("crew = None\n")
            (agent_dir / "requirements.txt").write_text("crewai\n")
            image = rt.build(agent_dir, config)
            dockerfile = (image.context_dir / "Dockerfile").read_text()
        return dockerfile

    def test_build_no_crewai_block_omits_process_env(self) -> None:
        dockerfile = self._build_and_read_dockerfile(None)
        assert "AGENT_CREWAI_PROCESS" not in dockerfile

    def test_build_sequential_process_written_as_env(self) -> None:
        dockerfile = self._build_and_read_dockerfile({"process": "sequential"})
        assert "ENV AGENT_CREWAI_PROCESS=sequential" in dockerfile

    def test_build_hierarchical_process_written_as_env(self) -> None:
        dockerfile = self._build_and_read_dockerfile(
            {"process": "hierarchical", "manager_llm": "claude-opus-4"}
        )
        assert "ENV AGENT_CREWAI_PROCESS=hierarchical" in dockerfile
        assert "ENV AGENT_CREWAI_MANAGER_LLM=claude-opus-4" in dockerfile

    def test_build_parallel_process_written_as_env(self) -> None:
        dockerfile = self._build_and_read_dockerfile({"process": "parallel"})
        assert "ENV AGENT_CREWAI_PROCESS=parallel" in dockerfile

    def test_build_verbose_true_written_as_env(self) -> None:
        dockerfile = self._build_and_read_dockerfile({"verbose": True})
        assert "ENV AGENT_CREWAI_VERBOSE=true" in dockerfile

    def test_build_memory_true_written_as_env(self) -> None:
        dockerfile = self._build_and_read_dockerfile({"memory": True})
        assert "ENV AGENT_CREWAI_MEMORY=true" in dockerfile

    def test_build_manager_llm_absent_when_not_set(self) -> None:
        dockerfile = self._build_and_read_dockerfile({"process": "sequential"})
        assert "AGENT_CREWAI_MANAGER_LLM" not in dockerfile


# ---------------------------------------------------------------------------
# Task 3: Flow detection + dispatch in crewai_server.py
# ---------------------------------------------------------------------------

def _make_crew_module(has_flow: bool = False, has_crew: bool = True) -> types.ModuleType:
    mod = types.ModuleType("agent")
    if has_flow:
        flow_instance = MagicMock()
        flow_instance.kickoff_async = AsyncMock(return_value="flow-result")
        mod.flow = flow_instance
    if has_crew:
        crew_instance = MagicMock()
        crew_instance.kickoff = MagicMock(return_value="crew-result")
        mod.crew = crew_instance
    return mod


def _load_crewai_server():
    """Load crewai_server module with mocked crewai SDK."""
    for key in list(sys.modules.keys()):
        if "crewai_server" in key:
            del sys.modules[key]
    fake_crewai = types.ModuleType("crewai")
    fake_crewai_tools = types.ModuleType("crewai.tools")
    class FakeBaseTool:
        name = ""; description = ""; args_schema = None
        def _run(self, **kw): return ""
    fake_crewai_tools.BaseTool = FakeBaseTool
    fake_engine_tb = types.ModuleType("engine.tool_bridge")
    fake_engine_tb.to_crewai_tools = lambda refs: []
    fake_engine_cp = types.ModuleType("engine.config_parser")
    class _TR:
        def __init__(self, **kw): [setattr(self, k, v) for k, v in kw.items()]
    fake_engine_cp.ToolRef = _TR
    with patch.dict(sys.modules, {
        "crewai": fake_crewai, "crewai.tools": fake_crewai_tools,
        "engine.tool_bridge": fake_engine_tb, "engine.config_parser": fake_engine_cp,
    }):
        sys.path.insert(0, "engine/runtimes/templates")
        import crewai_server as srv
    return srv


class TestCrewAIServerFlowDetection:
    def test_detect_mode_returns_flow_when_flow_attribute_present(self) -> None:
        srv = _load_crewai_server()
        mod = _make_crew_module(has_flow=True, has_crew=False)
        mode, obj = srv._detect_mode(mod)
        assert mode == "flow"
        assert obj is mod.flow

    def test_detect_mode_returns_crew_when_only_crew_present(self) -> None:
        srv = _load_crewai_server()
        mod = _make_crew_module(has_flow=False, has_crew=True)
        mode, obj = srv._detect_mode(mod)
        assert mode == "crew"
        assert obj is mod.crew

    def test_detect_mode_prefers_flow_over_crew_when_both_present(self) -> None:
        srv = _load_crewai_server()
        mod = _make_crew_module(has_flow=True, has_crew=True)
        mode, obj = srv._detect_mode(mod)
        assert mode == "flow"

    def test_detect_mode_raises_when_neither_present(self) -> None:
        srv = _load_crewai_server()
        mod = types.ModuleType("agent")
        with pytest.raises(RuntimeError, match="neither 'flow' nor 'crew'"):
            srv._detect_mode(mod)


class TestCrewAIServerFlowDispatch:
    def test_flow_invoke_calls_kickoff_async(self) -> None:
        srv = _load_crewai_server()
        mod = _make_crew_module(has_flow=True, has_crew=False)
        result = asyncio.get_event_loop().run_until_complete(
            srv._dispatch(mod.flow, "flow", {"prompt": "hello"})
        )
        mod.flow.kickoff_async.assert_called_once_with(inputs={"prompt": "hello"})
        assert result == "flow-result"

    def test_crew_invoke_calls_kickoff_in_thread(self) -> None:
        srv = _load_crewai_server()
        mod = _make_crew_module(has_flow=False, has_crew=True)
        result = asyncio.get_event_loop().run_until_complete(
            srv._dispatch(mod.crew, "crew", {"prompt": "hello"})
        )
        mod.crew.kickoff.assert_called_once()
        assert result == "crew-result"


# ---------------------------------------------------------------------------
# Task 4: Structured output validation
# ---------------------------------------------------------------------------

class TestCrewAIServerStructuredOutput:
    def _get_srv(self):
        return _load_crewai_server()

    def test_validate_output_returns_none_when_no_schema(self) -> None:
        srv = self._get_srv()
        errors = srv._validate_output("any string", schema=None)
        assert errors is None

    def test_validate_output_passes_valid_json(self) -> None:
        srv = self._get_srv()
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        errors = srv._validate_output(json.dumps({"answer": "Paris"}), schema=schema)
        assert errors is None

    def test_validate_output_catches_missing_required_field(self) -> None:
        srv = self._get_srv()
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        errors = srv._validate_output(json.dumps({"wrong_key": "x"}), schema=schema)
        assert errors is not None
        assert len(errors) > 0

    def test_validate_output_catches_non_json_output(self) -> None:
        srv = self._get_srv()
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        errors = srv._validate_output("this is not json", schema=schema)
        assert errors is not None

    def test_validate_output_catches_wrong_type(self) -> None:
        srv = self._get_srv()
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        }
        errors = srv._validate_output(json.dumps({"count": "not-an-int"}), schema=schema)
        assert errors is not None


class TestCrewAIServerInvokeResponseSchema:
    def test_invoke_response_has_output_schema_errors_field(self) -> None:
        srv = _load_crewai_server()
        resp = srv.InvokeResponse(output="hello", mode="crew", output_schema_errors=None)
        assert hasattr(resp, "output_schema_errors")

    def test_invoke_response_output_schema_errors_defaults_none(self) -> None:
        srv = _load_crewai_server()
        resp = srv.InvokeResponse(output="hello", mode="crew")
        assert resp.output_schema_errors is None


# ---------------------------------------------------------------------------
# Task 5: Integration — hierarchical crew end-to-end
# ---------------------------------------------------------------------------

class TestHierarchicalCrewEndToEnd:
    _AGENT_YAML = """\
name: support-agent
version: 1.0.0
team: eng
owner: dev@example.com
framework: crewai
model:
  primary: claude-sonnet-4
  temperature: 0.3
  max_tokens: 2048
deploy:
  cloud: local
crewai:
  process: hierarchical
  manager_llm: claude-opus-4
  verbose: true
  memory: true
"""

    def test_yaml_parses_to_crewai_config(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(self._AGENT_YAML)
        config = parse_config(yaml_file)
        assert config.crewai is not None
        assert config.crewai.process == "hierarchical"
        assert config.crewai.manager_llm == "claude-opus-4"
        assert config.crewai.verbose is True
        assert config.crewai.memory is True

    def test_build_dockerfile_contains_all_crewai_env_vars(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(self._AGENT_YAML)
        config = parse_config(yaml_file)
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "crew.py").write_text("crew = None\n")
        (agent_dir / "requirements.txt").write_text("crewai\n")
        image = CrewAIRuntime().build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "ENV AGENT_CREWAI_PROCESS=hierarchical" in dockerfile
        assert "ENV AGENT_CREWAI_MANAGER_LLM=claude-opus-4" in dockerfile
        assert "ENV AGENT_CREWAI_VERBOSE=true" in dockerfile
        assert "ENV AGENT_CREWAI_MEMORY=true" in dockerfile

    def test_build_requirements_include_crewai_tools(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(self._AGENT_YAML)
        config = parse_config(yaml_file)
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "crew.py").write_text("crew = None\n")
        (agent_dir / "requirements.txt").write_text("crewai\n")
        image = CrewAIRuntime().build(agent_dir, config)
        req_text = (image.context_dir / "requirements.txt").read_text()
        assert "crewai-tools" in req_text

    def test_server_detects_crew_mode_for_hierarchical_agent(self) -> None:
        srv = _load_crewai_server()
        mod = types.ModuleType("agent")
        crew_mock = MagicMock()
        crew_mock.kickoff = MagicMock(return_value="ticket resolved")
        mod.crew = crew_mock
        mode, obj = srv._detect_mode(mod)
        assert mode == "crew"
        assert obj is crew_mock

    def test_server_dispatches_crew_kickoff_and_returns_result(self) -> None:
        srv = _load_crewai_server()
        crew_mock = MagicMock()
        crew_mock.kickoff = MagicMock(return_value="ticket resolved")
        result = asyncio.get_event_loop().run_until_complete(
            srv._dispatch(crew_mock, "crew", {"prompt": "resolve ticket #42"})
        )
        crew_mock.kickoff.assert_called_once()
        assert result == "ticket resolved"

    def test_schema_validates_full_hierarchical_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text(self._AGENT_YAML)
        result = validate_config(yaml_file)
        assert result.valid, result.errors
