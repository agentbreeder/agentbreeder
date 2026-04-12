"""Coverage boost tests — targets 0% and low-coverage modules.

Covers:
  - api/models/tracing.py, teams.py, costs.py, cost_schemas.py, audit.py  (0%)
  - api/middleware/rbac.py  (0%)
  - api/auth.py  (72% → get_optional_user)
  - engine/runtimes/__init__.py  (79% → KeyError path)
  - engine/runtimes/templates/_tracing.py  (50% → init_tracing branches)
  - registry/mcp_servers.py  (77% → SSE test_connection / discover_tools)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ─────────────────────────────────────────────────────────────
# DB engine shared by registry tests
# ─────────────────────────────────────────────────────────────
from api.models.database import Base

_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionFactory() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ─────────────────────────────────────────────────────────────
# api/models — import + instantiate to reach module-level code
# ─────────────────────────────────────────────────────────────


class TestModelImports:
    """Just importing + instantiating these models covers the 0% module-level code."""

    def test_trace_model_columns(self):
        from api.models.tracing import Span, Trace

        assert Trace.__tablename__ == "traces"
        assert Span.__tablename__ == "spans"

    def test_team_models_columns(self):
        from api.models.teams import Team, TeamApiKey, TeamMembership

        assert Team.__tablename__ == "teams"
        assert TeamMembership.__tablename__ == "team_memberships"
        assert TeamApiKey.__tablename__ == "team_api_keys"

    def test_cost_model_columns(self):
        from api.models.costs import Budget, CostEvent

        assert CostEvent.__tablename__ == "cost_events"
        assert Budget.__tablename__ == "budgets"

    def test_audit_models_columns(self):
        from api.models.audit import AuditEvent, ResourceDependency

        assert AuditEvent.__tablename__ == "audit_events"
        assert ResourceDependency.__tablename__ == "resource_dependencies"


# ─────────────────────────────────────────────────────────────
# api/models/cost_schemas.py — Pydantic schema instantiation
# ─────────────────────────────────────────────────────────────


class TestCostSchemas:
    def test_cost_event_create(self):
        from api.models.cost_schemas import CostEventCreate

        obj = CostEventCreate(
            agent_name="my-agent",
            team="eng",
            model_name="claude-3",
            provider="anthropic",
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
            cost_usd=0.05,
            request_type="chat",
        )
        assert obj.agent_name == "my-agent"

    def test_cost_event_response(self):
        from api.models.cost_schemas import CostEventResponse

        obj = CostEventResponse(
            id=str(uuid.uuid4()),
            agent_name="a",
            team="t",
            model_name="m",
            provider="p",
            input_tokens=1,
            output_tokens=2,
            total_tokens=3,
            cost_usd=0.01,
            request_type="chat",
            created_at="2026-04-11T00:00:00Z",
        )
        assert obj.total_tokens == 3

    def test_cost_summary(self):
        from api.models.cost_schemas import CostSummary

        obj = CostSummary(total_cost=1.23, request_count=10, total_tokens=5000, period="30d")
        assert obj.total_cost == 1.23

    def test_cost_breakdown(self):
        from api.models.cost_schemas import CostBreakdown, CostBreakdownItem

        item = CostBreakdownItem(name="gpt-4", cost=0.5, tokens=1000, requests=3)
        breakdown = CostBreakdown(by_model=[item])
        assert len(breakdown.by_model) == 1

    def test_cost_trend(self):
        from api.models.cost_schemas import CostTrendResponse, DailyCostPoint

        pt = DailyCostPoint(date="2026-04-01", cost=0.1, tokens=500, requests=2)
        trend = CostTrendResponse(points=[pt], total_cost=0.1)
        assert len(trend.points) == 1

    def test_budget_schemas(self):
        from api.models.cost_schemas import BudgetCreate, BudgetResponse, BudgetUpdate

        create = BudgetCreate(team="eng", monthly_limit_usd=100.0)
        assert create.team == "eng"

        update = BudgetUpdate(monthly_limit_usd=200.0)
        assert update.monthly_limit_usd == 200.0

        resp = BudgetResponse(
            id=str(uuid.uuid4()),
            team="eng",
            monthly_limit_usd=100.0,
            alert_threshold_pct=80.0,
            current_month_spend=12.0,
            pct_used=12.0,
            is_exceeded=False,
            created_at="2026-04-11T00:00:00Z",
            updated_at="2026-04-11T00:00:00Z",
        )
        assert resp.is_exceeded is False

    def test_cost_comparison_schemas(self):
        from api.models.cost_schemas import CostComparisonRequest, CostComparisonResponse

        req = CostComparisonRequest(model_a="gpt-4", model_b="claude-3")
        assert req.model_a == "gpt-4"

        resp = CostComparisonResponse(
            model_a="gpt-4",
            model_b="claude-3",
            model_a_cost=0.02,
            model_b_cost=0.01,
            savings_pct=50.0,
            sample_tokens=1_000_000,
        )
        assert resp.savings_pct == 50.0


# ─────────────────────────────────────────────────────────────
# api/middleware/rbac.py
# ─────────────────────────────────────────────────────────────


class TestRequireRole:
    """Tests for the require_role dependency factory."""

    def _make_user(self, user_id: str | None = None):
        user = MagicMock()
        user.id = uuid.UUID(user_id) if user_id else uuid.uuid4()
        user.role = "viewer"
        return user

    @pytest.mark.asyncio
    async def test_invalid_role_raises_500(self):
        from fastapi import HTTPException

        from api.middleware.rbac import require_role

        checker = require_role("supermaster")
        user = self._make_user()
        with pytest.raises(HTTPException) as exc_info:
            await checker(user=user)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_user_not_in_team_raises_403(self):
        from fastapi import HTTPException

        from api.middleware.rbac import require_role

        checker = require_role("admin", resource_team="some-team")
        user = self._make_user()

        with patch(
            "api.middleware.rbac.TeamService.get_user_role_in_team",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await checker(user=user)
        assert exc_info.value.status_code == 403
        assert "not a member" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_insufficient_role_in_team_raises_403(self):
        from fastapi import HTTPException

        from api.middleware.rbac import require_role

        checker = require_role("admin", resource_team="some-team")
        user = self._make_user()

        with patch(
            "api.middleware.rbac.TeamService.get_user_role_in_team",
            new=AsyncMock(return_value="viewer"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await checker(user=user)
        assert exc_info.value.status_code == 403
        assert "admin" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sufficient_role_in_team_returns_user(self):
        from api.middleware.rbac import require_role

        checker = require_role("viewer", resource_team="some-team")
        user = self._make_user()

        with patch(
            "api.middleware.rbac.TeamService.get_user_role_in_team",
            new=AsyncMock(return_value="admin"),
        ):
            result = await checker(user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_no_team_sufficient_global_role_returns_user(self):
        from api.middleware.rbac import require_role
        from api.services.team_service import MembershipData

        checker = require_role("viewer")
        user = self._make_user()
        uid = str(user.id)

        m = MembershipData(
            team_id="t1",
            user_id=uid,
            user_email="u@example.com",
            user_name="User",
            role="admin",
        )

        with patch("api.middleware.rbac.TeamService._memberships", {"m1": m}):
            result = await checker(user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_no_team_insufficient_global_role_raises_403(self):
        from fastapi import HTTPException

        from api.middleware.rbac import require_role

        checker = require_role("admin")
        user = self._make_user()

        with patch("api.middleware.rbac.TeamService._memberships", {}):
            with pytest.raises(HTTPException) as exc_info:
                await checker(user=user)
        assert exc_info.value.status_code == 403


class TestGetUserTeamRole:
    def _make_user(self, is_admin: bool = False):
        user = MagicMock()
        user.id = uuid.uuid4()
        user.role = "admin" if is_admin else "viewer"
        return user

    @pytest.mark.asyncio
    async def test_member_returns_role(self):
        from api.middleware.rbac import get_user_team_role

        user = self._make_user()
        with patch(
            "api.middleware.rbac.TeamService.get_user_role_in_team",
            new=AsyncMock(return_value="deployer"),
        ):
            role = await get_user_team_role(team_id="t1", user=user)
        assert role == "deployer"

    @pytest.mark.asyncio
    async def test_non_member_raises_403(self):
        from fastapi import HTTPException

        from api.middleware.rbac import get_user_team_role

        user = self._make_user(is_admin=False)
        # Override role attr to not be "admin"
        user.role = "viewer"
        with patch(
            "api.middleware.rbac.TeamService.get_user_role_in_team",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_user_team_role(team_id="t1", user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_platform_admin_non_member_returns_admin(self):
        from api.middleware.rbac import get_user_team_role

        user = self._make_user(is_admin=True)
        with patch(
            "api.middleware.rbac.TeamService.get_user_role_in_team",
            new=AsyncMock(return_value=None),
        ):
            role = await get_user_team_role(team_id="t1", user=user)
        assert role == "admin"


# ─────────────────────────────────────────────────────────────
# api/auth.py — get_optional_user (lines 53-60)
# ─────────────────────────────────────────────────────────────


class TestGetOptionalUser:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_credentials(self):
        from api.auth import get_optional_user

        db = AsyncMock()
        result = await get_optional_user(credentials=None, db=db)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_invalid_token(self):
        from api.auth import get_optional_user

        creds = MagicMock()
        creds.credentials = "bad.token.here"
        db = AsyncMock()

        with patch("api.auth.decode_access_token", return_value=None):
            result = await get_optional_user(credentials=creds, db=db)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_user_when_valid_token(self):
        from api.auth import get_optional_user

        user_id = uuid.uuid4()
        creds = MagicMock()
        creds.credentials = "valid.token"
        db = AsyncMock()
        mock_user = MagicMock()

        with patch("api.auth.decode_access_token", return_value={"sub": str(user_id)}):
            with patch("api.auth.get_user_by_id", new=AsyncMock(return_value=mock_user)):
                result = await get_optional_user(credentials=creds, db=db)
        assert result is mock_user


# ─────────────────────────────────────────────────────────────
# engine/runtimes/__init__.py — unsupported framework path
# ─────────────────────────────────────────────────────────────


class TestGetRuntime:
    def test_all_frameworks_resolve(self):
        from engine.config_parser import FrameworkType
        from engine.runtimes import get_runtime

        for fw in FrameworkType:
            runtime = get_runtime(fw)
            assert runtime is not None

    def test_unsupported_framework_raises_key_error(self):
        import engine.runtimes as rt_module
        from engine.config_parser import FrameworkType

        # Swap RUNTIMES for an empty dict to trigger the None branch
        original = rt_module.RUNTIMES
        rt_module.RUNTIMES = {}
        try:
            with pytest.raises(KeyError) as exc_info:
                rt_module.get_runtime(FrameworkType.langgraph)
            assert "not yet supported" in str(exc_info.value)
        finally:
            rt_module.RUNTIMES = original


# ─────────────────────────────────────────────────────────────
# engine/runtimes/templates/_tracing.py — init_tracing branches
# ─────────────────────────────────────────────────────────────


class TestInitTracing:
    def test_no_endpoint_returns_noop_tracer(self):
        import importlib
        import sys

        # Remove from cache to get fresh import
        if "engine.runtimes.templates._tracing" in sys.modules:
            del sys.modules["engine.runtimes.templates._tracing"]
        if "_tracing" in sys.modules:
            del sys.modules["_tracing"]

        # Simulate the file standalone (it's a template — not in a package)
        import os

        spec_path = str(
            Path(__file__).resolve().parent.parent.parent
            / "engine"
            / "runtimes"
            / "templates"
            / "_tracing.py"
        )
        import importlib.util

        spec = importlib.util.spec_from_file_location("_tracing", spec_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENTELEMETRY_ENDPOINT", None)
            tracer = mod.init_tracing()

        assert isinstance(tracer, mod._NoopTracer)

    def test_endpoint_set_import_error_returns_noop(self):
        import importlib.util
        import os

        spec_path = str(
            Path(__file__).resolve().parent.parent.parent
            / "engine"
            / "runtimes"
            / "templates"
            / "_tracing.py"
        )
        spec = importlib.util.spec_from_file_location("_tracing2", spec_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch.dict(os.environ, {"OPENTELEMETRY_ENDPOINT": "http://otel:4317"}):
            with patch("builtins.__import__", side_effect=ImportError("no otel")):
                tracer = mod.init_tracing()

        assert isinstance(tracer, mod._NoopTracer)

    def test_endpoint_set_exception_returns_noop(self):
        import importlib.util
        import os

        spec_path = str(
            Path(__file__).resolve().parent.parent.parent
            / "engine"
            / "runtimes"
            / "templates"
            / "_tracing.py"
        )
        spec = importlib.util.spec_from_file_location("_tracing3", spec_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch.dict(os.environ, {"OPENTELEMETRY_ENDPOINT": "http://otel:4317"}):
            # Make the OTel import succeed but TracerProvider raise
            mock_trace = MagicMock()
            mock_resource_cls = MagicMock()
            mock_resource_cls.create.side_effect = RuntimeError("otel boom")

            fake_modules = {
                "opentelemetry": MagicMock(trace=mock_trace),
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(),
            }

            import builtins

            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name in fake_modules:
                    return fake_modules[name]
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                tracer = mod.init_tracing()

        assert isinstance(tracer, mod._NoopTracer)

    def test_noop_span_protocol(self):
        import importlib.util

        spec_path = str(
            Path(__file__).resolve().parent.parent.parent
            / "engine"
            / "runtimes"
            / "templates"
            / "_tracing.py"
        )
        spec = importlib.util.spec_from_file_location("_tracing4", spec_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        span = mod._NoopSpan()
        span.set_attribute("key", "val")
        span.record_exception(ValueError("x"))
        span.set_status("ok")
        with span as s:
            assert s is span

    def test_noop_tracer_methods(self):
        import importlib.util

        spec_path = str(
            Path(__file__).resolve().parent.parent.parent
            / "engine"
            / "runtimes"
            / "templates"
            / "_tracing.py"
        )
        spec = importlib.util.spec_from_file_location("_tracing5", spec_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        tracer = mod._NoopTracer()
        span1 = tracer.start_as_current_span("foo")
        span2 = tracer.start_span("bar")
        assert isinstance(span1, mod._NoopSpan)
        assert isinstance(span2, mod._NoopSpan)


# ─────────────────────────────────────────────────────────────
# registry/mcp_servers.py — SSE/HTTP test_connection & discover_tools
# ─────────────────────────────────────────────────────────────


class TestMcpServerSSEConnection:
    """Cover the HTTP/SSE transport branches (lines 123-143)."""

    @pytest.mark.asyncio
    async def test_sse_connection_success(self, session: AsyncSession) -> None:
        from registry.mcp_servers import McpServerRegistry

        server = await McpServerRegistry.create(
            session, name="sse-ok", endpoint="http://sse-server", transport="sse"
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await McpServerRegistry.test_connection(session, str(server.id))

        assert result["success"] is True
        assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_sse_connection_http_error(self, session: AsyncSession) -> None:
        from registry.mcp_servers import McpServerRegistry

        server = await McpServerRegistry.create(
            session, name="sse-err", endpoint="http://sse-server", transport="sse"
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await McpServerRegistry.test_connection(session, str(server.id))

        assert result["success"] is False
        assert "503" in result["error"]

    @pytest.mark.asyncio
    async def test_sse_connection_network_exception(self, session: AsyncSession) -> None:
        from registry.mcp_servers import McpServerRegistry

        server = await McpServerRegistry.create(
            session, name="sse-exc", endpoint="http://sse-server", transport="sse"
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await McpServerRegistry.test_connection(session, str(server.id))

        assert result["success"] is False
        assert "refused" in result["error"]


class TestMcpServerSSEDiscoverTools:
    """Cover the HTTP/SSE discover_tools branch (lines 166-191)."""

    @pytest.mark.asyncio
    async def test_discover_sse_success(self, session: AsyncSession) -> None:
        from registry.mcp_servers import McpServerRegistry

        server = await McpServerRegistry.create(
            session, name="disc-sse", endpoint="http://mcp-server", transport="sse"
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(
            return_value={
                "result": {
                    "tools": [
                        {"name": "search", "description": "Search tool", "inputSchema": {}},
                        {"name": "fetch", "description": "Fetch tool", "inputSchema": {}},
                    ]
                }
            }
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await McpServerRegistry.discover_tools(session, str(server.id))

        assert result["total"] == 2
        assert result["tools"][0]["name"] == "search"
        assert result["tools"][1]["name"] == "fetch"

    @pytest.mark.asyncio
    async def test_discover_sse_http_error_falls_back(self, session: AsyncSession) -> None:
        from registry.mcp_servers import McpServerRegistry

        server = await McpServerRegistry.create(
            session, name="disc-fb", endpoint="http://mcp-server", transport="sse"
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await McpServerRegistry.discover_tools(session, str(server.id))

        # Falls back to placeholder tools
        assert result["total"] == 2
        assert "disc-fb-search" in result["tools"][0]["name"]

    @pytest.mark.asyncio
    async def test_discover_sse_exception_falls_back(self, session: AsyncSession) -> None:
        from registry.mcp_servers import McpServerRegistry

        server = await McpServerRegistry.create(
            session, name="disc-exc", endpoint="http://mcp-server", transport="sse"
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=ConnectionError("unreachable"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await McpServerRegistry.discover_tools(session, str(server.id))

        # Falls back to placeholder tools
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_discover_streamable_http_transport(self, session: AsyncSession) -> None:
        from registry.mcp_servers import McpServerRegistry

        server = await McpServerRegistry.create(
            session,
            name="disc-stream",
            endpoint="http://mcp-stream",
            transport="streamable_http",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(
            return_value={"result": {"tools": [{"name": "t1", "description": "d1"}]}}
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await McpServerRegistry.discover_tools(session, str(server.id))

        assert result["total"] == 1
