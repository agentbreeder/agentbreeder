"""Auto-provision phase — AWSProvisioner.provision_data_backend(engine="redis").

Provisions a single-node ElastiCache Redis into the agent's BYO subnets behind a
dedicated security group (6379 from the agent SG only). boto3 is patched at the
``_client`` boundary.
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


def _client_factory(builders: dict[str, MagicMock]) -> Any:
    def _fn(service: str, region: str, fields: dict[str, Any]) -> MagicMock:
        if service not in builders:
            raise KeyError(f"unexpected boto3 client requested: {service}")
        return builders[service]

    return _fn


def _make_ec2() -> MagicMock:
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-redis"}
    ec2.describe_subnets.return_value = {"Subnets": [{"VpcId": "vpc-derived"}]}
    return ec2


def _make_elasticache() -> MagicMock:
    ec = MagicMock()
    ec.describe_cache_subnet_groups.side_effect = ClientError(
        {"Error": {"Code": "CacheSubnetGroupNotFoundFault", "Message": "x"}},
        "DescribeCacheSubnetGroups",
    )
    ec.describe_cache_clusters.side_effect = [
        ClientError(
            {"Error": {"Code": "CacheClusterNotFound", "Message": "x"}},
            "DescribeCacheClusters",
        ),
        {
            "CacheClusters": [
                {
                    "CacheClusterStatus": "available",
                    "CacheNodes": [
                        {"Endpoint": {"Address": "demo.abc.cache.amazonaws.com", "Port": 6379}}
                    ],
                }
            ]
        },
    ]
    ec.get_waiter.return_value = MagicMock()
    return ec


def _request() -> DataBackendRequest:
    return DataBackendRequest(
        cloud="aws",
        region="us-east-1",
        agent_name="demo",
        agent_version="1.0.0",
        engine="redis",
        network={"subnet_ids": ["subnet-a", "subnet-b"], "agent_security_group_ids": ["sg-agent"]},
    )


@pytest.fixture
def fake_clients() -> dict[str, MagicMock]:
    return {"ec2": _make_ec2(), "elasticache": _make_elasticache()}


async def test_redis_returns_state_with_elasticache_resource(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        state = await AWSProvisioner().provision_data_backend(_request())

    assert state.cloud == "aws"
    assert state.mode == "provisioned"
    ec = state.resources["elasticache"]
    assert ec["endpoint"] == "demo.abc.cache.amazonaws.com"
    assert ec["port"] == 6379
    assert ec["engine"] == "redis"
    # DB SG recorded under the destroy()-readable key.
    assert state.resources["security_groups"]["redis_sg_id"] == "sg-redis"


async def test_redis_sg_ingress_is_from_agent_sg_only(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        await AWSProvisioner().provision_data_backend(_request())

    ec2 = fake_clients["ec2"]
    ec2.create_security_group.assert_called_once()
    ingress = ec2.authorize_security_group_ingress.call_args_list
    assert len(ingress) == 1
    perm = ingress[0].kwargs["IpPermissions"][0]
    assert perm["FromPort"] == 6379
    assert perm["ToPort"] == 6379
    assert "IpRanges" not in perm
    assert perm["UserIdGroupPairs"] == [{"GroupId": "sg-agent"}]


async def test_redis_cluster_uses_supplied_subnets_and_new_sg(fake_clients) -> None:
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(fake_clients)):
        await AWSProvisioner().provision_data_backend(_request())

    ec = fake_clients["elasticache"]
    create_grp = ec.create_cache_subnet_group
    create_grp.assert_called_once()
    assert create_grp.call_args.kwargs["SubnetIds"] == ["subnet-a", "subnet-b"]
    create_cluster = ec.create_cache_cluster
    assert create_cluster.call_args.kwargs["SecurityGroupIds"] == ["sg-redis"]
    assert create_cluster.call_args.kwargs["Engine"] == "redis"


async def test_destroy_removes_cluster_subnet_group_and_redis_sg() -> None:
    from datetime import UTC, datetime

    from engine.provisioners.state import InfraState

    ec = MagicMock()
    ec.describe_cache_clusters.return_value = {
        "CacheClusters": [
            {"ARN": "arn:aws:elasticache:...:demo", "CacheClusterStatus": "available"}
        ]
    }
    ec.list_tags_for_resource.return_value = {
        "TagList": [{"Key": "AgentBreeder", "Value": "true"}]
    }
    ec.get_waiter.return_value = MagicMock()
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}]
    }
    clients = {"elasticache": ec, "ec2": ec2}

    state = InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="test",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "elasticache": {"cluster_id": "agentbreeder-demo", "subnet_group": "ab-demo-cache"},
            "security_groups": {"redis_sg_id": "sg-redis"},
        },
    )
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(clients)):
        await AWSProvisioner().destroy(state)

    ec.delete_cache_cluster.assert_called_once_with(CacheClusterId="agentbreeder-demo")
    ec.delete_cache_subnet_group.assert_called_once_with(CacheSubnetGroupName="ab-demo-cache")
    ec2.delete_security_group.assert_called_once()
