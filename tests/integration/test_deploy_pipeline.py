"""Integration tests for the AgentBreeder deploy pipeline.

Tests multi-component integration (parse -> validate -> resolve -> build -> deploy -> register)
with mocked external services (Docker, cloud providers) but real internal logic.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.builder import BuildError, DeployEngine, DeployError, PipelineStep
from engine.config_parser import (
    AgentConfig,
    CloudType,
    ConfigParseError,
    FrameworkType,
    parse_config,
    validate_config,
)
from engine.deployers.base import DeployResult, HealthStatus, InfraResult
from engine.governance import RBACDeniedError
from engine.resolver import resolve_dependencies
from engine.runtimes.base import ContainerImage, RuntimeValidationResult
from engine.secrets.env_backend import EnvBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_YAML = """\
name: test-agent
version: 1.0.0
team: engineering
owner: alice@example.com
framework: langgraph
model:
  primary: gpt-4o
  fallback: claude-sonnet-4
deploy:
  cloud: local
"""

VALID_YAML_WITH_TOOLS = """\
name: tool-agent
version: 2.0.0
team: data-science
owner: bob@example.com
framework: langgraph
model:
  primary: claude-sonnet-4
tools:
  - ref: tools/zendesk-mcp
  - ref: tools/order-lookup
knowledge_bases:
  - ref: kb/product-docs
deploy:
  cloud: local
"""

VALID_YAML_GCP = """\
name: gcp-agent
version: 1.0.0
team: platform
owner: carol@example.com
framework: openai_agents
model:
  primary: gpt-4o
deploy:
  cloud: gcp
  runtime: cloud-run
  region: us-central1
"""

ORCHESTRATION_YAML = """\
name: support-pipeline
version: 1.0.0
strategy: sequential
agents:
  - ref: agents/classifier
  - ref: agents/responder
shared_state:
  type: dict
  backend: in_memory
deploy:
  target: local
"""

INVALID_YAML_MISSING_FIELDS = """\
name: bad-agent
version: not-semver
"""

INVALID_YAML_SYNTAX = """\
name: test
  bad_indent: value
   broken: true
"""


def _make_agent_dir(yaml_content: str = VALID_YAML) -> Path:
    """Create a temp agent directory with valid files."""
    d = Path(tempfile.mkdtemp())
    (d / "agent.yaml").write_text(yaml_content)
    (d / "agent.py").write_text("graph = None")
    (d / "requirements.txt").write_text("langgraph>=0.2.0")
    return d


def _mock_runtime():
    """Create a mock runtime builder that validates + builds successfully."""
    runtime = MagicMock()
    runtime.validate.return_value = RuntimeValidationResult(valid=True, errors=[])
    runtime.build.return_value = ContainerImage(
        tag="agentbreeder-test-agent:1.0.0",
        dockerfile_content="FROM python:3.11",
        context_dir=Path(tempfile.mkdtemp()),
    )
    return runtime


def _mock_deployer():
    """Create a mock deployer that provisions, deploys, and passes health checks."""
    deployer = AsyncMock()
    deployer.provision.return_value = InfraResult(
        endpoint_url="http://localhost:8080",
        resource_ids={"port": "8080"},
    )
    deployer.deploy.return_value = DeployResult(
        endpoint_url="http://localhost:8080",
        container_id="container-abc123",
        status="running",
        agent_name="test-agent",
        version="1.0.0",
    )
    deployer.health_check.return_value = HealthStatus(
        healthy=True, checks={"reachable": True, "healthy": True}
    )
    deployer.teardown = AsyncMock()
    return deployer


# ===========================================================================
# 1. Full deploy pipeline: valid YAML -> parse -> RBAC -> resolve -> build -> deploy -> register
# ===========================================================================


class TestFullDeployPipeline:
    """Tests for the complete deploy pipeline end-to-end."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self) -> None:
        """A valid agent.yaml should pass through all 8 pipeline steps."""
        agent_dir = _make_agent_dir()
        config_path = agent_dir / "agent.yaml"
        runtime = _mock_runtime()
        deployer = _mock_deployer()

        steps_seen: list[str] = []

        def on_step(step: PipelineStep) -> None:
            if step.status in ("running", "completed"):
                steps_seen.append(f"{step.name}:{step.status}")

        engine = DeployEngine(on_step=on_step)

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=deployer),
        ):
            result = await engine.deploy(config_path, target="local", user="alice")

        assert result.status == "running"
        assert result.endpoint_url == "http://localhost:8080"
        assert result.agent_name == "test-agent"
        # All 8 steps should have run
        completed_steps = [s for s in steps_seen if s.endswith(":completed")]
        assert len(completed_steps) == 8

    @pytest.mark.asyncio
    async def test_pipeline_step_order(self) -> None:
        """Steps must execute in the correct order."""
        agent_dir = _make_agent_dir()
        step_names: list[str] = []

        def on_step(step: PipelineStep) -> None:
            if step.status == "running":
                step_names.append(step.name)

        engine = DeployEngine(on_step=on_step)

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
        ):
            await engine.deploy(agent_dir / "agent.yaml", target="local")

        expected_order = [
            "Parse & validate YAML",
            "RBAC check",
            "Resolve dependencies",
            "Build container",
            "Provision infrastructure",
            "Deploy & health check",
            "Register in registry",
            "Return endpoint",
        ]
        assert step_names == expected_order

    @pytest.mark.asyncio
    async def test_pipeline_with_target_override(self) -> None:
        """Specifying --target should override the deploy.cloud in YAML."""
        agent_dir = _make_agent_dir()
        runtime = _mock_runtime()
        deployer = _mock_deployer()

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=deployer) as mock_get_deployer,
        ):
            await engine.deploy(agent_dir / "agent.yaml", target="local")

        # get_deployer was called with local
        mock_get_deployer.assert_called_once_with(CloudType.local, None)

    @pytest.mark.asyncio
    async def test_pipeline_with_cloud_run_target(self) -> None:
        """Specifying target=cloud-run maps to GCP with correct runtime."""
        agent_dir = _make_agent_dir()
        runtime = _mock_runtime()
        deployer = _mock_deployer()

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=deployer) as mock_get_deployer,
        ):
            await engine.deploy(agent_dir / "agent.yaml", target="cloud-run")

        mock_get_deployer.assert_called_once_with(CloudType.gcp, "cloud-run")

    @pytest.mark.asyncio
    async def test_pipeline_registers_agent_in_local_registry(self) -> None:
        """After successful deploy, the agent should be written to the registry file."""
        agent_dir = _make_agent_dir()
        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
            patch("engine.builder.REGISTRY_DIR", Path(tempfile.mkdtemp())),
        ):
            import engine.builder as builder_mod

            result = await engine.deploy(agent_dir / "agent.yaml", target="local")

            registry_file = builder_mod.REGISTRY_DIR / "agents.json"
            assert registry_file.exists()
            registry = json.loads(registry_file.read_text())
            assert "test-agent" in registry
            assert registry["test-agent"]["version"] == "1.0.0"
            assert registry["test-agent"]["endpoint_url"] == result.endpoint_url
            assert registry["test-agent"]["status"] == "running"


# ===========================================================================
# 2. RBAC failure stops pipeline before build
# ===========================================================================


class TestRBACFailure:
    """Tests that RBAC failures halt the pipeline early."""

    @pytest.mark.asyncio
    async def test_rbac_denied_stops_pipeline(self) -> None:
        """When check_rbac raises RBACDeniedError, no build or deploy should happen."""
        agent_dir = _make_agent_dir()
        runtime = _mock_runtime()
        deployer = _mock_deployer()

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=deployer),
            patch(
                "engine.builder.check_rbac",
                side_effect=RBACDeniedError("alice", "engineering", "deploy"),
            ),
        ):
            with pytest.raises(RBACDeniedError, match="alice.*not authorized"):
                await engine.deploy(agent_dir / "agent.yaml", target="local", user="alice")

        # Build and deploy should never have been called
        runtime.validate.assert_not_called()
        runtime.build.assert_not_called()
        deployer.provision.assert_not_called()
        deployer.deploy.assert_not_called()

    @pytest.mark.asyncio
    async def test_rbac_step_marked_failed(self) -> None:
        """The RBAC step should be marked as failed in step tracking."""
        agent_dir = _make_agent_dir()
        failed_steps: list[str] = []

        def on_step(step: PipelineStep) -> None:
            if step.status == "failed":
                failed_steps.append(step.name)

        engine = DeployEngine(on_step=on_step)

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
            patch(
                "engine.builder.check_rbac",
                side_effect=RBACDeniedError("alice", "engineering", "deploy"),
            ),
        ):
            with pytest.raises(RBACDeniedError):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

        assert "RBAC check" in failed_steps

    def test_rbac_denied_error_attributes(self) -> None:
        """RBACDeniedError should carry user, team, and action info."""
        err = RBACDeniedError("bob", "data-team", "deploy")
        assert err.user == "bob"
        assert err.team == "data-team"
        assert err.action == "deploy"
        assert "bob" in str(err)
        assert "data-team" in str(err)


# ===========================================================================
# 3. Invalid YAML fails at parse step
# ===========================================================================


class TestInvalidYAML:
    """Tests that malformed YAML is caught at the parse step."""

    def test_missing_required_fields(self) -> None:
        """YAML missing required fields should produce validation errors."""
        agent_dir = _make_agent_dir(INVALID_YAML_MISSING_FIELDS)
        result = validate_config(agent_dir / "agent.yaml")
        assert not result.valid
        assert len(result.errors) > 0
        error_paths = [e.path for e in result.errors]
        # Missing model, deploy, etc. should be caught
        assert any("model" in p or "(root)" in p for p in error_paths)

    def test_invalid_version_format(self) -> None:
        """Non-semver version should fail validation."""
        yaml_content = """\
name: test-agent
version: 1.0
team: engineering
owner: alice@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        agent_dir = _make_agent_dir(yaml_content)
        result = validate_config(agent_dir / "agent.yaml")
        assert not result.valid

    def test_invalid_framework(self) -> None:
        """Unknown framework should fail validation."""
        yaml_content = """\
name: test-agent
version: 1.0.0
team: engineering
owner: alice@example.com
framework: unknown_framework
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        agent_dir = _make_agent_dir(yaml_content)
        result = validate_config(agent_dir / "agent.yaml")
        assert not result.valid

    def test_file_not_found(self) -> None:
        """Non-existent file should produce a clear error."""
        result = validate_config(Path("/nonexistent/agent.yaml"))
        assert not result.valid
        assert any("not found" in e.message.lower() for e in result.errors)

    def test_empty_yaml(self) -> None:
        """Empty YAML file should produce a clear error."""
        agent_dir = _make_agent_dir("")
        # Rewrite to truly empty
        (agent_dir / "agent.yaml").write_text("")
        result = validate_config(agent_dir / "agent.yaml")
        assert not result.valid

    @pytest.mark.asyncio
    async def test_parse_failure_stops_pipeline(self) -> None:
        """Parse errors should prevent the pipeline from continuing."""
        agent_dir = _make_agent_dir(INVALID_YAML_MISSING_FIELDS)
        runtime = _mock_runtime()
        deployer = _mock_deployer()

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=deployer),
        ):
            with pytest.raises(ConfigParseError):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

        runtime.validate.assert_not_called()
        deployer.deploy.assert_not_called()

    def test_parse_config_raises_on_invalid(self) -> None:
        """parse_config should raise ConfigParseError with structured errors."""
        agent_dir = _make_agent_dir(INVALID_YAML_MISSING_FIELDS)
        with pytest.raises(ConfigParseError) as exc_info:
            parse_config(agent_dir / "agent.yaml")
        assert len(exc_info.value.errors) > 0

    def test_valid_yaml_parses_successfully(self) -> None:
        """A well-formed agent.yaml should parse into an AgentConfig."""
        agent_dir = _make_agent_dir(VALID_YAML)
        config = parse_config(agent_dir / "agent.yaml")
        assert isinstance(config, AgentConfig)
        assert config.name == "test-agent"
        assert config.version == "1.0.0"
        assert config.framework == FrameworkType.langgraph
        assert config.model.primary == "gpt-4o"
        assert config.model.fallback == "claude-sonnet-4"
        assert config.deploy.cloud == CloudType.local


# ===========================================================================
# 4. Dependency resolution with missing refs
# ===========================================================================


class TestDependencyResolution:
    """Tests for the resolve_dependencies step."""

    def test_resolve_passes_through_tool_refs(self) -> None:
        """Tool refs should be preserved after resolution (stub behavior)."""
        agent_dir = _make_agent_dir(VALID_YAML_WITH_TOOLS)
        config = parse_config(agent_dir / "agent.yaml")
        original_tool_count = len(config.tools)
        resolved = resolve_dependencies(config)
        assert len(resolved.tools) >= original_tool_count
        refs = [t.ref for t in resolved.tools if t.ref]
        assert "tools/zendesk-mcp" in refs
        assert "tools/order-lookup" in refs

    def test_resolve_passes_through_kb_refs(self) -> None:
        """Knowledge base refs should be preserved after resolution."""
        agent_dir = _make_agent_dir(VALID_YAML_WITH_TOOLS)
        config = parse_config(agent_dir / "agent.yaml")
        resolved = resolve_dependencies(config)
        kb_refs = [kb.ref for kb in resolved.knowledge_bases]
        assert "kb/product-docs" in kb_refs

    def test_resolve_with_no_refs(self) -> None:
        """An agent with no tool/kb refs should resolve without error."""
        agent_dir = _make_agent_dir(VALID_YAML)
        config = parse_config(agent_dir / "agent.yaml")
        resolved = resolve_dependencies(config)
        assert resolved is config  # same object, no mutation needed

    def test_resolve_preserves_config_identity(self) -> None:
        """Resolution should return a config (not create a new one unnecessarily)."""
        agent_dir = _make_agent_dir(VALID_YAML)
        config = parse_config(agent_dir / "agent.yaml")
        resolved = resolve_dependencies(config)
        assert resolved.name == config.name
        assert resolved.model == config.model


# ===========================================================================
# 5. Build failure triggers rollback
# ===========================================================================


class TestBuildFailure:
    """Tests that build failures are handled correctly."""

    @pytest.mark.asyncio
    async def test_build_validation_failure_stops_pipeline(self) -> None:
        """When runtime validation fails, the pipeline should stop at build step."""
        agent_dir = _make_agent_dir()
        runtime = MagicMock()
        runtime.validate.return_value = RuntimeValidationResult(
            valid=False, errors=["Missing agent.py entry point"]
        )
        deployer = _mock_deployer()

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=deployer),
        ):
            with pytest.raises(BuildError, match="Validation failed"):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

        deployer.provision.assert_not_called()
        deployer.deploy.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_exception_propagates(self) -> None:
        """If runtime.build() raises, the error should propagate."""
        agent_dir = _make_agent_dir()
        runtime = MagicMock()
        runtime.validate.return_value = RuntimeValidationResult(valid=True, errors=[])
        runtime.build.side_effect = RuntimeError("Docker daemon not running")
        deployer = _mock_deployer()

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=deployer),
        ):
            with pytest.raises(RuntimeError, match="Docker daemon not running"):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

        deployer.provision.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_failure_step_tracking(self) -> None:
        """Build failure should be tracked as a failed step."""
        agent_dir = _make_agent_dir()
        runtime = MagicMock()
        runtime.validate.return_value = RuntimeValidationResult(
            valid=False, errors=["No entrypoint"]
        )
        failed_steps: list[str] = []

        def on_step(step: PipelineStep) -> None:
            if step.status == "failed":
                failed_steps.append(step.name)

        engine = DeployEngine(on_step=on_step)

        with (
            patch("engine.builder.get_runtime_from_config", return_value=runtime),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
        ):
            with pytest.raises(BuildError):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

        assert "Build container" in failed_steps


# ===========================================================================
# 6. Deploy failure cleanup
# ===========================================================================


class TestDeployFailure:
    """Tests that deploy failures trigger proper cleanup."""

    @pytest.mark.asyncio
    async def test_health_check_failure_triggers_teardown(self) -> None:
        """If health check fails, the deployer.teardown() should be called."""
        agent_dir = _make_agent_dir()
        deployer = _mock_deployer()
        deployer.health_check.return_value = HealthStatus(
            healthy=False, checks={"reachable": True, "healthy": False}
        )

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=deployer),
        ):
            with pytest.raises(DeployError, match="Health check failed"):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

        deployer.teardown.assert_awaited_once_with("test-agent")

    @pytest.mark.asyncio
    async def test_provision_failure_stops_before_deploy(self) -> None:
        """If provisioning fails, deploy should not be attempted."""
        agent_dir = _make_agent_dir()
        deployer = _mock_deployer()
        deployer.provision.side_effect = RuntimeError("Insufficient quota")

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=deployer),
        ):
            with pytest.raises(RuntimeError, match="Insufficient quota"):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

        deployer.deploy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deploy_failure_does_not_register(self) -> None:
        """If deploy fails, the agent should NOT be registered."""
        agent_dir = _make_agent_dir()
        deployer = _mock_deployer()
        deployer.deploy.side_effect = RuntimeError("Container crashed on startup")

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=deployer),
            patch("engine.builder.REGISTRY_DIR", Path(tempfile.mkdtemp())),
        ):
            import engine.builder as builder_mod

            with pytest.raises(RuntimeError, match="Container crashed"):
                await engine.deploy(agent_dir / "agent.yaml", target="local")

            registry_file = builder_mod.REGISTRY_DIR / "agents.json"
            # Registry file should either not exist or not contain the agent
            if registry_file.exists():
                data = json.loads(registry_file.read_text())
                assert "test-agent" not in data


# ===========================================================================
# 7. Registry registration after successful deploy
# ===========================================================================


class TestRegistryRegistration:
    """Tests that successful deploys are properly registered."""

    @pytest.mark.asyncio
    async def test_registration_creates_registry_entry(self) -> None:
        """After a successful deploy, the agent should appear in the registry."""
        agent_dir = _make_agent_dir()
        registry_dir = Path(tempfile.mkdtemp())

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
            patch("engine.builder.REGISTRY_DIR", registry_dir),
        ):
            result = await engine.deploy(agent_dir / "agent.yaml", target="local")

        registry = json.loads((registry_dir / "agents.json").read_text())
        entry = registry["test-agent"]
        assert entry["name"] == "test-agent"
        assert entry["version"] == "1.0.0"
        assert entry["team"] == "engineering"
        assert entry["owner"] == "alice@example.com"
        assert entry["framework"] == "langgraph"
        assert entry["model_primary"] == "gpt-4o"
        assert entry["model_fallback"] == "claude-sonnet-4"
        assert entry["endpoint_url"] == result.endpoint_url
        assert entry["status"] == "running"

    @pytest.mark.asyncio
    async def test_re_registration_updates_existing_entry(self) -> None:
        """Deploying the same agent again should update the existing registry entry."""
        registry_dir = Path(tempfile.mkdtemp())

        # First deploy
        agent_dir = _make_agent_dir()
        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
            patch("engine.builder.REGISTRY_DIR", registry_dir),
        ):
            await engine.deploy(agent_dir / "agent.yaml", target="local")

        # Second deploy with updated version
        v2_yaml = VALID_YAML.replace("version: 1.0.0", "version: 2.0.0")
        agent_dir2 = _make_agent_dir(v2_yaml)

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
            patch("engine.builder.REGISTRY_DIR", registry_dir),
        ):
            await engine.deploy(agent_dir2 / "agent.yaml", target="local")

        registry = json.loads((registry_dir / "agents.json").read_text())
        assert registry["test-agent"]["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_registration_includes_timestamp(self) -> None:
        """Registry entries should include a timestamp."""
        agent_dir = _make_agent_dir()
        registry_dir = Path(tempfile.mkdtemp())

        engine = DeployEngine()

        with (
            patch("engine.builder.get_runtime_from_config", return_value=_mock_runtime()),
            patch("engine.builder.get_deployer", return_value=_mock_deployer()),
            patch("engine.builder.REGISTRY_DIR", registry_dir),
        ):
            await engine.deploy(agent_dir / "agent.yaml", target="local")

        registry = json.loads((registry_dir / "agents.json").read_text())
        assert "registered_at" in registry["test-agent"]


# ===========================================================================
# 8. End-to-end config parsing integration
# ===========================================================================


class TestConfigParsingIntegration:
    """Tests that cover realistic config parsing scenarios."""

    def test_parse_with_all_fields(self) -> None:
        """A fully populated agent.yaml should parse all fields correctly."""
        full_yaml = """\
name: full-agent
version: 3.2.1
description: "A fully configured agent"
team: platform-team
owner: admin@example.com
tags: [production, support, v3]
framework: langgraph
model:
  primary: claude-sonnet-4
  fallback: gpt-4o
  temperature: 0.5
  max_tokens: 8192
tools:
  - ref: tools/zendesk-mcp
  - name: search
    type: function
    description: "Search knowledge base"
deploy:
  cloud: local
  scaling:
    min: 2
    max: 20
    target_cpu: 80
  resources:
    cpu: "2"
    memory: "4Gi"
  env_vars:
    LOG_LEVEL: info
  secrets:
    - API_KEY
guardrails:
  - pii_detection
  - hallucination_check
access:
  visibility: public
  require_approval: false
"""
        agent_dir = _make_agent_dir(full_yaml)
        config = parse_config(agent_dir / "agent.yaml")

        assert config.name == "full-agent"
        assert config.version == "3.2.1"
        assert config.description == "A fully configured agent"
        assert "production" in config.tags
        assert config.model.temperature == 0.5
        assert config.model.max_tokens == 8192
        assert config.deploy.scaling.min == 2
        assert config.deploy.scaling.max == 20
        assert config.deploy.resources.cpu == "2"
        assert config.deploy.resources.memory == "4Gi"
        assert config.deploy.env_vars["LOG_LEVEL"] == "info"
        assert "API_KEY" in config.deploy.secrets
        assert len(config.guardrails) == 2
        assert config.access.visibility.value == "public"

    def test_parse_minimal_config(self) -> None:
        """A minimal agent.yaml should use sensible defaults."""
        agent_dir = _make_agent_dir(VALID_YAML)
        config = parse_config(agent_dir / "agent.yaml")
        assert config.deploy.scaling.min == 1
        assert config.deploy.scaling.max == 10
        assert config.deploy.scaling.target_cpu == 70
        assert config.deploy.resources.cpu == "0.5"
        assert config.deploy.resources.memory == "1Gi"
        assert config.access.visibility.value == "team"
        assert config.access.require_approval is False

    def test_gcp_config_parses_runtime(self) -> None:
        """GCP config with runtime and region should parse correctly."""
        agent_dir = _make_agent_dir(VALID_YAML_GCP)
        config = parse_config(agent_dir / "agent.yaml")
        assert config.deploy.cloud == CloudType.gcp
        assert config.deploy.runtime == "cloud-run"
        assert config.deploy.region == "us-central1"
        assert config.framework == FrameworkType.openai_agents


# ===========================================================================
# 9. Orchestration deploy: multi-agent orchestration
# ===========================================================================


class TestOrchestrationDeploy:
    """Tests for orchestration YAML parsing and validation."""

    def test_orchestration_yaml_parses(self) -> None:
        """Orchestration YAML should parse into OrchestrationConfig."""
        from engine.orchestration_parser import parse_orchestration

        yaml_content = """\
name: support-pipeline
version: 1.0.0
strategy: sequential
agents:
  classifier:
    ref: agents/classifier
  responder:
    ref: agents/responder
"""
        d = Path(tempfile.mkdtemp())
        (d / "orchestration.yaml").write_text(yaml_content)
        config = parse_orchestration(d / "orchestration.yaml")
        assert config.name == "support-pipeline"
        assert config.strategy.value == "sequential"
        assert len(config.agents) == 2

    def test_orchestration_invalid_strategy(self) -> None:
        """Invalid strategy should fail validation."""
        from engine.orchestration_parser import validate_orchestration

        bad_yaml = """\
name: bad-pipeline
version: 1.0.0
strategy: invalid_strategy
agents:
  test-agent:
    ref: agents/test
"""
        d = Path(tempfile.mkdtemp())
        (d / "orchestration.yaml").write_text(bad_yaml)
        result = validate_orchestration(d / "orchestration.yaml")
        assert not result.valid

    def test_orchestration_missing_agents(self) -> None:
        """Orchestration without agents should fail validation."""
        from engine.orchestration_parser import validate_orchestration

        bad_yaml = """\
name: empty-pipeline
version: 1.0.0
strategy: sequential
"""
        d = Path(tempfile.mkdtemp())
        (d / "orchestration.yaml").write_text(bad_yaml)
        result = validate_orchestration(d / "orchestration.yaml")
        assert not result.valid

    def test_orchestration_with_shared_state(self) -> None:
        """Orchestration with shared state config should parse correctly."""
        from engine.orchestration_parser import parse_orchestration

        yaml_content = """\
name: stateful-pipeline
version: 1.0.0
strategy: parallel
agents:
  analyzer:
    ref: agents/analyzer
  summarizer:
    ref: agents/summarizer
shared_state:
  type: dict
  backend: in_memory
deploy:
  target: local
"""
        d = Path(tempfile.mkdtemp())
        (d / "orchestration.yaml").write_text(yaml_content)
        config = parse_orchestration(d / "orchestration.yaml")
        assert config.shared_state is not None
        assert config.shared_state.type == "dict"


# ===========================================================================
# 10. Secret resolution during deploy
# ===========================================================================


class TestSecretResolution:
    """Tests for secret resolution using the env backend."""

    @pytest.mark.asyncio
    async def test_env_backend_set_and_get(self) -> None:
        """Secrets stored via env backend should be retrievable."""
        env_file = Path(tempfile.mkdtemp()) / ".env"
        backend = EnvBackend(env_file=env_file)

        await backend.set("MY_SECRET", "super-secret-value")
        value = await backend.get("MY_SECRET")
        assert value == "super-secret-value"

    @pytest.mark.asyncio
    async def test_env_backend_list_masks_values(self) -> None:
        """Listing secrets should show masked values, not plain text."""
        env_file = Path(tempfile.mkdtemp()) / ".env"
        backend = EnvBackend(env_file=env_file)

        await backend.set("API_KEY", "sk-1234567890abcdef")
        entries = await backend.list()
        api_entry = next(e for e in entries if e.name == "API_KEY")
        assert "1234567890" not in api_entry.masked_value
        assert api_entry.masked_value.startswith("••••")

    @pytest.mark.asyncio
    async def test_env_backend_delete(self) -> None:
        """Deleting a secret should remove it from the backend."""
        env_file = Path(tempfile.mkdtemp()) / ".env"
        backend = EnvBackend(env_file=env_file)

        await backend.set("TEMP_KEY", "temp-value")
        await backend.delete("TEMP_KEY")
        value = await backend.get("TEMP_KEY")
        assert value is None

    @pytest.mark.asyncio
    async def test_env_backend_delete_nonexistent_raises(self) -> None:
        """Deleting a non-existent secret should raise KeyError."""
        env_file = Path(tempfile.mkdtemp()) / ".env"
        backend = EnvBackend(env_file=env_file)

        with pytest.raises(KeyError, match="not found"):
            await backend.delete("NONEXISTENT")

    @pytest.mark.asyncio
    async def test_env_backend_rotate(self) -> None:
        """Rotating a secret should update its value."""
        env_file = Path(tempfile.mkdtemp()) / ".env"
        backend = EnvBackend(env_file=env_file)

        await backend.set("ROTATE_KEY", "old-value")
        await backend.rotate("ROTATE_KEY", "new-value")
        value = await backend.get("ROTATE_KEY")
        assert value == "new-value"

    def test_deploy_config_carries_secrets(self) -> None:
        """Agent config with secrets should preserve them for deploy."""
        yaml_content = """\
name: secret-agent
version: 1.0.0
team: security
owner: sec@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
  secrets:
    - OPENAI_API_KEY
    - ZENDESK_TOKEN
"""
        agent_dir = _make_agent_dir(yaml_content)
        config = parse_config(agent_dir / "agent.yaml")
        assert "OPENAI_API_KEY" in config.deploy.secrets
        assert "ZENDESK_TOKEN" in config.deploy.secrets
        assert len(config.deploy.secrets) == 2
