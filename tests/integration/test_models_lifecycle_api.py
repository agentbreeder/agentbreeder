"""Integration tests for ``/api/v1/models/sync`` and ``/deprecate`` — Track G (#163).

The discovery layer is fully mocked — the test patches
``api.routes.models._build_discoveries`` to return a static dict of
in-memory adapters. This keeps the test deterministic and offline.

Auth is wired by the ``_integration_auth`` fixture in
``tests/integration/conftest.py``. RBAC is checked by overriding
``api.middleware.rbac.require_role`` to a deny-all callable for the
viewer-role test, then restoring the override.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database import get_db
from api.main import app
from api.models.database import Base, Model
from api.services.audit_service import AuditService
from engine.providers.discovery import DiscoveredModel, DiscoveryError, ProviderDiscovery
from registry.model_lifecycle import RETIREMENT_GRACE_DAYS

# ─── Async sqlite test DB ──────────────────────────────────────────────────


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AuditService.reset()

    sessions: list[AsyncSession] = []

    async def _get_db_override():
        s = factory()
        sessions.append(s)
        try:
            yield s
        finally:
            await s.close()

    app.dependency_overrides[get_db] = _get_db_override
    yield factory
    app.dependency_overrides.pop(get_db, None)
    for s in sessions:
        await s.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ─── Stub discovery ────────────────────────────────────────────────────────


class _StaticDiscovery:
    def __init__(self, name: str, models: list[DiscoveredModel] | Exception) -> None:
        self.provider_name = name
        self._models = models

    async def list_models(self) -> list[DiscoveredModel]:
        if isinstance(self._models, Exception):
            raise self._models
        return list(self._models)


def _patch_discoveries(discoveries: dict[str, ProviderDiscovery]):
    """Patch the route's discovery-building helper."""

    async def _stub(_db, _requested):  # noqa: ANN001 — match router signature loosely
        return discoveries

    return patch("api.routes.models._build_discoveries", side_effect=_stub)


client = TestClient(app)


# ─── /sync ─────────────────────────────────────────────────────────────────


class TestSync:
    @pytest.mark.asyncio
    async def test_sync_creates_models_and_audit(self, async_session) -> None:
        adapters = {
            "openai": _StaticDiscovery(
                "openai",
                [
                    DiscoveredModel(
                        id="gpt-4o", name="gpt-4o", provider="openai", context_window=128_000
                    )
                ],
            )
        }
        with _patch_discoveries(adapters):
            resp = client.post("/api/v1/models/sync", json={})
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["totals"]["added"] == 1
        assert body["providers"][0]["provider"] == "openai"

        async with async_session() as s:
            rows = (await s.execute(select(Model))).scalars().all()
            assert {r.name for r in rows} == {"gpt-4o"}
            assert rows[0].status == "active"
            assert rows[0].source == "discovery"
            assert rows[0].discovered_at is not None

        events, _ = await AuditService.list_events(action="model.added")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_sync_with_no_adapters_returns_400(self, async_session) -> None:
        with _patch_discoveries({}):
            resp = client.post("/api/v1/models/sync", json={})
        assert resp.status_code == 400
        assert "providers to sync" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_sync_isolates_per_provider_errors(self, async_session) -> None:
        adapters = {
            "openai": _StaticDiscovery(
                "openai", [DiscoveredModel(id="m1", name="m1", provider="openai")]
            ),
            "google": _StaticDiscovery("google", DiscoveryError("boom")),
        }
        with _patch_discoveries(adapters):
            resp = client.post("/api/v1/models/sync", json={"providers": ["openai", "google"]})
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        provs = {p["provider"]: p for p in body["providers"]}
        assert provs["openai"]["error"] is None
        assert provs["google"]["error"] == "boom"

    @pytest.mark.asyncio
    async def test_sync_requires_deployer_role(self, async_session) -> None:
        # Override _integration_auth fixture's admin user with a viewer for
        # this test: replace get_current_user with one that returns viewer.
        from unittest.mock import MagicMock

        from api.auth import get_current_user
        from api.models.enums import UserRole

        viewer = MagicMock()
        viewer.id = uuid.uuid4()
        viewer.email = "viewer@x.io"
        viewer.role = UserRole.viewer
        viewer.team = "engineering"
        viewer.is_active = True

        async def _viewer():
            return viewer

        prev = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = _viewer
        try:
            with _patch_discoveries({}):
                resp = client.post("/api/v1/models/sync", json={})
            # Either 403 (RBAC) or 400 (no adapters) — but for viewers the
            # RBAC dependency must fire first. We accept 403.
            assert resp.status_code == 403, resp.text
        finally:
            if prev is None:
                app.dependency_overrides.pop(get_current_user, None)
            else:
                app.dependency_overrides[get_current_user] = prev


# ─── /deprecate ────────────────────────────────────────────────────────────


class TestDeprecate:
    @pytest.mark.asyncio
    async def test_deprecate_existing_model(self, async_session) -> None:
        adapters = {
            "openai": _StaticDiscovery(
                "openai",
                [
                    DiscoveredModel(id="old", name="old", provider="openai"),
                    DiscoveredModel(id="new", name="new", provider="openai"),
                ],
            )
        }
        with _patch_discoveries(adapters):
            client.post("/api/v1/models/sync", json={})

        resp = client.post("/api/v1/models/old/deprecate", json={"replacement": "new"})
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "deprecated"
        assert body["replacement"] == "new"
        assert body["deprecated_at"] is not None

        async with async_session() as s:
            row = (await s.execute(select(Model).where(Model.name == "old"))).scalar_one()
            assert row.status == "deprecated"
            assert row.deprecation_replacement_id is not None

        events, _ = await AuditService.list_events(action="model.deprecated")
        names = {e.resource_name for e in events}
        assert "old" in names

    @pytest.mark.asyncio
    async def test_deprecate_unknown_returns_404(self, async_session) -> None:
        resp = client.post("/api/v1/models/ghost/deprecate", json={})
        assert resp.status_code == 404


# ─── Schema surface ────────────────────────────────────────────────────────


class TestModelResponseShape:
    @pytest.mark.asyncio
    async def test_lifecycle_fields_on_list(self, async_session) -> None:
        adapters = {
            "openai": _StaticDiscovery(
                "openai", [DiscoveredModel(id="m1", name="m1", provider="openai")]
            )
        }
        with _patch_discoveries(adapters):
            client.post("/api/v1/models/sync", json={})
        resp = client.get("/api/v1/models")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        assert len(rows) == 1
        row = rows[0]
        for key in (
            "discovered_at",
            "last_seen_at",
            "deprecated_at",
            "deprecation_replacement_id",
            "status",
        ):
            assert key in row
        assert row["status"] == "active"


# ─── grace-period semantics through the API ────────────────────────────────


def test_grace_window_constant_exposed() -> None:
    assert RETIREMENT_GRACE_DAYS == 30
