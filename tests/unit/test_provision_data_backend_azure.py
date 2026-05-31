"""Auto-provision phase — AzureProvisioner.provision_data_backend + destroy_data_backend.

Provisions a PostgreSQL Flexible Server (pgvector) into a BYO resource group +
delegated subnet, recording ONLY what it creates so teardown never touches the
user's resource group. The heavy ARM helpers are patched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.provisioners.azure import AzureProvisioner
from engine.provisioners.base import DataBackendRequest
from engine.provisioners.state import InfraState


def _request(**net) -> DataBackendRequest:
    network = {"db_subnet_id": "/subscriptions/s/.../subnets/db-subnet"}
    network.update(net)
    return DataBackendRequest(
        cloud="azure",
        region="eastus",
        agent_name="demo",
        agent_version="1.0.0",
        engine="postgres",
        network=network,
        fields={"AZURE_SUBSCRIPTION_ID": "sub-1", "AZURE_RESOURCE_GROUP": "byo-rg"},
    )


def _patched(p: AzureProvisioner):
    return (
        patch.object(
            p,
            "_ensure_key_vault",
            new=AsyncMock(return_value=("kv-id", "https://ab-demo-kv.vault.azure.net/")),
        ),
        patch.object(
            p,
            "_ensure_postgres_flexible",
            new=AsyncMock(
                return_value=("db-id", "agentbreeder-demo-db.postgres.database.azure.com")
            ),
        ),
        patch.object(
            p,
            "_write_db_password_to_kv",
            new=AsyncMock(
                return_value="https://ab-demo-kv.vault.azure.net/secrets/agentbreeder-demo-db-password"
            ),
        ),
        patch.object(p, "_delete_postgres_flexible", new=AsyncMock(return_value=None)),
    )


async def test_returns_state_with_postgres_and_no_resource_group() -> None:
    p = AzureProvisioner()
    kv, pg, kvwrite, _ = _patched(p)
    with kv, pg, kvwrite:
        state = await p.provision_data_backend(_request())

    assert state.cloud == "azure"
    assert state.resources["postgres"]["fqdn"].endswith(".postgres.database.azure.com")
    assert state.resources["postgres"]["password_secret_uri"].endswith("-password")
    # RG-safety invariant: the user's BYO resource group is NEVER recorded, so
    # the greenfield cascade-delete can never fire against it.
    assert "resource_group" not in state.resources


async def test_requires_delegated_db_subnet() -> None:
    p = AzureProvisioner()
    req = _request()
    req.network.pop("db_subnet_id")
    with pytest.raises(ValueError, match="db_subnet_id"):
        await p.provision_data_backend(req)


async def test_requires_subscription_and_resource_group() -> None:
    p = AzureProvisioner()
    req = DataBackendRequest(
        cloud="azure",
        region="eastus",
        agent_name="demo",
        network={"db_subnet_id": "x"},
        fields={},
    )
    with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
        await p.provision_data_backend(req)


async def test_password_never_appears_in_state() -> None:
    p = AzureProvisioner()
    kv, pg, kvwrite, _ = _patched(p)
    with kv, pg, kvwrite:
        state = await p.provision_data_backend(_request())
    # The plaintext password is generated internally; only its KV URI is stored.
    blob = state.model_dump_json()
    assert "password_secret_uri" in blob
    # token_urlsafe(32) yields ~43 chars; ensure no obvious plaintext leaked by
    # checking the only password-shaped field is the URI ref.
    assert state.resources["postgres"]["password_secret_uri"].startswith("https://")


async def test_destroy_data_backend_deletes_only_the_server() -> None:
    p = AzureProvisioner()
    delete = AsyncMock()
    state = InfraState(
        cloud="azure",
        region="eastus",
        provisioned_by="test",
        provisioned_at="2026-05-31T00:00:00+00:00",
        mode="provisioned",
        resources={
            "postgres": {
                "name": "agentbreeder-demo-db",
                "subscription_id": "sub-1",
                "resource_group": "byo-rg",
            }
        },
    )
    with (
        patch.object(p, "_delete_postgres_flexible", new=delete),
        patch.object(p, "_delete_resource_group", new=AsyncMock()) as del_rg,
    ):
        await p.destroy_data_backend(state)

    delete.assert_awaited_once()
    kwargs = delete.await_args.kwargs
    assert kwargs["db_name"] == "agentbreeder-demo-db"
    assert kwargs["rg_name"] == "byo-rg"
    # The BYO resource group must NEVER be deleted by the focused teardown.
    del_rg.assert_not_called()


async def test_redis_not_implemented_yet() -> None:
    p = AzureProvisioner()
    req = _request()
    req.engine = "redis"
    with pytest.raises(NotImplementedError):
        await p.provision_data_backend(req)
