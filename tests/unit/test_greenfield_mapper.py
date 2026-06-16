"""Greenfield mapper — turn a provisioned ``InfraState`` into the deploy.env_vars
the cloud deployer reads, so the existing BYO deploy path serves an agent into a
freshly-provisioned footprint.

Covers ``engine.deployers._greenfield.infra_state_to_env`` (issue #537).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.deployers._greenfield import infra_state_to_env
from engine.provisioners.state import InfraState


def _aws_state() -> InfraState:
    """A representative greenfield InfraState as AWSProvisioner.provision() returns it."""
    return InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="agentbreeder.AWSProvisioner",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "vpc": {"vpc_id": "vpc-0abc", "cidr": "10.0.0.0/16"},
            "network": {
                "public_subnet_ids": ["subnet-pub1", "subnet-pub2"],
                "private_subnet_ids": ["subnet-prv1", "subnet-prv2"],
                "internet_gateway_id": "igw-0a",
                "nat_gateway_ids": ["nat-0a"],
            },
            "security_groups": {
                "alb_sg_id": "sg-alb",
                "agent_sg_id": "sg-agent",
                "db_sg_id": "sg-db",
            },
            "ecs_cluster": {"name": "agentbreeder-my-agent", "arn": "arn:aws:ecs:...:cluster/x"},
            "iam_execution_role": {
                "name": "agentbreeder-execution-my-agent",
                "arn": "arn:aws:iam::123456789012:role/agentbreeder-execution-my-agent",
            },
        },
    )


def test_aws_mapper_emits_the_env_the_ecs_deployer_reads() -> None:
    env = infra_state_to_env("aws", _aws_state())

    # These are exactly the keys aws_ecs.py::_extract_ecs_config reads.
    assert env["AWS_ECS_CLUSTER"] == "agentbreeder-my-agent"
    assert (
        env["AWS_EXECUTION_ROLE_ARN"]
        == "arn:aws:iam::123456789012:role/agentbreeder-execution-my-agent"
    )
    assert env["AWS_VPC_SUBNETS"] == "subnet-pub1,subnet-pub2"
    assert env["AWS_SECURITY_GROUPS"] == "sg-agent"
    assert env["AWS_REGION"] == "us-east-1"
    # Required by the ECS deployer — derived from the execution-role ARN.
    assert env["AWS_ACCOUNT_ID"] == "123456789012"


def test_aws_mapper_exposes_vpc_and_private_subnets_for_the_data_tier() -> None:
    # The data-backend auto-provision step places RDS/Redis into this VPC and
    # prefers private subnets.
    env = infra_state_to_env("aws", _aws_state())
    assert env["AWS_VPC_ID"] == "vpc-0abc"
    assert env["AWS_DB_SUBNETS"] == "subnet-prv1,subnet-prv2"


def test_aws_mapper_returns_only_strings() -> None:
    env = infra_state_to_env("aws", _aws_state())
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())


def _gcp_state() -> InfraState:
    """A representative greenfield InfraState as GCPProvisioner.provision() returns it."""
    return InfraState(
        cloud="gcp",
        region="us-central1",
        provisioned_by="agentbreeder.GCPProvisioner",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "artifact_registry": {
                "name": "projects/my-proj/locations/us-central1/repositories/agentbreeder",
                "repo": "agentbreeder",
                "region": "us-central1",
            },
            "service_account": {
                "email": "ab-my-agent@my-proj.iam.gserviceaccount.com",
                "sa_id": "ab-my-agent",
                "project": "my-proj",
            },
            "vpc_connector": {
                "name": "projects/my-proj/locations/us-central1/connectors/ab-my-agent",
                "network": "ab-my-agent-vpc",
            },
            "network": {"name": "ab-my-agent-vpc", "subnet": "ab-my-agent-subnet"},
        },
    )


def test_gcp_mapper_emits_the_env_the_cloudrun_deployer_reads() -> None:
    env = infra_state_to_env("gcp", _gcp_state())
    assert env["GCP_PROJECT_ID"] == "my-proj"
    assert env["GCP_SERVICE_ACCOUNT"] == "ab-my-agent@my-proj.iam.gserviceaccount.com"
    assert env["GCP_ARTIFACT_REGISTRY_REPO"] == "agentbreeder"
    assert env["GCP_REGION"] == "us-central1"
    assert env["GCP_VPC_CONNECTOR"].endswith("/connectors/ab-my-agent")
    # Data tier lands in the greenfield VPC.
    assert env["GCP_VPC_NAME"] == "ab-my-agent-vpc"
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())


def _azure_state() -> InfraState:
    """A representative greenfield InfraState as AzureProvisioner.provision() returns it."""
    return InfraState(
        cloud="azure",
        region="eastus",
        provisioned_by="agentbreeder.AzureProvisioner",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "resource_group": {
                "id": "/subscriptions/sub-123/resourceGroups/ab-my-agent-rg",
                "name": "ab-my-agent-rg",
                "region": "eastus",
            },
            "container_apps_environment": {"id": "...", "name": "ab-my-agent-env"},
            "acr": {
                "id": "...",
                "name": "abmyagentacr",
                "login_server": "abmyagentacr.azurecr.io",
            },
            "managed_identity": {"id": "...", "name": "ab-my-agent-id"},
            "vnet": {
                "id": "...",
                "name": "ab-my-agent-vnet",
                "db_subnet_id": "/subscriptions/sub-123/.../subnets/db",
            },
            "key_vault": {"id": "...", "name": "abkv", "uri": "https://abkv.vault.azure.net/"},
        },
    )


def test_azure_mapper_emits_the_env_the_aca_deployer_reads() -> None:
    env = infra_state_to_env("azure", _azure_state())
    assert env["AZURE_SUBSCRIPTION_ID"] == "sub-123"
    assert env["AZURE_RESOURCE_GROUP"] == "ab-my-agent-rg"
    assert env["AZURE_CONTAINER_APPS_ENV"] == "ab-my-agent-env"
    assert env["AZURE_REGISTRY_SERVER"] == "abmyagentacr.azurecr.io"
    assert env["AZURE_LOCATION"] == "eastus"
    # Data tier lands in the greenfield VNet's delegated subnet.
    assert env["AZURE_VNET_NAME"] == "ab-my-agent-vnet"
    assert env["AZURE_DB_SUBNET_ID"].endswith("/subnets/db")
    assert env["AZURE_KEYVAULT_URL"] == "https://abkv.vault.azure.net/"
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())


def test_unsupported_cloud_raises() -> None:
    state = _aws_state()
    state.cloud = "kubernetes"
    with pytest.raises(NotImplementedError):
        infra_state_to_env("kubernetes", state)
