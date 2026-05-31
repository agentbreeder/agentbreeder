"""Auto-provision phase — AWSProvisioner.provision_data_backend.

Unlike the greenfield ``provision()`` (which builds a whole VPC), the focused
``provision_data_backend()`` provisions ONE managed data store (Postgres for
pgvector, Redis for memory) INTO the deploy's existing BYO network. boto3 is
patched at the ``_client`` boundary so the suite needs no real AWS creds.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")
pytest.importorskip("botocore")

from botocore.exceptions import ClientError  # noqa: E402

from engine.provisioners.aws import AWSProvisioner  # noqa: E402
from engine.provisioners.base import DataBackendRequest  # noqa: E402

# -------------------------------------------------------------------- helpers


def _client_factory(builders: dict[str, MagicMock]) -> Any:
    def _fn(service: str, region: str, fields: dict[str, Any]) -> MagicMock:
        if service not in builders:
            raise KeyError(f"unexpected boto3 client requested: {service}")
        return builders[service]

    return _fn


def _make_ec2() -> MagicMock:
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-db"}
    return ec2


def _make_rds() -> MagicMock:
    rds = MagicMock()
    rds.describe_db_subnet_groups.side_effect = ClientError(
        {"Error": {"Code": "DBSubnetGroupNotFoundFault", "Message": "x"}},
        "DescribeDBSubnetGroups",
    )
    rds.describe_db_instances.side_effect = [
        ClientError(
            {"Error": {"Code": "DBInstanceNotFound", "Message": "x"}},
            "DescribeDBInstances",
        ),
        {"DBInstances": [{"Endpoint": {"Address": "demo.abc.rds.amazonaws.com"}}]},
    ]
    rds.get_waiter.return_value = MagicMock()
    return rds


def _make_secrets() -> MagicMock:
    sm = MagicMock()
    sm.create_secret.return_value = {
        "ARN": "arn:aws:secretsmanager:us-east-1:1:secret:agentbreeder/demo/db-password"
    }
    return sm


def _request(engine: str = "postgres", **net: Any) -> DataBackendRequest:
    network = {
        "vpc_id": "vpc-123",
        "subnet_ids": ["subnet-a", "subnet-b"],
        "agent_security_group_ids": ["sg-agent"],
    }
    network.update(net)
    return DataBackendRequest(
        cloud="aws",
        region="us-east-1",
        agent_name="demo",
        agent_version="1.0.0",
        engine=engine,
        network=network,
    )


@pytest.fixture
def fake_clients() -> dict[str, MagicMock]:
    return {"ec2": _make_ec2(), "rds": _make_rds(), "secretsmanager": _make_secrets()}


# -------------------------------------------------------------------- tests


async def test_postgres_returns_state_with_rds_resource(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        state = await AWSProvisioner().provision_data_backend(_request())

    assert state.cloud == "aws"
    assert state.region == "us-east-1"
    assert state.mode == "provisioned"
    rds = state.resources["rds"]
    assert rds["endpoint"] == "demo.abc.rds.amazonaws.com"
    assert rds["secret_arn"].startswith("arn:aws:secretsmanager:")
    assert rds["publicly_accessible"] is False
    assert rds["storage_encrypted"] is True


async def test_does_not_create_a_vpc(fake_clients) -> None:
    """BYO network — the focused path must NEVER create greenfield networking."""
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        await AWSProvisioner().provision_data_backend(_request())

    fake_clients["ec2"].create_vpc.assert_not_called()
    fake_clients["ec2"].create_subnet.assert_not_called()
    fake_clients["ec2"].create_nat_gateway.assert_not_called()


async def test_db_sg_ingress_is_from_agent_sg_only(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        await AWSProvisioner().provision_data_backend(_request())

    ec2 = fake_clients["ec2"]
    ec2.create_security_group.assert_called_once()
    # The only ingress authorization is 5432 sourced from the agent SG — never a CIDR.
    ingress_calls = ec2.authorize_security_group_ingress.call_args_list
    assert len(ingress_calls) == 1
    perm = ingress_calls[0].kwargs["IpPermissions"][0]
    assert perm["FromPort"] == 5432
    assert perm["ToPort"] == 5432
    assert "IpRanges" not in perm
    assert perm["UserIdGroupPairs"] == [{"GroupId": "sg-agent"}]


async def test_rds_provisioned_into_supplied_subnets(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        await AWSProvisioner().provision_data_backend(_request())

    create_subnet_group = fake_clients["rds"].create_db_subnet_group
    create_subnet_group.assert_called_once()
    assert create_subnet_group.call_args.kwargs["SubnetIds"] == ["subnet-a", "subnet-b"]
    # The DB instance binds to the new DB SG, not the agent SG.
    create_db = fake_clients["rds"].create_db_instance
    assert create_db.call_args.kwargs["VpcSecurityGroupIds"] == ["sg-db"]


async def test_password_never_appears_in_state(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        state = await AWSProvisioner().provision_data_backend(_request())

    blob = state.model_dump_json()
    pw = fake_clients["secretsmanager"].create_secret.call_args.kwargs["SecretString"]
    assert pw not in blob


async def test_vpc_id_derived_from_subnet_when_absent(fake_clients) -> None:
    """The ECS BYO contract exposes subnets + SGs but no VPC id — derive it."""
    fake_clients["ec2"].describe_subnets.return_value = {"Subnets": [{"VpcId": "vpc-derived"}]}
    req = _request()
    req.network.pop("vpc_id")  # only subnet_ids + agent SGs supplied
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        await AWSProvisioner().provision_data_backend(req)

    fake_clients["ec2"].describe_subnets.assert_called_once_with(SubnetIds=["subnet-a"])
    create_sg = fake_clients["ec2"].create_security_group
    assert create_sg.call_args.kwargs["VpcId"] == "vpc-derived"


async def test_redis_engine_not_implemented_yet(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        with pytest.raises(NotImplementedError):
            await AWSProvisioner().provision_data_backend(_request(engine="redis"))
