"""Deployer registry.

Maps cloud types to their deployer implementations.
"""

from __future__ import annotations

from engine.config_parser import CloudType
from engine.deployers.base import BaseDeployer
from engine.deployers.docker_compose import DockerComposeDeployer
from engine.deployers.gcp_cloudrun import GCPCloudRunDeployer

DEPLOYERS: dict[CloudType, type[BaseDeployer]] = {
    CloudType.local: DockerComposeDeployer,
    CloudType.kubernetes: DockerComposeDeployer,  # local K8s uses Docker Compose for M1
    CloudType.gcp: GCPCloudRunDeployer,
}

# Maps runtime strings (from deploy.runtime) to deployer classes.
# When deploy.cloud is "gcp" and deploy.runtime is set, this allows
# selecting a specific GCP deployer (Cloud Run vs GKE vs Cloud Functions).
RUNTIME_DEPLOYERS: dict[str, type[BaseDeployer]] = {
    "cloud-run": GCPCloudRunDeployer,
    "cloudrun": GCPCloudRunDeployer,
}


def get_deployer(cloud: CloudType, runtime: str | None = None) -> BaseDeployer:
    """Get the deployer for a given cloud target and optional runtime.

    If runtime is specified and matches a known deployer, use that.
    Otherwise fall back to the default deployer for the cloud type.
    Raises KeyError if the cloud target is not yet supported.
    """
    # Check runtime-specific deployer first
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
