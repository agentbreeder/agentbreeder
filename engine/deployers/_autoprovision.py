"""Auto-provision glue between the deploy pipeline and the cloud provisioners.

When an agent declares a knowledge base (or, later, memory) WITHOUT an explicit
``backend_url``, the deploy pipeline provisions a managed data store for it.
This module is the thin, cloud-aware seam that:

1. translates the agent's BYO-network ``deploy.env_vars`` into a
   :class:`~engine.provisioners.base.DataBackendRequest`
   (:func:`build_pg_data_backend_request`), and
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


def build_pg_data_backend_request(config: AgentConfig) -> DataBackendRequest | None:
    """Build a Postgres :class:`DataBackendRequest` from an agent config.

    Returns ``None`` when the deploy target is not a managed-Postgres cloud
    (e.g. ``local`` / ``kubernetes`` / ``claude-managed``). The network identifiers
    are read from the SAME ``deploy.env_vars`` keys the cloud deployer uses, so
    the provisioned DB lands in the agent's own VPC/network.
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
        engine="postgres",
        network=network,
        fields=fields,
    )


async def _resolve_db_password(cloud: str, secret_ref: str, region: str) -> str | None:
    """Fetch the DB password from the cloud secret store by its provisioned ref."""
    if cloud == "aws":
        # prefix="" so the raw ARN is used verbatim as the SecretId.
        return await AWSSecretsManagerBackend(region=region, prefix="").get(secret_ref)
    # GCP / Azure resolution lands with their provision_data_backend (Inc 5).
    logger.warning("No DB-password resolver wired for cloud=%s yet", cloud)
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
