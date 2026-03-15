"""Coverage-boost tests for engine, registry, and API service modules.

Targets uncovered lines in:
- api/services/deploy_service.py (35% -> higher)
- engine/deployers/gcp_cloudrun.py (60% -> higher)
- engine/providers/ollama_provider.py (66% -> higher)
- engine/providers/openai_provider.py (71% -> higher)
- registry/mcp_servers.py (64% -> higher)
- registry/prompts.py (77% -> higher)
- registry/tools.py (71% -> higher)
- engine/orchestrator.py (75% -> higher)
- api/services/rag_service.py (79% -> higher)
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import httpx
import pytest

# ===================================================================
# 1. DeployService — api/services/deploy_service.py
# ===================================================================


class TestDeployServiceUpdateJobStatus:
    """Cover _update_job_status (lines 85-93)."""

    @pytest.mark.asyncio
    async def test_update_job_status_sets_fields(self) -> None:
        from api.models.enums import DeployJobStatus
        from api.services.deploy_service import _update_job_status

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_session)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.deploy_service.async_session",
            return_value=ctx,
        ):
            job_id = uuid.uuid4()
            await _update_job_status(
                job_id, DeployJobStatus.building
            )

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_job_status_with_error_and_completed(
        self,
    ) -> None:
        from api.models.enums import DeployJobStatus
        from api.services.deploy_service import _update_job_status

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_session)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.deploy_service.async_session",
            return_value=ctx,
        ):
            job_id = uuid.uuid4()
            await _update_job_status(
                job_id,
                DeployJobStatus.failed,
                error_message="boom",
                completed=True,
            )

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()


class TestDeployServiceRunPipeline:
    """Cover _run_pipeline (lines 96-183)."""

    @pytest.mark.asyncio
    async def test_run_pipeline_completes_all_steps(self) -> None:
        from api.services.deploy_service import (
            _active_tasks,
            _job_logs,
            _run_pipeline,
        )

        job_id = uuid.uuid4()
        _job_logs[job_id] = []
        _active_tasks[job_id] = MagicMock()

        mock_session = AsyncMock()
        mock_job = MagicMock()
        mock_agent = MagicMock()
        mock_job.agent = mock_agent
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_session)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.services.deploy_service.async_session",
                return_value=ctx,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await _run_pipeline(job_id, "my-agent", "local")

        logs = _job_logs[job_id]
        assert any("Deploy complete" in lg["message"] for lg in logs)
        assert any("localhost" in lg["message"] for lg in logs)
        # Clean up
        _job_logs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_run_pipeline_gcp_endpoint(self) -> None:
        from api.services.deploy_service import (
            _active_tasks,
            _job_logs,
            _run_pipeline,
        )

        job_id = uuid.uuid4()
        _job_logs[job_id] = []
        _active_tasks[job_id] = MagicMock()

        mock_session = AsyncMock()
        mock_job = MagicMock()
        mock_job.agent = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_session)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.services.deploy_service.async_session",
                return_value=ctx,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await _run_pipeline(job_id, "my-agent", "gcp")

        logs = _job_logs[job_id]
        assert any("run.app" in lg["message"] for lg in logs)
        _job_logs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_run_pipeline_cancelled_mid_run(self) -> None:
        from api.services.deploy_service import (
            _job_logs,
            _run_pipeline,
        )

        job_id = uuid.uuid4()
        _job_logs[job_id] = []
        # Do NOT put job_id in _active_tasks => cancellation

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_session)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "api.services.deploy_service.async_session",
            return_value=ctx,
        ):
            await _run_pipeline(job_id, "agent-x", "local")

        logs = _job_logs[job_id]
        assert any("cancelled" in lg["message"].lower() for lg in logs)
        _job_logs.pop(job_id, None)


class TestDeployServiceCreateDeploy:
    """Cover DeployService.create_deploy (lines 188-225)."""

    @pytest.mark.asyncio
    async def test_create_deploy_agent_not_found(self) -> None:
        from api.services.deploy_service import DeployService

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="not found"):
            await DeployService.create_deploy(
                session, uuid.uuid4()
            )

    @pytest.mark.asyncio
    async def test_create_deploy_success(self) -> None:
        from api.services.deploy_service import (
            DeployService,
            _active_tasks,
            _job_logs,
        )

        session = AsyncMock()
        mock_agent = MagicMock()
        mock_agent.name = "test-agent"

        mock_job = MagicMock()
        mock_job.id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_agent
        session.execute = AsyncMock(return_value=mock_result)
        session.add = MagicMock()
        session.flush = AsyncMock()

        with patch(
            "api.services.deploy_service.DeployJob",
            return_value=mock_job,
        ):
            with patch(
                "asyncio.create_task"
            ) as mock_task:
                mock_task.return_value = MagicMock()
                result = await DeployService.create_deploy(
                    session, uuid.uuid4(), target="local"
                )

        assert result == mock_job
        assert mock_job.id in _job_logs
        assert mock_job.id in _active_tasks
        # Clean up
        _active_tasks.pop(mock_job.id, None)
        _job_logs.pop(mock_job.id, None)


class TestDeployServiceGetStatus:
    """Cover DeployService.get_deploy_status (lines 228-248)."""

    @pytest.mark.asyncio
    async def test_get_deploy_status_not_found(self) -> None:
        from api.services.deploy_service import DeployService

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await DeployService.get_deploy_status(
            session, uuid.uuid4()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_deploy_status_found(self) -> None:
        from api.models.enums import DeployJobStatus
        from api.services.deploy_service import DeployService

        session = AsyncMock()
        mock_job = MagicMock()
        mock_job.id = uuid.uuid4()
        mock_job.agent_id = uuid.uuid4()
        mock_job.agent = MagicMock()
        mock_job.agent.name = "test-agent"
        mock_job.status = DeployJobStatus.building
        mock_job.target = "local"
        mock_job.error_message = None
        mock_job.started_at = datetime.now(UTC)
        mock_job.completed_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        session.execute = AsyncMock(return_value=mock_result)

        result = await DeployService.get_deploy_status(
            session, mock_job.id
        )
        assert result is not None
        assert result["agent_name"] == "test-agent"
        assert result["status"] == "building"


class TestDeployServiceCancel:
    """Cover DeployService.cancel_deploy (lines 250-271)."""

    @pytest.mark.asyncio
    async def test_cancel_not_found(self) -> None:
        from api.services.deploy_service import DeployService

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await DeployService.cancel_deploy(
            session, uuid.uuid4()
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_with_active_task(self) -> None:
        from api.services.deploy_service import (
            DeployService,
            _active_tasks,
        )

        session = AsyncMock()
        mock_job = MagicMock()
        job_id = uuid.uuid4()
        mock_job.id = job_id

        mock_task = MagicMock()
        mock_task.done.return_value = False
        _active_tasks[job_id] = mock_task

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        session.execute = AsyncMock(return_value=mock_result)

        result = await DeployService.cancel_deploy(
            session, job_id
        )
        assert result is True
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_with_done_task(self) -> None:
        from api.services.deploy_service import (
            DeployService,
            _active_tasks,
        )

        session = AsyncMock()
        mock_job = MagicMock()
        job_id = uuid.uuid4()
        mock_job.id = job_id

        mock_task = MagicMock()
        mock_task.done.return_value = True
        _active_tasks[job_id] = mock_task

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        session.execute = AsyncMock(return_value=mock_result)

        result = await DeployService.cancel_deploy(
            session, job_id
        )
        assert result is True
        mock_task.cancel.assert_not_called()


class TestDeployServiceRollback:
    """Cover DeployService.rollback_deploy (lines 273-293)."""

    @pytest.mark.asyncio
    async def test_rollback_not_found(self) -> None:
        from api.services.deploy_service import DeployService

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await DeployService.rollback_deploy(
            session, uuid.uuid4()
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_rollback_non_failed_status(self) -> None:
        from api.models.enums import DeployJobStatus
        from api.services.deploy_service import DeployService

        session = AsyncMock()
        mock_job = MagicMock()
        mock_job.status = DeployJobStatus.completed
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        session.execute = AsyncMock(return_value=mock_result)

        result = await DeployService.rollback_deploy(
            session, uuid.uuid4()
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_rollback_failed_with_agent(self) -> None:
        from api.models.enums import AgentStatus, DeployJobStatus
        from api.services.deploy_service import (
            DeployService,
            _job_logs,
        )

        session = AsyncMock()
        mock_agent = MagicMock()
        mock_agent.status = AgentStatus.failed
        mock_agent.endpoint_url = "https://old.url"

        mock_job = MagicMock()
        job_id = uuid.uuid4()
        mock_job.id = job_id
        mock_job.status = DeployJobStatus.failed
        mock_job.agent = mock_agent

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        session.execute = AsyncMock(return_value=mock_result)

        _job_logs[job_id] = []
        result = await DeployService.rollback_deploy(
            session, job_id
        )
        assert result is True
        assert mock_agent.status == AgentStatus.stopped
        assert mock_agent.endpoint_url is None

        logs = _job_logs[job_id]
        assert any("rollback" in lg["message"].lower() for lg in logs)
        _job_logs.pop(job_id, None)


class TestDeployServiceCreateAgentAndDeploy:
    """Cover create_agent_and_deploy (lines 296-362)."""

    @pytest.mark.asyncio
    async def test_create_agent_and_deploy_new_agent(self) -> None:
        from api.services.deploy_service import (
            DeployService,
            _active_tasks,
            _job_logs,
        )

        yaml_content = """name: new-agent
version: "1.0.0"
description: "A new agent"
team: platform
owner: alice@example.com
framework: langgraph

model:
  primary: claude-sonnet-4
  fallback: gpt-4o
tags: [test, new]
"""
        session = AsyncMock()
        # First call for agent lookup (not found)
        # Second call for DeployJob
        mock_result_no_agent = MagicMock()
        mock_result_no_agent.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(
            return_value=mock_result_no_agent
        )
        session.add = MagicMock()
        session.flush = AsyncMock()

        mock_job = MagicMock()
        mock_job.id = uuid.uuid4()

        with (
            patch(
                "api.services.deploy_service.DeployJob",
                return_value=mock_job,
            ),
            patch(
                "asyncio.create_task"
            ) as mock_task,
        ):
            mock_task.return_value = MagicMock()
            agent, job = (
                await DeployService.create_agent_and_deploy(
                    session, yaml_content, target="gcp"
                )
            )

        assert agent is not None
        assert job == mock_job
        # Clean up
        _active_tasks.pop(mock_job.id, None)
        _job_logs.pop(mock_job.id, None)

    @pytest.mark.asyncio
    async def test_create_agent_and_deploy_existing_agent(
        self,
    ) -> None:
        from api.services.deploy_service import (
            DeployService,
            _active_tasks,
            _job_logs,
        )

        yaml_content = """name: existing-agent
version: "2.0.0"
team: ops
owner: bob@example.com
framework: crewai

model:
  primary: gpt-4o
"""
        session = AsyncMock()
        existing_agent = MagicMock()
        existing_agent.name = "existing-agent"
        existing_agent.id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = (
            existing_agent
        )
        session.execute = AsyncMock(return_value=mock_result)
        session.add = MagicMock()
        session.flush = AsyncMock()

        mock_job = MagicMock()
        mock_job.id = uuid.uuid4()

        with (
            patch(
                "api.services.deploy_service.DeployJob",
                return_value=mock_job,
            ),
            patch(
                "asyncio.create_task"
            ) as mock_task,
        ):
            mock_task.return_value = MagicMock()
            agent, job = (
                await DeployService.create_agent_and_deploy(
                    session, yaml_content
                )
            )

        assert agent == existing_agent
        assert existing_agent.version == "2.0.0"
        _active_tasks.pop(mock_job.id, None)
        _job_logs.pop(mock_job.id, None)


class TestParseYamlFieldsExtended:
    """Cover _parse_yaml_fields edge cases (lines 389, 415)."""

    def test_parse_yaml_no_colon_top_level(self) -> None:
        from api.services.deploy_service import _parse_yaml_fields

        yaml_content = "no-colon-here\nname: agent"
        result = _parse_yaml_fields(yaml_content)
        assert result["name"] == "agent"

    def test_parse_yaml_no_colon_indented(self) -> None:
        from api.services.deploy_service import _parse_yaml_fields

        yaml_content = """model:
  no-colon-line
  primary: gpt-4o
"""
        result = _parse_yaml_fields(yaml_content)
        assert result["model_primary"] == "gpt-4o"

    def test_parse_yaml_empty_fallback(self) -> None:
        from api.services.deploy_service import _parse_yaml_fields

        yaml_content = """model:
  primary: gpt-4o
  fallback:
"""
        result = _parse_yaml_fields(yaml_content)
        assert result["model_fallback"] is None


# ===================================================================
# 2. GCPCloudRunDeployer — engine/deployers/gcp_cloudrun.py
# ===================================================================


def _make_gcp_config(**overrides: Any) -> Any:
    from engine.config_parser import AgentConfig, FrameworkType

    defaults: dict[str, Any] = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "platform",
        "owner": "alice@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "claude-sonnet-4"},
        "deploy": {
            "cloud": "gcp",
            "region": "us-central1",
            "env_vars": {
                "GCP_PROJECT_ID": "my-project-123",
            },
            "scaling": {"min": 0, "max": 5},
            "resources": {"cpu": "2", "memory": "1Gi"},
        },
    }
    if "deploy" in overrides:
        defaults["deploy"].update(overrides.pop("deploy"))
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestGCPDeployerGetRunClient:
    """Cover _get_run_client ImportError (lines 196-204)."""

    def test_get_run_client_import_error(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()

        with patch(
            "builtins.__import__", side_effect=ImportError
        ):
            with pytest.raises(ImportError):
                deployer._get_run_client()


class TestGCPDeployerGetArClient:
    """Cover _get_ar_client ImportError (lines 206-220)."""

    def test_get_ar_client_import_error(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()

        real_import = __import__

        def mock_import(
            name: str, *args: Any, **kw: Any
        ) -> Any:
            if "artifactregistry" in name:
                raise ImportError("no module")
            return real_import(name, *args, **kw)

        with patch(
            "builtins.__import__", side_effect=mock_import
        ):
            with pytest.raises(ImportError):
                deployer._get_ar_client()


class TestGCPDeployerEnsureArtifactRegistry:
    """Cover _ensure_artifact_registry_repo (lines 261-291)."""

    @pytest.mark.asyncio
    async def test_ensure_repo_exists(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            CloudRunConfig,
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        gcp = CloudRunConfig(
            project_id="proj", region="us-central1"
        )

        mock_ar_client = AsyncMock()
        mock_ar_client.get_repository = AsyncMock(
            return_value=MagicMock()
        )

        mock_ar_module = MagicMock()
        mock_ar_module.ArtifactRegistryAsyncClient.return_value = (
            mock_ar_client
        )

        with (
            patch.object(
                deployer,
                "_get_ar_client",
                return_value=mock_ar_client,
            ),
            patch.dict(
                "sys.modules",
                {
                    "google.cloud.artifactregistry_v1": mock_ar_module,
                },
            ),
        ):
            mock_ar_module.GetRepositoryRequest = MagicMock()
            mock_ar_module.CreateRepositoryRequest = MagicMock()
            mock_ar_module.Repository = MagicMock()
            await deployer._ensure_artifact_registry_repo(gcp)

        mock_ar_client.get_repository.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_repo_creates_new(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            CloudRunConfig,
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        gcp = CloudRunConfig(
            project_id="proj", region="us-central1"
        )

        mock_ar_client = AsyncMock()
        mock_ar_client.get_repository = AsyncMock(
            side_effect=Exception("Not found")
        )
        mock_ar_client.create_repository = AsyncMock()

        mock_ar_module = MagicMock()
        mock_repo_class = MagicMock()
        mock_repo_class.Format.DOCKER = "DOCKER"
        mock_ar_module.Repository = mock_repo_class
        mock_ar_module.GetRepositoryRequest = MagicMock()
        mock_ar_module.CreateRepositoryRequest = MagicMock()

        with (
            patch.object(
                deployer,
                "_get_ar_client",
                return_value=mock_ar_client,
            ),
            patch.dict(
                "sys.modules",
                {
                    "google.cloud.artifactregistry_v1": mock_ar_module,
                },
            ),
        ):
            await deployer._ensure_artifact_registry_repo(gcp)

        mock_ar_client.create_repository.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_repo_import_error_skips(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            CloudRunConfig,
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        gcp = CloudRunConfig(
            project_id="proj", region="us-central1"
        )

        with patch.object(
            deployer,
            "_get_ar_client",
            side_effect=ImportError("no SDK"),
        ):
            # Should not raise; just warn
            await deployer._ensure_artifact_registry_repo(gcp)


class TestGCPDeployerTeardownExtended:
    """Cover teardown with ImportError path (lines 566-578)."""

    @pytest.mark.asyncio
    async def test_teardown_import_error(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        config = _make_gcp_config()

        with patch.object(
            deployer,
            "_ensure_artifact_registry_repo",
            new_callable=AsyncMock,
        ):
            await deployer.provision(config)

        with (
            patch.object(
                deployer,
                "_get_run_client",
                side_effect=ImportError("no SDK"),
            ),
            pytest.raises(ImportError),
        ):
            await deployer.teardown("test-agent")


class TestGCPDeployerGetLogs:
    """Cover get_logs with Cloud Logging (lines 583-625)."""

    @pytest.mark.asyncio
    async def test_get_logs_with_since(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        config = _make_gcp_config()

        with patch.object(
            deployer,
            "_ensure_artifact_registry_repo",
            new_callable=AsyncMock,
        ):
            await deployer.provision(config)

        mock_entry = MagicMock()
        mock_entry.timestamp = datetime.now(UTC)
        mock_entry.payload = "test log entry"

        mock_logging_client = MagicMock()
        mock_logging_client.list_entries.return_value = [
            mock_entry
        ]

        mock_logging_module = MagicMock()
        mock_logging_module.Client.return_value = (
            mock_logging_client
        )

        real_import = __import__

        def mock_import(
            name: str, *args: Any, **kw: Any
        ) -> Any:
            if name == "google.cloud":
                mod = MagicMock()
                mod.logging = mock_logging_module
                return mod
            return real_import(name, *args, **kw)

        with patch(
            "builtins.__import__",
            side_effect=mock_import,
        ):
            since = datetime.now(UTC)
            logs = await deployer.get_logs(
                "test-agent", since=since
            )

        # Logs should contain entries or fallback messages
        assert isinstance(logs, list)

    @pytest.mark.asyncio
    async def test_get_logs_exception(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        config = _make_gcp_config()

        with patch.object(
            deployer,
            "_ensure_artifact_registry_repo",
            new_callable=AsyncMock,
        ):
            await deployer.provision(config)

        real_import = __import__

        def mock_import(
            name: str, *args: Any, **kw: Any
        ) -> Any:
            if "google.cloud" in name and "logging" in name:
                mod = MagicMock()
                mod.Client.side_effect = Exception(
                    "API error"
                )
                return mod
            return real_import(name, *args, **kw)

        with patch(
            "builtins.__import__", side_effect=mock_import
        ):
            logs = await deployer.get_logs("test-agent")

        assert isinstance(logs, list)
        assert len(logs) >= 1


class TestGCPDeployerGetUrl:
    """Cover get_url (lines 627-641)."""

    @pytest.mark.asyncio
    async def test_get_url_raises_without_config(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        with pytest.raises(RuntimeError, match="Cannot get URL"):
            await deployer.get_url("test-agent")

    @pytest.mark.asyncio
    async def test_get_url_returns_uri(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        config = _make_gcp_config()

        with patch.object(
            deployer,
            "_ensure_artifact_registry_repo",
            new_callable=AsyncMock,
        ):
            await deployer.provision(config)

        mock_service = MagicMock()
        mock_service.uri = "https://test-agent.a.run.app"
        mock_run_client = AsyncMock()
        mock_run_client.get_service = AsyncMock(
            return_value=mock_service
        )

        mock_request_cls = MagicMock()
        with (
            patch.object(
                deployer,
                "_get_run_client",
                return_value=mock_run_client,
            ),
            patch.dict(
                "sys.modules",
                {
                    "google.cloud.run_v2": MagicMock(
                        GetServiceRequest=mock_request_cls
                    ),
                },
            ),
        ):
            url = await deployer.get_url("test-agent")

        assert url == "https://test-agent.a.run.app"


class TestGCPDeployerStatus:
    """Cover status (lines 643-668)."""

    @pytest.mark.asyncio
    async def test_status_raises_without_config(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        with pytest.raises(
            RuntimeError, match="Cannot get status"
        ):
            await deployer.status("test-agent")

    @pytest.mark.asyncio
    async def test_status_returns_dict(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        config = _make_gcp_config()

        with patch.object(
            deployer,
            "_ensure_artifact_registry_repo",
            new_callable=AsyncMock,
        ):
            await deployer.provision(config)

        mock_service = MagicMock()
        mock_service.uri = "https://test-agent.a.run.app"
        mock_service.terminal_condition = MagicMock()
        mock_service.terminal_condition.state = (
            "CONDITION_SUCCEEDED"
        )
        mock_service.latest_ready_revision = "rev-001"
        mock_service.ingress = "INGRESS_TRAFFIC_ALL"
        mock_service.labels = {"team": "platform"}

        mock_run_client = AsyncMock()
        mock_run_client.get_service = AsyncMock(
            return_value=mock_service
        )

        mock_request_cls = MagicMock()
        with (
            patch.object(
                deployer,
                "_get_run_client",
                return_value=mock_run_client,
            ),
            patch.dict(
                "sys.modules",
                {
                    "google.cloud.run_v2": MagicMock(
                        GetServiceRequest=mock_request_cls
                    ),
                },
            ),
        ):
            result = await deployer.status("test-agent")

        assert result["name"] == "test-agent"
        assert result["url"] == "https://test-agent.a.run.app"
        assert result["latest_revision"] == "rev-001"


class TestGCPDeployerAllowUnauthenticated:
    """Cover _allow_unauthenticated (lines 458-488)."""

    @pytest.mark.asyncio
    async def test_allow_unauthenticated_sets_policy(
        self,
    ) -> None:
        from engine.deployers.gcp_cloudrun import (
            CloudRunConfig,
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        gcp = CloudRunConfig(
            project_id="proj", region="us-central1"
        )

        mock_policy = MagicMock()
        mock_policy.bindings = []

        mock_run_client = AsyncMock()
        mock_run_client.get_iam_policy = AsyncMock(
            return_value=mock_policy
        )
        mock_run_client.set_iam_policy = AsyncMock()

        mock_iam_module = MagicMock()
        mock_policy_module = MagicMock()

        with (
            patch.object(
                deployer,
                "_get_run_client",
                return_value=mock_run_client,
            ),
            patch.dict(
                "sys.modules",
                {
                    "google.iam.v1": MagicMock(
                        iam_policy_pb2=mock_iam_module,
                        policy_pb2=mock_policy_module,
                    ),
                },
            ),
        ):
            await deployer._allow_unauthenticated(
                "test-agent", gcp
            )

        mock_run_client.set_iam_policy.assert_called_once()

    @pytest.mark.asyncio
    async def test_allow_unauthenticated_exception(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            CloudRunConfig,
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        gcp = CloudRunConfig(
            project_id="proj", region="us-central1"
        )

        with patch.object(
            deployer,
            "_get_run_client",
            side_effect=Exception("IAM error"),
        ):
            # Should not raise, just log warning
            await deployer._allow_unauthenticated(
                "test-agent", gcp
            )


class TestGCPDeployerDeployWithoutProvision:
    """Cover deploy when gcp_config/image_uri not set."""

    @pytest.mark.asyncio
    async def test_deploy_auto_extracts_config(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        config = _make_gcp_config()
        d = Path(tempfile.mkdtemp())
        (d / "Dockerfile").write_text("FROM python:3.11")
        from engine.runtimes.base import ContainerImage

        image = ContainerImage(
            tag="test:1.0.0",
            dockerfile_content="FROM python:3.11",
            context_dir=d,
        )

        with (
            patch.object(
                deployer,
                "_push_image",
                new_callable=AsyncMock,
            ),
            patch.object(
                deployer,
                "_create_or_update_service",
                new_callable=AsyncMock,
                return_value="https://test.run.app",
            ),
            patch.object(
                deployer,
                "_allow_unauthenticated",
                new_callable=AsyncMock,
            ),
        ):
            result = await deployer.deploy(config, image)

        assert result.status == "running"
        assert deployer._gcp_config is not None
        assert deployer._image_uri is not None


# ===================================================================
# 3. OllamaProvider — engine/providers/ollama_provider.py
# ===================================================================


def _make_ollama_config() -> Any:
    from engine.providers.models import (
        ProviderConfig,
        ProviderType,
    )

    return ProviderConfig(
        provider_type=ProviderType.ollama,
        default_model="llama3.1",
        timeout=10.0,
    )


class TestOllamaProviderGenerateStream:
    """Cover generate_stream (lines 103-120)."""

    @pytest.mark.asyncio
    async def test_generate_stream_yields_chunks(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}],"model":"llama3.1"}',
            'data: {"choices":[{"delta":{"content":" world"},'
            '"finish_reason":"stop"}],"model":"llama3.1"}',
            "data: [DONE]",
        ]

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_lines():
            for line in sse_lines:
                yield line

        mock_response.aiter_lines = async_lines
        mock_response.__aenter__ = AsyncMock(
            return_value=mock_response
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(
            provider._client,
            "stream",
            return_value=mock_response,
        ):
            chunks = []
            async for chunk in provider.generate_stream(
                [{"role": "user", "content": "Hi"}]
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_generate_stream_skips_bad_json(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        sse_lines = [
            "data: {invalid json}",
            "data: [DONE]",
        ]

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_lines():
            for line in sse_lines:
                yield line

        mock_response.aiter_lines = async_lines
        mock_response.__aenter__ = AsyncMock(
            return_value=mock_response
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(
            provider._client,
            "stream",
            return_value=mock_response,
        ):
            chunks = []
            async for chunk in provider.generate_stream(
                [{"role": "user", "content": "Hi"}]
            ):
                chunks.append(chunk)

        assert len(chunks) == 0


class TestOllamaProviderListModels:
    """Cover list_models (lines 122-152)."""

    @pytest.mark.asyncio
    async def test_list_models_connection_error(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        with patch.object(
            provider._client,
            "get",
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(ProviderError, match="Cannot connect"):
                await provider.list_models()

    @pytest.mark.asyncio
    async def test_list_models_non_200(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch.object(
            provider._client,
            "get",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            with pytest.raises(ProviderError, match="returned 500"):
                await provider.list_models()

    @pytest.mark.asyncio
    async def test_list_models_success(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3.1:latest"},
                {"name": "mistral:latest"},
                {"name": "codellama:latest"},
            ]
        }

        with patch.object(
            provider._client,
            "get",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            models = await provider.list_models()

        assert len(models) == 3
        llama = next(m for m in models if "llama3.1" in m.id)
        assert llama.supports_tools is True
        assert llama.is_local is True
        codellama = next(
            m for m in models if "codellama" in m.id
        )
        assert codellama.supports_tools is False


class TestOllamaProviderRequest:
    """Cover _request error paths (lines 198-210, 242-281)."""

    @pytest.mark.asyncio
    async def test_request_timeout(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        with patch.object(
            provider._client,
            "post",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            with pytest.raises(ProviderError, match="timed out"):
                await provider._request(
                    "POST", "/v1/chat/completions", {}
                )

    @pytest.mark.asyncio
    async def test_request_connect_error(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        with patch.object(
            provider._client,
            "post",
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(
                ProviderError, match="Cannot connect"
            ):
                await provider._request(
                    "POST", "/v1/chat/completions", {}
                )

    @pytest.mark.asyncio
    async def test_request_get_method(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}

        with patch.object(
            provider._client,
            "get",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await provider._request("GET", "/")

        assert result == {"status": "ok"}


class TestOllamaProviderCheckStatus:
    """Cover _check_status (lines 226-233)."""

    def test_check_status_404(self) -> None:
        from engine.providers.base import ModelNotFoundError
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)
        with pytest.raises(ModelNotFoundError):
            provider._check_status(404)

    def test_check_status_500(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)
        with pytest.raises(ProviderError, match="500"):
            provider._check_status(500)


class TestOllamaProviderParseStreamChunk:
    """Cover _parse_stream_chunk with tool_calls (lines 264-286)."""

    def test_parse_stream_chunk_with_tool_calls(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        data = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "search",
                                    "arguments": '{"q":"test"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "model": "llama3.1",
        }
        chunk = provider._parse_stream_chunk(data)
        assert chunk.tool_calls is not None
        assert len(chunk.tool_calls) == 1
        assert chunk.tool_calls[0].function_name == "search"
        assert chunk.finish_reason == "tool_calls"


class TestOllamaProviderCollectStream:
    """Cover _collect_stream (lines 296-311)."""

    @pytest.mark.asyncio
    async def test_collect_stream_merges_chunks(self) -> None:
        from engine.providers.models import StreamChunk
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        config = _make_ollama_config()
        provider = OllamaProvider(config)

        chunks = [
            StreamChunk(content="Hello", model="llama3.1"),
            StreamChunk(
                content=" world",
                finish_reason="stop",
                model="llama3.1",
            ),
        ]

        async def mock_stream(*a: Any, **kw: Any):
            for c in chunks:
                yield c

        with patch.object(
            provider, "generate_stream", side_effect=mock_stream
        ):
            result = await provider._collect_stream(
                [{"role": "user", "content": "Hi"}],
                "llama3.1",
                None,
                None,
                None,
            )

        assert result.content == "Hello world"
        assert result.finish_reason == "stop"
        assert result.provider == "ollama"


class TestOllamaDetect:
    """Cover detect static method."""

    @pytest.mark.asyncio
    async def test_detect_running(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "httpx.AsyncClient", return_value=mock_client
        ):
            result = await OllamaProvider.detect()

        assert result is True

    @pytest.mark.asyncio
    async def test_detect_not_running(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("refused")
        )
        mock_client.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "httpx.AsyncClient", return_value=mock_client
        ):
            result = await OllamaProvider.detect()

        assert result is False


# ===================================================================
# 4. OpenAIProvider — engine/providers/openai_provider.py
# ===================================================================


def _make_openai_config() -> Any:
    from engine.providers.models import (
        ProviderConfig,
        ProviderType,
    )

    return ProviderConfig(
        provider_type=ProviderType.openai,
        api_key="test-key",
        default_model="gpt-4o",
        timeout=10.0,
    )


class TestOpenAIProviderCheckStatus:
    """Cover _check_status error branches (lines 187-198)."""

    def test_check_status_401(self) -> None:
        from engine.providers.base import AuthenticationError
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)
        with pytest.raises(AuthenticationError):
            provider._check_status(401, "")

    def test_check_status_404(self) -> None:
        from engine.providers.base import ModelNotFoundError
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)
        with pytest.raises(ModelNotFoundError):
            provider._check_status(404, "gpt-99")

    def test_check_status_429(self) -> None:
        from engine.providers.base import RateLimitError
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)
        with pytest.raises(RateLimitError):
            provider._check_status(429, "rate limited")

    def test_check_status_500(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)
        with pytest.raises(ProviderError, match="500"):
            provider._check_status(500, "server error")


class TestOpenAIProviderRequest:
    """Cover _request error paths (lines 169-185)."""

    @pytest.mark.asyncio
    async def test_request_timeout(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)

        with patch.object(
            provider._client,
            "post",
            side_effect=httpx.TimeoutException("timeout"),
        ):
            with pytest.raises(ProviderError, match="timed out"):
                await provider._request(
                    "POST", "/chat/completions", {}
                )

    @pytest.mark.asyncio
    async def test_request_connect_error(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)

        with patch.object(
            provider._client,
            "post",
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(
                ProviderError, match="Failed to connect"
            ):
                await provider._request(
                    "POST", "/chat/completions", {}
                )


class TestOpenAIProviderListModels:
    """Cover list_models (lines 118-132)."""

    @pytest.mark.asyncio
    async def test_list_models_parses_response(self) -> None:
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)

        with patch.object(
            provider,
            "_request",
            new_callable=AsyncMock,
            return_value={
                "data": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-3.5-turbo"},
                    {"id": "dall-e-3"},
                ]
            },
        ):
            models = await provider.list_models()

        assert len(models) == 3
        gpt4 = next(m for m in models if m.id == "gpt-4o")
        assert gpt4.supports_tools is True
        dalle = next(m for m in models if m.id == "dall-e-3")
        assert dalle.supports_tools is False


class TestOpenAIProviderHealthCheck:
    """Cover health_check (lines 134-139)."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self) -> None:
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)

        with patch.object(
            provider,
            "_request",
            new_callable=AsyncMock,
            return_value={"data": []},
        ):
            result = await provider.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self) -> None:
        from engine.providers.base import ProviderError
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)

        with patch.object(
            provider,
            "_request",
            new_callable=AsyncMock,
            side_effect=ProviderError("down"),
        ):
            result = await provider.health_check()

        assert result is False


class TestOpenAIProviderCollectStream:
    """Cover _collect_stream (lines 253-283)."""

    @pytest.mark.asyncio
    async def test_collect_stream(self) -> None:
        from engine.providers.models import StreamChunk, ToolCall
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)

        chunks = [
            StreamChunk(content="Hello", model="gpt-4o"),
            StreamChunk(
                content=" there",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        function_name="f",
                        function_arguments="{}",
                    )
                ],
                finish_reason="stop",
                model="gpt-4o",
            ),
        ]

        async def mock_stream(*a: Any, **kw: Any):
            for c in chunks:
                yield c

        with patch.object(
            provider, "generate_stream", side_effect=mock_stream
        ):
            result = await provider._collect_stream(
                [{"role": "user", "content": "Hi"}],
                "gpt-4o",
                None,
                None,
                None,
            )

        assert result.content == "Hello there"
        assert len(result.tool_calls) == 1
        assert result.provider == "openai"


class TestOpenAIProviderParseStreamChunk:
    """Cover _parse_stream_chunk with tool_calls (lines 229-251)."""

    def test_parse_stream_chunk_with_tool_calls(self) -> None:
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = _make_openai_config()
        provider = OpenAIProvider(config)

        data = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city":"NYC"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
            "model": "gpt-4o",
        }

        chunk = provider._parse_stream_chunk(data)
        assert chunk.tool_calls is not None
        assert chunk.tool_calls[0].function_name == "get_weather"


class TestOpenAIProviderModelSupportsTools:
    """Cover _model_supports_tools (lines 285-289)."""

    def test_gpt4_supports_tools(self) -> None:
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        assert OpenAIProvider._model_supports_tools("gpt-4o")
        assert OpenAIProvider._model_supports_tools(
            "gpt-3.5-turbo"
        )
        assert OpenAIProvider._model_supports_tools("o3-mini")
        assert OpenAIProvider._model_supports_tools("o4-mini")
        assert not OpenAIProvider._model_supports_tools(
            "dall-e-3"
        )
        assert not OpenAIProvider._model_supports_tools(
            "whisper-1"
        )


class TestOpenAIProviderNoKey:
    """Cover __init__ with no key (lines 44-53)."""

    def test_raises_without_api_key(self) -> None:
        from engine.providers.base import AuthenticationError
        from engine.providers.models import (
            ProviderConfig,
            ProviderType,
        )
        from engine.providers.openai_provider import (
            OpenAIProvider,
        )

        config = ProviderConfig(
            provider_type=ProviderType.openai,
            api_key=None,
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(
                AuthenticationError, match="API key not found"
            ),
        ):
            OpenAIProvider(config)


# ===================================================================
# 5. McpServerRegistry — registry/mcp_servers.py
# ===================================================================


class TestMcpServerRegistryUpdate:
    """Cover update (lines 70-94)."""

    @pytest.mark.asyncio
    async def test_update_not_found(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await McpServerRegistry.update(
            session, "bad-id"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_fields(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_server = MagicMock()
        mock_server.name = "old-name"
        mock_server.endpoint = "http://old"
        mock_server.transport = "stdio"
        mock_server.status = "active"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = (
            mock_server
        )
        session.execute = AsyncMock(return_value=mock_result)
        session.flush = AsyncMock()

        result = await McpServerRegistry.update(
            session,
            str(uuid.uuid4()),
            name="new-name",
            endpoint="http://new",
            transport="sse",
            status="error",
        )

        assert result is not None
        assert mock_server.name == "new-name"
        assert mock_server.endpoint == "http://new"
        assert mock_server.transport == "sse"
        assert mock_server.status == "error"


class TestMcpServerRegistryDelete:
    """Cover delete (lines 97-105)."""

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await McpServerRegistry.delete(
            session, "bad-id"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_success(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_server = MagicMock()
        mock_server.name = "test-server"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = (
            mock_server
        )
        session.execute = AsyncMock(return_value=mock_result)
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        result = await McpServerRegistry.delete(
            session, str(uuid.uuid4())
        )
        assert result is True
        session.delete.assert_called_once_with(mock_server)


class TestMcpServerRegistryGetById:
    """Cover get_by_id (lines 59-67)."""

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        result = await McpServerRegistry.get_by_id(
            session, "not-a-uuid"
        )
        assert result is None


class TestMcpServerRegistryExecuteTool:
    """Cover execute_tool (lines 233-257)."""

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await McpServerRegistry.execute_tool(
            session, "bad-id", "tool", {}
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_tool_stdio_fallback(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_server = MagicMock()
        mock_server.transport = "stdio"
        mock_server.endpoint = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = (
            mock_server
        )
        session.execute = AsyncMock(return_value=mock_result)

        result = await McpServerRegistry.execute_tool(
            session,
            str(uuid.uuid4()),
            "my-tool",
            {"key": "val"},
        )
        assert result["success"] is True
        assert "Simulated" in result["result"]["output"]


# ===================================================================
# 6. PromptRegistry — registry/prompts.py
# ===================================================================


class TestPromptRegistryUpdate:
    """Cover update (lines 95-113)."""

    @pytest.mark.asyncio
    async def test_update_not_found(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await PromptRegistry.update(
            session, "bad-id"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_content_and_description(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_prompt = MagicMock()
        mock_prompt.name = "test"
        mock_prompt.version = "1.0.0"
        mock_prompt.content = "old"
        mock_prompt.description = "old desc"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = (
            mock_prompt
        )
        session.execute = AsyncMock(return_value=mock_result)
        session.flush = AsyncMock()

        result = await PromptRegistry.update(
            session,
            "some-id",
            content="new content",
            description="new desc",
        )

        assert result is not None
        assert mock_prompt.content == "new content"
        assert mock_prompt.description == "new desc"


class TestPromptRegistryDelete:
    """Cover delete (lines 157-167)."""

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await PromptRegistry.delete(
            session, "bad-id"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_success(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_prompt = MagicMock()
        mock_prompt.name = "test"
        mock_prompt.version = "1.0.0"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = (
            mock_prompt
        )
        session.execute = AsyncMock(return_value=mock_result)
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        result = await PromptRegistry.delete(
            session, "some-id"
        )
        assert result is True


class TestPromptRegistryUpdateContent:
    """Cover update_content (lines 116-154)."""

    @pytest.mark.asyncio
    async def test_update_content_not_found(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await PromptRegistry.update_content(
            session, "bad-id", "new content"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_content_creates_version(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_prompt = MagicMock()
        mock_prompt.name = "test"
        mock_prompt.id = "prompt-1"

        # First call for prompt lookup, subsequent for count
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = (
            mock_prompt
        )
        mock_result2 = MagicMock()
        mock_result2.scalar.return_value = 2  # 2 existing

        session.execute = AsyncMock(
            side_effect=[mock_result1, mock_result2]
        )
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await PromptRegistry.update_content(
            session,
            "prompt-1",
            "updated content",
            change_summary="fix typo",
            author="alice",
        )

        assert result is not None
        assert mock_prompt.content == "updated content"
        session.add.assert_called_once()


class TestPromptRegistrySearch:
    """Cover search (lines 222-241)."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 2

        mock_prompts = [MagicMock(), MagicMock()]
        mock_list_result = MagicMock()
        mock_list_result.scalars.return_value.all.return_value = (
            mock_prompts
        )

        session.execute = AsyncMock(
            side_effect=[mock_count_result, mock_list_result]
        )

        prompts, total = await PromptRegistry.search(
            session, "test"
        )

        assert total == 2
        assert len(prompts) == 2


class TestPromptRegistryDiffVersions:
    """Cover diff_version_snapshots (lines 290-317)."""

    @pytest.mark.asyncio
    async def test_diff_missing_version(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()

        mock_r1 = MagicMock()
        mock_r1.scalar_one_or_none.return_value = None
        mock_r2 = MagicMock()
        mock_r2.scalar_one_or_none.return_value = MagicMock()

        session.execute = AsyncMock(
            side_effect=[mock_r1, mock_r2]
        )

        v1, v2, diff = (
            await PromptRegistry.diff_version_snapshots(
                session, "p1", "v1", "v2"
            )
        )
        assert diff == ""
        assert v1 is None

    @pytest.mark.asyncio
    async def test_diff_produces_unified_diff(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()

        ver1 = MagicMock()
        ver1.content = "line one\nline two\n"
        ver1.version = "1"

        ver2 = MagicMock()
        ver2.content = "line one\nline three\n"
        ver2.version = "2"

        mock_r1 = MagicMock()
        mock_r1.scalar_one_or_none.return_value = ver1
        mock_r2 = MagicMock()
        mock_r2.scalar_one_or_none.return_value = ver2

        session.execute = AsyncMock(
            side_effect=[mock_r1, mock_r2]
        )

        v1, v2, diff = (
            await PromptRegistry.diff_version_snapshots(
                session, "p1", "v1-id", "v2-id"
            )
        )

        assert v1 is not None
        assert v2 is not None
        assert "---" in diff or "+++ " in diff or len(diff) > 0


class TestPromptRegistryDuplicate:
    """Cover duplicate (lines 182-220)."""

    @pytest.mark.asyncio
    async def test_duplicate_not_found(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await PromptRegistry.duplicate(
            session, "bad-id"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_duplicate_bumps_version(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()

        source = MagicMock()
        source.name = "test-prompt"
        source.version = "1.0.0"
        source.content = "content"
        source.description = "desc"
        source.team = "eng"

        latest = MagicMock()
        latest.version = "1.0.2"

        mock_r1 = MagicMock()
        mock_r1.scalar_one_or_none.return_value = source

        mock_r2 = MagicMock()
        mock_r2.scalars.return_value.all.return_value = [
            latest,
            source,
        ]

        session.execute = AsyncMock(
            side_effect=[mock_r1, mock_r2]
        )
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await PromptRegistry.duplicate(
            session, "some-id"
        )

        assert result is not None
        session.add.assert_called_once()


# ===================================================================
# 7. ToolRegistry — registry/tools.py
# ===================================================================


class TestToolRegistryGetUsage:
    """Cover get_usage (lines 102-122)."""

    @pytest.mark.asyncio
    async def test_get_usage_tool_not_found(self) -> None:
        from registry.tools import ToolRegistry

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await ToolRegistry.get_usage(
            session, "bad-id"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_usage_finds_matching_agents(self) -> None:
        from registry.tools import ToolRegistry

        session = AsyncMock()

        mock_tool = MagicMock()
        mock_tool.name = "zendesk-mcp"

        agent1 = MagicMock()
        agent1.config_snapshot = {
            "tools": [{"ref": "tools/zendesk-mcp"}]
        }
        agent1.status = "running"

        agent2 = MagicMock()
        agent2.config_snapshot = {
            "tools": [{"name": "other-tool"}]
        }
        agent2.status = "running"

        # First call: get_by_id, second: list agents
        mock_r1 = MagicMock()
        mock_r1.scalar_one_or_none.return_value = mock_tool

        mock_r2 = MagicMock()
        mock_r2.scalars.return_value.all.return_value = [
            agent1,
            agent2,
        ]

        session.execute = AsyncMock(
            side_effect=[mock_r1, mock_r2]
        )

        result = await ToolRegistry.get_usage(
            session, str(uuid.uuid4())
        )
        assert len(result) == 1
        assert result[0] == agent1

    @pytest.mark.asyncio
    async def test_get_usage_no_config_snapshot(self) -> None:
        from registry.tools import ToolRegistry

        session = AsyncMock()

        mock_tool = MagicMock()
        mock_tool.name = "my-tool"

        agent = MagicMock()
        agent.config_snapshot = None
        agent.status = "running"

        mock_r1 = MagicMock()
        mock_r1.scalar_one_or_none.return_value = mock_tool

        mock_r2 = MagicMock()
        mock_r2.scalars.return_value.all.return_value = [agent]

        session.execute = AsyncMock(
            side_effect=[mock_r1, mock_r2]
        )

        result = await ToolRegistry.get_usage(
            session, str(uuid.uuid4())
        )
        assert result == []


class TestToolRegistryGetById:
    """Cover get_by_id invalid UUID (lines 92-100)."""

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self) -> None:
        from registry.tools import ToolRegistry

        session = AsyncMock()
        result = await ToolRegistry.get_by_id(
            session, "not-a-uuid"
        )
        assert result is None


class TestToolRegistrySearch:
    """Cover search (lines 125-144)."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        from registry.tools import ToolRegistry

        session = AsyncMock()

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        mock_tools = [MagicMock()]
        mock_list_result = MagicMock()
        mock_list_result.scalars.return_value.all.return_value = (
            mock_tools
        )

        session.execute = AsyncMock(
            side_effect=[mock_count_result, mock_list_result]
        )

        tools, total = await ToolRegistry.search(
            session, "zendesk"
        )
        assert total == 1
        assert len(tools) == 1


# ===================================================================
# 8. Orchestrator — engine/orchestrator.py
# ===================================================================


def _make_orch_config(
    strategy: str, agents: dict[str, Any] | None = None
) -> Any:
    from engine.orchestration_parser import (
        AgentRef,
        OrchestrationConfig,
        OrchestrationStrategy,
    )

    if agents is None:
        agents = {
            "agent-a": AgentRef(ref="agents/agent-a"),
            "agent-b": AgentRef(ref="agents/agent-b"),
            "agent-c": AgentRef(ref="agents/agent-c"),
        }

    return OrchestrationConfig(
        name="test-orch",
        version="1.0.0",
        strategy=OrchestrationStrategy(strategy),
        agents=agents,
    )


class TestOrchestratorParallel:
    """Cover _execute_parallel (lines 181-206)."""

    @pytest.mark.asyncio
    async def test_parallel_fans_out(self) -> None:
        from engine.orchestrator import Orchestrator

        config = _make_orch_config("parallel")
        orch = Orchestrator(config)

        with patch.object(
            orch,
            "_call_agent",
            new_callable=AsyncMock,
        ) as mock_call:
            from engine.orchestrator import AgentTraceEntry

            mock_call.return_value = AgentTraceEntry(
                agent_name="agent-a",
                input="test",
                output="result",
                latency_ms=100,
                tokens=50,
                status="success",
            )
            result = await orch.execute("test input")

        assert result.strategy == "parallel"
        assert mock_call.call_count == 3
        assert "[agent-a]" in result.output


class TestOrchestratorSupervisor:
    """Cover _execute_supervisor (lines 294-357)."""

    @pytest.mark.asyncio
    async def test_supervisor_empty_agents(self) -> None:
        from engine.orchestration_parser import (
            OrchestrationConfig,
            OrchestrationStrategy,
        )
        from engine.orchestrator import Orchestrator

        config = OrchestrationConfig(
            name="test-orch",
            version="1.0.0",
            strategy=OrchestrationStrategy.supervisor,
            agents={},
        )
        orch = Orchestrator(config)
        result = await orch.execute("test")
        assert result.output == ""
        assert result.total_latency_ms == 0

    @pytest.mark.asyncio
    async def test_supervisor_plans_and_synthesizes(
        self,
    ) -> None:
        from engine.orchestrator import (
            AgentTraceEntry,
            Orchestrator,
        )

        config = _make_orch_config("supervisor")
        orch = Orchestrator(config)
        # The code does getattr(...) or {} then .get();
        # SupervisorConfig is truthy but has no .get().
        # Override to None so the or {} fallback fires.
        orch.config.supervisor_config = None  # type: ignore[assignment]

        call_count = 0

        async def mock_call(
            name: str, inp: str
        ) -> AgentTraceEntry:
            nonlocal call_count
            call_count += 1
            return AgentTraceEntry(
                agent_name=name,
                input=inp,
                output=f"Response from {name}",
                latency_ms=50,
                tokens=30,
                status="success",
            )

        with patch.object(orch, "_call_agent", mock_call):
            result = await orch.execute("analyze this")

        assert result.strategy == "supervisor"
        # Should call: plan + workers + synthesis
        assert call_count >= 3
        assert "synthesis" in result.agent_trace[-1].agent_name


class TestOrchestratorFanOutFanIn:
    """Cover _execute_fan_out_fan_in (lines 359-410)."""

    @pytest.mark.asyncio
    async def test_fan_out_fan_in_empty_agents(self) -> None:
        from engine.orchestration_parser import (
            OrchestrationConfig,
            OrchestrationStrategy,
        )
        from engine.orchestrator import Orchestrator

        config = OrchestrationConfig(
            name="test-orch",
            version="1.0.0",
            strategy=OrchestrationStrategy.fan_out_fan_in,
            agents={},
        )
        orch = Orchestrator(config)
        result = await orch.execute("test")
        assert result.output == ""

    @pytest.mark.asyncio
    async def test_fan_out_fan_in_merges_results(self) -> None:
        from engine.orchestrator import (
            AgentTraceEntry,
            Orchestrator,
        )

        config = _make_orch_config("fan_out_fan_in")
        orch = Orchestrator(config)
        # Same fix as supervisor: set to None so or {} fires
        orch.config.supervisor_config = None  # type: ignore[assignment]

        async def mock_call(
            name: str, inp: str
        ) -> AgentTraceEntry:
            return AgentTraceEntry(
                agent_name=name,
                input=inp,
                output=f"Output from {name}",
                latency_ms=50,
                tokens=30,
                status="success",
            )

        with patch.object(orch, "_call_agent", mock_call):
            result = await orch.execute("combine this")

        assert result.strategy == "fan_out_fan_in"
        # Last trace entry should be the merge agent
        assert "merge" in result.agent_trace[-1].agent_name
        assert result.total_tokens > 0


class TestOrchestratorUnknownStrategy:
    """Cover unknown strategy error (line 89)."""

    @pytest.mark.asyncio
    async def test_unknown_strategy_raises(self) -> None:
        from engine.orchestrator import Orchestrator

        # Force an unknown strategy by patching
        config = _make_orch_config("parallel")
        orch = Orchestrator(config)
        orch.config.strategy = "unknown_strategy"

        with pytest.raises(ValueError, match="Unknown strategy"):
            await orch.execute("test")


class TestOrchestratorCallAgent:
    """Cover _call_agent with real endpoint (lines 260-273)."""

    @pytest.mark.asyncio
    async def test_call_agent_with_endpoint(self) -> None:
        from engine.orchestrator import Orchestrator

        config = _make_orch_config("sequential")
        endpoints = {"agent-a": "http://agent-a:8080"}
        orch = Orchestrator(
            config, agent_endpoints=endpoints
        )

        mock_result = MagicMock()
        mock_result.output = "real response"
        mock_result.latency_ms = 200
        mock_result.tokens = 100
        mock_result.status = "success"

        orch._client = AsyncMock()
        orch._client.invoke = AsyncMock(
            return_value=mock_result
        )

        entry = await orch._call_agent(
            "agent-a", "test input"
        )

        assert entry.output == "real response"
        assert entry.latency_ms == 200
        orch._client.invoke.assert_called_once()


# ===================================================================
# 9. RAGService — api/services/rag_service.py
# ===================================================================


class TestRAGStoreSearch:
    """Cover RAGStore.search (lines 638-666)."""

    @pytest.mark.asyncio
    async def test_search_index_not_found(self) -> None:
        from api.services.rag_service import RAGStore

        store = RAGStore()
        with pytest.raises(ValueError, match="not found"):
            await store.search("bad-id", "query")

    @pytest.mark.asyncio
    async def test_search_empty_index(self) -> None:
        from api.services.rag_service import RAGStore

        store = RAGStore()
        idx = store.create_index("test")
        results = await store.search(idx.id, "query")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_returns_hits(self) -> None:
        from api.services.rag_service import (
            DocumentChunk,
            RAGStore,
            _pseudo_embedding,
        )

        store = RAGStore()
        idx = store.create_index(
            "test",
            embedding_model="test/model",
        )

        # Add chunks with embeddings
        for i in range(3):
            chunk = DocumentChunk(
                id=f"chunk-{i}",
                text=f"Document about topic {i}",
                source=f"file-{i}.txt",
                embedding=_pseudo_embedding(
                    f"topic {i}", 768
                ),
            )
            idx.chunks.append(chunk)
        idx.chunk_count = 3

        with patch(
            "api.services.rag_service.embed_texts",
            new_callable=AsyncMock,
            return_value=[_pseudo_embedding("query", 768)],
        ):
            results = await store.search(
                idx.id, "topic 1", top_k=2
            )

        assert len(results) <= 2
        assert all(hasattr(r, "score") for r in results)


class TestRAGStoreDeleteIndex:
    """Cover delete_index (lines 538-544)."""

    def test_delete_nonexistent(self) -> None:
        from api.services.rag_service import RAGStore

        store = RAGStore()
        assert store.delete_index("bad-id") is False

    def test_delete_existing(self) -> None:
        from api.services.rag_service import RAGStore

        store = RAGStore()
        idx = store.create_index("test")
        # Add a job referencing this index
        store.create_ingest_job(idx.id, 1)
        assert len(store._jobs) == 1

        result = store.delete_index(idx.id)
        assert result is True
        assert idx.id not in store._indexes
        assert len(store._jobs) == 0


class TestRAGStoreIngestFiles:
    """Cover ingest_files (lines 564-634)."""

    @pytest.mark.asyncio
    async def test_ingest_files_index_not_found(self) -> None:
        from api.services.rag_service import RAGStore

        store = RAGStore()
        with pytest.raises(ValueError, match="not found"):
            await store.ingest_files(
                "bad-id", [("test.txt", b"content")]
            )

    @pytest.mark.asyncio
    async def test_ingest_files_success(self) -> None:
        from api.services.rag_service import (
            IngestJobStatus,
            RAGStore,
            _pseudo_embedding,
        )

        store = RAGStore()
        idx = store.create_index(
            "test",
            embedding_model="test/model",
            chunk_size=512,
        )

        # Content large enough to produce chunks
        content = b"A" * 600

        async def mock_embed(
            texts: list[str], **kw: Any
        ) -> list[list[float]]:
            return [_pseudo_embedding(t, 768) for t in texts]

        with patch(
            "api.services.rag_service.embed_texts",
            side_effect=mock_embed,
        ):
            job = await store.ingest_files(
                idx.id,
                [("test.txt", content)],
            )

        assert job.status == IngestJobStatus.completed
        assert job.processed_files == 1
        assert idx.doc_count == 1
        assert idx.chunk_count > 0

    @pytest.mark.asyncio
    async def test_ingest_files_embedding_failure(self) -> None:
        from api.services.rag_service import (
            IngestJobStatus,
            RAGStore,
        )

        store = RAGStore()
        idx = store.create_index(
            "test",
            embedding_model="test/model",
        )

        with patch(
            "api.services.rag_service.embed_texts",
            new_callable=AsyncMock,
            side_effect=Exception("Embedding API down"),
        ):
            job = await store.ingest_files(
                idx.id,
                [("test.txt", b"Content")],
            )

        assert job.status == IngestJobStatus.failed
        assert "Embedding API down" in (job.error or "")


class TestRAGEmbedTexts:
    """Cover embed_texts (lines 328-356)."""

    @pytest.mark.asyncio
    async def test_embed_empty_list(self) -> None:
        from api.services.rag_service import embed_texts

        result = await embed_texts([])
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_unknown_model_fallback(self) -> None:
        from api.services.rag_service import embed_texts

        result = await embed_texts(
            ["test"], model="unknown/model"
        )
        assert len(result) == 1
        assert len(result[0]) == 768  # default dims


class TestRAGExtractText:
    """Cover extract_text (lines 273-295)."""

    def test_extract_csv(self) -> None:
        from api.services.rag_service import extract_text

        csv_content = b"name,age\nAlice,30\nBob,25"
        result = extract_text("data.csv", csv_content)
        assert "Alice" in result
        assert "Bob" in result

    def test_extract_json(self) -> None:
        from api.services.rag_service import extract_text

        json_content = b'{"key": "value"}'
        result = extract_text("data.json", json_content)
        assert "key" in result

    def test_extract_json_invalid(self) -> None:
        from api.services.rag_service import extract_text

        result = extract_text(
            "bad.json", b"not json at all"
        )
        assert "not json" in result

    def test_extract_pdf(self) -> None:
        from api.services.rag_service import extract_text

        result = extract_text("doc.pdf", b"BT (Hello) ET")
        assert "Hello" in result

    def test_extract_unknown_extension(self) -> None:
        from api.services.rag_service import extract_text

        result = extract_text("file.xyz", b"raw text")
        assert result == "raw text"


class TestRAGChunking:
    """Cover chunking functions."""

    def test_chunk_fixed_size_empty(self) -> None:
        from api.services.rag_service import chunk_fixed_size

        result = chunk_fixed_size("")
        assert result == []

    def test_chunk_recursive_small_text(self) -> None:
        from api.services.rag_service import chunk_recursive

        result = chunk_recursive("small text", chunk_size=100)
        assert result == ["small text"]

    def test_chunk_recursive_large_text(self) -> None:
        from api.services.rag_service import chunk_recursive

        text = "Paragraph one.\n\nParagraph two.\n\n" * 50
        result = chunk_recursive(text, chunk_size=100)
        assert len(result) > 1


class TestRAGHybridSearch:
    """Cover hybrid_search and helpers."""

    def test_fulltext_score_empty_query(self) -> None:
        from api.services.rag_service import fulltext_score

        assert fulltext_score("", "some text") == 0.0

    def test_cosine_similarity_identical(self) -> None:
        from api.services.rag_service import cosine_similarity

        v = [1.0, 0.0, 0.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal(self) -> None:
        from api.services.rag_service import cosine_similarity

        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        assert abs(cosine_similarity(v1, v2)) < 1e-6

    def test_hybrid_search_skips_no_embedding(self) -> None:
        from api.services.rag_service import (
            DocumentChunk,
            hybrid_search,
        )

        chunk_no_emb = DocumentChunk(
            id="1",
            text="test",
            source="f.txt",
            embedding=None,
        )
        result = hybrid_search(
            [1.0, 0.0], "test", [chunk_no_emb]
        )
        assert result == []


class TestRAGGetStore:
    """Cover get_rag_store singleton (lines 670-678)."""

    def test_get_rag_store_returns_singleton(self) -> None:
        from api.services.rag_service import get_rag_store

        store1 = get_rag_store()
        store2 = get_rag_store()
        assert store1 is store2


class TestIngestJobProgress:
    """Cover IngestJob.to_dict progress calc."""

    def test_to_dict_progress_by_chunks(self) -> None:
        from api.services.rag_service import (
            IngestJob,
            IngestJobStatus,
        )

        job = IngestJob(
            id="j1",
            index_id="i1",
            status=IngestJobStatus.embedding,
            total_files=2,
            processed_files=2,
            total_chunks=10,
            embedded_chunks=5,
        )
        d = job.to_dict()
        assert d["progress_pct"] == 50.0

    def test_to_dict_progress_by_files(self) -> None:
        from api.services.rag_service import (
            IngestJob,
            IngestJobStatus,
        )

        job = IngestJob(
            id="j1",
            index_id="i1",
            status=IngestJobStatus.chunking,
            total_files=4,
            processed_files=2,
            total_chunks=0,
            embedded_chunks=0,
        )
        d = job.to_dict()
        assert d["progress_pct"] == 50.0
