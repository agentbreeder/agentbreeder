"""Extended tests for API routes: teams, templates, playground, gateway,
and additional agent endpoints.

Uses the same TestClient + mock pattern as test_api_routes.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from api.models.enums import (
    AgentStatus,
    TemplateCategory,
    TemplateStatus,
    UserRole,
)
from api.services.auth import create_access_token

client = TestClient(app)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ── Helpers ──────────────────────────────────────────────────────


def _auth_headers() -> dict[str, str]:
    """Return Authorization headers with a valid JWT."""
    token = create_access_token(str(uuid.uuid4()), "test@test.com", "viewer")
    return {"Authorization": f"Bearer {token}"}


def _make_mock_user(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "email": "test@test.com",
        "name": "Test User",
        "role": UserRole.viewer,
        "team": "engineering",
        "is_active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_agent(name: str = "test-agent", **kwargs):
    defaults = {
        "id": kwargs.pop("id", uuid.uuid4()),
        "name": name,
        "version": "1.0.0",
        "description": "A test agent",
        "team": "engineering",
        "owner": "test@example.com",
        "framework": "langgraph",
        "model_primary": "gpt-4o",
        "model_fallback": None,
        "endpoint_url": "http://localhost:8080",
        "status": AgentStatus.running,
        "tags": [],
        "config_snapshot": {},
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_template(**kwargs):
    defaults = {
        "id": kwargs.pop("id", uuid.uuid4()),
        "name": "support-template",
        "version": "1.0.0",
        "description": "A test template",
        "category": TemplateCategory.customer_support,
        "framework": "langgraph",
        "config_template": {"name": "{{agent_name}}"},
        "parameters": [
            {"name": "agent_name", "default": "my-agent"},
        ],
        "tags": ["test"],
        "author": "tester@example.com",
        "team": "engineering",
        "status": TemplateStatus.published,
        "readme": "",
        "use_count": 0,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ── Agent Additional Routes ──────────────────────────────────────


class TestValidateAgent:
    @patch(
        "api.routes.agents.validate_config_yaml",
    )
    def test_validate_valid_yaml(self, mock_val) -> None:
        result = MagicMock()
        result.valid = True
        result.errors = []
        result.warnings = []
        mock_val.return_value = result
        resp = client.post(
            "/api/v1/agents/validate",
            json={"yaml_content": "name: test\nversion: 1.0.0"},
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["valid"] is True
        assert body["errors"] == []

    @patch("api.routes.agents.validate_config_yaml")
    def test_validate_invalid_yaml(self, mock_val) -> None:
        err = MagicMock()
        err.path = "name"
        err.message = "name is required"
        err.suggestion = "Add a name field"
        result = MagicMock()
        result.valid = False
        result.errors = [err]
        result.warnings = []
        mock_val.return_value = result
        resp = client.post(
            "/api/v1/agents/validate",
            json={"yaml_content": "version: 1.0.0"},
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["valid"] is False
        assert len(body["errors"]) == 1
        assert body["errors"][0]["path"] == "name"


class TestCreateAgentFromYaml:
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.agents.create_from_yaml",
        new_callable=AsyncMock,
    )
    def test_create_from_yaml_success(self, mock_create, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_create.return_value = _make_agent("yaml-agent")
        resp = client.post(
            "/api/v1/agents/from-yaml",
            headers=_auth_headers(),
            json={"yaml_content": "name: yaml-agent\nversion: 1.0.0"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "yaml-agent"

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.agents.create_from_yaml",
        new_callable=AsyncMock,
    )
    def test_create_from_yaml_invalid(self, mock_create, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_create.side_effect = ValueError("Validation failed")
        resp = client.post(
            "/api/v1/agents/from-yaml",
            headers=_auth_headers(),
            json={"yaml_content": "bad"},
        )
        assert resp.status_code == 422


class TestCloneAgent:
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.agents.Agent")
    @patch(
        "api.routes.agents.AgentRegistry.get",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.agents.AgentRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_clone_success(
        self,
        mock_get_by_id,
        mock_get,
        mock_agent_cls,
        mock_get_user,
    ) -> None:
        from api.database import get_db

        mock_get_user.return_value = _make_mock_user()
        source = _make_agent("source-agent")
        mock_get_by_id.return_value = source
        mock_get.return_value = None
        cloned = _make_agent(
            "cloned-agent",
            version="2.0.0",
            status=AgentStatus.stopped,
            endpoint_url=None,
        )
        mock_agent_cls.return_value = cloned

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        async def _override_db():
            return mock_db

        app.dependency_overrides[get_db] = _override_db
        try:
            resp = client.post(
                f"/api/v1/agents/{source.id}/clone",
                headers=_auth_headers(),
                json={
                    "name": "cloned-agent",
                    "version": "2.0.0",
                },
            )
            assert resp.status_code == 201
            name = resp.json()["data"]["name"]
            assert name == "cloned-agent"
        finally:
            app.dependency_overrides.pop(get_db, None)

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.agents.AgentRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_clone_source_not_found(self, mock_get_by_id, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_get_by_id.return_value = None
        resp = client.post(
            f"/api/v1/agents/{uuid.uuid4()}/clone",
            headers=_auth_headers(),
            json={"name": "cloned", "version": "1.0.0"},
        )
        assert resp.status_code == 404

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.agents.AgentRegistry.get",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.agents.AgentRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_clone_name_conflict(self, mock_get_by_id, mock_get, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_get_by_id.return_value = _make_agent("src")
        mock_get.return_value = _make_agent("existing")
        resp = client.post(
            f"/api/v1/agents/{uuid.uuid4()}/clone",
            headers=_auth_headers(),
            json={"name": "existing", "version": "1.0.0"},
        )
        assert resp.status_code == 409


# ── Deploy Routes ────────────────────────────────────────────────


# ── Cost Routes ──────────────────────────────────────────────────


# ── Team Routes ──────────────────────────────────────────────────


class TestListTeams:
    @patch(
        "api.routes.teams.TeamService.get_member_count",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.teams.TeamService.list_teams",
        new_callable=AsyncMock,
    )
    def test_list_empty(self, mock_list, mock_count) -> None:
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/teams")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @patch(
        "api.routes.teams.TeamService.get_member_count",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.teams.TeamService.list_teams",
        new_callable=AsyncMock,
    )
    def test_list_returns_teams(self, mock_list, mock_count) -> None:
        team = MagicMock()
        team.id = "t1"
        team.name = "eng"
        team.display_name = "Engineering"
        team.description = "Eng team"
        team.created_at = _NOW
        mock_list.return_value = ([team], 1)
        mock_count.return_value = 3
        resp = client.get("/api/v1/teams")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["member_count"] == 3


class TestCreateTeam:
    @patch(
        "api.routes.teams.TeamService.create_team",
        new_callable=AsyncMock,
    )
    def test_create_success(self, mock_create) -> None:
        team = MagicMock()
        team.id = "t2"
        team.name = "platform"
        team.display_name = "Platform"
        team.description = "Platform team"
        team.created_at = _NOW
        mock_create.return_value = team
        resp = client.post(
            "/api/v1/teams",
            json={
                "name": "platform",
                "display_name": "Platform",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "platform"

    @patch(
        "api.routes.teams.TeamService.create_team",
        new_callable=AsyncMock,
    )
    def test_create_duplicate(self, mock_create) -> None:
        mock_create.side_effect = ValueError("exists")
        resp = client.post(
            "/api/v1/teams",
            json={
                "name": "eng",
                "display_name": "Engineering",
            },
        )
        assert resp.status_code == 409


class TestGetTeam:
    @patch(
        "api.routes.teams.TeamService.get_team_members",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.teams.TeamService.get_team",
        new_callable=AsyncMock,
    )
    def test_get_found(self, mock_get, mock_members) -> None:
        team = MagicMock()
        team.id = "t1"
        team.name = "eng"
        team.display_name = "Engineering"
        team.description = ""
        team.created_at = _NOW
        team.updated_at = _NOW
        mock_get.return_value = team
        mock_members.return_value = []
        resp = client.get("/api/v1/teams/t1")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "eng"

    @patch(
        "api.routes.teams.TeamService.get_team",
        new_callable=AsyncMock,
    )
    def test_get_not_found(self, mock_get) -> None:
        mock_get.return_value = None
        resp = client.get("/api/v1/teams/nonexistent")
        assert resp.status_code == 404


class TestUpdateTeam:
    @patch(
        "api.routes.teams.TeamService.get_member_count",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.teams.TeamService.update_team",
        new_callable=AsyncMock,
    )
    def test_update_success(self, mock_update, mock_count) -> None:
        team = MagicMock()
        team.id = "t1"
        team.name = "eng"
        team.display_name = "Updated Eng"
        team.description = "updated"
        team.created_at = _NOW
        mock_update.return_value = team
        mock_count.return_value = 5
        resp = client.put(
            "/api/v1/teams/t1",
            json={"display_name": "Updated Eng"},
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.teams.TeamService.update_team",
        new_callable=AsyncMock,
    )
    def test_update_not_found(self, mock_update) -> None:
        mock_update.return_value = None
        resp = client.put(
            "/api/v1/teams/nope",
            json={"display_name": "X"},
        )
        assert resp.status_code == 404


class TestDeleteTeam:
    @patch(
        "api.routes.teams.TeamService.delete_team",
        new_callable=AsyncMock,
    )
    def test_delete_success(self, mock_del) -> None:
        mock_del.return_value = True
        resp = client.delete("/api/v1/teams/t1")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    @patch(
        "api.routes.teams.TeamService.delete_team",
        new_callable=AsyncMock,
    )
    def test_delete_not_found(self, mock_del) -> None:
        mock_del.return_value = False
        resp = client.delete("/api/v1/teams/nope")
        assert resp.status_code == 404


class TestTeamMembers:
    @patch(
        "api.routes.teams.TeamService.add_member",
        new_callable=AsyncMock,
    )
    def test_add_member(self, mock_add) -> None:
        member = MagicMock()
        member.id = "m1"
        member.user_id = "u1"
        member.user_email = "a@b.com"
        member.user_name = "a"
        member.role = "viewer"
        member.joined_at = _NOW
        mock_add.return_value = member
        resp = client.post(
            "/api/v1/teams/t1/members",
            json={"user_email": "a@b.com", "role": "viewer"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["user_email"] == "a@b.com"

    @patch(
        "api.routes.teams.TeamService.add_member",
        new_callable=AsyncMock,
    )
    def test_add_member_team_not_found(self, mock_add) -> None:
        mock_add.side_effect = ValueError("Team not found")
        resp = client.post(
            "/api/v1/teams/nope/members",
            json={"user_email": "a@b.com", "role": "viewer"},
        )
        assert resp.status_code == 400

    @patch(
        "api.routes.teams.TeamService.remove_member",
        new_callable=AsyncMock,
    )
    def test_remove_member(self, mock_rm) -> None:
        mock_rm.return_value = True
        resp = client.delete("/api/v1/teams/t1/members/u1")
        assert resp.status_code == 200

    @patch(
        "api.routes.teams.TeamService.remove_member",
        new_callable=AsyncMock,
    )
    def test_remove_member_not_found(self, mock_rm) -> None:
        mock_rm.return_value = False
        resp = client.delete("/api/v1/teams/t1/members/u99")
        assert resp.status_code == 404


class TestTeamApiKeys:
    @patch(
        "api.routes.teams.TeamService.list_api_keys",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.teams.TeamService.get_team",
        new_callable=AsyncMock,
    )
    def test_list_keys(self, mock_team, mock_keys) -> None:
        team = MagicMock()
        team.id = "t1"
        mock_team.return_value = team
        key = MagicMock()
        key.id = "k1"
        key.provider = "openai"
        key.key_hint = "...abcd"
        key.created_by = "admin@x.com"
        key.created_at = _NOW
        mock_keys.return_value = [key]
        resp = client.get("/api/v1/teams/t1/api-keys")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch(
        "api.routes.teams.TeamService.get_team",
        new_callable=AsyncMock,
    )
    def test_list_keys_team_not_found(self, mock_team) -> None:
        mock_team.return_value = None
        resp = client.get("/api/v1/teams/nope/api-keys")
        assert resp.status_code == 404

    @patch(
        "api.routes.teams.TeamService.set_api_key",
        new_callable=AsyncMock,
    )
    def test_set_api_key(self, mock_set) -> None:
        key = MagicMock()
        key.id = "k2"
        key.provider = "anthropic"
        key.key_hint = "...wxyz"
        key.created_by = "admin@x.com"
        key.created_at = _NOW
        mock_set.return_value = key
        resp = client.post(
            "/api/v1/teams/t1/api-keys",
            json={
                "provider": "anthropic",
                "api_key": "sk-ant-wxyz",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.teams.TeamService.delete_api_key",
        new_callable=AsyncMock,
    )
    def test_delete_api_key(self, mock_del) -> None:
        mock_del.return_value = True
        resp = client.delete("/api/v1/teams/t1/api-keys/k1")
        assert resp.status_code == 200

    @patch(
        "api.routes.teams.TeamService.delete_api_key",
        new_callable=AsyncMock,
    )
    def test_delete_api_key_not_found(self, mock_del) -> None:
        mock_del.return_value = False
        resp = client.delete("/api/v1/teams/t1/api-keys/k99")
        assert resp.status_code == 404

    @patch(
        "api.routes.teams.TeamService.test_api_key",
        new_callable=AsyncMock,
    )
    def test_test_api_key(self, mock_test) -> None:
        mock_test.return_value = {
            "success": True,
            "provider": "openai",
        }
        resp = client.post("/api/v1/teams/t1/api-keys/k1/test")
        assert resp.status_code == 200
        assert resp.json()["data"]["success"] is True


# ── Template Routes ──────────────────────────────────────────────


class TestListTemplates:
    @patch(
        "api.routes.templates.TemplateRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_empty(self, mock_list) -> None:
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/templates")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @patch(
        "api.routes.templates.TemplateRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_with_filters(self, mock_list) -> None:
        mock_list.return_value = ([], 0)
        client.get(
            "/api/v1/templates",
            params={
                "category": "customer_support",
                "framework": "langgraph",
            },
        )
        kw = mock_list.call_args[1]
        assert kw["category"] == TemplateCategory.customer_support
        assert kw["framework"] == "langgraph"

    @patch(
        "api.routes.templates.TemplateRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_pagination(self, mock_list) -> None:
        mock_list.return_value = ([_make_template()], 10)
        resp = client.get(
            "/api/v1/templates",
            params={"page": 2, "per_page": 5},
        )
        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["total"] == 10
        assert meta["page"] == 2


class TestCreateTemplate:
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.templates.TemplateRegistry.create",
        new_callable=AsyncMock,
    )
    def test_create_success(self, mock_create, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_create.return_value = _make_template()
        resp = client.post(
            "/api/v1/templates",
            headers=_auth_headers(),
            json={
                "name": "support-template",
                "framework": "langgraph",
                "config_template": {"name": "{{agent_name}}"},
                "author": "tester@example.com",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "support-template"


class TestGetTemplate:
    @patch(
        "api.routes.templates.TemplateRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_found(self, mock_get) -> None:
        tmpl = _make_template()
        mock_get.return_value = tmpl
        resp = client.get(f"/api/v1/templates/{tmpl.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "support-template"

    @patch(
        "api.routes.templates.TemplateRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_not_found(self, mock_get) -> None:
        mock_get.return_value = None
        resp = client.get(f"/api/v1/templates/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestUpdateTemplate:
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.templates.TemplateRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update_success(self, mock_update, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_update.return_value = _make_template(description="Updated")
        tid = uuid.uuid4()
        resp = client.put(
            f"/api/v1/templates/{tid}",
            headers=_auth_headers(),
            json={"description": "Updated"},
        )
        assert resp.status_code == 200

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.templates.TemplateRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update_not_found(self, mock_update, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_update.return_value = None
        resp = client.put(
            f"/api/v1/templates/{uuid.uuid4()}",
            headers=_auth_headers(),
            json={"description": "X"},
        )
        assert resp.status_code == 404


class TestDeleteTemplate:
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.templates.TemplateRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete_success(self, mock_del, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_del.return_value = True
        resp = client.delete(
            f"/api/v1/templates/{uuid.uuid4()}",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch(
        "api.routes.templates.TemplateRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete_not_found(self, mock_del, mock_get_user) -> None:
        mock_get_user.return_value = _make_mock_user()
        mock_del.return_value = False
        resp = client.delete(
            f"/api/v1/templates/{uuid.uuid4()}",
            headers=_auth_headers(),
        )
        assert resp.status_code == 404


class TestInstantiateTemplate:
    @patch(
        "api.routes.templates.TemplateRegistry.increment_use_count",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.templates.TemplateRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_instantiate_success(self, mock_get, mock_inc) -> None:
        tmpl = _make_template()
        mock_get.return_value = tmpl
        resp = client.post(
            f"/api/v1/templates/{tmpl.id}/instantiate",
            json={"values": {"agent_name": "my-bot"}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "yaml_content" in data
        assert "agent_name" in data

    @patch(
        "api.routes.templates.TemplateRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_instantiate_not_found(self, mock_get) -> None:
        mock_get.return_value = None
        resp = client.post(
            f"/api/v1/templates/{uuid.uuid4()}/instantiate",
            json={"values": {}},
        )
        assert resp.status_code == 404


# ── Orchestration Routes ─────────────────────────────────────────


# ── Playground Routes ────────────────────────────────────────────


class TestPlaygroundChat:
    def test_chat_returns_response(self) -> None:
        resp = client.post(
            "/api/v1/playground/chat",
            json={
                "agent_id": "agent-1",
                "message": "Hello!",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "response" in data
        assert "token_count" in data
        assert "cost_estimate" in data
        assert "model_used" in data
        assert "conversation_id" in data

    def test_chat_with_model_override(self) -> None:
        resp = client.post(
            "/api/v1/playground/chat",
            json={
                "agent_id": "agent-1",
                "message": "Hi",
                "model_override": "gpt-4o-mini",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["model_used"] == "gpt-4o-mini"

    def test_chat_with_system_prompt(self) -> None:
        resp = client.post(
            "/api/v1/playground/chat",
            json={
                "agent_id": "agent-1",
                "message": "Hi",
                "system_prompt_override": "Be concise.",
            },
        )
        assert resp.status_code == 200

    def test_chat_with_history(self) -> None:
        resp = client.post(
            "/api/v1/playground/chat",
            json={
                "agent_id": "agent-1",
                "message": "Follow up",
                "conversation_history": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi!"},
                ],
            },
        )
        assert resp.status_code == 200


# ── Gateway Routes ───────────────────────────────────────────────


class TestGatewayStatus:
    def test_status(self) -> None:
        resp = client.get("/api/v1/gateway/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 1
        tiers = {t["tier"] for t in data}
        assert "litellm" in tiers


_LITELLM_MODELS = {
    "data": [
        {"id": "gpt-4o", "owned_by": "openai"},
        {"id": "claude-sonnet-4-6", "owned_by": "anthropic"},
        {"id": "gemini-2.0-flash", "owned_by": "google"},
    ]
}


class TestGatewayModels:
    @patch("api.routes.gateway._fetch_litellm", new_callable=AsyncMock)
    def test_list_all_models(self, mock_fetch) -> None:
        mock_fetch.return_value = _LITELLM_MODELS
        resp = client.get("/api/v1/gateway/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [m["id"] for m in data] == ["gpt-4o", "claude-sonnet-4-6", "gemini-2.0-flash"]
        # Prices/context are not reported by LiteLLM /models — never invented.
        assert all(m["input_price_per_million"] is None for m in data)

    @patch("api.routes.gateway._fetch_litellm", new_callable=AsyncMock)
    def test_unreachable_litellm_returns_empty(self, mock_fetch) -> None:
        mock_fetch.return_value = {"data": []}
        resp = client.get("/api/v1/gateway/models")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @patch("api.routes.gateway._fetch_litellm", new_callable=AsyncMock)
    def test_filter_by_tier(self, mock_fetch) -> None:
        mock_fetch.return_value = _LITELLM_MODELS
        resp = client.get(
            "/api/v1/gateway/models",
            params={"tier": "litellm"},
        )
        assert resp.status_code == 200
        for m in resp.json()["data"]:
            assert m["gateway_tier"] == "litellm"

    @patch("api.routes.gateway._fetch_litellm", new_callable=AsyncMock)
    def test_filter_by_provider(self, mock_fetch) -> None:
        mock_fetch.return_value = _LITELLM_MODELS
        resp = client.get(
            "/api/v1/gateway/models",
            params={"provider": "anthropic"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [m["id"] for m in data] == ["claude-sonnet-4-6"]

    @patch("api.routes.gateway._fetch_litellm", new_callable=AsyncMock)
    def test_pagination(self, mock_fetch) -> None:
        mock_fetch.return_value = _LITELLM_MODELS
        resp = client.get(
            "/api/v1/gateway/models",
            params={"page": 1, "per_page": 2},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2
        assert resp.json()["meta"]["total"] == 3


class TestGatewayProviders:
    @patch("api.routes.gateway._fetch_litellm", new_callable=AsyncMock)
    def test_list_providers(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "healthy_endpoints": [{"model": "anthropic/claude-sonnet-4-6"}],
            "unhealthy_endpoints": [{"model": "openai/gpt-4o"}],
        }
        resp = client.get("/api/v1/gateway/providers")
        assert resp.status_code == 200
        data = {p["id"]: p["status"] for p in resp.json()["data"]}
        assert data == {"anthropic": "healthy", "openai": "unhealthy"}


def _fake_spend_logs() -> list[dict]:
    """Return a list of already-normalized LogEntry dicts for tests.

    The route uses ``fetch_spend_logs`` directly (it both calls LiteLLM and
    normalizes the response), so when we patch it we return the post-
    normalization shape. ``test_normalize_entry`` covers raw-row parsing.
    """
    return [
        {
            "id": "abc-123",
            "timestamp": "2026-04-29T10:00:00+00:00",
            "agent": "support-agent",
            "model": "gpt-4o",
            "provider": "openai",
            "gateway_tier": "direct",
            "input_tokens": 500,
            "output_tokens": 100,
            "latency_ms": 1000,
            "cost_usd": 0.0035,
            "status": "success",
        },
        {
            "id": "def-456",
            "timestamp": "2026-04-29T10:00:02+00:00",
            "agent": "review-agent",
            "model": "anthropic/claude-sonnet-4-6",
            "provider": "anthropic",
            "gateway_tier": "litellm",
            "input_tokens": 1000,
            "output_tokens": 250,
            "latency_ms": 2000,
            "cost_usd": 0.0075,
            "status": "success",
        },
    ]


class TestGatewayLogs:
    """Verify /api/v1/gateway/logs queries LiteLLM /spend/logs (no synthetic data)."""

    @patch("api.routes.gateway.fetch_spend_logs", new_callable=AsyncMock)
    def test_logs_default(self, mock_fetch) -> None:
        mock_fetch.return_value = _fake_spend_logs()
        resp = client.get("/api/v1/gateway/logs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) <= 20
        # Real LiteLLM rows were normalized into LogEntry dicts.
        assert {"id", "model", "provider", "gateway_tier", "cost_usd"} <= set(data[0].keys())
        mock_fetch.assert_awaited()

    @patch("api.routes.gateway.fetch_spend_logs", new_callable=AsyncMock)
    def test_logs_pagination(self, mock_fetch) -> None:
        mock_fetch.return_value = _fake_spend_logs()
        resp = client.get(
            "/api/v1/gateway/logs",
            params={"page": 1, "per_page": 1},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch("api.routes.gateway.fetch_spend_logs", new_callable=AsyncMock)
    def test_logs_filter_by_model(self, mock_fetch) -> None:
        mock_fetch.return_value = _fake_spend_logs()
        resp = client.get(
            "/api/v1/gateway/logs",
            params={"model": "gpt-4o"},
        )
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) == 1
        assert rows[0]["model"] == "gpt-4o"

    @patch("api.routes.gateway.fetch_spend_logs", new_callable=AsyncMock)
    def test_logs_returns_503_when_litellm_unreachable(self, mock_fetch) -> None:
        from api.services.gateway_logs_service import GatewayLogsUnavailableError

        mock_fetch.side_effect = GatewayLogsUnavailableError(
            "LiteLLM proxy unreachable: connection refused"
        )
        resp = client.get("/api/v1/gateway/logs")
        # 503 with empty data + error message — never synthetic logs.
        assert resp.status_code == 503
        body = resp.json()
        assert body["data"] == []
        assert any("LiteLLM" in e for e in body["errors"])
