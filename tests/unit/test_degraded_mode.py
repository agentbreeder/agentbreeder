"""Tests for engine.observability.degraded_mode."""

from __future__ import annotations

import logging

import pytest

from engine.observability.degraded_mode import (
    DegradedFlag,
    clear_degraded_state,
    warn_once,
)


@pytest.fixture(autouse=True)
def reset_state():
    clear_degraded_state()
    yield
    clear_degraded_state()


def test_warn_once_logs_first_occurrence(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    matches = [r for r in caplog.records if "rag.embedding" in r.message]
    assert len(matches) == 1


def test_warn_once_dedupes_same_pair(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    warn_once("rag.embedding", "openai-no-api-key")
    warn_once("rag.embedding", "openai-no-api-key")
    matches = [r for r in caplog.records if "rag.embedding" in r.message]
    assert len(matches) == 1


def test_warn_once_logs_distinct_reasons(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    warn_once("rag.embedding", "ollama-unreachable")
    warn_once("provider.fallback", "anthropic-429")
    matches = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(matches) == 3


def test_warn_once_includes_extras(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key", extra={"model": "text-embed-3"})
    record = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert record.component == "rag.embedding"
    assert record.reason == "openai-no-api-key"
    assert record.model == "text-embed-3"


def test_degraded_flag_starts_false() -> None:
    flag = DegradedFlag()
    assert flag.is_degraded is False
    assert flag.first_reason is None


def test_degraded_flag_records_first_reason() -> None:
    flag = DegradedFlag()
    flag.mark("openai-no-api-key")
    assert flag.is_degraded is True
    assert flag.first_reason == "openai-no-api-key"


def test_degraded_flag_keeps_first_reason() -> None:
    flag = DegradedFlag()
    flag.mark("openai-no-api-key")
    flag.mark("ollama-unreachable")
    assert flag.is_degraded is True
    assert flag.first_reason == "openai-no-api-key"


def test_clear_state_resets_dedup(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    clear_degraded_state()
    warn_once("rag.embedding", "openai-no-api-key")
    matches = [r for r in caplog.records if "rag.embedding" in r.message]
    assert len(matches) == 2
