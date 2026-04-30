"""Tests for ``api/routes/secrets.py`` — the workspace secrets REST surface.

Covers the :http:post:`/api/v1/secrets` endpoint added for issue #175 plus
the existing ``GET /workspace`` / ``GET /`` / ``POST /{name}/rotate`` paths
where they overlap with the new request flow.

All external services (workspace backend, audit service) are mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.enums import UserRole
from api.services.auth import create_access_token
from engine.secrets.base import SecretEntry

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(role: UserRole = UserRole.viewer, user_id: uuid.UUID | None = None):
    uid = user_id or uuid.uuid4()
    mock = MagicMock()
    mock.id = uid
    mock.email = f"{role.value}@example.com"
    mock.name = f"{role.value} user"
    mock.role = role
    mock.team = "engineering"
    mock.is_active = True
    return mock


def _viewer_headers(user_id: uuid.UUID) -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(str(user_id), 'viewer@example.com', 'viewer')}"
    }


def _deployer_headers(user_id: uuid.UUID) -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(str(user_id), 'dep@example.com', 'deployer')}"
    }


class _FakeBackend:
    """In-memory fake of ``engine.secrets.base.SecretsBackend``."""

    backend_name = "env"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []

    async def set(self, name: str, value: str, *, tags=None) -> None:
        self.values[name] = value
        self.set_calls.append((name, value))

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def list(self) -> list[SecretEntry]:
        ts = datetime(2026, 4, 28, tzinfo=UTC)
        return [
            SecretEntry(
                name=k,
                masked_value=f"••••{v[-4:]}" if len(v) > 8 else "••••",
                backend=self.backend_name,
                updated_at=ts,
            )
            for k, v in self.values.items()
        ]

    async def delete(self, name: str) -> None:
        self.values.pop(name, None)

    async def rotate(self, name: str, new_value: str) -> None:
        if name not in self.values:
            raise KeyError(name)
        self.values[name] = new_value


def _ws_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.workspace = "default"
    return cfg


def _patch_auth(role: UserRole, backend: _FakeBackend):
    """Stack of patches enabling an authenticated request to flow through RBAC.

    Returns the ``user_id`` so callers can re-derive a matching JWT.
    """
    user_id = uuid.uuid4()
    user = _make_user(role=role, user_id=user_id)

    # All teams report the user's role so require_role("deployer") works.
    async def fake_get_teams(uid):
        team = MagicMock()
        team.id = "engineering"
        return [team]

    async def fake_get_role_in_team(uid, team_id):
        return role.value

    patches = [
        patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}),
        patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch(
            "api.services.team_service.TeamService.get_user_teams",
            new=AsyncMock(side_effect=fake_get_teams),
        ),
        patch(
            "api.services.team_service.TeamService.get_user_role_in_team",
            new=AsyncMock(side_effect=fake_get_role_in_team),
        ),
        patch(
            "api.routes.secrets.get_workspace_backend",
            return_value=(backend, _ws_cfg()),
        ),
        patch("api.routes.secrets._instantiate", return_value=backend),
    ]
    return patches, user_id


# ---------------------------------------------------------------------------
# POST /api/v1/secrets
# ---------------------------------------------------------------------------


class TestCreateSecret:
    @pytest.mark.no_auto_auth
    def test_unauthenticated_returns_401(self) -> None:
        resp = client.post("/api/v1/secrets", json={"name": "X", "value": "y"})
        assert resp.status_code == 401

    @pytest.mark.no_auto_auth
    def test_viewer_forbidden_403(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.viewer, backend)
        for p in patches:
            p.start()
        try:
            resp = client.post(
                "/api/v1/secrets",
                json={"name": "nvidia/api-key", "value": "nvapi-abc12345"},
                headers=_viewer_headers(uid),
            )
            assert resp.status_code == 403
            # Backend MUST NOT have been written to.
            assert backend.set_calls == []
        finally:
            for p in patches:
                p.stop()

    def test_deployer_creates_secret_and_returns_summary(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.deployer, backend)
        for p in patches:
            p.start()
        try:
            resp = client.post(
                "/api/v1/secrets",
                json={
                    "name": "nvidia/api-key",
                    "value": "nvapi-secret-12345",
                    "backend": "env",
                },
                headers=_deployer_headers(uid),
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            data = body["data"]
            assert data["name"] == "nvidia/api-key"
            assert data["backend"] == "env"
            assert data["workspace"] == "default"
            # Masked value must NOT include the raw secret.
            assert "nvapi-secret" not in data["masked_value"]
            assert data["masked_value"].startswith("••••")
            # Backend was actually written.
            assert backend.set_calls == [("nvidia/api-key", "nvapi-secret-12345")]
        finally:
            for p in patches:
                p.stop()

    def test_unsupported_backend_returns_400(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.deployer, backend)
        for p in patches:
            p.start()
        try:
            resp = client.post(
                "/api/v1/secrets",
                json={"name": "X", "value": "y", "backend": "lolnope"},
                headers=_deployer_headers(uid),
            )
            assert resp.status_code == 400
            assert "Unsupported backend" in resp.json()["detail"]
            assert backend.set_calls == []
        finally:
            for p in patches:
                p.stop()

    def test_missing_value_returns_422(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.deployer, backend)
        for p in patches:
            p.start()
        try:
            resp = client.post(
                "/api/v1/secrets",
                json={"name": "X"},
                headers=_deployer_headers(uid),
            )
            assert resp.status_code == 422
        finally:
            for p in patches:
                p.stop()

    def test_workspace_backend_used_when_backend_omitted(self) -> None:
        """When body omits ``backend``, falls back to workspace's configured backend."""
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.deployer, backend)
        for p in patches:
            p.start()
        try:
            resp = client.post(
                "/api/v1/secrets",
                json={"name": "groq/api-key", "value": "gsk_abc12345"},
                headers=_deployer_headers(uid),
            )
            assert resp.status_code == 201
            assert backend.set_calls == [("groq/api-key", "gsk_abc12345")]
        finally:
            for p in patches:
                p.stop()

    def test_audit_event_emitted(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.deployer, backend)
        log_event = AsyncMock()
        patches.append(patch("api.services.audit_service.AuditService.log_event", log_event))
        for p in patches:
            p.start()
        try:
            client.post(
                "/api/v1/secrets",
                json={"name": "kimi/api-key", "value": "ms-abcdef12"},
                headers=_deployer_headers(uid),
            )
            assert log_event.await_count == 1
            kwargs = log_event.await_args.kwargs
            assert kwargs["action"] == "secret.created"
            assert kwargs["resource_name"] == "kimi/api-key"
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# PUT /api/v1/secrets/workspace — backend chooser (issue #213)
# ---------------------------------------------------------------------------


def _admin_headers(user_id: uuid.UUID) -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(str(user_id), 'adm@example.com', 'admin')}"
    }


class TestSetWorkspaceBackend:
    @pytest.mark.no_auto_auth
    def test_unauthenticated_returns_401(self) -> None:
        resp = client.put("/api/v1/secrets/workspace", json={"backend": "env"})
        assert resp.status_code == 401

    @pytest.mark.no_auto_auth
    def test_viewer_forbidden_403(self, tmp_path) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.viewer, backend)
        save = MagicMock()
        patches.append(patch("api.routes.secrets.save_workspace_secrets_config", save))
        for p in patches:
            p.start()
        try:
            resp = client.put(
                "/api/v1/secrets/workspace",
                json={"backend": "env"},
                headers=_viewer_headers(uid),
            )
            assert resp.status_code == 403
            save.assert_not_called()
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.no_auto_auth
    def test_deployer_forbidden_403(self) -> None:
        """Backend swap is admin-only; deployers cannot change it."""
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.deployer, backend)
        save = MagicMock()
        patches.append(patch("api.routes.secrets.save_workspace_secrets_config", save))
        for p in patches:
            p.start()
        try:
            resp = client.put(
                "/api/v1/secrets/workspace",
                json={"backend": "env"},
                headers=_deployer_headers(uid),
            )
            assert resp.status_code == 403
            save.assert_not_called()
        finally:
            for p in patches:
                p.stop()

    def test_admin_persists_and_returns_workspace_info(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.admin, backend)
        saved_cfg = MagicMock()
        saved_cfg.workspace = "default"
        saved_cfg.backend = "env"
        saved_cfg.options = {}
        save = MagicMock(return_value=saved_cfg)
        patches.append(patch("api.routes.secrets.save_workspace_secrets_config", save))
        for p in patches:
            p.start()
        try:
            resp = client.put(
                "/api/v1/secrets/workspace",
                json={"backend": "env"},
                headers=_admin_headers(uid),
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()["data"]
            assert body["backend"] == "env"
            assert body["workspace"] == "default"
            assert "supported_backends" in body
            save.assert_called_once()
            # Backend kwarg passed through correctly
            assert save.call_args.kwargs["backend"] == "env"
        finally:
            for p in patches:
                p.stop()

    def test_unsupported_backend_rejected_400(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.admin, backend)
        save = MagicMock()
        patches.append(patch("api.routes.secrets.save_workspace_secrets_config", save))
        for p in patches:
            p.start()
        try:
            resp = client.put(
                "/api/v1/secrets/workspace",
                json={"backend": "nonsense_backend"},
                headers=_admin_headers(uid),
            )
            assert resp.status_code == 400
            save.assert_not_called()
        finally:
            for p in patches:
                p.stop()

    def test_audit_event_emitted_on_swap(self) -> None:
        backend = _FakeBackend()
        patches, uid = _patch_auth(UserRole.admin, backend)
        saved_cfg = MagicMock()
        saved_cfg.workspace = "default"
        saved_cfg.backend = "env"
        saved_cfg.options = {}
        patches.append(
            patch(
                "api.routes.secrets.save_workspace_secrets_config",
                MagicMock(return_value=saved_cfg),
            )
        )
        log_event = AsyncMock()
        patches.append(patch("api.services.audit_service.AuditService.log_event", log_event))
        for p in patches:
            p.start()
        try:
            resp = client.put(
                "/api/v1/secrets/workspace",
                json={"backend": "env"},
                headers=_admin_headers(uid),
            )
            assert resp.status_code == 200
            assert log_event.await_count == 1
            kwargs = log_event.await_args.kwargs
            assert kwargs["action"] == "secret.backend_changed"
            assert kwargs["resource_type"] == "workspace"
        finally:
            for p in patches:
                p.stop()
