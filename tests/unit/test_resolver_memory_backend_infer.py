"""P3 — infer the memory backend from an explicit backend_url scheme.

A user should be able to point an agent at a managed store with just
``memory.backend_url: redis://…`` (no separate ``memory.backend``).
"""

from __future__ import annotations

import pytest

from engine.config_parser import AgentConfig, DeployConfig, MemoryConfig, ModelConfig
from engine.resolver import _infer_memory_backend, resolve_dependencies


def _cfg(memory=None):
    return AgentConfig(
        name="x",
        version="1.0.0",
        team="t",
        owner="o@e.com",
        framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(cloud="aws"),
        memory=memory,
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("redis://h:6379", "redis"),
        ("rediss://cache.aws:6379", "redis"),
        ("postgresql://h/db", "postgresql"),
        ("postgres://h/db", "postgresql"),
        ("mysql://h/db", None),
        ("", None),
        (None, None),
    ],
)
def test_infer_memory_backend(url, expected):
    assert _infer_memory_backend(url) == expected


def test_backend_url_only_redis_infers_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    cfg = resolve_dependencies(
        _cfg(MemoryConfig(stores=[], backend_url="rediss://prod-cache.aws:6379")),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["MEMORY_BACKEND"] == "redis"
    assert cfg.deploy.env_vars["REDIS_URL"] == "rediss://prod-cache.aws:6379"


def test_backend_url_only_postgres_infers_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    cfg = resolve_dependencies(
        _cfg(MemoryConfig(stores=[], backend_url="postgresql://prod-db.aws:5432/mem")),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["MEMORY_BACKEND"] == "postgresql"
    assert cfg.deploy.env_vars["DATABASE_URL"] == "postgresql://prod-db.aws:5432/mem"


def test_explicit_backend_wins_over_url_scheme(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    cfg = resolve_dependencies(
        _cfg(MemoryConfig(stores=[], backend="postgresql", backend_url="postgresql://db/mem")),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["MEMORY_BACKEND"] == "postgresql"
    assert cfg.deploy.env_vars["DATABASE_URL"] == "postgresql://db/mem"
