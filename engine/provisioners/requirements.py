"""Cloud-requirements registry — the user-input contract per cloud + mode.

Returned by GET /api/v1/deployments/cloud-requirements/{cloud}. Static data
(no SDK calls). The validate-infra endpoint and the dashboard wizard read
this to know what to ask the user for.

"simple" mode = account + region + creds; AgentBreeder uses cloud defaults.
"full"   mode = user describes every specific resource (cluster, subnets, IAM).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CloudMode = Literal["simple", "full"]
CloudName = Literal["aws", "gcp", "azure"]


class CloudField(BaseModel):
    """One required-or-optional input field for a cloud + mode."""

    name: str
    description: str
    default: str | None = None
    sensitive: bool = False


class CloudRequirements(BaseModel):
    """Full input contract for one cloud + mode."""

    cloud: CloudName
    mode: CloudMode
    required: list[CloudField] = Field(default_factory=list)
    optional: list[CloudField] = Field(default_factory=list)
    rate_limit_per_minute: int = 10
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

_AWS_SIMPLE = CloudRequirements(
    cloud="aws",
    mode="simple",
    required=[
        CloudField(name="AWS_ACCOUNT_ID", description="AWS Account ID (12 digits)"),
        CloudField(name="AWS_DEFAULT_REGION", description="AWS region", default="us-east-1"),
        CloudField(
            name="AWS_ACCESS_KEY_ID",
            description="AWS access key (or use AWS_PROFILE / instance profile)",
            sensitive=True,
        ),
        CloudField(
            name="AWS_SECRET_ACCESS_KEY",
            description="AWS secret access key",
            sensitive=True,
        ),
    ],
    optional=[
        CloudField(name="AWS_SESSION_TOKEN", description="STS session token", sensitive=True),
        CloudField(name="AWS_PROFILE", description="Named profile from ~/.aws/credentials"),
        CloudField(name="AWS_ECR_REGISTRY", description="Override ECR registry host"),
    ],
    notes=[
        "Simple mode uses ECS cluster 'agentbreeder-default', IAM role "
        "'agentbreeder-ecs-execution-role', the default VPC, and the default "
        "security group. These are created on first deploy via --provision "
        "(coming with #383).",
    ],
)

_AWS_FULL = CloudRequirements(
    cloud="aws",
    mode="full",
    required=_AWS_SIMPLE.required
    + [
        CloudField(name="AWS_ECS_CLUSTER", description="ECS cluster name"),
        CloudField(
            name="AWS_EXECUTION_ROLE_ARN",
            description="IAM execution role ARN (ecsTaskExecutionRole)",
        ),
        CloudField(
            name="AWS_VPC_SUBNETS",
            description="Comma-separated subnet IDs (>=2 for HA)",
        ),
        CloudField(name="AWS_SECURITY_GROUPS", description="Comma-separated security group IDs"),
        CloudField(name="AWS_ECR_REPOSITORY", description="ECR repository name"),
    ],
    optional=_AWS_SIMPLE.optional
    + [
        CloudField(name="AWS_TASK_ROLE_ARN", description="Task IAM role ARN"),
        CloudField(
            name="AWS_ALB_TARGET_GROUP_ARN",
            description="Application Load Balancer target group ARN",
        ),
        CloudField(name="AWS_CLOUDWATCH_LOG_GROUP", description="CloudWatch Logs group name"),
        CloudField(
            name="AWS_SECRETS_MANAGER_PREFIX", description="ARN prefix for Secrets Manager"
        ),
    ],
)


# ---------------------------------------------------------------------------
# GCP
# ---------------------------------------------------------------------------

_GCP_SIMPLE = CloudRequirements(
    cloud="gcp",
    mode="simple",
    required=[
        CloudField(name="GOOGLE_CLOUD_PROJECT", description="GCP project ID"),
        CloudField(
            name="GOOGLE_APPLICATION_CREDENTIALS",
            description="Path to service-account JSON (or use ADC)",
            sensitive=True,
        ),
    ],
    optional=[
        CloudField(name="GCP_REGION", description="Cloud Run region", default="us-central1"),
    ],
    notes=[
        "Simple mode uses Artifact Registry repo 'agentbreeder' and the "
        "default compute service account '<project-number>-compute@developer.gserviceaccount.com'. "
        "Verify the four IAM roles in website/content/docs/deployment.mdx are granted.",
    ],
)

_GCP_FULL = CloudRequirements(
    cloud="gcp",
    mode="full",
    required=_GCP_SIMPLE.required
    + [
        CloudField(
            name="GCP_ARTIFACT_REGISTRY_REPO", description="Artifact Registry repository name"
        ),
        CloudField(
            name="GCP_CLOUD_RUN_SERVICE_ACCOUNT",
            description="Service account email for the Cloud Run service",
        ),
    ],
    optional=_GCP_SIMPLE.optional
    + [
        CloudField(name="GCP_VPC_CONNECTOR", description="Serverless VPC Access connector"),
        CloudField(
            name="GCP_CLOUD_SQL_INSTANCE", description="Cloud SQL instance connection name"
        ),
        CloudField(
            name="GCP_ALLOW_UNAUTHENTICATED",
            description="Permit unauthenticated invocations (true/false)",
        ),
        CloudField(name="GCP_CUSTOM_DOMAIN", description="Custom domain mapping"),
    ],
)


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------

_AZURE_SIMPLE = CloudRequirements(
    cloud="azure",
    mode="simple",
    required=[
        CloudField(name="AZURE_SUBSCRIPTION_ID", description="Azure subscription ID"),
        CloudField(name="AZURE_TENANT_ID", description="Microsoft Entra (Azure AD) tenant ID"),
        CloudField(
            name="AZURE_CLIENT_ID",
            description="Service principal client ID (or 'az login' profile)",
            sensitive=True,
        ),
        CloudField(
            name="AZURE_CLIENT_SECRET",
            description="Service principal secret",
            sensitive=True,
        ),
    ],
    optional=[
        CloudField(name="AZURE_LOCATION", description="Azure region", default="eastus"),
    ],
    notes=[
        "Simple mode uses Resource Group 'agentbreeder-rg' and an "
        "auto-named ACR ('agentbreeder<5-char-hash>'), Log Analytics, and "
        "ACA environment created on first deploy.",
    ],
)

_AZURE_FULL = CloudRequirements(
    cloud="azure",
    mode="full",
    required=_AZURE_SIMPLE.required
    + [
        CloudField(name="AZURE_RESOURCE_GROUP", description="Resource Group name"),
        CloudField(
            name="AZURE_ACR_LOGIN_SERVER", description="Azure Container Registry login server"
        ),
        CloudField(
            name="AZURE_ACA_ENVIRONMENT", description="Container Apps managed environment name"
        ),
        CloudField(
            name="AZURE_LOG_ANALYTICS_WORKSPACE_ID",
            description="Log Analytics workspace ID (GUID)",
        ),
        CloudField(
            name="AZURE_MANAGED_IDENTITY_ID", description="User-assigned managed identity ID"
        ),
    ],
    optional=_AZURE_SIMPLE.optional
    + [
        CloudField(name="AZURE_KEY_VAULT_NAME", description="Key Vault name for secrets"),
        CloudField(name="AZURE_POSTGRES_FQDN", description="Azure Database for PostgreSQL FQDN"),
        CloudField(name="AZURE_VNET_SUBNET_ID", description="VNet subnet ID for delegation"),
    ],
)


_REQUIREMENTS: dict[tuple[CloudName, CloudMode], CloudRequirements] = {
    ("aws", "simple"): _AWS_SIMPLE,
    ("aws", "full"): _AWS_FULL,
    ("gcp", "simple"): _GCP_SIMPLE,
    ("gcp", "full"): _GCP_FULL,
    ("azure", "simple"): _AZURE_SIMPLE,
    ("azure", "full"): _AZURE_FULL,
}


def get_requirements(cloud: CloudName, mode: CloudMode = "simple") -> CloudRequirements:
    """Return the user-input contract for the given cloud + mode."""
    try:
        return _REQUIREMENTS[(cloud, mode)]
    except KeyError as e:
        raise ValueError(f"No requirements registered for cloud={cloud!r} mode={mode!r}") from e
