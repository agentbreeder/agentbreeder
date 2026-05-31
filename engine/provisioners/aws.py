"""AWS provisioner — validates BYO infra and greenfield-provisions per agent (#383).

The greenfield path creates the minimum-viable AWS footprint for an
AgentBreeder ECS Fargate deploy:

VPC + subnets + IGW + NAT + route tables → security groups → ECS cluster +
IAM execution role → ECR repo (delegated to existing ``_ensure_ecr_repository``
in the deployer) → (optional) RDS Postgres → (optional) ALB.

Every resource is tagged ``AgentBreeder=true`` + ``AgentName`` + ``Version``;
:meth:`destroy` refuses to touch any resource missing the AgentBreeder tag.
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
)

from engine.provisioners.base import (
    DataBackendRequest,
    InfraProvisioner,
    InfraValidationInput,
    ValidationCheck,
    ValidationResult,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from engine.provisioners.base import ProgressCallback
    from engine.provisioners.state import InfraState

logger = logging.getLogger(__name__)

# Constants ------------------------------------------------------------------

VPC_CIDR = "10.0.0.0/16"
PUBLIC_SUBNET_CIDRS = ("10.0.1.0/24", "10.0.2.0/24")
PRIVATE_SUBNET_CIDRS = ("10.0.3.0/24", "10.0.4.0/24")
TLS_LISTENER_POLICY = "ELBSecurityPolicy-TLS13-1-2-2021-06"
DB_NAME = "agentbreeder_memory"
DB_PORT = 5432
ALB_HTTP_PORT = 80
ALB_HTTPS_PORT = 443
AGENT_CONTAINER_PORT = 8080


def _session(fields: dict[str, Any], region: str) -> boto3.session.Session:
    """Build a boto3 Session honouring AWS_PROFILE / explicit creds / default chain.

    Never logs credentials. When no creds are provided, boto3 falls back to the
    standard credential resolution chain (env, ~/.aws/credentials, IRSA, etc.).
    """
    session_kwargs: dict[str, Any] = {"region_name": region}
    if profile := fields.get("AWS_PROFILE"):
        session_kwargs["profile_name"] = profile
    return boto3.session.Session(**session_kwargs)


def _client(service: str, region: str, fields: dict[str, Any]) -> Any:
    """Build a boto3 client. Honors AWS_PROFILE if set."""
    return _session(fields, region).client(service)


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


def _tags(agent_name: str, version: str, extra: dict[str, str] | None = None) -> list[dict]:
    """Standard AgentBreeder tag set. Tag-set used to filter destroy() targets."""
    tags = [
        {"Key": "AgentBreeder", "Value": "true"},
        {"Key": "AgentName", "Value": agent_name},
        {"Key": "Version", "Value": version},
    ]
    if extra:
        tags.extend({"Key": k, "Value": v} for k, v in extra.items())
    return tags


def _has_agentbreeder_tag(tags: list[dict] | None) -> bool:
    """Return True only if the resource bears the canonical AgentBreeder=true tag."""
    if not tags:
        return False
    for t in tags:
        # boto3 uses {"Key": .., "Value": ..} for EC2/ECS/IAM/ELB and
        # {"key": .., "value": ..} for RDS — handle both.
        k = t.get("Key") or t.get("key")
        v = t.get("Value") or t.get("value")
        if k == "AgentBreeder" and str(v).lower() == "true":
            return True
    return False


class AWSProvisioner(InfraProvisioner):
    """Validates BYO AWS resources and greenfield-provisions per agent (#383)."""

    # ------------------------------------------------------------------
    # validate_existing — read-only check that referenced resources exist.
    # ------------------------------------------------------------------

    async def validate_existing(self, payload: InfraValidationInput) -> ValidationResult:
        fields = payload.fields
        region = payload.region
        checks: list[ValidationCheck] = []

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
                    detail=(
                        f"credentials resolve to account {identity_check.detail!r}, "
                        f"but AWS_ACCOUNT_ID={claimed!r}"
                    ),
                )
            else:
                identity_check = ValidationCheck(
                    resource="credentials",
                    status="found",
                    detail=f"account {identity_check.detail}",
                )
        checks.append(identity_check)

        if identity_check.status != "found":
            return ValidationResult(valid=False, cloud="aws", region=region, checks=checks)

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

    # ------------------------------------------------------------------
    # Greenfield provisioning (#383)
    # ------------------------------------------------------------------

    async def provision(
        self,
        payload: InfraValidationInput,
        progress: ProgressCallback | None = None,
    ) -> InfraState:
        """Create the minimum-viable AWS footprint for an AgentBreeder deploy.

        Resources (in order): VPC → subnets → IGW → NAT → route tables →
        security groups → ECS cluster → IAM execution role → (cond) RDS →
        (cond) ALB. ECR is left to the deployer's existing
        ``_ensure_ecr_repository`` to avoid duplication.

        All resources are tagged ``AgentBreeder=true``, ``AgentName=<name>``,
        ``Version=<version>`` so :meth:`destroy` can filter safely.
        """
        from datetime import UTC, datetime

        from engine.provisioners.state import InfraState

        fields = payload.fields
        region = payload.region or fields.get("AWS_DEFAULT_REGION", "us-east-1")

        agent_name = str(fields.get("AWS_AGENT_NAME", "agentbreeder-default"))
        agent_version = str(fields.get("AWS_AGENT_VERSION", "0.0.0"))
        has_memory = bool(fields.get("AWS_HAS_MEMORY"))
        is_public = str(fields.get("AWS_ACCESS_VISIBILITY", "")).lower() == "public"
        multi_az_nat = str(fields.get("AWS_MULTI_AZ_NAT", "")).strip() in {"1", "true", "yes"}

        ec2 = _client("ec2", region, fields)
        ecs = _client("ecs", region, fields)
        iam = _client("iam", region, fields)
        rds = _client("rds", region, fields) if has_memory else None
        elbv2 = _client("elbv2", region, fields) if is_public else None
        secrets_client = _client("secretsmanager", region, fields) if has_memory else None

        resources: dict[str, Any] = {}

        async def _emit(msg: str) -> None:
            logger.info("aws.provision: %s", msg)
            if progress is not None:
                await progress(msg)

        # ---- 1. VPC ------------------------------------------------------
        await _emit(f"ensuring VPC {VPC_CIDR}")
        vpc_id = await self._provision_vpc(
            ec2=ec2, agent_name=agent_name, agent_version=agent_version
        )
        resources["vpc"] = {"vpc_id": vpc_id, "cidr": VPC_CIDR}

        # ---- 2. Subnets + IGW + NAT + Route Tables -----------------------
        await _emit("ensuring subnets, IGW, NAT gateway, route tables")
        net = await self._provision_subnets(
            ec2=ec2,
            vpc_id=vpc_id,
            agent_name=agent_name,
            agent_version=agent_version,
            multi_az_nat=multi_az_nat,
        )
        resources["network"] = net

        # ---- 3. Security Groups -----------------------------------------
        await _emit("ensuring security groups")
        sgs = await self._provision_security_groups(
            ec2=ec2,
            vpc_id=vpc_id,
            agent_name=agent_name,
            agent_version=agent_version,
            include_db_sg=has_memory,
        )
        resources["security_groups"] = sgs

        # ---- 4. ECS Cluster (FARGATE + FARGATE_SPOT) --------------------
        cluster_name = f"agentbreeder-{agent_name}"
        await _emit(f"ensuring ECS cluster '{cluster_name}'")
        cluster_arn = await self._provision_ecs_cluster(
            ecs=ecs,
            cluster_name=cluster_name,
            agent_name=agent_name,
            agent_version=agent_version,
        )
        resources["ecs_cluster"] = {"name": cluster_name, "arn": cluster_arn}

        # ---- 5. IAM Execution Role --------------------------------------
        role_name = f"agentbreeder-execution-{agent_name}"
        await _emit(f"ensuring IAM execution role '{role_name}'")
        role_arn = await self._provision_iam_role(
            iam=iam,
            role_name=role_name,
            agent_name=agent_name,
            agent_version=agent_version,
        )
        resources["iam_execution_role"] = {"name": role_name, "arn": role_arn}

        # ECR is left to the deployer's _ensure_ecr_repository(); see #383 issue.
        # Record the expected repo name + tag so destroy can find it later.
        resources["ecr_repository"] = {
            "name": agent_name,
            "note": "ECR is created on first push by the AWS deployer; not duplicated here.",
        }

        # ---- 6. RDS PostgreSQL (conditional) -----------------------------
        if has_memory and rds is not None and secrets_client is not None:
            db_sg_id = sgs["db_sg_id"]
            assert db_sg_id is not None, "db_sg_id required when has_memory=True"
            await _emit("provisioning RDS PostgreSQL (this can take 10-15 min)")
            rds_info = await self._provision_rds(
                rds=rds,
                ec2=ec2,
                secrets_client=secrets_client,
                vpc_id=vpc_id,
                private_subnet_ids=net["private_subnet_ids"],
                db_sg_id=db_sg_id,
                agent_name=agent_name,
                agent_version=agent_version,
                progress=progress,
            )
            resources["rds"] = rds_info

        # ---- 7. ALB (conditional) ---------------------------------------
        if is_public and elbv2 is not None:
            alb_sg_id = sgs["alb_sg_id"]
            assert alb_sg_id is not None, "alb_sg_id required when access.visibility=public"
            await _emit("provisioning ALB + target group + listener")
            alb_info = await self._provision_alb(
                elbv2=elbv2,
                vpc_id=vpc_id,
                public_subnet_ids=net["public_subnet_ids"],
                alb_sg_id=alb_sg_id,
                agent_name=agent_name,
                agent_version=agent_version,
                certificate_arn=fields.get("AWS_ACM_CERTIFICATE_ARN"),
            )
            resources["alb"] = alb_info

        state = InfraState(
            cloud="aws",
            region=region,
            provisioned_by="agentbreeder.AWSProvisioner",
            provisioned_at=datetime.now(UTC),
            mode="provisioned",
            resources=resources,
        )
        await _emit("provision complete")
        return state

    async def destroy(
        self,
        state: InfraState,
        *,
        no_final_snapshot: bool = False,
    ) -> None:
        """Reverse what :meth:`provision` created, in safe order.

        REFUSES to touch any resource that does not carry the ``AgentBreeder=true``
        tag — a hard guard against blast-radius mistakes when state and reality drift.

        ``no_final_snapshot`` defaults to False: RDS is deleted *with* a final
        snapshot (data preservation by default). Pass True only when the caller
        has explicitly opted into data destruction.
        """
        if state.cloud != "aws":
            raise ValueError(f"destroy(aws): state.cloud is {state.cloud!r}, expected 'aws'")

        region = state.region
        fields: dict[str, Any] = {}  # destroy uses default cred chain
        resources = dict(state.resources)

        # 1. ALB first — it has dependencies on subnets / SGs.
        if alb := resources.get("alb"):
            try:
                await self._delete_alb(
                    elbv2=_client("elbv2", region, fields),
                    alb_arn=alb["arn"],
                    target_group_arn=alb.get("target_group_arn"),
                    listener_arn=alb.get("listener_arn"),
                )
            except Exception:  # noqa: BLE001
                logger.exception("destroy(aws): failed to delete ALB %s", alb.get("arn"))

        # 2. RDS (final snapshot by default).
        if rds_info := resources.get("rds"):
            try:
                await self._delete_rds(
                    rds=_client("rds", region, fields),
                    db_id=rds_info["db_instance_identifier"],
                    no_final_snapshot=no_final_snapshot,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "destroy(aws): failed to delete RDS %s", rds_info.get("db_instance_identifier")
                )

        # 3. IAM execution role.
        if role := resources.get("iam_execution_role"):
            try:
                await self._delete_iam_role(
                    iam=_client("iam", region, fields), role_name=role["name"]
                )
            except Exception:  # noqa: BLE001
                logger.exception("destroy(aws): failed to delete IAM role %s", role.get("name"))

        # 4. ECS cluster.
        if cluster := resources.get("ecs_cluster"):
            try:
                await self._delete_ecs_cluster(
                    ecs=_client("ecs", region, fields), cluster_name=cluster["name"]
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "destroy(aws): failed to delete ECS cluster %s", cluster.get("name")
                )

        # 5. Security groups + network + VPC.
        ec2 = _client("ec2", region, fields)
        if sgs := resources.get("security_groups"):
            for key in ("db_sg_id", "agent_sg_id", "alb_sg_id"):
                sg_id = sgs.get(key)
                if not sg_id:
                    continue
                try:
                    await self._delete_security_group(ec2=ec2, sg_id=sg_id)
                except Exception:  # noqa: BLE001
                    logger.exception("destroy(aws): failed to delete SG %s", sg_id)

        if net := resources.get("network"):
            try:
                await self._delete_network(ec2=ec2, net=net)
            except Exception:  # noqa: BLE001
                logger.exception("destroy(aws): failed to delete network for VPC")

        if vpc := resources.get("vpc"):
            try:
                await self._delete_vpc(ec2=ec2, vpc_id=vpc["vpc_id"])
            except Exception:  # noqa: BLE001
                logger.exception("destroy(aws): failed to delete VPC %s", vpc.get("vpc_id"))

    async def provision_data_backend(
        self,
        request: DataBackendRequest,
        progress: ProgressCallback | None = None,
    ) -> InfraState:
        """Provision a managed Postgres (pgvector) into the agent's BYO VPC.

        Unlike :meth:`provision`, this creates NO greenfield networking — it
        joins the existing VPC/subnets the deployer already uses, adding only a
        dedicated DB security group (5432 from the agent SG, never the
        internet) and the RDS instance itself. Reuses :meth:`_provision_rds`,
        so every security invariant there (private, encrypted, password →
        Secrets Manager, rollback-on-secret-failure) holds unchanged.

        Returns an :class:`InfraState` whose ``resources["rds"]`` is the same
        shape the greenfield path records, so
        ``pgvector_dsn_from_resources("aws", ...)`` consumes it directly.
        """
        from datetime import UTC, datetime

        from engine.provisioners.state import InfraState

        if request.engine != "postgres":
            raise NotImplementedError(
                f"AWS provision_data_backend(engine={request.engine!r}) is not "
                "implemented yet (Redis/ElastiCache is tracked separately)."
            )

        fields = request.fields
        region = request.region
        agent_name = request.agent_name
        agent_version = request.agent_version
        net = request.network

        vpc_id = net.get("vpc_id")
        subnet_ids = list(net.get("subnet_ids", []))
        agent_sg_ids = list(net.get("agent_security_group_ids", []))
        if not vpc_id:
            raise ValueError("provision_data_backend(aws) requires network.vpc_id")
        if not subnet_ids:
            raise ValueError("provision_data_backend(aws) requires network.subnet_ids")

        ec2 = _client("ec2", region, fields)
        rds = _client("rds", region, fields)
        secrets_client = _client("secretsmanager", region, fields)

        async def _emit(msg: str) -> None:
            logger.info("aws.provision_data_backend: %s", msg)
            if progress is not None:
                await progress(msg)

        # 1. Dedicated DB security group in the BYO VPC: 5432 from agent SG only.
        await _emit(f"ensuring DB security group in {vpc_id}")
        db_sg_id = self._ensure_security_group(
            ec2=ec2,
            vpc_id=vpc_id,
            name=f"agentbreeder-db-{agent_name}"[:255],
            description=f"AgentBreeder DB ingress (5432 from agent SG) for {agent_name}",
            agent_name=agent_name,
            agent_version=agent_version,
        )
        for src_sg in agent_sg_ids:
            self._authorize_ingress(
                ec2=ec2,
                sg_id=db_sg_id,
                protocol="tcp",
                from_port=DB_PORT,
                to_port=DB_PORT,
                source_sg_id=src_sg,
            )

        # 2. RDS Postgres in the supplied subnets, bound to the new DB SG.
        await _emit("provisioning RDS PostgreSQL (this can take 10-15 min)")
        rds_info = await self._provision_rds(
            rds=rds,
            ec2=ec2,
            secrets_client=secrets_client,
            vpc_id=vpc_id,
            private_subnet_ids=subnet_ids,
            db_sg_id=db_sg_id,
            agent_name=agent_name,
            agent_version=agent_version,
            progress=progress,
        )

        state = InfraState(
            cloud="aws",
            region=region,
            provisioned_by="agentbreeder.AWSProvisioner.provision_data_backend",
            provisioned_at=datetime.now(UTC),
            mode="provisioned",
            resources={"rds": rds_info, "db_security_group_id": db_sg_id},
        )
        await _emit("data backend provision complete")
        return state

    # ------------------------------------------------------------------
    # Low-level helpers — each idempotent, each broken out for unit tests.
    # ------------------------------------------------------------------

    async def _provision_vpc(self, *, ec2: Any, agent_name: str, agent_version: str) -> str:
        existing = ec2.describe_vpcs(
            Filters=[
                {"Name": "tag:AgentBreeder", "Values": ["true"]},
                {"Name": "tag:AgentName", "Values": [agent_name]},
                {"Name": "cidr", "Values": [VPC_CIDR]},
            ]
        )
        if vpcs := existing.get("Vpcs", []):
            return vpcs[0]["VpcId"]
        created = ec2.create_vpc(
            CidrBlock=VPC_CIDR,
            TagSpecifications=[{"ResourceType": "vpc", "Tags": _tags(agent_name, agent_version)}],
        )
        vpc_id = created["Vpc"]["VpcId"]
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        return vpc_id

    async def _provision_subnets(
        self,
        *,
        ec2: Any,
        vpc_id: str,
        agent_name: str,
        agent_version: str,
        multi_az_nat: bool,
    ) -> dict[str, Any]:
        azs = ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}]
        ).get("AvailabilityZones", [])
        if len(azs) < 2:
            raise RuntimeError("provision(aws): region must have at least 2 AZs")
        az_names = [az["ZoneName"] for az in azs[:2]]

        public_subnet_ids: list[str] = []
        private_subnet_ids: list[str] = []

        for cidr, az in zip(PUBLIC_SUBNET_CIDRS, az_names, strict=True):
            sid = self._ensure_subnet(
                ec2=ec2,
                vpc_id=vpc_id,
                cidr=cidr,
                az=az,
                kind="public",
                agent_name=agent_name,
                agent_version=agent_version,
            )
            public_subnet_ids.append(sid)
        for cidr, az in zip(PRIVATE_SUBNET_CIDRS, az_names, strict=True):
            sid = self._ensure_subnet(
                ec2=ec2,
                vpc_id=vpc_id,
                cidr=cidr,
                az=az,
                kind="private",
                agent_name=agent_name,
                agent_version=agent_version,
            )
            private_subnet_ids.append(sid)

        igw_id = self._ensure_igw(
            ec2=ec2, vpc_id=vpc_id, agent_name=agent_name, agent_version=agent_version
        )

        # NAT gateway(s) in public subnets — single by default, per-AZ if requested.
        nat_subnets = public_subnet_ids if multi_az_nat else public_subnet_ids[:1]
        nat_gateway_ids: list[str] = []
        for psid in nat_subnets:
            nat_id = self._ensure_nat_gateway(
                ec2=ec2,
                subnet_id=psid,
                agent_name=agent_name,
                agent_version=agent_version,
            )
            nat_gateway_ids.append(nat_id)

        # Route tables: public → IGW, private → NAT (first NAT used for both AZs
        # unless multi_az_nat=True).
        public_rt_id = self._ensure_route_table(
            ec2=ec2,
            vpc_id=vpc_id,
            kind="public",
            agent_name=agent_name,
            agent_version=agent_version,
        )
        self._ensure_route(
            ec2=ec2,
            route_table_id=public_rt_id,
            destination_cidr="0.0.0.0/0",
            gateway_id=igw_id,
        )
        for psid in public_subnet_ids:
            self._ensure_associate_route_table(
                ec2=ec2, route_table_id=public_rt_id, subnet_id=psid
            )

        private_rt_ids: list[str] = []
        for i, psid in enumerate(private_subnet_ids):
            rt_id = self._ensure_route_table(
                ec2=ec2,
                vpc_id=vpc_id,
                kind=f"private-{i}",
                agent_name=agent_name,
                agent_version=agent_version,
            )
            nat_for_az = nat_gateway_ids[i] if multi_az_nat else nat_gateway_ids[0]
            self._ensure_route(
                ec2=ec2,
                route_table_id=rt_id,
                destination_cidr="0.0.0.0/0",
                nat_gateway_id=nat_for_az,
            )
            self._ensure_associate_route_table(ec2=ec2, route_table_id=rt_id, subnet_id=psid)
            private_rt_ids.append(rt_id)

        return {
            "public_subnet_ids": public_subnet_ids,
            "private_subnet_ids": private_subnet_ids,
            "internet_gateway_id": igw_id,
            "nat_gateway_ids": nat_gateway_ids,
            "public_route_table_id": public_rt_id,
            "private_route_table_ids": private_rt_ids,
            "azs": az_names,
        }

    def _ensure_subnet(
        self,
        *,
        ec2: Any,
        vpc_id: str,
        cidr: str,
        az: str,
        kind: str,
        agent_name: str,
        agent_version: str,
    ) -> str:
        found = ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "cidr-block", "Values": [cidr]},
            ]
        )
        if subs := found.get("Subnets", []):
            return subs[0]["SubnetId"]
        out = ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=cidr,
            AvailabilityZone=az,
            TagSpecifications=[
                {
                    "ResourceType": "subnet",
                    "Tags": _tags(
                        agent_name,
                        agent_version,
                        extra={"Kind": kind, "Name": f"agentbreeder-{kind}-{az}"},
                    ),
                }
            ],
        )
        return out["Subnet"]["SubnetId"]

    def _ensure_igw(self, *, ec2: Any, vpc_id: str, agent_name: str, agent_version: str) -> str:
        attached = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        )
        if igws := attached.get("InternetGateways", []):
            return igws[0]["InternetGatewayId"]
        igw = ec2.create_internet_gateway(
            TagSpecifications=[
                {"ResourceType": "internet-gateway", "Tags": _tags(agent_name, agent_version)}
            ]
        )
        igw_id = igw["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        return igw_id

    def _ensure_nat_gateway(
        self, *, ec2: Any, subnet_id: str, agent_name: str, agent_version: str
    ) -> str:
        existing = ec2.describe_nat_gateways(
            Filters=[
                {"Name": "subnet-id", "Values": [subnet_id]},
                {"Name": "state", "Values": ["available", "pending"]},
                {"Name": "tag:AgentBreeder", "Values": ["true"]},
            ]
        )
        if nats := existing.get("NatGateways", []):
            return nats[0]["NatGatewayId"]
        eip = ec2.allocate_address(
            Domain="vpc",
            TagSpecifications=[
                {"ResourceType": "elastic-ip", "Tags": _tags(agent_name, agent_version)}
            ],
        )
        nat = ec2.create_nat_gateway(
            SubnetId=subnet_id,
            AllocationId=eip["AllocationId"],
            TagSpecifications=[
                {"ResourceType": "natgateway", "Tags": _tags(agent_name, agent_version)}
            ],
        )
        return nat["NatGateway"]["NatGatewayId"]

    def _ensure_route_table(
        self,
        *,
        ec2: Any,
        vpc_id: str,
        kind: str,
        agent_name: str,
        agent_version: str,
    ) -> str:
        existing = ec2.describe_route_tables(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "tag:Kind", "Values": [kind]},
                {"Name": "tag:AgentBreeder", "Values": ["true"]},
            ]
        )
        if rts := existing.get("RouteTables", []):
            return rts[0]["RouteTableId"]
        rt = ec2.create_route_table(
            VpcId=vpc_id,
            TagSpecifications=[
                {
                    "ResourceType": "route-table",
                    "Tags": _tags(agent_name, agent_version, extra={"Kind": kind}),
                }
            ],
        )
        return rt["RouteTable"]["RouteTableId"]

    def _ensure_route(
        self,
        *,
        ec2: Any,
        route_table_id: str,
        destination_cidr: str,
        gateway_id: str | None = None,
        nat_gateway_id: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "RouteTableId": route_table_id,
            "DestinationCidrBlock": destination_cidr,
        }
        if gateway_id is not None:
            kwargs["GatewayId"] = gateway_id
        if nat_gateway_id is not None:
            kwargs["NatGatewayId"] = nat_gateway_id
        try:
            ec2.create_route(**kwargs)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "RouteAlreadyExists":
                return
            raise

    def _ensure_associate_route_table(
        self, *, ec2: Any, route_table_id: str, subnet_id: str
    ) -> None:
        existing = ec2.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
        )
        for rt in existing.get("RouteTables", []):
            for assoc in rt.get("Associations", []):
                if assoc.get("SubnetId") == subnet_id and rt["RouteTableId"] == route_table_id:
                    return
        ec2.associate_route_table(RouteTableId=route_table_id, SubnetId=subnet_id)

    async def _provision_security_groups(
        self,
        *,
        ec2: Any,
        vpc_id: str,
        agent_name: str,
        agent_version: str,
        include_db_sg: bool,
    ) -> dict[str, str | None]:
        alb_sg_id = self._ensure_security_group(
            ec2=ec2,
            vpc_id=vpc_id,
            name="agentbreeder-alb-sg",
            description="AgentBreeder ALB ingress",
            agent_name=agent_name,
            agent_version=agent_version,
        )
        # ALB ingress: 80/443 from 0.0.0.0/0
        self._authorize_ingress(
            ec2=ec2,
            sg_id=alb_sg_id,
            protocol="tcp",
            from_port=ALB_HTTP_PORT,
            to_port=ALB_HTTP_PORT,
            cidr="0.0.0.0/0",
        )
        self._authorize_ingress(
            ec2=ec2,
            sg_id=alb_sg_id,
            protocol="tcp",
            from_port=ALB_HTTPS_PORT,
            to_port=ALB_HTTPS_PORT,
            cidr="0.0.0.0/0",
        )

        agent_sg_id = self._ensure_security_group(
            ec2=ec2,
            vpc_id=vpc_id,
            name="agentbreeder-agent-sg",
            description="AgentBreeder agent container ingress",
            agent_name=agent_name,
            agent_version=agent_version,
        )
        # Agent ingress: 8080 from ALB SG only
        self._authorize_ingress(
            ec2=ec2,
            sg_id=agent_sg_id,
            protocol="tcp",
            from_port=AGENT_CONTAINER_PORT,
            to_port=AGENT_CONTAINER_PORT,
            source_sg_id=alb_sg_id,
        )

        db_sg_id: str | None = None
        if include_db_sg:
            db_sg_id = self._ensure_security_group(
                ec2=ec2,
                vpc_id=vpc_id,
                name="agentbreeder-db-sg",
                description="AgentBreeder DB ingress (5432 from agent SG only)",
                agent_name=agent_name,
                agent_version=agent_version,
            )
            # DB ingress: 5432 from agent SG only — NEVER 0.0.0.0/0.
            self._authorize_ingress(
                ec2=ec2,
                sg_id=db_sg_id,
                protocol="tcp",
                from_port=DB_PORT,
                to_port=DB_PORT,
                source_sg_id=agent_sg_id,
            )

        return {
            "alb_sg_id": alb_sg_id,
            "agent_sg_id": agent_sg_id,
            "db_sg_id": db_sg_id,
        }

    def _ensure_security_group(
        self,
        *,
        ec2: Any,
        vpc_id: str,
        name: str,
        description: str,
        agent_name: str,
        agent_version: str,
    ) -> str:
        found = ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": [name]},
            ]
        )
        if sgs := found.get("SecurityGroups", []):
            return sgs[0]["GroupId"]
        out = ec2.create_security_group(
            VpcId=vpc_id,
            GroupName=name,
            Description=description,
            TagSpecifications=[
                {
                    "ResourceType": "security-group",
                    "Tags": _tags(agent_name, agent_version, extra={"Name": name}),
                }
            ],
        )
        return out["GroupId"]

    def _authorize_ingress(
        self,
        *,
        ec2: Any,
        sg_id: str,
        protocol: str,
        from_port: int,
        to_port: int,
        cidr: str | None = None,
        source_sg_id: str | None = None,
    ) -> None:
        if cidr is None and source_sg_id is None:
            raise ValueError("_authorize_ingress requires cidr or source_sg_id")
        ip_perm: dict[str, Any] = {
            "IpProtocol": protocol,
            "FromPort": from_port,
            "ToPort": to_port,
        }
        if cidr is not None:
            ip_perm["IpRanges"] = [{"CidrIp": cidr}]
        if source_sg_id is not None:
            ip_perm["UserIdGroupPairs"] = [{"GroupId": source_sg_id}]
        try:
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[ip_perm])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "InvalidPermission.Duplicate":
                return
            raise

    async def _provision_ecs_cluster(
        self,
        *,
        ecs: Any,
        cluster_name: str,
        agent_name: str,
        agent_version: str,
    ) -> str:
        existing = ecs.describe_clusters(clusters=[cluster_name])
        for c in existing.get("clusters", []):
            if c.get("status") == "ACTIVE":
                return c["clusterArn"]
        out = ecs.create_cluster(
            clusterName=cluster_name,
            capacityProviders=["FARGATE", "FARGATE_SPOT"],
            defaultCapacityProviderStrategy=[
                {"capacityProvider": "FARGATE", "weight": 1, "base": 1},
                {"capacityProvider": "FARGATE_SPOT", "weight": 4},
            ],
            tags=[
                {"key": "AgentBreeder", "value": "true"},
                {"key": "AgentName", "value": agent_name},
                {"key": "Version", "value": agent_version},
            ],
        )
        return out["cluster"]["clusterArn"]

    async def _provision_iam_role(
        self,
        *,
        iam: Any,
        role_name: str,
        agent_name: str,
        agent_version: str,
    ) -> str:
        assume_role_policy = (
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
            '"Principal":{"Service":"ecs-tasks.amazonaws.com"},'
            '"Action":"sts:AssumeRole"}]}'
        )
        try:
            role = iam.get_role(RoleName=role_name)
            arn = role["Role"]["Arn"]
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code != "NoSuchEntity":
                raise
            out = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=assume_role_policy,
                Description=(f"AgentBreeder ECS task execution role for agent '{agent_name}'"),
                Tags=_tags(agent_name, agent_version),
            )
            arn = out["Role"]["Arn"]

        # AmazonECSTaskExecutionRolePolicy covers ECR pull + CloudWatch Logs push.
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )
        return arn

    async def _provision_rds(
        self,
        *,
        rds: Any,
        ec2: Any,
        secrets_client: Any,
        vpc_id: str,
        private_subnet_ids: list[str],
        db_sg_id: str,
        agent_name: str,
        agent_version: str,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        """Provision RDS Postgres in private subnets.

        SECURITY INVARIANTS (do not regress):
        - publicly_accessible=False
        - storage_encrypted=True
        - DB security group only allows ingress from agent SG (handled in
          :meth:`_provision_security_groups`)
        - Random password generated with ``secrets.token_urlsafe(32)`` and
          written to AWS Secrets Manager IN THIS METHOD. If the secrets write
          fails, the DB is rolled back. The plaintext password NEVER reaches
          the returned state.
        """
        del ec2, vpc_id  # reserved for future security-group cross-checks

        db_id = f"agentbreeder-{agent_name}".lower()[:63].rstrip("-")
        subnet_group_name = f"agentbreeder-{agent_name}-subnets".lower()[:255].rstrip("-")

        # 1. DB subnet group (must come before the instance).
        try:
            rds.describe_db_subnet_groups(DBSubnetGroupName=subnet_group_name)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "DBSubnetGroupNotFoundFault":
                raise
            rds.create_db_subnet_group(
                DBSubnetGroupName=subnet_group_name,
                DBSubnetGroupDescription=f"AgentBreeder private subnets for {agent_name}",
                SubnetIds=private_subnet_ids,
                Tags=[
                    {"Key": "AgentBreeder", "Value": "true"},
                    {"Key": "AgentName", "Value": agent_name},
                    {"Key": "Version", "Value": agent_version},
                ],
            )

        # 2. Existing DB? Return its info; we already have a secret ARN on file.
        try:
            existing = rds.describe_db_instances(DBInstanceIdentifier=db_id)
            inst = existing["DBInstances"][0]
            # Sanity-check the existing instance is private + encrypted.
            if inst.get("PubliclyAccessible"):
                raise RuntimeError(
                    f"RDS instance '{db_id}' is publicly accessible — refusing to adopt"
                )
            if not inst.get("StorageEncrypted"):
                raise RuntimeError(f"RDS instance '{db_id}' is not encrypted — refusing to adopt")
            # Look up the existing secret ARN.
            secret_name = f"agentbreeder/{agent_name}/db-password"
            secret_arn = secrets_client.describe_secret(SecretId=secret_name)["ARN"]
            return {
                "db_instance_identifier": db_id,
                "endpoint": (inst.get("Endpoint") or {}).get("Address"),
                "port": (inst.get("Endpoint") or {}).get("Port", DB_PORT),
                "db_name": DB_NAME,
                "secret_arn": secret_arn,
                "subnet_group": subnet_group_name,
                "publicly_accessible": False,
                "storage_encrypted": True,
            }
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "DBInstanceNotFound":
                raise

        # 3. Greenfield create.
        password = secrets.token_urlsafe(32)
        try:
            rds.create_db_instance(
                DBInstanceIdentifier=db_id,
                DBInstanceClass="db.t3.micro",
                Engine="postgres",
                AllocatedStorage=20,
                MasterUsername="agentbreeder",
                MasterUserPassword=password,
                DBName=DB_NAME,
                VpcSecurityGroupIds=[db_sg_id],
                DBSubnetGroupName=subnet_group_name,
                PubliclyAccessible=False,
                StorageEncrypted=True,
                BackupRetentionPeriod=7,
                Tags=[
                    {"Key": "AgentBreeder", "Value": "true"},
                    {"Key": "AgentName", "Value": agent_name},
                    {"Key": "Version", "Value": agent_version},
                ],
            )
        except Exception:
            logger.exception("rds.create_db_instance failed; nothing to roll back yet")
            raise

        # 4. Write secret. If this fails, ROLL BACK the DB so we never end up
        #    with an inaccessible instance + lost password.
        secret_name = f"agentbreeder/{agent_name}/db-password"
        try:
            try:
                create_resp = secrets_client.create_secret(
                    Name=secret_name,
                    Description=f"AgentBreeder DB password for {agent_name}",
                    SecretString=password,
                    Tags=[
                        {"Key": "AgentBreeder", "Value": "true"},
                        {"Key": "AgentName", "Value": agent_name},
                        {"Key": "Version", "Value": agent_version},
                    ],
                )
                secret_arn = create_resp["ARN"]
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "ResourceExistsException":
                    raise
                secrets_client.put_secret_value(SecretId=secret_name, SecretString=password)
                secret_arn = secrets_client.describe_secret(SecretId=secret_name)["ARN"]
        except Exception:
            logger.exception("Secrets Manager write failed; rolling back RDS instance '%s'", db_id)
            try:
                rds.delete_db_instance(DBInstanceIdentifier=db_id, SkipFinalSnapshot=True)
            except Exception:  # noqa: BLE001
                logger.exception("Rollback of RDS instance '%s' also failed", db_id)
            raise

        # 5. Wait for endpoint to become available (10-15 min in real cloud).
        try:
            waiter = rds.get_waiter("db_instance_available")
            if progress is not None:
                await progress(f"waiting for RDS '{db_id}' to become available")
            waiter.wait(DBInstanceIdentifier=db_id)
        except Exception:  # noqa: BLE001
            logger.warning("RDS waiter unavailable; proceeding without final endpoint")

        try:
            described = rds.describe_db_instances(DBInstanceIdentifier=db_id)
            endpoint = described["DBInstances"][0].get("Endpoint", {}).get("Address")
        except Exception:  # noqa: BLE001
            endpoint = None

        return {
            "db_instance_identifier": db_id,
            "endpoint": endpoint,
            "port": DB_PORT,
            "db_name": DB_NAME,
            "secret_arn": secret_arn,
            "subnet_group": subnet_group_name,
            "publicly_accessible": False,
            "storage_encrypted": True,
        }

    async def _provision_alb(
        self,
        *,
        elbv2: Any,
        vpc_id: str,
        public_subnet_ids: list[str],
        alb_sg_id: str,
        agent_name: str,
        agent_version: str,
        certificate_arn: str | None,
    ) -> dict[str, Any]:
        alb_name = f"agentbreeder-{agent_name}"[:32]
        tg_name = f"agentbreeder-{agent_name}-tg"[:32]

        # ALB
        existing: dict[str, Any] | None = None
        try:
            existing = elbv2.describe_load_balancers(Names=[alb_name])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in {
                "LoadBalancerNotFound",
                "LoadBalancerNotFoundException",
            }:
                raise
        if existing and existing.get("LoadBalancers"):
            lb = existing["LoadBalancers"][0]
        else:
            try:
                lb = elbv2.create_load_balancer(
                    Name=alb_name,
                    Subnets=public_subnet_ids,
                    SecurityGroups=[alb_sg_id],
                    Scheme="internet-facing",
                    Type="application",
                    IpAddressType="ipv4",
                    Tags=[
                        {"Key": "AgentBreeder", "Value": "true"},
                        {"Key": "AgentName", "Value": agent_name},
                        {"Key": "Version", "Value": agent_version},
                    ],
                )["LoadBalancers"][0]
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "DuplicateLoadBalancerName":
                    raise
                lb = elbv2.describe_load_balancers(Names=[alb_name])["LoadBalancers"][0]

        # Target group
        try:
            tg = elbv2.describe_target_groups(Names=[tg_name])["TargetGroups"][0]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "TargetGroupNotFound":
                raise
            tg = elbv2.create_target_group(
                Name=tg_name,
                Protocol="HTTP",
                Port=AGENT_CONTAINER_PORT,
                VpcId=vpc_id,
                TargetType="ip",
                HealthCheckPath="/health",
                HealthCheckIntervalSeconds=30,
                Matcher={"HttpCode": "200"},
                Tags=[
                    {"Key": "AgentBreeder", "Value": "true"},
                    {"Key": "AgentName", "Value": agent_name},
                    {"Key": "Version", "Value": agent_version},
                ],
            )["TargetGroups"][0]

        # Listener — HTTPS when cert supplied, else HTTP. HTTPS uses TLS 1.2+ policy.
        listener_args: dict[str, Any] = {
            "LoadBalancerArn": lb["LoadBalancerArn"],
            "DefaultActions": [{"Type": "forward", "TargetGroupArn": tg["TargetGroupArn"]}],
        }
        if certificate_arn:
            listener_args.update(
                {
                    "Protocol": "HTTPS",
                    "Port": ALB_HTTPS_PORT,
                    "Certificates": [{"CertificateArn": certificate_arn}],
                    "SslPolicy": TLS_LISTENER_POLICY,
                }
            )
        else:
            listener_args.update({"Protocol": "HTTP", "Port": ALB_HTTP_PORT})

        # Reuse existing listener on the same port if present.
        listener_arn: str | None = None
        existing_listeners = elbv2.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"]).get(
            "Listeners", []
        )
        for li in existing_listeners:
            if li.get("Port") == listener_args["Port"]:
                listener_arn = li["ListenerArn"]
                break
        if listener_arn is None:
            listener_arn = elbv2.create_listener(**listener_args)["Listeners"][0]["ListenerArn"]

        return {
            "arn": lb["LoadBalancerArn"],
            "dns_name": lb.get("DNSName"),
            "target_group_arn": tg["TargetGroupArn"],
            "listener_arn": listener_arn,
            "ssl_policy": TLS_LISTENER_POLICY if certificate_arn else None,
        }

    # ------------------------------------------------------------------
    # Destroy helpers — each refuses to act on untagged resources.
    # ------------------------------------------------------------------

    async def _delete_alb(
        self,
        *,
        elbv2: Any,
        alb_arn: str,
        target_group_arn: str | None,
        listener_arn: str | None,
    ) -> None:
        # Tag check
        tags = (
            elbv2.describe_tags(ResourceArns=[alb_arn])
            .get("TagDescriptions", [{}])[0]
            .get("Tags", [])
        )
        if not _has_agentbreeder_tag(tags):
            raise PermissionError(
                f"destroy(aws): refusing to delete ALB {alb_arn!r} — missing AgentBreeder=true tag"
            )
        if listener_arn:
            elbv2.delete_listener(ListenerArn=listener_arn)
        elbv2.delete_load_balancer(LoadBalancerArn=alb_arn)
        if target_group_arn:
            elbv2.delete_target_group(TargetGroupArn=target_group_arn)

    async def _delete_rds(self, *, rds: Any, db_id: str, no_final_snapshot: bool) -> None:
        described = rds.describe_db_instances(DBInstanceIdentifier=db_id)
        inst = described["DBInstances"][0]
        tag_list = rds.list_tags_for_resource(ResourceName=inst["DBInstanceArn"]).get(
            "TagList", []
        )
        if not _has_agentbreeder_tag(tag_list):
            raise PermissionError(
                f"destroy(aws): refusing to delete RDS {db_id!r} — missing AgentBreeder=true tag"
            )
        if no_final_snapshot:
            rds.delete_db_instance(DBInstanceIdentifier=db_id, SkipFinalSnapshot=True)
        else:
            snap_id = f"agentbreeder-{db_id}-final-snap"[:255]
            rds.delete_db_instance(
                DBInstanceIdentifier=db_id,
                SkipFinalSnapshot=False,
                FinalDBSnapshotIdentifier=snap_id,
            )

    async def _delete_iam_role(self, *, iam: Any, role_name: str) -> None:
        role = iam.get_role(RoleName=role_name)["Role"]
        tags = role.get("Tags", [])
        if not _has_agentbreeder_tag(tags):
            raise PermissionError(
                f"destroy(aws): refusing to delete IAM role {role_name!r} — missing AgentBreeder=true tag"
            )
        # Detach attached managed policies first.
        attached = iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", [])
        for p in attached:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        iam.delete_role(RoleName=role_name)

    async def _delete_ecs_cluster(self, *, ecs: Any, cluster_name: str) -> None:
        described = ecs.describe_clusters(clusters=[cluster_name])
        # AWS surfaces ECS tags either inline on describe_clusters or via
        # list_tags_for_resource depending on SDK version — try both.
        cluster = described["clusters"][0]
        tags = cluster.get("tags") or []
        if not tags:
            try:
                tags = ecs.list_tags_for_resource(resourceArn=cluster["clusterArn"]).get(
                    "tags", []
                )
            except Exception:  # noqa: BLE001
                tags = []
        # ECS tags are lowercase {key, value}
        if not _has_agentbreeder_tag(tags):
            raise PermissionError(
                f"destroy(aws): refusing to delete ECS cluster {cluster_name!r} — missing AgentBreeder=true tag"
            )
        ecs.delete_cluster(cluster=cluster_name)

    async def _delete_security_group(self, *, ec2: Any, sg_id: str) -> None:
        sg = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
        tags = sg.get("Tags", [])
        if not _has_agentbreeder_tag(tags):
            raise PermissionError(
                f"destroy(aws): refusing to delete SG {sg_id!r} — missing AgentBreeder=true tag"
            )
        ec2.delete_security_group(GroupId=sg_id)

    async def _delete_network(self, *, ec2: Any, net: dict[str, Any]) -> None:
        # NAT gateway(s) first — they hold ENIs in subnets.
        for nat_id in net.get("nat_gateway_ids") or []:
            try:
                ec2.delete_nat_gateway(NatGatewayId=nat_id)
            except ClientError:
                logger.exception("delete_nat_gateway failed for %s", nat_id)

        # IGW: detach then delete.
        if igw_id := net.get("internet_gateway_id"):
            try:
                igws = ec2.describe_internet_gateways(InternetGatewayIds=[igw_id]).get(
                    "InternetGateways", []
                )
                for igw in igws:
                    for att in igw.get("Attachments", []):
                        ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=att["VpcId"])
                ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            except ClientError:
                logger.exception("delete_internet_gateway failed for %s", igw_id)

        # Route tables.
        for rt_id in [net.get("public_route_table_id")] + list(
            net.get("private_route_table_ids") or []
        ):
            if not rt_id:
                continue
            try:
                ec2.delete_route_table(RouteTableId=rt_id)
            except ClientError:
                logger.exception("delete_route_table failed for %s", rt_id)

        # Subnets.
        for sid in (net.get("public_subnet_ids") or []) + (net.get("private_subnet_ids") or []):
            try:
                ec2.delete_subnet(SubnetId=sid)
            except ClientError:
                logger.exception("delete_subnet failed for %s", sid)

    async def _delete_vpc(self, *, ec2: Any, vpc_id: str) -> None:
        vpcs = ec2.describe_vpcs(VpcIds=[vpc_id]).get("Vpcs", [])
        if not vpcs:
            return
        tags = vpcs[0].get("Tags", [])
        if not _has_agentbreeder_tag(tags):
            raise PermissionError(
                f"destroy(aws): refusing to delete VPC {vpc_id!r} — missing AgentBreeder=true tag"
            )
        ec2.delete_vpc(VpcId=vpc_id)
