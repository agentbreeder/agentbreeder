"""Cross-cloud parity matrix (#198, epic #505).

One governed agent (guardrails declared) deployed to AWS ECS Fargate, GCP Cloud
Run, and Azure Container Apps must produce the *same* governance topology: the
AgentBreeder sidecar is the ingress container on :8080 and the agent listens on
an internal :8081, with the sidecar reverse-proxying to it. This is the single
source of truth that inbound guardrails / bearer-token enforcement fire
identically on all three targets — if a deployer regresses to routing external
traffic straight at the agent, the matrix breaks here.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

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

SIDECAR = "agentbreeder-sidecar"


def _governed_config(*, env_vars: dict[str, str], runtime: str) -> AgentConfig:
    """A minimal agent that declares guardrails (→ sidecar required)."""
    config = AgentConfig(
        name="my-agent",
        version="1.0.0",
        team="engineering",
        owner="dev@example.com",
        framework=FrameworkType.langgraph,
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(
            cloud=CloudType.aws,
            runtime=runtime,
            region="us-east-1",
            env_vars=env_vars,
            resources=ResourceConfig(cpu="1", memory="1Gi"),
            scaling=ScalingConfig(min=1, max=3),
        ),
        access=AccessConfig(),
    )
    config.guardrails = ["pii_detection"]
    return config


def _gcp_spec() -> dict:
    from engine.deployers.gcp_cloudrun import (
        _build_service_template,
        _extract_cloudrun_config,
    )

    config = _governed_config(env_vars={"GCP_PROJECT_ID": "my-project-123"}, runtime="cloud-run")
    gcp = _extract_cloudrun_config(config)
    template = _build_service_template(config, gcp, "img:1.0.0")
    containers = template["containers"]
    sidecar = next(c for c in containers if c.get("name") == SIDECAR)
    agent = next(c for c in containers if c.get("name") != SIDECAR)
    sc_env = {e["name"]: e.get("value") for e in sidecar["env"]}
    agent_env = {e["name"]: e.get("value") for e in agent["env"] if "value" in e}
    return {
        "num_containers": len(containers),
        "sidecar_ingress_port": sidecar["ports"][0]["container_port"],
        "agent_internal_port": int(agent_env["PORT"]),
        "agent_declares_ingress": "ports" in agent,
        "sidecar_agent_url": sc_env["AGENTBREEDER_SIDECAR_AGENT_URL"],
    }


def _ecs_spec() -> dict:
    from engine.deployers.aws_ecs import AWSECSDeployer, _extract_ecs_config

    config = _governed_config(
        env_vars={
            "AWS_ACCOUNT_ID": "123456789012",
            "AWS_REGION": "us-east-1",
            "AWS_ECS_CLUSTER": "agentbreeder-cluster",
            "AWS_EXECUTION_ROLE_ARN": "arn:aws:iam::123456789012:role/ecsTaskExecutionRole",
            "AWS_VPC_SUBNETS": "subnet-aaa,subnet-bbb",
            "AWS_SECURITY_GROUPS": "sg-111",
        },
        runtime="ecs-fargate",
    )
    deployer = AWSECSDeployer()
    deployer._aws_config = _extract_ecs_config(config)
    ecs_mock = MagicMock()
    ecs_mock.register_task_definition.return_value = {
        "taskDefinition": {"taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/x:1"}
    }
    with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
        asyncio.run(deployer._register_task_definition(config, "img:1.0.0"))
    defs = ecs_mock.register_task_definition.call_args.kwargs["containerDefinitions"]
    by_name = {c["name"]: c for c in defs}
    sidecar = by_name[SIDECAR]
    agent = by_name[config.name]
    sc_env = {e["name"]: e["value"] for e in sidecar["environment"]}
    return {
        "num_containers": len(defs),
        "sidecar_ingress_port": sidecar["portMappings"][0]["containerPort"],
        "agent_internal_port": agent["portMappings"][0]["containerPort"],
        # ECS has no ALB here (public-IP mode); the security group only exposes
        # the sidecar's 8080. The agent's mapping is internal-only.
        "agent_declares_ingress": False,
        "sidecar_agent_url": sc_env["AGENTBREEDER_SIDECAR_AGENT_URL"],
    }


def _azure_spec() -> dict:
    from engine.deployers.azure_container_apps import (
        AzureContainerAppsDeployer,
        _extract_azure_config,
    )

    config = _governed_config(
        env_vars={
            "AZURE_SUBSCRIPTION_ID": "sub-1234",
            "AZURE_RESOURCE_GROUP": "rg-agents",
            "AZURE_CONTAINER_APPS_ENV": "aca-env-prod",
            "AZURE_REGISTRY_SERVER": "myregistry.azurecr.io",
            "AZURE_LOCATION": "eastus",
        },
        runtime="container-apps",
    )
    azure = _extract_azure_config(config)
    body = AzureContainerAppsDeployer()._build_container_app_body(
        config, azure, "img:1.0.0", "env-id"
    )
    containers = body["properties"]["template"]["containers"]
    sidecar = next(c for c in containers if c.get("name") == SIDECAR)
    agent = next(c for c in containers if c.get("name") != SIDECAR)
    agent_env = {e["name"]: e.get("value") for e in agent["env"] if "value" in e}
    sc_env = {e["name"]: e.get("value") for e in sidecar["env"]}
    return {
        "num_containers": len(containers),
        # Container Apps has one app-level ingress; it targets the sidecar port.
        "sidecar_ingress_port": body["properties"]["configuration"]["ingress"]["targetPort"],
        "agent_internal_port": int(agent_env["PORT"]),
        "agent_declares_ingress": False,
        "sidecar_agent_url": sc_env["AGENTBREEDER_SIDECAR_AGENT_URL"],
    }


@pytest.mark.parametrize(
    "spec_fn",
    [_gcp_spec, _ecs_spec, _azure_spec],
    ids=["gcp_cloud_run", "aws_ecs_fargate", "azure_container_apps"],
)
def test_inbound_routes_through_sidecar_on_every_cloud(spec_fn) -> None:
    spec = spec_fn()

    # Agent + sidecar are both present.
    assert spec["num_containers"] == 2
    # External traffic terminates at the sidecar on 8080 …
    assert spec["sidecar_ingress_port"] == 8080
    # … the agent only listens on the internal port …
    assert spec["agent_internal_port"] == 8081
    assert spec["agent_declares_ingress"] is False
    # … and the sidecar forwards to that internal port.
    assert spec["sidecar_agent_url"] == "http://localhost:8081"
