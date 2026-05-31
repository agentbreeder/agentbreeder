"""TDD tests for Task 6 — explicit backend_url contract in resolve_dependencies.

Explicit agent.yaml backend_url fields WIN over host-env scraping.
Host-env (REDIS_URL, DATABASE_URL, NEO4J_URL) is only forwarded when
AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1 is set (local docker compose dev).
"""

from engine.config_parser import (
    AgentConfig,
    DeployConfig,
    KnowledgeBaseRef,
    MemoryConfig,
    ModelConfig,
)
from engine.resolver import resolve_dependencies


def _cfg(memory=None, kbs=None):
    return AgentConfig(
        name="x",
        version="1.0.0",
        team="t",
        owner="o@e.com",
        framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(cloud="aws"),
        memory=memory,
        knowledge_bases=kbs or [],
    )


def test_explicit_memory_backend_url_is_injected(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    cfg = resolve_dependencies(
        _cfg(
            memory=MemoryConfig(
                stores=["mem/s"],
                backend="redis",
                backend_url="redis://prod-cache:6379",
            )
        ),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["REDIS_URL"] == "redis://prod-cache:6379"


def test_local_redis_is_not_scraped_without_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    cfg = resolve_dependencies(
        _cfg(memory=MemoryConfig(stores=["mem/s"], backend="redis")),
        project_root=tmp_path,
    )
    assert "REDIS_URL" not in cfg.deploy.env_vars


def test_local_redis_scraped_when_flag_set(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", "1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    cfg = resolve_dependencies(
        _cfg(memory=MemoryConfig(stores=["mem/s"], backend="redis")),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["REDIS_URL"] == "redis://localhost:6379"


def test_kb_backend_url_exposed_as_pgvector_dsn(tmp_path):
    cfg = resolve_dependencies(
        _cfg(kbs=[KnowledgeBaseRef(ref="kb/docs", backend_url="postgresql://pg/db")]),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["KB_PGVECTOR_DSN"] == "postgresql://pg/db"


def test_local_postgresql_is_not_scraped_without_flag(monkeypatch, tmp_path):
    from engine.config_parser import MemoryConfig

    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    cfg = resolve_dependencies(
        _cfg(memory=MemoryConfig(stores=["mem/s"], backend="postgresql")),
        project_root=tmp_path,
    )
    assert "DATABASE_URL" not in cfg.deploy.env_vars


def test_explicit_memory_backend_url_no_stray_local_injection(monkeypatch, tmp_path):
    """When an explicit backend_url is set, no stray host-env URL keys leak in."""
    from engine.config_parser import MemoryConfig

    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    cfg = resolve_dependencies(
        _cfg(
            memory=MemoryConfig(
                stores=["mem/s"],
                backend="redis",
                backend_url="redis://prod-cache:6379",
            )
        ),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["REDIS_URL"] == "redis://prod-cache:6379"
    url_keys = [k for k in cfg.deploy.env_vars if k.endswith("_URL") or k.endswith("URL")]
    assert url_keys == ["REDIS_URL"]


def test_localhost_url_on_cloud_warns(monkeypatch, tmp_path, caplog):
    import logging

    from engine.config_parser import MemoryConfig

    caplog.set_level(logging.WARNING, logger="engine.resolver")
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    resolve_dependencies(
        _cfg(
            memory=MemoryConfig(
                stores=["m"],
                backend="redis",
                backend_url="redis://localhost:6379",
            )
        ),
        project_root=tmp_path,
    )
    assert "localhost" in caplog.text.lower()
