"""agentbreeder teardown — remove a deployed agent or org-wide cloud resources."""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
logger = logging.getLogger(__name__)

STATE_FILE = Path.home() / ".agentbreeder" / "state.json"
REGISTRY_DIR = Path.home() / ".agentbreeder" / "registry"

AGENTBREEDER_LABEL = "managed-by=agentbreeder"
IAM_ROLE_PREFIX = "agentbreeder-"


class CloudProvider(str, Enum):
    gcp = "gcp"
    aws = "aws"
    azure = "azure"
    all = "all"


# ---------------------------------------------------------------------------
# State / registry helpers (shared with single-agent path)
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"agents": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _update_registry(agent_name: str) -> None:
    """Mark the agent as stopped in the registry."""
    registry_file = REGISTRY_DIR / "agents.json"
    if not registry_file.exists():
        return
    registry = json.loads(registry_file.read_text())
    if agent_name in registry:
        registry[agent_name]["status"] = "stopped"
        registry_file.write_text(json.dumps(registry, indent=2))


def _load_registry_agent_names() -> list[str]:
    """Return agent names from ~/.agentbreeder/registry/agents.json."""
    registry_file = REGISTRY_DIR / "agents.json"
    if not registry_file.exists():
        return []
    try:
        registry = json.loads(registry_file.read_text())
        return list(registry.keys())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Result row for the cloud teardown table
# ---------------------------------------------------------------------------


class TeardownRow:
    def __init__(self, name: str, resource_type: str, status: str, detail: str = "") -> None:
        self.name = name
        self.resource_type = resource_type
        self.status = status  # "deleted" | "not_found" | "dry_run" | "error"
        self.detail = detail


def _print_teardown_table(rows: list[TeardownRow], cloud: str, dry_run: bool) -> None:
    table = Table(title=f"Cloud Teardown — {cloud.upper()}" + (" (dry run)" if dry_run else ""))
    table.add_column("Resource", style="cyan", no_wrap=True)
    table.add_column("Type", style="dim")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    status_styles = {
        "deleted": "[bold green]deleted[/bold green]",
        "not_found": "[dim]not found[/dim]",
        "dry_run": "[bold yellow]would delete[/bold yellow]",
        "error": "[bold red]error[/bold red]",
    }

    for row in rows:
        table.add_row(
            row.name,
            row.resource_type,
            status_styles.get(row.status, row.status),
            row.detail,
        )

    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# GCP teardown
# ---------------------------------------------------------------------------


def _teardown_gcp(
    region: Optional[str],
    project: Optional[str],
    dry_run: bool,
    agent_filter: Optional[str],
) -> list[TeardownRow]:
    """Delete GCP Cloud Run services, Artifact Registry repos, and Secret Manager secrets
    managed by AgentBreeder.  Returns a list of TeardownRow results."""
    rows: list[TeardownRow] = []
    agent_names = _load_registry_agent_names()
    if agent_filter:
        agent_names = [n for n in agent_names if n == agent_filter]

    try:
        from google.api_core.exceptions import NotFound
        from google.cloud import run_v2, secretmanager
        from google.cloud.artifactregistry_v1 import ArtifactRegistryClient
    except ImportError:
        console.print(
            "[yellow]Warning:[/yellow] google-cloud packages not installed. "
            "Install with: pip install google-cloud-run google-cloud-artifact-registry google-cloud-secret-manager"
        )
        return rows

    # --- Cloud Run services ---
    try:
        run_client = run_v2.ServicesClient()
        parent = f"projects/{project}/locations/{region}" if (project and region) else "-"
        services = list(run_client.list_services(parent=parent))
        for svc in services:
            labels = dict(svc.labels) if svc.labels else {}
            svc_name = svc.name.split("/")[-1]
            if labels.get("managed-by") != "agentbreeder" and svc_name not in agent_names:
                continue
            if agent_filter and svc_name != agent_filter:
                continue
            if dry_run:
                rows.append(TeardownRow(svc_name, "Cloud Run Service", "dry_run", svc.name))
            else:
                try:
                    run_client.delete_service(name=svc.name)
                    rows.append(TeardownRow(svc_name, "Cloud Run Service", "deleted", svc.name))
                except NotFound:
                    rows.append(TeardownRow(svc_name, "Cloud Run Service", "not_found"))
                except Exception as exc:
                    rows.append(TeardownRow(svc_name, "Cloud Run Service", "error", str(exc)))
    except Exception as exc:
        logger.warning("GCP Cloud Run listing failed: %s", exc)

    # --- Artifact Registry repositories ---
    try:
        ar_client = ArtifactRegistryClient()
        ar_parent = f"projects/{project}/locations/{region}" if (project and region) else "-"
        repos = list(ar_client.list_repositories(parent=ar_parent))
        for repo in repos:
            labels = dict(repo.labels) if repo.labels else {}
            repo_name = repo.name.split("/")[-1]
            if labels.get("managed-by") != "agentbreeder" and repo_name not in agent_names:
                continue
            if agent_filter and repo_name != agent_filter:
                continue
            if dry_run:
                rows.append(TeardownRow(repo_name, "Artifact Registry Repo", "dry_run", repo.name))
            else:
                try:
                    ar_client.delete_repository(name=repo.name)
                    rows.append(TeardownRow(repo_name, "Artifact Registry Repo", "deleted", repo.name))
                except NotFound:
                    rows.append(TeardownRow(repo_name, "Artifact Registry Repo", "not_found"))
                except Exception as exc:
                    rows.append(TeardownRow(repo_name, "Artifact Registry Repo", "error", str(exc)))
    except Exception as exc:
        logger.warning("GCP Artifact Registry listing failed: %s", exc)

    # --- Secret Manager secrets ---
    try:
        sm_client = secretmanager.SecretManagerServiceClient()
        sm_parent = f"projects/{project}" if project else "-"
        secrets = list(sm_client.list_secrets(parent=sm_parent))
        for secret in secrets:
            labels = dict(secret.labels) if secret.labels else {}
            secret_name = secret.name.split("/")[-1]
            if labels.get("managed-by") != "agentbreeder" and not any(
                secret_name.startswith(n) for n in agent_names
            ):
                continue
            if agent_filter and not secret_name.startswith(agent_filter):
                continue
            if dry_run:
                rows.append(TeardownRow(secret_name, "Secret Manager Secret", "dry_run", secret.name))
            else:
                try:
                    sm_client.delete_secret(name=secret.name)
                    rows.append(TeardownRow(secret_name, "Secret Manager Secret", "deleted", secret.name))
                except NotFound:
                    rows.append(TeardownRow(secret_name, "Secret Manager Secret", "not_found"))
                except Exception as exc:
                    rows.append(TeardownRow(secret_name, "Secret Manager Secret", "error", str(exc)))
    except Exception as exc:
        logger.warning("GCP Secret Manager listing failed: %s", exc)

    return rows


# ---------------------------------------------------------------------------
# AWS teardown
# ---------------------------------------------------------------------------


def _aws_name_matches_filter(name: str, agent_filter: str) -> bool:
    """Return True if *name* refers to *agent_filter* under any AWS naming convention.

    AWS resources may be named directly after the agent (e.g. ECS service "demo-agent")
    or with the agentbreeder prefix (e.g. ECR repo "agentbreeder-demo-agent").
    """
    return name.startswith(agent_filter) or name.startswith(f"{IAM_ROLE_PREFIX}{agent_filter}")


def _teardown_aws(
    region: Optional[str],
    dry_run: bool,
    agent_filter: Optional[str],
) -> list[TeardownRow]:
    """Delete AWS ECS services/task definitions, ECR repos, Secrets Manager secrets, and
    IAM roles prefixed 'agentbreeder-'.  Returns a list of TeardownRow results."""
    rows: list[TeardownRow] = []
    agent_names = _load_registry_agent_names()
    if agent_filter:
        agent_names = [n for n in agent_names if n == agent_filter]

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        console.print(
            "[yellow]Warning:[/yellow] boto3 not installed. "
            "Install with: pip install boto3"
        )
        return rows

    kwargs: dict = {}
    if region:
        kwargs["region_name"] = region

    # --- ECS services ---
    try:
        ecs = boto3.client("ecs", **kwargs)
        clusters_resp = ecs.list_clusters()
        for cluster_arn in clusters_resp.get("clusterArns", []):
            services_resp = ecs.list_services(cluster=cluster_arn)
            for svc_arn in services_resp.get("serviceArns", []):
                svc_name = svc_arn.split("/")[-1]
                if not any(svc_name.startswith(n) for n in agent_names) and not svc_name.startswith(
                    "agentbreeder-"
                ):
                    continue
                if agent_filter and not _aws_name_matches_filter(svc_name, agent_filter):
                    continue
                if dry_run:
                    rows.append(TeardownRow(svc_name, "ECS Service", "dry_run", cluster_arn))
                else:
                    try:
                        ecs.update_service(cluster=cluster_arn, service=svc_arn, desiredCount=0)
                        ecs.delete_service(cluster=cluster_arn, service=svc_arn, force=True)
                        rows.append(TeardownRow(svc_name, "ECS Service", "deleted", cluster_arn))
                    except ClientError as exc:
                        code = exc.response["Error"]["Code"]
                        if code in ("ServiceNotFoundException", "ServiceNotActiveException"):
                            rows.append(TeardownRow(svc_name, "ECS Service", "not_found"))
                        else:
                            rows.append(TeardownRow(svc_name, "ECS Service", "error", str(exc)))
    except Exception as exc:
        logger.warning("AWS ECS listing failed: %s", exc)

    # --- ECS task definitions ---
    try:
        ecs = boto3.client("ecs", **kwargs)
        paginator = ecs.get_paginator("list_task_definitions")
        for page in paginator.paginate():
            for td_arn in page.get("taskDefinitionArns", []):
                td_name = td_arn.split("/")[-1].rsplit(":", 1)[0]
                if not any(td_name.startswith(n) for n in agent_names) and not td_name.startswith(
                    "agentbreeder-"
                ):
                    continue
                if agent_filter and not _aws_name_matches_filter(td_name, agent_filter):
                    continue
                if dry_run:
                    rows.append(TeardownRow(td_name, "ECS Task Definition", "dry_run", td_arn))
                else:
                    try:
                        ecs.deregister_task_definition(taskDefinition=td_arn)
                        rows.append(TeardownRow(td_name, "ECS Task Definition", "deleted", td_arn))
                    except ClientError as exc:
                        code = exc.response["Error"]["Code"]
                        if code == "InvalidParameterException":
                            rows.append(TeardownRow(td_name, "ECS Task Definition", "not_found"))
                        else:
                            rows.append(TeardownRow(td_name, "ECS Task Definition", "error", str(exc)))
    except Exception as exc:
        logger.warning("AWS ECS task definition listing failed: %s", exc)

    # --- ECR repositories ---
    try:
        ecr = boto3.client("ecr", **kwargs)
        repos_resp = ecr.describe_repositories()
        for repo in repos_resp.get("repositories", []):
            repo_name = repo["repositoryName"]
            if not any(repo_name.startswith(n) for n in agent_names) and not repo_name.startswith(
                "agentbreeder-"
            ):
                continue
            if agent_filter and not _aws_name_matches_filter(repo_name, agent_filter):
                continue
            if dry_run:
                rows.append(TeardownRow(repo_name, "ECR Repository", "dry_run"))
            else:
                try:
                    ecr.delete_repository(repositoryName=repo_name, force=True)
                    rows.append(TeardownRow(repo_name, "ECR Repository", "deleted"))
                except ClientError as exc:
                    code = exc.response["Error"]["Code"]
                    if code == "RepositoryNotFoundException":
                        rows.append(TeardownRow(repo_name, "ECR Repository", "not_found"))
                    else:
                        rows.append(TeardownRow(repo_name, "ECR Repository", "error", str(exc)))
    except Exception as exc:
        logger.warning("AWS ECR listing failed: %s", exc)

    # --- Secrets Manager secrets ---
    try:
        sm = boto3.client("secretsmanager", **kwargs)
        paginator = sm.get_paginator("list_secrets")
        for page in paginator.paginate():
            for secret in page.get("SecretList", []):
                secret_name = secret["Name"]
                if not any(secret_name.startswith(n) for n in agent_names) and not secret_name.startswith(
                    "agentbreeder-"
                ):
                    continue
                if agent_filter and not _aws_name_matches_filter(secret_name, agent_filter):
                    continue
                if dry_run:
                    rows.append(TeardownRow(secret_name, "Secrets Manager Secret", "dry_run"))
                else:
                    try:
                        sm.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
                        rows.append(TeardownRow(secret_name, "Secrets Manager Secret", "deleted"))
                    except ClientError as exc:
                        code = exc.response["Error"]["Code"]
                        if code == "ResourceNotFoundException":
                            rows.append(TeardownRow(secret_name, "Secrets Manager Secret", "not_found"))
                        else:
                            rows.append(TeardownRow(secret_name, "Secrets Manager Secret", "error", str(exc)))
    except Exception as exc:
        logger.warning("AWS Secrets Manager listing failed: %s", exc)

    # --- IAM roles prefixed 'agentbreeder-' ---
    try:
        iam = boto3.client("iam", **kwargs)
        paginator = iam.get_paginator("list_roles")
        for page in paginator.paginate():
            for role in page.get("Roles", []):
                role_name = role["RoleName"]
                if not role_name.startswith(IAM_ROLE_PREFIX):
                    continue
                if agent_filter and not _aws_name_matches_filter(role_name, agent_filter):
                    continue
                if dry_run:
                    rows.append(TeardownRow(role_name, "IAM Role", "dry_run"))
                else:
                    try:
                        # Detach managed policies first
                        policies = iam.list_attached_role_policies(RoleName=role_name).get(
                            "AttachedPolicies", []
                        )
                        for policy in policies:
                            iam.detach_role_policy(
                                RoleName=role_name, PolicyArn=policy["PolicyArn"]
                            )
                        # Delete inline policies
                        inline = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])
                        for pol_name in inline:
                            iam.delete_role_policy(RoleName=role_name, PolicyName=pol_name)
                        iam.delete_role(RoleName=role_name)
                        rows.append(TeardownRow(role_name, "IAM Role", "deleted"))
                    except ClientError as exc:
                        code = exc.response["Error"]["Code"]
                        if code == "NoSuchEntityException":
                            rows.append(TeardownRow(role_name, "IAM Role", "not_found"))
                        else:
                            rows.append(TeardownRow(role_name, "IAM Role", "error", str(exc)))
    except Exception as exc:
        logger.warning("AWS IAM role listing failed: %s", exc)

    return rows


# ---------------------------------------------------------------------------
# Azure teardown
# ---------------------------------------------------------------------------


def _teardown_azure(
    dry_run: bool,
    agent_filter: Optional[str],
    destroy_resource_group: bool = False,
) -> list[TeardownRow]:
    """Delete Azure Container Apps, ACR repos, Key Vault secrets, and Container Apps
    Environments tagged managed-by=agentbreeder.  Returns a list of TeardownRow results."""
    rows: list[TeardownRow] = []
    agent_names = _load_registry_agent_names()
    if agent_filter:
        agent_names = [n for n in agent_names if n == agent_filter]

    try:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.appcontainers import ContainerAppsAPIClient
        from azure.mgmt.containerregistry import ContainerRegistryManagementClient
        from azure.mgmt.keyvault import KeyVaultManagementClient
        from azure.mgmt.resource import ResourceManagementClient
        from azure.mgmt.resource.subscriptions import SubscriptionClient
    except ImportError:
        console.print(
            "[yellow]Warning:[/yellow] azure-mgmt packages not installed. "
            "Install with: pip install azure-mgmt-appcontainers azure-mgmt-containerregistry "
            "azure-mgmt-keyvault azure-mgmt-resource azure-identity"
        )
        return rows

    try:
        credential = DefaultAzureCredential()
        sub_client = SubscriptionClient(credential)
        subscription_id = next(sub_client.subscriptions.list()).subscription_id
    except Exception as exc:
        logger.warning("Azure credential/subscription lookup failed: %s", exc)
        return rows

    try:
        resource_client = ResourceManagementClient(credential, subscription_id)
        resource_groups = [rg.name for rg in resource_client.resource_groups.list()]
    except Exception as exc:
        logger.warning("Azure resource group listing failed: %s", exc)
        return rows

    for rg_name in resource_groups:
        # --- Container Apps ---
        try:
            ca_client = ContainerAppsAPIClient(credential, subscription_id)
            apps = list(ca_client.container_apps.list_by_resource_group(rg_name))
            for app in apps:
                tags = app.tags or {}
                app_name = app.name
                if tags.get("managed-by") != "agentbreeder" and app_name not in agent_names:
                    continue
                if agent_filter and app_name != agent_filter:
                    continue
                if dry_run:
                    rows.append(TeardownRow(app_name, "Container App", "dry_run", rg_name))
                else:
                    try:
                        ca_client.container_apps.begin_delete(rg_name, app_name).result()
                        rows.append(TeardownRow(app_name, "Container App", "deleted", rg_name))
                    except ResourceNotFoundError:
                        rows.append(TeardownRow(app_name, "Container App", "not_found"))
                    except Exception as exc:
                        rows.append(TeardownRow(app_name, "Container App", "error", str(exc)))
        except Exception as exc:
            logger.warning("Azure Container Apps listing failed for %s: %s", rg_name, exc)

        # --- Container Apps Environments ---
        try:
            ca_client = ContainerAppsAPIClient(credential, subscription_id)
            envs = list(ca_client.managed_environments.list_by_resource_group(rg_name))
            for env in envs:
                tags = env.tags or {}
                env_name = env.name
                if tags.get("managed-by") != "agentbreeder":
                    continue
                if agent_filter:
                    continue  # environments are shared; only delete when not filtering
                if dry_run:
                    rows.append(TeardownRow(env_name, "Container Apps Environment", "dry_run", rg_name))
                else:
                    try:
                        ca_client.managed_environments.begin_delete(rg_name, env_name).result()
                        rows.append(TeardownRow(env_name, "Container Apps Environment", "deleted", rg_name))
                    except ResourceNotFoundError:
                        rows.append(TeardownRow(env_name, "Container Apps Environment", "not_found"))
                    except Exception as exc:
                        rows.append(TeardownRow(env_name, "Container Apps Environment", "error", str(exc)))
        except Exception as exc:
            logger.warning("Azure Container Apps Env listing failed for %s: %s", rg_name, exc)

        # --- Azure Container Registry repositories ---
        try:
            acr_client = ContainerRegistryManagementClient(credential, subscription_id)
            registries = list(acr_client.registries.list_by_resource_group(rg_name))
            for registry in registries:
                tags = registry.tags or {}
                if tags.get("managed-by") != "agentbreeder":
                    continue
                reg_name = registry.name
                # List repositories within the registry via the data-plane client
                try:
                    from azure.containerregistry import ContainerRegistryClient as DataPlaneACR

                    login_server = registry.login_server
                    data_client = DataPlaneACR(f"https://{login_server}", credential)
                    for repo_name in data_client.list_repository_names():
                        if agent_filter and not repo_name.startswith(agent_filter):
                            continue
                        if dry_run:
                            rows.append(
                                TeardownRow(repo_name, "ACR Repository", "dry_run", reg_name)
                            )
                        else:
                            try:
                                data_client.delete_repository(repo_name)
                                rows.append(
                                    TeardownRow(repo_name, "ACR Repository", "deleted", reg_name)
                                )
                            except ResourceNotFoundError:
                                rows.append(TeardownRow(repo_name, "ACR Repository", "not_found"))
                            except Exception as exc:
                                rows.append(
                                    TeardownRow(repo_name, "ACR Repository", "error", str(exc))
                                )
                except ImportError:
                    pass  # azure-containerregistry data-plane client not installed
        except Exception as exc:
            logger.warning("Azure ACR listing failed for %s: %s", rg_name, exc)

        # --- Key Vault secrets ---
        try:
            kv_client = KeyVaultManagementClient(credential, subscription_id)
            vaults = list(kv_client.vaults.list_by_resource_group(rg_name))
            for vault in vaults:
                tags = vault.tags or {}
                if tags.get("managed-by") != "agentbreeder":
                    continue
                vault_url = vault.properties.vault_uri
                try:
                    from azure.keyvault.secrets import SecretClient

                    secret_client = SecretClient(vault_url=vault_url, credential=credential)
                    for secret_prop in secret_client.list_properties_of_secrets():
                        secret_name = secret_prop.name
                        if agent_filter and not secret_name.startswith(agent_filter):
                            continue
                        if dry_run:
                            rows.append(
                                TeardownRow(secret_name, "Key Vault Secret", "dry_run", vault.name)
                            )
                        else:
                            try:
                                secret_client.begin_delete_secret(secret_name).result()
                                rows.append(
                                    TeardownRow(secret_name, "Key Vault Secret", "deleted", vault.name)
                                )
                            except ResourceNotFoundError:
                                rows.append(TeardownRow(secret_name, "Key Vault Secret", "not_found"))
                            except Exception as exc:
                                rows.append(
                                    TeardownRow(secret_name, "Key Vault Secret", "error", str(exc))
                                )
                except ImportError:
                    pass  # azure-keyvault-secrets not installed
        except Exception as exc:
            logger.warning("Azure Key Vault listing failed for %s: %s", rg_name, exc)

        # --- Optionally destroy resource groups ---
        if destroy_resource_group and not agent_filter:
            try:
                rg = resource_client.resource_groups.get(rg_name)
                tags = rg.tags or {}
                if tags.get("managed-by") == "agentbreeder":
                    if dry_run:
                        rows.append(TeardownRow(rg_name, "Resource Group", "dry_run"))
                    else:
                        try:
                            resource_client.resource_groups.begin_delete(rg_name).result()
                            rows.append(TeardownRow(rg_name, "Resource Group", "deleted"))
                        except ResourceNotFoundError:
                            rows.append(TeardownRow(rg_name, "Resource Group", "not_found"))
                        except Exception as exc:
                            rows.append(TeardownRow(rg_name, "Resource Group", "error", str(exc)))
            except Exception as exc:
                logger.warning("Azure resource group delete failed for %s: %s", rg_name, exc)

    return rows


# ---------------------------------------------------------------------------
# Cloud teardown dispatcher
# ---------------------------------------------------------------------------


def _run_cloud_teardown(
    cloud: CloudProvider,
    region: Optional[str],
    project: Optional[str],
    dry_run: bool,
    agent_filter: Optional[str],
    destroy_resource_group: bool,
) -> int:
    """Run cloud-provider teardown(s).  Returns exit code (0 = success, 1 = any error)."""
    any_error = False

    if cloud in (CloudProvider.gcp, CloudProvider.all):
        rows = _teardown_gcp(region=region, project=project, dry_run=dry_run, agent_filter=agent_filter)
        _print_teardown_table(rows, "gcp", dry_run)
        if any(r.status == "error" for r in rows):
            any_error = True

    if cloud in (CloudProvider.aws, CloudProvider.all):
        rows = _teardown_aws(region=region, dry_run=dry_run, agent_filter=agent_filter)
        _print_teardown_table(rows, "aws", dry_run)
        if any(r.status == "error" for r in rows):
            any_error = True

    if cloud in (CloudProvider.azure, CloudProvider.all):
        rows = _teardown_azure(
            dry_run=dry_run,
            agent_filter=agent_filter,
            destroy_resource_group=destroy_resource_group,
        )
        _print_teardown_table(rows, "azure", dry_run)
        if any(r.status == "error" for r in rows):
            any_error = True

    return 1 if any_error else 0


# ---------------------------------------------------------------------------
# Main teardown command
# ---------------------------------------------------------------------------


def teardown(
    agent_name: Optional[str] = typer.Argument(None, help="Name of the agent to remove"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    # Cloud teardown options
    cloud: Optional[CloudProvider] = typer.Option(
        None,
        "--cloud",
        help="Cloud provider(s) to clean up: gcp | aws | azure | all",
        case_sensitive=False,
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        help="Cloud region (GCP/AWS). Defaults to provider default.",
    ),
    project: Optional[str] = typer.Option(
        None,
        "--project",
        help="GCP project ID (GCP only).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what WOULD be deleted without deleting anything.",
    ),
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        help="Filter cloud teardown to resources belonging to this agent name.",
    ),
    destroy_resource_group: bool = typer.Option(
        False,
        "--destroy-resource-group",
        help="(Azure only) Also delete Resource Groups tagged managed-by=agentbreeder.",
    ),
) -> None:
    """Remove a deployed agent or clean up org-wide cloud resources.

    Examples:
        agentbreeder teardown my-agent
        agentbreeder teardown my-agent --force
        agentbreeder teardown --cloud gcp --region us-central1 --project my-proj --dry-run
        agentbreeder teardown --cloud aws --region us-east-1 --dry-run
        agentbreeder teardown --cloud azure --dry-run --destroy-resource-group
        agentbreeder teardown --cloud all --dry-run
        agentbreeder teardown --cloud gcp --agent demo-agent
    """
    # -----------------------------------------------------------------------
    # Cloud-wide teardown path
    # -----------------------------------------------------------------------
    if cloud is not None:
        if not force and not dry_run:
            console.print()
            scope = f"agent [cyan]{agent}[/cyan]" if agent else "all AgentBreeder resources"
            console.print(
                f"  [bold]Cloud Teardown:[/bold] [yellow]{cloud.value}[/yellow]  "
                f"[dim](scope: {scope})[/dim]"
            )
            if dry_run:
                console.print("  [dim]Mode: dry run (no changes will be made)[/dim]")
            console.print()
            confirm = console.input("  [bold]Are you sure? (y/N): [/bold]").strip().lower()
            if confirm != "y":
                console.print("  [dim]Aborted.[/dim]\n")
                raise typer.Exit(code=0)

        exit_code = _run_cloud_teardown(
            cloud=cloud,
            region=region,
            project=project,
            dry_run=dry_run,
            agent_filter=agent,
            destroy_resource_group=destroy_resource_group,
        )
        raise typer.Exit(code=exit_code)

    # -----------------------------------------------------------------------
    # Single-agent teardown path (original behaviour — preserved exactly)
    # -----------------------------------------------------------------------
    if agent_name is None:
        console.print("[bold red]Error:[/bold red] Provide an agent name or --cloud <provider>.")
        raise typer.Exit(code=1)

    state = _load_state()
    agents = state.get("agents", {})

    if agent_name not in agents:
        available = list(agents.keys())
        if json_output:
            import sys

            sys.stdout.write(
                json.dumps({"error": f"Agent '{agent_name}' not found", "available": available})
                + "\n"
            )
        else:
            console.print()
            msg = f"[bold red]Agent '{agent_name}' not found[/bold red]"
            if available:
                msg += f"\n\n  Available agents: [cyan]{', '.join(available)}[/cyan]"
            else:
                msg += "\n\n  No agents deployed."
            console.print(Panel(msg, title="Error", border_style="red"))
            console.print()
        raise typer.Exit(code=1)

    agent_info = agents[agent_name]
    status = agent_info.get("status", "unknown")

    # Confirmation
    if not force and not json_output:
        console.print()
        console.print(
            f"  [bold]Teardown:[/bold] [cyan]{agent_name}[/cyan]  [dim](status: {status})[/dim]"
        )
        endpoint = agent_info.get("endpoint_url", "")
        if endpoint:
            console.print(f"  [dim]Endpoint: {endpoint}[/dim]")
        console.print()

        confirm = console.input("  [bold]Are you sure? (y/N): [/bold]").strip().lower()
        if confirm != "y":
            console.print("  [dim]Aborted.[/dim]\n")
            raise typer.Exit(code=0)

    # Attempt Docker teardown
    container_removed = False
    if status == "running":
        container_removed = _teardown_container(agent_name, json_output)

    # Update state
    agents[agent_name]["status"] = "stopped"
    _save_state(state)

    # Update registry
    _update_registry(agent_name)

    if json_output:
        import sys

        sys.stdout.write(
            json.dumps(
                {
                    "agent": agent_name,
                    "status": "stopped",
                    "container_removed": container_removed,
                }
            )
            + "\n"
        )
        return

    console.print()
    console.print(
        Panel(
            f"[bold green]Torn down:[/bold green] [cyan]{agent_name}[/cyan]\n\n"
            + (
                "  [green]✓[/green] Container stopped and removed\n"
                if container_removed
                else "  [dim]✓ State updated (no running container found)[/dim]\n"
            )
            + "  [green]✓[/green] Registry updated\n"
            + "  [green]✓[/green] Status set to stopped",
            title="Teardown Complete",
            border_style="green",
        )
    )
    console.print()


def _teardown_container(agent_name: str, json_output: bool) -> bool:
    """Stop and remove the Docker container. Returns True if removed."""
    try:
        from engine.deployers.docker_compose import DockerComposeDeployer

        deployer = DockerComposeDeployer()
        asyncio.run(deployer.teardown(agent_name))
        return True
    except RuntimeError as e:
        # Docker SDK not installed or Docker not running
        if not json_output:
            console.print(f"  [yellow]Warning:[/yellow] Could not stop container: {e}")
        return False
    except Exception as e:
        if not json_output:
            console.print(f"  [yellow]Warning:[/yellow] Container cleanup: {e}")
        return False
