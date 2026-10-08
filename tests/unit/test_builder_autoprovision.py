"""Deploy pipeline auto-provision hook — DeployEngine._auto_provision_data_backends.

Verifies the seam that, between infra-provision and deploy, provisions a
managed Postgres or Redis for ``memory`` declared WITHOUT an explicit
``backend_url`` and injects the connection env (``DATABASE_URL`` /
``REDIS_URL`` + ``MEMORY_BACKEND``) into ``deploy.env_vars`` so the container
reaches it. The cloud provisioner + URL resolvers are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from engine.builder import DeployEngine
from engine.config_parser import (
    AgentConfig,
    CloudType,
    DeployConfig,
    MemoryConfig,
    ModelConfig,
)
from engine.provisioners.state import InfraState

_PG_MEMORY = MemoryConfig(backend="postgresql")


def _cfg(
    *,
    cloud: CloudType = CloudType.aws,
    memory: MemoryConfig | None = _PG_MEMORY,
    env_vars: dict[str, str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        name="demo",
        version="1.0.0",
        team="t",
        owner="a@b.com",
        framework="langgraph",
        model=ModelConfig(primary="gpt-4o"),
        memory=memory,
        deploy=DeployConfig(
            cloud=cloud,
            env_vars=env_vars
            or {"AWS_VPC_SUBNETS": "subnet-a", "AWS_SECURITY_GROUPS": "sg-agent"},
        ),
    )


def _state() -> InfraState:
    return InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="test",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={"rds": {"endpoint": "demo.rds.amazonaws.com", "secret_arn": "arn:x"}},
    )


def _redis_state() -> InfraState:
    return InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="test",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "elasticache": {"endpoint": "demo.cache.amazonaws.com", "port": 6379},
            "security_groups": {"redis_sg_id": "sg-redis"},
        },
    )


async def test_memory_postgres_injects_database_url_and_backend() -> None:
    cfg = _cfg()
    prov = MagicMock()
    prov.provision_data_backend = AsyncMock(return_value=_state())
    with (
        patch("engine.builder.provisioner_for", return_value=prov),
        patch(
            "engine.builder.resolve_pgvector_dsn",
            AsyncMock(return_value="postgresql://u:p@demo.rds.amazonaws.com:5432/agentbreeder"),
        ),
    ):
        await DeployEngine()._auto_provision_data_backends(cfg)

    prov.provision_data_backend.assert_awaited_once()
    assert prov.provision_data_backend.await_args.args[0].engine == "postgres"
    assert (
        cfg.deploy.env_vars["DATABASE_URL"]
        == "postgresql://u:p@demo.rds.amazonaws.com:5432/agentbreeder"
    )
    assert cfg.deploy.env_vars["MEMORY_BACKEND"] == "postgresql"


async def test_persists_infra_state_to_project_dir(tmp_path) -> None:
    cfg = _cfg()
    prov = MagicMock()
    prov.provision_data_backend = AsyncMock(return_value=_state())
    with (
        patch("engine.builder.provisioner_for", return_value=prov),
        patch("engine.builder.resolve_pgvector_dsn", AsyncMock(return_value="postgresql://x")),
    ):
        await DeployEngine()._auto_provision_data_backends(cfg, tmp_path)

    saved = InfraState.load(tmp_path / ".agentbreeder" / "infra-state.json")
    assert saved.cloud == "aws"
    assert saved.resources["rds"]["endpoint"] == "demo.rds.amazonaws.com"


async def test_no_state_file_without_project_dir() -> None:
    """Direct calls without a project dir (e.g. unit drivers) must not crash."""
    cfg = _cfg()
    prov = MagicMock()
    prov.provision_data_backend = AsyncMock(return_value=_state())
    with (
        patch("engine.builder.provisioner_for", return_value=prov),
        patch("engine.builder.resolve_pgvector_dsn", AsyncMock(return_value="postgresql://x")),
    ):
        await DeployEngine()._auto_provision_data_backends(cfg, None)
    assert cfg.deploy.env_vars["DATABASE_URL"] == "postgresql://x"


async def test_memory_postgres_with_backend_url_is_skipped() -> None:
    cfg = _cfg(memory=MemoryConfig(backend="postgresql", backend_url="postgresql://byo"))
    with patch("engine.builder.provisioner_for") as prov_for:
        await DeployEngine()._auto_provision_data_backends(cfg)
    prov_for.assert_not_called()


async def test_memory_redis_injects_redis_url_and_backend(tmp_path) -> None:
    cfg = _cfg(memory=MemoryConfig(backend="redis"))
    prov = MagicMock()
    prov.provision_data_backend = AsyncMock(return_value=_redis_state())
    with (
        patch("engine.builder.provisioner_for", return_value=prov),
        patch(
            "engine.builder.resolve_redis_url",
            AsyncMock(return_value="redis://demo.cache.amazonaws.com:6379/0"),
        ),
    ):
        await DeployEngine()._auto_provision_data_backends(cfg, tmp_path)

    prov.provision_data_backend.assert_awaited_once()
    assert prov.provision_data_backend.await_args.args[0].engine == "redis"
    assert cfg.deploy.env_vars["REDIS_URL"] == "redis://demo.cache.amazonaws.com:6379/0"
    assert cfg.deploy.env_vars["MEMORY_BACKEND"] == "redis"
    saved = InfraState.load(tmp_path / ".agentbreeder" / "infra-state.json")
    assert saved.resources["security_groups"]["redis_sg_id"] == "sg-redis"


async def test_skips_when_no_memory() -> None:
    cfg = _cfg(memory=None)
    with patch("engine.builder.provisioner_for") as prov_for:
        await DeployEngine()._auto_provision_data_backends(cfg)
    prov_for.assert_not_called()


async def test_skips_for_local_cloud() -> None:
    cfg = _cfg(cloud=CloudType.local, env_vars={})
    with patch("engine.builder.provisioner_for") as prov_for:
        await DeployEngine()._auto_provision_data_backends(cfg)
    prov_for.assert_not_called()


async def test_does_not_inject_when_dsn_unresolved() -> None:
    cfg = _cfg()
    prov = MagicMock()
    prov.provision_data_backend = AsyncMock(return_value=_state())
    with (
        patch("engine.builder.provisioner_for", return_value=prov),
        patch("engine.builder.resolve_pgvector_dsn", AsyncMock(return_value=None)),
    ):
        await DeployEngine()._auto_provision_data_backends(cfg)
    assert "DATABASE_URL" not in cfg.deploy.env_vars
