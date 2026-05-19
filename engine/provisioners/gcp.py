"""GCP infrastructure validator (google-cloud SDKs, read-only)."""

from __future__ import annotations

import logging
from typing import Any

from engine.provisioners.base import (
    InfraProvisioner,
    InfraValidationInput,
    ValidationCheck,
    ValidationResult,
)

logger = logging.getLogger(__name__)


def _check(resource: str, fn) -> ValidationCheck:  # noqa: ANN001
    """Run an SDK lookup, translating google-cloud exceptions into a ValidationCheck."""
    try:
        from google.api_core.exceptions import Forbidden, GoogleAPIError, NotFound
    except ImportError as e:
        return ValidationCheck(
            resource=resource, status="error", detail=f"google-cloud not installed: {e}"
        )

    try:
        return ValidationCheck(resource=resource, status="found", detail=str(fn()))
    except NotFound as e:
        return ValidationCheck(resource=resource, status="missing", detail=str(e)[:200])
    except Forbidden as e:
        return ValidationCheck(resource=resource, status="forbidden", detail=str(e)[:200])
    except GoogleAPIError as e:
        logger.warning("GCP lookup failed: resource=%s err=%s", resource, e)
        return ValidationCheck(resource=resource, status="error", detail=type(e).__name__)


def _credentials(fields: dict[str, Any]):  # noqa: ANN201
    """Resolve google-auth credentials. Falls back to ADC if no path supplied."""
    from google.auth import default, load_credentials_from_file

    if path := fields.get("GOOGLE_APPLICATION_CREDENTIALS"):
        creds, _ = load_credentials_from_file(path)
        return creds
    creds, _ = default()
    return creds


class GCPProvisioner(InfraProvisioner):
    """Validates user-supplied GCP resources via read-only google-cloud calls."""

    async def validate_existing(self, payload: InfraValidationInput) -> ValidationResult:
        fields = payload.fields
        region = payload.region
        checks: list[ValidationCheck] = []

        project = str(fields.get("GOOGLE_CLOUD_PROJECT", "")).strip()
        if not project:
            checks.append(
                ValidationCheck(
                    resource="GOOGLE_CLOUD_PROJECT",
                    status="missing",
                    detail="required field is empty",
                )
            )
            return ValidationResult(valid=False, cloud="gcp", region=region, checks=checks)

        # 1. Credentials + project must resolve.
        def check_project() -> str:
            from google.cloud import resourcemanager_v3

            creds = _credentials(fields)
            client = resourcemanager_v3.ProjectsClient(credentials=creds)
            proj = client.get_project(name=f"projects/{project}")
            return proj.state.name

        checks.append(_check(f"project:{project}", check_project))

        if checks[-1].status != "found":
            return ValidationResult(valid=False, cloud="gcp", region=region, checks=checks)

        # 2. Full-mode resource checks.
        if payload.mode == "full":
            if repo := fields.get("GCP_ARTIFACT_REGISTRY_REPO"):

                def check_repo() -> str:
                    from google.cloud import artifactregistry_v1

                    creds = _credentials(fields)
                    client = artifactregistry_v1.ArtifactRegistryClient(credentials=creds)
                    parent = f"projects/{project}/locations/{region}/repositories/{repo}"
                    result = client.get_repository(name=parent)
                    return result.format_.name

                checks.append(_check(f"artifact-registry:{repo}", check_repo))

            if sa := fields.get("GCP_CLOUD_RUN_SERVICE_ACCOUNT"):

                def check_sa() -> str:
                    from google.cloud import iam_admin_v1

                    creds = _credentials(fields)
                    client = iam_admin_v1.IAMClient(credentials=creds)
                    name = f"projects/{project}/serviceAccounts/{sa}"
                    result = client.get_service_account(name=name)
                    return result.email

                checks.append(_check(f"service-account:{sa}", check_sa))

        valid = all(c.status == "found" for c in checks)
        return ValidationResult(valid=valid, cloud="gcp", region=region, checks=checks)
