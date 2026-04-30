"""Unit tests for the daily models-sync cron — issue #199.

Strategy:

* ``run_sync_once`` is the public entry point. We patch
  :func:`api.routes.models._build_discoveries` so the test can hand the
  service a deterministic dict of stub adapters, and we route the
  service's commit through an in-memory SQLite session factory.
* The audit-event side-effect is verified via the existing
  :class:`api.services.audit_service.AuditService` (in-memory store).
* The ``daily_sync_enabled()`` resolver is pure env-var logic and is
  unit-tested directly without I/O.
* The CLI ``sync-now`` command is exercised through Typer's ``CliRunner``
  with ``run_sync_once`` patched to a canned summary.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from api.models.database import Base
from api.services.audit_service import AuditService
from api.tasks.models_sync_cron import (
    SYNC_AUDIT_ACTION,
    daily_sync_enabled,
    run_sync_once,
)
from cli.commands.model import model_app
from engine.providers.discovery import DiscoveredModel, DiscoveryError

# ─── Test helpers ──────────────────────────────────────────────────────────


_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def _fresh_db():
    """Reset schema + audit before each test, drop after."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    AuditService.reset()
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@asynccontextmanager
async def _session_cm():
    async with _SessionFactory() as s:
        yield s


class _StaticDiscovery:
    """In-memory ``ProviderDiscovery`` for unit tests."""

    def __init__(
        self,
        provider_name: str,
        models: list[DiscoveredModel] | Exception,
    ) -> None:
        self.provider_name = provider_name
        self._models = models

    async def list_models(self) -> list[DiscoveredModel]:
        if isinstance(self._models, Exception):
            raise self._models
        return list(self._models)


def _model(id_: str, provider: str = "openai") -> DiscoveredModel:
    return DiscoveredModel(id=id_, name=id_, provider=provider)


# ─── daily_sync_enabled() ──────────────────────────────────────────────────


class TestDailySyncEnabled:
    def test_explicit_true(self, monkeypatch) -> None:
        monkeypatch.setenv("AGENTBREEDER_MODELS_DAILY_SYNC", "true")
        monkeypatch.setenv("AGENTBREEDER_INSTALL_MODE", "selfhost")
        assert daily_sync_enabled() is True

    def test_explicit_false_overrides_cloud(self, monkeypatch) -> None:
        monkeypatch.setenv("AGENTBREEDER_MODELS_DAILY_SYNC", "false")
        monkeypatch.setenv("AGENTBREEDER_INSTALL_MODE", "cloud")
        assert daily_sync_enabled() is False

    def test_cloud_default_on(self, monkeypatch) -> None:
        monkeypatch.delenv("AGENTBREEDER_MODELS_DAILY_SYNC", raising=False)
        monkeypatch.setenv("AGENTBREEDER_INSTALL_MODE", "cloud")
        assert daily_sync_enabled() is True

    def test_selfhost_default_off(self, monkeypatch) -> None:
        monkeypatch.delenv("AGENTBREEDER_MODELS_DAILY_SYNC", raising=False)
        monkeypatch.delenv("AGENTBREEDER_INSTALL_MODE", raising=False)
        assert daily_sync_enabled() is False


# ─── run_sync_once() — core sweep behaviour ────────────────────────────────


class TestRunSyncOnce:
    @pytest.mark.asyncio
    async def test_happy_path_reconciles_and_emits_audit(self, _fresh_db) -> None:
        discoveries = {
            "openai": _StaticDiscovery("openai", [_model("gpt-4o"), _model("gpt-4o-mini")]),
        }
        with (
            patch(
                "api.database.async_session",
                _session_cm,
            ),
            patch(
                "api.routes.models._build_discoveries",
                AsyncMock(return_value=discoveries),
            ),
        ):
            summary = await run_sync_once()

        # Two new rows added on a fresh DB.
        assert summary["totals"]["added"] == 2
        assert summary["totals"]["deprecated"] == 0
        assert summary["totals"]["retired"] == 0
        assert {p["provider"] for p in summary["providers"]} == {"openai"}

        # Audit event fired with the cron action.
        events, _ = await AuditService.list_events(action=SYNC_AUDIT_ACTION)
        assert len(events) == 1
        evt = events[0]
        assert evt.actor == "system:cron"
        assert evt.resource_type == "model"
        assert evt.details["added"] == 2
        assert evt.details["providers"] == ["openai"]

    @pytest.mark.asyncio
    async def test_empty_providers_short_circuits(self, _fresh_db) -> None:
        with (
            patch("api.database.async_session", _session_cm),
            patch(
                "api.routes.models._build_discoveries",
                AsyncMock(return_value={}),
            ),
        ):
            summary = await run_sync_once()

        assert summary["skipped_reason"] == "no-providers"
        assert summary["providers"] == []
        assert summary["totals"] == {"added": 0, "deprecated": 0, "retired": 0}

        # No audit event emitted on an empty sweep.
        events, _ = await AuditService.list_events(action=SYNC_AUDIT_ACTION)
        assert events == []

    @pytest.mark.asyncio
    async def test_single_provider_failure_does_not_kill_sweep(self, _fresh_db) -> None:
        discoveries = {
            "openai": _StaticDiscovery("openai", [_model("gpt-4o")]),
            "anthropic": _StaticDiscovery(
                "anthropic", DiscoveryError("anthropic 503: upstream timeout")
            ),
        }
        with (
            patch("api.database.async_session", _session_cm),
            patch(
                "api.routes.models._build_discoveries",
                AsyncMock(return_value=discoveries),
            ),
        ):
            summary = await run_sync_once()

        # Both providers appear in the summary; one with an error, one with results.
        per_provider = {p["provider"]: p for p in summary["providers"]}
        assert per_provider["anthropic"]["error"] is not None
        assert "upstream timeout" in per_provider["anthropic"]["error"]
        assert per_provider["openai"]["error"] is None
        assert per_provider["openai"]["added"] == ["gpt-4o"]

        # Totals reflect only the successful provider.
        assert summary["totals"]["added"] == 1

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_break_sync(self, _fresh_db) -> None:
        discoveries = {
            "openai": _StaticDiscovery("openai", [_model("gpt-4o")]),
        }
        with (
            patch("api.database.async_session", _session_cm),
            patch(
                "api.routes.models._build_discoveries",
                AsyncMock(return_value=discoveries),
            ),
            patch(
                "api.services.audit_service.AuditService.log_event",
                AsyncMock(side_effect=RuntimeError("audit store unavailable")),
            ),
        ):
            # Should NOT raise — audit emission is best-effort.
            summary = await run_sync_once()

        assert summary["totals"]["added"] == 1


# ─── start_background_task() — env-var gating ──────────────────────────────


class TestStartBackgroundTask:
    def test_disabled_returns_none(self, monkeypatch) -> None:
        from api.tasks import models_sync_cron as mod

        monkeypatch.setenv("AGENTBREEDER_MODELS_DAILY_SYNC", "false")
        monkeypatch.delenv("AGENTBREEDER_INSTALL_MODE", raising=False)

        async def _runner() -> object | None:
            return mod.start_background_task()

        result = asyncio.run(_runner())
        assert result is None

    def test_enabled_creates_task(self, monkeypatch) -> None:
        from api.tasks import models_sync_cron as mod

        monkeypatch.setenv("AGENTBREEDER_MODELS_DAILY_SYNC", "true")

        async def _runner() -> bool:
            task = mod.start_background_task()
            try:
                assert task is not None
                assert task.get_name() == "models-sync-cron"
                return True
            finally:
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass

        assert asyncio.run(_runner()) is True


# ─── CLI: agentbreeder model sync-now ──────────────────────────────────────


class TestSyncNowCli:
    def test_sync_now_human_output(self) -> None:
        canned = {
            "started_at": "2026-04-29T00:00:00+00:00",
            "finished_at": "2026-04-29T00:00:01+00:00",
            "duration_seconds": 1.0,
            "providers": [
                {
                    "provider": "openai",
                    "total_seen": 2,
                    "added": ["gpt-4o"],
                    "seen": ["gpt-4o-mini"],
                    "deprecated": [],
                    "retired": [],
                    "error": None,
                }
            ],
            "totals": {"added": 1, "deprecated": 0, "retired": 0},
        }
        with patch(
            "api.tasks.models_sync_cron.run_sync_once",
            AsyncMock(return_value=canned),
        ):
            result = CliRunner().invoke(model_app, ["sync-now"])
        assert result.exit_code == 0, result.output
        assert "Sync complete" in result.output
        assert "openai" in result.output

    def test_sync_now_skipped_no_providers(self) -> None:
        canned = {
            "started_at": "2026-04-29T00:00:00+00:00",
            "finished_at": "2026-04-29T00:00:00+00:00",
            "duration_seconds": 0.0,
            "providers": [],
            "totals": {"added": 0, "deprecated": 0, "retired": 0},
            "skipped_reason": "no-providers",
        }
        with patch(
            "api.tasks.models_sync_cron.run_sync_once",
            AsyncMock(return_value=canned),
        ):
            result = CliRunner().invoke(model_app, ["sync-now"])
        assert result.exit_code == 0, result.output
        assert "skipped" in result.output.lower()
        assert "no-providers" in result.output
