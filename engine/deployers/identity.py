"""Per-agent cloud identity provisioning.

Issue #72: Per-agent cloud IAM identity.

Creates a dedicated IAM Role (AWS) or Service Account (GCP) for each deployed
agent so that every agent has its own, narrowly-scoped cloud identity rather than
sharing a broad platform role.

Usage (called by the deploy pipeline after RBAC check, before container build):

    from engine.deployers.identity import IdentityConfig, provision_aws_identity

    identity_cfg = IdentityConfig(
        create=True,
        permissions=["s3:GetObject:arn:aws:s3:::my-bucket/*"],
    )
    result = provision_aws_identity(config.name, identity_cfg)
    if result.created:
        # Pass result.identity_arn to the ECS task definition
        ...

Cloud SDK imports are lazy so the module can be imported without boto3/google-auth
installed (common in local dev where neither cloud is configured).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class IdentityConfig:
    """Parsed representation of deploy.identity from agent.yaml."""

    create: bool = False
    permissions: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    boundary: Optional[str] = None


@dataclass
class ProvisionedIdentity:
    """The cloud identity created (or looked up) for an agent."""

    cloud: str
    agent_name: str
    identity_arn: Optional[str] = None  # AWS IAM Role ARN
    service_account_email: Optional[str] = None  # GCP Service Account email
    created: bool = False


def provision_aws_identity(agent_name: str, config: IdentityConfig) -> ProvisionedIdentity:
    """Provision a per-agent IAM role on AWS.

    If config.create is False this is a no-op and returns an empty identity so
    the caller can safely ignore it.  All AWS SDK calls are guarded with an
    ImportError catch so the function degrades gracefully when boto3 is absent.
    """
    if not config.create:
        logger.debug("AWS identity: create=false — skipping for agent '%s'", agent_name)
        return ProvisionedIdentity(cloud="aws", agent_name=agent_name)

    role_name = f"agentbreeder-{agent_name}-role"
    logger.info("Provisioning AWS IAM role: %s", role_name)

    try:
        import boto3  # type: ignore[import]
    except ImportError:
        logger.warning(
            "boto3 not installed — skipping AWS identity provisioning for '%s'", agent_name
        )
        return ProvisionedIdentity(cloud="aws", agent_name=agent_name)

    try:
        iam = boto3.client("iam")

        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        create_kwargs: dict = {
            "RoleName": role_name,
            "AssumeRolePolicyDocument": json.dumps(assume_role_policy),
            "Description": f"AgentBreeder per-agent role for {agent_name}",
            "Tags": [
                {"Key": "agentbreeder", "Value": "true"},
                {"Key": "agent", "Value": agent_name},
            ],
        }
        if config.boundary:
            create_kwargs["PermissionsBoundary"] = config.boundary

        response = iam.create_role(**create_kwargs)
        role_arn: str = response["Role"]["Arn"]

        # Attach inline policy if permissions were provided
        if config.permissions:
            statements = []
            for perm in config.permissions:
                # Format: "Action:Resource" where Resource may contain colons
                parts = perm.split(":", 2)
                if len(parts) == 3:
                    action = f"{parts[0]}:{parts[1]}"
                    resource = parts[2]
                elif len(parts) == 2:
                    action = perm
                    resource = "*"
                else:
                    action = perm
                    resource = "*"
                statements.append(
                    {"Effect": "Allow", "Action": [action], "Resource": resource}
                )
            policy_doc = {"Version": "2012-10-17", "Statement": statements}
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{role_name}-policy",
                PolicyDocument=json.dumps(policy_doc),
            )
            logger.debug("Attached inline policy to role %s", role_name)

        logger.info("Created AWS IAM role: %s", role_arn)
        return ProvisionedIdentity(
            cloud="aws",
            agent_name=agent_name,
            identity_arn=role_arn,
            created=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to provision AWS identity for '%s': %s", agent_name, exc)
        return ProvisionedIdentity(cloud="aws", agent_name=agent_name)


def provision_gcp_identity(
    agent_name: str, project_id: str, config: IdentityConfig
) -> ProvisionedIdentity:
    """Provision a per-agent Service Account on GCP.

    Service Account names are limited to 30 chars so we truncate agent_name.
    All GCP SDK calls are guarded with ImportError so the function degrades
    gracefully when google-api-python-client is absent.
    """
    if not config.create:
        logger.debug("GCP identity: create=false — skipping for agent '%s'", agent_name)
        return ProvisionedIdentity(cloud="gcp", agent_name=agent_name)

    # SA account IDs: 6–30 chars, lowercase letters/digits/hyphens
    sa_id = f"ab-{agent_name[:24]}".rstrip("-").lower()
    sa_email = f"{sa_id}@{project_id}.iam.gserviceaccount.com"
    logger.info("Provisioning GCP Service Account: %s", sa_email)

    try:
        from googleapiclient import discovery  # type: ignore[import]
    except ImportError:
        logger.warning(
            "google-api-python-client not installed — skipping GCP identity provisioning for '%s'",
            agent_name,
        )
        return ProvisionedIdentity(cloud="gcp", agent_name=agent_name)

    try:
        iam_service = discovery.build("iam", "v1")
        iam_service.projects().serviceAccounts().create(
            name=f"projects/{project_id}",
            body={
                "accountId": sa_id,
                "serviceAccount": {
                    "displayName": f"AgentBreeder agent: {agent_name}",
                    "description": f"Per-agent Service Account for AgentBreeder agent '{agent_name}'",
                },
            },
        ).execute()

        # Bind IAM roles to the Service Account if provided
        if config.roles:
            crm = discovery.build("cloudresourcemanager", "v1")
            policy_resp = crm.projects().getIamPolicy(
                resource=project_id, body={"options": {"requestedPolicyVersion": 1}}
            ).execute()
            policy = policy_resp
            member = f"serviceAccount:{sa_email}"
            for role in config.roles:
                # Find or create binding
                binding = next(
                    (b for b in policy.get("bindings", []) if b["role"] == role), None
                )
                if binding:
                    if member not in binding["members"]:
                        binding["members"].append(member)
                else:
                    policy.setdefault("bindings", []).append(
                        {"role": role, "members": [member]}
                    )
            crm.projects().setIamPolicy(
                resource=project_id, body={"policy": policy}
            ).execute()
            logger.debug("Bound %d IAM role(s) to %s", len(config.roles), sa_email)

        logger.info("Created GCP Service Account: %s", sa_email)
        return ProvisionedIdentity(
            cloud="gcp",
            agent_name=agent_name,
            service_account_email=sa_email,
            created=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to provision GCP identity for '%s': %s", agent_name, exc)
        return ProvisionedIdentity(cloud="gcp", agent_name=agent_name)
