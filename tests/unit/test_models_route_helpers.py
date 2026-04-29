"""Unit tests for ``api.routes.models._build_discoveries`` — Track G (#163).

Exercises the resolution logic that decides which discovery adapters to
build for a sync. Discovery itself is fully mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes.models import _build_discoveries
from engine.providers.discovery import (
    AnthropicDiscovery,
    DiscoveryError,
    OpenAICompatibleDiscovery,
)


@pytest.mark.asyncio
async def test_explicit_provider_list_short_circuits(monkeypatch) -> None:
    """When the caller passes an explicit provider list, env scanning is skipped."""
    db = MagicMock()
    db.execute = AsyncMock()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    out = await _build_discoveries(db, ["anthropic"])
    assert "anthropic" in out
    assert isinstance(out["anthropic"], AnthropicDiscovery)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_explicit_provider_skipped(monkeypatch) -> None:
    """Bad provider names just get skipped, never raise."""
    db = MagicMock()
    db.execute = AsyncMock()
    out = await _build_discoveries(db, ["not-a-real-provider"])
    assert out == {}


@pytest.mark.asyncio
async def test_default_includes_anthropic_without_api_key(monkeypatch) -> None:
    """Anthropic discovery is curated, so it's included even without env keys.

    Other providers (openai, google, catalog) only appear if their api-key
    env var is set.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # Clear *every* catalog api-key env var so only first-class detection matters.
    from engine.providers.catalog import list_entries

    for entry in list_entries().values():
        monkeypatch.delenv(entry.api_key_env, raising=False)

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))
    out = await _build_discoveries(db, [])
    assert "anthropic" in out
    assert isinstance(out["anthropic"], AnthropicDiscovery)


@pytest.mark.asyncio
async def test_default_picks_up_catalog_with_env_var(monkeypatch) -> None:
    """A catalog provider whose env var is set is auto-included."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))
    out = await _build_discoveries(db, [])
    assert "groq" in out
    assert isinstance(out["groq"], OpenAICompatibleDiscovery)


@pytest.mark.asyncio
async def test_db_provider_with_base_url_picked_up(monkeypatch) -> None:
    """A custom provider in the DB with a base_url is included."""
    # Clear all env-based detection.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from engine.providers.catalog import list_entries

    for entry in list_entries().values():
        monkeypatch.delenv(entry.api_key_env, raising=False)

    fake_provider = MagicMock()
    fake_provider.name = "byo-vllm"
    fake_provider.base_url = "https://vllm.example.com/v1"
    fake_provider.is_enabled = True

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [fake_provider]))
    )
    out = await _build_discoveries(db, [])
    assert "byo-vllm" in out
    assert isinstance(out["byo-vllm"], OpenAICompatibleDiscovery)


@pytest.mark.asyncio
async def test_db_provider_failure_is_swallowed(monkeypatch) -> None:
    """A DB row that can't resolve a discovery doesn't blow up the build."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from engine.providers.catalog import list_entries

    for entry in list_entries().values():
        monkeypatch.delenv(entry.api_key_env, raising=False)

    bad = MagicMock()
    bad.name = "ghost"
    bad.base_url = None
    bad.is_enabled = True

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [bad])))
    # Should not raise; should return an empty dict (anthropic is gated on env above).
    out = await _build_discoveries(db, [])
    assert "ghost" not in out


@pytest.mark.asyncio
async def test_explicit_unknown_provider_logs_warning(monkeypatch, caplog) -> None:
    """Unknown providers in the explicit list log a warning instead of raising."""
    db = MagicMock()
    db.execute = AsyncMock()
    with caplog.at_level("WARNING"):
        out = await _build_discoveries(db, ["totally-fake-provider"])
    assert out == {}
    assert any("totally-fake-provider" in rec.message for rec in caplog.records)


def test_discovery_error_is_provider_error_subclass() -> None:
    """Sanity check the public surface of DiscoveryError."""
    assert issubclass(DiscoveryError, Exception)
