"""Google Cloud Run deployer.

Deploys agents to GCP Cloud Run with:
- Artifact Registry for container images
- Auto-scaling and scale-to-zero
- Workload Identity for auth (falls back to service account key)
- Sidecar injection for observability

Cloud-specific logic stays in this module — never leak GCP details elsewhere.
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
DEFAULT_REGION = "us-central1"
DEFAULT_MIN_INSTANCES = 0  # Scale-to-zero
DEFAULT_MAX_INSTANCES = 10
DEFAULT_CPU = "1"
DEFAULT_MEMORY = "512Mi"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_CONCURRENCY = 80
HEALTH_CHECK_TIMEOUT = 120
HEALTH_CHECK_INTERVAL = 5


class CloudRunConfig(BaseModel):
    """GCP-specific configuration extracted from AgentConfig.deploy."""

    project_id: str
    region: str = DEFAULT_REGION
    service_account: str | None = None
    artifact_registry_repo: str | None = None
    vpc_connector: str | None = None
    ingress: str = "all"  # all | internal | internal-and-cloud-load-balancing
    cpu_throttling: bool = True
    startup_cpu_boost: bool = True
    execution_environment: str = "gen2"  # gen1 | gen2
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    concurrency: int = DEFAULT_CONCURRENCY


def _extract_cloudrun_config(config: AgentConfig) -> CloudRunConfig:
    """Extract GCP Cloud Run config from the agent's deploy section.

    The project_id is required and must be set in env_vars or as a deploy field.
    Region comes from deploy.region, falling back to DEFAULT_REGION.
    Additional GCP-specific settings come from deploy.env_vars with a GCP_ prefix.
    """
    env = config.deploy.env_vars

    project_id = env.get("GCP_PROJECT_ID", env.get("GOOGLE_CLOUD_PROJECT", ""))
    if not project_id:
        msg = (
            "GCP project ID is required for Cloud Run deployment. "
            "Set GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT in deploy.env_vars."
        )
        raise ValueError(msg)

    return CloudRunConfig(
        project_id=project_id,
        region=config.deploy.region or DEFAULT_REGION,
        service_account=env.get("GCP_SERVICE_ACCOUNT"),
        artifact_registry_repo=env.get("GCP_ARTIFACT_REGISTRY_REPO"),
        vpc_connector=env.get("GCP_VPC_CONNECTOR"),
        ingress=env.get("GCP_INGRESS", "all"),
        cpu_throttling=env.get("GCP_CPU_THROTTLING", "true").lower() == "true",
        startup_cpu_boost=env.get("GCP_STARTUP_CPU_BOOST", "true").lower() == "true",
        execution_environment=env.get("GCP_EXECUTION_ENVIRONMENT", "gen2"),
        timeout_seconds=int(env.get("GCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        concurrency=int(env.get("GCP_CONCURRENCY", str(DEFAULT_CONCURRENCY))),
    )


def _get_artifact_registry_image_uri(
    gcp_config: CloudRunConfig, agent_name: str, version: str
) -> str:
    """Build the full Artifact Registry image URI.

    Format: {region}-docker.pkg.dev/{project}/{repo}/{image}:{tag}
    """
    repo = gcp_config.artifact_registry_repo or "agent-garden"
    return (
        f"{gcp_config.region}-docker.pkg.dev/{gcp_config.project_id}/{repo}/{agent_name}:{version}"
    )


def _build_service_template(
    config: AgentConfig,
    gcp_config: CloudRunConfig,
    image_uri: str,
) -> dict[str, Any]:
    """Build the Cloud Run service revision template.

    This produces the template dict used by the Cloud Run v2 API
    to define the service's container spec, scaling, and resource limits.
    """
    # Parse resource config
    cpu = config.deploy.resources.cpu or DEFAULT_CPU
    memory = config.deploy.resources.memory or DEFAULT_MEMORY

    # Environment variables for the container
    env_vars = {
        "AGENT_NAME": config.name,
        "AGENT_VERSION": config.version,
        "AGENT_FRAMEWORK": config.framework.value,
    }
    # Add user-defined env vars, excluding GCP_ prefixed ones (those are for infra config)
    for key, value in config.deploy.env_vars.items():
        if not key.startswith("GCP_") and not key.startswith("GOOGLE_"):
            env_vars[key] = value

    container = {
        "image": image_uri,
        "resources": {
            "limits": {
                "cpu": cpu,
                "memory": memory,
            },
        },
        "env": [{"name": k, "value": v} for k, v in env_vars.items()],
        "ports": [{"containerPort": 8080}],
        "startupProbe": {
            "httpGet": {"path": "/health"},
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
            "failureThreshold": 12,
        },
        "livenessProbe": {
            "httpGet": {"path": "/health"},
            "periodSeconds": 30,
        },
    }

    # Scaling
    scaling_min = config.deploy.scaling.min
    min_instances = scaling_min if scaling_min >= 0 else DEFAULT_MIN_INSTANCES
    max_instances = config.deploy.scaling.max or DEFAULT_MAX_INSTANCES

    template: dict[str, Any] = {
        "containers": [container],
        "scaling": {
            "minInstanceCount": min_instances,
            "maxInstanceCount": max_instances,
        },
        "timeout": f"{gcp_config.timeout_seconds}s",
        "maxInstanceRequestConcurrency": gcp_config.concurrency,
        "executionEnvironment": (
            "EXECUTION_ENVIRONMENT_GEN2"
            if gcp_config.execution_environment == "gen2"
            else "EXECUTION_ENVIRONMENT_GEN1"
        ),
    }

    if gcp_config.service_account:
        template["serviceAccount"] = gcp_config.service_account

    if gcp_config.vpc_connector:
        template["vpcAccess"] = {
            "connector": gcp_config.vpc_connector,
            "egress": "PRIVATE_RANGES_ONLY",
        }

    return template


class GCPCloudRunDeployer(BaseDeployer):
    """Deploys agents to Google Cloud Run.

    Uses the Cloud Run v2 Admin API and Artifact Registry for container images.
    Supports Workload Identity (preferred) and service account key auth.
    """

    def __init__(self) -> None:
        self._gcp_config: CloudRunConfig | None = None
        self._image_uri: str | None = None

    def _get_run_client(self) -> Any:
        """Get the Cloud Run v2 Services client.

        Raises ImportError with install instructions if the SDK is missing.
        """
        try:
            from google.cloud.run_v2 import ServicesAsyncClient
        except ImportError as e:
            msg = (
                "Google Cloud Run SDK not installed. "
                "Run: pip install google-cloud-run google-cloud-artifact-registry"
            )
            raise ImportError(msg) from e
        return ServicesAsyncClient()

    def _get_ar_client(self) -> Any:
        """Get the Artifact Registry client for docker operations.

        We shell out to `gcloud` / `docker` for push since the Artifact Registry
        Python SDK doesn't handle docker push directly.
        """
        try:
            from google.cloud import artifactregistry_v1
        except ImportError as e:
            msg = (
                "Google Cloud Artifact Registry SDK not installed. "
                "Run: pip install google-cloud-artifact-registry"
            )
            raise ImportError(msg) from e
        return artifactregistry_v1.ArtifactRegistryAsyncClient()

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Provision GCP infrastructure for the agent.

        Steps:
        1. Validate GCP config (project, region, etc.)
        2. Ensure Artifact Registry repository exists
        3. Return the expected Cloud Run service URL
        """
        self._gcp_config = _extract_cloudrun_config(config)
        gcp = self._gcp_config

        logger.info(
            "Provisioning Cloud Run service for '%s' in project '%s' region '%s'",
            config.name,
            gcp.project_id,
            gcp.region,
        )

        # Ensure Artifact Registry repo exists
        await self._ensure_artifact_registry_repo(gcp)

        # Compute the expected service URL
        # Cloud Run URL format: https://{service}-{hash}-{region}.a.run.app
        # We return a placeholder — the real URL comes after deploy
        expected_url = f"https://{config.name}-{gcp.region}.a.run.app"

        self._image_uri = _get_artifact_registry_image_uri(gcp, config.name, config.version)

        return InfraResult(
            endpoint_url=expected_url,
            resource_ids={
                "project_id": gcp.project_id,
                "region": gcp.region,
                "image_uri": self._image_uri,
            },
        )

    async def _ensure_artifact_registry_repo(self, gcp: CloudRunConfig) -> None:
        """Create the Artifact Registry docker repository if it doesn't exist."""
        repo_name = gcp.artifact_registry_repo or "agent-garden"

        try:
            ar_client = self._get_ar_client()
            from google.cloud.artifactregistry_v1 import (
                CreateRepositoryRequest,
                GetRepositoryRequest,
                Repository,
            )

            parent = f"projects/{gcp.project_id}/locations/{gcp.region}"
            repo_path = f"{parent}/repositories/{repo_name}"

            try:
                await ar_client.get_repository(request=GetRepositoryRequest(name=repo_path))
                logger.info("Artifact Registry repo '%s' already exists", repo_name)
            except Exception:
                logger.info("Creating Artifact Registry repo '%s'", repo_name)
                await ar_client.create_repository(
                    request=CreateRepositoryRequest(
                        parent=parent,
                        repository_id=repo_name,
                        repository=Repository(
                            format_=Repository.Format.DOCKER,
                            description=f"Agent Garden container images ({gcp.project_id})",
                        ),
                    )
                )
                logger.info("Created Artifact Registry repo '%s'", repo_name)
        except ImportError:
            logger.warning(
                "Artifact Registry SDK not available — skipping repo creation. "
                "Ensure the repo '%s' exists in project '%s'.",
                repo_name,
                gcp.project_id,
            )

    async def _push_image(self, image: ContainerImage, image_uri: str) -> None:
        """Tag and push the container image to Artifact Registry.

        Uses the Docker SDK to tag the locally-built image and push it.
        Requires `gcloud auth configure-docker` to have been run for the
        Artifact Registry region.
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

        # Tag for Artifact Registry
        logger.info("Tagging image as %s", image_uri)
        built_image.tag(image_uri)

        # Push to Artifact Registry
        logger.info("Pushing image to Artifact Registry: %s", image_uri)
        push_output = client.images.push(image_uri, stream=True, decode=True)
        for chunk in push_output:
            if "status" in chunk:
                logger.debug("  %s", chunk["status"])
            if "error" in chunk:
                msg = f"Image push failed: {chunk['error']}"
                raise RuntimeError(msg)

        logger.info("Image pushed successfully: %s", image_uri)

    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult:
        """Build, push, and deploy the agent to Cloud Run.

        Steps:
        1. Build and push container image to Artifact Registry
        2. Create or update the Cloud Run service
        3. Set IAM policy if the service should be publicly accessible
        4. Return the service URL
        """
        if self._gcp_config is None:
            self._gcp_config = _extract_cloudrun_config(config)
        gcp = self._gcp_config

        if self._image_uri is None:
            self._image_uri = _get_artifact_registry_image_uri(gcp, config.name, config.version)

        # Step 1: Push image to Artifact Registry
        await self._push_image(image, self._image_uri)

        # Step 2: Create or update Cloud Run service
        service_url = await self._create_or_update_service(config, gcp, self._image_uri)

        # Step 3: Set IAM policy for public access if visibility is public
        if str(config.access.visibility) == "public":
            await self._allow_unauthenticated(config.name, gcp)

        logger.info("Cloud Run service deployed: %s → %s", config.name, service_url)

        return DeployResult(
            endpoint_url=service_url,
            container_id=self._image_uri,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def _create_or_update_service(
        self,
        config: AgentConfig,
        gcp: CloudRunConfig,
        image_uri: str,
    ) -> str:
        """Create a new Cloud Run service or update an existing one.

        Returns the service URL.
        """
        from google.cloud.run_v2 import (
            CreateServiceRequest,
            GetServiceRequest,
            Service,
            UpdateServiceRequest,
        )
        from google.cloud.run_v2.types import RevisionTemplate

        run_client = self._get_run_client()

        parent = f"projects/{gcp.project_id}/locations/{gcp.region}"
        service_name = f"{parent}/services/{config.name}"

        template_dict = _build_service_template(config, gcp, image_uri)

        # Build ingress setting
        ingress_map = {
            "all": Service.Ingress.INGRESS_TRAFFIC_ALL,
            "internal": Service.Ingress.INGRESS_TRAFFIC_INTERNAL_ONLY,
            "internal-and-cloud-load-balancing": (
                Service.Ingress.INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER
            ),
        }
        ingress = ingress_map.get(gcp.ingress, Service.Ingress.INGRESS_TRAFFIC_ALL)

        # Try to get existing service first
        try:
            existing = await run_client.get_service(request=GetServiceRequest(name=service_name))
            logger.info("Updating existing Cloud Run service: %s", config.name)

            existing.template = RevisionTemplate(template_dict)
            existing.ingress = ingress

            operation = await run_client.update_service(
                request=UpdateServiceRequest(service=existing)
            )
            service = await operation.result()
        except Exception:
            logger.info("Creating new Cloud Run service: %s", config.name)

            service_obj = Service(
                template=RevisionTemplate(template_dict),
                ingress=ingress,
                labels={
                    "managed-by": "agent-garden",
                    "agent-name": config.name,
                    "agent-version": config.version.replace(".", "-"),
                    "team": config.team,
                },
            )

            operation = await run_client.create_service(
                request=CreateServiceRequest(
                    parent=parent,
                    service=service_obj,
                    service_id=config.name,
                )
            )
            service = await operation.result()

        # Extract the service URL
        service_url = service.uri
        if not service_url:
            # Construct it if not returned
            service_url = f"https://{config.name}-{gcp.region}.a.run.app"

        return service_url

    async def _allow_unauthenticated(self, service_name: str, gcp: CloudRunConfig) -> None:
        """Set IAM policy to allow unauthenticated access (public agents)."""
        try:
            from google.iam.v1 import iam_policy_pb2, policy_pb2

            run_client = self._get_run_client()
            resource = f"projects/{gcp.project_id}/locations/{gcp.region}/services/{service_name}"

            policy = await run_client.get_iam_policy(
                request=iam_policy_pb2.GetIamPolicyRequest(resource=resource)
            )

            # Add allUsers as invoker
            invoker_binding = policy_pb2.Binding(
                role="roles/run.invoker",
                members=["allUsers"],
            )

            # Check if binding already exists
            has_binding = any(
                b.role == "roles/run.invoker" and "allUsers" in b.members for b in policy.bindings
            )
            if not has_binding:
                policy.bindings.append(invoker_binding)
                await run_client.set_iam_policy(
                    request=iam_policy_pb2.SetIamPolicyRequest(
                        resource=resource,
                        policy=policy,
                    )
                )
                logger.info("Enabled unauthenticated access for service '%s'", service_name)
        except Exception as e:
            logger.warning(
                "Could not set IAM policy for public access on '%s': %s",
                service_name,
                e,
            )

    async def health_check(
        self,
        deploy_result: DeployResult,
        timeout: int = HEALTH_CHECK_TIMEOUT,
        interval: int = HEALTH_CHECK_INTERVAL,
    ) -> HealthStatus:
        """Verify the Cloud Run service is healthy by polling its /health endpoint.

        Cloud Run services may take a moment to become ready after deployment,
        especially on cold start with scale-to-zero.
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
        """Delete the Cloud Run service and clean up resources.

        Note: The container image in Artifact Registry is NOT deleted
        to preserve rollback capability. Use `garden cleanup` for image pruning.
        """
        if self._gcp_config is None:
            msg = (
                "Cannot teardown without GCP config. "
                "Call provision() or deploy() first, or re-initialize with config."
            )
            raise RuntimeError(msg)

        gcp = self._gcp_config

        service_name = f"projects/{gcp.project_id}/locations/{gcp.region}/services/{agent_name}"

        try:
            run_client = self._get_run_client()

            # Build a request object — use a simple namespace if SDK import fails
            try:
                from google.cloud.run_v2 import DeleteServiceRequest

                request = DeleteServiceRequest(name=service_name)
            except ImportError:
                # Fallback: create a simple request-like object for mocked clients
                request = type("DeleteServiceRequest", (), {"name": service_name})()

            logger.info("Deleting Cloud Run service: %s", agent_name)
            operation = await run_client.delete_service(request=request)
            await operation.result()
            logger.info("Cloud Run service deleted: %s", agent_name)
        except ImportError as e:
            msg = "Google Cloud Run SDK not installed. Run: pip install google-cloud-run"
            logger.error("Failed to delete Cloud Run service '%s': %s", agent_name, msg)
            raise ImportError(msg) from e
        except Exception as e:
            logger.error("Failed to delete Cloud Run service '%s': %s", agent_name, e)
            raise

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Retrieve logs from Cloud Logging for the Cloud Run service.

        Uses the Cloud Logging API to fetch logs filtered by the service name.
        """
        if self._gcp_config is None:
            return [f"Cannot get logs: GCP config not initialized for '{agent_name}'"]

        gcp = self._gcp_config

        try:
            from google.cloud import logging as cloud_logging

            client = cloud_logging.Client(project=gcp.project_id)

            filter_str = (
                f'resource.type="cloud_run_revision" '
                f'resource.labels.service_name="{agent_name}" '
                f'resource.labels.location="{gcp.region}"'
            )
            if since:
                filter_str += f' timestamp>="{since.isoformat()}Z"'

            entries = client.list_entries(
                filter_=filter_str,
                order_by="timestamp desc",
                max_results=100,
            )

            logs: list[str] = []
            for entry in entries:
                timestamp = entry.timestamp.isoformat() if entry.timestamp else ""
                payload = entry.payload if hasattr(entry, "payload") else str(entry)
                logs.append(f"{timestamp} {payload}")

            return logs if logs else [f"No logs found for service '{agent_name}'"]

        except ImportError:
            return [
                "Google Cloud Logging SDK not installed. Run: pip install google-cloud-logging"
            ]
        except Exception as e:
            return [f"Error fetching logs for '{agent_name}': {e}"]

    async def get_url(self, agent_name: str) -> str:
        """Get the URL of a deployed Cloud Run service."""
        if self._gcp_config is None:
            msg = "Cannot get URL: GCP config not initialized."
            raise RuntimeError(msg)

        gcp = self._gcp_config

        from google.cloud.run_v2 import GetServiceRequest

        run_client = self._get_run_client()
        service_name = f"projects/{gcp.project_id}/locations/{gcp.region}/services/{agent_name}"

        service = await run_client.get_service(request=GetServiceRequest(name=service_name))
        return service.uri

    async def status(self, agent_name: str) -> dict[str, Any]:
        """Get the status of a deployed Cloud Run service."""
        if self._gcp_config is None:
            msg = "Cannot get status: GCP config not initialized."
            raise RuntimeError(msg)

        gcp = self._gcp_config

        from google.cloud.run_v2 import GetServiceRequest

        run_client = self._get_run_client()
        service_name = f"projects/{gcp.project_id}/locations/{gcp.region}/services/{agent_name}"

        service = await run_client.get_service(request=GetServiceRequest(name=service_name))

        return {
            "name": agent_name,
            "url": service.uri,
            "ready": (
                service.terminal_condition
                and service.terminal_condition.state == "CONDITION_SUCCEEDED"
            ),
            "latest_revision": service.latest_ready_revision,
            "ingress": str(service.ingress),
            "labels": dict(service.labels) if service.labels else {},
        }
