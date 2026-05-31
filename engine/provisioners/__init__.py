"""Cloud infrastructure provisioners.

Each cloud target has a provisioner that:
- validates that user-supplied existing infrastructure resources exist (BYO mode)
- (later) provisions a greenfield environment end-to-end (#382-#384)
- (later) tears down provisioned resources

Per CLAUDE.md cloud-agnostic rule: cloud-specific SDK calls stay inside
this package. Routes and CLI talk to the abstract InfraProvisioner ABC.

Cloud SDKs (boto3 / google-cloud-* / azure-mgmt-*) are installed via the
optional dependency groups ``aws``, ``gcp``, ``azure``, or ``all-clouds``
in pyproject.toml. Provisioner classes are imported lazily by
:func:`provisioner_for` so callers without a given SDK installed can still
import this package and use the requirements API.
"""

from __future__ import annotations

import importlib

from engine.provisioners.base import (
    DataBackendRequest,
    InfraProvisioner,
    InfraValidationInput,
    ValidationCheck,
    ValidationResult,
)
from engine.provisioners.requirements import (
    CloudField,
    CloudMode,
    CloudName,
    CloudRequirements,
    get_requirements,
)
from engine.provisioners.state import InfraState

_PROVISIONER_MODULES: dict[CloudName, tuple[str, str]] = {
    "aws": ("engine.provisioners.aws", "AWSProvisioner"),
    "gcp": ("engine.provisioners.gcp", "GCPProvisioner"),
    "azure": ("engine.provisioners.azure", "AzureProvisioner"),
}


def provisioner_for(cloud: CloudName) -> InfraProvisioner:
    """Return a fresh provisioner instance for the given cloud.

    Lazily imports the cloud-specific module so the matching cloud SDK is
    only loaded when actually used. Raises ValueError for unknown clouds
    and ImportError (with a clear extras hint) when the SDK is missing.
    """
    if cloud not in _PROVISIONER_MODULES:
        raise ValueError(f"No provisioner registered for cloud={cloud!r}")
    module_path, class_name = _PROVISIONER_MODULES[cloud]
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Cannot load {cloud!r} provisioner: missing SDK. "
            f"Install with: pip install 'agentbreeder[{cloud}]'  (or 'all-clouds'). "
            f"Original error: {e}"
        ) from e
    cls = getattr(module, class_name)
    return cls()


__all__ = [
    "CloudField",
    "CloudMode",
    "CloudName",
    "CloudRequirements",
    "DataBackendRequest",
    "InfraProvisioner",
    "InfraState",
    "InfraValidationInput",
    "ValidationCheck",
    "ValidationResult",
    "get_requirements",
    "provisioner_for",
]
