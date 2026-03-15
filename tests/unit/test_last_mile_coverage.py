"""Last-mile coverage tests — targeting specific uncovered lines."""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.auth import create_access_token


def _auth_headers(role: str = "admin") -> dict[str, str]:
    token = create_access_token(
        str(uuid.uuid4()), "test@test.com", role
    )
    return {"Authorization": f"Bearer {token}"}


# ── engine/providers/registry.py — get_provider for each type ────


class TestProviderRegistryGetProvider:
    """Cover engine/providers/registry.py lines 72-84."""

    def test_get_provider_openai(self) -> None:
        from engine.providers.registry import create_provider_from_env

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-t"}):
            p = create_provider_from_env("openai")
        assert p is not None

    def test_get_provider_anthropic(self) -> None:
        from engine.providers.registry import create_provider_from_env

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            p = create_provider_from_env("anthropic")
        assert p is not None

    def test_get_provider_google(self) -> None:
        from engine.providers.registry import create_provider_from_env

        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "k"}):
            p = create_provider_from_env("google")
        assert p is not None

    def test_get_provider_ollama(self) -> None:
        from engine.providers.registry import create_provider_from_env

        p = create_provider_from_env("ollama")
        assert p is not None

    def test_get_provider_openrouter(self) -> None:
        from engine.providers.registry import create_provider_from_env

        env = {
            "OPENROUTER_API_KEY": "k",
            "OPENROUTER_BASE_URL": "https://or.ai",
        }
        with patch.dict(os.environ, env):
            p = create_provider_from_env("openrouter")
        assert p is not None

    def test_fallback_chain_single_provider(self) -> None:
        from engine.providers.models import (
            FallbackConfig,
            ProviderConfig,
        )
        from engine.providers.registry import FallbackChain

        cfg = FallbackConfig(
            primary=ProviderConfig(
                provider_type="openai", api_key="k"
            ),
            fallbacks=[],
        )
        chain = FallbackChain(cfg)
        assert len(chain.providers) == 1

    def test_fallback_chain_with_fallbacks(self) -> None:
        from engine.providers.models import (
            FallbackConfig,
            ProviderConfig,
        )
        from engine.providers.registry import FallbackChain

        cfg = FallbackConfig(
            primary=ProviderConfig(
                provider_type="openai", api_key="k1"
            ),
            fallbacks=[
                ProviderConfig(
                    provider_type="anthropic", api_key="k2"
                ),
            ],
        )
        chain = FallbackChain(cfg)
        assert len(chain.providers) == 2


# ── engine/deployers/docker_compose.py — deploy/teardown ─────────


class TestDockerComposeDeployerImportError:
    """Cover docker_compose.py lines 70-72, 95-97."""

    @pytest.mark.asyncio
    async def test_deploy_no_docker_sdk(self) -> None:
        from engine.deployers.docker_compose import (
            DockerComposeDeployer,
        )

        deployer = DockerComposeDeployer()
        config = MagicMock()
        config.name = "test"
        image = MagicMock()

        with patch.dict("sys.modules", {"docker": None}):
            with pytest.raises(RuntimeError, match="Docker SDK"):
                await deployer.deploy(config, image)

    @pytest.mark.asyncio
    async def test_teardown_no_docker_sdk(self) -> None:
        from engine.deployers.docker_compose import (
            DockerComposeDeployer,
        )

        deployer = DockerComposeDeployer()

        with patch.dict("sys.modules", {"docker": None}):
            with pytest.raises(RuntimeError, match="Docker SDK"):
                await deployer.teardown("test-agent")


# ── registry/mcp_servers.py — test_connection, discover_tools ────


class TestMcpServerRegistryAsync:
    """Cover mcp_servers.py lines 122-191."""

    @pytest.mark.asyncio
    async def test_connection_stdio_transport(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        mock_server = MagicMock()
        mock_server.transport = "stdio"
        mock_server.endpoint = None

        mock_session = AsyncMock()

        with patch.object(
            McpServerRegistry, "get_by_id",
            new_callable=AsyncMock,
            return_value=mock_server,
        ):
            result = await McpServerRegistry.test_connection(
                mock_session, "server-1"
            )
            assert result["success"] is True
            assert result["latency_ms"] == 0

    @pytest.mark.asyncio
    async def test_connection_not_found(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        mock_session = AsyncMock()

        with patch.object(
            McpServerRegistry, "get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await McpServerRegistry.test_connection(
                mock_session, "nonexistent"
            )
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_discover_tools_not_found(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        mock_session = AsyncMock()

        with patch.object(
            McpServerRegistry, "get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await McpServerRegistry.discover_tools(
                mock_session, "nonexistent"
            )
            assert result["tools"] == []
            assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_discover_tools_stdio_fallback(self) -> None:
        from registry.mcp_servers import McpServerRegistry

        mock_server = MagicMock()
        mock_server.transport = "stdio"
        mock_server.endpoint = None
        mock_server.name = "test-mcp"

        mock_session = AsyncMock()

        with patch.object(
            McpServerRegistry, "get_by_id",
            new_callable=AsyncMock,
            return_value=mock_server,
        ):
            result = await McpServerRegistry.discover_tools(
                mock_session, "server-1"
            )
            # Falls back to placeholder tools
            assert result["total"] > 0


# ── api/main.py — seed_admin ─────────────────────────────────────


class TestSeedAdmin:
    """Cover api/main.py lines 46-66."""

    @pytest.mark.asyncio
    async def test_seed_creates_user(self) -> None:
        from api.main import _seed_default_admin

        mock_db = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.database.async_session",
                return_value=mock_ctx,
            ),
            patch(
                "api.services.auth.get_user_by_email",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_get,
            patch(
                "api.services.auth.create_user",
                new_callable=AsyncMock,
            ) as mock_create,
        ):
            await _seed_default_admin()
            mock_get.assert_called_once()
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_seed_skips_existing(self) -> None:
        from api.main import _seed_default_admin

        mock_db = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.database.async_session",
                return_value=mock_ctx,
            ),
            patch(
                "api.services.auth.get_user_by_email",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "api.services.auth.create_user",
                new_callable=AsyncMock,
            ) as mock_create,
        ):
            await _seed_default_admin()
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_handles_db_error(self) -> None:
        from api.main import _seed_default_admin

        mock_db = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.database.async_session",
                return_value=mock_ctx,
            ),
            patch(
                "api.services.auth.get_user_by_email",
                new_callable=AsyncMock,
                side_effect=Exception("db error"),
            ),
        ):
            await _seed_default_admin()  # Should not raise


# ── engine/resolver.py — resolve refs ────────────────────────────


class TestEngineResolver:
    """Cover engine/resolver.py lines 38-57."""

    def test_resolve_tool_refs(self) -> None:
        from engine.resolver import resolve_dependencies

        config = MagicMock()
        config.tools = [
            MagicMock(ref="tools/search", name=None),
            MagicMock(ref=None, name="inline-tool"),
        ]
        config.knowledge_bases = []
        config.prompts = MagicMock()
        config.prompts.system = "You are helpful."

        result = resolve_dependencies(config)
        assert result is not None

    def test_resolve_kb_refs(self) -> None:
        from engine.resolver import resolve_dependencies

        config = MagicMock()
        config.tools = []
        config.knowledge_bases = [
            MagicMock(ref="kb/product-docs"),
        ]
        config.prompts = MagicMock()
        config.prompts.system = "You are helpful."

        result = resolve_dependencies(config)
        assert result is not None

    def test_resolve_prompt_ref(self) -> None:
        from engine.resolver import resolve_dependencies

        config = MagicMock()
        config.tools = []
        config.knowledge_bases = []
        config.prompts = MagicMock()
        config.prompts.system = "prompts/support-v3"

        result = resolve_dependencies(config)
        assert result is not None


# ── api/services/auth.py — remaining paths ───────────────────────


class TestAuthServicePaths:
    """Cover api/services/auth.py lines 51-57, 69-78."""

    def test_create_and_decode_token(self) -> None:
        from api.services.auth import (
            create_access_token,
            decode_access_token,
        )

        uid = str(uuid.uuid4())
        token = create_access_token(uid, "a@b.com", "admin")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == uid

    def test_decode_invalid_token(self) -> None:
        from api.services.auth import decode_access_token

        result = decode_access_token("not.a.valid.jwt")
        assert result is None

    def test_hash_and_verify_password(self) -> None:
        from api.services.auth import (
            hash_password,
            verify_password,
        )

        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed) is True
        assert verify_password("wrong", hashed) is False
