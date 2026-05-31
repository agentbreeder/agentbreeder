"""Auto-provision phase — GCPProvisioner.provision_data_backend(engine="redis").

Provisions a Memorystore Redis on the agent's BYO VPC network. The heavy
``_ensure_memorystore`` SDK helper is patched so the suite runs without
google-cloud-redis installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from engine.provisioners.base import DataBackendRequest
from engine.provisioners.gcp import GCPProvisioner
from engine.provisioners.state import InfraState

_MEMORYSTORE_RETURN = {
    "instance_id": "demo",
    "name": "projects/test-proj/locations/us-central1/instances/demo",
    "host": "10.9.8.7",
    "port": 6379,
    "region": "us-central1",
    "project": "test-proj",
    "vpc_network": "prod-vpc",
    "engine": "redis",
}


def _request(**fields_override) -> DataBackendRequest:
    fields = {"GCP_PROJECT_ID": "test-proj"}
    fields.update(fields_override)
    return DataBackendRequest(
        cloud="gcp",
        region="us-central1",
        agent_name="demo",
        agent_version="1.0.0",
        engine="redis",
        network={"vpc_network": "prod-vpc"},
        fields=fields,
    )


async def test_returns_state_with_memorystore_resource() -> None:
    p = GCPProvisioner()
    with patch.object(p, "_ensure_memorystore", new=AsyncMock(return_value=_MEMORYSTORE_RETURN)):
        state = await p.provision_data_backend(_request())

    assert state.cloud == "gcp"
    assert state.resources["memorystore"]["host"] == "10.9.8.7"
    assert state.resources["memorystore"]["engine"] == "redis"
    assert "cloud_sql" not in state.resources


async def test_uses_byo_vpc_network_and_project() -> None:
    p = GCPProvisioner()
    ensure = AsyncMock(return_value=_MEMORYSTORE_RETURN)
    with patch.object(p, "_ensure_memorystore", new=ensure):
        await p.provision_data_backend(_request())

    kwargs = ensure.await_args.kwargs
    assert kwargs["project"] == "test-proj"
    assert kwargs["vpc_network"] == "prod-vpc"
    assert kwargs["region"] == "us-central1"


async def test_destroy_deletes_memorystore_instance() -> None:
    p = GCPProvisioner()
    delete = AsyncMock()
    state = InfraState(
        cloud="gcp",
        region="us-central1",
        provisioned_by="test",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "memorystore": {
                "instance_id": "demo",
                "name": "projects/test-proj/locations/us-central1/instances/demo",
            }
        },
    )
    with patch.object(p, "_delete_memorystore", new=delete):
        await p.destroy(state)

    delete.assert_awaited_once()
    assert delete.await_args.kwargs["name"].endswith("/instances/demo")
