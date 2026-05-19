"""Azure infrastructure validator (azure-sdk, read-only)."""

from __future__ import annotations

import logging
from typing import Any

from engine.provisioners.base import (
    InfraProvisioner,
    InfraValidationInput,
    ValidationCheck,
    ValidationResult,
)

logger = logging.getLogger(__name__)


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


class AzureProvisioner(InfraProvisioner):
    """Validates user-supplied Azure resources via read-only azure-sdk calls."""

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
