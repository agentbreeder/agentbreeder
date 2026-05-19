"""AWS App Runner deployer.

Deploys agents to AWS App Runner with:
- ECR for container image storage
- App Runner for serverless container execution (no VPC/ALB required)
- CloudWatch Logs for log aggregation

Structurally parallel to GCP Cloud Run — push image, get HTTPS URL.
Cloud-specific logic stays in this module — never leak AWS details elsewhere.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from engine.config_parser import AgentConfig
from engine.deployers._health import HealthCheckTimeout, poll_until_ready
from engine.deployers.base import (
    BaseDeployer,
    DeployResult,
    ExistingDeployment,
    HealthStatus,
    InfraResult,
)
from engine.runtimes.base import ContainerImage
from engine.sidecar import validate_sidecar_config

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-east-1"
DEFAULT_CPU = "1 vCPU"  # App Runner CPU spec
DEFAULT_MEMORY = "2 GB"  # App Runner memory spec
HEALTH_CHECK_TIMEOUT = 300
HEALTH_CHECK_INTERVAL = 10
WAITER_DELAY = 15
WAITER_MAX_ATTEMPTS = 60  # 60 * 15s = 15 minutes


class AWSAppRunnerConfig(BaseModel):
    """AWS-specific configuration extracted from AgentConfig.deploy."""

    account_id: str
    region: str = DEFAULT_REGION
    ecr_repo: str
    access_role_arn: str | None = None  # IAM role for App Runner to pull from ECR


def _extract_app_runner_config(config: AgentConfig) -> AWSAppRunnerConfig:
    """Extract AWS App Runner config from the agent's deploy section.

    Required fields come from deploy.env_vars with AWS_ prefix.
    Region falls back to deploy.region, then DEFAULT_REGION.
    ECR repo defaults to the agent name if not set.
    """
    env = config.deploy.env_vars

    logger.debug(
        "resolving_credential",
        extra={"key": "AWS_ACCOUNT_ID", "sources": ["deploy.env_vars"]},
    )
    account_id = env.get("AWS_ACCOUNT_ID", "")
    if not account_id:
        msg = (
            "AWS_ACCOUNT_ID is required for App Runner deployment. "
            "Set AWS_ACCOUNT_ID in deploy.env_vars."
        )
        raise ValueError(msg)
    logger.info(
        "credential_resolved", extra={"key": "AWS_ACCOUNT_ID", "source": "deploy.env_vars"}
    )

    logger.debug(
        "resolving_credential",
        extra={"key": "AWS_REGION", "sources": ["deploy.env_vars", "deploy.region", "default"]},
    )
    region = env.get("AWS_REGION") or config.deploy.region or DEFAULT_REGION
    region_source = (
        "deploy.env_vars"
        if env.get("AWS_REGION")
        else ("deploy.region" if config.deploy.region else "default")
    )
    logger.info("credential_resolved", extra={"key": "AWS_REGION", "source": region_source})

    ecr_repo = env.get("AWS_ECR_REPO") or config.name

    return AWSAppRunnerConfig(
        account_id=account_id,
        region=region,
        ecr_repo=ecr_repo,
        access_role_arn=env.get("AWS_APP_RUNNER_ACCESS_ROLE_ARN"),
    )


def _get_ecr_image_uri(ar_config: AWSAppRunnerConfig, version: str) -> str:
    """Build the full ECR image URI."""
    return (
        f"{ar_config.account_id}.dkr.ecr.{ar_config.region}.amazonaws.com"
        f"/{ar_config.ecr_repo}:{version}"
    )


def _build_env_vars(config: AgentConfig) -> list[dict[str, str]]:
    """Build the App Runner environment variable list.

    Excludes AWS_ prefixed keys (infra config, not agent runtime config).
    """
    env_vars: dict[str, str] = {
        "AGENT_NAME": config.name,
        "AGENT_VERSION": config.version,
        "AGENT_FRAMEWORK": config.framework.value
        if config.framework
        else (config.runtime.framework if config.runtime else "unknown"),
    }
    for key, value in config.deploy.env_vars.items():
        if not key.startswith("AWS_"):
            env_vars[key] = value
    return [{"Name": k, "Value": v} for k, v in env_vars.items()]


class AWSAppRunnerDeployer(BaseDeployer):
    """Deploys agents to AWS App Runner.

    Uses ECR for container storage, App Runner for serverless runtime,
    and CloudWatch Logs for observability. No VPC, ALB, or ECS cluster required.
    """

    def __init__(self) -> None:
        self._ar_config: AWSAppRunnerConfig | None = None
        self._image_uri: str | None = None

    def _get_boto3_client(self, service: str) -> Any:
        """Get a boto3 client for the given AWS service.

        Raises ImportError with install instructions if boto3 is missing.
        """
        try:
            import boto3
        except ImportError as e:
            msg = "boto3 is not installed. Run: pip install agentbreeder[aws]"
            raise ImportError(msg) from e

        region = self._ar_config.region if self._ar_config else DEFAULT_REGION
        return boto3.client(service, region_name=region)

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Provision AWS infrastructure for the agent.

        Steps:
        1. Validate AWS config (account ID, ECR repo)
        2. Ensure ECR repository exists (create if absent)
        3. Return the expected App Runner endpoint (finalised after deploy())
        """
        self._ar_config = _extract_app_runner_config(config)
        ar = self._ar_config

        logger.info(
            "Provisioning App Runner infrastructure for '%s' in %s",
            config.name,
            ar.region,
        )

        await self._ensure_ecr_repository(ar.ecr_repo)

        self._image_uri = _get_ecr_image_uri(ar, config.version)

        # App Runner service URL is not known until after create_service,
        # so return a placeholder that will be updated in deploy().
        placeholder_url = f"https://{config.name}.{ar.region}.awsapprunner.com"

        return InfraResult(
            endpoint_url=placeholder_url,
            resource_ids={
                "account_id": ar.account_id,
                "region": ar.region,
                "ecr_repo": ar.ecr_repo,
                "image_uri": self._image_uri,
            },
        )

    async def _ensure_ecr_repository(self, repo_name: str) -> None:
        """Create the ECR repository if it does not already exist."""
        ecr = self._get_boto3_client("ecr")
        try:
            ecr.describe_repositories(repositoryNames=[repo_name])
            logger.info("ECR repository '%s' already exists", repo_name)
        except ecr.exceptions.RepositoryNotFoundException:
            logger.info("Creating ECR repository '%s'", repo_name)
            ecr.create_repository(
                repositoryName=repo_name,
                imageScanningConfiguration={"scanOnPush": True},
                imageTagMutability="MUTABLE",
            )
            logger.info("Created ECR repository '%s'", repo_name)
        except Exception as exc:
            logger.warning(
                "Could not verify ECR repository '%s': %s — continuing",
                repo_name,
                exc,
            )

    async def _push_image(self, image: ContainerImage, image_uri: str) -> None:
        """Tag and push the container image to ECR."""
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise ImportError(msg) from e

        assert self._ar_config is not None, "provision() must be called before _push_image()"
        ar = self._ar_config
        ecr = self._get_boto3_client("ecr")

        auth_response = ecr.get_authorization_token(registryIds=[ar.account_id])
        auth_data = auth_response["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":", 1)

        docker_client = docker.from_env()

        logger.info("Building Docker image: %s", image.tag)
        built_image, build_logs = docker_client.images.build(
            path=str(image.context_dir),
            tag=image.tag,
            rm=True,
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = chunk["stream"].strip()
                if line:
                    logger.debug("  %s", line)

        logger.info("Tagging image as %s", image_uri)
        built_image.tag(image_uri)

        logger.info("Pushing image to ECR: %s", image_uri)
        push_output = docker_client.images.push(
            image_uri,
            auth_config={"username": username, "password": password},
            stream=True,
            decode=True,
        )
        for chunk in push_output:
            if "status" in chunk:
                logger.debug("  %s", chunk["status"])
            if "error" in chunk:
                msg = f"ECR image push failed: {chunk['error']}"
                raise RuntimeError(msg)

        logger.info("Image pushed successfully: %s", image_uri)

    def _build_service_config(self, config: AgentConfig, image_uri: str) -> dict[str, Any]:
        """Build the App Runner create_service / update_service configuration."""
        assert self._ar_config is not None
        ar = self._ar_config

        resources = config.deploy.resources
        cpu = resources.cpu or "1"
        memory = resources.memory or "2Gi"

        # Normalise CPU: App Runner accepts "1 vCPU", "2 vCPU", "4 vCPU"
        cpu_str = "".join(c for c in cpu if c.isdigit() or c == ".").rstrip(".")
        if not cpu_str:
            cpu_str = "1"
        cpu_spec = f"{cpu_str} vCPU"

        # Normalise memory: App Runner accepts "2 GB", "3 GB", "4 GB"
        mem_digits = "".join(c for c in memory if c.isdigit()).lstrip("0") or "2"
        mem_spec = f"{mem_digits} GB"

        image_config: dict[str, Any] = {
            "ImageIdentifier": image_uri,
            "ImageConfiguration": {
                "RuntimeEnvironmentVariables": {
                    e["Name"]: e["Value"] for e in _build_env_vars(config)
                },
                "Port": "8080",
            },
            "ImageRepositoryType": "ECR",
        }

        if ar.access_role_arn:
            image_config["AuthenticationConfiguration"] = {"AccessRoleArn": ar.access_role_arn}

        return {
            "ServiceName": config.name,
            "SourceConfiguration": {"ImageRepository": image_config},
            "InstanceConfiguration": {
                "Cpu": cpu_spec,
                "Memory": mem_spec,
            },
            "AutoScalingConfigurationArn": None,  # use App Runner default
            "HealthCheckConfiguration": {
                "Protocol": "HTTP",
                "Path": "/health",
                "Interval": 10,
                "Timeout": 5,
                "HealthyThreshold": 1,
                "UnhealthyThreshold": 5,
            },
        }

    async def _lookup_existing(self, agent_name: str) -> ExistingDeployment | None:
        """Return an :class:`ExistingDeployment` snapshot for the App Runner service.

        A service in ``RUNNING`` state is considered healthy. ``OPERATION_IN_PROGRESS``,
        ``CREATE_FAILED``, ``PAUSED``, ``DELETED`` and unknown states are
        reported as unhealthy so the caller cleans up before redeploying.
        """
        if self._ar_config is None:
            return None
        try:
            ar_client = self._get_boto3_client("apprunner")
        except ImportError:
            return None

        try:
            response = ar_client.describe_service(ServiceName=agent_name)
        except Exception as exc:  # noqa: BLE001 — broad to handle boto/moto variance
            exc_type = type(exc).__name__
            if "ResourceNotFoundException" not in exc_type:
                logger.debug("describe_service failed for '%s': %s", agent_name, exc)
            return None

        svc = response.get("Service") or {}
        status = str(svc.get("Status", "UNKNOWN"))
        service_url = svc.get("ServiceUrl")
        url = f"https://{service_url}" if service_url else None
        is_healthy = status == "RUNNING"
        return ExistingDeployment(
            status="healthy" if is_healthy else "unhealthy",
            url=url,
            resource_id=svc.get("ServiceArn"),
        )

    async def deploy(self, config: AgentConfig, image: ContainerImage | None) -> DeployResult:
        """Push the image to ECR and create / update the App Runner service."""
        # W4-37: Pre-validate sidecar before any cloud API call.
        validate_sidecar_config(config)

        assert self._ar_config is not None, "provision() must be called before deploy()"
        assert self._image_uri is not None, "provision() must be called before deploy()"
        assert image is not None, "ContainerImage required for App Runner deployer"

        # W4-35: Idempotency check.
        existing = await self._lookup_existing(config.name)
        if existing is not None:
            if existing.status == "healthy":
                logger.info(
                    "deploy_idempotent_hit",
                    extra={"agent": config.name, "cloud": "aws-app-runner"},
                )
                return DeployResult(
                    endpoint_url=existing.url or "",
                    container_id=existing.resource_id or self._image_uri,
                    status="running",
                    agent_name=config.name,
                    version=config.version,
                )
            logger.info(
                "deploy_cleaning_stale",
                extra={
                    "agent": config.name,
                    "cloud": "aws-app-runner",
                    "status": existing.status,
                },
            )
            try:
                await self.teardown(config.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Cleanup of stale App Runner service '%s' failed: %s — continuing deploy",
                    config.name,
                    exc,
                )

        await self._push_image(image, self._image_uri)

        ar_client = self._get_boto3_client("apprunner")
        service_config = self._build_service_config(config, self._image_uri)
        service_name = config.name

        try:
            existing = ar_client.describe_service(ServiceName=service_name)
            service_arn = existing["Service"]["ServiceArn"]
            logger.info("Updating existing App Runner service '%s'", service_name)
            ar_client.update_service(
                ServiceArn=service_arn,
                SourceConfiguration=service_config["SourceConfiguration"],
                InstanceConfiguration=service_config["InstanceConfiguration"],
            )
        except ar_client.exceptions.ResourceNotFoundException:
            logger.info("Creating App Runner service '%s'", service_name)
            # Remove None values before passing to create_service
            clean_config = {k: v for k, v in service_config.items() if v is not None}
            response = ar_client.create_service(**clean_config)
            service_arn = response["Service"]["ServiceArn"]

        endpoint_url = await self._wait_for_service_running(ar_client, service_name)

        logger.info("App Runner service deployed: %s → %s", service_name, endpoint_url)

        return DeployResult(
            endpoint_url=endpoint_url,
            container_id=service_arn,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def _wait_for_service_running(self, ar_client: Any, service_name: str) -> str:
        """Poll until the App Runner service is RUNNING and return its URL."""
        # poll_until_ready returns via early-exit (we capture the URL in closure);
        # invalid statuses raise RuntimeError, which propagates through the helper.
        service_url: dict[str, str] = {}

        async def _check() -> bool:
            response = ar_client.describe_service(ServiceName=service_name)
            svc = response["Service"]
            status = svc.get("Status", "")
            if status == "RUNNING":
                service_url["url"] = f"https://{svc['ServiceUrl']}"
                return True
            if status in ("CREATE_FAILED", "DELETE_FAILED", "PAUSED"):
                msg = f"App Runner service '{service_name}' entered unexpected status '{status}'"
                raise RuntimeError(msg)
            logger.info(
                "Waiting for App Runner service '%s' (status=%s)",
                service_name,
                status,
            )
            return False

        try:
            # Effective timeout matches original semantics: N checks, then raise.
            # poll_until_ready always checks once before evaluating the deadline,
            # so we shave one interval to keep the same total call count.
            await poll_until_ready(
                _check,
                timeout=float((WAITER_MAX_ATTEMPTS - 1) * WAITER_DELAY),
                initial_interval=float(WAITER_DELAY),
                max_interval=float(WAITER_DELAY),
                backoff_factor=1.0,
            )
        except HealthCheckTimeout as e:
            msg = (
                f"Timed out waiting for App Runner service '{service_name}' to reach RUNNING "
                f"after {WAITER_MAX_ATTEMPTS * WAITER_DELAY}s."
            )
            raise TimeoutError(msg) from e

        return service_url["url"]

    async def health_check(
        self,
        deploy_result: DeployResult,
        timeout: int = HEALTH_CHECK_TIMEOUT,
        interval: int = HEALTH_CHECK_INTERVAL,
    ) -> HealthStatus:
        """HTTP health check against the App Runner service /health endpoint."""
        url = f"{deploy_result.endpoint_url}/health"

        async with httpx.AsyncClient(timeout=10.0) as client:

            async def _check() -> bool:
                try:
                    response = await client.get(url)
                    if response.status_code < 500:
                        return True
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                return False

            try:
                await poll_until_ready(
                    _check,
                    timeout=float(timeout),
                    initial_interval=float(interval),
                    max_interval=max(float(interval) * 4, 30.0),
                    backoff_factor=1.0,
                )
                return HealthStatus(healthy=True, checks={"http_health": True})
            except HealthCheckTimeout:
                return HealthStatus(healthy=False, checks={"http_health": False})

    async def teardown(self, agent_name: str) -> None:
        """Delete the App Runner service."""
        if self._ar_config is None:
            msg = (
                f"App Runner config not initialised — cannot teardown '{agent_name}'. "
                "Call provision() first or reconstruct deployer state."
            )
            raise RuntimeError(msg)

        ar_client = self._get_boto3_client("apprunner")
        response = ar_client.describe_service(ServiceName=agent_name)
        service_arn = response["Service"]["ServiceArn"]
        logger.info("Deleting App Runner service '%s' (ARN: %s)", agent_name, service_arn)
        ar_client.delete_service(ServiceArn=service_arn)
        logger.info("App Runner service '%s' deleted", agent_name)

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Retrieve logs from CloudWatch Logs for the App Runner service."""
        if self._ar_config is None:
            return [f"Cannot get logs: App Runner config not initialized for '{agent_name}'"]

        logs_client = self._get_boto3_client("logs")
        log_group = f"/aws/apprunner/{agent_name}"

        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "limit": 100,
            "startFromHead": False,
        }
        if since:
            kwargs["startTime"] = int(since.timestamp() * 1000)

        try:
            response = logs_client.filter_log_events(**kwargs)
            events = response.get("events", [])
            if not events:
                return [f"No logs found in log group '{log_group}'"]
            return [
                f"{datetime.utcfromtimestamp(e['timestamp'] / 1000).isoformat()} "
                f"{e['message'].rstrip()}"
                for e in events
            ]
        except Exception as exc:
            return [f"Error fetching logs for '{agent_name}': {exc}"]

    async def get_url(self, agent_name: str) -> str:
        """Get the URL of a deployed App Runner service."""
        if self._ar_config is None:
            msg = "Cannot get URL: App Runner config not initialized."
            raise RuntimeError(msg)
        ar_client = self._get_boto3_client("apprunner")
        response = ar_client.describe_service(ServiceName=agent_name)
        return f"https://{response['Service']['ServiceUrl']}"

    async def status(self, agent_name: str) -> dict[str, Any]:
        """Get status of a deployed App Runner service."""
        if self._ar_config is None:
            msg = "Cannot get status: App Runner config not initialized."
            raise RuntimeError(msg)
        ar_client = self._get_boto3_client("apprunner")
        try:
            response = ar_client.describe_service(ServiceName=agent_name)
            svc = response["Service"]
            return {
                "name": agent_name,
                "status": svc.get("Status", "UNKNOWN"),
                "url": f"https://{svc.get('ServiceUrl', '')}",
                "service_arn": svc.get("ServiceArn", ""),
            }
        except Exception:
            return {"name": agent_name, "status": "not_found"}
