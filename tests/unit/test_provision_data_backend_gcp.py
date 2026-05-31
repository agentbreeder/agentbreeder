"""Auto-provision phase — GCPProvisioner.provision_data_backend.

Provisions a Cloud SQL Postgres (pgvector) into the agent's BYO VPC network
without the greenfield Artifact Registry / Service Account scaffolding. The
heavy ``_ensure_cloud_sql`` SDK helper is patched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.provisioners.base import DataBackendRequest
from engine.provisioners.gcp import GCPProvisioner

_CLOUD_SQL_RETURN = {
    "instance_id": "demo-memory",
    "instance_name": "projects/test-proj/locations/us-central1/instances/demo-memory",
    "connection_name": "test-proj:us-central1:demo-memory",
    "private_ip": "10.1.2.3",
    "database": "agentbreeder_memory",
    "user": "agentbreeder",
    "tier": "db-f1-micro",
    "region": "us-central1",
    "project": "test-proj",
    "vpc_network": "prod-vpc",
    "password_secret": "projects/test-proj/secrets/agentbreeder-demo-memory-db-password",
}


def _request(**fields_override) -> DataBackendRequest:
    fields = {"GCP_PROJECT_ID": "test-proj"}
    fields.update(fields_override)
    return DataBackendRequest(
        cloud="gcp",
        region="us-central1",
        agent_name="demo",
        agent_version="1.0.0",
        engine="postgres",
        network={"vpc_network": "prod-vpc"},
        fields=fields,
    )


async def test_returns_state_with_cloud_sql_resource() -> None:
    p = GCPProvisioner()
    with patch.object(p, "_ensure_cloud_sql", new=AsyncMock(return_value=_CLOUD_SQL_RETURN)):
        state = await p.provision_data_backend(_request())

    assert state.cloud == "gcp"
    assert state.region == "us-central1"
    assert state.mode == "provisioned"
    assert state.resources["cloud_sql"]["private_ip"] == "10.1.2.3"
    # No greenfield artifact registry / service account in the focused path.
    assert "artifact_registry" not in state.resources
    assert "service_account" not in state.resources


async def test_uses_byo_vpc_network_and_project() -> None:
    p = GCPProvisioner()
    ensure = AsyncMock(return_value=_CLOUD_SQL_RETURN)
    with patch.object(p, "_ensure_cloud_sql", new=ensure):
        await p.provision_data_backend(_request())

    kwargs = ensure.await_args.kwargs
    assert kwargs["project"] == "test-proj"
    assert kwargs["vpc_network"] == "prod-vpc"
    assert kwargs["region"] == "us-central1"


async def test_requires_project() -> None:
    p = GCPProvisioner()
    req = DataBackendRequest(
        cloud="gcp", region="us-central1", agent_name="demo", network={"vpc_network": "v"}
    )
    with pytest.raises(ValueError, match="project"):
        await p.provision_data_backend(req)


# Memorystore Redis provisioning is covered in
# test_provision_data_backend_gcp_redis.py.
