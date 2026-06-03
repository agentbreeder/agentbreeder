"""ECS public-IP endpoint resolution + multi-user docker socket (2026-05-31).

Real-cloud validation showed the ECS deployer returned a placeholder
``.ecs.local`` URL (so the pipeline health check tore the service back down)
and that ``docker.from_env()`` broke on a shared host whose
``/var/run/docker.sock`` symlinks to another user. These lock the fixes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")

from engine.deployers.aws_ecs import AWSECSDeployer, _docker_client  # noqa: E402


def _deployer_with_clients(ecs: MagicMock, ec2: MagicMock) -> AWSECSDeployer:
    d = AWSECSDeployer()
    d._aws_config = SimpleNamespace(ecs_cluster="agentbreeder-validation")  # type: ignore[attr-defined]

    def _client(service: str) -> MagicMock:
        return {"ecs": ecs, "ec2": ec2}[service]

    d._get_boto3_client = _client  # type: ignore[assignment]
    return d


async def test_resolve_task_endpoint_returns_public_ip() -> None:
    ecs = MagicMock()
    ecs.list_tasks.return_value = {"taskArns": ["arn:task/abc"]}
    ecs.describe_tasks.return_value = {
        "tasks": [
            {"attachments": [{"details": [{"name": "networkInterfaceId", "value": "eni-123"}]}]}
        ]
    }
    ec2 = MagicMock()
    ec2.describe_network_interfaces.return_value = {
        "NetworkInterfaces": [{"Association": {"PublicIp": "203.0.113.7"}}]
    }
    d = _deployer_with_clients(ecs, ec2)

    url = await d._resolve_task_endpoint(SimpleNamespace(name="nvidia-support-agent"))
    assert url == "http://203.0.113.7:8080"
    ec2.describe_network_interfaces.assert_called_once_with(NetworkInterfaceIds=["eni-123"])


async def test_resolve_task_endpoint_none_when_no_task() -> None:
    ecs = MagicMock()
    ecs.list_tasks.return_value = {"taskArns": []}
    d = _deployer_with_clients(ecs, MagicMock())
    url = await d._resolve_task_endpoint(SimpleNamespace(name="x"))
    assert url is None


async def test_resolve_task_endpoint_none_when_no_public_ip() -> None:
    ecs = MagicMock()
    ecs.list_tasks.return_value = {"taskArns": ["arn:task/abc"]}
    ecs.describe_tasks.return_value = {
        "tasks": [
            {"attachments": [{"details": [{"name": "networkInterfaceId", "value": "eni-9"}]}]}
        ]
    }
    ec2 = MagicMock()
    ec2.describe_network_interfaces.return_value = {"NetworkInterfaces": [{"Association": {}}]}
    d = _deployer_with_clients(ecs, ec2)
    url = await d._resolve_task_endpoint(SimpleNamespace(name="x"))
    assert url is None


def test_docker_client_honors_docker_host() -> None:
    with (
        patch.dict("os.environ", {"DOCKER_HOST": "unix:///tmp/x.sock"}),
        patch("docker.from_env") as from_env,
    ):
        _docker_client()
        from_env.assert_called_once()
