"""Deployer registry.

Maps cloud types to their deployer implementations.
"""

from __future__ import annotations

from engine.config_parser import CloudType
from engine.deployers.aws_app_runner import AWSAppRunnerDeployer
from engine.deployers.aws_ecs import AWSECSDeployer
from engine.deployers.azure_container_apps import AzureContainerAppsDeployer
from engine.deployers.base import BaseDeployer
from engine.deployers.claude_managed import ClaudeManagedDeployer
from engine.deployers.docker_compose import DockerComposeDeployer
from engine.deployers.gcp_cloudrun import GCPCloudRunDeployer
from engine.deployers.kubernetes import KubernetesDeployer

DEPLOYERS: dict[CloudType, type[BaseDeployer]] = {
    CloudType.local: DockerComposeDeployer,
    CloudType.aws: AWSECSDeployer,
    CloudType.azure: AzureContainerAppsDeployer,
    CloudType.gcp: GCPCloudRunDeployer,
    CloudType.kubernetes: KubernetesDeployer,
    CloudType.claude_managed: ClaudeManagedDeployer,
}

# Maps runtime strings (from deploy.runtime) to deployer classes.
RUNTIME_DEPLOYERS: dict[str, type[BaseDeployer]] = {
    "cloud-run": GCPCloudRunDeployer,
    "cloudrun": GCPCloudRunDeployer,
    "ecs-fargate": AWSECSDeployer,
    "ecs": AWSECSDeployer,
    "app-runner": AWSAppRunnerDeployer,
    "apprunner": AWSAppRunnerDeployer,
    "container-apps": AzureContainerAppsDeployer,
    "eks": KubernetesDeployer,
    "gke": KubernetesDeployer,
    "aks": KubernetesDeployer,
}


def get_deployer(cloud: CloudType, runtime: str | None = None) -> BaseDeployer:
    """Get the deployer for a given cloud target and optional runtime.

    **Precedence rule:** ``RUNTIME_DEPLOYERS`` takes precedence over ``DEPLOYERS``.
    If ``runtime`` is provided and matches a known runtime alias (case-insensitive,
    whitespace-trimmed), the runtime-specific deployer is returned regardless of
    ``cloud``. This lets an operator opt into, e.g., AWS App Runner even when the
    agent's ``deploy.cloud`` is ``aws`` (whose default is ECS Fargate).

    If ``runtime`` is ``None`` or does not match any key in ``RUNTIME_DEPLOYERS``,
    the lookup falls back to the cloud-default deployer in ``DEPLOYERS``.

    Args:
        cloud: The cloud target from ``agent.yaml`` ``deploy.cloud``.
        runtime: Optional override from ``deploy.runtime`` — e.g. ``app-runner``
            to deploy to AWS App Runner instead of the AWS default (ECS Fargate).

    Raises:
        KeyError: If ``cloud`` is not yet supported and no matching runtime
            override is provided.
    """
    # Check runtime-specific deployer first (RUNTIME_DEPLOYERS > DEPLOYERS)
    if runtime:
        runtime_key = runtime.lower().strip()
        deployer_cls = RUNTIME_DEPLOYERS.get(runtime_key)
        if deployer_cls is not None:
            return deployer_cls()

    deployer_cls = DEPLOYERS.get(cloud)
    if deployer_cls is None:
        supported = ", ".join(d.value for d in DEPLOYERS)
        msg = (
            f"Cloud target '{cloud.value}' is not yet supported. "
            f"Supported targets: {supported}. "
            f"See CONTRIBUTING.md for how to add a new deployer."
        )
        raise KeyError(msg)
    return deployer_cls()
