# AWS App Runner Deployer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `AWSAppRunnerDeployer` as a `runtime: app-runner` option under `cloud: aws`, giving users a GCP Cloud Run-equivalent serverless container target on AWS.

**Architecture:** App Runner deploys container images from ECR as HTTPS endpoints with no VPC/ALB/cluster required. The deployer shares ECR image push logic with `AWSECSDeployer` and follows the same `BaseDeployer` interface. `RUNTIME_DEPLOYERS["app-runner"]` in `__init__.py` routes to it.

**Tech Stack:** `boto3`, `httpx`, `pydantic`, Python 3.11+

**Reference:** Existing `engine/deployers/gcp_cloudrun.py` (structural parallel), `engine/deployers/aws_ecs.py` (ECR push pattern to follow)

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `engine/deployers/aws_app_runner.py` | Full `AWSAppRunnerDeployer` implementation |
| Modify | `engine/deployers/__init__.py:25-34` | Add `"app-runner"` to `RUNTIME_DEPLOYERS` |
| Create | `tests/unit/test_deployers_aws_app_runner.py` | Unit tests (mocked boto3) |
| Create | `tests/integration/test_aws_app_runner_integration.py` | Real AWS integration test (gated) |

---

## Task 1: Write failing unit tests

**Files:**
- Create: `tests/unit/test_deployers_aws_app_runner.py`

- [ ] **Step 1: Create the test file**

```python
"""Unit tests for the AWS App Runner deployer.

All AWS API calls are mocked via unittest.mock.patch — no real AWS
credentials or infrastructure are required.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import (
    AccessConfig,
    AgentConfig,
    CloudType,
    DeployConfig,
    FrameworkType,
    ModelConfig,
    ResourceConfig,
    ScalingConfig,
)
from engine.deployers.base import DeployResult


def _make_agent_config(
    *,
    name: str = "my-agent",
    version: str = "1.0.0",
    extra_env: dict[str, str] | None = None,
) -> AgentConfig:
    env_vars: dict[str, str] = {
        "AWS_ACCOUNT_ID": "123456789012",
        "AWS_REGION": "us-east-1",
        "AWS_ECR_REPO": "agentbreeder",
        "LOG_LEVEL": "info",
    }
    if extra_env:
        env_vars.update(extra_env)

    return AgentConfig(
        name=name,
        version=version,
        description="Test agent",
        team="engineering",
        owner="alice@example.com",
        framework=FrameworkType.langgraph,
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(
            cloud=CloudType.aws,
            runtime="app-runner",
            region="us-east-1",
            scaling=ScalingConfig(min=1, max=5),
            resources=ResourceConfig(cpu="1", memory="2Gi"),
            env_vars=env_vars,
        ),
        access=AccessConfig(),
    )


def _make_deployer():
    from engine.deployers.aws_app_runner import AWSAppRunnerDeployer
    return AWSAppRunnerDeployer()


class TestExtractAppRunnerConfig:
    def test_extracts_required_fields(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config
        config = _make_agent_config()
        ar = _extract_app_runner_config(config)
        assert ar.account_id == "123456789012"
        assert ar.region == "us-east-1"
        assert ar.ecr_repo == "agentbreeder"

    def test_raises_when_account_id_missing(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config
        config = _make_agent_config()
        del config.deploy.env_vars["AWS_ACCOUNT_ID"]
        with pytest.raises(ValueError, match="AWS_ACCOUNT_ID"):
            _extract_app_runner_config(config)

    def test_region_falls_back_to_deploy_region(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config
        config = _make_agent_config()
        del config.deploy.env_vars["AWS_REGION"]
        config.deploy.region = "eu-west-1"
        ar = _extract_app_runner_config(config)
        assert ar.region == "eu-west-1"

    def test_ecr_repo_defaults_to_agent_name(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config
        config = _make_agent_config()
        del config.deploy.env_vars["AWS_ECR_REPO"]
        ar = _extract_app_runner_config(config)
        assert ar.ecr_repo == "my-agent"

    def test_optional_access_role_arn(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config
        config = _make_agent_config(
            extra_env={"AWS_APP_RUNNER_ACCESS_ROLE_ARN": "arn:aws:iam::123:role/AppRunnerECRAccess"}
        )
        ar = _extract_app_runner_config(config)
        assert ar.access_role_arn == "arn:aws:iam::123:role/AppRunnerECRAccess"


class TestProvision:
    @pytest.mark.asyncio
    async def test_provision_returns_placeholder_url(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.return_value = {"repositories": [{}]}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            result = await deployer.provision(config)

        assert "my-agent" in result.endpoint_url
        assert result.resource_ids["account_id"] == "123456789012"
        assert "image_uri" in result.resource_ids

    @pytest.mark.asyncio
    async def test_provision_creates_ecr_repo_when_absent(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.side_effect = (
            ecr_mock.exceptions.RepositoryNotFoundException
        )

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            await deployer.provision(config)

        ecr_mock.create_repository.assert_called_once()

    @pytest.mark.asyncio
    async def test_provision_raises_import_error_without_boto3(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        original = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="pip install agentbreeder"):
                await deployer.provision(config)
        finally:
            if original is None:
                del sys.modules["boto3"]
            else:
                sys.modules["boto3"] = original


class TestDeploy:
    def _make_image(self) -> MagicMock:
        from pathlib import Path
        img = MagicMock()
        img.tag = "my-agent:1.0.0"
        img.context_dir = Path("/tmp/agent-context")
        return img

    @pytest.mark.asyncio
    async def test_deploy_creates_new_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_app_runner import _extract_app_runner_config
        deployer._ar_config = _extract_app_runner_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        ar_mock = MagicMock()
        ar_mock.exceptions = MagicMock()
        ar_mock.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )
        ar_mock.describe_service.side_effect = ar_mock.exceptions.ResourceNotFoundException
        ar_mock.create_service.return_value = {
            "Service": {
                "ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc",
                "ServiceUrl": "abc123.us-east-1.awsapprunner.com",
                "Status": "RUNNING",
            }
        }

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ar_mock),
            patch.object(deployer, "_wait_for_service_running", new_callable=AsyncMock,
                         return_value="https://abc123.us-east-1.awsapprunner.com"),
        ):
            result = await deployer.deploy(config, image)

        ar_mock.create_service.assert_called_once()
        call_kwargs = ar_mock.create_service.call_args.kwargs
        assert call_kwargs["ServiceName"] == "my-agent"
        assert result.status == "running"
        assert "awsapprunner.com" in result.endpoint_url

    @pytest.mark.asyncio
    async def test_deploy_updates_existing_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_app_runner import _extract_app_runner_config
        deployer._ar_config = _extract_app_runner_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {
                "ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc",
                "ServiceUrl": "abc123.us-east-1.awsapprunner.com",
                "Status": "RUNNING",
            }
        }
        ar_mock.update_service.return_value = {
            "Service": {"ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc"}
        }

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ar_mock),
            patch.object(deployer, "_wait_for_service_running", new_callable=AsyncMock,
                         return_value="https://abc123.us-east-1.awsapprunner.com"),
        ):
            await deployer.deploy(config, image)

        ar_mock.update_service.assert_called_once()
        ar_mock.create_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_env_vars_exclude_aws_prefixed_keys(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config, _build_env_vars
        deployer._ar_config = _extract_app_runner_config(config)

        env_list = _build_env_vars(config)
        env_names = {e["Name"] for e in env_list}

        assert "AGENT_NAME" in env_names
        assert "LOG_LEVEL" in env_names
        assert "AWS_ACCOUNT_ID" not in env_names
        assert "AWS_REGION" not in env_names


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_deletes_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config
        deployer._ar_config = _extract_app_runner_config(config)

        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {"ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc"}
        }
        ar_mock.delete_service.return_value = {}

        with patch.object(deployer, "_get_boto3_client", return_value=ar_mock):
            await deployer.teardown("my-agent")

        ar_mock.delete_service.assert_called_once()
        call_kwargs = ar_mock.delete_service.call_args.kwargs
        assert "ServiceArn" in call_kwargs

    @pytest.mark.asyncio
    async def test_teardown_raises_without_ar_config(self) -> None:
        deployer = _make_deployer()
        with pytest.raises(RuntimeError, match="App Runner config"):
            await deployer.teardown("orphan-agent")


class TestGetLogs:
    @pytest.mark.asyncio
    async def test_get_logs_calls_cloudwatch(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config
        deployer._ar_config = _extract_app_runner_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.return_value = {
            "events": [{"timestamp": 1700000000000, "message": "App Runner started"}]
        }

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            logs = await deployer.get_logs("my-agent")

        logs_mock.filter_log_events.assert_called_once()
        assert "App Runner started" in logs[0]


class TestDeployerRegistry:
    def test_app_runner_runtime_routes_to_app_runner_deployer(self) -> None:
        from engine.deployers import get_deployer
        from engine.deployers.aws_app_runner import AWSAppRunnerDeployer
        config = _make_agent_config()
        deployer = get_deployer(config.deploy.cloud, runtime="app-runner")
        assert isinstance(deployer, AWSAppRunnerDeployer)

    def test_ecs_fargate_runtime_routes_to_ecs_deployer(self) -> None:
        from engine.deployers import get_deployer
        from engine.deployers.aws_ecs import AWSECSDeployer
        config = _make_agent_config()
        deployer = get_deployer(config.deploy.cloud, runtime="ecs-fargate")
        assert isinstance(deployer, AWSECSDeployer)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/unit/test_deployers_aws_app_runner.py -x -q 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'engine.deployers.aws_app_runner'`

---

## Task 2: Implement `AWSAppRunnerDeployer`

**Files:**
- Create: `engine/deployers/aws_app_runner.py`

- [ ] **Step 1: Create the implementation**

```python
"""AWS App Runner deployer.

Deploys agents to AWS App Runner with:
- ECR for container image storage
- App Runner for serverless container execution (no VPC/ALB required)
- CloudWatch Logs for log aggregation

Structurally parallel to GCP Cloud Run — push image, get HTTPS URL.
Cloud-specific logic stays in this module — never leak AWS details elsewhere.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from engine.config_parser import AgentConfig
from engine.deployers.base import BaseDeployer, DeployResult, HealthStatus, InfraResult
from engine.runtimes.base import ContainerImage

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-east-1"
DEFAULT_CPU = "1 vCPU"       # App Runner CPU spec
DEFAULT_MEMORY = "2 GB"      # App Runner memory spec
HEALTH_CHECK_TIMEOUT = 300   # App Runner can take up to 5 min to become RUNNING
HEALTH_CHECK_INTERVAL = 10
POLL_INTERVAL = 15           # seconds between status polls while waiting for RUNNING


class AWSAppRunnerConfig(BaseModel):
    """AWS App Runner-specific configuration extracted from AgentConfig.deploy."""

    account_id: str
    region: str = DEFAULT_REGION
    ecr_repo: str
    access_role_arn: str | None = None  # IAM role for App Runner to pull from ECR


def _extract_app_runner_config(config: AgentConfig) -> AWSAppRunnerConfig:
    """Extract App Runner config from deploy.env_vars (AWS_ prefix convention)."""
    env = config.deploy.env_vars

    account_id = env.get("AWS_ACCOUNT_ID", "")
    if not account_id:
        msg = (
            "AWS account ID is required for App Runner deployment. "
            "Set AWS_ACCOUNT_ID in deploy.env_vars."
        )
        raise ValueError(msg)

    region = env.get("AWS_REGION") or config.deploy.region or DEFAULT_REGION
    ecr_repo = env.get("AWS_ECR_REPO") or config.name

    return AWSAppRunnerConfig(
        account_id=account_id,
        region=region,
        ecr_repo=ecr_repo,
        access_role_arn=env.get("AWS_APP_RUNNER_ACCESS_ROLE_ARN"),
    )


def _get_ecr_image_uri(ar_config: AWSAppRunnerConfig, agent_name: str, version: str) -> str:
    """Build the full ECR image URI for App Runner."""
    return (
        f"{ar_config.account_id}.dkr.ecr.{ar_config.region}.amazonaws.com"
        f"/{ar_config.ecr_repo}:{version}"
    )


def _build_env_vars(config: AgentConfig) -> list[dict[str, str]]:
    """Build App Runner env var list, excluding AWS_ infra keys."""
    env: dict[str, str] = {
        "AGENT_NAME": config.name,
        "AGENT_VERSION": config.version,
        "AGENT_FRAMEWORK": config.framework.value,
    }
    import os as _os
    if otel := _os.getenv("OPENTELEMETRY_ENDPOINT"):
        env["OPENTELEMETRY_ENDPOINT"] = otel
    for key, value in config.deploy.env_vars.items():
        if not key.startswith("AWS_"):
            env[key] = value
    return [{"Name": k, "Value": v} for k, v in env.items()]


class AWSAppRunnerDeployer(BaseDeployer):
    """Deploys agents to AWS App Runner.

    Uses ECR for container storage and App Runner for serverless HTTPS execution.
    No VPC, ALB, or cluster configuration required — parallel to GCP Cloud Run.
    """

    def __init__(self) -> None:
        self._ar_config: AWSAppRunnerConfig | None = None
        self._image_uri: str | None = None
        self._service_arn: str | None = None

    def _get_boto3_client(self, service: str) -> Any:
        """Get a boto3 client for the given AWS service."""
        try:
            import boto3
        except ImportError as e:
            msg = "boto3 is not installed. Run: pip install agentbreeder[aws]"
            raise ImportError(msg) from e
        region = self._ar_config.region if self._ar_config else DEFAULT_REGION
        return boto3.client(service, region_name=region)

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Validate config and ensure ECR repo exists."""
        self._ar_config = _extract_app_runner_config(config)
        ar = self._ar_config

        logger.info(
            "Provisioning App Runner service for '%s' in account '%s' region '%s'",
            config.name, ar.account_id, ar.region,
        )

        await self._ensure_ecr_repository(config.name)
        self._image_uri = _get_ecr_image_uri(ar, config.name, config.version)

        # App Runner URL is only known after create; return placeholder
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
                encryptionConfiguration={"encryptionType": "AES256"},
                tags=[{"Key": "managed-by", "Value": "agentbreeder"}],
            )
        except Exception as exc:
            logger.warning("Could not verify ECR repository '%s': %s — continuing", repo_name, exc)

    async def _push_image(self, image: ContainerImage, image_uri: str) -> None:
        """Build, tag, and push the container image to ECR."""
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise ImportError(msg) from e

        assert self._ar_config is not None
        ar = self._ar_config
        ecr = self._get_boto3_client("ecr")

        auth_response = ecr.get_authorization_token(registryIds=[ar.account_id])
        auth_data = auth_response["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":", 1)

        docker_client = docker.from_env()

        logger.info("Building Docker image: %s", image.tag)
        built_image, build_logs = docker_client.images.build(
            path=str(image.context_dir), tag=image.tag, rm=True,
        )
        for chunk in build_logs:
            if "stream" in chunk and (line := chunk["stream"].strip()):
                logger.debug("  %s", line)

        built_image.tag(image_uri)
        logger.info("Pushing image to ECR: %s", image_uri)
        push_output = docker_client.images.push(
            image_uri,
            auth_config={"username": username, "password": password},
            stream=True, decode=True,
        )
        for chunk in push_output:
            if "status" in chunk:
                logger.debug("  %s", chunk["status"])
            if "error" in chunk:
                raise RuntimeError(f"ECR push failed: {chunk['error']}")

        logger.info("Image pushed: %s", image_uri)

    async def _wait_for_service_running(self, service_arn: str) -> str:
        """Poll App Runner until the service reaches RUNNING state, return its URL."""
        assert self._ar_config is not None
        ar_client = self._get_boto3_client("apprunner")

        for attempt in range(HEALTH_CHECK_TIMEOUT // POLL_INTERVAL):
            response = ar_client.describe_service(ServiceArn=service_arn)
            service = response["Service"]
            status = service.get("Status", "")
            url = f"https://{service.get('ServiceUrl', '')}"

            if status == "RUNNING":
                logger.info("App Runner service is RUNNING: %s", url)
                return url
            if status in ("CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"):
                msg = f"App Runner service failed with status: {status}"
                raise RuntimeError(msg)

            logger.debug(
                "App Runner status: %s (attempt %d/%d) — waiting %ds...",
                status, attempt + 1, HEALTH_CHECK_TIMEOUT // POLL_INTERVAL, POLL_INTERVAL,
            )
            await asyncio.sleep(POLL_INTERVAL)

        msg = f"App Runner service did not reach RUNNING within {HEALTH_CHECK_TIMEOUT}s"
        raise TimeoutError(msg)

    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult:
        """Build, push, and deploy the agent to App Runner."""
        if self._ar_config is None:
            self._ar_config = _extract_app_runner_config(config)
        ar = self._ar_config

        if self._image_uri is None:
            self._image_uri = _get_ecr_image_uri(ar, config.name, config.version)

        await self._push_image(image, self._image_uri)

        ar_client = self._get_boto3_client("apprunner")

        image_config: dict[str, Any] = {
            "ImageIdentifier": self._image_uri,
            "ImageRepositoryType": "ECR",
            "ImageConfiguration": {
                "Port": "8080",
                "RuntimeEnvironmentVariables": {
                    e["Name"]: e["Value"] for e in _build_env_vars(config)
                },
                "StartCommand": "",
            },
        }
        if ar.access_role_arn:
            image_config["AuthenticationConfiguration"] = {"AccessRoleArn": ar.access_role_arn}

        instance_config = {
            "Cpu": config.deploy.resources.cpu or DEFAULT_CPU,
            "Memory": config.deploy.resources.memory or DEFAULT_MEMORY,
        }

        # Check if service already exists
        service_arn: str | None = None
        try:
            response = ar_client.describe_service(ServiceName=config.name)
            service_arn = response["Service"]["ServiceArn"]
            logger.info("Updating existing App Runner service: %s", config.name)
            ar_client.update_service(
                ServiceArn=service_arn,
                SourceConfiguration={"ImageRepository": image_config},
                InstanceConfiguration=instance_config,
            )
        except ar_client.exceptions.ResourceNotFoundException:
            logger.info("Creating new App Runner service: %s", config.name)
            response = ar_client.create_service(
                ServiceName=config.name,
                SourceConfiguration={"ImageRepository": image_config},
                InstanceConfiguration=instance_config,
                Tags=[
                    {"Key": "managed-by", "Value": "agentbreeder"},
                    {"Key": "agent-name", "Value": config.name},
                    {"Key": "team", "Value": config.team},
                ],
            )
            service_arn = response["Service"]["ServiceArn"]

        self._service_arn = service_arn
        endpoint_url = await self._wait_for_service_running(service_arn)

        logger.info("App Runner service deployed: %s → %s", config.name, endpoint_url)

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
        """Poll /health endpoint until the service responds 200."""
        url = f"{deploy_result.endpoint_url}/health"
        checks: dict[str, bool] = {"reachable": False, "healthy": False}

        for attempt in range(timeout // interval):
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    response = await client.get(url)
                    checks["reachable"] = True
                    if response.status_code == 200:
                        checks["healthy"] = True
                        logger.info("Health check passed (attempt %d)", attempt + 1)
                        return HealthStatus(healthy=True, checks=checks)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                pass
            await asyncio.sleep(interval)

        return HealthStatus(healthy=False, checks=checks)

    async def teardown(self, agent_name: str) -> None:
        """Delete the App Runner service."""
        if self._ar_config is None:
            msg = "Cannot teardown without App Runner config. Call provision() or deploy() first."
            raise RuntimeError(msg)

        ar_client = self._get_boto3_client("apprunner")

        try:
            response = ar_client.describe_service(ServiceName=agent_name)
            service_arn = response["Service"]["ServiceArn"]
            logger.info("Deleting App Runner service: %s", agent_name)
            ar_client.delete_service(ServiceArn=service_arn)
            logger.info("App Runner service deleted: %s", agent_name)
        except Exception as exc:
            logger.error("Failed to delete App Runner service '%s': %s", agent_name, exc)
            raise

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
                f"{datetime.utcfromtimestamp(e['timestamp'] / 1000).isoformat()} {e['message'].rstrip()}"
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
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
python3 -m pytest tests/unit/test_deployers_aws_app_runner.py -x -q 2>&1 | tail -15
```

Expected: All tests pass except `TestDeployerRegistry` (needs `__init__.py` update next).

- [ ] **Step 3: Commit**

```bash
git add engine/deployers/aws_app_runner.py tests/unit/test_deployers_aws_app_runner.py
git commit -m "feat(deployers): add AWSAppRunnerDeployer — serverless container target on AWS"
```

---

## Task 3: Wire App Runner into the deployer registry

**Files:**
- Modify: `engine/deployers/__init__.py`

- [ ] **Step 1: Add App Runner to registry**

In `engine/deployers/__init__.py`, add the import and two entries:

```python
# Add import after existing imports (line 14 area):
from engine.deployers.aws_app_runner import AWSAppRunnerDeployer

# Add to RUNTIME_DEPLOYERS dict (after line 33):
    "app-runner": AWSAppRunnerDeployer,
    "apprunner": AWSAppRunnerDeployer,
```

- [ ] **Step 2: Run registry test**

```bash
python3 -m pytest tests/unit/test_deployers_aws_app_runner.py::TestDeployerRegistry -v
```

Expected: PASS

- [ ] **Step 3: Run all deployer tests to confirm no regression**

```bash
python3 -m pytest tests/unit/test_deployers_aws.py tests/unit/test_deployers_aws_app_runner.py tests/unit/test_deployers_azure.py tests/unit/test_deployers_kubernetes.py tests/unit/test_gcp_cloudrun_deployer.py -q 2>&1 | tail -10
```

Expected: All 175+ tests pass.

- [ ] **Step 4: Commit**

```bash
git add engine/deployers/__init__.py
git commit -m "feat(deployers): register AWSAppRunnerDeployer for runtime: app-runner"
```

---

## Task 4: Integration test (gated behind `RUN_AWS_INTEGRATION_TESTS`)

**Files:**
- Create: `tests/integration/test_aws_app_runner_integration.py`

> **Note:** This test requires real AWS credentials and an existing ECR repository. Run with `RUN_AWS_INTEGRATION_TESTS=1` after cloud environment setup.

- [ ] **Step 1: Create the integration test**

```python
"""AWS App Runner integration test.

Requires:
  RUN_AWS_INTEGRATION_TESTS=1
  AWS_ACCOUNT_ID, AWS_REGION, AWS credentials in env

Run: RUN_AWS_INTEGRATION_TESTS=1 pytest tests/integration/test_aws_app_runner_integration.py -v
"""
from __future__ import annotations

import os
import pytest


SKIP_REASON = "Set RUN_AWS_INTEGRATION_TESTS=1 to run AWS integration tests"
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_AWS_INTEGRATION_TESTS") != "1",
    reason=SKIP_REASON,
)


@pytest.mark.asyncio
async def test_app_runner_full_deploy_and_teardown() -> None:
    """Deploy the langgraph example agent to App Runner, verify health, then tear down."""
    from pathlib import Path

    from engine.config_parser import parse_config
    from engine.builder import RuntimeBuilder
    from engine.deployers.aws_app_runner import AWSAppRunnerDeployer

    # Use the langgraph example agent as the test subject
    agent_dir = Path("examples/langgraph-agent")
    config = parse_config(agent_dir / "agent.yaml")

    # Override cloud target for this test
    config.deploy.cloud = "aws"  # type: ignore[assignment]
    config.deploy.runtime = "app-runner"
    config.deploy.env_vars.update({
        "AWS_ACCOUNT_ID": os.environ["AWS_ACCOUNT_ID"],
        "AWS_REGION": os.environ.get("AWS_REGION", "us-east-1"),
    })

    deployer = AWSAppRunnerDeployer()

    # 1. Provision
    infra = await deployer.provision(config)
    assert infra.endpoint_url

    # 2. Build container image (uses existing RuntimeBuilder)
    builder = RuntimeBuilder.for_framework(config.framework)
    image = builder.build(agent_dir, config)

    # 3. Deploy
    result = await deployer.deploy(config, image)
    assert result.status == "running"
    assert "awsapprunner.com" in result.endpoint_url

    # 4. Health check
    health = await deployer.health_check(result)
    assert health.healthy, f"Health check failed: {health.checks}"

    # 5. Teardown
    await deployer.teardown(config.name)
    print(f"Integration test passed. Endpoint was: {result.endpoint_url}")
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_aws_app_runner_integration.py
git commit -m "test(integration): add AWS App Runner integration test (gated)"
```
