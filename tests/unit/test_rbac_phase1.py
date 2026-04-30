"""Phase 1 RBAC tests — verify auth enforcement across all route files.

Tests:
  1. All previously-unprotected endpoints now return 401 when called without a token.
  2. Admin-only endpoints return 403 for a viewer role.
  3. Deployer+ endpoints return 403 for a viewer role.
  4. Authenticated viewers can reach read-only endpoints.
  5. approvals.py approve/reject require admin.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.enums import UserRole
from api.services.auth import create_access_token, hash_password

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW_ISO = "2026-04-24T00:00:00+00:00"


def _make_user(
    email: str = "test@example.com",
    name: str = "Test User",
    role: UserRole = UserRole.viewer,
    user_id: uuid.UUID | None = None,
):
    """Return a mock User ORM object."""
    uid = user_id or uuid.uuid4()
    mock = MagicMock()
    mock.id = uid
    mock.email = email
    mock.name = name
    mock.role = role
    mock.team = "engineering"
    mock.is_active = True
    mock.password_hash = hash_password("testpass")
    return mock


def _viewer_headers(user_id: uuid.UUID | None = None) -> dict:
    uid = user_id or uuid.uuid4()
    token = create_access_token(str(uid), "viewer@example.com", "viewer")
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(user_id: uuid.UUID | None = None) -> dict:
    uid = user_id or uuid.uuid4()
    token = create_access_token(str(uid), "admin@example.com", "admin")
    return {"Authorization": f"Bearer {token}"}


def _deployer_headers(user_id: uuid.UUID | None = None) -> dict:
    uid = user_id or uuid.uuid4()
    token = create_access_token(str(uid), "deployer@example.com", "deployer")
    return {"Authorization": f"Bearer {token}"}


def _mock_db():
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=MagicMock())
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


# ---------------------------------------------------------------------------
# Decorator to patch get_current_user / require_role with a real user
# ---------------------------------------------------------------------------


def _patch_auth(role: UserRole = UserRole.viewer):
    """Context manager: patch get_current_user + get_user_by_id to return a mock user."""
    user_id = uuid.uuid4()
    user = _make_user(role=role, user_id=user_id)

    patches = [
        patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}),
        patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch("api.database.get_db", return_value=_mock_db()),
    ]
    return patches, user


# ---------------------------------------------------------------------------
# 1. 401 for unauthenticated requests
# ---------------------------------------------------------------------------


class TestUnauthenticated401:
    """Every previously-open endpoint should now return 401."""

    # ── agentops ──
    def test_agentops_fleet(self):
        assert client.get("/api/v1/agentops/fleet").status_code == 401

    def test_agentops_incidents_list(self):
        assert client.get("/api/v1/agentops/incidents").status_code == 401

    # ── audit ──
    def test_audit_list(self):
        assert client.get("/api/v1/audit").status_code == 401

    # ── builders ──
    def test_builders_get_yaml(self):
        assert client.get("/api/v1/builders/agent/test/yaml").status_code == 401

    # ── compliance ──
    def test_compliance_standards(self):
        assert client.get("/api/v1/compliance/standards").status_code == 401

    # ── costs ──
    def test_costs_summary(self):
        assert client.get("/api/v1/costs/summary").status_code == 401

    # ── deploys ──
    def test_deploys_list(self):
        assert client.get("/api/v1/deploys").status_code == 401

    def test_deploys_create(self):
        assert client.post("/api/v1/deploys", json={}).status_code == 401

    # ── evals ──
    def test_evals_datasets_list(self):
        assert client.get("/api/v1/eval/datasets").status_code == 401

    # ── gateway ──
    def test_gateway_status(self):
        assert client.get("/api/v1/gateway/status").status_code == 401

    # ── git ──
    def test_git_branches_list(self):
        assert client.get("/api/v1/git/branches").status_code == 401

    # ── mcp_servers ──
    def test_mcp_servers_list(self):
        assert client.get("/api/v1/mcp-servers").status_code == 401

    # ── memory ──
    def test_memory_configs_list(self):
        assert client.get("/api/v1/memory/configs").status_code == 401

    # ── orchestrations ──
    def test_orchestrations_list(self):
        assert client.get("/api/v1/orchestrations").status_code == 401

    # ── playground ──
    def test_playground_chat(self):
        assert client.post("/api/v1/playground/chat", json={}).status_code == 401

    # ── prompts ──
    def test_prompts_test(self):
        assert client.post("/api/v1/prompts/test", json={}).status_code == 401

    # ── rag ──
    def test_rag_indexes_list(self):
        assert client.get("/api/v1/rag/indexes").status_code == 401

    # ── registry ──
    def test_registry_tools_list(self):
        assert client.get("/api/v1/registry/tools").status_code == 401

    def test_registry_models_list(self):
        assert client.get("/api/v1/registry/models").status_code == 401

    # ── sandbox ──
    def test_sandbox_execute(self):
        assert client.post("/api/v1/tools/sandbox/execute", json={}).status_code == 401

    # ── tracing ──
    def test_tracing_list(self):
        assert client.get("/api/v1/traces").status_code == 401

    # ── teams ──
    def test_teams_list(self):
        assert client.get("/api/v1/teams").status_code == 401

    def test_teams_create(self):
        assert client.post("/api/v1/teams", json={}).status_code == 401

    # ── approvals ──
    def test_approvals_list(self):
        assert client.get("/api/v1/approvals/").status_code == 401

    def test_approvals_request(self):
        assert client.post("/api/v1/approvals/", json={}).status_code == 401

    def test_approvals_approve(self):
        assert client.post(f"/api/v1/approvals/{uuid.uuid4()}/approve").status_code == 401

    def test_approvals_reject(self):
        assert client.post(f"/api/v1/approvals/{uuid.uuid4()}/reject").status_code == 401

    # ── a2a ──
    def test_a2a_agents_list(self):
        assert client.get("/api/v1/a2a/agents").status_code == 401

    def test_a2a_agents_create(self):
        assert client.post("/api/v1/a2a/agents", json={}).status_code == 401


# ---------------------------------------------------------------------------
# 2. 403 for viewer on admin-only endpoints
# ---------------------------------------------------------------------------


class TestViewerForbiddenOnAdminEndpoints:
    """Viewer role must be rejected on admin-only routes."""

    def _auth_patches(self):
        user_id = uuid.uuid4()
        viewer = _make_user(role=UserRole.viewer, user_id=user_id)
        return [
            patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}),
            patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=viewer),
            patch("api.database.get_db", return_value=_mock_db()),
            patch(
                "api.services.team_service.TeamService.get_user_teams",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ]

    def test_teams_create_requires_admin(self):
        patches = self._auth_patches()
        for p in patches:
            p.start()
        try:
            headers = _viewer_headers()
            resp = client.post(
                "/api/v1/teams", json={"name": "x", "display_name": "X"}, headers=headers
            )
            assert resp.status_code == 403
        finally:
            for p in patches:
                p.stop()

    def test_teams_delete_requires_admin(self):
        patches = self._auth_patches()
        for p in patches:
            p.start()
        try:
            headers = _viewer_headers()
            resp = client.delete(f"/api/v1/teams/{uuid.uuid4()}", headers=headers)
            assert resp.status_code == 403
        finally:
            for p in patches:
                p.stop()

    def test_approvals_approve_requires_admin(self):
        patches = self._auth_patches()
        for p in patches:
            p.start()
        try:
            headers = _viewer_headers()
            resp = client.post(f"/api/v1/approvals/{uuid.uuid4()}/approve", headers=headers)
            assert resp.status_code == 403
        finally:
            for p in patches:
                p.stop()

    def test_approvals_reject_requires_admin(self):
        patches = self._auth_patches()
        for p in patches:
            p.start()
        try:
            headers = _viewer_headers()
            resp = client.post(f"/api/v1/approvals/{uuid.uuid4()}/reject", headers=headers)
            assert resp.status_code == 403
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# 3. 403 for viewer on deployer+ endpoints
# ---------------------------------------------------------------------------


class TestViewerForbiddenOnDeployerEndpoints:
    """Viewer must be rejected on deployer+ write routes."""

    def _auth_patches(self):
        user_id = uuid.uuid4()
        viewer = _make_user(role=UserRole.viewer, user_id=user_id)
        return [
            patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}),
            patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=viewer),
            patch("api.database.get_db", return_value=_mock_db()),
            patch(
                "api.services.team_service.TeamService.get_user_teams",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ]

    def test_deploys_create_requires_deployer(self):
        patches = self._auth_patches()
        for p in patches:
            p.start()
        try:
            headers = _viewer_headers()
            resp = client.post(
                "/api/v1/deploys",
                json={"config_yaml": "name: x\nversion: 1.0.0", "target": "local"},
                headers=headers,
            )
            assert resp.status_code == 403
        finally:
            for p in patches:
                p.stop()

    def test_sandbox_execute_requires_deployer(self):
        patches = self._auth_patches()
        for p in patches:
            p.start()
        try:
            headers = _viewer_headers()
            resp = client.post(
                "/api/v1/tools/sandbox/execute",
                json={"tool_name": "t", "code": "pass", "input": {}},
                headers=headers,
            )
            assert resp.status_code == 403
        finally:
            for p in patches:
                p.stop()

    def test_a2a_create_requires_deployer(self):
        patches = self._auth_patches()
        for p in patches:
            p.start()
        try:
            headers = _viewer_headers()
            resp = client.post(
                "/api/v1/a2a/agents",
                json={"name": "bot", "endpoint_url": "http://localhost"},
                headers=headers,
            )
            assert resp.status_code == 403
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# 4. Authenticated viewer can reach read-only endpoints
# ---------------------------------------------------------------------------


class TestViewerCanReadPublicEndpoints:
    """Viewer-role users should be able to hit read-only (GET) endpoints."""

    def test_agentops_fleet_viewer_ok(self):
        # /agentops/fleet became DB-backed in #206 — patching ``api.database.get_db``
        # no longer intercepts the FastAPI dependency, so we override it directly
        # on the app and stub ``FleetService.get_fleet_overview`` to avoid a real
        # PostgreSQL hit. RBAC behaviour (viewer can read) is unchanged.
        from api.database import get_db
        from api.main import app

        user_id = uuid.uuid4()
        viewer = _make_user(role=UserRole.viewer, user_id=user_id)

        async def _stub_db():
            return AsyncMock()

        app.dependency_overrides[get_db] = _stub_db
        try:
            with (
                patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}),
                patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=viewer),
                patch(
                    "api.routes.agentops.FleetService.get_fleet_overview",
                    new_callable=AsyncMock,
                    return_value={"agents": [], "summary": {"total": 0}},
                ),
            ):
                headers = _viewer_headers(user_id)
                resp = client.get("/api/v1/agentops/fleet", headers=headers)
                assert resp.status_code != 401
                assert resp.status_code != 403
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_gateway_status_viewer_ok(self):
        user_id = uuid.uuid4()
        viewer = _make_user(role=UserRole.viewer, user_id=user_id)
        with (
            patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}),
            patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=viewer),
            patch("api.database.get_db", return_value=_mock_db()),
        ):
            headers = _viewer_headers(user_id)
            resp = client.get("/api/v1/gateway/status", headers=headers)
            assert resp.status_code != 401
            assert resp.status_code != 403


# ---------------------------------------------------------------------------
# 5. RBAC middleware — require_role DB-backed check
# ---------------------------------------------------------------------------


class TestRequireRoleDBBacked:
    """require_role() must use TeamService.get_user_teams (DB-backed), not _memberships."""

    @pytest.mark.asyncio
    async def test_require_role_uses_get_user_teams(self):
        """When require_role is checked, it calls TeamService.get_user_teams not _memberships."""
        from api.middleware.rbac import require_role
        from api.services.team_service import TeamService

        user_id = uuid.uuid4()
        user = _make_user(role=UserRole.viewer, user_id=user_id)

        # Simulate user having deployer role in one team
        mock_team = MagicMock()
        mock_team.id = "team-123"

        with (
            patch.object(
                TeamService,
                "get_user_teams",
                new_callable=AsyncMock,
                return_value=[mock_team],
            ) as mock_get_teams,
            patch.object(
                TeamService,
                "get_user_role_in_team",
                new_callable=AsyncMock,
                return_value="deployer",
            ),
            patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=user),
            patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}),
            patch("api.database.get_db", return_value=_mock_db()),
        ):
            checker = require_role("deployer")

            # Build a mock credentials object and call the inner check function
            creds = MagicMock()
            creds.credentials = create_access_token(str(user_id), "test@test.com", "viewer")

            AsyncMock()
            result_user = await checker(user=user)
            assert result_user is user
            mock_get_teams.assert_awaited_once_with(str(user_id))

    @pytest.mark.asyncio
    async def test_require_role_raises_403_for_insufficient_role(self):
        """User with only viewer membership gets 403 for deployer-required route."""
        from fastapi import HTTPException

        from api.middleware.rbac import require_role
        from api.services.team_service import TeamService

        user_id = uuid.uuid4()
        user = _make_user(role=UserRole.viewer, user_id=user_id)

        mock_team = MagicMock()
        mock_team.id = "team-456"

        with (
            patch.object(
                TeamService,
                "get_user_teams",
                new_callable=AsyncMock,
                return_value=[mock_team],
            ),
            patch.object(
                TeamService,
                "get_user_role_in_team",
                new_callable=AsyncMock,
                return_value="viewer",
            ),
        ):
            checker = require_role("deployer")
            with pytest.raises(HTTPException) as exc_info:
                await checker(user=user)
            assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# 6. Approvals endpoint integration
# ---------------------------------------------------------------------------


class TestApprovalsRequireAuth:
    """All approvals routes require authentication; approve/reject require admin."""

    def test_approvals_list_no_token_401(self):
        assert client.get("/api/v1/approvals/").status_code == 401

    def test_approvals_request_no_token_401(self):
        assert client.post("/api/v1/approvals/", json={}).status_code == 401

    def test_approvals_approve_no_token_401(self):
        assert client.post(f"/api/v1/approvals/{uuid.uuid4()}/approve").status_code == 401

    def test_approvals_reject_no_token_401(self):
        assert client.post(f"/api/v1/approvals/{uuid.uuid4()}/reject").status_code == 401
