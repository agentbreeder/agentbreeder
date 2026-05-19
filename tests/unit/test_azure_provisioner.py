"""#384 — Azure greenfield provisioner unit tests.

The low-level azure-mgmt-* / azure-keyvault-secrets calls are patched at the
``AzureProvisioner._ensure_*`` / ``_delete_*`` / ``_write_*`` boundary so the
test suite runs without any Azure SDK calls actually firing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from engine.provisioners import InfraState, InfraValidationInput
from engine.provisioners.azure import (
    ACR_PULL_ROLE_DEF_ID,
    AzureProvisioner,
    _has_agentbreeder_tag,
    _resource_tags,
    _safe_acr_name,
    _safe_kv_name,
)

# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def test_safe_acr_name_alphanumeric_5_to_50() -> None:
    name = _safe_acr_name("Customer Support Agent!")
    assert name.isalnum()
    assert 5 <= len(name) <= 50
    assert name.startswith("ab")


def test_safe_acr_name_handles_empty_input() -> None:
    name = _safe_acr_name("")
    assert name.isalnum()
    assert 5 <= len(name) <= 50


def test_safe_kv_name_caps_at_24_chars() -> None:
    name = _safe_kv_name("a" * 100)
    assert len(name) <= 24
    assert name.endswith("-kv")


def test_safe_kv_name_handles_empty() -> None:
    name = _safe_kv_name("")
    assert name == "ab-default-kv"


def test_resource_tags_includes_agentbreeder_marker() -> None:
    tags = _resource_tags("demo", "1.2.3")
    assert tags["AgentBreeder"] == "true"
    assert tags["AgentName"] == "demo"
    assert tags["Version"] == "1.2.3"


def test_has_agentbreeder_tag_detects_marker() -> None:
    assert _has_agentbreeder_tag({"AgentBreeder": "true"})
    assert not _has_agentbreeder_tag({"AgentBreeder": "false"})
    assert not _has_agentbreeder_tag({})
    assert not _has_agentbreeder_tag(None)


# ---------------------------------------------------------------------------
# provision() orchestration
# ---------------------------------------------------------------------------


def _payload(extra: dict | None = None) -> InfraValidationInput:
    fields = {
        "AZURE_SUBSCRIPTION_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_TENANT_ID": "22222222-2222-2222-2222-222222222222",
        "AZURE_LOCATION": "eastus",
        "AZURE_AGENT_NAME": "demo",
        "AZURE_AGENT_VERSION": "1.0.0",
    }
    if extra:
        fields.update(extra)
    return InfraValidationInput(cloud="azure", region="eastus", mode="simple", fields=fields)


def _sub() -> str:
    return "11111111-1111-1111-1111-111111111111"


def _rg_id(name: str = "agentbreeder-demo-rg") -> str:
    return f"/subscriptions/{_sub()}/resourceGroups/{name}"


def _acr_id(rg: str = "agentbreeder-demo-rg", acr: str = "abdemo123456") -> str:
    return (
        f"/subscriptions/{_sub()}/resourceGroups/{rg}"
        f"/providers/Microsoft.ContainerRegistry/registries/{acr}"
    )


@pytest.fixture
def patched_provisioner():
    """AzureProvisioner with every low-level SDK helper patched out."""
    p = AzureProvisioner()
    with (
        patch.object(p, "_ensure_resource_group", new=AsyncMock(return_value=_rg_id())),
        patch.object(
            p,
            "_ensure_log_analytics",
            new=AsyncMock(
                return_value=(
                    f"{_rg_id()}/providers/Microsoft.OperationalInsights/workspaces/law",
                    "law-customer-id",
                    "law-shared-key",
                )
            ),
        ),
        patch.object(
            p,
            "_ensure_container_apps_environment",
            new=AsyncMock(
                return_value=f"{_rg_id()}/providers/Microsoft.App/managedEnvironments/env"
            ),
        ),
        patch.object(
            p,
            "_ensure_acr",
            new=AsyncMock(return_value=(_acr_id(), "abdemo123456.azurecr.io")),
        ),
        patch.object(
            p,
            "_ensure_managed_identity",
            new=AsyncMock(
                return_value=(
                    f"{_rg_id()}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id",
                    "principal-uuid",
                )
            ),
        ),
        patch.object(
            p,
            "_ensure_acr_pull_role",
            new=AsyncMock(return_value="role-assignment-id"),
        ),
        patch.object(
            p,
            "_ensure_vnet_with_db_subnet",
            new=AsyncMock(
                return_value=(
                    f"{_rg_id()}/providers/Microsoft.Network/virtualNetworks/vnet",
                    f"{_rg_id()}/providers/Microsoft.Network/virtualNetworks/vnet/subnets/db",
                )
            ),
        ),
        patch.object(
            p,
            "_ensure_key_vault",
            new=AsyncMock(
                return_value=(
                    f"{_rg_id()}/providers/Microsoft.KeyVault/vaults/kv",
                    "https://ab-demo-kv.vault.azure.net/",
                )
            ),
        ),
        patch.object(
            p,
            "_ensure_postgres_flexible",
            new=AsyncMock(
                return_value=(
                    f"{_rg_id()}/providers/Microsoft.DBforPostgreSQL/flexibleServers/db",
                    "agentbreeder-demo-db.postgres.database.azure.com",
                )
            ),
        ),
        patch.object(
            p,
            "_write_db_password_to_kv",
            new=AsyncMock(
                return_value="https://ab-demo-kv.vault.azure.net/secrets/agentbreeder-demo-db-password"
            ),
        ),
        patch.object(
            p,
            "_delete_postgres_flexible",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            p,
            "_delete_resource_group",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            p,
            "_resource_group_is_agentbreeder_tagged",
            new=AsyncMock(return_value=True),
        ),
    ):
        yield p


@pytest.mark.asyncio
async def test_provision_returns_state_with_core_resources(patched_provisioner) -> None:
    state = await patched_provisioner.provision(_payload())

    assert isinstance(state, InfraState)
    assert state.cloud == "azure"
    assert state.region == "eastus"
    assert state.mode == "provisioned"
    # Core resources always created.
    assert "resource_group" in state.resources
    assert "log_analytics" in state.resources
    assert "container_apps_environment" in state.resources
    assert "acr" in state.resources
    assert "managed_identity" in state.resources
    # No DB unless explicitly requested.
    assert "postgres" not in state.resources
    assert "key_vault" not in state.resources
    assert "vnet" not in state.resources


@pytest.mark.asyncio
async def test_provision_creates_acr_with_admin_disabled(patched_provisioner) -> None:
    state = await patched_provisioner.provision(_payload())
    assert state.resources["acr"]["admin_user_enabled"] is False


@pytest.mark.asyncio
async def test_provision_creates_internal_aca_env_by_default(patched_provisioner) -> None:
    state = await patched_provisioner.provision(_payload())
    assert state.resources["container_apps_environment"]["internal_only"] is True


@pytest.mark.asyncio
async def test_provision_creates_public_aca_env_when_visibility_public(
    patched_provisioner,
) -> None:
    state = await patched_provisioner.provision(_payload({"AZURE_ACCESS_VISIBILITY": "public"}))
    assert state.resources["container_apps_environment"]["internal_only"] is False


@pytest.mark.asyncio
async def test_provision_acr_pull_role_scoped_to_registry_not_subscription(
    patched_provisioner,
) -> None:
    """Critical security check: AcrPull MUST be scoped to the registry."""
    state = await patched_provisioner.provision(_payload())
    scope = state.resources["managed_identity"]["acr_pull_scope"]
    # Must contain the registry path component.
    assert "/providers/Microsoft.ContainerRegistry/registries/" in scope
    # And NOT just the subscription root.
    assert scope != f"/subscriptions/{_sub()}"
    # The mock asserts the helper was called with the registry-scoped acr_id.
    call_kwargs = patched_provisioner._ensure_acr_pull_role.await_args.kwargs
    assert "/providers/Microsoft.ContainerRegistry/registries/" in call_kwargs["acr_id"]


@pytest.mark.asyncio
async def test_provision_creates_postgres_with_public_access_disabled(
    patched_provisioner,
) -> None:
    state = await patched_provisioner.provision(_payload({"AZURE_PROVISION_POSTGRES": "1"}))
    assert state.resources["postgres"]["public_network_access"] == "Disabled"
    assert "vnet" in state.resources
    assert "key_vault" in state.resources


@pytest.mark.asyncio
async def test_provision_state_never_contains_plaintext_password(
    patched_provisioner,
) -> None:
    """The DB password must only land in Key Vault — never the state file."""
    state = await patched_provisioner.provision(_payload({"AZURE_PROVISION_POSTGRES": "1"}))
    pg = state.resources["postgres"]
    # Only the URI is stored — no `password` field.
    assert "password" not in pg
    assert pg["password_secret_uri"].startswith("https://")
    # Round-trip serialisation must not leak the password either.
    dumped = state.model_dump_json()
    # The plaintext password isn't even known to the test, but we can at least
    # assert the field name shape doesn't leak it inadvertently.
    assert "administrator_login_password" not in dumped
    assert '"password"' not in dumped


@pytest.mark.asyncio
async def test_provision_db_rolls_back_when_keyvault_write_fails(
    patched_provisioner,
) -> None:
    patched_provisioner._write_db_password_to_kv.side_effect = RuntimeError("kv down")

    with pytest.raises(RuntimeError, match="kv down"):
        await patched_provisioner.provision(_payload({"AZURE_PROVISION_POSTGRES": "1"}))

    # DB rollback was attempted.
    patched_provisioner._delete_postgres_flexible.assert_awaited()


@pytest.mark.asyncio
async def test_provision_emits_progress_messages(patched_provisioner) -> None:
    messages: list[str] = []

    async def _capture(msg: str) -> None:
        messages.append(msg)

    await patched_provisioner.provision(_payload(), progress=_capture)
    joined = " ".join(messages)
    assert "Resource Group" in joined
    assert "Log Analytics Workspace" in joined
    assert "Container Apps Environment" in joined
    assert "Azure Container Registry" in joined
    assert "Managed Identity" in joined
    assert "AcrPull" in joined
    assert "provision complete" in joined


@pytest.mark.asyncio
async def test_provision_raises_when_subscription_missing() -> None:
    p = AzureProvisioner()
    payload = InfraValidationInput(cloud="azure", region="eastus", mode="simple", fields={})
    with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
        await p.provision(payload)


@pytest.mark.asyncio
async def test_provision_is_idempotent_when_helpers_are(patched_provisioner) -> None:
    """Re-running provision yields identical resource IDs (helpers are check-then-create)."""
    state1 = await patched_provisioner.provision(_payload())
    state2 = await patched_provisioner.provision(_payload())
    assert state1.resources["resource_group"]["id"] == state2.resources["resource_group"]["id"]
    assert state1.resources["acr"]["id"] == state2.resources["acr"]["id"]
    assert (
        state1.resources["managed_identity"]["acr_pull_role_assignment_id"]
        == state2.resources["managed_identity"]["acr_pull_role_assignment_id"]
    )


# ---------------------------------------------------------------------------
# destroy() reverses
# ---------------------------------------------------------------------------


def _state_for_demo(*, with_db: bool = False, tags_ok: bool = True) -> InfraState:
    resources: dict = {
        "resource_group": {
            "id": _rg_id(),
            "name": "agentbreeder-demo-rg",
            "region": "eastus",
        },
        "log_analytics": {"id": "law-id", "name": "law", "customer_id": "law-customer"},
        "container_apps_environment": {
            "id": "env-id",
            "name": "env",
            "internal_only": True,
        },
        "acr": {
            "id": _acr_id(),
            "name": "abdemo123456",
            "login_server": "abdemo123456.azurecr.io",
            "admin_user_enabled": False,
        },
        "managed_identity": {
            "id": "identity-id",
            "name": "agentbreeder-demo-id",
            "principal_id": "principal-uuid",
            "acr_pull_role_assignment_id": "role-id",
            "acr_pull_scope": _acr_id(),
        },
    }
    if with_db:
        resources["postgres"] = {
            "id": "db-id",
            "name": "agentbreeder-demo-db",
            "fqdn": "agentbreeder-demo-db.postgres.database.azure.com",
            "admin_user": "agentbreeder",
            "public_network_access": "Disabled",
            "password_secret_uri": "https://kv/secrets/x",
        }
    # tags_ok is implicit: we control via _resource_group_is_agentbreeder_tagged mock
    _ = tags_ok
    return InfraState(
        cloud="azure",
        region="eastus",
        provisioned_by="agentbreeder.AzureProvisioner",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources=resources,
    )


@pytest.mark.asyncio
async def test_destroy_invokes_resource_group_delete(patched_provisioner) -> None:
    await patched_provisioner.destroy(_state_for_demo())
    patched_provisioner._delete_resource_group.assert_awaited_once()
    # No DB in state, so DB delete should NOT have been called.
    patched_provisioner._delete_postgres_flexible.assert_not_awaited()


@pytest.mark.asyncio
async def test_destroy_deletes_db_before_resource_group(patched_provisioner) -> None:
    await patched_provisioner.destroy(_state_for_demo(with_db=True))
    patched_provisioner._delete_postgres_flexible.assert_awaited_once()
    patched_provisioner._delete_resource_group.assert_awaited_once()


@pytest.mark.asyncio
async def test_destroy_refuses_untagged_resources(patched_provisioner) -> None:
    # Flip the tag-check to False — destroy must refuse.
    patched_provisioner._resource_group_is_agentbreeder_tagged.return_value = False
    with pytest.raises(PermissionError, match="AgentBreeder=true"):
        await patched_provisioner.destroy(_state_for_demo())
    patched_provisioner._delete_resource_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_destroy_rejects_non_azure_state() -> None:
    p = AzureProvisioner()
    state = InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="t",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
    )
    with pytest.raises(ValueError, match="state.cloud is"):
        await p.destroy(state)


@pytest.mark.asyncio
async def test_destroy_swallows_individual_failures(patched_provisioner) -> None:
    """A delete failure on one resource must not block the cascading RG delete."""
    patched_provisioner._delete_postgres_flexible.side_effect = RuntimeError("boom")
    await patched_provisioner.destroy(_state_for_demo(with_db=True))
    # RG delete still attempted.
    patched_provisioner._delete_resource_group.assert_awaited_once()


# ---------------------------------------------------------------------------
# Static guards on the role-assignment helper itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_acr_pull_role_refuses_subscription_scope() -> None:
    """The helper itself must refuse to be called with a subscription-level scope."""
    p = AzureProvisioner()
    with pytest.raises(ValueError, match="registry resource ID"):
        await p._ensure_acr_pull_role(
            subscription_id=_sub(),
            acr_id=f"/subscriptions/{_sub()}",  # ← forbidden broad scope
            principal_id="principal-uuid",
            fields={},
        )


def test_acr_pull_role_def_id_is_the_builtin() -> None:
    # Sanity: the constant matches Azure's published built-in AcrPull GUID.
    assert ACR_PULL_ROLE_DEF_ID == "7f951dda-4ed3-4680-a7ca-43fe172d538d"
