"""Auto-provision phase — AzureProvisioner.provision_data_backend(engine="redis").

Provisions an Azure Cache for Redis (TLS-only, key in Key Vault) into a BYO
resource group, recording ONLY what it creates so teardown never touches the
user's RG. The heavy ARM helpers are patched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.provisioners.azure import AzureProvisioner
from engine.provisioners.base import DataBackendRequest
from engine.provisioners.state import InfraState


def _request() -> DataBackendRequest:
    return DataBackendRequest(
        cloud="azure",
        region="eastus",
        agent_name="demo",
        agent_version="1.0.0",
        engine="redis",
        network={},
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
            "_ensure_azure_redis",
            new=AsyncMock(
                return_value=(
                    "redis-id",
                    "ab-demo-redis.redis.cache.windows.net",
                    6380,
                    "primary-access-key",
                )
            ),
        ),
        patch.object(
            p,
            "_write_db_password_to_kv",
            new=AsyncMock(
                return_value="https://ab-demo-kv.vault.azure.net/secrets/ab-demo-redis-key"
            ),
        ),
    )


async def test_returns_state_with_redis_and_no_resource_group() -> None:
    p = AzureProvisioner()
    kv, redis, kvwrite = _patched(p)
    with kv, redis, kvwrite:
        state = await p.provision_data_backend(_request())

    rc = state.resources["redis"]
    assert rc["hostname"] == "ab-demo-redis.redis.cache.windows.net"
    assert rc["ssl_port"] == 6380
    assert rc["key_secret_uri"].endswith("-key")
    assert rc["resource_group"] == "byo-rg"
    assert state.resources["key_vault"]["uri"].startswith("https://")
    # RG-safety invariant.
    assert "resource_group" not in state.resources


async def test_access_key_written_to_key_vault_not_state() -> None:
    p = AzureProvisioner()
    kv, redis, kvwrite = _patched(p)
    with kv, redis, kvwrite as write_mock:
        state = await p.provision_data_backend(_request())

    # The raw key goes to KV; only its URI is recorded in state.
    assert write_mock.await_args.kwargs["password"] == "primary-access-key"
    assert "primary-access-key" not in state.model_dump_json()


async def test_requires_subscription_and_resource_group() -> None:
    p = AzureProvisioner()
    req = DataBackendRequest(
        cloud="azure", region="eastus", agent_name="demo", engine="redis", fields={}
    )
    with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
        await p.provision_data_backend(req)


async def test_destroy_data_backend_deletes_only_the_cache() -> None:
    p = AzureProvisioner()
    delete = AsyncMock()
    state = InfraState(
        cloud="azure",
        region="eastus",
        provisioned_by="test",
        provisioned_at="2026-05-31T00:00:00+00:00",
        mode="provisioned",
        resources={
            "redis": {
                "name": "ab-demo-redis",
                "subscription_id": "sub-1",
                "resource_group": "byo-rg",
            }
        },
    )
    with (
        patch.object(p, "_delete_azure_redis", new=delete),
        patch.object(p, "_delete_resource_group", new=AsyncMock()) as del_rg,
    ):
        await p.destroy_data_backend(state)

    delete.assert_awaited_once()
    assert delete.await_args.kwargs["cache_name"] == "ab-demo-redis"
    assert delete.await_args.kwargs["rg_name"] == "byo-rg"
    del_rg.assert_not_called()
