"""Unit tests for ``registry.model_lifecycle`` — Track G (#163).

Covers the diff/status derivation contract:

* New model in discovery → ``status="active"``, ``discovered_at`` set, audit
  ``model.added`` emitted.
* Existing model still present → ``last_seen_at`` updated, deprecated rows
  brought back to active when the upstream un-retires them.
* Existing model absent for the first time → ``status="deprecated"``,
  ``deprecated_at = now``, audit ``model.deprecated`` emitted.
* Deprecated model absent for ≥ ``RETIREMENT_GRACE_DAYS`` →
  ``status="retired"``, audit ``model.retired`` emitted.
* Per-provider discovery error does not blow up the sync.
* Manual ``deprecate()`` sets the replacement pointer + emits audit.

Tests run against SQLite in-memory; the audit service is reset per test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base, Model
from api.services.audit_service import AuditService
from engine.providers.discovery import DiscoveredModel, DiscoveryError, ProviderDiscovery
from registry.model_lifecycle import (
    ACTIVE,
    DEPRECATED,
    RETIRED,
    RETIREMENT_GRACE_DAYS,
    ModelLifecycleService,
)


def _eq_dt(a, b) -> bool:
    """Compare datetimes ignoring tz (SQLite drops tzinfo on round-trip)."""
    if a is None or b is None:
        return a is b
    return a.replace(tzinfo=None) == b.replace(tzinfo=None)


_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AuditService.reset()
    async with _SessionFactory() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── Stub discovery adapters ─────────────────────────────────────────────────


class _StaticDiscovery:
    """In-memory ``ProviderDiscovery`` for unit tests."""

    def __init__(self, provider_name: str, models: list[DiscoveredModel] | Exception) -> None:
        self.provider_name = provider_name
        self._models = models

    async def list_models(self) -> list[DiscoveredModel]:
        if isinstance(self._models, Exception):
            raise self._models
        return list(self._models)


def _model(id_: str, provider: str = "openai", **kwargs) -> DiscoveredModel:
    return DiscoveredModel(id=id_, name=id_, provider=provider, **kwargs)


# ─── Discovery contract ────────────────────────────────────────────────────


class TestDiscoveryProtocol:
    def test_static_discovery_satisfies_protocol(self) -> None:
        d = _StaticDiscovery("p", [])
        assert isinstance(d, ProviderDiscovery)


# ─── Sync — first run (everything is new) ──────────────────────────────────


class TestFirstSync:
    @pytest.mark.asyncio
    async def test_creates_active_rows(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService(actor="alice@x.io")
        d = _StaticDiscovery(
            "openai",
            [_model("gpt-4o", context_window=128_000), _model("gpt-4o-mini")],
        )
        result = await svc.sync_provider(session, provider_name="openai", discovery=d)
        await session.commit()

        assert sorted(result.added) == ["gpt-4o", "gpt-4o-mini"]
        assert result.seen == []
        assert result.deprecated == []

        rows = (await session.execute(select(Model))).scalars().all()
        assert len(rows) == 2
        for row in rows:
            assert row.status == ACTIVE
            assert row.discovered_at is not None
            assert row.last_seen_at is not None
            assert row.source == "discovery"

    @pytest.mark.asyncio
    async def test_emits_model_added_audit(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        d = _StaticDiscovery("openai", [_model("gpt-4o")])
        await svc.sync_provider(session, provider_name="openai", discovery=d)
        await session.commit()

        events, _ = await AuditService.list_events(action="model.added")
        assert len(events) == 1
        assert events[0].resource_type == "model"
        assert events[0].resource_name == "gpt-4o"
        assert events[0].details.get("provider") == "openai"


# ─── Sync — second run (existing models still present) ────────────────────


class TestStableSync:
    @pytest.mark.asyncio
    async def test_updates_last_seen(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        d = _StaticDiscovery("openai", [_model("gpt-4o")])
        ts1 = datetime(2026, 4, 1, tzinfo=UTC)
        await svc.sync_provider(session, provider_name="openai", discovery=d, now=ts1)
        await session.commit()

        ts2 = datetime(2026, 4, 2, tzinfo=UTC)
        result = await svc.sync_provider(session, provider_name="openai", discovery=d, now=ts2)
        await session.commit()
        assert result.added == []
        assert result.seen == ["gpt-4o"]

        row = (await session.execute(select(Model))).scalar_one()
        assert _eq_dt(row.last_seen_at, ts2)
        assert _eq_dt(row.discovered_at, ts1)


# ─── Sync — deprecation + retirement ───────────────────────────────────────


class TestDeprecationLifecycle:
    @pytest.mark.asyncio
    async def test_absent_model_becomes_deprecated(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        first = _StaticDiscovery("openai", [_model("gpt-3.5"), _model("gpt-4o")])
        ts1 = datetime(2026, 4, 1, tzinfo=UTC)
        await svc.sync_provider(session, provider_name="openai", discovery=first, now=ts1)
        await session.commit()

        # gpt-3.5 disappears.
        second = _StaticDiscovery("openai", [_model("gpt-4o")])
        ts2 = datetime(2026, 4, 5, tzinfo=UTC)
        result = await svc.sync_provider(
            session, provider_name="openai", discovery=second, now=ts2
        )
        await session.commit()
        assert result.deprecated == ["gpt-3.5"]

        rows = {r.name: r for r in (await session.execute(select(Model))).scalars()}
        assert rows["gpt-3.5"].status == DEPRECATED
        assert _eq_dt(rows["gpt-3.5"].deprecated_at, ts2)
        assert rows["gpt-4o"].status == ACTIVE

        events, _ = await AuditService.list_events(action="model.deprecated")
        assert len(events) == 1
        assert events[0].resource_name == "gpt-3.5"
        assert events[0].details.get("reason") == "absent_from_discovery"

    @pytest.mark.asyncio
    async def test_retires_after_grace_window(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        first = _StaticDiscovery("openai", [_model("gpt-3.5")])
        t0 = datetime(2026, 4, 1, tzinfo=UTC)
        await svc.sync_provider(session, provider_name="openai", discovery=first, now=t0)
        await session.commit()

        # Day 1 absence → deprecated.
        empty = _StaticDiscovery("openai", [])
        t1 = t0 + timedelta(days=1)
        await svc.sync_provider(session, provider_name="openai", discovery=empty, now=t1)
        await session.commit()
        rows = {r.name: r for r in (await session.execute(select(Model))).scalars()}
        assert rows["gpt-3.5"].status == DEPRECATED

        # Within grace window — still deprecated.
        t_within = t1 + timedelta(days=RETIREMENT_GRACE_DAYS - 1)
        result = await svc.sync_provider(
            session, provider_name="openai", discovery=empty, now=t_within
        )
        await session.commit()
        assert result.retired == []

        # After grace window — retired.
        t_after = t1 + timedelta(days=RETIREMENT_GRACE_DAYS + 1)
        result = await svc.sync_provider(
            session, provider_name="openai", discovery=empty, now=t_after
        )
        await session.commit()
        assert result.retired == ["gpt-3.5"]
        rows = {r.name: r for r in (await session.execute(select(Model))).scalars()}
        assert rows["gpt-3.5"].status == RETIRED

        events, _ = await AuditService.list_events(action="model.retired")
        assert len(events) == 1
        assert events[0].resource_name == "gpt-3.5"

    @pytest.mark.asyncio
    async def test_un_retire_brings_back_to_active(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        first = _StaticDiscovery("openai", [_model("foo")])
        t0 = datetime(2026, 4, 1, tzinfo=UTC)
        await svc.sync_provider(session, provider_name="openai", discovery=first, now=t0)
        # Disappear → deprecate.
        empty = _StaticDiscovery("openai", [])
        t1 = t0 + timedelta(days=1)
        await svc.sync_provider(session, provider_name="openai", discovery=empty, now=t1)
        await session.commit()
        # Re-appear.
        again = _StaticDiscovery("openai", [_model("foo")])
        t2 = t1 + timedelta(days=2)
        await svc.sync_provider(session, provider_name="openai", discovery=again, now=t2)
        await session.commit()
        row = (await session.execute(select(Model))).scalar_one()
        assert row.status == ACTIVE
        assert row.deprecated_at is None
        assert _eq_dt(row.last_seen_at, t2)


# ─── Sync — discovery failure isolation ───────────────────────────────────


class TestDiscoveryFailure:
    @pytest.mark.asyncio
    async def test_records_error_does_not_deprecate(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        ok = _StaticDiscovery("openai", [_model("gpt-4o")])
        t0 = datetime(2026, 4, 1, tzinfo=UTC)
        await svc.sync_provider(session, provider_name="openai", discovery=ok, now=t0)
        await session.commit()

        broken = _StaticDiscovery("openai", DiscoveryError("network down"))
        t1 = t0 + timedelta(days=1)
        result = await svc.sync_provider(session, provider_name="openai", discovery=broken, now=t1)
        await session.commit()

        assert result.error == "network down"
        # Existing row left alone.
        row = (await session.execute(select(Model))).scalar_one()
        assert row.status == ACTIVE

    @pytest.mark.asyncio
    async def test_multi_provider_isolation(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        broken = _StaticDiscovery("openai", DiscoveryError("boom"))
        ok = _StaticDiscovery("anthropic", [_model("claude-x", provider="anthropic")])
        result = await svc.sync(session, discoveries={"openai": broken, "anthropic": ok})
        await session.commit()

        names = {p.provider for p in result.providers}
        assert names == {"openai", "anthropic"}
        ok_row = (
            await session.execute(select(Model).where(Model.name == "claude-x"))
        ).scalar_one()
        assert ok_row.status == ACTIVE


# ─── Manual deprecate ──────────────────────────────────────────────────────


class TestManualDeprecate:
    @pytest.mark.asyncio
    async def test_deprecate_with_replacement(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        first = _StaticDiscovery("openai", [_model("old"), _model("new")])
        await svc.sync_provider(session, provider_name="openai", discovery=first)
        await session.commit()

        await svc.deprecate(
            session,
            model_name="old",
            replacement_name="new",
            actor="bob@x.io",
        )
        await session.commit()
        old = (await session.execute(select(Model).where(Model.name == "old"))).scalar_one()
        new = (await session.execute(select(Model).where(Model.name == "new"))).scalar_one()
        assert old.status == DEPRECATED
        assert old.deprecated_at is not None
        assert old.deprecation_replacement_id == new.id

        events, _ = await AuditService.list_events(action="model.deprecated")
        assert any(
            e.resource_name == "old" and e.details.get("reason") == "manual" for e in events
        )

    @pytest.mark.asyncio
    async def test_deprecate_unknown_model(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        with pytest.raises(LookupError, match="Model 'ghost' not found"):
            await svc.deprecate(session, model_name="ghost")

    @pytest.mark.asyncio
    async def test_deprecate_unknown_replacement(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        d = _StaticDiscovery("openai", [_model("only")])
        await svc.sync_provider(session, provider_name="openai", discovery=d)
        await session.commit()
        with pytest.raises(LookupError, match="Replacement model"):
            await svc.deprecate(session, model_name="only", replacement_name="ghost")


# ─── as_dict serialisation ─────────────────────────────────────────────────


class TestSerialisation:
    @pytest.mark.asyncio
    async def test_sync_result_as_dict(self, session: AsyncSession) -> None:
        svc = ModelLifecycleService()
        d = _StaticDiscovery("openai", [_model("gpt-4o")])
        result = await svc.sync(session, discoveries={"openai": d})
        await session.commit()
        payload = result.as_dict()
        assert "started_at" in payload
        assert "finished_at" in payload
        assert payload["totals"]["added"] == 1
        assert payload["providers"][0]["provider"] == "openai"
        assert payload["providers"][0]["added"] == ["gpt-4o"]
