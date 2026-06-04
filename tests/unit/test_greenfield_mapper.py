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


def test_aws_mapper_exposes_vpc_and_private_subnets_for_the_data_tier() -> None:
    # The data-backend auto-provision step places RDS/Redis into this VPC and
    # prefers private subnets.
    env = infra_state_to_env("aws", _aws_state())
    assert env["AWS_VPC_ID"] == "vpc-0abc"
    assert env["AWS_DB_SUBNETS"] == "subnet-prv1,subnet-prv2"


def test_aws_mapper_returns_only_strings() -> None:
    env = infra_state_to_env("aws", _aws_state())
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())


def test_unsupported_cloud_raises() -> None:
    state = _aws_state()
    state.cloud = "gcp"
    with pytest.raises(NotImplementedError):
        infra_state_to_env("gcp", state)
