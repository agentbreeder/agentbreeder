"""AWS ECS Fargate deployer.

Deploys agents to AWS ECS Fargate with:
- ECR for container image storage
- ECS Fargate for serverless container execution
- CloudWatch Logs for log aggregation
- awsvpc networking mode with configurable subnets and security groups

Cloud-specific logic stays in this module — never leak AWS details elsewhere.
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
DEFAULT_REGION = "us-east-1"
DEFAULT_CPU = "512"  # ECS task CPU units (256, 512, 1024, 2048, 4096)
DEFAULT_MEMORY = "1024"  # ECS task memory in MiB
HEALTH_CHECK_TIMEOUT = 120
HEALTH_CHECK_INTERVAL = 5
WAITER_DELAY = 15  # seconds between waiter polls
WAITER_MAX_ATTEMPTS = 40  # 40 * 15s = 10 minutes


class AWSECSConfig(BaseModel):
    """AWS-specific configuration extracted from AgentConfig.deploy."""

    account_id: str
    region: str = DEFAULT_REGION
    ecs_cluster: str
    execution_role_arn: str
    vpc_subnets: list[str]
    security_groups: list[str]
    task_role_arn: str | None = None


def _extract_ecs_config(config: AgentConfig) -> AWSECSConfig:
    """Extract AWS ECS config from the agent's deploy section.

    Required fields come from deploy.env_vars with AWS_ prefix.
    Region falls back to deploy.region, then DEFAULT_REGION.
    """
    env = config.deploy.env_vars

    account_id = env.get("AWS_ACCOUNT_ID", "")
    if not account_id:
        msg = (
            "AWS account ID is required for ECS Fargate deployment. "
            "Set AWS_ACCOUNT_ID in deploy.env_vars."
        )
        raise ValueError(msg)

    ecs_cluster = env.get("AWS_ECS_CLUSTER", "")
    if not ecs_cluster:
        msg = (
            "ECS cluster name is required for ECS Fargate deployment. "
            "Set AWS_ECS_CLUSTER in deploy.env_vars."
        )
        raise ValueError(msg)

    execution_role_arn = env.get("AWS_EXECUTION_ROLE_ARN", "")
    if not execution_role_arn:
        msg = (
            "ECS execution role ARN is required for ECS Fargate deployment. "
            "Set AWS_EXECUTION_ROLE_ARN in deploy.env_vars."
        )
        raise ValueError(msg)

    vpc_subnets_raw = env.get("AWS_VPC_SUBNETS", "")
    if not vpc_subnets_raw:
        msg = (
            "VPC subnets are required for ECS Fargate deployment. "
            "Set AWS_VPC_SUBNETS as a comma-separated list of subnet IDs in deploy.env_vars."
        )
        raise ValueError(msg)
    vpc_subnets = [s.strip() for s in vpc_subnets_raw.split(",") if s.strip()]

    security_groups_raw = env.get("AWS_SECURITY_GROUPS", "")
    if not security_groups_raw:
        msg = (
            "Security groups are required for ECS Fargate deployment. "
            "Set AWS_SECURITY_GROUPS as a comma-separated list of SG IDs in deploy.env_vars."
        )
        raise ValueError(msg)
    security_groups = [sg.strip() for sg in security_groups_raw.split(",") if sg.strip()]

    region = env.get("AWS_REGION") or config.deploy.region or DEFAULT_REGION

    return AWSECSConfig(
        account_id=account_id,
        region=region,
        ecs_cluster=ecs_cluster,
        execution_role_arn=execution_role_arn,
        vpc_subnets=vpc_subnets,
        security_groups=security_groups,
        task_role_arn=env.get("AWS_TASK_ROLE_ARN"),
    )


def _get_ecr_image_uri(aws_config: AWSECSConfig, repo_name: str, version: str) -> str:
    """Build the full ECR image URI.

    Format: {account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}:{version}
    """
    return (
        f"{aws_config.account_id}.dkr.ecr.{aws_config.region}.amazonaws.com/{repo_name}:{version}"
    )


class AWSECSDeployer(BaseDeployer):
    """Deploys agents to AWS ECS Fargate.

    Uses ECR for container storage, ECS Fargate for runtime, and CloudWatch
    Logs for observability. All networking uses awsvpc mode.
    """

    def __init__(self) -> None:
        self._aws_config: AWSECSConfig | None = None
        self._image_uri: str | None = None
        self._task_definition_arn: str | None = None

    def _get_boto3_client(self, service: str) -> Any:
        """Get a boto3 client for the given AWS service.

        Raises ImportError with install instructions if boto3 is missing.
        """
        try:
            import boto3
        except ImportError as e:
            msg = "boto3 is not installed. Run: pip install agentbreeder[aws]"
            raise ImportError(msg) from e

        region = self._aws_config.region if self._aws_config else DEFAULT_REGION
        return boto3.client(service, region_name=region)

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Provision AWS infrastructure for the agent.

        Steps:
        1. Validate AWS config (account ID, cluster, roles, networking)
        2. Ensure ECR repository exists (create if absent)
        3. Return the expected ECS service endpoint
        """
        self._aws_config = _extract_ecs_config(config)
        aws = self._aws_config

        logger.info(
            "Provisioning ECS Fargate service for '%s' in account '%s' region '%s'",
            config.name,
            aws.account_id,
            aws.region,
        )

        # Ensure ECR repository exists
        await self._ensure_ecr_repository(config.name)

        # Compute expected image URI
        self._image_uri = _get_ecr_image_uri(aws, config.name, config.version)

        # ECS does not provide a stable DNS for services by default without an ALB.
        # We return a local ECS DNS placeholder; the real URL is an ALB configured
        # outside AgentBreeder or set after deploy via resource_ids.
        expected_url = f"https://{config.name}.{aws.region}.ecs.local"

        return InfraResult(
            endpoint_url=expected_url,
            resource_ids={
                "account_id": aws.account_id,
                "region": aws.region,
                "ecs_cluster": aws.ecs_cluster,
                "ecr_repo": config.name,
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
                encryptionConfiguration={"encryptionType": "AES256"},
                tags=[
                    {"Key": "managed-by", "Value": "agentbreeder"},
                    {"Key": "agent-name", "Value": repo_name},
                ],
            )
            logger.info("Created ECR repository '%s'", repo_name)
        except Exception as exc:
            # Catch non-existence exception from moto/boto3 naming differences
            exc_type = type(exc).__name__
            if "RepositoryNotFound" in exc_type or "NoSuchEntity" in exc_type:
                logger.info("Creating ECR repository '%s' (fallback path)", repo_name)
                ecr.create_repository(repositoryName=repo_name)
            else:
                logger.warning(
                    "Could not verify ECR repository '%s': %s — continuing",
                    repo_name,
                    exc,
                )

    async def _push_image(self, image: ContainerImage, image_uri: str) -> None:
        """Tag and push the container image to ECR.

        Authenticates via `ecr.get_authorization_token` and uses the Docker SDK
        to build, tag, and push the image.
        """
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise ImportError(msg) from e

        assert self._aws_config is not None, "provision() must be called before _push_image()"
        aws = self._aws_config
        ecr = self._get_boto3_client("ecr")

        # Get ECR auth token
        auth_response = ecr.get_authorization_token(registryIds=[aws.account_id])
        auth_data = auth_response["authorizationData"][0]
        import base64

        token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":", 1)
        docker_client = docker.from_env()

        # Build the image locally
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

        # Tag for ECR
        logger.info("Tagging image as %s", image_uri)
        built_image.tag(image_uri)

        # Authenticate and push
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

    def _build_container_definition(
        self,
        config: AgentConfig,
        image_uri: str,
    ) -> dict[str, Any]:
        """Build the ECS container definition dict."""
        # Resource config — ECS uses CPU units (1 vCPU = 1024 units)
        resources = config.deploy.resources
        # Allow override via int string; default to task-level values
        cpu = resources.cpu or DEFAULT_CPU
        memory = resources.memory or DEFAULT_MEMORY
        # Strip non-numeric characters if user passed "512m" style
        cpu_str = "".join(c for c in cpu if c.isdigit()) or DEFAULT_CPU
        memory_str = "".join(c for c in memory if c.isdigit()) or DEFAULT_MEMORY

        # Environment variables
        env_vars: dict[str, str] = {
            "AGENT_NAME": config.name,
            "AGENT_VERSION": config.version,
            "AGENT_FRAMEWORK": config.framework.value,
        }
        # Add user-defined env vars, excluding AWS_ prefixed infra vars
        for key, value in config.deploy.env_vars.items():
            if not key.startswith("AWS_"):
                env_vars[key] = value

        return {
            "name": config.name,
            "image": image_uri,
            "cpu": int(cpu_str),
            "memory": int(memory_str),
            "essential": True,
            "portMappings": [
                {
                    "containerPort": 8080,
                    "protocol": "tcp",
                }
            ],
            "environment": [{"name": k, "value": v} for k, v in env_vars.items()],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": f"/agentbreeder/{config.name}",
                    "awslogs-region": (
                        self._aws_config.region if self._aws_config else DEFAULT_REGION
                    ),
                    "awslogs-stream-prefix": "ecs",
                    "awslogs-create-group": "true",
                },
            },
            "healthCheck": {
                "command": ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"],
                "interval": 30,
                "timeout": 5,
                "retries": 3,
                "startPeriod": 15,
            },
        }

    async def _register_task_definition(self, config: AgentConfig, image_uri: str) -> str:
        """Register an ECS task definition and return its ARN."""
        ecs = self._get_boto3_client("ecs")
        assert self._aws_config is not None, (
            "provision() must be called before _register_task_definition()"
        )
        aws = self._aws_config

        resources = config.deploy.resources
        cpu_str = "".join(c for c in (resources.cpu or DEFAULT_CPU) if c.isdigit()) or DEFAULT_CPU
        memory_str = (
            "".join(c for c in (resources.memory or DEFAULT_MEMORY) if c.isdigit())
            or DEFAULT_MEMORY
        )

        container_def = self._build_container_definition(config, image_uri)

        kwargs: dict[str, Any] = {
            "family": config.name,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": cpu_str,
            "memory": memory_str,
            "executionRoleArn": aws.execution_role_arn,
            "containerDefinitions": [container_def],
            "tags": [
                {"key": "managed-by", "value": "agentbreeder"},
                {"key": "agent-name", "value": config.name},
                {"key": "agent-version", "value": config.version},
                {"key": "team", "value": config.team},
            ],
        }

        if aws.task_role_arn:
            kwargs["taskRoleArn"] = aws.task_role_arn

        logger.info("Registering ECS task definition for '%s'", config.name)
        response = ecs.register_task_definition(**kwargs)
        task_def_arn = response["taskDefinition"]["taskDefinitionArn"]
        logger.info("Registered task definition: %s", task_def_arn)
        return task_def_arn

    async def _create_or_update_service(self, config: AgentConfig, task_def_arn: str) -> None:
        """Create a new ECS service or update an existing one."""
        ecs = self._get_boto3_client("ecs")
        assert self._aws_config is not None, (
            "provision() must be called before _create_or_update_service()"
        )
        aws = self._aws_config

        network_config = {
            "awsvpcConfiguration": {
                "subnets": aws.vpc_subnets,
                "securityGroups": aws.security_groups,
                "assignPublicIp": "ENABLED",
            }
        }

        # Try to describe the service to see if it exists
        try:
            response = ecs.describe_services(
                cluster=aws.ecs_cluster,
                services=[config.name],
            )
            existing = [s for s in response.get("services", []) if s["status"] != "INACTIVE"]
        except Exception:
            existing = []

        if existing:
            logger.info("Updating existing ECS service: %s", config.name)
            ecs.update_service(
                cluster=aws.ecs_cluster,
                service=config.name,
                taskDefinition=task_def_arn,
                desiredCount=1,
                networkConfiguration=network_config,
                forceNewDeployment=True,
            )
        else:
            logger.info("Creating new ECS service: %s", config.name)
            ecs.create_service(
                cluster=aws.ecs_cluster,
                serviceName=config.name,
                taskDefinition=task_def_arn,
                desiredCount=1,
                launchType="FARGATE",
                networkConfiguration=network_config,
                tags=[
                    {"key": "managed-by", "value": "agentbreeder"},
                    {"key": "agent-name", "value": config.name},
                    {"key": "team", "value": config.team},
                ],
            )

        # Wait for service stability
        logger.info("Waiting for ECS service '%s' to stabilize...", config.name)
        waiter = ecs.get_waiter("services_stable")
        waiter.wait(
            cluster=aws.ecs_cluster,
            services=[config.name],
            WaiterConfig={
                "Delay": WAITER_DELAY,
                "MaxAttempts": WAITER_MAX_ATTEMPTS,
            },
        )
        logger.info("ECS service '%s' is stable", config.name)

    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult:
        """Build, push, and deploy the agent to ECS Fargate.

        Steps:
        1. Build and push container image to ECR
        2. Register ECS task definition
        3. Create or update ECS service
        4. Wait for service stability
        5. Return the service endpoint
        """
        if self._aws_config is None:
            self._aws_config = _extract_ecs_config(config)
        aws = self._aws_config

        if self._image_uri is None:
            self._image_uri = _get_ecr_image_uri(aws, config.name, config.version)

        # Step 1: Push image to ECR
        await self._push_image(image, self._image_uri)

        # Step 2: Register task definition
        self._task_definition_arn = await self._register_task_definition(config, self._image_uri)

        # Step 3 & 4: Create/update service and wait for stability
        await self._create_or_update_service(config, self._task_definition_arn)

        endpoint_url = f"https://{config.name}.{aws.region}.ecs.local"

        logger.info("ECS Fargate service deployed: %s → %s", config.name, endpoint_url)

        return DeployResult(
            endpoint_url=endpoint_url,
            container_id=self._image_uri,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def health_check(
        self,
        deploy_result: DeployResult,
        timeout: int = HEALTH_CHECK_TIMEOUT,
        interval: int = HEALTH_CHECK_INTERVAL,
    ) -> HealthStatus:
        """Verify the ECS service is healthy by polling its /health endpoint.

        In production the URL should be an ALB endpoint. If the service uses
        the placeholder ECS local URL, health checks may not resolve unless
        inside the VPC — this is expected behavior.
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
        """Remove the ECS service and clean up task definition revisions.

        Container images in ECR are NOT deleted to preserve rollback capability.
        Use `agentbreeder cleanup` for image pruning.
        """
        if self._aws_config is None:
            msg = (
                "Cannot teardown without AWS config. "
                "Call provision() or deploy() first, or re-initialize with config."
            )
            raise RuntimeError(msg)

        aws = self._aws_config
        ecs = self._get_boto3_client("ecs")

        # Step 1: Scale service to zero
        logger.info("Scaling ECS service '%s' to zero", agent_name)
        try:
            ecs.update_service(
                cluster=aws.ecs_cluster,
                service=agent_name,
                desiredCount=0,
            )
        except Exception as exc:
            logger.warning(
                "Could not scale service '%s' to zero: %s — continuing teardown",
                agent_name,
                exc,
            )

        # Step 2: Delete the ECS service
        logger.info("Deleting ECS service '%s'", agent_name)
        try:
            ecs.delete_service(
                cluster=aws.ecs_cluster,
                service=agent_name,
                force=True,
            )
            logger.info("ECS service '%s' deleted", agent_name)
        except Exception as exc:
            logger.error("Failed to delete ECS service '%s': %s", agent_name, exc)
            raise

        # Step 3: Deregister all task definition revisions for this family
        logger.info("Deregistering task definition revisions for family '%s'", agent_name)
        try:
            paginator = ecs.get_paginator("list_task_definitions")
            pages = paginator.paginate(familyPrefix=agent_name, status="ACTIVE")
            for page in pages:
                for task_def_arn in page.get("taskDefinitionArns", []):
                    ecs.deregister_task_definition(taskDefinition=task_def_arn)
                    logger.debug("Deregistered task definition: %s", task_def_arn)
        except Exception as exc:
            logger.warning(
                "Could not deregister task definitions for '%s': %s",
                agent_name,
                exc,
            )

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Retrieve logs from CloudWatch Logs for the ECS service.

        Uses the CloudWatch Logs API to fetch log events from the agent's
        dedicated log group: /agentbreeder/{agent_name}
        """
        logs_client = self._get_boto3_client("logs")
        log_group = f"/agentbreeder/{agent_name}"

        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "limit": 100,
            "startFromHead": False,
        }
        if since:
            # CloudWatch uses milliseconds since epoch
            kwargs["startTime"] = int(since.timestamp() * 1000)

        try:
            response = logs_client.filter_log_events(**kwargs)
            events = response.get("events", [])

            if not events:
                return [f"No logs found in log group '{log_group}'"]

            log_lines: list[str] = []
            for event in events:
                ts_ms = event.get("timestamp", 0)
                ts = datetime.utcfromtimestamp(ts_ms / 1000).isoformat()
                message = event.get("message", "").rstrip()
                log_lines.append(f"{ts} {message}")

            return log_lines

        except Exception as exc:
            exc_type = type(exc).__name__
            if "ResourceNotFoundException" in exc_type or "NoSuchLogGroup" in str(exc):
                return [f"Log group '{log_group}' does not exist yet for agent '{agent_name}'"]
            return [f"Error fetching logs for '{agent_name}': {exc}"]

    async def get_url(self, agent_name: str) -> str:
        """Get the URL of a deployed ECS service.

        Returns the ALB URL stored in resource_ids if available; otherwise
        returns the ECS local placeholder URL.
        """
        if self._aws_config is None:
            msg = "Cannot get URL: AWS config not initialized."
            raise RuntimeError(msg)

        # In a real deployment the caller would supply the ALB URL via
        # resource_ids after provisioning an ALB. We return the placeholder.
        aws = self._aws_config
        return f"https://{agent_name}.{aws.region}.ecs.local"

    async def status(self, agent_name: str) -> dict[str, Any]:
        """Get the status of a deployed ECS service."""
        if self._aws_config is None:
            msg = "Cannot get status: AWS config not initialized."
            raise RuntimeError(msg)

        aws = self._aws_config
        ecs = self._get_boto3_client("ecs")

        response = ecs.describe_services(
            cluster=aws.ecs_cluster,
            services=[agent_name],
        )

        services = response.get("services", [])
        if not services:
            return {"name": agent_name, "status": "not_found"}

        svc = services[0]
        return {
            "name": agent_name,
            "status": svc.get("status", "UNKNOWN"),
            "running_count": svc.get("runningCount", 0),
            "desired_count": svc.get("desiredCount", 0),
            "pending_count": svc.get("pendingCount", 0),
            "task_definition": svc.get("taskDefinition", ""),
            "cluster": svc.get("clusterArn", aws.ecs_cluster),
        }
