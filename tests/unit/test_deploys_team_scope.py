"""Tests for the team-scoped RBAC gate on /api/v1/deploys (issue #414).

The HR-1 analogue for deploys: ``require_role("deployer")`` without
``resource_team=`` lets a user with ``deployer`` in team A trigger a deploy
that targets an agent owned by team B. These tests verify the tightened
gate added in #414:

* POST /api/v1/deploys with ``agent_id`` → 403 when the caller is a
  deployer in a different team than the agent.
* POST /api/v1/deploys with ``config_yaml`` → 403 when the caller is a
  deployer in a different team than the YAML's ``team:`` field.
* DELETE /api/v1/deploys/{id} and POST /api/v1/deploys/{id}/rollback →
  403 when caller is a deployer in a different team than the job's agent.
* Same-team deployer is allowed.
* Platform admins pass regardless of team.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app
from api.models.enums import UserRole
from api.services.auth import create_access_token

# Opt every test in this module out of the conftest's auto-auth admin override
# — we need to test what happens when the *caller's* role doesn't satisfy the
# team-scope gate, so we can't have an autouse fixture handing every test a
# platform-admin user.
pytestmark = pytest.mark.no_auto_auth

client = TestClient(app)


# ── Test doubles ────────────────────────────────────────────────────────────


def _make_user(team: str, role: UserRole) -> MagicMock:
    mock = MagicMock()
    mock.id = uuid.uuid4()
    mock.email = f"{role.value}@{team}.example.com"
    mock.name = f"Test {role.value}"
    mock.role = role
    mock.team = team
    mock.is_active = True
    return mock


def _bearer(user: MagicMock) -> dict[str, str]:
    token = create_access_token(str(user.id), user.email, str(user.role.value))
    return {"Authorization": f"Bearer {token}"}


def _make_agent(team: str, agent_id: uuid.UUID | None = None) -> MagicMock:
    mock = MagicMock()
    mock.id = agent_id or uuid.uuid4()
    mock.name = f"agent-in-{team}"
    mock.team = team
    return mock


def _make_job(agent_id: uuid.UUID, job_id: uuid.UUID | None = None) -> SimpleNamespace:
    """Build a DeployJob stand-in that survives Pydantic ``model_validate``.

    ``_enrich`` in api/routes/deploys.py validates the job against
    ``DeployJobResponse`` — the schema rejects MagicMock attribute proxies,
    so each field has to be a real value of the right type. SimpleNamespace
    gives us a plain object with concrete attributes.
    """
    from datetime import UTC, datetime

    return SimpleNamespace(
        id=job_id or uuid.uuid4(),
        agent_id=agent_id,
        agent_name=None,
        status="pending",
        target="local",
        error_message=None,
        started_at=datetime.now(tz=UTC),
        completed_at=None,
        agent=None,
    )


@contextmanager
def _override_db(get_side_effect: Any) -> Iterator[None]:
    """Override the ``get_db`` dependency with a mock session.

    ``get_side_effect`` is wired onto the session's ``.get()`` method so the
    test can return whatever ORM row each handler is looking up (Agent,
    DeployJob, …).
    """
    session = MagicMock()
    session.get = AsyncMock(side_effect=get_side_effect)

    async def _stub_db() -> Any:
        return session

    app.dependency_overrides[get_db] = _stub_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


def _patch_membership(user_id: str, team: str, role: str) -> list[Any]:
    """Patch TeamService so the user has ``role`` in ``team`` only.

    Returns a list of patches because the route's two-layer gate calls both
    ``get_user_teams`` (the global ``require_role("deployer")`` pre-check
    walks the user's teams) and ``get_user_role_in_team`` (the
    ``enforce_team_role`` refinement asks about one team specifically).
    """

    async def fake_role(uid: str, tid: str) -> str | None:
        if str(uid) == str(user_id) and tid == team:
            return role
        return None

    team_obj = MagicMock()
    team_obj.id = team
    team_obj.name = team

    async def fake_teams(uid: str) -> list[Any]:
        if str(uid) == str(user_id):
            return [team_obj]
        return []

    return [
        patch(
            "api.services.team_service.TeamService.get_user_role_in_team",
            new=AsyncMock(side_effect=fake_role),
        ),
        patch(
            "api.services.team_service.TeamService.get_user_teams",
            new=AsyncMock(side_effect=fake_teams),
        ),
    ]


def _patch_auth(user: MagicMock) -> list[Any]:
    return [
        patch("api.auth.decode_access_token", return_value={"sub": str(user.id)}),
        patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=user),
    ]


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def deployer_a() -> MagicMock:
    return _make_user(team="team-a", role=UserRole.deployer)


@pytest.fixture
def admin_user() -> MagicMock:
    return _make_user(team="team-a", role=UserRole.admin)


# ── POST /deploys with agent_id ────────────────────────────────────────────


class TestCreateDeployCrossTeam:
    def test_403_when_agent_team_differs_from_user_team(self, deployer_a: MagicMock) -> None:
        agent = _make_agent(team="team-b")
        stack = _patch_auth(deployer_a) + _patch_membership(
            str(deployer_a.id), team="team-a", role="deployer"
        )
        with _override_db(lambda model, pk: agent):
            for p in stack:
                p.start()
            try:
                resp = client.post(
                    "/api/v1/deploys",
                    json={"agent_id": str(agent.id), "target": "local"},
                    headers=_bearer(deployer_a),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 403, resp.text
        assert "team-b" in resp.text

    def test_200_when_agent_team_matches_user_team(self, deployer_a: MagicMock) -> None:
        agent = _make_agent(team="team-a")
        job = _make_job(agent_id=agent.id)
        job.target = "local"
        job.status = "pending"
        job.error_message = None
        job.started_at = "2026-05-20T00:00:00Z"
        job.completed_at = None
        job.agent = agent

        stack = (
            _patch_auth(deployer_a)
            + _patch_membership(str(deployer_a.id), team="team-a", role="deployer")
            + [
                patch(
                    "api.services.deploy_service.DeployService.create_deploy",
                    new_callable=AsyncMock,
                    return_value=job,
                ),
            ]
        )
        with _override_db(lambda model, pk: agent):
            for p in stack:
                p.start()
            try:
                resp = client.post(
                    "/api/v1/deploys",
                    json={"agent_id": str(agent.id), "target": "local"},
                    headers=_bearer(deployer_a),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 200, resp.text

    def test_404_when_agent_id_unknown(self, deployer_a: MagicMock) -> None:
        stack = _patch_auth(deployer_a) + _patch_membership(
            str(deployer_a.id), team="team-a", role="deployer"
        )
        with _override_db(lambda model, pk: None):
            for p in stack:
                p.start()
            try:
                resp = client.post(
                    "/api/v1/deploys",
                    json={"agent_id": str(uuid.uuid4()), "target": "local"},
                    headers=_bearer(deployer_a),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 404, resp.text


# ── POST /deploys with config_yaml ─────────────────────────────────────────


YAML_TEAM_B = """\
name: x
version: 1.0.0
team: team-b
owner: x@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""

YAML_NO_TEAM = """\
name: x
version: 1.0.0
owner: x@example.com
framework: langgraph
"""


class TestCreateDeployYamlCrossTeam:
    def test_403_when_yaml_team_differs(self, deployer_a: MagicMock) -> None:
        stack = _patch_auth(deployer_a) + _patch_membership(
            str(deployer_a.id), team="team-a", role="deployer"
        )
        with _override_db(lambda model, pk: None):
            for p in stack:
                p.start()
            try:
                resp = client.post(
                    "/api/v1/deploys",
                    json={"config_yaml": YAML_TEAM_B, "target": "local"},
                    headers=_bearer(deployer_a),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 403, resp.text

    def test_400_when_yaml_missing_team(self, deployer_a: MagicMock) -> None:
        stack = _patch_auth(deployer_a) + _patch_membership(
            str(deployer_a.id), team="team-a", role="deployer"
        )
        with _override_db(lambda model, pk: None):
            for p in stack:
                p.start()
            try:
                resp = client.post(
                    "/api/v1/deploys",
                    json={"config_yaml": YAML_NO_TEAM, "target": "local"},
                    headers=_bearer(deployer_a),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 400, resp.text


# ── Lifecycle: cancel + rollback ────────────────────────────────────────────


class TestLifecycleCrossTeam:
    def test_cancel_403_cross_team(self, deployer_a: MagicMock) -> None:
        agent = _make_agent(team="team-b")
        job = _make_job(agent_id=agent.id)

        def _get(model: Any, pk: Any) -> Any:
            return job if model.__name__ == "DeployJob" else agent

        stack = _patch_auth(deployer_a) + _patch_membership(
            str(deployer_a.id), team="team-a", role="deployer"
        )
        with _override_db(_get):
            for p in stack:
                p.start()
            try:
                resp = client.delete(
                    f"/api/v1/deploys/{job.id}",
                    headers=_bearer(deployer_a),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 403, resp.text

    def test_rollback_403_cross_team(self, deployer_a: MagicMock) -> None:
        agent = _make_agent(team="team-b")
        job = _make_job(agent_id=agent.id)

        def _get(model: Any, pk: Any) -> Any:
            return job if model.__name__ == "DeployJob" else agent

        stack = _patch_auth(deployer_a) + _patch_membership(
            str(deployer_a.id), team="team-a", role="deployer"
        )
        with _override_db(_get):
            for p in stack:
                p.start()
            try:
                resp = client.post(
                    f"/api/v1/deploys/{job.id}/rollback",
                    headers=_bearer(deployer_a),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 403, resp.text


# ── Platform admin escape hatch ─────────────────────────────────────────────


class TestPlatformAdminBypass:
    def test_admin_can_deploy_any_team(self, admin_user: MagicMock) -> None:
        agent = _make_agent(team="team-b")
        job = _make_job(agent_id=agent.id)
        job.target = "local"
        job.status = "pending"
        job.error_message = None
        job.started_at = "2026-05-20T00:00:00Z"
        job.completed_at = None
        job.agent = agent

        stack = _patch_auth(admin_user) + [
            patch(
                "api.services.deploy_service.DeployService.create_deploy",
                new_callable=AsyncMock,
                return_value=job,
            ),
        ]
        with _override_db(lambda model, pk: agent):
            for p in stack:
                p.start()
            try:
                resp = client.post(
                    "/api/v1/deploys",
                    json={"agent_id": str(agent.id), "target": "local"},
                    headers=_bearer(admin_user),
                )
            finally:
                for p in stack:
                    p.stop()
        assert resp.status_code == 200, resp.text
