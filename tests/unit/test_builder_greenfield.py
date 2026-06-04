"""DeployEngine greenfield branch — `agentbreeder deploy --provision` (#537).

Covers ``DeployEngine._maybe_provision_greenfield``: when ``--provision`` is set
and no BYO infra is supplied, it provisions the cloud footprint, maps the
returned ``InfraState`` into ``deploy.env_vars`` (so the existing deploy path
serves the agent into it), and records the footprint for teardown. AWS only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from engine.builder import DeployEngine, DeployError
from engine.config_parser import (
    AgentConfig,
    CloudType,
    DeployConfig,
    ModelConfig,
)
from engine.provisioners.state import InfraState


def _cfg(
    *, cloud: CloudType = CloudType.aws, env_vars: dict[str, str] | None = None
) -> AgentConfig:
    return AgentConfig(
        name="demo",
        version="1.0.0",
        team="t",
        owner="a@b.com",
        framework="langgraph",
        model=ModelConfig(primary="gpt-4o"),
        deploy=DeployConfig(cloud=cloud, region="us-east-1", env_vars=env_vars or {}),
    )


def _greenfield_state() -> InfraState:
    return InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="agentbreeder.AWSProvisioner",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "vpc": {"vpc_id": "vpc-0abc"},
            "network": {
                "public_subnet_ids": ["subnet-pub1", "subnet-pub2"],
                "private_subnet_ids": ["subnet-prv1"],
            },
            "security_groups": {"agent_sg_id": "sg-agent", "alb_sg_id": None, "db_sg_id": None},
            "ecs_cluster": {"name": "agentbreeder-demo", "arn": "arn:x"},
            "iam_execution_role": {"name": "r", "arn": "arn:aws:iam::1:role/r"},
        },
    )


def _fake_provisioner(state: InfraState) -> AsyncMock:
    fake = AsyncMock()
    fake.provision.return_value = state
    return fake


@pytest.mark.asyncio
async def test_greenfield_provisions_and_injects_env(tmp_path: Path) -> None:
    cfg = _cfg(env_vars={"AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "x"})
    fake = _fake_provisioner(_greenfield_state())

    with patch("engine.builder.provisioner_for", return_value=fake):
        await DeployEngine()._maybe_provision_greenfield(cfg, tmp_path, provision=True)

    fake.provision.assert_awaited_once()
    env = cfg.deploy.env_vars
    assert env["AWS_ECS_CLUSTER"] == "agentbreeder-demo"
    assert env["AWS_VPC_SUBNETS"] == "subnet-pub1,subnet-pub2"
    assert env["AWS_SECURITY_GROUPS"] == "sg-agent"
    assert env["AWS_VPC_ID"] == "vpc-0abc"
    # Footprint recorded for teardown.
    assert (tmp_path / ".agentbreeder" / "infra-state.json").exists()


@pytest.mark.asyncio
async def test_no_op_when_provision_false(tmp_path: Path) -> None:
    cfg = _cfg()
    with patch("engine.builder.provisioner_for") as pf:
        await DeployEngine()._maybe_provision_greenfield(cfg, tmp_path, provision=False)
    pf.assert_not_called()
    assert "AWS_ECS_CLUSTER" not in cfg.deploy.env_vars


@pytest.mark.asyncio
async def test_skips_when_byo_infra_present(tmp_path: Path) -> None:
    cfg = _cfg(env_vars={"AWS_ECS_CLUSTER": "mine", "AWS_VPC_SUBNETS": "subnet-mine"})
    with patch("engine.builder.provisioner_for") as pf:
        await DeployEngine()._maybe_provision_greenfield(cfg, tmp_path, provision=True)
    pf.assert_not_called()
    # User's values are untouched.
    assert cfg.deploy.env_vars["AWS_ECS_CLUSTER"] == "mine"


@pytest.mark.asyncio
async def test_rejects_non_aws_cloud(tmp_path: Path) -> None:
    cfg = _cfg(cloud=CloudType.gcp)
    with pytest.raises(DeployError, match="AWS"):
        await DeployEngine()._maybe_provision_greenfield(cfg, tmp_path, provision=True)


@pytest.mark.asyncio
async def test_reuses_existing_greenfield_state(tmp_path: Path) -> None:
    # Pre-record a greenfield footprint.
    state_dir = tmp_path / ".agentbreeder"
    state_dir.mkdir()
    _greenfield_state().save(state_dir / "infra-state.json")

    cfg = _cfg()
    fake = _fake_provisioner(_greenfield_state())
    with patch("engine.builder.provisioner_for", return_value=fake):
        await DeployEngine()._maybe_provision_greenfield(cfg, tmp_path, provision=True)

    # Existing infra reused — no re-provision — but env still injected.
    fake.provision.assert_not_awaited()
    assert cfg.deploy.env_vars["AWS_ECS_CLUSTER"] == "agentbreeder-demo"
