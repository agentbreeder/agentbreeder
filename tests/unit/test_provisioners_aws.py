"""Unit tests for AWSProvisioner — boto3 fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")
pytest.importorskip("botocore")

from botocore.exceptions import ClientError  # noqa: E402

from engine.provisioners import InfraValidationInput  # noqa: E402
from engine.provisioners.aws import AWSProvisioner  # noqa: E402


def _make_session(sts_account: str = "123456789012") -> MagicMock:
    """Build a boto3 Session mock whose .client(svc) returns per-service mocks."""
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": sts_account}

    ecs = MagicMock()
    ecs.describe_clusters.return_value = {"clusters": [{"status": "ACTIVE"}]}

    iam = MagicMock()
    iam.get_role.return_value = {"Role": {"Arn": "arn:aws:iam::123456789012:role/exec-role"}}

    ec2 = MagicMock()
    ec2.describe_subnets.return_value = {"Subnets": [{"AvailabilityZone": "us-east-1a"}]}
    ec2.describe_security_groups.return_value = {"SecurityGroups": [{"GroupName": "agent-sg"}]}

    ecr = MagicMock()
    ecr.describe_repositories.return_value = {
        "repositories": [{"repositoryUri": "123.dkr.ecr.us-east-1.amazonaws.com/agent"}]
    }

    services = {"sts": sts, "ecs": ecs, "iam": iam, "ec2": ec2, "ecr": ecr}
    session = MagicMock()
    session.client.side_effect = lambda svc: services[svc]
    return session


@pytest.fixture
def patch_boto3_session():
    with patch("engine.provisioners.aws.boto3.session.Session") as factory:
        factory.return_value = _make_session()
        yield factory


async def test_aws_simple_mode_passes_when_account_matches(patch_boto3_session) -> None:
    p = AWSProvisioner()
    payload = InfraValidationInput(
        cloud="aws",
        region="us-east-1",
        mode="simple",
        fields={"AWS_ACCOUNT_ID": "123456789012"},
    )
    result = await p.validate_existing(payload)
    assert result.valid is True
    assert result.checks[0].resource == "credentials"
    assert result.checks[0].status == "found"


async def test_aws_simple_mode_fails_when_account_mismatch(patch_boto3_session) -> None:
    p = AWSProvisioner()
    payload = InfraValidationInput(
        cloud="aws",
        region="us-east-1",
        mode="simple",
        fields={"AWS_ACCOUNT_ID": "999999999999"},
    )
    result = await p.validate_existing(payload)
    assert result.valid is False
    assert result.checks[0].status == "error"
    assert "999999999999" in result.checks[0].detail


async def test_aws_full_mode_resolves_each_named_resource(patch_boto3_session) -> None:
    p = AWSProvisioner()
    payload = InfraValidationInput(
        cloud="aws",
        region="us-east-1",
        mode="full",
        fields={
            "AWS_ACCOUNT_ID": "123456789012",
            "AWS_ECS_CLUSTER": "my-cluster",
            "AWS_EXECUTION_ROLE_ARN": "arn:aws:iam::123456789012:role/exec-role",
            "AWS_VPC_SUBNETS": "subnet-aaa,subnet-bbb",
            "AWS_SECURITY_GROUPS": "sg-xxx",
            "AWS_ECR_REPOSITORY": "agent",
        },
    )
    result = await p.validate_existing(payload)
    assert result.valid is True
    resources = [c.resource for c in result.checks]
    assert "my-cluster" in resources
    assert "subnet-aaa" in resources
    assert "subnet-bbb" in resources
    assert "sg-xxx" in resources
    assert "agent" in resources


async def test_aws_missing_subnet_is_reported_as_missing() -> None:
    session = _make_session()
    ec2 = session.client.side_effect("ec2")
    # Re-arm ec2 to raise the boto3 not-found error.
    ec2.describe_subnets.side_effect = ClientError(
        {"Error": {"Code": "InvalidSubnetID.NotFound", "Message": "subnet absent"}},
        "DescribeSubnets",
    )
    with patch("engine.provisioners.aws.boto3.session.Session", return_value=session):
        p = AWSProvisioner()
        payload = InfraValidationInput(
            cloud="aws",
            region="us-east-1",
            mode="full",
            fields={
                "AWS_ACCOUNT_ID": "123456789012",
                "AWS_VPC_SUBNETS": "subnet-missing",
            },
        )
        result = await p.validate_existing(payload)
    assert result.valid is False
    missing = [c for c in result.checks if c.resource == "subnet-missing"]
    assert len(missing) == 1
    assert missing[0].status == "missing"


async def test_aws_access_denied_is_reported_as_forbidden() -> None:
    session = _make_session()
    sts = session.client.side_effect("sts")
    sts.get_caller_identity.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetCallerIdentity"
    )
    with patch("engine.provisioners.aws.boto3.session.Session", return_value=session):
        p = AWSProvisioner()
        payload = InfraValidationInput(
            cloud="aws", region="us-east-1", mode="simple", fields={"AWS_ACCOUNT_ID": "1"}
        )
        result = await p.validate_existing(payload)
    assert result.valid is False
    assert result.checks[0].status == "forbidden"
