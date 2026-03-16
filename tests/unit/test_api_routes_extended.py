"""Extended tests for API routes: deploys, costs, teams, templates,
orchestrations, playground, gateway, and additional agent endpoints.

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
    DeployJobStatus,
    TemplateCategory,
    TemplateStatus,
    UserRole,
)
from api.services.auth import create_access_token

client = TestClient(app)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_NOW_ISO = _NOW.isoformat()


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


def _make_deploy_job(**kwargs):
    defaults = {
        "id": kwargs.pop("id", uuid.uuid4()),
        "agent_id": uuid.uuid4(),
        "agent_name": None,
        "status": DeployJobStatus.pending,
        "target": "local",
        "error_message": None,
        "started_at": _NOW,
        "completed_at": None,
        "agent": None,
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


class TestListDeploys:
    @patch(
        "api.routes.deploys.DeployRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_empty(self, mock_list) -> None:
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/deploys")
        assert resp.status_code == 200
        assert resp.json()["data"] == []
        assert resp.json()["meta"]["total"] == 0

    @patch(
        "api.routes.deploys.DeployRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_returns_jobs(self, mock_list) -> None:
        jobs = [_make_deploy_job(), _make_deploy_job()]
        mock_list.return_value = (jobs, 2)
        resp = client.get("/api/v1/deploys")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    @patch(
        "api.routes.deploys.DeployRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_with_pagination(self, mock_list) -> None:
        mock_list.return_value = ([_make_deploy_job()], 5)
        resp = client.get(
            "/api/v1/deploys",
            params={"page": 2, "per_page": 3},
        )
        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["total"] == 5
        assert meta["page"] == 2

    @patch(
        "api.routes.deploys.DeployRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_with_status_filter(self, mock_list) -> None:
        mock_list.return_value = ([], 0)
        client.get(
            "/api/v1/deploys",
            params={"status": "pending"},
        )
        kw = mock_list.call_args[1]
        assert kw["status"] == DeployJobStatus.pending


class TestGetDeploy:
    @patch(
        "api.routes.deploys.DeployService.get_deploy_status",
        new_callable=AsyncMock,
    )
    def test_get_existing(self, mock_get) -> None:
        job_id = uuid.uuid4()
        mock_get.return_value = {
            "id": str(job_id),
            "agent_id": str(uuid.uuid4()),
            "agent_name": "test",
            "status": "pending",
            "target": "local",
            "error_message": None,
            "started_at": _NOW_ISO,
            "completed_at": None,
            "logs": [],
        }
        resp = client.get(f"/api/v1/deploys/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == str(job_id)

    @patch(
        "api.routes.deploys.DeployService.get_deploy_status",
        new_callable=AsyncMock,
    )
    def test_get_not_found(self, mock_get) -> None:
        mock_get.return_value = None
        resp = client.get(f"/api/v1/deploys/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestCancelDeploy:
    @patch(
        "api.routes.deploys.DeployService.cancel_deploy",
        new_callable=AsyncMock,
    )
    def test_cancel_success(self, mock_cancel) -> None:
        mock_cancel.return_value = True
        job_id = uuid.uuid4()
        resp = client.delete(f"/api/v1/deploys/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["cancelled"] is True

    @patch(
        "api.routes.deploys.DeployService.cancel_deploy",
        new_callable=AsyncMock,
    )
    def test_cancel_not_found(self, mock_cancel) -> None:
        mock_cancel.return_value = False
        resp = client.delete(f"/api/v1/deploys/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestRollbackDeploy:
    @patch(
        "api.routes.deploys.DeployService.rollback_deploy",
        new_callable=AsyncMock,
    )
    def test_rollback_success(self, mock_rb) -> None:
        mock_rb.return_value = True
        job_id = uuid.uuid4()
        resp = client.post(f"/api/v1/deploys/{job_id}/rollback")
        assert resp.status_code == 200
        assert resp.json()["data"]["rolled_back"] is True

    @patch(
        "api.routes.deploys.DeployService.rollback_deploy",
        new_callable=AsyncMock,
    )
    def test_rollback_not_failed(self, mock_rb) -> None:
        mock_rb.return_value = False
        resp = client.post(f"/api/v1/deploys/{uuid.uuid4()}/rollback")
        assert resp.status_code == 400


class TestCreateDeploy:
    @patch(
        "api.routes.deploys.DeployService.create_deploy",
        new_callable=AsyncMock,
    )
    def test_create_with_agent_id(self, mock_create) -> None:
        agent_id = uuid.uuid4()
        job = _make_deploy_job(agent_id=agent_id)
        mock_create.return_value = job
        resp = client.post(
            "/api/v1/deploys",
            json={
                "agent_id": str(agent_id),
                "target": "local",
            },
        )
        assert resp.status_code == 200

    def test_create_missing_both(self) -> None:
        resp = client.post(
            "/api/v1/deploys",
            json={"target": "local"},
        )
        assert resp.status_code == 400

    @patch(
        "api.routes.deploys.DeployService.create_deploy",
        new_callable=AsyncMock,
    )
    def test_create_agent_not_found(self, mock_create) -> None:
        mock_create.side_effect = ValueError("Agent not found")
        resp = client.post(
            "/api/v1/deploys",
            json={
                "agent_id": str(uuid.uuid4()),
                "target": "local",
            },
        )
        assert resp.status_code == 404


# ── Cost Routes ──────────────────────────────────────────────────


class TestCostEvents:
    @patch("api.routes.costs.get_cost_store")
    def test_record_event_success(self, mock_store_fn) -> None:
        store = MagicMock()
        event = MagicMock()
        event.to_dict.return_value = {
            "id": "evt-1",
            "agent_name": "bot",
        }
        store.record_cost_event.return_value = event
        mock_store_fn.return_value = store
        resp = client.post(
            "/api/v1/costs/events",
            json={
                "agent_name": "bot",
                "team": "eng",
                "model_name": "gpt-4o",
                "provider": "openai",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.001,
            },
        )
        assert resp.status_code == 201
        store.record_cost_event.assert_called_once()

    @patch("api.routes.costs.get_cost_store")
    def test_record_event_missing_fields(self, mock_store_fn) -> None:
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/costs/events",
            json={"agent_name": "bot"},
        )
        assert resp.status_code == 400

    @patch("api.routes.costs.get_cost_store")
    def test_record_event_missing_tokens(self, mock_store_fn) -> None:
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/costs/events",
            json={
                "agent_name": "bot",
                "team": "eng",
                "model_name": "gpt-4o",
                "provider": "openai",
            },
        )
        assert resp.status_code == 400


class TestCostSummary:
    @patch("api.routes.costs.get_cost_store")
    def test_summary_default(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get_cost_summary.return_value = {
            "total_cost": 1.23,
            "total_tokens": 5000,
            "request_count": 10,
            "period": "30d",
        }
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/costs/summary")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_cost"] == 1.23

    @patch("api.routes.costs.get_cost_store")
    def test_summary_with_filters(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get_cost_summary.return_value = {
            "total_cost": 0,
            "total_tokens": 0,
            "request_count": 0,
            "period": "7d",
        }
        mock_store_fn.return_value = store
        resp = client.get(
            "/api/v1/costs/summary",
            params={"team": "eng", "days": 7},
        )
        assert resp.status_code == 200
        store.get_cost_summary.assert_called_once_with(team="eng", agent_name=None, days=7)


class TestCostBreakdown:
    @patch("api.routes.costs.get_cost_store")
    def test_breakdown(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get_cost_breakdown.return_value = {
            "by_agent": [],
            "by_model": [],
            "by_team": [],
        }
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/costs/breakdown")
        assert resp.status_code == 200
        assert "by_agent" in resp.json()["data"]


class TestCostTrend:
    @patch("api.routes.costs.get_cost_store")
    def test_trend(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get_cost_trend.return_value = {
            "points": [],
            "total_cost": 0,
            "period": "30d",
        }
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/costs/trend")
        assert resp.status_code == 200


class TestTopSpenders:
    @patch("api.routes.costs.get_cost_store")
    def test_top_spenders(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get_top_spenders.return_value = [
            {"agent_name": "bot", "cost": 5.0},
        ]
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/costs/top-spenders")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1


class TestCompareModels:
    @patch("api.routes.costs.get_cost_store")
    def test_compare_success(self, mock_store_fn) -> None:
        store = MagicMock()
        store.compare_models.return_value = {
            "model_a": "gpt-4o",
            "model_b": "claude-sonnet-4",
            "model_a_cost": 6.25,
            "model_b_cost": 9.0,
            "savings_pct": -44.0,
            "sample_tokens": 1000000,
        }
        mock_store_fn.return_value = store
        resp = client.post(
            "/api/v1/costs/compare",
            json={"model_a": "gpt-4o", "model_b": "claude-sonnet-4"},
        )
        assert resp.status_code == 200
        assert "savings_pct" in resp.json()["data"]

    @patch("api.routes.costs.get_cost_store")
    def test_compare_missing_models(self, mock_store_fn) -> None:
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/costs/compare",
            json={"model_a": "gpt-4o"},
        )
        assert resp.status_code == 400


class TestBudgets:
    @patch("api.routes.costs.get_cost_store")
    def test_list_budgets(self, mock_store_fn) -> None:
        store = MagicMock()
        budget = MagicMock()
        budget.to_dict.return_value = {
            "id": "b1",
            "team": "eng",
            "monthly_limit_usd": 500,
        }
        store.list_budgets.return_value = [budget]
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/budgets")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch("api.routes.costs.get_cost_store")
    def test_create_budget(self, mock_store_fn) -> None:
        store = MagicMock()
        budget = MagicMock()
        budget.to_dict.return_value = {
            "id": "b2",
            "team": "eng",
            "monthly_limit_usd": 1000,
        }
        store.create_budget.return_value = budget
        mock_store_fn.return_value = store
        resp = client.post(
            "/api/v1/budgets",
            json={"team": "eng", "monthly_limit_usd": 1000},
        )
        assert resp.status_code == 201

    @patch("api.routes.costs.get_cost_store")
    def test_create_budget_missing_fields(self, mock_store_fn) -> None:
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/budgets",
            json={"team": "eng"},
        )
        assert resp.status_code == 400

    @patch("api.routes.costs.get_cost_store")
    def test_get_budget_found(self, mock_store_fn) -> None:
        store = MagicMock()
        budget = MagicMock()
        budget.to_dict.return_value = {
            "team": "eng",
            "monthly_limit_usd": 500,
        }
        store.get_budget.return_value = budget
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/budgets/eng")
        assert resp.status_code == 200

    @patch("api.routes.costs.get_cost_store")
    def test_get_budget_not_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get_budget.return_value = None
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/budgets/nope")
        assert resp.status_code == 404

    @patch("api.routes.costs.get_cost_store")
    def test_update_budget_found(self, mock_store_fn) -> None:
        store = MagicMock()
        budget = MagicMock()
        budget.to_dict.return_value = {
            "team": "eng",
            "monthly_limit_usd": 2000,
        }
        store.update_budget.return_value = budget
        mock_store_fn.return_value = store
        resp = client.put(
            "/api/v1/budgets/eng",
            json={"monthly_limit_usd": 2000},
        )
        assert resp.status_code == 200

    @patch("api.routes.costs.get_cost_store")
    def test_update_budget_not_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.update_budget.return_value = None
        mock_store_fn.return_value = store
        resp = client.put(
            "/api/v1/budgets/nope",
            json={"monthly_limit_usd": 500},
        )
        assert resp.status_code == 404


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


class TestListOrchestrations:
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_list_all(self, mock_store_fn) -> None:
        store = MagicMock()
        store.list.return_value = [
            {"id": "o1", "name": "pipeline-1"},
        ]
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/orchestrations")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_list_with_filter(self, mock_store_fn) -> None:
        store = MagicMock()
        store.list.return_value = []
        mock_store_fn.return_value = store
        client.get(
            "/api/v1/orchestrations",
            params={"team": "eng", "status": "deployed"},
        )
        store.list.assert_called_once_with(team="eng", status="deployed")


class TestCreateOrchestration:
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_create_success(self, mock_store_fn) -> None:
        store = MagicMock()
        store.create.return_value = {
            "id": "o2",
            "name": "new-orch",
        }
        mock_store_fn.return_value = store
        resp = client.post(
            "/api/v1/orchestrations",
            json={
                "name": "new-orch",
                "version": "1.0.0",
                "strategy": "router",
                "agents": {"a": {"ref": "agents/a"}},
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_create_missing_fields(self, mock_store_fn) -> None:
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/orchestrations",
            json={"name": "incomplete"},
        )
        assert resp.status_code == 400


class TestGetOrchestration:
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_get_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get.return_value = {
            "id": "o1",
            "name": "orch-1",
        }
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/orchestrations/o1")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "orch-1"

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_get_not_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.get.return_value = None
        mock_store_fn.return_value = store
        resp = client.get("/api/v1/orchestrations/nope")
        assert resp.status_code == 404


class TestUpdateOrchestration:
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_update_success(self, mock_store_fn) -> None:
        store = MagicMock()
        store.update.return_value = {
            "id": "o1",
            "description": "Updated",
        }
        mock_store_fn.return_value = store
        resp = client.put(
            "/api/v1/orchestrations/o1",
            json={"description": "Updated"},
        )
        assert resp.status_code == 200

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_update_not_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.update.return_value = None
        mock_store_fn.return_value = store
        resp = client.put(
            "/api/v1/orchestrations/nope",
            json={"description": "X"},
        )
        assert resp.status_code == 404


class TestDeleteOrchestration:
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_delete_success(self, mock_store_fn) -> None:
        store = MagicMock()
        store.delete.return_value = True
        mock_store_fn.return_value = store
        resp = client.delete("/api/v1/orchestrations/o1")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] == "o1"

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_delete_not_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.delete.return_value = False
        mock_store_fn.return_value = store
        resp = client.delete("/api/v1/orchestrations/nope")
        assert resp.status_code == 404


class TestValidateOrchestration:
    @patch("api.routes.orchestrations.validate_orchestration")
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_validate_valid(self, mock_store_fn, mock_validate) -> None:
        result = MagicMock()
        result.valid = True
        result.errors = []
        mock_validate.return_value = result
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/orchestrations/validate",
            json={"yaml_content": "name: test\nversion: 1.0.0"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["valid"] is True

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_validate_empty_yaml(self, mock_store_fn) -> None:
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/orchestrations/validate",
            json={"yaml_content": ""},
        )
        assert resp.status_code == 400


class TestDeployOrchestration:
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_deploy_success(self, mock_store_fn) -> None:
        store = MagicMock()
        store.deploy.return_value = {
            "id": "o1",
            "status": "deployed",
        }
        mock_store_fn.return_value = store
        resp = client.post("/api/v1/orchestrations/o1/deploy")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "deployed"

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_deploy_not_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.deploy.return_value = None
        mock_store_fn.return_value = store
        resp = client.post("/api/v1/orchestrations/nope/deploy")
        assert resp.status_code == 404


class TestExecuteOrchestration:
    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_execute_success(self, mock_store_fn) -> None:
        store = MagicMock()
        store.execute = AsyncMock(return_value={"output": "done"})
        mock_store_fn.return_value = store
        resp = client.post(
            "/api/v1/orchestrations/o1/execute",
            json={"input_message": "hello"},
        )
        assert resp.status_code == 200

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_execute_missing_message(self, mock_store_fn) -> None:
        mock_store_fn.return_value = MagicMock()
        resp = client.post(
            "/api/v1/orchestrations/o1/execute",
            json={},
        )
        assert resp.status_code == 400

    @patch("api.routes.orchestrations.get_orchestration_store")
    def test_execute_not_found(self, mock_store_fn) -> None:
        store = MagicMock()
        store.execute = AsyncMock(side_effect=ValueError("not found"))
        mock_store_fn.return_value = store
        resp = client.post(
            "/api/v1/orchestrations/o1/execute",
            json={"input_message": "hi"},
        )
        assert resp.status_code == 404


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


class TestPlaygroundEvalCase:
    def test_save_eval_case(self) -> None:
        resp = client.post(
            "/api/v1/playground/eval-case",
            json={
                "agent_id": "agent-1",
                "conversation_history": [
                    {"role": "user", "content": "Hello"},
                ],
                "assistant_message": "Hi there!",
                "model_used": "gpt-4o",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["saved"] is True
        assert "eval_case_id" in data


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


class TestGatewayModels:
    def test_list_all_models(self) -> None:
        resp = client.get("/api/v1/gateway/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 1

    def test_filter_by_tier(self) -> None:
        resp = client.get(
            "/api/v1/gateway/models",
            params={"tier": "litellm"},
        )
        assert resp.status_code == 200
        for m in resp.json()["data"]:
            assert m["gateway_tier"] == "litellm"

    def test_filter_by_provider(self) -> None:
        resp = client.get(
            "/api/v1/gateway/models",
            params={"provider": "anthropic"},
        )
        assert resp.status_code == 200
        for m in resp.json()["data"]:
            assert m["provider"] == "anthropic"

    def test_pagination(self) -> None:
        resp = client.get(
            "/api/v1/gateway/models",
            params={"page": 1, "per_page": 2},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) <= 2


class TestGatewayProviders:
    def test_list_providers(self) -> None:
        resp = client.get("/api/v1/gateway/providers")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        ids = {p["id"] for p in data}
        assert "anthropic" in ids
        assert "openai" in ids


class TestGatewayLogs:
    def test_logs_default(self) -> None:
        resp = client.get("/api/v1/gateway/logs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) <= 20  # default per_page

    def test_logs_pagination(self) -> None:
        resp = client.get(
            "/api/v1/gateway/logs",
            params={"page": 1, "per_page": 5},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) <= 5

    def test_logs_filter_by_model(self) -> None:
        resp = client.get(
            "/api/v1/gateway/logs",
            params={"model": "gpt-4o"},
        )
        assert resp.status_code == 200
        for entry in resp.json()["data"]:
            assert entry["model"] == "gpt-4o"


class TestGatewayCostComparison:
    def test_cost_comparison(self) -> None:
        resp = client.get("/api/v1/gateway/costs/comparison")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 1
        # Sorted by input price ascending
        prices = [d["input_per_million"] for d in data]
        assert prices == sorted(prices)
