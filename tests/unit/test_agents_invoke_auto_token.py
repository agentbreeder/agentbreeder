"""Tests for server-side ``AGENT_AUTH_TOKEN`` resolution in the invoke proxy.

Issue #176 — the dashboard no longer sends a bearer token from the browser.
The API resolves it from the workspace secrets backend keyed by
``agentbreeder/<agent-name>/auth-token``. These tests cover:

* the deterministic-name helper
* lookup happy path (secret found → forwarded as ``Authorization`` header)
* lookup miss (secret absent → no ``Authorization`` header, no 500)
* lookup error (backend raises → no ``Authorization`` header, no 500)
* explicit override (``body.auth_token`` wins, secrets backend not consulted)
* legacy env-var fallback (when neither override nor secret is set)
* missing endpoint → 400
* missing agent → 404
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.agents import (
    _agent_auth_token_secret_name,
    _resolve_agent_auth_token,
    router,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_mock_agent(name: str = "support-bot", endpoint: str = "http://agent:8080") -> MagicMock:
    agent = MagicMock()
    agent.id = "11111111-1111-1111-1111-111111111111"
    agent.name = name
    agent.endpoint_url = endpoint
    return agent


def _make_test_app() -> FastAPI:
    """FastAPI app with mocked auth + DB dependency overrides."""
    from api.auth import get_current_user
    from api.database import get_db

    mock_user = MagicMock()
    mock_user.email = "alice@example.com"

    mock_db = AsyncMock()

    app = FastAPI()

    async def override_get_db():
        yield mock_db

    async def override_get_current_user():
        return mock_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.include_router(router)
    return app


def _patch_runtime_response(*, status_code: int = 200, payload: dict | None = None):
    """Build an httpx mock that captures the call and returns a canned response."""
    payload = payload or {"output": "hi", "session_id": "sess-1"}
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = payload
    mock_response.text = "ok"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ── deterministic-name helper ────────────────────────────────────────────────


class TestSecretNameHelper:
    def test_format_matches_track_k_convention(self) -> None:
        assert (
            _agent_auth_token_secret_name("support-bot") == "agentbreeder/support-bot/auth-token"
        )

    def test_passes_name_through_verbatim(self) -> None:
        # Caller is responsible for slug normalisation (matches deploy mirror).
        assert _agent_auth_token_secret_name("Weird_Name") == "agentbreeder/Weird_Name/auth-token"


# ── _resolve_agent_auth_token ────────────────────────────────────────────────


class TestResolveAgentAuthToken:
    @pytest.mark.asyncio
    async def test_returns_value_when_backend_has_secret(self) -> None:
        backend = AsyncMock()
        backend.get = AsyncMock(return_value="secret-token")
        with patch(
            "engine.secrets.factory.get_workspace_backend",
            return_value=(backend, MagicMock()),
        ):
            token = await _resolve_agent_auth_token("support-bot")
        assert token == "secret-token"
        backend.get.assert_awaited_once_with("agentbreeder/support-bot/auth-token")

    @pytest.mark.asyncio
    async def test_returns_none_when_secret_missing(self) -> None:
        backend = AsyncMock()
        backend.get = AsyncMock(return_value=None)
        with patch(
            "engine.secrets.factory.get_workspace_backend",
            return_value=(backend, MagicMock()),
        ):
            token = await _resolve_agent_auth_token("support-bot")
        assert token is None

    @pytest.mark.asyncio
    async def test_returns_none_when_secret_empty_string(self) -> None:
        backend = AsyncMock()
        backend.get = AsyncMock(return_value="   ")
        with patch(
            "engine.secrets.factory.get_workspace_backend",
            return_value=(backend, MagicMock()),
        ):
            token = await _resolve_agent_auth_token("support-bot")
        assert token is None

    @pytest.mark.asyncio
    async def test_returns_none_and_logs_when_backend_raises(self) -> None:
        backend = AsyncMock()
        backend.get = AsyncMock(side_effect=RuntimeError("kms unavailable"))
        with patch(
            "engine.secrets.factory.get_workspace_backend",
            return_value=(backend, MagicMock()),
        ):
            token = await _resolve_agent_auth_token("support-bot")
        assert token is None  # never raises out of the proxy

    @pytest.mark.asyncio
    async def test_returns_none_when_factory_raises(self) -> None:
        with patch(
            "engine.secrets.factory.get_workspace_backend",
            side_effect=ImportError("backend dep missing"),
        ):
            token = await _resolve_agent_auth_token("support-bot")
        assert token is None


# ── invoke proxy: end-to-end via TestClient ──────────────────────────────────


class TestInvokeProxyTokenResolution:
    def test_secret_token_attached_as_bearer_when_request_omits_token(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent()
        mock_client = _patch_runtime_response()

        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            patch(
                "api.routes.agents._resolve_agent_auth_token",
                AsyncMock(return_value="resolved-secret"),
            ),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hello"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["output"] == "hi"
        assert body["data"]["session_id"] == "sess-1"
        # Authorization header was injected from the resolved secret.
        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer resolved-secret"
        assert call_kwargs["json"] == {"input": "hello"}
        # /invoke path is appended to the endpoint url.
        assert mock_client.post.call_args.args[0] == "http://agent:8080/invoke"

    def test_explicit_body_token_overrides_stored_secret(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent()
        mock_client = _patch_runtime_response()

        resolver = AsyncMock(return_value="should-not-be-used")
        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            patch("api.routes.agents._resolve_agent_auth_token", resolver),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hi", "auth_token": "explicit-override"},
            )

        assert resp.status_code == 200
        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer explicit-override"
        # Secrets resolver MUST NOT be consulted when caller supplies the token.
        resolver.assert_not_awaited()

    def test_no_auth_header_when_secret_missing(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent()
        mock_client = _patch_runtime_response()

        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            patch(
                "api.routes.agents._resolve_agent_auth_token",
                AsyncMock(return_value=None),
            ),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
            patch.dict("os.environ", {}, clear=False),
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hello"},
            )

        assert resp.status_code == 200
        call_kwargs = mock_client.post.call_args.kwargs
        # No Authorization header when nothing resolves — runtime will 401.
        assert "Authorization" not in call_kwargs["headers"]

    def test_falls_back_to_legacy_env_var_when_secret_missing(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent(name="support-bot")
        mock_client = _patch_runtime_response()

        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            patch(
                "api.routes.agents._resolve_agent_auth_token",
                AsyncMock(return_value=None),
            ),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
            patch.dict("os.environ", {"AGENT_SUPPORT_BOT_TOKEN": "legacy-env-token"}, clear=False),
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hello"},
            )

        assert resp.status_code == 200
        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer legacy-env-token"

    def test_resolver_failure_falls_through_no_500(self) -> None:
        """A backend exception inside the resolver must not propagate as a 500."""
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent()
        mock_client = _patch_runtime_response()

        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            # The real resolver is wrapped in try/except; here we just simulate
            # the post-recovery "None" return value.
            patch(
                "api.routes.agents._resolve_agent_auth_token",
                AsyncMock(return_value=None),
            ),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hello"},
            )

        assert resp.status_code == 200

    def test_endpoint_override_in_request_body(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent(endpoint="http://default:8080")
        mock_client = _patch_runtime_response()

        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            patch(
                "api.routes.agents._resolve_agent_auth_token",
                AsyncMock(return_value="t"),
            ),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hi", "endpoint_url": "https://override.example.com/"},
            )

        assert resp.status_code == 200
        # Trailing slash stripped, /invoke appended.
        assert mock_client.post.call_args.args[0] == "https://override.example.com/invoke"

    def test_session_id_is_forwarded(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent()
        mock_client = _patch_runtime_response()

        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            patch(
                "api.routes.agents._resolve_agent_auth_token",
                AsyncMock(return_value=None),
            ),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hi", "session_id": "sess-99"},
            )

        assert resp.status_code == 200
        assert mock_client.post.call_args.kwargs["json"] == {
            "input": "hi",
            "session_id": "sess-99",
        }

    def test_404_when_agent_does_not_exist(self) -> None:
        app = _make_test_app()
        client = TestClient(app)

        with patch("registry.agents.AgentRegistry.get_by_id", AsyncMock(return_value=None)):
            resp = client.post(
                "/api/v1/agents/22222222-2222-2222-2222-222222222222/invoke",
                json={"input": "hi"},
            )

        assert resp.status_code == 404

    def test_400_when_no_endpoint_resolvable(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent(endpoint="")
        agent.endpoint_url = None

        with patch(
            "registry.agents.AgentRegistry.get_by_id",
            AsyncMock(return_value=agent),
        ):
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hi"},
            )

        assert resp.status_code == 400
        assert "endpoint_url" in resp.json()["detail"]

    def test_runtime_4xx_surfaces_via_response_body(self) -> None:
        app = _make_test_app()
        client = TestClient(app)
        agent = _make_mock_agent()
        mock_client = _patch_runtime_response(status_code=401)
        mock_client.post.return_value.text = "Unauthorized"

        with (
            patch(
                "registry.agents.AgentRegistry.get_by_id",
                AsyncMock(return_value=agent),
            ),
            patch(
                "api.routes.agents._resolve_agent_auth_token",
                AsyncMock(return_value=None),
            ),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                f"/api/v1/agents/{agent.id}/invoke",
                json={"input": "hi"},
            )

        # The proxy itself returns 200; the runtime status is in the envelope.
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status_code"] == 401
        assert "Unauthorized" in body["data"]["error"]
