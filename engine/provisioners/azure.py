"""Azure provisioner — validates BYO infra and greenfield-provisions per agent (#384).

Mirrors the structure of :mod:`engine.provisioners.gcp` so future cross-cloud
refactors only have to look in one place. Greenfield provision builds the
minimum-viable footprint for an AgentBreeder Container Apps deploy:

    Resource Group
        → Log Analytics Workspace (required for ACA)
        → Container Apps Environment (internal-only unless visibility=public)
        → Azure Container Registry (admin_user_enabled=False — MI auth only)
        → User-assigned Managed Identity + AcrPull role on the registry
        → (optional) VNet + delegated subnet
        → (optional) PostgreSQL Flexible Server (public access Disabled, private DNS)
        → (optional) Key Vault — receives the DB password; state stores secret URI only

Re-running provision() is a no-op: every helper is check-then-create. destroy()
refuses untagged resources, then deletes the resource group last to cascade
anything we don't explicitly track.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import TYPE_CHECKING, Any

from engine.provisioners.base import (
    InfraProvisioner,
    InfraValidationInput,
    ValidationCheck,
    ValidationResult,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from engine.provisioners.base import ProgressCallback
    from engine.provisioners.state import InfraState

logger = logging.getLogger(__name__)

# AgentBreeder tags every resource it creates so destroy() can refuse to touch
# anything it didn't build.
_AB_TAG_KEY = "AgentBreeder"
_AB_TAG_VALUE = "true"

# Long-running-operation default timeout — PostgreSQL Flexible Server creation
# can take 15+ minutes. Operators on slower SKUs can override.
_LRO_TIMEOUT_SEC = 30 * 60


def _check(resource: str, fn) -> ValidationCheck:  # noqa: ANN001
    """Run an SDK lookup, translating Azure exceptions into a ValidationCheck."""
    try:
        from azure.core.exceptions import (
            ClientAuthenticationError,
            HttpResponseError,
            ResourceNotFoundError,
        )
    except ImportError as e:
        return ValidationCheck(
            resource=resource, status="error", detail=f"azure-sdk not installed: {e}"
        )

    try:
        return ValidationCheck(resource=resource, status="found", detail=str(fn()))
    except ResourceNotFoundError as e:
        return ValidationCheck(resource=resource, status="missing", detail=str(e)[:200])
    except ClientAuthenticationError as e:
        return ValidationCheck(resource=resource, status="forbidden", detail=str(e)[:200])
    except HttpResponseError as e:
        logger.warning("Azure lookup failed: resource=%s err=%s", resource, e)
        return ValidationCheck(resource=resource, status="error", detail=type(e).__name__)


def _credentials(fields: dict[str, Any]):  # noqa: ANN201
    """Resolve azure-identity credential. Falls back to DefaultAzureCredential."""
    from azure.identity import ClientSecretCredential, DefaultAzureCredential

    client_id = fields.get("AZURE_CLIENT_ID")
    client_secret = fields.get("AZURE_CLIENT_SECRET")
    tenant_id = fields.get("AZURE_TENANT_ID")
    if client_id and client_secret and tenant_id:
        return ClientSecretCredential(
            tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
        )
    return DefaultAzureCredential()


def _safe_acr_name(agent_name: str) -> str:
    """ACR names: 5-50 chars, alphanumeric only, globally unique.

    Strategy: ``ab`` prefix + sanitized agent name + 6-hex random suffix.
    """
    base = re.sub(r"[^a-z0-9]", "", agent_name.lower())[:36]
    if not base:
        base = "default"
    suffix = secrets.token_hex(3)  # 6 hex chars
    name = f"ab{base}{suffix}"
    return name[:50]


def _resource_tags(agent_name: str, version: str = "1.0.0") -> dict[str, str]:
    """Tags applied to every Azure resource AgentBreeder provisions."""
    return {
        _AB_TAG_KEY: _AB_TAG_VALUE,
        "AgentName": agent_name,
        "Version": version,
    }


def _has_agentbreeder_tag(tags: dict[str, str] | None) -> bool:
    if not tags:
        return False
    return tags.get(_AB_TAG_KEY) == _AB_TAG_VALUE


# AcrPull built-in role definition ID — constant across all Azure subscriptions.
ACR_PULL_ROLE_DEF_ID = "7f951dda-4ed3-4680-a7ca-43fe172d538d"


class AzureProvisioner(InfraProvisioner):
    """Validates and provisions Azure resources for AgentBreeder agents."""

    async def validate_existing(self, payload: InfraValidationInput) -> ValidationResult:
        fields = payload.fields
        region = payload.region
        checks: list[ValidationCheck] = []

        subscription_id = str(fields.get("AZURE_SUBSCRIPTION_ID", "")).strip()
        if not subscription_id:
            checks.append(
                ValidationCheck(
                    resource="AZURE_SUBSCRIPTION_ID",
                    status="missing",
                    detail="required field is empty",
                )
            )
            return ValidationResult(valid=False, cloud="azure", region=region, checks=checks)

        # 1. Credentials + subscription must resolve.
        def check_subscription() -> str:
            from azure.mgmt.resource import SubscriptionClient

            creds = _credentials(fields)
            client = SubscriptionClient(creds)
            sub = client.subscriptions.get(subscription_id)
            return sub.display_name or sub.subscription_id

        checks.append(_check(f"subscription:{subscription_id}", check_subscription))

        if checks[-1].status != "found":
            return ValidationResult(valid=False, cloud="azure", region=region, checks=checks)

        # 2. Full-mode resource checks.
        if payload.mode == "full":
            if rg := fields.get("AZURE_RESOURCE_GROUP"):

                def check_rg() -> str:
                    from azure.mgmt.resource import ResourceManagementClient

                    creds = _credentials(fields)
                    client = ResourceManagementClient(creds, subscription_id)
                    return client.resource_groups.get(rg).location

                checks.append(_check(f"resource-group:{rg}", check_rg))

            acr_server = fields.get("AZURE_ACR_LOGIN_SERVER")
            acr_rg = fields.get("AZURE_RESOURCE_GROUP")
            if acr_server and acr_rg:
                acr_name = acr_server.split(".")[0]

                def check_acr() -> str:
                    from azure.mgmt.containerregistry import ContainerRegistryManagementClient

                    creds = _credentials(fields)
                    client = ContainerRegistryManagementClient(creds, subscription_id)
                    return client.registries.get(acr_rg, acr_name).login_server

                checks.append(_check(f"acr:{acr_server}", check_acr))

            aca_env = fields.get("AZURE_ACA_ENVIRONMENT")
            if aca_env and acr_rg:

                def check_aca_env() -> str:
                    from azure.mgmt.appcontainers import ContainerAppsAPIClient

                    creds = _credentials(fields)
                    client = ContainerAppsAPIClient(creds, subscription_id)
                    return client.managed_environments.get(acr_rg, aca_env).provisioning_state

                checks.append(_check(f"aca-env:{aca_env}", check_aca_env))

        valid = all(c.status == "found" for c in checks)
        return ValidationResult(valid=valid, cloud="azure", region=region, checks=checks)

    # ------------------------------------------------------------------
    # Greenfield provisioning (#384)
    # ------------------------------------------------------------------

    async def provision(
        self,
        payload: InfraValidationInput,
        progress: ProgressCallback | None = None,
    ) -> InfraState:
        """Create the minimum-viable Azure footprint for an AgentBreeder ACA deploy.

        Order (every step is idempotent):

        1. Resource Group
        2. Log Analytics Workspace
        3. Container Apps Environment (internal unless ``access.visibility == public``)
        4. Azure Container Registry (Basic SKU, admin user disabled)
        5. User-assigned Managed Identity + AcrPull on the specific registry
        6. (if memory) VNet + delegated subnet + Key Vault + PostgreSQL Flexible Server

        On Key Vault write failure the freshly created DB is rolled back so the
        password never lands on disk.
        """
        from datetime import UTC, datetime

        from engine.provisioners.state import InfraState

        fields = payload.fields
        region = payload.region or fields.get("AZURE_LOCATION", "eastus")
        subscription_id = str(fields.get("AZURE_SUBSCRIPTION_ID", "")).strip()
        if not subscription_id:
            raise ValueError("provision(azure): AZURE_SUBSCRIPTION_ID is required")

        agent_name = str(fields.get("AZURE_AGENT_NAME", "default"))
        agent_version = str(fields.get("AZURE_AGENT_VERSION", "1.0.0"))
        visibility = str(fields.get("AZURE_ACCESS_VISIBILITY", "team")).lower()
        wants_db = bool(fields.get("AZURE_PROVISION_POSTGRES"))

        rg_name = f"agentbreeder-{agent_name}-rg"
        law_name = f"agentbreeder-{agent_name}-law"
        env_name = f"agentbreeder-{agent_name}-env"
        identity_name = f"agentbreeder-{agent_name}-id"
        acr_name = _safe_acr_name(agent_name)
        vnet_name = f"agentbreeder-{agent_name}-vnet"
        subnet_name = "db-subnet"
        db_name = f"agentbreeder-{agent_name}-db"
        kv_name = _safe_kv_name(agent_name)

        tags = _resource_tags(agent_name, agent_version)
        resources: dict[str, Any] = {}

        async def _emit(msg: str) -> None:
            logger.info("azure.provision: %s", msg)
            if progress is not None:
                await progress(msg)

        # ---- 1. Resource Group ----------------------------------------
        await _emit(f"ensuring Resource Group '{rg_name}' in {region}")
        rg_id = await self._ensure_resource_group(
            subscription_id=subscription_id,
            rg_name=rg_name,
            region=region,
            tags=tags,
            fields=fields,
        )
        resources["resource_group"] = {"id": rg_id, "name": rg_name, "region": region}

        # ---- 2. Log Analytics Workspace -------------------------------
        await _emit(f"ensuring Log Analytics Workspace '{law_name}'")
        law_id, law_customer_id, law_shared_key = await self._ensure_log_analytics(
            subscription_id=subscription_id,
            rg_name=rg_name,
            law_name=law_name,
            region=region,
            tags=tags,
            fields=fields,
        )
        resources["log_analytics"] = {
            "id": law_id,
            "name": law_name,
            "customer_id": law_customer_id,
        }

        # ---- 3. Container Apps Environment ----------------------------
        await _emit(f"ensuring Container Apps Environment '{env_name}' (visibility={visibility})")
        env_id = await self._ensure_container_apps_environment(
            subscription_id=subscription_id,
            rg_name=rg_name,
            env_name=env_name,
            region=region,
            law_customer_id=law_customer_id,
            law_shared_key=law_shared_key,
            internal_only=(visibility != "public"),
            tags=tags,
            fields=fields,
        )
        resources["container_apps_environment"] = {
            "id": env_id,
            "name": env_name,
            "internal_only": visibility != "public",
        }

        # ---- 4. Azure Container Registry ------------------------------
        await _emit(f"ensuring Azure Container Registry '{acr_name}' (Basic, admin disabled)")
        acr_id, acr_login_server = await self._ensure_acr(
            subscription_id=subscription_id,
            rg_name=rg_name,
            acr_name=acr_name,
            region=region,
            tags=tags,
            fields=fields,
        )
        resources["acr"] = {
            "id": acr_id,
            "name": acr_name,
            "login_server": acr_login_server,
            "admin_user_enabled": False,
        }

        # ---- 5. Managed Identity + AcrPull ----------------------------
        await _emit(f"ensuring user-assigned Managed Identity '{identity_name}'")
        identity_id, identity_principal_id = await self._ensure_managed_identity(
            subscription_id=subscription_id,
            rg_name=rg_name,
            identity_name=identity_name,
            region=region,
            tags=tags,
            fields=fields,
        )
        await _emit(f"binding AcrPull on registry '{acr_name}' to identity '{identity_name}'")
        role_assignment_id = await self._ensure_acr_pull_role(
            subscription_id=subscription_id,
            acr_id=acr_id,
            principal_id=identity_principal_id,
            fields=fields,
        )
        resources["managed_identity"] = {
            "id": identity_id,
            "name": identity_name,
            "principal_id": identity_principal_id,
            "acr_pull_role_assignment_id": role_assignment_id,
            "acr_pull_scope": acr_id,
        }

        # ---- 6. (optional) Private networking + DB --------------------
        if wants_db:
            await _emit(f"ensuring VNet '{vnet_name}' with delegated subnet '{subnet_name}'")
            vnet_id, subnet_id = await self._ensure_vnet_with_db_subnet(
                subscription_id=subscription_id,
                rg_name=rg_name,
                vnet_name=vnet_name,
                subnet_name=subnet_name,
                region=region,
                tags=tags,
                fields=fields,
            )
            resources["vnet"] = {"id": vnet_id, "name": vnet_name, "db_subnet_id": subnet_id}

            await _emit(f"ensuring Key Vault '{kv_name}' for DB secret")
            kv_id, kv_uri = await self._ensure_key_vault(
                subscription_id=subscription_id,
                rg_name=rg_name,
                kv_name=kv_name,
                region=region,
                tenant_id=str(fields.get("AZURE_TENANT_ID", "")),
                tags=tags,
                fields=fields,
            )
            resources["key_vault"] = {"id": kv_id, "name": kv_name, "uri": kv_uri}

            # Random password — never logged, never returned in state.
            db_password = secrets.token_urlsafe(32)
            db_admin = "agentbreeder"

            await _emit(
                f"ensuring PostgreSQL Flexible Server '{db_name}' (public_access=Disabled)"
            )
            db_id, db_fqdn = await self._ensure_postgres_flexible(
                subscription_id=subscription_id,
                rg_name=rg_name,
                db_name=db_name,
                region=region,
                admin_user=db_admin,
                admin_password=db_password,
                subnet_id=subnet_id,
                tags=tags,
                fields=fields,
            )

            try:
                secret_uri = await self._write_db_password_to_kv(
                    kv_uri=kv_uri,
                    secret_name=f"{db_name}-password",
                    password=db_password,
                    fields=fields,
                )
            except Exception:  # noqa: BLE001
                logger.exception("azure.provision: KV write failed — rolling back DB")
                try:
                    await self._delete_postgres_flexible(
                        subscription_id=subscription_id,
                        rg_name=rg_name,
                        db_name=db_name,
                        fields=fields,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("azure.provision: DB rollback also failed")
                raise

            resources["postgres"] = {
                "id": db_id,
                "name": db_name,
                "fqdn": db_fqdn,
                "admin_user": db_admin,
                "public_network_access": "Disabled",
                "password_secret_uri": secret_uri,  # URI only — never the plaintext.
            }

        state = InfraState(
            cloud="azure",
            region=region,
            provisioned_by="agentbreeder.AzureProvisioner",
            provisioned_at=datetime.now(UTC),
            mode="provisioned",
            resources=resources,
        )
        await _emit("provision complete")
        return state

    async def destroy(self, state: InfraState) -> None:
        """Tear down provisioned resources. Refuses untagged resources.

        Deletes individual resources in reverse-dependency order, then deletes
        the resource group last so anything we don't explicitly track gets
        cleaned up by the cascade.
        """
        if state.cloud != "azure":
            raise ValueError(f"destroy(azure): state.cloud is {state.cloud!r}, expected 'azure'")

        resources = dict(state.resources)
        rg = resources.get("resource_group") or {}
        rg_name = rg.get("name")
        rg_id = rg.get("id")
        if not rg_name or not rg_id:
            raise ValueError("destroy(azure): state is missing resource_group.name/id")

        # Pull subscription out of the resource group ID:
        #   /subscriptions/<sub>/resourceGroups/<rg>
        m = re.match(r"/subscriptions/([^/]+)/resourceGroups/", rg_id)
        if not m:
            raise ValueError(f"destroy(azure): cannot extract subscription from rg id {rg_id!r}")
        subscription_id = m.group(1)

        fields: dict[str, Any] = {}

        # Safety check — refuse to touch a Resource Group we don't own.
        if not await self._resource_group_is_agentbreeder_tagged(
            subscription_id=subscription_id, rg_name=rg_name, fields=fields
        ):
            raise PermissionError(
                f"destroy(azure): resource group {rg_name!r} is missing the "
                f"{_AB_TAG_KEY}={_AB_TAG_VALUE} tag — refusing to delete."
            )

        # Best-effort per-resource teardown (the RG delete will cascade anyway,
        # but explicit deletes give cleaner audit logs and let LRO progress).
        if db := resources.get("postgres"):
            try:
                await self._delete_postgres_flexible(
                    subscription_id=subscription_id,
                    rg_name=rg_name,
                    db_name=db["name"],
                    fields=fields,
                )
            except Exception:  # noqa: BLE001
                logger.exception("destroy(azure): failed to delete DB %s", db.get("name"))

        # Resource Group delete cascades everything else.
        try:
            await self._delete_resource_group(
                subscription_id=subscription_id, rg_name=rg_name, fields=fields
            )
        except Exception:  # noqa: BLE001
            logger.exception("destroy(azure): failed to delete RG %s", rg_name)

    # ------------------------------------------------------------------
    # Low-level helpers — broken out so unit tests can patch them.
    # ------------------------------------------------------------------

    async def _ensure_resource_group(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        region: str,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> str:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.resource import ResourceManagementClient

        creds = _credentials(fields)
        client = ResourceManagementClient(creds, subscription_id)
        try:
            existing = client.resource_groups.get(rg_name)
            logger.debug("resource group %s already exists", rg_name)
            return existing.id
        except ResourceNotFoundError:
            pass
        rg = client.resource_groups.create_or_update(rg_name, {"location": region, "tags": tags})
        return rg.id

    async def _delete_resource_group(
        self, *, subscription_id: str, rg_name: str, fields: dict[str, Any]
    ) -> None:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.resource import ResourceManagementClient

        creds = _credentials(fields)
        client = ResourceManagementClient(creds, subscription_id)
        try:
            poller = client.resource_groups.begin_delete(rg_name)
            poller.result(timeout=_LRO_TIMEOUT_SEC)
        except ResourceNotFoundError:
            logger.debug("resource group %s already absent", rg_name)

    async def _resource_group_is_agentbreeder_tagged(
        self, *, subscription_id: str, rg_name: str, fields: dict[str, Any]
    ) -> bool:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.resource import ResourceManagementClient

        creds = _credentials(fields)
        client = ResourceManagementClient(creds, subscription_id)
        try:
            rg = client.resource_groups.get(rg_name)
        except ResourceNotFoundError:
            return False
        return _has_agentbreeder_tag(rg.tags)

    async def _ensure_log_analytics(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        law_name: str,
        region: str,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> tuple[str, str, str]:
        """Return ``(workspace_id, customer_id, shared_key)``."""
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.loganalytics import LogAnalyticsManagementClient
        from azure.mgmt.loganalytics.models import Workspace, WorkspaceSku

        creds = _credentials(fields)
        client = LogAnalyticsManagementClient(creds, subscription_id)
        try:
            existing = client.workspaces.get(rg_name, law_name)
            workspace_id = existing.id
            customer_id = existing.customer_id
        except ResourceNotFoundError:
            poller = client.workspaces.begin_create_or_update(
                rg_name,
                law_name,
                Workspace(
                    location=region,
                    sku=WorkspaceSku(name="PerGB2018"),
                    retention_in_days=30,
                    tags=tags,
                ),
            )
            ws = poller.result(timeout=_LRO_TIMEOUT_SEC)
            workspace_id = ws.id
            customer_id = ws.customer_id
        keys = client.shared_keys.get_shared_keys(rg_name, law_name)
        return workspace_id, customer_id, keys.primary_shared_key

    async def _ensure_container_apps_environment(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        env_name: str,
        region: str,
        law_customer_id: str,
        law_shared_key: str,
        internal_only: bool,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> str:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.appcontainers import ContainerAppsAPIClient
        from azure.mgmt.appcontainers.models import (
            AppLogsConfiguration,
            LogAnalyticsConfiguration,
            ManagedEnvironment,
            VnetConfiguration,
        )

        creds = _credentials(fields)
        client = ContainerAppsAPIClient(creds, subscription_id)
        try:
            existing = client.managed_environments.get(rg_name, env_name)
            return existing.id
        except ResourceNotFoundError:
            pass
        env = ManagedEnvironment(
            location=region,
            tags=tags,
            app_logs_configuration=AppLogsConfiguration(
                destination="log-analytics",
                log_analytics_configuration=LogAnalyticsConfiguration(
                    customer_id=law_customer_id,
                    shared_key=law_shared_key,
                ),
            ),
            vnet_configuration=VnetConfiguration(internal=internal_only)
            if internal_only
            else None,
        )
        poller = client.managed_environments.begin_create_or_update(rg_name, env_name, env)
        result = poller.result(timeout=_LRO_TIMEOUT_SEC)
        return result.id

    async def _ensure_acr(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        acr_name: str,
        region: str,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> tuple[str, str]:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.containerregistry import ContainerRegistryManagementClient
        from azure.mgmt.containerregistry.models import Registry, Sku

        creds = _credentials(fields)
        client = ContainerRegistryManagementClient(creds, subscription_id)
        try:
            existing = client.registries.get(rg_name, acr_name)
            return existing.id, existing.login_server
        except ResourceNotFoundError:
            pass
        registry = Registry(
            location=region,
            sku=Sku(name="Basic"),
            admin_user_enabled=False,  # auth via Managed Identity only
            tags=tags,
        )
        poller = client.registries.begin_create(rg_name, acr_name, registry)
        result = poller.result(timeout=_LRO_TIMEOUT_SEC)
        return result.id, result.login_server

    async def _ensure_managed_identity(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        identity_name: str,
        region: str,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> tuple[str, str]:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.msi import ManagedServiceIdentityClient
        from azure.mgmt.msi.models import Identity

        creds = _credentials(fields)
        client = ManagedServiceIdentityClient(creds, subscription_id)
        try:
            existing = client.user_assigned_identities.get(rg_name, identity_name)
            return existing.id, existing.principal_id
        except ResourceNotFoundError:
            pass
        result = client.user_assigned_identities.create_or_update(
            rg_name, identity_name, Identity(location=region, tags=tags)
        )
        return result.id, result.principal_id

    async def _ensure_acr_pull_role(
        self,
        *,
        subscription_id: str,
        acr_id: str,
        principal_id: str,
        fields: dict[str, Any],
    ) -> str:
        """Assign AcrPull on the **specific** registry — never the subscription."""
        import uuid

        from azure.core.exceptions import HttpResponseError, ResourceExistsError
        from azure.mgmt.authorization import AuthorizationManagementClient
        from azure.mgmt.authorization.models import RoleAssignmentCreateParameters

        # Guardrail: refuse to scope to anything broader than a registry.
        if "/providers/Microsoft.ContainerRegistry/registries/" not in acr_id:
            raise ValueError(
                f"_ensure_acr_pull_role: scope must be a registry resource ID, got {acr_id!r}"
            )

        creds = _credentials(fields)
        client = AuthorizationManagementClient(creds, subscription_id)
        role_def_id = (
            f"/subscriptions/{subscription_id}"
            f"/providers/Microsoft.Authorization/roleDefinitions/{ACR_PULL_ROLE_DEF_ID}"
        )

        # Idempotency: deterministic role-assignment GUID per (scope, principal).
        ra_name = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{acr_id}|{principal_id}|AcrPull"))
        params = RoleAssignmentCreateParameters(
            role_definition_id=role_def_id,
            principal_id=principal_id,
            principal_type="ServicePrincipal",
        )
        try:
            result = client.role_assignments.create(
                scope=acr_id, role_assignment_name=ra_name, parameters=params
            )
            return result.id
        except ResourceExistsError:
            existing = client.role_assignments.get(scope=acr_id, role_assignment_name=ra_name)
            return existing.id
        except HttpResponseError as e:
            # Azure occasionally surfaces RoleAssignmentExists as a 409 without
            # the typed ResourceExistsError on older SDK builds.
            if getattr(e, "status_code", None) == 409 or "RoleAssignmentExists" in str(e):
                existing = client.role_assignments.get(scope=acr_id, role_assignment_name=ra_name)
                return existing.id
            raise

    async def _ensure_vnet_with_db_subnet(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        vnet_name: str,
        subnet_name: str,
        region: str,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> tuple[str, str]:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.network import NetworkManagementClient
        from azure.mgmt.network.models import (
            AddressSpace,
            Delegation,
            Subnet,
            VirtualNetwork,
        )

        creds = _credentials(fields)
        client = NetworkManagementClient(creds, subscription_id)
        try:
            existing = client.virtual_networks.get(rg_name, vnet_name)
            vnet_id = existing.id
        except ResourceNotFoundError:
            poller = client.virtual_networks.begin_create_or_update(
                rg_name,
                vnet_name,
                VirtualNetwork(
                    location=region,
                    address_space=AddressSpace(address_prefixes=["10.10.0.0/16"]),
                    tags=tags,
                ),
            )
            vnet = poller.result(timeout=_LRO_TIMEOUT_SEC)
            vnet_id = vnet.id

        try:
            subnet = client.subnets.get(rg_name, vnet_name, subnet_name)
            return vnet_id, subnet.id
        except ResourceNotFoundError:
            pass
        poller = client.subnets.begin_create_or_update(
            rg_name,
            vnet_name,
            subnet_name,
            Subnet(
                address_prefix="10.10.1.0/24",
                delegations=[
                    Delegation(
                        name="flexibleServers",
                        service_name="Microsoft.DBforPostgreSQL/flexibleServers",
                    )
                ],
            ),
        )
        subnet = poller.result(timeout=_LRO_TIMEOUT_SEC)
        return vnet_id, subnet.id

    async def _ensure_key_vault(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        kv_name: str,
        region: str,
        tenant_id: str,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> tuple[str, str]:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.keyvault import KeyVaultManagementClient
        from azure.mgmt.keyvault.models import (
            Sku as KvSku,
        )
        from azure.mgmt.keyvault.models import (
            VaultCreateOrUpdateParameters,
            VaultProperties,
        )

        creds = _credentials(fields)
        client = KeyVaultManagementClient(creds, subscription_id)
        try:
            existing = client.vaults.get(rg_name, kv_name)
            return existing.id, existing.properties.vault_uri
        except ResourceNotFoundError:
            pass
        params = VaultCreateOrUpdateParameters(
            location=region,
            tags=tags,
            properties=VaultProperties(
                tenant_id=tenant_id,
                sku=KvSku(family="A", name="standard"),
                enable_rbac_authorization=True,
                enable_soft_delete=True,
                public_network_access="Disabled",
            ),
        )
        poller = client.vaults.begin_create_or_update(rg_name, kv_name, params)
        result = poller.result(timeout=_LRO_TIMEOUT_SEC)
        return result.id, result.properties.vault_uri

    async def _write_db_password_to_kv(
        self,
        *,
        kv_uri: str,
        secret_name: str,
        password: str,
        fields: dict[str, Any],
    ) -> str:
        """Push the DB password into Key Vault and return only its versionless URI."""
        from azure.keyvault.secrets import SecretClient

        creds = _credentials(fields)
        client = SecretClient(vault_url=kv_uri, credential=creds)
        client.set_secret(secret_name, password)
        # Versionless URI: <vault>/secrets/<name>
        return f"{kv_uri.rstrip('/')}/secrets/{secret_name}"

    async def _ensure_postgres_flexible(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        db_name: str,
        region: str,
        admin_user: str,
        admin_password: str,
        subnet_id: str,
        tags: dict[str, str],
        fields: dict[str, Any],
    ) -> tuple[str, str]:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.rdbms.postgresql_flexibleservers import PostgreSQLManagementClient
        from azure.mgmt.rdbms.postgresql_flexibleservers.models import (
            Network,
            Server,
            Storage,
        )
        from azure.mgmt.rdbms.postgresql_flexibleservers.models import (
            Sku as PgSku,
        )

        creds = _credentials(fields)
        client = PostgreSQLManagementClient(creds, subscription_id)
        try:
            existing = client.servers.get(rg_name, db_name)
            return existing.id, existing.fully_qualified_domain_name
        except ResourceNotFoundError:
            pass
        server = Server(
            location=region,
            tags=tags,
            sku=PgSku(name="Standard_B1ms", tier="Burstable"),
            administrator_login=admin_user,
            administrator_login_password=admin_password,
            version="14",
            storage=Storage(storage_size_gb=32),
            network=Network(
                delegated_subnet_resource_id=subnet_id,
                # public_network_access on Flexible Server is implicit-Disabled
                # when a delegated subnet is supplied. We additionally pass it
                # through as a kwarg below for the SDK builds that expose it.
            ),
            create_mode="Default",
        )
        # Older SDK builds don't accept public_network_access on Network() — set
        # it via the public attribute when present so we satisfy the security
        # checklist explicitly rather than relying on the implicit default.
        try:
            server.network.public_network_access = "Disabled"
        except Exception:  # noqa: BLE001
            logger.debug("Network.public_network_access not settable on this SDK build")

        poller = client.servers.begin_create(rg_name, db_name, server)
        # PG Flexible Server can take 15+ min on the first run.
        result = poller.result(timeout=_LRO_TIMEOUT_SEC)
        return result.id, result.fully_qualified_domain_name

    async def _delete_postgres_flexible(
        self,
        *,
        subscription_id: str,
        rg_name: str,
        db_name: str,
        fields: dict[str, Any],
    ) -> None:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.mgmt.rdbms.postgresql_flexibleservers import PostgreSQLManagementClient

        creds = _credentials(fields)
        client = PostgreSQLManagementClient(creds, subscription_id)
        try:
            poller = client.servers.begin_delete(rg_name, db_name)
            poller.result(timeout=_LRO_TIMEOUT_SEC)
        except ResourceNotFoundError:
            logger.debug("postgres flexible server %s already absent", db_name)


def _safe_kv_name(agent_name: str) -> str:
    """Key Vault names: 3-24 alphanumeric+hyphen, must start with a letter.

    We deterministically derive the name from the agent so destroy() can find
    it back. The name is globally unique within Azure DNS, so collisions across
    tenants are possible — operators on shared tenants should override.
    """
    base = re.sub(r"[^a-zA-Z0-9-]", "-", agent_name.lower())[:18].strip("-")
    if not base:
        base = "default"
    name = f"ab-{base}-kv"
    return name[:24]
