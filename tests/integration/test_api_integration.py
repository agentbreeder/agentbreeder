"""Integration tests for the FastAPI application.

Tests API endpoint flows with real route/service logic and mocked database/auth.
Uses TestClient against the FastAPI app for realistic request handling.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from api.models.enums import (
    AgentStatus,
    DeployJobStatus,
    TemplateCategory,
    TemplateStatus,
    UserRole,
)
from api.services.auth import create_access_token

client = TestClient(app)

_NOW = datetime(2026, 3, 14, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    """Return Authorization headers with a valid JWT for tests."""
    token = create_access_token(str(uuid.uuid4()), "test@example.com", "admin")
    return {"Authorization": f"Bearer {token}"}


def _make_mock_user(**kwargs) -> MagicMock:
    """Create a mock User object for auth dependency."""
    defaults = {
        "id": uuid.uuid4(),
        "email": "test@example.com",
        "name": "Test User",
        "role": UserRole.admin,
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


def _make_agent_mock(name: str = "test-agent", **kwargs) -> MagicMock:
    """Create a mock Agent-like object."""
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


def _make_deploy_job_mock(
    agent_name: str = "test-agent", status: DeployJobStatus = DeployJobStatus.pending
) -> MagicMock:
    """Create a mock DeployJob-like object."""
    job = MagicMock()
    job.id = uuid.uuid4()
    job.agent_id = uuid.uuid4()
    job.status = status
    job.target = "local"
    job.error_message = None
    job.agent_name = None
    job.started_at = _NOW
    job.completed_at = None
    job.logs = {}
    agent = MagicMock()
    agent.name = agent_name
    job.agent = agent
    return job


def _make_template_mock(name: str = "support-template", **kwargs) -> MagicMock:
    """Create a mock Template-like object."""
    defaults = {
        "id": kwargs.pop("id", uuid.uuid4()),
        "name": name,
        "version": "1.0.0",
        "description": "A support agent template",
        "category": TemplateCategory.customer_support,
        "framework": "langgraph",
        "config_template": {
            "name": "{{agent_name}}",
            "version": "1.0.0",
            "framework": "langgraph",
            "model": {"primary": "gpt-4o"},
        },
        "parameters": [
            {"name": "agent_name", "label": "Agent Name", "type": "string", "required": True}
        ],
        "tags": ["support"],
        "author": "test@example.com",
        "team": "default",
        "status": TemplateStatus.draft,
        "use_count": 0,
        "readme": "# Support Template",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ===========================================================================
# 1. Agent CRUD: create -> list -> get -> update -> delete
# ===========================================================================


class TestAgentCRUDFlow:
    """Tests the full agent CRUD lifecycle through the API."""

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.agents.AgentRegistry.register", new_callable=AsyncMock)
    def test_create_agent(self, mock_register: AsyncMock, mock_user: AsyncMock) -> None:
        """POST /api/v1/agents should create a new agent."""
        agent_id = uuid.uuid4()
        mock_user.return_value = _make_mock_user()
        mock_register.return_value = _make_agent_mock("my-agent", id=agent_id)

        resp = client.post(
            "/api/v1/agents",
            json={
                "name": "my-agent",
                "version": "1.0.0",
                "team": "engineering",
                "owner": "dev@example.com",
                "framework": "langgraph",
                "model_primary": "gpt-4o",
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == "my-agent"
        assert data["framework"] == "langgraph"

    @patch("api.routes.agents.AgentRegistry.list", new_callable=AsyncMock)
    def test_list_agents(self, mock_list: AsyncMock) -> None:
        """GET /api/v1/agents should return a paginated list."""
        agents = [_make_agent_mock(f"agent-{i}") for i in range(3)]
        mock_list.return_value = (agents, 3)

        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 3
        assert data["meta"]["total"] == 3

    @patch("api.routes.agents.AgentRegistry.list", new_callable=AsyncMock)
    def test_list_agents_with_filters(self, mock_list: AsyncMock) -> None:
        """GET /api/v1/agents?team=...&framework=... should pass filters."""
        mock_list.return_value = ([], 0)

        resp = client.get("/api/v1/agents?team=engineering&framework=langgraph&status=running")
        assert resp.status_code == 200
        call_kwargs = mock_list.call_args
        assert call_kwargs[1]["team"] == "engineering"
        assert call_kwargs[1]["framework"] == "langgraph"

    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_get_agent_by_id(self, mock_get: AsyncMock) -> None:
        """GET /api/v1/agents/{id} should return agent details."""
        agent_id = uuid.uuid4()
        mock_get.return_value = _make_agent_mock("my-agent", id=agent_id)

        resp = client.get(f"/api/v1/agents/{agent_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "my-agent"

    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_get_agent_not_found(self, mock_get: AsyncMock) -> None:
        """GET /api/v1/agents/{id} should return 404 for unknown agents."""
        mock_get.return_value = None
        resp = client.get(f"/api/v1/agents/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_update_agent(self, mock_get: AsyncMock, mock_user: AsyncMock) -> None:
        """PUT /api/v1/agents/{id} should update agent fields."""
        agent_id = uuid.uuid4()
        agent = _make_agent_mock("my-agent", id=agent_id)
        mock_get.return_value = agent
        mock_user.return_value = _make_mock_user()

        resp = client.put(
            f"/api/v1/agents/{agent_id}",
            json={"version": "2.0.0", "description": "Updated description"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_delete_agent(self, mock_get: AsyncMock, mock_user: AsyncMock) -> None:
        """DELETE /api/v1/agents/{id} should soft-delete (archive) the agent."""
        agent_id = uuid.uuid4()
        agent = _make_agent_mock("my-agent", id=agent_id)
        mock_get.return_value = agent
        mock_user.return_value = _make_mock_user()

        resp = client.delete(f"/api/v1/agents/{agent_id}", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "archived" in data["message"].lower() or "my-agent" in data["message"]

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_delete_nonexistent_agent(self, mock_get: AsyncMock, mock_user: AsyncMock) -> None:
        """DELETE /api/v1/agents/{id} should return 404 for unknown agents."""
        mock_get.return_value = None
        mock_user.return_value = _make_mock_user()

        resp = client.delete(f"/api/v1/agents/{uuid.uuid4()}", headers=_auth_headers())
        assert resp.status_code == 404

    def test_create_agent_unauthorized(self) -> None:
        """POST /api/v1/agents without auth should return 401."""
        resp = client.post(
            "/api/v1/agents",
            json={
                "name": "no-auth-agent",
                "version": "1.0.0",
                "team": "engineering",
                "owner": "dev@example.com",
                "framework": "langgraph",
                "model_primary": "gpt-4o",
            },
        )
        assert resp.status_code == 401


class TestAgentSearchAndValidation:
    """Tests for agent search and YAML validation endpoints."""

    @patch("api.routes.agents.AgentRegistry.search", new_callable=AsyncMock)
    def test_search_agents(self, mock_search: AsyncMock) -> None:
        """GET /api/v1/agents/search?q=... should search by query."""
        mock_search.return_value = ([_make_agent_mock("found-agent")], 1)

        resp = client.get("/api/v1/agents/search?q=found")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1
        assert resp.json()["data"][0]["name"] == "found-agent"

    def test_validate_yaml_valid(self) -> None:
        """POST /api/v1/agents/validate should accept valid YAML."""
        valid_yaml = """\
name: valid-agent
version: 1.0.0
team: engineering
owner: dev@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        resp = client.post(
            "/api/v1/agents/validate",
            json={"yaml_content": valid_yaml},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is True
        assert len(data["errors"]) == 0

    def test_validate_yaml_invalid(self) -> None:
        """POST /api/v1/agents/validate should reject invalid YAML."""
        resp = client.post(
            "/api/v1/agents/validate",
            json={"yaml_content": "name: bad\nversion: not-semver"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_yaml_empty(self) -> None:
        """POST /api/v1/agents/validate should reject empty YAML."""
        resp = client.post(
            "/api/v1/agents/validate",
            json={"yaml_content": ""},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is False

    def test_validate_yaml_warnings(self) -> None:
        """Validation should return warnings for missing optional fields."""
        yaml_content = """\
name: warn-agent
version: 1.0.0
team: engineering
owner: dev@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""
        resp = client.post(
            "/api/v1/agents/validate",
            json={"yaml_content": yaml_content},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Should get warnings for missing tools, prompts, guardrails, description
        assert len(data["warnings"]) > 0


# ===========================================================================
# 2. Cost event recording -> summary retrieval
# ===========================================================================


class TestCostEventFlow:
    """Tests for the cost tracking API."""

    def test_record_cost_event(self) -> None:
        """POST /api/v1/costs/events should record a cost event."""
        resp = client.post(
            "/api/v1/costs/events",
            json={
                "agent_name": "support-agent",
                "team": "customer-success",
                "model_name": "gpt-4o",
                "provider": "openai",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cost_usd": 0.015,
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["agent_name"] == "support-agent"
        assert data["input_tokens"] == 1000

    def test_record_cost_event_missing_fields(self) -> None:
        """POST /api/v1/costs/events should reject incomplete events."""
        resp = client.post(
            "/api/v1/costs/events",
            json={"agent_name": "test"},
        )
        assert resp.status_code == 400

    def test_record_and_retrieve_summary(self) -> None:
        """Recording events then getting summary should reflect the recorded data."""
        unique_team = f"cost-team-{uuid.uuid4().hex[:8]}"
        for i in range(3):
            client.post(
                "/api/v1/costs/events",
                json={
                    "agent_name": f"cost-agent-{i}",
                    "team": unique_team,
                    "model_name": "gpt-4o",
                    "provider": "openai",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cost_usd": 0.01,
                },
            )

        resp = client.get(f"/api/v1/costs/summary?team={unique_team}")
        assert resp.status_code == 200

    def test_get_cost_breakdown(self) -> None:
        """GET /api/v1/costs/breakdown should return costs grouped by agent."""
        resp = client.get("/api/v1/costs/breakdown?group_by=agent")
        assert resp.status_code == 200

    def test_get_cost_trend(self) -> None:
        """GET /api/v1/costs/trend should return daily cost data."""
        resp = client.get("/api/v1/costs/trend?days=7")
        assert resp.status_code == 200

    def test_get_top_spenders(self) -> None:
        """GET /api/v1/costs/top-spenders should return top agents by cost."""
        resp = client.get("/api/v1/costs/top-spenders?limit=5")
        assert resp.status_code == 200

    def test_compare_models(self) -> None:
        """POST /api/v1/costs/compare should compare two models."""
        resp = client.post(
            "/api/v1/costs/compare",
            json={"model_a": "gpt-4o", "model_b": "claude-sonnet-4.6"},
        )
        assert resp.status_code == 200

    def test_compare_models_missing_fields(self) -> None:
        """POST /api/v1/costs/compare should reject incomplete requests."""
        resp = client.post("/api/v1/costs/compare", json={"model_a": "gpt-4o"})
        assert resp.status_code == 400


# ===========================================================================
# 3. Team creation -> member management
# ===========================================================================


class TestTeamManagementFlow:
    """Tests for team CRUD and member management."""

    def test_create_team(self) -> None:
        """POST /api/v1/teams should create a new team."""
        resp = client.post(
            "/api/v1/teams",
            json={
                "name": f"test-team-{uuid.uuid4().hex[:8]}",
                "display_name": "Test Team",
                "description": "A test team",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["display_name"] == "Test Team"
        assert data["member_count"] == 0

    def test_list_teams(self) -> None:
        """GET /api/v1/teams should list all teams."""
        resp = client.get("/api/v1/teams")
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_get_team_detail(self) -> None:
        """GET /api/v1/teams/{id} should return team with members."""
        team_name = f"detail-team-{uuid.uuid4().hex[:8]}"
        create_resp = client.post(
            "/api/v1/teams",
            json={
                "name": team_name,
                "display_name": "Detail Team",
                "description": "For detail test",
            },
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["id"]

        resp = client.get(f"/api/v1/teams/{team_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["display_name"] == "Detail Team"
        assert "members" in data

    def test_add_member_to_team(self) -> None:
        """POST /api/v1/teams/{id}/members should add a member."""
        team_name = f"member-team-{uuid.uuid4().hex[:8]}"
        create_resp = client.post(
            "/api/v1/teams",
            json={"name": team_name, "display_name": "Member Team"},
        )
        team_id = create_resp.json()["data"]["id"]

        resp = client.post(
            f"/api/v1/teams/{team_id}/members",
            json={
                "user_email": "alice@example.com",
                "role": "deployer",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["user_email"] == "alice@example.com"
        assert data["role"] == "deployer"

    def test_update_team(self) -> None:
        """PUT /api/v1/teams/{id} should update team metadata."""
        team_name = f"update-team-{uuid.uuid4().hex[:8]}"
        create_resp = client.post(
            "/api/v1/teams",
            json={"name": team_name, "display_name": "Before Update"},
        )
        team_id = create_resp.json()["data"]["id"]

        resp = client.put(
            f"/api/v1/teams/{team_id}",
            json={"display_name": "After Update", "description": "Updated desc"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["display_name"] == "After Update"

    def test_delete_team(self) -> None:
        """DELETE /api/v1/teams/{id} should delete the team."""
        team_name = f"delete-team-{uuid.uuid4().hex[:8]}"
        create_resp = client.post(
            "/api/v1/teams",
            json={"name": team_name, "display_name": "Delete Me"},
        )
        team_id = create_resp.json()["data"]["id"]

        resp = client.delete(f"/api/v1/teams/{team_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    def test_get_nonexistent_team(self) -> None:
        """GET /api/v1/teams/{id} should return 404 for unknown teams."""
        resp = client.get(f"/api/v1/teams/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_team_lifecycle_create_add_member_remove_member(self) -> None:
        """Full lifecycle: create team, add member, remove member."""
        team_name = f"lifecycle-team-{uuid.uuid4().hex[:8]}"
        create_resp = client.post(
            "/api/v1/teams",
            json={"name": team_name, "display_name": "Lifecycle Team"},
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["id"]

        # Add member
        add_resp = client.post(
            f"/api/v1/teams/{team_id}/members",
            json={"user_email": "bob@example.com", "role": "viewer"},
        )
        assert add_resp.status_code == 201
        user_id = add_resp.json()["data"]["user_id"]

        # Remove member
        remove_resp = client.delete(f"/api/v1/teams/{team_id}/members/{user_id}")
        assert remove_resp.status_code == 200
        assert remove_resp.json()["data"]["removed"] is True


# ===========================================================================
# 4. Template CRUD flow
# ===========================================================================


class TestTemplateCRUDFlow:
    """Tests for the template management API."""

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.templates.TemplateRegistry.create", new_callable=AsyncMock)
    def test_create_template(
        self, mock_create: AsyncMock, mock_user: AsyncMock
    ) -> None:
        """POST /api/v1/templates should create a new template."""
        template_id = uuid.uuid4()
        mock_user.return_value = _make_mock_user()
        mock_create.return_value = _make_template_mock("new-template", id=template_id)

        resp = client.post(
            "/api/v1/templates",
            json={
                "name": "new-template",
                "framework": "langgraph",
                "config_template": {"name": "{{agent_name}}", "framework": "langgraph"},
                "author": "test@example.com",
                "parameters": [
                    {
                        "name": "agent_name",
                        "label": "Agent Name",
                        "type": "string",
                        "required": True,
                    }
                ],
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == "new-template"

    @patch("api.routes.templates.TemplateRegistry.list", new_callable=AsyncMock)
    def test_list_templates(self, mock_list: AsyncMock) -> None:
        """GET /api/v1/templates should return a paginated list."""
        templates = [_make_template_mock(f"template-{i}") for i in range(2)]
        mock_list.return_value = (templates, 2)

        resp = client.get("/api/v1/templates")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    @patch("api.routes.templates.TemplateRegistry.list", new_callable=AsyncMock)
    def test_list_templates_with_category_filter(self, mock_list: AsyncMock) -> None:
        """GET /api/v1/templates?category=... should filter by category."""
        mock_list.return_value = ([], 0)

        resp = client.get("/api/v1/templates?category=customer_support")
        assert resp.status_code == 200

    @patch("api.routes.templates.TemplateRegistry.get_by_id", new_callable=AsyncMock)
    def test_get_template(self, mock_get: AsyncMock) -> None:
        """GET /api/v1/templates/{id} should return template details."""
        template_id = uuid.uuid4()
        mock_get.return_value = _make_template_mock("my-template", id=template_id)

        resp = client.get(f"/api/v1/templates/{template_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "my-template"

    @patch("api.routes.templates.TemplateRegistry.get_by_id", new_callable=AsyncMock)
    def test_get_template_not_found(self, mock_get: AsyncMock) -> None:
        """GET /api/v1/templates/{id} should return 404 for unknown templates."""
        mock_get.return_value = None
        resp = client.get(f"/api/v1/templates/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.templates.TemplateRegistry.update", new_callable=AsyncMock)
    def test_update_template(self, mock_update: AsyncMock, mock_user: AsyncMock) -> None:
        """PUT /api/v1/templates/{id} should update template fields."""
        template_id = uuid.uuid4()
        mock_user.return_value = _make_mock_user()
        mock_update.return_value = _make_template_mock(
            "updated-template", id=template_id, description="Updated"
        )

        resp = client.put(
            f"/api/v1/templates/{template_id}",
            json={"description": "Updated"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.templates.TemplateRegistry.delete", new_callable=AsyncMock)
    def test_delete_template(self, mock_delete: AsyncMock, mock_user: AsyncMock) -> None:
        """DELETE /api/v1/templates/{id} should delete the template."""
        mock_user.return_value = _make_mock_user()
        mock_delete.return_value = True

        resp = client.delete(f"/api/v1/templates/{uuid.uuid4()}", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    @patch("api.routes.templates.TemplateRegistry.delete", new_callable=AsyncMock)
    def test_delete_nonexistent_template(
        self, mock_delete: AsyncMock, mock_user: AsyncMock
    ) -> None:
        """DELETE /api/v1/templates/{id} should return 404 for unknown templates."""
        mock_user.return_value = _make_mock_user()
        mock_delete.return_value = False

        resp = client.delete(f"/api/v1/templates/{uuid.uuid4()}", headers=_auth_headers())
        assert resp.status_code == 404


# ===========================================================================
# Deploy API endpoint tests
# ===========================================================================


class TestDeployAPIFlow:
    """Tests for the deploy API endpoints."""

    @patch("api.routes.deploys.DeployRegistry.list", new_callable=AsyncMock)
    def test_list_deploys(self, mock_list: AsyncMock) -> None:
        """GET /api/v1/deploys should return deploy jobs."""
        jobs = [_make_deploy_job_mock()]
        mock_list.return_value = (jobs, 1)

        resp = client.get("/api/v1/deploys")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch("api.routes.deploys.DeployService.get_deploy_status", new_callable=AsyncMock)
    def test_get_deploy_status(self, mock_status: AsyncMock) -> None:
        """GET /api/v1/deploys/{id} should return job details with logs."""
        job_id = uuid.uuid4()
        mock_status.return_value = {
            "id": str(job_id),
            "agent_id": str(uuid.uuid4()),
            "agent_name": "test-agent",
            "status": "building",
            "target": "local",
            "error_message": None,
            "started_at": _NOW.isoformat(),
            "completed_at": None,
            "logs": [
                {
                    "timestamp": _NOW.isoformat(),
                    "level": "info",
                    "message": "Building container...",
                    "step": "building",
                }
            ],
        }

        resp = client.get(f"/api/v1/deploys/{job_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "building"
        assert len(data["logs"]) == 1

    @patch("api.routes.deploys.DeployService.get_deploy_status", new_callable=AsyncMock)
    def test_get_deploy_not_found(self, mock_status: AsyncMock) -> None:
        """GET /api/v1/deploys/{id} should return 404 for unknown jobs."""
        mock_status.return_value = None
        resp = client.get(f"/api/v1/deploys/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch("api.routes.deploys.DeployService.cancel_deploy", new_callable=AsyncMock)
    def test_cancel_deploy(self, mock_cancel: AsyncMock) -> None:
        """DELETE /api/v1/deploys/{id} should cancel the deployment."""
        job_id = uuid.uuid4()
        mock_cancel.return_value = True

        resp = client.delete(f"/api/v1/deploys/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["cancelled"] is True

    @patch("api.routes.deploys.DeployService.rollback_deploy", new_callable=AsyncMock)
    def test_rollback_deploy(self, mock_rollback: AsyncMock) -> None:
        """POST /api/v1/deploys/{id}/rollback should rollback a failed deploy."""
        job_id = uuid.uuid4()
        mock_rollback.return_value = True

        resp = client.post(f"/api/v1/deploys/{job_id}/rollback")
        assert resp.status_code == 200
        assert resp.json()["data"]["rolled_back"] is True

    @patch("api.routes.deploys.DeployService.rollback_deploy", new_callable=AsyncMock)
    def test_rollback_non_failed_deploy(self, mock_rollback: AsyncMock) -> None:
        """POST /api/v1/deploys/{id}/rollback should reject non-failed jobs."""
        mock_rollback.return_value = False
        resp = client.post(f"/api/v1/deploys/{uuid.uuid4()}/rollback")
        assert resp.status_code == 400


# ===========================================================================
# Health check and cross-cutting concerns
# ===========================================================================


class TestHealthAndMeta:
    """Tests for health check and API metadata."""

    def test_health_endpoint(self) -> None:
        """GET /health should return healthy status."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "agent-garden-api"

    def test_unknown_route_returns_404(self) -> None:
        """Requests to unknown routes should return 404/405."""
        resp = client.get("/api/v1/nonexistent")
        assert resp.status_code in (404, 405)

    @patch("api.routes.agents.AgentRegistry.list", new_callable=AsyncMock)
    def test_agents_endpoint_accepts_pagination(self, mock_list: AsyncMock) -> None:
        """Pagination parameters should be accepted and validated."""
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/agents?page=2&per_page=10")
        assert resp.status_code == 200
        assert resp.json()["meta"]["page"] == 2
        assert resp.json()["meta"]["per_page"] == 10

    def test_api_response_format(self) -> None:
        """All API responses should have data, meta, and errors keys."""
        resp = client.get("/health")
        # Health is a simple dict, but agent endpoints should follow format
        with patch(
            "api.routes.agents.AgentRegistry.list",
            new_callable=AsyncMock,
            return_value=([], 0),
        ):
            resp = client.get("/api/v1/agents")
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "errors" in body


# ===========================================================================
# Budget management
# ===========================================================================


class TestBudgetFlow:
    """Tests for team budget management."""

    def test_create_budget(self) -> None:
        """POST /api/v1/budgets should create a team budget."""
        team_name = f"budget-team-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/api/v1/budgets",
            json={
                "team": team_name,
                "monthly_limit_usd": 500.0,
                "alert_threshold_pct": 80.0,
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["team"] == team_name
        assert data["monthly_limit_usd"] == 500.0

    def test_get_budget(self) -> None:
        """GET /api/v1/budgets/{team} should return the team's budget."""
        team_name = f"get-budget-team-{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/budgets",
            json={"team": team_name, "monthly_limit_usd": 1000.0},
        )

        resp = client.get(f"/api/v1/budgets/{team_name}")
        assert resp.status_code == 200
        assert resp.json()["data"]["monthly_limit_usd"] == 1000.0

    def test_get_nonexistent_budget(self) -> None:
        """GET /api/v1/budgets/{team} should return 404 for unknown teams."""
        resp = client.get(f"/api/v1/budgets/nonexistent-team-{uuid.uuid4().hex[:8]}")
        assert resp.status_code == 404

    def test_create_budget_missing_fields(self) -> None:
        """POST /api/v1/budgets should reject incomplete requests."""
        resp = client.post("/api/v1/budgets", json={"team": "only-team"})
        assert resp.status_code == 400

    def test_list_budgets(self) -> None:
        """GET /api/v1/budgets should list all budgets."""
        resp = client.get("/api/v1/budgets")
        assert resp.status_code == 200
        assert "data" in resp.json()
