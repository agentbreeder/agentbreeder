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

    Raises ``NotImplementedError`` for clouds whose CLI greenfield mapping is
    not implemented yet (GCP/Azure follow in later work; see #537).
    """
    if cloud == "aws":
        return _aws_env(state)
    raise NotImplementedError(
        f"CLI greenfield provisioning is implemented for AWS only; got {cloud!r}. "
        "Use the Studio deploy wizard for GCP/Azure greenfield (issue #537)."
    )


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
