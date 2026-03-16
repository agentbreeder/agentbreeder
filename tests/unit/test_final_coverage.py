"""Final coverage-gap tests — targets 93% -> 95%+.

Covers uncovered lines in:
  1. api/routes/registry.py       (tool usage, model compare/usage,
                                    prompt versions, cross-entity search)
  2. api/routes/marketplace.py    (listing detail, publish, update, review)
  3. registry/mcp_servers.py      (update, delete, execute_tool HTTP)
  4. cli/commands/provider.py     (add, test, remove, config)
  5. cli/commands/orchestration.py (run interactive, json mode, visual)
  6. api/services/auth.py         (decode token, authenticate_user)
  7. api/main.py                  (lifespan, CORS)
  8. api/auth.py                  (get_current_user dependency)
  9. engine/resolver.py           (subagent + MCP refs)
  10. cli/commands/teardown.py    (_teardown_container internals)
"""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from api.main import app as fastapi_app
from api.models.enums import UserRole
from api.services.auth import create_access_token
from cli.main import app as cli_app

client = TestClient(fastapi_app)
runner = CliRunner()

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


# -- Helpers -------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    token = create_access_token(str(uuid.uuid4()), "test@test.com", "admin")
    return {"Authorization": f"Bearer {token}"}


def _mock_user(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "email": "test@test.com",
        "name": "Test",
        "role": UserRole.admin,
        "team": "eng",
        "is_active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _mock_obj(**kwargs):
    m = MagicMock()
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


# ===================================================================
# 1. REGISTRY ROUTES — uncovered lines
# ===================================================================


class TestRegistryToolUsage:
    """Lines 93-108: GET /tools/{id}/usage."""

    @patch("api.routes.registry.ToolRegistry")
    def test_tool_usage_found(self, mock_tr):
        tid = str(uuid.uuid4())
        agent = _mock_obj(
            id=str(uuid.uuid4()),
            name="agent-a",
            status="running",
        )
        mock_tr.get_by_id = AsyncMock(return_value=_mock_obj())
        mock_tr.get_usage = AsyncMock(return_value=[agent])
        resp = client.get(f"/api/v1/registry/tools/{tid}/usage")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["agent_name"] == "agent-a"

    @patch("api.routes.registry.ToolRegistry")
    def test_tool_usage_not_found(self, mock_tr):
        mock_tr.get_by_id = AsyncMock(return_value=None)
        resp = client.get(f"/api/v1/registry/tools/{uuid.uuid4()}/usage")
        assert resp.status_code == 404

    @patch("api.routes.registry.ToolRegistry")
    def test_tool_usage_enum_status(self, mock_tr):
        """status with .value attribute (enum)."""
        tid = str(uuid.uuid4())
        status_enum = MagicMock()
        status_enum.value = "deployed"
        agent = _mock_obj(
            id=str(uuid.uuid4()),
            name="agent-b",
            status=status_enum,
        )
        mock_tr.get_by_id = AsyncMock(return_value=_mock_obj())
        mock_tr.get_usage = AsyncMock(return_value=[agent])
        resp = client.get(f"/api/v1/registry/tools/{tid}/usage")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["agent_status"] == "deployed"


class TestRegistryModelCompare:
    """Lines 117-129: GET /models/compare."""

    @patch("api.routes.registry.ModelRegistry")
    def test_compare_success(self, mock_mr):
        uid1, uid2 = uuid.uuid4(), uuid.uuid4()
        m1 = _mock_obj(
            id=uid1,
            name="gpt-4o",
            provider="openai",
            description="",
            source="manual",
            config={},
            status="active",
            context_window=128000,
            max_output_tokens=4096,
            input_price_per_million=5.0,
            output_price_per_million=15.0,
            capabilities=[],
            created_at=_NOW,
            updated_at=_NOW,
        )
        m2 = _mock_obj(
            id=uid2,
            name="claude",
            provider="anthropic",
            description="",
            source="manual",
            config={},
            status="active",
            context_window=200000,
            max_output_tokens=4096,
            input_price_per_million=3.0,
            output_price_per_million=15.0,
            capabilities=[],
            created_at=_NOW,
            updated_at=_NOW,
        )
        mock_mr.get_by_ids = AsyncMock(return_value=[m1, m2])
        resp = client.get(f"/api/v1/registry/models/compare?ids={uid1},{uid2}")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    @patch("api.routes.registry.ModelRegistry")
    def test_compare_bad_count(self, mock_mr):
        resp = client.get("/api/v1/registry/models/compare?ids=m1")
        assert resp.status_code == 400

    @patch("api.routes.registry.ModelRegistry")
    def test_compare_not_found(self, mock_mr):
        mock_mr.get_by_ids = AsyncMock(return_value=[_mock_obj()])
        resp = client.get("/api/v1/registry/models/compare?ids=m1,m2")
        assert resp.status_code == 404


class TestRegistryModelUsage:
    """Lines 193-211: GET /models/{id}/usage."""

    @patch("api.routes.registry.ModelRegistry")
    def test_model_usage_found(self, mock_mr):
        mid = str(uuid.uuid4())
        agent = _mock_obj(
            id=str(uuid.uuid4()),
            name="agent-x",
            status="running",
        )
        mock_mr.get_by_id = AsyncMock(return_value=_mock_obj())
        mock_mr.get_usage = AsyncMock(return_value=[(agent, "primary")])
        resp = client.get(f"/api/v1/registry/models/{mid}/usage")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data[0]["usage_type"] == "primary"

    @patch("api.routes.registry.ModelRegistry")
    def test_model_usage_not_found(self, mock_mr):
        mock_mr.get_by_id = AsyncMock(return_value=None)
        resp = client.get(f"/api/v1/registry/models/{uuid.uuid4()}/usage")
        assert resp.status_code == 404

    @patch("api.routes.registry.ModelRegistry")
    def test_model_usage_enum_status(self, mock_mr):
        mid = str(uuid.uuid4())
        se = MagicMock()
        se.value = "active"
        agent = _mock_obj(id=str(uuid.uuid4()), name="a", status=se)
        mock_mr.get_by_id = AsyncMock(return_value=_mock_obj())
        mock_mr.get_usage = AsyncMock(return_value=[(agent, "fallback")])
        resp = client.get(f"/api/v1/registry/models/{mid}/usage")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["agent_status"] == "active"


class TestRegistryPromptVersions:
    """Lines 353-418: prompt version history/snapshot/diff."""

    @patch("api.routes.registry.PromptRegistry")
    def test_version_history_found(self, mock_pr):
        pid = str(uuid.uuid4())
        mock_pr.get_by_id = AsyncMock(return_value=_mock_obj())
        ver = _mock_obj(
            id=uuid.uuid4(),
            prompt_id=uuid.UUID(pid),
            version="1.0.0",
            content="hello",
            change_summary="init",
            author="alice",
            created_at=_NOW,
        )
        mock_pr.list_version_snapshots = AsyncMock(return_value=[ver])
        resp = client.get(f"/api/v1/registry/prompts/{pid}/versions/history")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch("api.routes.registry.PromptRegistry")
    def test_version_history_not_found(self, mock_pr):
        mock_pr.get_by_id = AsyncMock(return_value=None)
        resp = client.get(f"/api/v1/registry/prompts/{uuid.uuid4()}/versions/history")
        assert resp.status_code == 404

    @patch("api.routes.registry.PromptRegistry")
    def test_create_version_snapshot(self, mock_pr):
        pid = str(uuid.uuid4())
        mock_pr.get_by_id = AsyncMock(return_value=_mock_obj())
        ver = _mock_obj(
            id=uuid.uuid4(),
            prompt_id=uuid.UUID(pid),
            version="2.0.0",
            content="updated",
            change_summary="fix",
            author="bob",
            created_at=_NOW,
        )
        mock_pr.create_version_snapshot = AsyncMock(return_value=ver)
        resp = client.post(
            f"/api/v1/registry/prompts/{pid}/versions/history",
            json={
                "version": "2.0.0",
                "content": "updated",
                "change_summary": "fix",
                "author": "bob",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.registry.PromptRegistry")
    def test_create_version_snapshot_prompt_missing(self, mock_pr):
        mock_pr.get_by_id = AsyncMock(return_value=None)
        resp = client.post(
            f"/api/v1/registry/prompts/{uuid.uuid4()}/versions/history",
            json={
                "version": "1.0.0",
                "content": "x",
                "change_summary": "s",
                "author": "a",
            },
        )
        assert resp.status_code == 404

    @patch("api.routes.registry.PromptRegistry")
    def test_get_version_snapshot(self, mock_pr):
        pid = str(uuid.uuid4())
        vid = str(uuid.uuid4())
        ver = _mock_obj(
            id=uuid.UUID(vid),
            prompt_id=uuid.UUID(pid),
            version="1.0.0",
            content="c",
            change_summary="s",
            author="a",
            created_at=_NOW,
        )
        mock_pr.get_version_snapshot = AsyncMock(return_value=ver)
        resp = client.get(f"/api/v1/registry/prompts/{pid}/versions/history/{vid}")
        assert resp.status_code == 200

    @patch("api.routes.registry.PromptRegistry")
    def test_get_version_snapshot_not_found(self, mock_pr):
        mock_pr.get_version_snapshot = AsyncMock(return_value=None)
        resp = client.get(
            f"/api/v1/registry/prompts/{uuid.uuid4()}/versions/history/{uuid.uuid4()}"
        )
        assert resp.status_code == 404

    @patch("api.routes.registry.PromptRegistry")
    def test_diff_versions(self, mock_pr):
        pid = str(uuid.uuid4())
        vid1 = str(uuid.uuid4())
        vid2 = str(uuid.uuid4())
        v1 = _mock_obj(
            id=uuid.UUID(vid1),
            prompt_id=uuid.UUID(pid),
            version="1.0.0",
            content="a",
            change_summary="",
            author="x",
            created_at=_NOW,
        )
        v2 = _mock_obj(
            id=uuid.UUID(vid2),
            prompt_id=uuid.UUID(pid),
            version="2.0.0",
            content="b",
            change_summary="",
            author="x",
            created_at=_NOW,
        )
        mock_pr.diff_version_snapshots = AsyncMock(return_value=(v1, v2, "--- a\n+++ b\n-a\n+b"))
        resp = client.get(f"/api/v1/registry/prompts/{pid}/versions/history/{vid1}/diff/{vid2}")
        assert resp.status_code == 200
        assert "diff" in resp.json()["data"]

    @patch("api.routes.registry.PromptRegistry")
    def test_diff_versions_missing(self, mock_pr):
        mock_pr.diff_version_snapshots = AsyncMock(return_value=(None, None, ""))
        resp = client.get(
            f"/api/v1/registry/prompts/{uuid.uuid4()}"
            f"/versions/history/{uuid.uuid4()}"
            f"/diff/{uuid.uuid4()}"
        )
        assert resp.status_code == 404


class TestRegistryCrossEntitySearch:
    """Lines 462-490: cross-entity search with models/prompts."""

    @patch("api.routes.registry.PromptRegistry")
    @patch("api.routes.registry.ModelRegistry")
    @patch("api.routes.registry.ToolRegistry")
    @patch("api.routes.registry.AgentRegistry")
    def test_search_returns_all_types(self, mock_ar, mock_tr, mock_mr, mock_pr):
        mock_ar.search = AsyncMock(
            return_value=(
                [
                    _mock_obj(
                        id=str(uuid.uuid4()),
                        name="agent1",
                        description="d",
                        team="t",
                    )
                ],
                1,
            )
        )
        mock_tr.search = AsyncMock(
            return_value=(
                [
                    _mock_obj(
                        id=str(uuid.uuid4()),
                        name="tool1",
                        description="d",
                    )
                ],
                1,
            )
        )
        mock_mr.search = AsyncMock(
            return_value=(
                [
                    _mock_obj(
                        id=str(uuid.uuid4()),
                        name="model1",
                        description="d",
                    )
                ],
                1,
            )
        )
        mock_pr.search = AsyncMock(
            return_value=(
                [
                    _mock_obj(
                        id=str(uuid.uuid4()),
                        name="prompt1",
                        description="d",
                        team="t",
                    )
                ],
                1,
            )
        )
        resp = client.get("/api/v1/registry/search?q=test")
        assert resp.status_code == 200
        data = resp.json()["data"]
        types = {r["entity_type"] for r in data}
        assert types == {"agent", "tool", "model", "prompt"}

    @patch("api.routes.registry.PromptRegistry")
    @patch("api.routes.registry.ModelRegistry")
    @patch("api.routes.registry.ToolRegistry")
    @patch("api.routes.registry.AgentRegistry")
    def test_search_empty(self, mock_ar, mock_tr, mock_mr, mock_pr):
        mock_ar.search = AsyncMock(return_value=([], 0))
        mock_tr.search = AsyncMock(return_value=([], 0))
        mock_mr.search = AsyncMock(return_value=([], 0))
        mock_pr.search = AsyncMock(return_value=([], 0))
        resp = client.get("/api/v1/registry/search?q=nothing")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ===================================================================
# 2. MARKETPLACE ROUTES — uncovered lines
# ===================================================================


class TestMarketplaceListingDetail:
    """Lines 84-108: submit listing + get listing detail."""

    @patch("api.auth.get_user_by_id")
    @patch("api.routes.marketplace.TemplateRegistry")
    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_submit_listing_template_not_found(self, mock_mkt, mock_tmpl, mock_get_user):
        mock_get_user.return_value = _mock_user()
        mock_tmpl.get_by_id = AsyncMock(return_value=None)
        resp = client.post(
            "/api/v1/marketplace/listings",
            json={
                "template_id": str(uuid.uuid4()),
                "submitted_by": "alice",
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_get_listing_found(self, mock_mkt):
        from api.models.enums import (
            ListingStatus,
            TemplateCategory,
        )

        lid = uuid.uuid4()
        tmpl = _mock_obj(
            id=uuid.uuid4(),
            name="tmpl",
            description="d",
            category=TemplateCategory.customer_support,
            framework="langgraph",
            tags=[],
            author="a",
            version="1.0.0",
            config={},
            config_template={},
            files=[],
            readme="r",
            team="eng",
            status="published",
            created_at=_NOW,
            updated_at=_NOW,
        )
        listing = _mock_obj(
            id=lid,
            template_id=tmpl.id,
            template=tmpl,
            status=ListingStatus.approved,
            submitted_by="a",
            reviewed_by="b",
            reject_reason=None,
            avg_rating=4.5,
            review_count=2,
            install_count=10,
            featured=False,
            published_at=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
        )
        mock_mkt.get_by_id = AsyncMock(return_value=listing)
        resp = client.get(f"/api/v1/marketplace/listings/{lid}")
        assert resp.status_code == 200

    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_get_listing_not_found(self, mock_mkt):
        mock_mkt.get_by_id = AsyncMock(return_value=None)
        resp = client.get(f"/api/v1/marketplace/listings/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestMarketplaceUpdateListing:
    """Lines 118-137: update listing approve/reject/featured."""

    @patch("api.auth.get_user_by_id")
    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_update_approve(self, mock_mkt, mock_get_user):
        from api.models.enums import ListingStatus

        mock_get_user.return_value = _mock_user()
        lid = uuid.uuid4()
        listing = _mock_obj(
            id=lid,
            template_id=uuid.uuid4(),
            template=None,
            status=ListingStatus.approved,
            submitted_by="a",
            reviewed_by="b",
            reject_reason=None,
            avg_rating=0,
            review_count=0,
            install_count=0,
            featured=False,
            published_at=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
        )
        mock_mkt.get_by_id = AsyncMock(return_value=listing)
        mock_mkt.approve = AsyncMock(return_value=listing)
        resp = client.put(
            f"/api/v1/marketplace/listings/{lid}",
            json={
                "status": "approved",
                "reviewed_by": "admin",
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch("api.auth.get_user_by_id")
    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_update_reject(self, mock_mkt, mock_get_user):
        from api.models.enums import ListingStatus

        mock_get_user.return_value = _mock_user()
        lid = uuid.uuid4()
        listing = _mock_obj(
            id=lid,
            template_id=uuid.uuid4(),
            template=None,
            status=ListingStatus.rejected,
            submitted_by="a",
            reviewed_by="b",
            reject_reason="low quality",
            avg_rating=0,
            review_count=0,
            install_count=0,
            featured=False,
            published_at=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
        )
        mock_mkt.get_by_id = AsyncMock(return_value=listing)
        mock_mkt.reject = AsyncMock(return_value=listing)
        resp = client.put(
            f"/api/v1/marketplace/listings/{lid}",
            json={
                "status": "rejected",
                "reviewed_by": "admin",
                "reject_reason": "low quality",
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch("api.auth.get_user_by_id")
    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_update_featured(self, mock_mkt, mock_get_user):
        from api.models.enums import ListingStatus

        mock_get_user.return_value = _mock_user()
        lid = uuid.uuid4()
        listing = _mock_obj(
            id=lid,
            template_id=uuid.uuid4(),
            template=None,
            status=ListingStatus.approved,
            submitted_by="a",
            reviewed_by=None,
            reject_reason=None,
            avg_rating=0,
            review_count=0,
            install_count=0,
            featured=True,
            published_at=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
        )
        mock_mkt.get_by_id = AsyncMock(return_value=listing)
        resp = client.put(
            f"/api/v1/marketplace/listings/{lid}",
            json={"featured": True},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch("api.auth.get_user_by_id")
    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_update_not_found(self, mock_mkt, mock_get_user):
        mock_get_user.return_value = _mock_user()
        mock_mkt.get_by_id = AsyncMock(return_value=None)
        resp = client.put(
            f"/api/v1/marketplace/listings/{uuid.uuid4()}",
            json={"featured": True},
            headers=_auth_headers(),
        )
        assert resp.status_code == 404


class TestMarketplaceReviews:
    """Lines 151-163: add review."""

    @patch("api.auth.get_user_by_id")
    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_add_review(self, mock_mkt, mock_get_user):
        mock_get_user.return_value = _mock_user()
        lid = uuid.uuid4()
        listing = _mock_obj(id=lid)
        review = _mock_obj(
            id=uuid.uuid4(),
            listing_id=lid,
            reviewer="alice",
            rating=5,
            comment="great",
            created_at=_NOW,
        )
        mock_mkt.get_by_id = AsyncMock(return_value=listing)
        mock_mkt.add_review = AsyncMock(return_value=review)
        resp = client.post(
            f"/api/v1/marketplace/listings/{lid}/reviews",
            json={
                "reviewer": "alice",
                "rating": 5,
                "comment": "great",
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 201

    @patch("api.auth.get_user_by_id")
    @patch("api.routes.marketplace.MarketplaceRegistry")
    def test_add_review_listing_not_found(self, mock_mkt, mock_get_user):
        mock_get_user.return_value = _mock_user()
        mock_mkt.get_by_id = AsyncMock(return_value=None)
        resp = client.post(
            f"/api/v1/marketplace/listings/{uuid.uuid4()}/reviews",
            json={
                "reviewer": "alice",
                "rating": 5,
                "comment": "great",
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 404


# ===================================================================
# 3. MCP SERVER REGISTRY — uncovered lines
# ===================================================================


class TestMcpServerUpdate:
    """Lines 70-94: McpServerRegistry.update."""

    @pytest.mark.asyncio
    async def test_update_server(self):
        from registry.mcp_servers import McpServerRegistry

        server = _mock_obj(
            id=uuid.uuid4(),
            name="old",
            endpoint="e",
            transport="stdio",
            status="active",
        )
        session = AsyncMock()
        with patch.object(
            McpServerRegistry,
            "get_by_id",
            new_callable=AsyncMock,
            return_value=server,
        ):
            result = await McpServerRegistry.update(
                session,
                str(server.id),
                name="new-name",
                endpoint="http://new",
                transport="sse",
                status="error",
            )
        assert result.name == "new-name"
        assert result.endpoint == "http://new"

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        with patch.object(
            McpServerRegistry,
            "get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await McpServerRegistry.update(session, "bad-id", name="x")
        assert result is None


class TestMcpServerDelete:
    """Lines 96-105: McpServerRegistry.delete."""

    @pytest.mark.asyncio
    async def test_delete_success(self):
        from registry.mcp_servers import McpServerRegistry

        server = _mock_obj(
            id=uuid.uuid4(),
            name="doomed",
        )
        session = AsyncMock()
        with patch.object(
            McpServerRegistry,
            "get_by_id",
            new_callable=AsyncMock,
            return_value=server,
        ):
            result = await McpServerRegistry.delete(session, str(server.id))
        assert result is True
        session.delete.assert_called_once_with(server)

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        with patch.object(
            McpServerRegistry,
            "get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await McpServerRegistry.delete(session, "nope")
        assert result is False


class TestMcpServerExecuteTool:
    """Lines 240-255: execute_tool with HTTP transport."""

    @pytest.mark.asyncio
    async def test_execute_tool_http_success(self):
        import httpx as real_httpx

        from registry.mcp_servers import McpServerRegistry

        server = _mock_obj(
            id=uuid.uuid4(),
            name="srv",
            transport="sse",
            endpoint="http://srv:8080",
        )
        session = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"output": "done"}}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                McpServerRegistry,
                "get_by_id",
                new_callable=AsyncMock,
                return_value=server,
            ),
            patch.object(
                real_httpx,
                "AsyncClient",
                return_value=mock_client,
            ),
        ):
            result = await McpServerRegistry.execute_tool(
                session, str(server.id), "my-tool", {"a": 1}
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_tool_http_error(self):
        import httpx as real_httpx

        from registry.mcp_servers import McpServerRegistry

        server = _mock_obj(
            id=uuid.uuid4(),
            name="srv",
            transport="streamable_http",
            endpoint="http://srv:8080",
        )
        session = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                McpServerRegistry,
                "get_by_id",
                new_callable=AsyncMock,
                return_value=server,
            ),
            patch.object(
                real_httpx,
                "AsyncClient",
                return_value=mock_client,
            ),
        ):
            result = await McpServerRegistry.execute_tool(session, str(server.id), "tool", {})
        assert result["success"] is False
        assert "500" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_http_exception(self):
        import httpx as real_httpx

        from registry.mcp_servers import McpServerRegistry

        server = _mock_obj(
            id=uuid.uuid4(),
            name="srv",
            transport="sse",
            endpoint="http://srv:8080",
        )
        session = AsyncMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                McpServerRegistry,
                "get_by_id",
                new_callable=AsyncMock,
                return_value=server,
            ),
            patch.object(
                real_httpx,
                "AsyncClient",
                return_value=mock_client,
            ),
        ):
            result = await McpServerRegistry.execute_tool(session, str(server.id), "tool", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        from registry.mcp_servers import McpServerRegistry

        session = AsyncMock()
        with patch.object(
            McpServerRegistry,
            "get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await McpServerRegistry.execute_tool(session, "bad", "tool", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_tool_stdio_fallback(self):
        from registry.mcp_servers import McpServerRegistry

        server = _mock_obj(
            id=uuid.uuid4(),
            name="local",
            transport="stdio",
            endpoint="",
        )
        session = AsyncMock()
        with patch.object(
            McpServerRegistry,
            "get_by_id",
            new_callable=AsyncMock,
            return_value=server,
        ):
            result = await McpServerRegistry.execute_tool(session, str(server.id), "my-tool", {})
        assert result["success"] is True
        assert "Simulated" in result["result"]["output"]


# ===================================================================
# 4. CLI PROVIDER COMMANDS — uncovered lines
# ===================================================================


class TestProviderAdd:
    """Lines 279-377: provider add (non-interactive)."""

    def test_add_unknown_type(self):
        result = runner.invoke(cli_app, ["provider", "add", "badtype"])
        assert result.exit_code == 1
        assert "Unknown" in result.output

    def test_add_ollama_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            with (
                patch("cli.commands.provider.PROVIDERS_FILE", pf),
                patch(
                    "cli.commands.provider._write_env_key",
                    return_value=Path(tmpdir) / ".env",
                ),
            ):
                result = runner.invoke(
                    cli_app,
                    [
                        "provider",
                        "add",
                        "ollama",
                        "--base-url",
                        "http://localhost:11434",
                        "--json",
                    ],
                )
            assert result.exit_code == 0
            out = json.loads(result.output)
            assert out["provider"]["provider_type"] == "ollama"

    def test_add_openai_with_key_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            with (
                patch("cli.commands.provider.PROVIDERS_FILE", pf),
                patch(
                    "cli.commands.provider._write_env_key",
                    return_value=Path(tmpdir) / ".env",
                ),
            ):
                result = runner.invoke(
                    cli_app,
                    [
                        "provider",
                        "add",
                        "openai",
                        "--api-key",
                        "sk-test123456789",
                        "--json",
                    ],
                )
            assert result.exit_code == 0
            out = json.loads(result.output)
            assert out["provider"]["status"] == "active"

    def test_add_openai_rich_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            with (
                patch("cli.commands.provider.PROVIDERS_FILE", pf),
                patch(
                    "cli.commands.provider._write_env_key",
                    return_value=Path(tmpdir) / ".env",
                ),
            ):
                result = runner.invoke(
                    cli_app,
                    [
                        "provider",
                        "add",
                        "openai",
                        "--api-key",
                        "sk-test123456789",
                    ],
                )
            assert result.exit_code == 0
            assert "connected" in result.output


class TestProviderTest:
    """Lines 380-418: provider test."""

    def test_provider_test_not_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text("{}")
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(cli_app, ["provider", "test", "openai"])
            assert result.exit_code == 1

    def test_provider_test_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI",
                            "provider_type": "openai",
                            "base_url": "https://api.openai.com/v1",
                            "status": "active",
                            "model_count": 7,
                            "latency_ms": 50,
                            "masked_key": "••••1234",
                        },
                    }
                )
            )
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(
                    cli_app,
                    ["provider", "test", "openai", "--json"],
                )
            assert result.exit_code == 0
            out = json.loads(result.output)
            assert out["success"] is True

    def test_provider_test_rich(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI",
                            "provider_type": "openai",
                            "base_url": "https://api.openai.com/v1",
                            "status": "active",
                            "model_count": 7,
                            "latency_ms": 50,
                            "masked_key": "••••1234",
                        },
                    }
                )
            )
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(
                    cli_app,
                    ["provider", "test", "openai"],
                )
            assert result.exit_code == 0
            assert "healthy" in result.output


class TestProviderRemove:
    """Lines 458-501: provider remove."""

    def test_remove_not_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text("{}")
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(cli_app, ["provider", "remove", "openai"])
            assert result.exit_code == 1

    def test_remove_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI",
                            "provider_type": "openai",
                            "status": "active",
                        },
                    }
                )
            )
            with (
                patch("cli.commands.provider.PROVIDERS_FILE", pf),
                patch("cli.commands.provider._remove_env_key"),
            ):
                result = runner.invoke(
                    cli_app,
                    [
                        "provider",
                        "remove",
                        "openai",
                        "--json",
                    ],
                )
            assert result.exit_code == 0
            out = json.loads(result.output)
            assert out["removed"] == "openai"

    def test_remove_confirm_yes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI",
                            "provider_type": "openai",
                            "status": "active",
                        },
                    }
                )
            )
            with (
                patch("cli.commands.provider.PROVIDERS_FILE", pf),
                patch("cli.commands.provider._remove_env_key"),
            ):
                result = runner.invoke(
                    cli_app,
                    ["provider", "remove", "openai"],
                    input="y\n",
                )
            assert result.exit_code == 0
            assert "removed" in result.output

    def test_remove_confirm_no(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI",
                            "provider_type": "openai",
                            "status": "active",
                        },
                    }
                )
            )
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(
                    cli_app,
                    ["provider", "remove", "openai"],
                    input="n\n",
                )
            assert result.exit_code == 0
            assert "Cancelled" in result.output


class TestProviderEnableDisable:
    """Lines 504-547: provider enable/disable."""

    def test_disable_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text("{}")
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(
                    cli_app,
                    ["provider", "disable", "openai"],
                )
            assert result.exit_code == 1

    def test_enable_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text("{}")
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(
                    cli_app,
                    ["provider", "enable", "openai"],
                )
            assert result.exit_code == 1

    def test_disable_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI",
                            "provider_type": "openai",
                            "status": "active",
                        },
                    }
                )
            )
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(
                    cli_app,
                    [
                        "provider",
                        "disable",
                        "openai",
                        "--json",
                    ],
                )
            assert result.exit_code == 0
            out = json.loads(result.output)
            assert out["status"] == "disabled"

    def test_enable_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "providers.json"
            pf.write_text(
                json.dumps(
                    {
                        "openai": {
                            "name": "OpenAI",
                            "provider_type": "openai",
                            "status": "disabled",
                        },
                    }
                )
            )
            with patch("cli.commands.provider.PROVIDERS_FILE", pf):
                result = runner.invoke(
                    cli_app,
                    [
                        "provider",
                        "enable",
                        "openai",
                        "--json",
                    ],
                )
            assert result.exit_code == 0
            out = json.loads(result.output)
            assert out["status"] == "active"


# ===================================================================
# 5. ORCHESTRATION CLI — uncovered lines
# ===================================================================

VALID_ORCH_YAML = """\
name: test-orch
version: 1.0.0
strategy: sequential
team: engineering
owner: test@example.com
description: test orchestration
agents:
  summarizer:
    ref: agents/summarizer
  reviewer:
    ref: agents/reviewer
"""


def _mock_httpx_client(
    get_json=None,
    post_json=None,
    get_side_effect=None,
    post_side_effect=None,
):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    if get_json is not None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = get_json
        resp.raise_for_status = MagicMock()
        mock_client.get.return_value = resp
    if get_side_effect is not None:
        mock_client.get.side_effect = get_side_effect

    if post_json is not None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = post_json
        resp.raise_for_status = MagicMock()
        mock_client.post.return_value = resp
    if post_side_effect is not None:
        mock_client.post.side_effect = post_side_effect

    return mock_client


class TestOrchestrationRun:
    """Lines 401-507: orchestration chat interactive loop."""

    def test_chat_not_found(self):

        mock_client = _mock_httpx_client(get_json={"data": []})
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                ["orchestration", "chat", "nonexistent"],
            )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_chat_connect_error(self):
        import httpx as _httpx

        mock_client = _mock_httpx_client(
            get_side_effect=_httpx.ConnectError("fail"),
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                ["orchestration", "chat", "myorch"],
            )
        assert result.exit_code == 1

    def test_chat_interactive_quit(self):
        orch_data = {
            "id": "orch-1",
            "name": "myorch",
            "strategy": "sequential",
            "agents_config": {"a": {"ref": "agents/a"}},
            "status": "deployed",
        }
        mock_client = _mock_httpx_client(
            get_json={"data": [orch_data]},
            post_json={
                "data": {
                    "output": "hello",
                    "agent_trace": [],
                    "total_tokens": 10,
                    "total_cost": 0.001,
                    "total_latency_ms": 50,
                    "strategy": "sequential",
                },
            },
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                ["orchestration", "chat", "myorch"],
                input="hello\n/quit\n",
            )
        assert result.exit_code == 0
        assert "hello" in result.output

    def test_chat_interactive_verbose(self):
        orch_data = {
            "id": "orch-1",
            "name": "myorch",
            "strategy": "sequential",
            "agents_config": {"a": {"ref": "agents/a"}},
            "status": "deployed",
        }
        trace = [
            {
                "agent_name": "a",
                "status": "success",
                "latency_ms": 30,
                "tokens": 5,
                "output": "trace output",
            },
        ]
        mock_client = _mock_httpx_client(
            get_json={"data": [orch_data]},
            post_json={
                "data": {
                    "output": "response",
                    "agent_trace": trace,
                    "total_tokens": 5,
                    "total_cost": 0.0001,
                    "total_latency_ms": 30,
                    "strategy": "sequential",
                },
            },
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                [
                    "orchestration",
                    "chat",
                    "myorch",
                    "--verbose",
                ],
                input="test\n/quit\n",
            )
        assert result.exit_code == 0
        assert "Agent Trace" in result.output

    def test_chat_interactive_help(self):
        orch_data = {
            "id": "orch-1",
            "name": "myorch",
            "strategy": "sequential",
            "agents_config": {},
            "status": "deployed",
        }
        mock_client = _mock_httpx_client(
            get_json={"data": [orch_data]},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                ["orchestration", "chat", "myorch"],
                input="/help\n/exit\n",
            )
        assert result.exit_code == 0
        assert "Chat Commands" in result.output


class TestOrchestrationJsonMode:
    """Lines 510-547: _run_json_mode."""

    def test_json_mode_not_found(self):

        mock_client = _mock_httpx_client(get_json={"data": []})
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                [
                    "orchestration",
                    "chat",
                    "missing",
                    "--json",
                ],
                input="hello\n",
            )
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_json_mode_success(self):
        orch_data = {
            "id": "orch-1",
            "name": "myorch",
            "strategy": "sequential",
            "agents_config": {},
            "status": "deployed",
        }
        mock_client = _mock_httpx_client(
            get_json={"data": [orch_data]},
            post_json={
                "data": {
                    "output": "result",
                    "total_tokens": 10,
                },
            },
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                [
                    "orchestration",
                    "chat",
                    "myorch",
                    "--json",
                ],
                input="question\n",
            )
        assert result.exit_code == 0
        assert "result" in result.output

    def test_json_mode_error(self):
        import httpx as _httpx

        orch_data = {
            "id": "orch-1",
            "name": "myorch",
            "strategy": "sequential",
            "agents_config": {},
            "status": "deployed",
        }
        # First call for find succeeds, second for execute fails
        mock_client = _mock_httpx_client(
            get_json={"data": [orch_data]},
            post_side_effect=_httpx.ConnectError("down"),
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=mock_client,
        ):
            result = runner.invoke(
                cli_app,
                [
                    "orchestration",
                    "chat",
                    "myorch",
                    "--json",
                ],
                input="hello\n",
            )
        assert "error" in result.output


# ===================================================================
# 6. AUTH SERVICE — uncovered lines
# ===================================================================


class TestAuthServiceDecode:
    """Lines 39-47, 51-57, 81-88."""

    def test_decode_valid_token(self):
        from api.services.auth import (
            create_access_token,
            decode_access_token,
        )

        token = create_access_token("u1", "a@b.com", "admin")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "u1"
        assert payload["email"] == "a@b.com"

    def test_decode_invalid_token(self):
        from api.services.auth import decode_access_token

        assert decode_access_token("garbage") is None

    def test_decode_missing_sub(self):
        import jwt as pyjwt

        from api.config import settings
        from api.services.auth import decode_access_token

        token = pyjwt.encode(
            {"email": "a@b.com"},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        assert decode_access_token(token) is None

    @pytest.mark.asyncio
    async def test_authenticate_user_success(self):
        from api.services.auth import authenticate_user

        user = _mock_obj(
            is_active=True,
            password_hash="$2b$12$hash",
        )
        with (
            patch(
                "api.services.auth.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "api.services.auth.verify_password",
                return_value=True,
            ),
        ):
            result = await authenticate_user(AsyncMock(), "a@b.com", "pass")
        assert result is user

    @pytest.mark.asyncio
    async def test_authenticate_user_bad_password(self):
        from api.services.auth import authenticate_user

        user = _mock_obj(is_active=True, password_hash="h")
        with (
            patch(
                "api.services.auth.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "api.services.auth.verify_password",
                return_value=False,
            ),
        ):
            result = await authenticate_user(AsyncMock(), "a@b.com", "wrong")
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_user_inactive(self):
        from api.services.auth import authenticate_user

        user = _mock_obj(is_active=False, password_hash="h")
        with patch(
            "api.services.auth.get_user_by_email",
            new_callable=AsyncMock,
            return_value=user,
        ):
            result = await authenticate_user(AsyncMock(), "a@b.com", "pass")
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_user_not_found(self):
        from api.services.auth import authenticate_user

        with patch(
            "api.services.auth.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await authenticate_user(AsyncMock(), "a@b.com", "pass")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_id(self):
        from api.services.auth import get_user_by_id

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        result = await get_user_by_id(mock_db, uuid.uuid4())
        assert result is None


# ===================================================================
# 7. API MAIN — uncovered lines
# ===================================================================


class TestApiMainLifespan:
    """Lines 46-75: lifespan + CORS + seed."""

    def test_health_endpoint(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_cors_headers(self):
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS should allow the origin
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_seed_admin_skip_existing(self):
        from api.main import _seed_default_admin

        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.database.async_session",
                return_value=mock_session_ctx,
            ),
            patch(
                "api.services.auth.get_user_by_email",
                new_callable=AsyncMock,
                return_value=_mock_user(),
            ),
        ):
            await _seed_default_admin()

    @pytest.mark.asyncio
    async def test_seed_admin_exception(self):
        from api.main import _seed_default_admin

        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.database.async_session",
                return_value=mock_session_ctx,
            ),
            patch(
                "api.services.auth.get_user_by_email",
                new_callable=AsyncMock,
                side_effect=Exception("db down"),
            ),
        ):
            await _seed_default_admin()


# ===================================================================
# 8. API AUTH DEPENDENCY — uncovered lines
# ===================================================================


class TestApiAuthDependency:
    """Lines 40, 53-60: get_current_user with valid/invalid."""

    def test_no_auth_header(self):
        resp = client.post(
            "/api/v1/marketplace/listings",
            json={
                "template_id": str(uuid.uuid4()),
                "submitted_by": "alice",
            },
        )
        # Should be 401 or 403 (no auth)
        assert resp.status_code in (401, 403)

    def test_invalid_token(self):
        resp = client.post(
            "/api/v1/marketplace/listings",
            json={
                "template_id": str(uuid.uuid4()),
                "submitted_by": "alice",
            },
            headers={"Authorization": "Bearer bad.token.here"},
        )
        assert resp.status_code == 401

    def test_valid_token_user_not_found(self):
        token = create_access_token(str(uuid.uuid4()), "ghost@test.com", "admin")
        with patch(
            "api.auth.get_user_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.post(
                "/api/v1/marketplace/listings",
                json={
                    "template_id": str(uuid.uuid4()),
                    "submitted_by": "alice",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 401


# ===================================================================
# 9. ENGINE RESOLVER — uncovered lines
# ===================================================================


class TestResolverSubagents:
    """Lines 38-57: subagent + MCP ref resolution."""

    def test_resolve_with_subagents(self):
        from engine.config_parser import (
            AgentConfig,
            FrameworkType,
        )
        from engine.resolver import resolve_dependencies

        config = AgentConfig(
            name="parent",
            version="1.0.0",
            team="eng",
            owner="a@b.com",
            framework=FrameworkType.langgraph,
            model={"primary": "gpt-4o"},
            deploy={"cloud": "local"},
            subagents=[
                {
                    "name": "helper",
                    "ref": "agents/helper",
                    "description": "A helper agent",
                }
            ],
        )
        resolved = resolve_dependencies(config)
        # Should have generated subagent tools
        tool_names = [t.name for t in resolved.tools if t.name]
        assert any("helper" in n for n in tool_names)

    def test_resolve_with_mcp_servers(self):
        from engine.config_parser import (
            AgentConfig,
            FrameworkType,
        )
        from engine.resolver import resolve_dependencies

        config = AgentConfig(
            name="mcp-user",
            version="1.0.0",
            team="eng",
            owner="a@b.com",
            framework=FrameworkType.langgraph,
            model={"primary": "gpt-4o"},
            deploy={"cloud": "local"},
            mcp_servers=[{"ref": "mcp/filesystem"}],
        )
        resolved = resolve_dependencies(config)
        assert len(resolved.mcp_servers) == 1


# ===================================================================
# 10. TEARDOWN — _teardown_container lines 146-160
# ===================================================================


class TestTeardownContainer:
    """Lines 146-160: _teardown_container internals."""

    def test_teardown_container_success(self):
        from cli.commands.teardown import _teardown_container

        mock_deployer = MagicMock()
        with (
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
            patch("cli.commands.teardown.asyncio.run"),
        ):
            result = _teardown_container("my-agent", False)
        assert result is True

    def test_teardown_container_runtime_error(self):
        from cli.commands.teardown import _teardown_container

        with patch(
            "engine.deployers.docker_compose.DockerComposeDeployer",
            side_effect=RuntimeError("no docker"),
        ):
            result = _teardown_container("my-agent", False)
        assert result is False

    def test_teardown_container_generic_error(self):
        from cli.commands.teardown import _teardown_container

        with patch(
            "engine.deployers.docker_compose.DockerComposeDeployer",
            side_effect=Exception("boom"),
        ):
            result = _teardown_container("my-agent", False)
        assert result is False

    def test_teardown_container_runtime_error_json(self):
        from cli.commands.teardown import _teardown_container

        with patch(
            "engine.deployers.docker_compose.DockerComposeDeployer",
            side_effect=RuntimeError("no docker"),
        ):
            result = _teardown_container("my-agent", True)
        assert result is False

    def test_teardown_container_generic_error_json(self):
        from cli.commands.teardown import _teardown_container

        with patch(
            "engine.deployers.docker_compose.DockerComposeDeployer",
            side_effect=Exception("boom"),
        ):
            result = _teardown_container("my-agent", True)
        assert result is False

    def test_teardown_force_with_running_agent_json(self):
        state = {
            "agents": {
                "my-agent": {
                    "port": 8080,
                    "status": "running",
                    "endpoint_url": "http://localhost:8080",
                    "container_id": "abc123",
                    "container_name": "garden-my-agent",
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(state))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            with (
                patch(
                    "cli.commands.teardown.STATE_FILE",
                    state_file,
                ),
                patch(
                    "cli.commands.teardown.REGISTRY_DIR",
                    registry_dir,
                ),
                patch(
                    "cli.commands.teardown._teardown_container",
                    return_value=True,
                ),
            ):
                result = runner.invoke(
                    cli_app,
                    [
                        "teardown",
                        "my-agent",
                        "--force",
                        "--json",
                    ],
                )
            assert result.exit_code == 0
            out = json.loads(result.output)
            assert out["container_removed"] is True
