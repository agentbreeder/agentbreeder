"""Auto-provision deploy glue — build_pg_data_backend_request + resolve_pgvector_dsn.

These pure-ish helpers sit between the deploy pipeline and the cloud
provisioners: they translate an agent's BYO-network ``deploy.env_vars`` into a
:class:`DataBackendRequest`, and turn a provisioned resources dict back into a
``KB_PGVECTOR_DSN`` (resolving the DB password from the cloud secret store).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from engine.config_parser import (
    AgentConfig,
    CloudType,
    DeployConfig,
    KnowledgeBaseRef,
    ModelConfig,
)
from engine.deployers._autoprovision import (
    build_pg_data_backend_request,
    resolve_pgvector_dsn,
)


def _cfg(
    *,
    cloud: CloudType = CloudType.aws,
    env_vars: dict[str, str] | None = None,
    kbs: list[KnowledgeBaseRef] | None = None,
    region: str | None = None,
) -> AgentConfig:
    return AgentConfig(
        name="demo",
        version="1.0.0",
        team="t",
        owner="a@b.com",
        framework="langgraph",
        model=ModelConfig(primary="gpt-4o"),
        knowledge_bases=kbs if kbs is not None else [KnowledgeBaseRef(ref="kb/docs")],
        deploy=DeployConfig(cloud=cloud, region=region, env_vars=env_vars or {}),
    )


# ------------------------------------------------ build_pg_data_backend_request


def test_aws_request_maps_subnets_and_security_groups() -> None:
    cfg = _cfg(
        env_vars={
            "AWS_VPC_SUBNETS": "subnet-a, subnet-b",
            "AWS_SECURITY_GROUPS": "sg-agent",
            "AWS_REGION": "eu-west-1",
        }
    )
    req = build_pg_data_backend_request(cfg)
    assert req is not None
    assert req.cloud == "aws"
    assert req.region == "eu-west-1"
    assert req.engine == "postgres"
    assert req.agent_name == "demo"
    assert req.network["subnet_ids"] == ["subnet-a", "subnet-b"]
    assert req.network["agent_security_group_ids"] == ["sg-agent"]


def test_region_falls_back_to_deploy_region() -> None:
    cfg = _cfg(
        cloud=CloudType.aws,
        region="ap-south-1",
        env_vars={"AWS_VPC_SUBNETS": "subnet-a", "AWS_SECURITY_GROUPS": "sg-agent"},
    )
    req = build_pg_data_backend_request(cfg)
    assert req is not None
    assert req.region == "ap-south-1"


def test_local_cloud_yields_no_request() -> None:
    cfg = _cfg(cloud=CloudType.local, env_vars={})
    assert build_pg_data_backend_request(cfg) is None


# ------------------------------------------------------- resolve_pgvector_dsn


async def test_resolve_dsn_for_aws_fetches_password_and_builds_url() -> None:
    resources = {
        "rds": {
            "endpoint": "demo.abc.rds.amazonaws.com",
            "port": 5432,
            "db_name": "agentbreeder_memory",
            "secret_arn": "arn:aws:secretsmanager:us-east-1:1:secret:agentbreeder/demo/db-password",
        }
    }
    backend = AsyncMock()
    backend.get = AsyncMock(return_value="s3cr3t/pw")
    with patch("engine.deployers._autoprovision.AWSSecretsManagerBackend", return_value=backend):
        dsn = await resolve_pgvector_dsn("aws", resources, "us-east-1")

    # Password is URL-encoded; host/db/user come from the resources dict.
    assert dsn == (
        "postgresql://agentbreeder:s3cr3t%2Fpw@demo.abc.rds.amazonaws.com:5432/agentbreeder_memory"
    )
    backend.get.assert_awaited_once_with(resources["rds"]["secret_arn"])


async def test_resolve_dsn_returns_none_when_secret_missing() -> None:
    resources = {
        "rds": {
            "endpoint": "demo.abc.rds.amazonaws.com",
            "secret_arn": "arn:aws:secretsmanager:...:db-password",
        }
    }
    backend = AsyncMock()
    backend.get = AsyncMock(return_value=None)
    with patch("engine.deployers._autoprovision.AWSSecretsManagerBackend", return_value=backend):
        dsn = await resolve_pgvector_dsn("aws", resources, "us-east-1")
    assert dsn is None


async def test_resolve_dsn_returns_none_when_resource_absent() -> None:
    assert await resolve_pgvector_dsn("aws", {}, "us-east-1") is None


async def test_resolve_dsn_for_gcp_parses_secret_ref_and_uses_private_ip() -> None:
    resources = {
        "cloud_sql": {
            "private_ip": "10.1.2.3",
            "database": "agentbreeder_memory",
            "user": "agentbreeder",
            "password_secret": "projects/test-proj/secrets/agentbreeder-demo-db-password",
        }
    }
    backend = AsyncMock()
    backend.get = AsyncMock(return_value="gcp-pw")
    with patch(
        "engine.secrets.gcp_backend.GCPSecretManagerBackend", return_value=backend
    ) as factory:
        dsn = await resolve_pgvector_dsn("gcp", resources, "us-central1")

    factory.assert_called_once_with(project_id="test-proj", prefix="")
    backend.get.assert_awaited_once_with("agentbreeder-demo-db-password")
    assert dsn == "postgresql://agentbreeder:gcp-pw@10.1.2.3:5432/agentbreeder_memory"


async def test_resolve_dsn_for_azure_parses_kv_uri() -> None:
    resources = {
        "postgres": {
            "fqdn": "agentbreeder-demo-db.postgres.database.azure.com",
            "admin_user": "agentbreeder",
            "database": "postgres",
            "password_secret_uri": (
                "https://ab-demo-kv.vault.azure.net/secrets/agentbreeder-demo-db-password"
            ),
        }
    }
    backend = AsyncMock()
    backend.get = AsyncMock(return_value="azure-pw")
    with patch(
        "engine.secrets.azure_backend.AzureKeyVaultBackend", return_value=backend
    ) as factory:
        dsn = await resolve_pgvector_dsn("azure", resources, "eastus")

    factory.assert_called_once_with(vault_url="https://ab-demo-kv.vault.azure.net", prefix="")
    backend.get.assert_awaited_once_with("agentbreeder-demo-db-password")
    assert dsn == (
        "postgresql://agentbreeder:azure-pw@"
        "agentbreeder-demo-db.postgres.database.azure.com:5432/postgres"
    )
