"""AWS infrastructure validator (boto3, read-only)."""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
)

from engine.provisioners.base import (
    InfraProvisioner,
    InfraValidationInput,
    ValidationCheck,
    ValidationResult,
)

logger = logging.getLogger(__name__)


def _client(service: str, region: str, fields: dict[str, Any]) -> Any:
    """Build a boto3 client from validation fields. Honors AWS_PROFILE if set."""
    session_kwargs: dict[str, Any] = {"region_name": region}
    if profile := fields.get("AWS_PROFILE"):
        session_kwargs["profile_name"] = profile
    if (key := fields.get("AWS_ACCESS_KEY_ID")) and (
        secret := fields.get("AWS_SECRET_ACCESS_KEY")
    ):
        session_kwargs["aws_access_key_id"] = key
        session_kwargs["aws_secret_access_key"] = secret
        if token := fields.get("AWS_SESSION_TOKEN"):
            session_kwargs["aws_session_token"] = token
    session = boto3.session.Session(**session_kwargs)
    return session.client(service)


def _check(resource: str, fn, *args, **kwargs) -> ValidationCheck:  # noqa: ANN001
    """Run an SDK lookup, translating exceptions into a ValidationCheck."""
    try:
        detail = fn(*args, **kwargs)
        return ValidationCheck(resource=resource, status="found", detail=detail)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        if code in {"AccessDenied", "UnauthorizedOperation"}:
            return ValidationCheck(resource=resource, status="forbidden", detail=code)
        if code in {
            "NoSuchEntity",
            "InvalidSubnetID.NotFound",
            "InvalidGroup.NotFound",
            "RepositoryNotFoundException",
        }:
            return ValidationCheck(resource=resource, status="missing", detail=code)
        logger.warning("AWS lookup failed: resource=%s code=%s", resource, code)
        return ValidationCheck(resource=resource, status="error", detail=code)
    except (BotoCoreError, NoCredentialsError, ProfileNotFound) as e:
        return ValidationCheck(resource=resource, status="error", detail=type(e).__name__)


class AWSProvisioner(InfraProvisioner):
    """Validates user-supplied AWS resources via read-only boto3 calls."""

    async def validate_existing(self, payload: InfraValidationInput) -> ValidationResult:
        fields = payload.fields
        region = payload.region
        checks: list[ValidationCheck] = []

        # 1. Credentials work and account matches what the user claimed.
        sts = _client("sts", region, fields)
        identity_check = _check(
            "credentials",
            lambda: sts.get_caller_identity()["Account"],
        )
        if identity_check.status == "found":
            claimed = str(fields.get("AWS_ACCOUNT_ID", "")).strip()
            if claimed and identity_check.detail != claimed:
                identity_check = ValidationCheck(
                    resource="credentials",
                    status="error",
                    detail=f"credentials resolve to account {identity_check.detail!r}, but AWS_ACCOUNT_ID={claimed!r}",
                )
            else:
                identity_check = ValidationCheck(
                    resource="credentials",
                    status="found",
                    detail=f"account {identity_check.detail}",
                )
        checks.append(identity_check)

        # Short-circuit if creds didn't work; deeper checks will all fail anyway.
        if identity_check.status != "found":
            return ValidationResult(valid=False, cloud="aws", region=region, checks=checks)

        # 2. Full-mode checks: each named resource must resolve.
        if payload.mode == "full":
            if cluster := fields.get("AWS_ECS_CLUSTER"):
                ecs = _client("ecs", region, fields)
                checks.append(
                    _check(
                        cluster,
                        lambda: ecs.describe_clusters(clusters=[cluster])["clusters"][0]["status"],
                    )
                )
            if role_arn := fields.get("AWS_EXECUTION_ROLE_ARN"):
                iam = _client("iam", region, fields)
                role_name = role_arn.split("/")[-1]
                checks.append(
                    _check(role_arn, lambda: iam.get_role(RoleName=role_name)["Role"]["Arn"])
                )
            if subnets_csv := fields.get("AWS_VPC_SUBNETS"):
                ec2 = _client("ec2", region, fields)
                subnet_ids = [s.strip() for s in subnets_csv.split(",") if s.strip()]
                for sid in subnet_ids:
                    checks.append(
                        _check(
                            sid,
                            lambda sid=sid: ec2.describe_subnets(SubnetIds=[sid])["Subnets"][0][
                                "AvailabilityZone"
                            ],
                        )
                    )
            if sgs_csv := fields.get("AWS_SECURITY_GROUPS"):
                ec2 = _client("ec2", region, fields)
                sg_ids = [s.strip() for s in sgs_csv.split(",") if s.strip()]
                for sg in sg_ids:
                    checks.append(
                        _check(
                            sg,
                            lambda sg=sg: ec2.describe_security_groups(GroupIds=[sg])[
                                "SecurityGroups"
                            ][0]["GroupName"],
                        )
                    )
            if repo := fields.get("AWS_ECR_REPOSITORY"):
                ecr = _client("ecr", region, fields)
                checks.append(
                    _check(
                        repo,
                        lambda: ecr.describe_repositories(repositoryNames=[repo])["repositories"][
                            0
                        ]["repositoryUri"],
                    )
                )

        valid = all(c.status == "found" for c in checks)
        return ValidationResult(valid=valid, cloud="aws", region=region, checks=checks)
