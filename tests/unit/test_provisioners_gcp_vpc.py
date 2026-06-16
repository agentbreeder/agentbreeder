"""GCP greenfield VPC provisioning (multi-cloud parity #505, #436/#382).

When ``GCP_PROVISION_VPC`` is set (the CLI ``deploy --provision`` path), the GCP
provisioner builds a dedicated custom-mode VPC + subnet (+ Cloud NAT + PSA) so
the agent and data tier share a private network, mirroring the AWS greenfield
VPC. SDK calls are patched at the ``_ensure_*`` boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from engine.provisioners import InfraValidationInput
from engine.provisioners.gcp import (
    GCPProvisioner,
    _should_provision_vpc_network,
    _truncate_network_id,
)
from engine.provisioners.state import InfraState


def _payload(extra: dict | None = None) -> InfraValidationInput:
    fields = {
        "GOOGLE_CLOUD_PROJECT": "test-proj",
        "GCP_REGION": "us-central1",
        "GCP_AGENT_NAME": "demo",
    }
    if extra:
        fields.update(extra)
    return InfraValidationInput(cloud="gcp", region="us-central1", mode="simple", fields=fields)


@pytest.fixture
def provisioner():
    p = GCPProvisioner()
    net_info = {
        "name": "ab-demo-vpc",
        "network_url": "projects/test-proj/global/networks/ab-demo-vpc",
        "subnet": "ab-demo-vpc-subnet",
        "subnet_cidr": "10.20.0.0/20",
        "region": "us-central1",
        "project": "test-proj",
        "router": "ab-demo-vpc-router",
        "nat": "ab-demo-vpc-nat",
        "psa_range": "ab-demo-vpc-psa",
    }
    with (
        patch.object(p, "_ensure_artifact_registry_repo", new=AsyncMock(return_value=None)),
        patch.object(p, "_ensure_service_account", new=AsyncMock(return_value=None)),
        patch.object(p, "_bind_iam_roles", new=AsyncMock(return_value=None)),
        patch.object(p, "_ensure_vpc_network", new=AsyncMock(return_value=net_info)),
        patch.object(
            p,
            "_ensure_vpc_connector",
            new=AsyncMock(
                return_value="projects/test-proj/locations/us-central1/connectors/ab-demo"
            ),
        ),
    ):
        yield p


# -- predicate + naming -----------------------------------------------------


@pytest.mark.parametrize("flag", [True, "true", "1", "yes"])
def test_should_provision_vpc_network_true(flag) -> None:
    assert _should_provision_vpc_network({"GCP_PROVISION_VPC": flag}) is True


def test_should_provision_vpc_network_default_false() -> None:
    assert _should_provision_vpc_network({}) is False


def test_truncate_network_id_starts_with_letter_and_lowercase() -> None:
    out = _truncate_network_id("My_Agent")
    assert out[0].isalpha()
    assert out == out.lower()
    assert len(out) <= 63


# -- provision() orchestration ----------------------------------------------


@pytest.mark.asyncio
async def test_provision_creates_vpc_when_flag_set(provisioner) -> None:
    state = await provisioner.provision(_payload({"GCP_PROVISION_VPC": "true"}))
    provisioner._ensure_vpc_network.assert_awaited_once()
    assert "network" in state.resources
    assert state.resources["network"]["name"] == "ab-demo-vpc"


@pytest.mark.asyncio
async def test_provision_skips_vpc_by_default(provisioner) -> None:
    state = await provisioner.provision(_payload())
    provisioner._ensure_vpc_network.assert_not_awaited()
    assert "network" not in state.resources


@pytest.mark.asyncio
async def test_provisioned_network_drives_the_connector(provisioner) -> None:
    # The connector must land on the freshly-created VPC, not "default".
    await provisioner.provision(
        _payload({"GCP_PROVISION_VPC": "true", "GCP_PROVISION_VPC_CONNECTOR": "true"})
    )
    provisioner._ensure_vpc_connector.assert_awaited_once()
    assert provisioner._ensure_vpc_connector.await_args.kwargs["network"] == "ab-demo-vpc"


@pytest.mark.asyncio
async def test_destroy_deletes_network() -> None:
    p = GCPProvisioner()
    state = InfraState(
        cloud="gcp",
        region="us-central1",
        provisioned_by="x",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "network": {
                "name": "ab-demo-vpc",
                "subnet": "ab-demo-vpc-subnet",
                "region": "us-central1",
                "project": "test-proj",
                "router": "ab-demo-vpc-router",
            }
        },
    )
    with patch.object(p, "_delete_vpc_network", new=AsyncMock(return_value=None)) as m:
        await p.destroy(state)
    m.assert_awaited_once()
    assert m.await_args.kwargs["info"]["name"] == "ab-demo-vpc"
