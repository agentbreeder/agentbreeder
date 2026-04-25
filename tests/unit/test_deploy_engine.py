"""Tests for engine/builder.py — the deploy pipeline orchestrator."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.builder import DeployEngine, PipelineStep
from engine.config_parser import ConfigParseError
from engine.deployers.base import DeployResult, HealthStatus, InfraResult


def _make_agent_dir() -> Path:
    """Create a temp agent directory with valid files."""
    d = Path(tempfile.mkdtemp())
    (d / "agent.yaml").write_text("""\
name: test-agent
version: 1.0.0
team: test
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
    (d / "agent.py").write_text("graph = None")
    (d / "requirements.txt").write_text("langgraph>=0.2.0")
    return d


class TestPipelineStep:
    def test_step_lifecycle(self) -> None:
        step = PipelineStep("Test step", 1)
        assert step.status == "pending"
        step.start()
        assert step.status == "running"
        assert step.started_at is not None
        step.complete()
        assert step.status == "completed"
        assert step.completed_at is not None

    def test_step_failure(self) -> None:
        step = PipelineStep("Test step", 1)
        step.start()
        step.fail("something broke")
        assert step.status == "failed"
        assert step.error == "something broke"


class TestDeployEngine:
    @pytest.mark.asyncio
    async def test_deploy_calls_all_steps(self) -> None:
        """Verify all 8 pipeline steps are called in order."""
        agent_dir = _make_agent_dir()
        steps_seen: list[str] = []

        def on_step(step: PipelineStep) -> None:
            if step.status == "completed":
                steps_seen.append(step.name)

        mock_deployer = MagicMock()
        mock_deployer.provision = AsyncMock(
            return_value=InfraResult(endpoint_url="http://localhost:8080", resource_ids={})
        )
        mock_deployer.deploy = AsyncMock(
            return_value=DeployResult(
                endpoint_url="http://localhost:8080",
                container_id="abc123",
                status="running",
                agent_name="test-agent",
                version="1.0.0",
            )
        )
        mock_deployer.health_check = AsyncMock(
            return_value=HealthStatus(healthy=True, checks={"healthy": True})
        )

        with (
            patch("engine.builder.get_deployer", return_value=mock_deployer),
            patch("engine.builder.REGISTRY_DIR", agent_dir / "registry"),
        ):
            engine = DeployEngine(on_step=on_step)
            result = await engine.deploy(agent_dir / "agent.yaml")

        assert result.endpoint_url == "http://localhost:8080"
        assert result.agent_name == "test-agent"
        assert len(steps_seen) == 8

    @pytest.mark.asyncio
    async def test_deploy_fails_on_invalid_yaml(self) -> None:
        d = Path(tempfile.mkdtemp())
        (d / "agent.yaml").write_text("invalid: yaml: broken")

        engine = DeployEngine()
        with pytest.raises(ConfigParseError):
            await engine.deploy(d / "agent.yaml")

    @pytest.mark.asyncio
    async def test_deploy_fails_on_health_check(self) -> None:
        """If health check fails, deployer.teardown is called."""
        agent_dir = _make_agent_dir()

        mock_deployer = MagicMock()
        mock_deployer.provision = AsyncMock(
            return_value=InfraResult(endpoint_url="http://localhost:8080", resource_ids={})
        )
        mock_deployer.deploy = AsyncMock(
            return_value=DeployResult(
                endpoint_url="http://localhost:8080",
                container_id="abc123",
                status="running",
                agent_name="test-agent",
                version="1.0.0",
            )
        )
        mock_deployer.health_check = AsyncMock(
            return_value=HealthStatus(healthy=False, checks={"healthy": False})
        )
        mock_deployer.teardown = AsyncMock()

        with patch("engine.builder.get_deployer", return_value=mock_deployer):
            engine = DeployEngine()
            with pytest.raises(Exception, match="Health check failed"):
                await engine.deploy(agent_dir / "agent.yaml")

        mock_deployer.teardown.assert_called_once_with("test-agent")

    @pytest.mark.asyncio
    async def test_deploy_registers_agent(self) -> None:
        """After successful deploy, agent should be in local registry."""
        agent_dir = _make_agent_dir()
        registry_dir = agent_dir / "registry"

        mock_deployer = MagicMock()
        mock_deployer.provision = AsyncMock(
            return_value=InfraResult(endpoint_url="http://localhost:8080", resource_ids={})
        )
        mock_deployer.deploy = AsyncMock(
            return_value=DeployResult(
                endpoint_url="http://localhost:8080",
                container_id="abc123",
                status="running",
                agent_name="test-agent",
                version="1.0.0",
            )
        )
        mock_deployer.health_check = AsyncMock(
            return_value=HealthStatus(healthy=True, checks={"healthy": True})
        )

        with (
            patch("engine.builder.get_deployer", return_value=mock_deployer),
            patch("engine.builder.REGISTRY_DIR", registry_dir),
        ):
            engine = DeployEngine()
            await engine.deploy(agent_dir / "agent.yaml")

        import json

        registry = json.loads((registry_dir / "agents.json").read_text())
        assert "test-agent" in registry
        assert registry["test-agent"]["endpoint_url"] == "http://localhost:8080"
        assert registry["test-agent"]["framework"] == "langgraph"

    @pytest.mark.asyncio
    async def test_deploy_step_callback(self) -> None:
        """Verify on_step callback is called for each step transition."""
        agent_dir = _make_agent_dir()
        callback_calls: list[tuple[str, str]] = []

        def on_step(step: PipelineStep) -> None:
            callback_calls.append((step.name, step.status))

        mock_deployer = MagicMock()
        mock_deployer.provision = AsyncMock(
            return_value=InfraResult(endpoint_url="http://localhost:8080", resource_ids={})
        )
        mock_deployer.deploy = AsyncMock(
            return_value=DeployResult(
                endpoint_url="http://localhost:8080",
                container_id="abc123",
                status="running",
                agent_name="test-agent",
                version="1.0.0",
            )
        )
        mock_deployer.health_check = AsyncMock(
            return_value=HealthStatus(healthy=True, checks={"healthy": True})
        )

        with (
            patch("engine.builder.get_deployer", return_value=mock_deployer),
            patch("engine.builder.REGISTRY_DIR", agent_dir / "registry"),
        ):
            engine = DeployEngine(on_step=on_step)
            await engine.deploy(agent_dir / "agent.yaml")

        # Each step should have running + completed = 2 calls per step, 8 steps = 16
        assert len(callback_calls) == 16
        # Verify running and completed alternate
        statuses = [s for _, s in callback_calls]
        for i in range(0, len(statuses), 2):
            assert statuses[i] == "running"
            assert statuses[i + 1] == "completed"


# ---------------------------------------------------------------------------
# Tests for _sync_to_api (issue #114 — dashboard API upsert)
# ---------------------------------------------------------------------------


def _make_minimal_config() -> MagicMock:
    """Return a minimal AgentConfig-like mock."""
    cfg = MagicMock()
    cfg.name = "my-agent"
    cfg.version = "1.2.0"
    cfg.description = "A test agent"
    cfg.team = "engineering"
    cfg.owner = "dev@example.com"
    cfg.framework.value = "langgraph"
    cfg.model.primary = "gpt-4o"
    cfg.model.fallback = None
    cfg.tags = ["prod"]
    return cfg


class TestSyncToApiCreate:
    """When no existing agent is found, POST /api/v1/agents should be called."""

    def test_post_called_when_agent_not_found(self) -> None:
        engine = DeployEngine()
        config = _make_minimal_config()

        search_response = MagicMock()
        search_response.raise_for_status = MagicMock()
        search_response.json.return_value = {"data": []}

        post_response = MagicMock()
        post_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = search_response
        mock_client.post.return_value = post_response

        with patch("engine.builder.httpx.Client", return_value=mock_client):
            engine._sync_to_api(config, "http://agent:8080", "http://localhost:8000")

        # GET search was called
        mock_client.get.assert_called_once_with(
            "http://localhost:8000/api/v1/agents/search",
            params={"q": "my-agent"},
        )

        # POST create was called with correct payload
        mock_client.post.assert_called_once()
        post_call_kwargs = mock_client.post.call_args
        assert post_call_kwargs[0][0] == "http://localhost:8000/api/v1/agents"
        payload = post_call_kwargs[1]["json"]
        assert payload["name"] == "my-agent"
        assert payload["version"] == "1.2.0"
        assert payload["framework"] == "langgraph"
        assert payload["endpoint_url"] == "http://agent:8080"

        # PUT was NOT called
        mock_client.put.assert_not_called()

    def test_post_called_when_search_returns_different_names(self) -> None:
        """Results with different names should not count as a match."""
        engine = DeployEngine()
        config = _make_minimal_config()

        search_response = MagicMock()
        search_response.raise_for_status = MagicMock()
        # Returns agents with different names
        search_response.json.return_value = {
            "data": [
                {"id": "aaa", "name": "my-agent-v2"},
                {"id": "bbb", "name": "other-agent"},
            ]
        }

        post_response = MagicMock()
        post_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = search_response
        mock_client.post.return_value = post_response

        with patch("engine.builder.httpx.Client", return_value=mock_client):
            engine._sync_to_api(config, "http://agent:8080", "http://localhost:8000")

        mock_client.post.assert_called_once()
        mock_client.put.assert_not_called()


class TestSyncToApiUpdate:
    """When an existing agent is found by name, PUT /api/v1/agents/{id} should be called."""

    def test_put_called_when_agent_found(self) -> None:
        engine = DeployEngine()
        config = _make_minimal_config()

        search_response = MagicMock()
        search_response.raise_for_status = MagicMock()
        search_response.json.return_value = {
            "data": [{"id": "existing-uuid-123", "name": "my-agent"}]
        }

        put_response = MagicMock()
        put_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = search_response
        mock_client.put.return_value = put_response

        with patch("engine.builder.httpx.Client", return_value=mock_client):
            engine._sync_to_api(config, "http://agent:9000", "http://localhost:8000")

        # PUT was called with correct path and payload
        mock_client.put.assert_called_once()
        put_call = mock_client.put.call_args
        assert put_call[0][0] == "http://localhost:8000/api/v1/agents/existing-uuid-123"
        payload = put_call[1]["json"]
        assert payload["version"] == "1.2.0"
        assert payload["endpoint_url"] == "http://agent:9000"
        assert payload["status"] == "running"
        assert payload["tags"] == ["prod"]

        # POST was NOT called
        mock_client.post.assert_not_called()


class TestSyncToApiBestEffort:
    """API errors must never raise — _sync_to_api must always be a no-op on failure."""

    def test_connection_error_is_swallowed(self) -> None:
        engine = DeployEngine()
        config = _make_minimal_config()

        import httpx as real_httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = real_httpx.ConnectError("refused")

        with patch("engine.builder.httpx.Client", return_value=mock_client):
            # Should not raise
            engine._sync_to_api(config, "http://agent:8080", "http://localhost:8000")

    def test_http_error_response_is_swallowed(self) -> None:
        engine = DeployEngine()
        config = _make_minimal_config()

        search_response = MagicMock()
        search_response.raise_for_status.side_effect = Exception("500 Internal Server Error")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = search_response

        with patch("engine.builder.httpx.Client", return_value=mock_client):
            # Should not raise
            engine._sync_to_api(config, "http://agent:8080", "http://localhost:8000")

    def test_env_var_sets_api_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AGENTBREEDER_API_URL env var should be passed as api_base."""
        monkeypatch.setenv("AGENTBREEDER_API_URL", "http://custom-api:9999")
        engine = DeployEngine()
        config = _make_minimal_config()

        with patch.object(engine, "_sync_to_api") as mock_sync:
            engine._register(config, "http://agent:8080")

        mock_sync.assert_called_once_with(
            config, "http://agent:8080", "http://custom-api:9999"
        )

    def test_default_api_base_is_localhost_8000(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When env var is absent, api_base should default to http://localhost:8000."""
        monkeypatch.delenv("AGENTBREEDER_API_URL", raising=False)
        engine = DeployEngine()
        config = _make_minimal_config()

        with patch.object(engine, "_sync_to_api") as mock_sync:
            engine._register(config, "http://agent:8080")

        mock_sync.assert_called_once_with(
            config, "http://agent:8080", "http://localhost:8000"
        )
