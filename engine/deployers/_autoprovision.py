"""Auto-provision glue between the deploy pipeline and the cloud provisioners.

When an agent declares a knowledge base (or, later, memory) WITHOUT an explicit
``backend_url``, the deploy pipeline provisions a managed data store for it.
This module is the thin, cloud-aware seam that:

1. translates the agent's BYO-network ``deploy.env_vars`` into a
   :class:`~engine.provisioners.base.DataBackendRequest`
   (:func:`build_data_backend_request`), and
2. turns the provisioner's returned resources dict back into a
   ``KB_PGVECTOR_DSN`` (:func:`resolve_pgvector_dsn`), resolving the DB password
   from the cloud secret store — the password is written ONLY to the secret
   store by the provisioner and never persisted in :class:`InfraState`.

Cloud-specific SDK calls stay inside ``engine/provisioners`` and
``engine/secrets``; this module only maps env strings and dispatches.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from engine.deployers._pgvector_dsn import (
    pgvector_dsn_from_resources,
    pgvector_secret_ref,
)
from engine.provisioners.base import DataBackendRequest
from engine.secrets.aws_backend import AWSSecretsManagerBackend

if TYPE_CHECKING:  # pragma: no cover - typing only
    from engine.config_parser import AgentConfig

logger = logging.getLogger(__name__)

# InfraState.resources sub-key holding the provisioned Postgres, per cloud.
PROVISIONED_PG_RESOURCE_KEY = {"aws": "rds", "gcp": "cloud_sql", "azure": "postgres"}

_DEFAULT_REGION = {"aws": "us-east-1", "gcp": "us-central1", "azure": "eastus"}


def _split(raw: str | None) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


def build_data_backend_request(
    config: AgentConfig, engine: str = "postgres"
) -> DataBackendRequest | None:
    """Build a :class:`DataBackendRequest` (Postgres or Redis) from an agent config.

    Returns ``None`` when the deploy target is not a managed cloud (e.g.
    ``local`` / ``kubernetes`` / ``claude-managed``). The network identifiers are
    read from the SAME ``deploy.env_vars`` keys the cloud deployer uses, so the
    provisioned store lands in the agent's own VPC/network.
    """
    deploy = config.deploy
    cloud = getattr(deploy.cloud, "value", deploy.cloud)
    env = deploy.env_vars or {}

    if cloud == "aws":
        region = env.get("AWS_REGION") or deploy.region or _DEFAULT_REGION["aws"]
        network: dict[str, Any] = {
            "subnet_ids": _split(env.get("AWS_VPC_SUBNETS")),
            "agent_security_group_ids": _split(env.get("AWS_SECURITY_GROUPS")),
        }
        if env.get("AWS_VPC_ID"):
            network["vpc_id"] = env["AWS_VPC_ID"]
        fields: dict[str, Any] = {}
    elif cloud == "gcp":
        region = env.get("GCP_REGION") or deploy.region or _DEFAULT_REGION["gcp"]
        network = {"vpc_network": env.get("GCP_VPC_NETWORK", "default")}
        fields = {
            "GCP_PROJECT_ID": env.get("GCP_PROJECT_ID", env.get("GOOGLE_CLOUD_PROJECT", "")),
        }
    elif cloud == "azure":
        region = env.get("AZURE_LOCATION") or deploy.region or _DEFAULT_REGION["azure"]
        network = {
            "vnet_name": env.get("AZURE_VNET_NAME", ""),
            "db_subnet_id": env.get("AZURE_DB_SUBNET_ID", ""),
        }
        fields = {
            "AZURE_SUBSCRIPTION_ID": env.get("AZURE_SUBSCRIPTION_ID", ""),
            "AZURE_RESOURCE_GROUP": env.get("AZURE_RESOURCE_GROUP", ""),
        }
    else:
        return None

    return DataBackendRequest(
        cloud=cloud,
        region=region,
        agent_name=config.name,
        agent_version=config.version,
        engine=engine,
        network=network,
        fields=fields,
    )


def needs_managed_memory_redis(config: Any) -> bool:
    """Whether the deploy should provision a managed Redis for memory.

    True when the agent declares ``memory`` with ``backend: redis`` and no
    explicit ``backend_url``, and the target is a managed cloud.
    """
    memory = getattr(config, "memory", None)
    if memory is None:
        return False
    if getattr(memory, "backend", None) != "redis":
        return False
    if getattr(memory, "backend_url", None):
        return False
    deploy = getattr(config, "deploy", None)
    cloud = getattr(deploy, "cloud", None)
    cloud_val = getattr(cloud, "value", cloud)
    return cloud_val in ("aws", "gcp", "azure")


async def _resolve_db_password(cloud: str, secret_ref: str, region: str) -> str | None:
    """Fetch the DB password from the cloud secret store by its provisioned ref.

    ``secret_ref`` is the cloud-native reference recorded by the provisioner:
    AWS Secrets Manager ARN, GCP Secret resource path
    (``projects/<p>/secrets/<id>``), or Azure Key Vault secret URI
    (``https://<vault>.vault.azure.net/secrets/<name>``).
    """
    if cloud == "aws":
        # prefix="" so the raw ARN is used verbatim as the SecretId.
        return await AWSSecretsManagerBackend(region=region, prefix="").get(secret_ref)

    if cloud == "gcp":
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        # secret_ref = projects/<project>/secrets/<secret_id>
        parts = secret_ref.split("/")
        if len(parts) < 4 or "secrets" not in parts:
            return None
        project = parts[1]
        secret_id = secret_ref.rsplit("/secrets/", 1)[-1]
        # prefix="" so the already-qualified secret id is used verbatim.
        return await GCPSecretManagerBackend(project_id=project, prefix="").get(secret_id)

    if cloud == "azure":
        from engine.secrets.azure_backend import AzureKeyVaultBackend

        # secret_ref = https://<vault>.vault.azure.net/secrets/<name>[/<version>]
        if "/secrets/" not in secret_ref:
            return None
        vault_url, _, tail = secret_ref.partition("/secrets/")
        secret_name = tail.split("/", 1)[0]
        # prefix="" so the literal secret name is used verbatim.
        return await AzureKeyVaultBackend(vault_url=vault_url, prefix="").get(secret_name)

    logger.warning("No DB-password resolver wired for cloud=%s", cloud)
    return None


async def resolve_pgvector_dsn(cloud: str, resources: dict[str, Any], region: str) -> str | None:
    """Assemble ``KB_PGVECTOR_DSN`` from a provisioned resources dict.

    Returns ``None`` (so the caller can fall back / skip) when the Postgres
    resource, its secret reference, or the password cannot be resolved.
    """
    inner = resources.get(PROVISIONED_PG_RESOURCE_KEY.get(cloud, ""))
    if not inner:
        return None
    secret_ref = pgvector_secret_ref(cloud, inner)
    if not secret_ref:
        return None
    password = await _resolve_db_password(cloud, secret_ref, region)
    if password is None:
        logger.warning("Could not resolve DB password for %s pgvector backend", cloud)
        return None
    return pgvector_dsn_from_resources(cloud, inner, password)


# InfraState.resources sub-key holding the provisioned Redis, per cloud.
PROVISIONED_REDIS_RESOURCE_KEY = {
    "aws": "elasticache",
    "gcp": "memorystore",
    "azure": "redis",
}


async def resolve_redis_url(cloud: str, resources: dict[str, Any], region: str) -> str | None:
    """Assemble a ``REDIS_URL`` from a provisioned Redis resources dict.

    AWS/GCP caches are network-isolated and unauthenticated → ``redis://``.
    Azure Cache for Redis is TLS + access-key authenticated → ``rediss://`` with
    the key fetched from Key Vault. Returns ``None`` when the store or its
    coordinates cannot be resolved.
    """
    inner = resources.get(PROVISIONED_REDIS_RESOURCE_KEY.get(cloud, ""))
    if not inner:
        return None

    if cloud == "aws":
        host = inner.get("endpoint")
        port = inner.get("port", 6379)
        return f"redis://{host}:{port}/0" if host else None

    if cloud == "gcp":
        host = inner.get("host")
        port = inner.get("port", 6379)
        return f"redis://{host}:{port}/0" if host else None

    if cloud == "azure":
        host = inner.get("hostname")
        ssl_port = inner.get("ssl_port", 6380)
        secret_uri = inner.get("key_secret_uri")
        if not (host and secret_uri):
            return None
        key = await _resolve_db_password("azure", secret_uri, region)
        if key is None:
            logger.warning("Could not resolve Azure Redis access key from Key Vault")
            return None
        return f"rediss://:{quote(key, safe='')}@{host}:{ssl_port}/0"

    return None
