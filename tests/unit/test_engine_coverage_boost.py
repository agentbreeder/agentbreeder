"""Coverage-boost tests for engine, registry, and API service modules.

Targets uncovered lines in:
- engine/deployers/gcp_cloudrun.py (60% -> higher)
- engine/providers/ollama_provider.py (66% -> higher)
- engine/providers/openai_provider.py (71% -> higher)
- registry/mcp_servers.py (64% -> higher)
- registry/prompts.py (77% -> higher)
- registry/tools.py (71% -> higher)
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

        with patch("builtins.__import__", side_effect=ImportError):
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

        def mock_import(name: str, *args: Any, **kw: Any) -> Any:
            if "artifactregistry" in name:
                raise ImportError("no module")
            return real_import(name, *args, **kw)

        with patch("builtins.__import__", side_effect=mock_import):
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
        gcp = CloudRunConfig(project_id="proj", region="us-central1")

        mock_ar_client = AsyncMock()
        mock_ar_client.get_repository = AsyncMock(return_value=MagicMock())

        mock_ar_module = MagicMock()
        mock_ar_module.ArtifactRegistryAsyncClient.return_value = mock_ar_client

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
        gcp = CloudRunConfig(project_id="proj", region="us-central1")

        mock_ar_client = AsyncMock()
        mock_ar_client.get_repository = AsyncMock(side_effect=Exception("Not found"))
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
        gcp = CloudRunConfig(project_id="proj", region="us-central1")

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
        mock_logging_client.list_entries.return_value = [mock_entry]

        mock_logging_module = MagicMock()
        mock_logging_module.Client.return_value = mock_logging_client

        real_import = __import__

        def mock_import(name: str, *args: Any, **kw: Any) -> Any:
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
            logs = await deployer.get_logs("test-agent", since=since)

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

        def mock_import(name: str, *args: Any, **kw: Any) -> Any:
            if "google.cloud" in name and "logging" in name:
                mod = MagicMock()
                mod.Client.side_effect = Exception("API error")
                return mod
            return real_import(name, *args, **kw)

        with patch("builtins.__import__", side_effect=mock_import):
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
        mock_run_client.get_service = AsyncMock(return_value=mock_service)

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
                    "google.cloud.run_v2": MagicMock(GetServiceRequest=mock_request_cls),
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
        with pytest.raises(RuntimeError, match="Cannot get status"):
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
        mock_service.terminal_condition.state = "CONDITION_SUCCEEDED"
        mock_service.latest_ready_revision = "rev-001"
        mock_service.ingress = "INGRESS_TRAFFIC_ALL"
        mock_service.labels = {"team": "platform"}

        mock_run_client = AsyncMock()
        mock_run_client.get_service = AsyncMock(return_value=mock_service)

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
                    "google.cloud.run_v2": MagicMock(GetServiceRequest=mock_request_cls),
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
        gcp = CloudRunConfig(project_id="proj", region="us-central1")

        mock_policy = MagicMock()
        mock_policy.bindings = []

        mock_run_client = AsyncMock()
        mock_run_client.get_iam_policy = AsyncMock(return_value=mock_policy)
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
            await deployer._allow_unauthenticated("test-agent", gcp)

        mock_run_client.set_iam_policy.assert_called_once()

    @pytest.mark.asyncio
    async def test_allow_unauthenticated_exception(self) -> None:
        from engine.deployers.gcp_cloudrun import (
            CloudRunConfig,
            GCPCloudRunDeployer,
        )

        deployer = GCPCloudRunDeployer()
        gcp = CloudRunConfig(project_id="proj", region="us-central1")

        with patch.object(
            deployer,
            "_get_run_client",
            side_effect=Exception("IAM error"),
        ):
            # Should not raise, just log warning
            await deployer._allow_unauthenticated("test-agent", gcp)


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
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(
            provider._client,
            "stream",
            return_value=mock_response,
        ):
            chunks = []
            async for chunk in provider.generate_stream([{"role": "user", "content": "Hi"}]):
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
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(
            provider._client,
            "stream",
            return_value=mock_response,
        ):
            chunks = []
            async for chunk in provider.generate_stream([{"role": "user", "content": "Hi"}]):
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
        codellama = next(m for m in models if "codellama" in m.id)
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
                await provider._request("POST", "/v1/chat/completions", {})

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
            with pytest.raises(ProviderError, match="Cannot connect"):
                await provider._request("POST", "/v1/chat/completions", {})

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

        with patch.object(provider, "generate_stream", side_effect=mock_stream):
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
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await OllamaProvider.detect()

        assert result is True

    @pytest.mark.asyncio
    async def test_detect_not_running(self) -> None:
        from engine.providers.ollama_provider import (
            OllamaProvider,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
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
                await provider._request("POST", "/chat/completions", {})

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
            with pytest.raises(ProviderError, match="Failed to connect"):
                await provider._request("POST", "/chat/completions", {})


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

        assert result.healthy is True

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

        assert result.healthy is False


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

        with patch.object(provider, "generate_stream", side_effect=mock_stream):
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
        assert OpenAIProvider._model_supports_tools("gpt-3.5-turbo")
        assert OpenAIProvider._model_supports_tools("o3-mini")
        assert OpenAIProvider._model_supports_tools("o4-mini")
        assert not OpenAIProvider._model_supports_tools("dall-e-3")
        assert not OpenAIProvider._model_supports_tools("whisper-1")


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
            pytest.raises(AuthenticationError, match="API key not found"),
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

        result = await McpServerRegistry.update(session, "bad-id")
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
        mock_result.scalar_one_or_none.return_value = mock_server
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

        result = await McpServerRegistry.delete(session, "bad-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_success(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_server = MagicMock()
        mock_server.name = "test-server"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_server
        session.execute = AsyncMock(return_value=mock_result)
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        result = await McpServerRegistry.delete(session, str(uuid.uuid4()))
        assert result is True
        session.delete.assert_called_once_with(mock_server)


class TestMcpServerRegistryGetById:
    """Cover get_by_id (lines 59-67)."""

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        result = await McpServerRegistry.get_by_id(session, "not-a-uuid")
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

        result = await McpServerRegistry.execute_tool(session, "bad-id", "tool", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_tool_stdio_fallback(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        mock_server = MagicMock()
        mock_server.transport = "stdio"
        mock_server.endpoint = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_server
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

        result = await PromptRegistry.update(session, "bad-id")
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
        mock_result.scalar_one_or_none.return_value = mock_prompt
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

        result = await PromptRegistry.delete(session, "bad-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_success(self) -> None:
        from registry.prompts import PromptRegistry

        session = AsyncMock()
        mock_prompt = MagicMock()
        mock_prompt.name = "test"
        mock_prompt.version = "1.0.0"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_prompt
        session.execute = AsyncMock(return_value=mock_result)
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        result = await PromptRegistry.delete(session, "some-id")
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

        result = await PromptRegistry.update_content(session, "bad-id", "new content")
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
        mock_result1.scalar_one_or_none.return_value = mock_prompt
        mock_result2 = MagicMock()
        mock_result2.scalar.return_value = 2  # 2 existing

        session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])
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
        mock_list_result.scalars.return_value.all.return_value = mock_prompts

        session.execute = AsyncMock(side_effect=[mock_count_result, mock_list_result])

        prompts, total = await PromptRegistry.search(session, "test")

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

        session.execute = AsyncMock(side_effect=[mock_r1, mock_r2])

        v1, v2, diff = await PromptRegistry.diff_version_snapshots(session, "p1", "v1", "v2")
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

        session.execute = AsyncMock(side_effect=[mock_r1, mock_r2])

        v1, v2, diff = await PromptRegistry.diff_version_snapshots(session, "p1", "v1-id", "v2-id")

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

        result = await PromptRegistry.duplicate(session, "bad-id")
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

        session.execute = AsyncMock(side_effect=[mock_r1, mock_r2])
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await PromptRegistry.duplicate(session, "some-id")

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

        result = await ToolRegistry.get_usage(session, "bad-id")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_usage_finds_matching_agents(self) -> None:
        from registry.tools import ToolRegistry

        session = AsyncMock()

        mock_tool = MagicMock()
        mock_tool.name = "zendesk-mcp"

        agent1 = MagicMock()
        agent1.config_snapshot = {"tools": [{"ref": "tools/zendesk-mcp"}]}
        agent1.status = "running"

        agent2 = MagicMock()
        agent2.config_snapshot = {"tools": [{"name": "other-tool"}]}
        agent2.status = "running"

        # First call: get_by_id, second: list agents
        mock_r1 = MagicMock()
        mock_r1.scalar_one_or_none.return_value = mock_tool

        mock_r2 = MagicMock()
        mock_r2.scalars.return_value.all.return_value = [
            agent1,
            agent2,
        ]

        session.execute = AsyncMock(side_effect=[mock_r1, mock_r2])

        result = await ToolRegistry.get_usage(session, str(uuid.uuid4()))
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

        session.execute = AsyncMock(side_effect=[mock_r1, mock_r2])

        result = await ToolRegistry.get_usage(session, str(uuid.uuid4()))
        assert result == []


class TestToolRegistryGetById:
    """Cover get_by_id invalid UUID (lines 92-100)."""

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self) -> None:
        from registry.tools import ToolRegistry

        session = AsyncMock()
        result = await ToolRegistry.get_by_id(session, "not-a-uuid")
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
        mock_list_result.scalars.return_value.all.return_value = mock_tools

        session.execute = AsyncMock(side_effect=[mock_count_result, mock_list_result])

        tools, total = await ToolRegistry.search(session, "zendesk")
        assert total == 1
        assert len(tools) == 1
