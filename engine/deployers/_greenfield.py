"""Greenfield → deploy glue.

After a greenfield :meth:`InfraProvisioner.provision` creates a fresh footprint,
:func:`infra_state_to_env` maps its returned ``InfraState.resources`` into the
``deploy.env_vars`` the cloud deployer already reads — so the existing
(bring-your-own-infra) deploy path serves the agent into the new footprint with
no further changes.

This is the one piece of new logic behind ``agentbreeder deploy --provision``
(issue #537). Keep it pure and per-cloud: given a provisioned ``InfraState``,
return a ``dict[str, str]`` of environment variables. Callers apply it with
``setdefault`` semantics so an explicit BYO field always wins.
"""

from __future__ import annotations

from engine.provisioners.state import InfraState


def infra_state_to_env(cloud: str, state: InfraState) -> dict[str, str]:
    """Map a greenfield ``InfraState`` into deploy ``env_vars`` for ``cloud``.

    Implemented for AWS, GCP, and Azure (multi-cloud parity, #505/#537).
    Raises ``NotImplementedError`` for any other cloud.
    """
    mapper = {"aws": _aws_env, "gcp": _gcp_env, "azure": _azure_env}.get(cloud)
    if mapper is None:
        raise NotImplementedError(f"CLI greenfield provisioning is not implemented for {cloud!r}.")
    return mapper(state)


def _aws_env(state: InfraState) -> dict[str, str]:
    res = state.resources
    network = res.get("network", {})
    sgs = res.get("security_groups", {})

    public_subnets = network.get("public_subnet_ids", []) or []
    private_subnets = network.get("private_subnet_ids", []) or []
    role_arn = str(res["iam_execution_role"]["arn"])
    # arn:aws:iam::<account-id>:role/<name> → the deployer requires the account ID.
    account_id = role_arn.split(":")[4] if role_arn.count(":") >= 4 else ""

    env: dict[str, str] = {
        # Inputs aws_ecs.py::_extract_ecs_config reads.
        "AWS_ACCOUNT_ID": account_id,
        "AWS_ECS_CLUSTER": str(res["ecs_cluster"]["name"]),
        "AWS_EXECUTION_ROLE_ARN": role_arn,
        # The agent task ENI joins the public subnets and gets a public IP.
        "AWS_VPC_SUBNETS": ",".join(public_subnets),
        "AWS_SECURITY_GROUPS": str(sgs["agent_sg_id"]),
        "AWS_REGION": str(state.region),
        # So the data-tier auto-provision (RDS/Redis) lands in the new VPC and
        # can prefer private subnets.
        "AWS_VPC_ID": str(res["vpc"]["vpc_id"]),
    }
    if private_subnets:
        env["AWS_DB_SUBNETS"] = ",".join(private_subnets)
    return env


def _gcp_env(state: InfraState) -> dict[str, str]:
    """Map a GCP greenfield ``InfraState`` into the env keys the Cloud Run
    deployer (``gcp_cloudrun._extract_cloudrun_config``) and the data-tier
    auto-provisioner read."""
    res = state.resources
    sa = res.get("service_account", {})
    ar = res.get("artifact_registry", {})
    connector = res.get("vpc_connector", {})
    network = res.get("network", {})

    project = str(sa.get("project") or "")
    env: dict[str, str] = {"GCP_REGION": str(state.region)}
    if project:
        # The deployer reads GCP_PROJECT_ID/GOOGLE_CLOUD_PROJECT; the user
        # normally supplies it, but stamp it for completeness.
        env["GCP_PROJECT_ID"] = project
    if sa.get("email"):
        env["GCP_SERVICE_ACCOUNT"] = str(sa["email"])
    if ar.get("repo"):
        env["GCP_ARTIFACT_REGISTRY_REPO"] = str(ar["repo"])
    if connector.get("name"):
        env["GCP_VPC_CONNECTOR"] = str(connector["name"])
    # A greenfield VPC (created by the provisioner) so the data tier
    # (Cloud SQL / Memorystore) lands in it rather than the default network.
    if network.get("name"):
        env["GCP_VPC_NAME"] = str(network["name"])
    if network.get("subnet"):
        env["GCP_SUBNET_NAME"] = str(network["subnet"])
    return env


def _azure_env(state: InfraState) -> dict[str, str]:
    """Map an Azure greenfield ``InfraState`` into the env keys the Container
    Apps deployer (``azure_container_apps._extract_azure_config``) and the
    data-tier auto-provisioner read."""
    res = state.resources
    rg = res.get("resource_group", {})
    aca_env = res.get("container_apps_environment", {})
    acr = res.get("acr", {})
    vnet = res.get("vnet", {})
    key_vault = res.get("key_vault", {})

    env: dict[str, str] = {"AZURE_LOCATION": str(state.region)}
    # Subscription id is supplied by the user, but recover it from the RG ARM id
    # (/subscriptions/<id>/resourceGroups/<name>) so the mapping is self-contained.
    rg_id = str(rg.get("id") or "")
    parts = rg_id.split("/")
    if "subscriptions" in parts:
        env["AZURE_SUBSCRIPTION_ID"] = parts[parts.index("subscriptions") + 1]
    if rg.get("name"):
        env["AZURE_RESOURCE_GROUP"] = str(rg["name"])
    if aca_env.get("name"):
        env["AZURE_CONTAINER_APPS_ENV"] = str(aca_env["name"])
    if acr.get("login_server"):
        env["AZURE_REGISTRY_SERVER"] = str(acr["login_server"])
    # Data tier: land managed Postgres in the greenfield VNet's delegated subnet.
    if vnet.get("name"):
        env["AZURE_VNET_NAME"] = str(vnet["name"])
    if vnet.get("db_subnet_id"):
        env["AZURE_DB_SUBNET_ID"] = str(vnet["db_subnet_id"])
    if key_vault.get("uri"):
        env["AZURE_KEYVAULT_URL"] = str(key_vault["uri"])
    return env
