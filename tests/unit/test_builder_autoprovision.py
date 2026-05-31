"""Deploy pipeline auto-provision hook — DeployEngine._auto_provision_data_backends.

Verifies the seam that, between infra-provision and deploy, provisions a
managed pgvector store for a knowledge base declared WITHOUT an explicit
``backend_url`` and injects ``KB_PGVECTOR_DSN`` into ``deploy.env_vars`` so the
container reaches it. The cloud provisioner + DSN resolver are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from engine.builder import DeployEngine
from engine.config_parser import (
    AgentConfig,
    CloudType,
    DeployConfig,
    KnowledgeBaseRef,
    MemoryConfig,
    ModelConfig,
)
from engine.provisioners.state import InfraState


def _cfg(
    *,
    cloud: CloudType = CloudType.aws,
    kbs: list[KnowledgeBaseRef] | None = None,
    memory: MemoryConfig | None = None,
    env_vars: dict[str, str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        name="demo",
        version="1.0.0",
        team="t",
        owner="a@b.com",
        framework="langgraph",
        model=ModelConfig(primary="gpt-4o"),
        knowledge_bases=kbs if kbs is not None else [KnowledgeBaseRef(ref="kb/docs")],
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


async def test_injects_dsn_for_kb_without_backend_url() -> None:
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
    assert (
        cfg.deploy.env_vars["KB_PGVECTOR_DSN"]
        == "postgresql://u:p@demo.rds.amazonaws.com:5432/agentbreeder"
    )


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
    assert cfg.deploy.env_vars["KB_PGVECTOR_DSN"] == "postgresql://x"


async def test_memory_postgres_injects_database_url_and_backend() -> None:
    cfg = _cfg(kbs=[], memory=MemoryConfig(backend="postgresql"))
    prov = MagicMock()
    prov.provision_data_backend = AsyncMock(return_value=_state())
    with (
        patch("engine.builder.provisioner_for", return_value=prov),
        patch("engine.builder.resolve_pgvector_dsn", AsyncMock(return_value="postgresql://x")),
    ):
        await DeployEngine()._auto_provision_data_backends(cfg)

    prov.provision_data_backend.assert_awaited_once()
    assert cfg.deploy.env_vars["DATABASE_URL"] == "postgresql://x"
    assert cfg.deploy.env_vars["MEMORY_BACKEND"] == "postgresql"
    # No KB declared → no pgvector env.
    assert "KB_PGVECTOR_DSN" not in cfg.deploy.env_vars


async def test_kb_and_memory_postgres_share_one_instance() -> None:
    cfg = _cfg(kbs=[KnowledgeBaseRef(ref="kb/docs")], memory=MemoryConfig(backend="postgresql"))
    prov = MagicMock()
    prov.provision_data_backend = AsyncMock(return_value=_state())
    with (
        patch("engine.builder.provisioner_for", return_value=prov),
        patch("engine.builder.resolve_pgvector_dsn", AsyncMock(return_value="postgresql://x")),
    ):
        await DeployEngine()._auto_provision_data_backends(cfg)

    # Provision Postgres at most once even when both KB + memory want it.
    prov.provision_data_backend.assert_awaited_once()
    assert cfg.deploy.env_vars["KB_PGVECTOR_DSN"] == "postgresql://x"
    assert cfg.deploy.env_vars["DATABASE_URL"] == "postgresql://x"


async def test_memory_postgres_with_backend_url_is_skipped() -> None:
    cfg = _cfg(
        kbs=[], memory=MemoryConfig(backend="postgresql", backend_url="postgresql://byo")
    )
    with patch("engine.builder.provisioner_for") as prov_for:
        await DeployEngine()._auto_provision_data_backends(cfg)
    prov_for.assert_not_called()


async def test_skips_when_backend_url_present() -> None:
    cfg = _cfg(kbs=[KnowledgeBaseRef(ref="kb/docs", backend_url="postgresql://byo")])
    with patch("engine.builder.provisioner_for") as prov_for:
        await DeployEngine()._auto_provision_data_backends(cfg)
    prov_for.assert_not_called()
    assert "KB_PGVECTOR_DSN" not in cfg.deploy.env_vars


async def test_skips_when_no_knowledge_base() -> None:
    cfg = _cfg(kbs=[])
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
    assert "KB_PGVECTOR_DSN" not in cfg.deploy.env_vars
