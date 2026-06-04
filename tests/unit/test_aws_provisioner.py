"""#383 — AWS greenfield provisioner unit tests.

boto3 SDK calls are patched at the ``_client`` boundary so the suite runs
without real AWS credentials. Tests focus on the security invariants
documented in the issue checklist: RDS privacy, DB SG ingress source,
encryption, password handling, idempotency, and tag-gated destroy.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")
pytest.importorskip("botocore")

from botocore.exceptions import ClientError  # noqa: E402

from engine.provisioners import InfraState, InfraValidationInput  # noqa: E402
from engine.provisioners.aws import (  # noqa: E402
    AWSProvisioner,
    _has_agentbreeder_tag,
    _tags,
)

# -------------------------------------------------------------------- helpers


def _client_factory(builders: dict[str, MagicMock]) -> Any:
    """Build a fake `_client(service, region, fields)` from a service map."""

    def _fn(service: str, region: str, fields: dict[str, Any]) -> MagicMock:
        if service not in builders:
            raise KeyError(f"unexpected boto3 client requested: {service}")
        return builders[service]

    return _fn


def _make_ec2() -> MagicMock:
    ec2 = MagicMock()
    # Empty lookups → forces create path.
    ec2.describe_vpcs.return_value = {"Vpcs": []}
    ec2.describe_subnets.return_value = {"Subnets": []}
    ec2.describe_internet_gateways.return_value = {"InternetGateways": []}
    ec2.describe_nat_gateways.return_value = {"NatGateways": []}
    ec2.describe_route_tables.return_value = {"RouteTables": []}
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.describe_availability_zones.return_value = {
        "AvailabilityZones": [
            {"ZoneName": "us-east-1a"},
            {"ZoneName": "us-east-1b"},
        ]
    }
    ec2.create_vpc.return_value = {"Vpc": {"VpcId": "vpc-123"}}
    # Each create_subnet returns a unique id based on call count.
    ec2.create_subnet.side_effect = [{"Subnet": {"SubnetId": f"subnet-{i}"}} for i in range(10)]
    ec2.create_internet_gateway.return_value = {"InternetGateway": {"InternetGatewayId": "igw-1"}}
    ec2.allocate_address.return_value = {"AllocationId": "eipalloc-1"}
    ec2.create_nat_gateway.return_value = {"NatGateway": {"NatGatewayId": "nat-1"}}
    ec2.create_route_table.side_effect = [
        {"RouteTable": {"RouteTableId": f"rtb-{i}"}} for i in range(5)
    ]
    ec2.create_security_group.side_effect = [
        {"GroupId": "sg-alb"},
        {"GroupId": "sg-agent"},
        {"GroupId": "sg-db"},
    ]
    return ec2


def _make_ecs() -> MagicMock:
    ecs = MagicMock()
    ecs.describe_clusters.return_value = {"clusters": []}
    ecs.create_cluster.return_value = {
        "cluster": {"clusterArn": "arn:aws:ecs:us-east-1:1:cluster/agentbreeder-demo"}
    }
    return ecs


def _make_iam() -> MagicMock:
    iam = MagicMock()
    not_found = ClientError({"Error": {"Code": "NoSuchEntity", "Message": "x"}}, "GetRole")
    iam.get_role.side_effect = not_found
    iam.create_role.return_value = {
        "Role": {"Arn": "arn:aws:iam::1:role/agentbreeder-execution-demo"}
    }
    return iam


def _make_rds() -> MagicMock:
    rds = MagicMock()
    rds.describe_db_subnet_groups.side_effect = ClientError(
        {"Error": {"Code": "DBSubnetGroupNotFoundFault", "Message": "x"}},
        "DescribeDBSubnetGroups",
    )
    rds.describe_db_instances.side_effect = [
        # First call (existence check): not found.
        ClientError(
            {"Error": {"Code": "DBInstanceNotFound", "Message": "x"}},
            "DescribeDBInstances",
        ),
        # Second call (post-create endpoint lookup).
        {"DBInstances": [{"Endpoint": {"Address": "agentbreeder-demo.abc.rds.amazonaws.com"}}]},
    ]
    rds.get_waiter.return_value = MagicMock()
    return rds


def _make_secrets() -> MagicMock:
    sm = MagicMock()
    sm.create_secret.return_value = {
        "ARN": "arn:aws:secretsmanager:us-east-1:1:secret:agentbreeder/demo/db-password"
    }
    return sm


def _make_elbv2() -> MagicMock:
    elbv2 = MagicMock()
    not_found_alb = ClientError(
        {"Error": {"Code": "LoadBalancerNotFound", "Message": "x"}},
        "DescribeLoadBalancers",
    )
    elbv2.describe_load_balancers.side_effect = not_found_alb
    elbv2.create_load_balancer.return_value = {
        "LoadBalancers": [
            {
                "LoadBalancerArn": "arn:aws:elasticloadbalancing:us-east-1:1:loadbalancer/app/agentbreeder-demo/abc",
                "DNSName": "agentbreeder-demo-abc.elb.amazonaws.com",
            }
        ]
    }
    elbv2.describe_target_groups.side_effect = ClientError(
        {"Error": {"Code": "TargetGroupNotFound", "Message": "x"}},
        "DescribeTargetGroups",
    )
    elbv2.create_target_group.return_value = {
        "TargetGroups": [
            {
                "TargetGroupArn": "arn:aws:elasticloadbalancing:us-east-1:1:targetgroup/agentbreeder-demo-tg/abc"
            }
        ]
    }
    elbv2.describe_listeners.return_value = {"Listeners": []}
    elbv2.create_listener.return_value = {
        "Listeners": [{"ListenerArn": "arn:aws:elb-listener/abc"}]
    }
    return elbv2


def _payload(**fields: Any) -> InfraValidationInput:
    base = {
        "AWS_AGENT_NAME": "demo",
        "AWS_AGENT_VERSION": "1.0.0",
    }
    base.update(fields)
    return InfraValidationInput(cloud="aws", region="us-east-1", mode="simple", fields=base)


# -- _tags / _has_agentbreeder_tag -----------------------------------------


def test_tags_emits_canonical_triple() -> None:
    tags = _tags("demo", "1.0.0")
    keys = {t["Key"]: t["Value"] for t in tags}
    assert keys == {"AgentBreeder": "true", "AgentName": "demo", "Version": "1.0.0"}


def test_tags_merges_extras() -> None:
    tags = _tags("demo", "1.0.0", extra={"Kind": "public"})
    keys = {t["Key"]: t["Value"] for t in tags}
    assert keys["Kind"] == "public"


def test_has_agentbreeder_tag_true_when_present() -> None:
    assert _has_agentbreeder_tag([{"Key": "AgentBreeder", "Value": "true"}])


def test_has_agentbreeder_tag_handles_lowercase_keys() -> None:
    # ECS / RDS sometimes use lowercase {key, value}.
    assert _has_agentbreeder_tag([{"key": "AgentBreeder", "value": "true"}])


def test_has_agentbreeder_tag_false_when_missing() -> None:
    assert not _has_agentbreeder_tag([{"Key": "Other", "Value": "true"}])
    assert not _has_agentbreeder_tag([])
    assert not _has_agentbreeder_tag(None)


# -- provision() — minimal (no memory, no public) ---------------------------


@pytest.fixture
def fake_clients_minimal():
    """ec2 + ecs + iam only — used when memory is not declared and not public."""
    builders = {
        "ec2": _make_ec2(),
        "ecs": _make_ecs(),
        "iam": _make_iam(),
    }
    return builders


@pytest.mark.asyncio
async def test_provision_minimal_returns_expected_state(fake_clients_minimal) -> None:
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        state = await AWSProvisioner().provision(_payload())

    assert isinstance(state, InfraState)
    assert state.cloud == "aws"
    assert state.region == "us-east-1"
    assert state.mode == "provisioned"

    # Core resources present.
    assert state.resources["vpc"]["cidr"] == "10.0.0.0/16"
    assert len(state.resources["network"]["public_subnet_ids"]) == 2
    assert len(state.resources["network"]["private_subnet_ids"]) == 2
    # Single NAT by default — cost-conscious.
    assert len(state.resources["network"]["nat_gateway_ids"]) == 1
    # Security groups present, db_sg_id None because no memory.
    assert state.resources["security_groups"]["alb_sg_id"]
    assert state.resources["security_groups"]["agent_sg_id"]
    assert state.resources["security_groups"]["db_sg_id"] is None
    # No RDS, no ALB.
    assert "rds" not in state.resources
    assert "alb" not in state.resources


@pytest.mark.asyncio
async def test_provision_calls_progress_callback(fake_clients_minimal) -> None:
    messages: list[str] = []

    async def _cb(msg: str) -> None:
        messages.append(msg)

    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload(), progress=_cb)

    assert any("VPC" in m for m in messages)
    assert any("ECS cluster" in m for m in messages)
    assert any("provision complete" in m for m in messages)


@pytest.mark.asyncio
async def test_provision_enables_dns_hostnames(fake_clients_minimal) -> None:
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload())

    ec2 = fake_clients_minimal["ec2"]
    hostnames_calls = [
        c
        for c in ec2.modify_vpc_attribute.call_args_list
        if c.kwargs.get("EnableDnsHostnames") == {"Value": True}
    ]
    support_calls = [
        c
        for c in ec2.modify_vpc_attribute.call_args_list
        if c.kwargs.get("EnableDnsSupport") == {"Value": True}
    ]
    assert hostnames_calls
    assert support_calls


@pytest.mark.asyncio
async def test_provision_creates_ecs_with_fargate_and_spot(
    fake_clients_minimal,
) -> None:
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload())

    ecs = fake_clients_minimal["ecs"]
    ecs.create_cluster.assert_called_once()
    kwargs = ecs.create_cluster.call_args.kwargs
    assert set(kwargs["capacityProviders"]) == {"FARGATE", "FARGATE_SPOT"}
    assert kwargs["clusterName"] == "agentbreeder-demo"


@pytest.mark.asyncio
async def test_provision_attaches_execution_role_policy(
    fake_clients_minimal,
) -> None:
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload())

    iam = fake_clients_minimal["iam"]
    iam.create_role.assert_called_once()
    iam.attach_role_policy.assert_called_with(
        RoleName="agentbreeder-execution-demo",
        PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
    )


@pytest.mark.asyncio
async def test_provision_multi_az_nat_when_env_set(fake_clients_minimal) -> None:
    fake_clients_minimal["ec2"].create_nat_gateway.side_effect = [
        {"NatGateway": {"NatGatewayId": "nat-1"}},
        {"NatGateway": {"NatGatewayId": "nat-2"}},
    ]
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        state = await AWSProvisioner().provision(_payload(AWS_MULTI_AZ_NAT="1"))
    assert len(state.resources["network"]["nat_gateway_ids"]) == 2


# -- Security group ingress sources ----------------------------------------


@pytest.mark.asyncio
async def test_db_sg_only_ingress_is_from_agent_sg_not_internet(
    fake_clients_minimal,
) -> None:
    """The DB SG must NEVER accept 0.0.0.0/0 on 5432 — only the agent SG."""
    fake_clients_minimal["rds"] = _make_rds()
    fake_clients_minimal["secretsmanager"] = _make_secrets()
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload(AWS_HAS_MEMORY="1"))

    ec2 = fake_clients_minimal["ec2"]
    db_ingress_calls = [
        c
        for c in ec2.authorize_security_group_ingress.call_args_list
        if c.kwargs.get("GroupId") == "sg-db"
    ]
    # There should be exactly one ingress rule on the DB SG.
    assert len(db_ingress_calls) == 1
    perms = db_ingress_calls[0].kwargs["IpPermissions"][0]
    assert perms["FromPort"] == 5432
    assert perms["ToPort"] == 5432
    assert "IpRanges" not in perms, "DB SG must NOT allow CIDR ingress"
    assert perms["UserIdGroupPairs"][0]["GroupId"] == "sg-agent"


@pytest.mark.asyncio
async def test_alb_sg_accepts_internet_traffic_on_80_443(
    fake_clients_minimal,
) -> None:
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload())

    ec2 = fake_clients_minimal["ec2"]
    alb_calls = [
        c
        for c in ec2.authorize_security_group_ingress.call_args_list
        if c.kwargs.get("GroupId") == "sg-alb"
    ]
    ports = {c.kwargs["IpPermissions"][0]["FromPort"] for c in alb_calls}
    assert ports == {80, 443}
    for c in alb_calls:
        assert c.kwargs["IpPermissions"][0]["IpRanges"] == [{"CidrIp": "0.0.0.0/0"}]


@pytest.mark.asyncio
async def test_agent_sg_has_no_internet_ingress_by_default(fake_clients_minimal) -> None:
    """Default greenfield: the agent SG accepts 8080 from the ALB SG only, never
    from the internet. Preserves the ALB-fronted architecture."""
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload())

    ec2 = fake_clients_minimal["ec2"]
    agent_calls = [
        c
        for c in ec2.authorize_security_group_ingress.call_args_list
        if c.kwargs.get("GroupId") == "sg-agent"
    ]
    # Exactly one rule, sourced from the ALB SG — no CIDR ingress.
    assert len(agent_calls) == 1
    perms = agent_calls[0].kwargs["IpPermissions"][0]
    assert perms["UserIdGroupPairs"][0]["GroupId"] == "sg-alb"
    assert "IpRanges" not in perms


@pytest.mark.asyncio
async def test_agent_sg_opens_8080_to_internet_when_public_ingress_requested(
    fake_clients_minimal,
) -> None:
    """With AWS_AGENT_PUBLIC_INGRESS set, the agent SG also accepts 8080 from
    0.0.0.0/0 so the ECS deployer's public-IP task (assignPublicIp=ENABLED) is
    reachable without an ALB. The runtime gates /invoke with a bearer token."""
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload(AWS_AGENT_PUBLIC_INGRESS="true"))

    ec2 = fake_clients_minimal["ec2"]
    agent_cidr_calls = [
        c
        for c in ec2.authorize_security_group_ingress.call_args_list
        if c.kwargs.get("GroupId") == "sg-agent"
        and c.kwargs["IpPermissions"][0].get("IpRanges") == [{"CidrIp": "0.0.0.0/0"}]
    ]
    assert len(agent_cidr_calls) == 1
    assert agent_cidr_calls[0].kwargs["IpPermissions"][0]["FromPort"] == 8080


@pytest.mark.asyncio
async def test_provision_waits_for_nat_available_before_routing(fake_clients_minimal) -> None:
    """A new NAT gateway starts 'pending' and cannot be a route target until
    'available'. provision() must wait, else CreateRoute fails with
    InvalidNatGatewayID.NotFound (caught in #537 live validation)."""
    ec2 = fake_clients_minimal["ec2"]
    waiter = MagicMock()
    ec2.get_waiter.return_value = waiter

    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload())

    ec2.get_waiter.assert_any_call("nat_gateway_available")
    waited_ids = [c.kwargs.get("NatGatewayIds") for c in waiter.wait.call_args_list]
    assert ["nat-1"] in waited_ids


# -- RDS security invariants -----------------------------------------------


@pytest.mark.asyncio
async def test_rds_private_encrypted_not_public(fake_clients_minimal) -> None:
    fake_clients_minimal["rds"] = _make_rds()
    fake_clients_minimal["secretsmanager"] = _make_secrets()
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        state = await AWSProvisioner().provision(_payload(AWS_HAS_MEMORY="1"))

    rds = fake_clients_minimal["rds"]
    create_kwargs = rds.create_db_instance.call_args.kwargs
    assert create_kwargs["PubliclyAccessible"] is False
    assert create_kwargs["StorageEncrypted"] is True
    assert create_kwargs["VpcSecurityGroupIds"] == ["sg-db"]
    assert create_kwargs["DBName"] == "agentbreeder_memory"

    # The state must say so too, and must not contain plaintext.
    assert state.resources["rds"]["publicly_accessible"] is False
    assert state.resources["rds"]["storage_encrypted"] is True
    assert state.resources["rds"]["secret_arn"].startswith("arn:aws:secretsmanager:")


@pytest.mark.asyncio
async def test_rds_password_never_appears_in_state_json(
    fake_clients_minimal,
) -> None:
    """Hard regex assertion: no plaintext-looking secret in the state JSON."""
    fake_clients_minimal["rds"] = _make_rds()
    fake_clients_minimal["secretsmanager"] = _make_secrets()
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        state = await AWSProvisioner().provision(_payload(AWS_HAS_MEMORY="1"))

    rds = fake_clients_minimal["rds"]
    pw_passed_to_aws = rds.create_db_instance.call_args.kwargs["MasterUserPassword"]
    # Sanity-check the password is actually long + token_urlsafe-shaped.
    assert len(pw_passed_to_aws) >= 32
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", pw_passed_to_aws)

    raw = state.model_dump_json()
    assert pw_passed_to_aws not in raw
    # Belt-and-suspenders: no top-level "password" key in the dumped JSON.
    assert '"password"' not in raw.lower()


@pytest.mark.asyncio
async def test_rds_rolled_back_if_secrets_write_fails(
    fake_clients_minimal,
) -> None:
    fake_clients_minimal["rds"] = _make_rds()
    sm = _make_secrets()
    sm.create_secret.side_effect = ClientError(
        {"Error": {"Code": "InternalFailure", "Message": "x"}}, "CreateSecret"
    )
    fake_clients_minimal["secretsmanager"] = sm

    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        with pytest.raises(ClientError):
            await AWSProvisioner().provision(_payload(AWS_HAS_MEMORY="1"))

    fake_clients_minimal["rds"].delete_db_instance.assert_called_once()
    kw = fake_clients_minimal["rds"].delete_db_instance.call_args.kwargs
    assert kw["SkipFinalSnapshot"] is True  # rollback skips snapshot


# -- ALB (TLS policy) ------------------------------------------------------


@pytest.mark.asyncio
async def test_alb_listener_https_uses_tls12_or_higher_policy(
    fake_clients_minimal,
) -> None:
    fake_clients_minimal["elbv2"] = _make_elbv2()
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        state = await AWSProvisioner().provision(
            _payload(
                AWS_ACCESS_VISIBILITY="public",
                AWS_ACM_CERTIFICATE_ARN="arn:aws:acm:us-east-1:1:certificate/abc",
            )
        )

    elbv2 = fake_clients_minimal["elbv2"]
    elbv2.create_listener.assert_called_once()
    listener_kwargs = elbv2.create_listener.call_args.kwargs
    assert listener_kwargs["Protocol"] == "HTTPS"
    assert listener_kwargs["SslPolicy"] == "ELBSecurityPolicy-TLS13-1-2-2021-06"
    assert state.resources["alb"]["ssl_policy"] == "ELBSecurityPolicy-TLS13-1-2-2021-06"


@pytest.mark.asyncio
async def test_alb_listener_falls_back_to_http_without_cert(
    fake_clients_minimal,
) -> None:
    fake_clients_minimal["elbv2"] = _make_elbv2()
    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        await AWSProvisioner().provision(_payload(AWS_ACCESS_VISIBILITY="public"))

    listener_kwargs = fake_clients_minimal["elbv2"].create_listener.call_args.kwargs
    assert listener_kwargs["Protocol"] == "HTTP"


# -- Idempotency -----------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_idempotent_same_state(fake_clients_minimal) -> None:
    """Re-running provision with existing-resource lookups returns the same IDs."""
    ec2 = fake_clients_minimal["ec2"]
    # Pretend everything already exists with deterministic IDs.
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-existing"}]}
    ec2.describe_subnets.side_effect = lambda **kw: {
        "Subnets": [
            {
                "SubnetId": f"subnet-existing-{kw.get('Filters', [{}, {}])[1].get('Values', [''])[0]}"
            }
        ]
    }
    ec2.describe_internet_gateways.return_value = {
        "InternetGateways": [{"InternetGatewayId": "igw-existing"}]
    }
    ec2.describe_nat_gateways.return_value = {"NatGateways": [{"NatGatewayId": "nat-existing"}]}
    ec2.describe_route_tables.side_effect = [
        # public_rt lookup
        {"RouteTables": [{"RouteTableId": "rtb-pub"}]},
        # association lookup for public subnet 1
        {"RouteTables": []},
        # association lookup for public subnet 2
        {"RouteTables": []},
        # private rt 0 lookup
        {"RouteTables": [{"RouteTableId": "rtb-priv-0"}]},
        # association lookup
        {"RouteTables": []},
        # private rt 1 lookup
        {"RouteTables": [{"RouteTableId": "rtb-priv-1"}]},
        # association lookup
        {"RouteTables": []},
    ]
    ec2.describe_security_groups.side_effect = [
        {"SecurityGroups": [{"GroupId": "sg-alb-existing"}]},
        {"SecurityGroups": [{"GroupId": "sg-agent-existing"}]},
    ]
    ecs = fake_clients_minimal["ecs"]
    ecs.describe_clusters.return_value = {
        "clusters": [{"clusterArn": "arn:aws:ecs::cluster/agentbreeder-demo", "status": "ACTIVE"}]
    }
    iam = fake_clients_minimal["iam"]
    iam.get_role.side_effect = None
    iam.get_role.return_value = {
        "Role": {"Arn": "arn:aws:iam::1:role/agentbreeder-execution-demo"}
    }

    with patch(
        "engine.provisioners.aws._client", side_effect=_client_factory(fake_clients_minimal)
    ):
        state1 = await AWSProvisioner().provision(_payload())

    assert state1.resources["vpc"]["vpc_id"] == "vpc-existing"
    assert state1.resources["ecs_cluster"]["arn"].endswith("cluster/agentbreeder-demo")
    # create_vpc / create_cluster / create_role should NOT have been called.
    ec2.create_vpc.assert_not_called()
    ecs.create_cluster.assert_not_called()
    iam.create_role.assert_not_called()


# -- Destroy ---------------------------------------------------------------


def _provisioned_state(*, with_rds: bool = False, with_alb: bool = False) -> InfraState:
    resources: dict[str, Any] = {
        "vpc": {"vpc_id": "vpc-1", "cidr": "10.0.0.0/16"},
        "network": {
            "public_subnet_ids": ["subnet-pub-a", "subnet-pub-b"],
            "private_subnet_ids": ["subnet-priv-a", "subnet-priv-b"],
            "internet_gateway_id": "igw-1",
            "nat_gateway_ids": ["nat-1"],
            "public_route_table_id": "rtb-pub",
            "private_route_table_ids": ["rtb-priv-0", "rtb-priv-1"],
            "azs": ["us-east-1a", "us-east-1b"],
        },
        "security_groups": {
            "alb_sg_id": "sg-alb",
            "agent_sg_id": "sg-agent",
            "db_sg_id": "sg-db" if with_rds else None,
        },
        "ecs_cluster": {
            "name": "agentbreeder-demo",
            "arn": "arn:aws:ecs::cluster/agentbreeder-demo",
        },
        "iam_execution_role": {
            "name": "agentbreeder-execution-demo",
            "arn": "arn:aws:iam::1:role/agentbreeder-execution-demo",
        },
    }
    if with_rds:
        resources["rds"] = {
            "db_instance_identifier": "agentbreeder-demo",
            "secret_arn": "arn:aws:secretsmanager:us-east-1:1:secret:agentbreeder/demo/db-password",
            "publicly_accessible": False,
            "storage_encrypted": True,
        }
    if with_alb:
        resources["alb"] = {
            "arn": "arn:aws:elb::loadbalancer/abc",
            "target_group_arn": "arn:aws:elb::targetgroup/abc",
            "listener_arn": "arn:aws:elb::listener/abc",
            "ssl_policy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
        }
    return InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="agentbreeder.AWSProvisioner",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources=resources,
    )


@pytest.mark.asyncio
async def test_destroy_rejects_non_aws_state() -> None:
    state = InfraState(
        cloud="gcp",
        region="us-central1",
        provisioned_by="t",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
    )
    with pytest.raises(ValueError, match="state.cloud is"):
        await AWSProvisioner().destroy(state)


@pytest.mark.asyncio
async def test_destroy_refuses_untagged_vpc() -> None:
    ec2 = MagicMock()
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-1", "Tags": []}]}
    # ECS / IAM minimal — short-circuit before the tag check is the point.
    ecs = MagicMock()
    ecs.describe_clusters.return_value = {
        "clusters": [{"clusterArn": "a", "tags": [{"key": "AgentBreeder", "value": "true"}]}]
    }
    iam = MagicMock()
    iam.get_role.return_value = {"Role": {"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}}
    iam.list_attached_role_policies.return_value = {"AttachedPolicies": []}
    builders = {"ec2": ec2, "ecs": ecs, "iam": iam}

    state = _provisioned_state()
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(builders)):
        await AWSProvisioner().destroy(state)
    # delete_vpc must NOT have been called because the VPC was untagged.
    ec2.delete_vpc.assert_not_called()


@pytest.mark.asyncio
async def test_destroy_refuses_untagged_rds() -> None:
    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{"DBInstanceArn": "arn:aws:rds:::db/agentbreeder-demo"}]
    }
    rds.list_tags_for_resource.return_value = {"TagList": []}

    ec2 = MagicMock()
    ec2.describe_vpcs.return_value = {
        "Vpcs": [{"VpcId": "vpc-1", "Tags": [{"Key": "AgentBreeder", "Value": "true"}]}]
    }
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}]
    }
    ecs = MagicMock()
    ecs.describe_clusters.return_value = {
        "clusters": [{"clusterArn": "a", "tags": [{"key": "AgentBreeder", "value": "true"}]}]
    }
    iam = MagicMock()
    iam.get_role.return_value = {"Role": {"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}}
    iam.list_attached_role_policies.return_value = {"AttachedPolicies": []}

    # destroy() always builds a secretsmanager client when RDS is present (to clean
    # up the generated DB-password secret), so the fake factory must serve it too.
    builders = {"ec2": ec2, "ecs": ecs, "iam": iam, "rds": rds, "secretsmanager": MagicMock()}
    state = _provisioned_state(with_rds=True)
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(builders)):
        # destroy() swallows individual delete exceptions; we assert via call counts.
        await AWSProvisioner().destroy(state)
    rds.delete_db_instance.assert_not_called()


@pytest.mark.asyncio
async def test_destroy_takes_final_snapshot_by_default() -> None:
    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{"DBInstanceArn": "arn:aws:rds:::db/agentbreeder-demo"}]
    }
    rds.list_tags_for_resource.return_value = {
        "TagList": [{"Key": "AgentBreeder", "Value": "true"}]
    }

    ec2 = MagicMock()
    ec2.describe_vpcs.return_value = {
        "Vpcs": [{"VpcId": "vpc-1", "Tags": [{"Key": "AgentBreeder", "Value": "true"}]}]
    }
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}]
    }
    ecs = MagicMock()
    ecs.describe_clusters.return_value = {
        "clusters": [{"clusterArn": "a", "tags": [{"key": "AgentBreeder", "value": "true"}]}]
    }
    iam = MagicMock()
    iam.get_role.return_value = {"Role": {"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}}
    iam.list_attached_role_policies.return_value = {"AttachedPolicies": []}

    # destroy() always builds a secretsmanager client when RDS is present (to clean
    # up the generated DB-password secret), so the fake factory must serve it too.
    builders = {"ec2": ec2, "ecs": ecs, "iam": iam, "rds": rds, "secretsmanager": MagicMock()}
    state = _provisioned_state(with_rds=True)
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(builders)):
        await AWSProvisioner().destroy(state)

    rds.delete_db_instance.assert_called_once()
    kw = rds.delete_db_instance.call_args.kwargs
    assert kw["SkipFinalSnapshot"] is False
    assert kw["FinalDBSnapshotIdentifier"].startswith("agentbreeder-")


@pytest.mark.asyncio
async def test_destroy_skips_snapshot_when_explicitly_requested() -> None:
    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{"DBInstanceArn": "arn:aws:rds:::db/agentbreeder-demo"}]
    }
    rds.list_tags_for_resource.return_value = {
        "TagList": [{"Key": "AgentBreeder", "Value": "true"}]
    }

    ec2 = MagicMock()
    ec2.describe_vpcs.return_value = {
        "Vpcs": [{"VpcId": "vpc-1", "Tags": [{"Key": "AgentBreeder", "Value": "true"}]}]
    }
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}]
    }
    ecs = MagicMock()
    ecs.describe_clusters.return_value = {
        "clusters": [{"clusterArn": "a", "tags": [{"key": "AgentBreeder", "value": "true"}]}]
    }
    iam = MagicMock()
    iam.get_role.return_value = {"Role": {"Tags": [{"Key": "AgentBreeder", "Value": "true"}]}}
    iam.list_attached_role_policies.return_value = {"AttachedPolicies": []}

    # destroy() always builds a secretsmanager client when RDS is present (to clean
    # up the generated DB-password secret), so the fake factory must serve it too.
    builders = {"ec2": ec2, "ecs": ecs, "iam": iam, "rds": rds, "secretsmanager": MagicMock()}
    state = _provisioned_state(with_rds=True)
    with patch("engine.provisioners.aws._client", side_effect=_client_factory(builders)):
        await AWSProvisioner().destroy(state, no_final_snapshot=True)

    kw = rds.delete_db_instance.call_args.kwargs
    assert kw["SkipFinalSnapshot"] is True


# -- Doesn't log creds -----------------------------------------------------


def test_session_does_not_accept_inline_creds_in_kwargs() -> None:
    """Hard guarantee that _session() never passes access keys to boto3.

    Confirms the no-`aws_access_key_id=` constraint from the issue.
    """
    from engine.provisioners.aws import _session

    fields = {
        "AWS_ACCESS_KEY_ID": "AKIA-NEVER-USE",
        "AWS_SECRET_ACCESS_KEY": "SECRET-NEVER-USE",
        "AWS_PROFILE": "demo",
    }
    with patch("engine.provisioners.aws.boto3.session.Session") as factory:
        _session(fields, "us-east-1")
    kwargs = factory.call_args.kwargs
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
    assert "aws_session_token" not in kwargs
    # Profile and region are fine.
    assert kwargs["profile_name"] == "demo"
    assert kwargs["region_name"] == "us-east-1"
