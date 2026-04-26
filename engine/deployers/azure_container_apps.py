"""Azure Container Apps deployer.

Deploys agents to Azure Container Apps with:
- Azure Container Registry (ACR) for container images
- Auto-scaling via KEDA-based rules
- DefaultAzureCredential for auth (Managed Identity, CLI, env, etc.)
- Container Apps Environment as the hosting runtime

Cloud-specific logic stays in this module — never leak Azure details elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from engine.config_parser import AgentConfig
from engine.deployers.base import BaseDeployer, DeployResult, HealthStatus, InfraResult
from engine.runtimes.base import ContainerImage

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_LOCATION = "eastus"
DEFAULT_MIN_REPLICAS = 0  # Scale-to-zero
DEFAULT_MAX_REPLICAS = 10
DEFAULT_CPU = 0.5
DEFAULT_MEMORY = "1.0Gi"
DEFAULT_TARGET_PORT = 8080
HEALTH_CHECK_TIMEOUT = 120
HEALTH_CHECK_INTERVAL = 5


class AzureConfig(BaseModel):
    """Azure-specific configuration extracted from AgentConfig.deploy."""

    subscription_id: str
    resource_group: str
    location: str = DEFAULT_LOCATION
    container_apps_env: str
    registry_server: str
    registry_username: str | None = None
    registry_password: str | None = None


def _extract_azure_config(config: AgentConfig) -> AzureConfig:
    """Extract Azure Container Apps config from the agent's deploy section.

    Required fields must be set in deploy.env_vars.
    Optional fields have sensible defaults.
    """
    env = config.deploy.env_vars

    subscription_id = env.get("AZURE_SUBSCRIPTION_ID", "")
    if not subscription_id:
        msg = (
            "Azure subscription ID is required for Container Apps deployment. "
            "Set AZURE_SUBSCRIPTION_ID in deploy.env_vars."
        )
        raise ValueError(msg)

    resource_group = env.get("AZURE_RESOURCE_GROUP", "")
    if not resource_group:
        msg = (
            "Azure resource group is required for Container Apps deployment. "
            "Set AZURE_RESOURCE_GROUP in deploy.env_vars."
        )
        raise ValueError(msg)

    container_apps_env = env.get("AZURE_CONTAINER_APPS_ENV", "")
    if not container_apps_env:
        msg = (
            "Azure Container Apps Environment name is required. "
            "Set AZURE_CONTAINER_APPS_ENV in deploy.env_vars."
        )
        raise ValueError(msg)

    registry_server = env.get("AZURE_REGISTRY_SERVER", "")
    if not registry_server:
        msg = (
            "Azure Container Registry server is required. "
            "Set AZURE_REGISTRY_SERVER in deploy.env_vars "
            "(e.g., myregistry.azurecr.io)."
        )
        raise ValueError(msg)

    return AzureConfig(
        subscription_id=subscription_id,
        resource_group=resource_group,
        location=env.get("AZURE_LOCATION", config.deploy.region or DEFAULT_LOCATION),
        container_apps_env=container_apps_env,
        registry_server=registry_server,
        registry_username=env.get("AZURE_REGISTRY_USERNAME"),
        registry_password=env.get("AZURE_REGISTRY_PASSWORD"),
    )


def _get_acr_image_uri(azure_config: AzureConfig, agent_name: str, version: str) -> str:
    """Build the full ACR image URI.

    Format: {registry_server}/{agent_name}:{version}
    """
    return f"{azure_config.registry_server}/{agent_name}:{version}"


class AzureContainerAppsDeployer(BaseDeployer):
    """Deploys agents to Azure Container Apps.

    Uses the Azure Container Apps management API and Azure Container Registry
    for container images. Supports DefaultAzureCredential for authentication
    (Managed Identity, Azure CLI, environment variables, etc.).
    """

    def __init__(self) -> None:
        self._azure_config: AzureConfig | None = None
        self._image_uri: str | None = None

    def _get_credential(self) -> Any:
        """Get the Azure credential using DefaultAzureCredential.

        Raises ImportError with install instructions if the SDK is missing.
        Tries: Managed Identity → Azure CLI → environment variables.
        """
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as e:
            msg = "Azure Identity SDK not installed. Run: pip install agentbreeder[azure]"
            raise ImportError(msg) from e
        return DefaultAzureCredential()

    def _get_aca_client(self) -> Any:
        """Get the Azure Container Apps management client.

        Raises ImportError with install instructions if the SDK is missing.
        """
        try:
            from azure.mgmt.appcontainers import ContainerAppsAPIClient
        except ImportError as e:
            msg = "Azure Container Apps SDK not installed. Run: pip install agentbreeder[azure]"
            raise ImportError(msg) from e

        if self._azure_config is None:
            msg = "Azure config not initialized. Call provision() first."
            raise RuntimeError(msg)

        credential = self._get_credential()
        return ContainerAppsAPIClient(
            credential=credential,
            subscription_id=self._azure_config.subscription_id,
        )

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Provision Azure infrastructure for the agent.

        Steps:
        1. Validate Azure config (subscription, resource group, etc.)
        2. Verify the Container Apps Environment exists (not auto-created)
        3. Return the expected Container Apps endpoint URL

        The Container Apps Environment must be pre-created — it requires
        a Log Analytics workspace and optional VNet integration which is
        too complex to auto-provision. Use the Azure portal or az CLI:
            az containerapp env create --name <env> --resource-group <rg> ...
        """
        self._azure_config = _extract_azure_config(config)
        azure = self._azure_config

        logger.info(
            "Provisioning Azure Container Apps for '%s' in resource group '%s' location '%s'",
            config.name,
            azure.resource_group,
            azure.location,
        )

        # Validate that the Container Apps Environment exists
        await self._validate_environment_exists(azure)

        # Compute image URI
        self._image_uri = _get_acr_image_uri(azure, config.name, config.version)

        # Azure Container Apps FQDN format:
        # https://{app-name}.{unique-id}.{location}.azurecontainerapps.io
        # We return a predictable placeholder — real FQDN comes after deploy
        expected_url = f"https://{config.name}.{azure.location}.azurecontainerapps.io"

        return InfraResult(
            endpoint_url=expected_url,
            resource_ids={
                "subscription_id": azure.subscription_id,
                "resource_group": azure.resource_group,
                "location": azure.location,
                "container_apps_env": azure.container_apps_env,
                "image_uri": self._image_uri,
            },
        )

    async def _validate_environment_exists(self, azure: AzureConfig) -> None:
        """Verify the Container Apps Environment exists in the resource group.

        Raises ValueError with a helpful message if it doesn't exist.
        The environment must be pre-created — auto-creation is intentionally
        not supported due to the complexity of Log Analytics workspace setup.
        """
        try:
            aca_client = self._get_aca_client()
            aca_client.managed_environments.get(
                resource_group_name=azure.resource_group,
                environment_name=azure.container_apps_env,
            )
            logger.info(
                "Container Apps Environment '%s' found in resource group '%s'",
                azure.container_apps_env,
                azure.resource_group,
            )
        except ImportError:
            # SDK not installed — log a warning and continue
            # The deploy step will fail with a clear error if needed
            logger.warning(
                "Azure SDK not available — skipping environment validation. "
                "Ensure Container Apps Environment '%s' exists in resource group '%s'.",
                azure.container_apps_env,
                azure.resource_group,
            )
        except Exception as e:
            err_str = str(e).lower()
            if "not found" in err_str or "resourcenotfound" in err_str or "404" in err_str:
                msg = (
                    f"Azure Container Apps Environment '{azure.container_apps_env}' "
                    f"not found in resource group '{azure.resource_group}'. "
                    "Create it first with:\n"
                    f"  az containerapp env create \\\n"
                    f"    --name {azure.container_apps_env} \\\n"
                    f"    --resource-group {azure.resource_group} \\\n"
                    f"    --location {azure.location} \\\n"
                    "    --logs-workspace-id <log-analytics-workspace-id> \\\n"
                    "    --logs-workspace-key <log-analytics-workspace-key>"
                )
                raise ValueError(msg) from e
            # Any other error (auth, network) — log and continue
            logger.warning(
                "Could not validate Container Apps Environment '%s': %s",
                azure.container_apps_env,
                e,
            )

    async def _push_image(self, image: ContainerImage, image_uri: str) -> None:
        """Tag and push the container image to Azure Container Registry.

        Uses the Docker SDK to build the locally-built image and push it to ACR.
        Requires `az acr login --name <registry>` or AZURE_REGISTRY_USERNAME/PASSWORD
        to be set for authentication.
        """
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise ImportError(msg) from e

        client = docker.from_env()

        # Build the image locally first
        logger.info("Building Docker image: %s", image.tag)
        built_image, build_logs = client.images.build(
            path=str(image.context_dir),
            tag=image.tag,
            rm=True,
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = chunk["stream"].strip()
                if line:
                    logger.debug("  %s", line)

        # Tag for ACR
        logger.info("Tagging image as %s", image_uri)
        built_image.tag(image_uri)

        # Build push kwargs — include auth if credentials provided
        push_kwargs: dict[str, Any] = {"stream": True, "decode": True}
        if self._azure_config and self._azure_config.registry_username:
            push_kwargs["auth_config"] = {
                "username": self._azure_config.registry_username,
                "password": self._azure_config.registry_password or "",
            }

        # Push to ACR
        logger.info("Pushing image to ACR: %s", image_uri)
        push_output = client.images.push(image_uri, **push_kwargs)
        for chunk in push_output:
            if "status" in chunk:
                logger.debug("  %s", chunk["status"])
            if "error" in chunk:
                msg = f"Image push to ACR failed: {chunk['error']}"
                raise RuntimeError(msg)

        logger.info("Image pushed to ACR successfully: %s", image_uri)

    def _build_container_app_body(
        self,
        config: AgentConfig,
        azure: AzureConfig,
        image_uri: str,
        managed_env_id: str,
    ) -> dict[str, Any]:
        """Build the ContainerApp resource definition dict.

        Constructs the template for the Container App resource including
        ingress, registry credentials, container spec, and scaling rules.
        """
        # Parse resource config — Azure Container Apps uses numeric CPU
        cpu_str = config.deploy.resources.cpu or str(DEFAULT_CPU)
        try:
            cpu = float(cpu_str)
        except ValueError:
            cpu = DEFAULT_CPU
        memory = config.deploy.resources.memory or DEFAULT_MEMORY

        # Environment variables for the container
        env_vars = [
            {"name": "AGENT_NAME", "value": config.name},
            {"name": "AGENT_VERSION", "value": config.version},
            {
                "name": "AGENT_FRAMEWORK",
                "value": config.framework.value
                if config.framework
                else (config.runtime.framework if config.runtime else "unknown"),
            },
        ]
        # Add user-defined env vars, excluding AZURE_ prefixed infra config vars
        for key, value in config.deploy.env_vars.items():
            if not key.startswith("AZURE_"):
                env_vars.append({"name": key, "value": value})

        # Scaling
        min_replicas = max(0, config.deploy.scaling.min)
        max_replicas = config.deploy.scaling.max or DEFAULT_MAX_REPLICAS

        # Registry credentials
        registries: list[dict[str, Any]] = []
        if azure.registry_username:
            registries.append(
                {
                    "server": azure.registry_server,
                    "username": azure.registry_username,
                    "passwordSecretRef": "acr-password",
                }
            )

        secrets: list[dict[str, Any]] = []
        if azure.registry_password:
            secrets.append(
                {
                    "name": "acr-password",
                    "value": azure.registry_password,
                }
            )

        body: dict[str, Any] = {
            "location": azure.location,
            "tags": {
                "managed-by": "agentbreeder",
                "agent-name": config.name,
                "agent-version": config.version.replace(".", "-"),
                "team": config.team,
            },
            "properties": {
                "managedEnvironmentId": managed_env_id,
                "configuration": {
                    "ingress": {
                        "targetPort": DEFAULT_TARGET_PORT,
                        "external": True,
                        "transport": "auto",
                    },
                    "registries": registries,
                    "secrets": secrets,
                },
                "template": {
                    "containers": [
                        {
                            "name": config.name,
                            "image": image_uri,
                            "env": env_vars,
                            "resources": {
                                "cpu": cpu,
                                "memory": memory,
                            },
                        }
                    ],
                    "scale": {
                        "minReplicas": min_replicas,
                        "maxReplicas": max_replicas,
                    },
                },
            },
        }

        return body

    async def deploy(self, config: AgentConfig, image: ContainerImage | None) -> DeployResult:
        """Build, push, and deploy the agent to Azure Container Apps.

        Steps:
        1. Build and push container image to ACR
        2. Look up the Container Apps Environment resource ID
        3. Create or update the Container App
        4. Return the app FQDN as the endpoint URL
        """
        if self._azure_config is None:
            self._azure_config = _extract_azure_config(config)
        azure = self._azure_config

        if self._image_uri is None:
            self._image_uri = _get_acr_image_uri(azure, config.name, config.version)

        # Step 1: Push image to ACR
        assert image is not None, "ContainerImage required for Azure Container Apps deployer"
        await self._push_image(image, self._image_uri)

        # Step 2: Get the managed environment resource ID
        managed_env_id = await self._get_managed_environment_id(azure)

        # Step 3: Create or update the Container App
        endpoint_url = await self._create_or_update_container_app(
            config, azure, self._image_uri, managed_env_id
        )

        logger.info("Azure Container App deployed: %s → %s", config.name, endpoint_url)

        return DeployResult(
            endpoint_url=endpoint_url,
            container_id=self._image_uri,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def _get_managed_environment_id(self, azure: AzureConfig) -> str:
        """Retrieve the full resource ID of the Container Apps Environment."""
        try:
            aca_client = self._get_aca_client()
            env = aca_client.managed_environments.get(
                resource_group_name=azure.resource_group,
                environment_name=azure.container_apps_env,
            )
            env_id: str = env.id
            return env_id
        except Exception as e:
            # Fall back to constructing the resource ID from known parts
            logger.warning(
                "Could not retrieve managed environment ID: %s — constructing from config",
                e,
            )
            return (
                f"/subscriptions/{azure.subscription_id}"
                f"/resourceGroups/{azure.resource_group}"
                f"/providers/Microsoft.App/managedEnvironments/{azure.container_apps_env}"
            )

    async def _create_or_update_container_app(
        self,
        config: AgentConfig,
        azure: AzureConfig,
        image_uri: str,
        managed_env_id: str,
    ) -> str:
        """Create a new Container App or update an existing one.

        Returns the HTTPS endpoint URL (FQDN) of the deployed app.
        """
        aca_client = self._get_aca_client()

        body = self._build_container_app_body(config, azure, image_uri, managed_env_id)

        logger.info(
            "Creating or updating Container App '%s' in resource group '%s'",
            config.name,
            azure.resource_group,
        )

        poller = aca_client.container_apps.begin_create_or_update(
            resource_group_name=azure.resource_group,
            container_app_name=config.name,
            container_app_envelope=body,
        )
        result = poller.result()

        # Extract FQDN from the result
        fqdn: str | None = None
        try:
            fqdn = result.properties.configuration.ingress.fqdn
        except AttributeError:
            pass

        if fqdn:
            endpoint_url = f"https://{fqdn}"
        else:
            # Fallback to the predictable format
            endpoint_url = f"https://{config.name}.{azure.location}.azurecontainerapps.io"

        return endpoint_url

    async def health_check(
        self,
        deploy_result: DeployResult,
        timeout: int = HEALTH_CHECK_TIMEOUT,
        interval: int = HEALTH_CHECK_INTERVAL,
    ) -> HealthStatus:
        """Verify the Container App is healthy by polling its /health endpoint.

        Container Apps may take a moment to become ready, especially when
        scaling from zero replicas on first request.
        """
        url = f"{deploy_result.endpoint_url}/health"
        checks: dict[str, bool] = {"reachable": False, "healthy": False}

        for attempt in range(timeout // interval):
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    response = await client.get(url)
                    checks["reachable"] = True
                    if response.status_code == 200:
                        checks["healthy"] = True
                        logger.info(
                            "Health check passed (attempt %d/%d)",
                            attempt + 1,
                            timeout // interval,
                        )
                        return HealthStatus(healthy=True, checks=checks)
                    else:
                        logger.debug(
                            "Health check returned %d (attempt %d/%d)",
                            response.status_code,
                            attempt + 1,
                            timeout // interval,
                        )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                pass

            logger.debug(
                "Health check attempt %d/%d — waiting %ds...",
                attempt + 1,
                timeout // interval,
                interval,
            )
            await asyncio.sleep(interval)

        logger.warning("Health check failed after %d seconds", timeout)
        return HealthStatus(healthy=False, checks=checks)

    async def teardown(self, agent_name: str) -> None:
        """Delete the Container App and clean up resources.

        Note: The container image in ACR is NOT deleted to preserve
        rollback capability. Use `agentbreeder cleanup` for image pruning.
        The Container Apps Environment is also preserved.
        """
        if self._azure_config is None:
            msg = (
                "Cannot teardown without Azure config. "
                "Call provision() or deploy() first, or re-initialize with config."
            )
            raise RuntimeError(msg)

        azure = self._azure_config

        try:
            aca_client = self._get_aca_client()
            logger.info(
                "Deleting Container App '%s' from resource group '%s'",
                agent_name,
                azure.resource_group,
            )
            poller = aca_client.container_apps.begin_delete(
                resource_group_name=azure.resource_group,
                container_app_name=agent_name,
            )
            poller.result()
            logger.info("Container App deleted: %s", agent_name)
        except ImportError as e:
            msg = "Azure Container Apps SDK not installed. Run: pip install agentbreeder[azure]"
            logger.error("Failed to delete Container App '%s': %s", agent_name, msg)
            raise ImportError(msg) from e
        except Exception as e:
            logger.error("Failed to delete Container App '%s': %s", agent_name, e)
            raise

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Retrieve logs from Azure Monitor Log Analytics for the Container App.

        Uses the azure-monitor-query SDK to query the Log Analytics workspace
        associated with the Container Apps Environment.

        Falls back to a helpful message if the SDK is not installed.
        """
        if self._azure_config is None:
            return [f"Cannot get logs: Azure config not initialized for '{agent_name}'"]

        azure = self._azure_config

        try:
            from azure.identity import DefaultAzureCredential as _Credential
            from azure.monitor.query import LogsQueryClient, LogsQueryStatus
        except ImportError:
            return [
                "Azure Monitor Logs query not configured — check Azure portal for logs. "
                "To enable log retrieval, run: pip install agentbreeder[azure]"
            ]

        try:
            credential = _Credential()
            logs_client = LogsQueryClient(credential)

            # Query ContainerAppConsoleLogs_CL table
            # The workspace ID must be discoverable from the environment
            # or passed via env_vars — we construct a reasonable query
            timespan_str = ""
            if since:
                timespan_str = f"| where TimeGenerated >= datetime('{since.isoformat()}Z')"

            query = (
                f"ContainerAppConsoleLogs_CL "
                f"| where ContainerAppName_s == '{agent_name}' "
                f"{timespan_str}"
                f"| project TimeGenerated, Log_s "
                f"| order by TimeGenerated desc "
                f"| take 100"
            )

            # Use the resource ID approach — query scoped to the subscription
            resource_id = (
                f"/subscriptions/{azure.subscription_id}"
                f"/resourceGroups/{azure.resource_group}"
                f"/providers/Microsoft.App/containerApps/{agent_name}"
            )

            response = logs_client.query_resource(resource_id, query)

            if response.status == LogsQueryStatus.SUCCESS and response.tables:
                logs: list[str] = []
                for table in response.tables:
                    for row in table.rows:
                        timestamp = str(row[0]) if row[0] else ""
                        log_line = str(row[1]) if len(row) > 1 else ""
                        logs.append(f"{timestamp} {log_line}".strip())
                return logs if logs else [f"No logs found for Container App '{agent_name}'"]

            return [f"Log query returned no results for '{agent_name}'"]

        except Exception as e:
            return [f"Error fetching logs for '{agent_name}': {e}"]
