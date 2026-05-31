"""GCP provisioner — validates BYO infra and greenfield-provisions per agent (#382)."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

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


def _select_private_ip(ip_addresses: Any) -> str | None:
    """Return the PRIVATE IP from a Cloud SQL instance's ``ip_addresses`` list.

    Robust to proto-plus enums (``ip.type_``) and plain objects (``ip.type``);
    matches on the enum *name* so it works without importing the SDK in tests.
    """
    for ip in ip_addresses or []:
        ip_type = getattr(ip, "type_", getattr(ip, "type", None))
        name = getattr(ip_type, "name", None) or str(ip_type)
        if name == "PRIVATE" or name.endswith(".PRIVATE"):
            return getattr(ip, "ip_address", None)
    return None


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

    # ------------------------------------------------------------------
    # Greenfield provisioning (#382)
    # ------------------------------------------------------------------

    async def provision(
        self,
        payload: InfraValidationInput,
        progress: ProgressCallback | None = None,
    ) -> InfraState:
        """Create the minimum-viable GCP footprint for an AgentBreeder Cloud Run deploy.

        Today: Artifact Registry repo + per-agent Service Account + 4 default
        IAM bindings (storage.objectViewer / cloudbuild.builds.builder /
        logging.logWriter / secretmanager.secretAccessor), plus an optional
        Serverless VPC Access connector (#436) when ``GCP_PROVISION_VPC_CONNECTOR``
        is set. All operations are idempotent — re-running is safe.

        When ``GCP_PROVISION_CLOUD_SQL`` is set the method also provisions a
        Cloud SQL Postgres instance (#435) with private IP through the VPC
        connector. The random database password is written to Secret Manager
        in the same call; ``InfraState.resources["cloud_sql"]`` records the
        instance connection name + secret ref only — the plaintext password
        never appears on disk.
        """
        from datetime import UTC, datetime

        from engine.provisioners.state import InfraState

        fields = payload.fields
        region = payload.region or fields.get("GCP_REGION", "us-central1")
        project = str(fields.get("GOOGLE_CLOUD_PROJECT", "")).strip()
        if not project:
            raise ValueError("provision(gcp): GOOGLE_CLOUD_PROJECT is required")

        agent_name = str(fields.get("GCP_AGENT_NAME", "agentbreeder-default"))
        repo_name = str(fields.get("GCP_ARTIFACT_REGISTRY_REPO", "agentbreeder"))
        roles: list[str] = list(
            fields.get(
                "GCP_DEFAULT_SA_ROLES",
                [
                    "roles/storage.objectViewer",
                    "roles/cloudbuild.builds.builder",
                    "roles/logging.logWriter",
                    "roles/secretmanager.secretAccessor",
                ],
            )
        )

        resources: dict[str, Any] = {}

        async def _emit(msg: str) -> None:
            logger.info("gcp.provision: %s", msg)
            if progress is not None:
                await progress(msg)

        # ---- 1. Artifact Registry repo ----------------------------------
        await _emit(f"ensuring Artifact Registry repo '{repo_name}' in {region}")
        await self._ensure_artifact_registry_repo(
            project=project, region=region, repo=repo_name, fields=fields
        )
        resources["artifact_registry"] = {
            "name": f"projects/{project}/locations/{region}/repositories/{repo_name}",
            "repo": repo_name,
            "region": region,
        }

        # ---- 2. Per-agent Service Account + IAM ------------------------
        sa_id = _truncate_sa_id(agent_name)
        sa_email = f"{sa_id}@{project}.iam.gserviceaccount.com"
        await _emit(f"ensuring Service Account '{sa_email}'")
        await self._ensure_service_account(
            project=project,
            sa_id=sa_id,
            agent_name=agent_name,
            fields=fields,
        )
        if roles:
            await _emit(f"binding {len(roles)} IAM role(s) to '{sa_email}': {', '.join(roles)}")
            await self._bind_iam_roles(
                project=project, sa_email=sa_email, roles=roles, fields=fields
            )
        resources["service_account"] = {
            "email": sa_email,
            "sa_id": sa_id,
            "roles": roles,
            "project": project,
        }

        # ---- 3. Serverless VPC Access Connector (optional, #436) -------
        if _should_provision_vpc_connector(fields):
            connector_id = _truncate_connector_id(agent_name)
            network = str(fields.get("GCP_VPC_NAME", "default"))
            await _emit(
                f"ensuring Serverless VPC Connector '{connector_id}' on network '{network}'"
            )
            connector_name = await self._ensure_vpc_connector(
                project=project,
                region=region,
                connector_id=connector_id,
                network=network,
                fields=fields,
            )
            resources["vpc_connector"] = {
                "name": connector_name,
                "connector_id": connector_id,
                "region": region,
                "network": network,
            }

        # ---- 4. Cloud SQL Postgres (optional, #435) --------------------
        if fields.get("GCP_PROVISION_CLOUD_SQL"):
            instance_id = _truncate_cloud_sql_instance_id(agent_name)
            tier = str(fields.get("GCP_CLOUD_SQL_TIER", "db-f1-micro"))
            db_name = str(fields.get("GCP_CLOUD_SQL_DATABASE", "agentbreeder_memory"))
            db_user = str(fields.get("GCP_CLOUD_SQL_USER", "agentbreeder"))
            vpc_network = resources.get("vpc_connector", {}).get("network") or str(
                fields.get("GCP_VPC_NAME", "default")
            )
            await _emit(f"ensuring Cloud SQL instance '{instance_id}' (tier={tier})")
            cloud_sql_info = await self._ensure_cloud_sql(
                project=project,
                region=region,
                instance_id=instance_id,
                tier=tier,
                db_name=db_name,
                db_user=db_user,
                vpc_network=vpc_network,
                fields=fields,
            )
            resources["cloud_sql"] = cloud_sql_info

        state = InfraState(
            cloud="gcp",
            region=region,
            provisioned_by="agentbreeder.GCPProvisioner",
            provisioned_at=datetime.now(UTC),
            mode="provisioned",
            resources=resources,
        )
        await _emit("provision complete")
        return state

    async def destroy(self, state: InfraState) -> None:
        """Reverse what :meth:`provision` created, in safe order."""
        if state.cloud != "gcp":
            raise ValueError(f"destroy(gcp): state.cloud is {state.cloud!r}, expected 'gcp'")

        resources = dict(state.resources)
        # Pull credentials from env (ADC) — destroy is rare enough that we don't
        # carry the original `fields` dict on InfraState. Operators using a
        # specific SA key for destroy should set GOOGLE_APPLICATION_CREDENTIALS.
        fields: dict[str, Any] = {}

        if sql := resources.get("cloud_sql"):
            if sql.get("instance_name"):
                try:
                    await self._delete_cloud_sql(
                        project=sql["project"],
                        instance_id=sql["instance_id"],
                        secret_name=sql.get("password_secret"),
                        fields=fields,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "destroy(gcp): failed to delete Cloud SQL %s", sql.get("instance_id")
                    )

        if vpc := resources.get("vpc_connector"):
            if vpc.get("name"):
                try:
                    await self._delete_vpc_connector(name=vpc["name"], fields=fields)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "destroy(gcp): failed to delete VPC connector %s", vpc.get("name")
                    )

        if sa := resources.get("service_account"):
            try:
                await self._delete_service_account(
                    project=sa["project"], sa_email=sa["email"], fields=fields
                )
            except Exception:  # noqa: BLE001
                logger.exception("destroy(gcp): failed to delete SA %s", sa.get("email"))

        if ar := resources.get("artifact_registry"):
            try:
                await self._delete_artifact_registry_repo(name=ar["name"], fields=fields)
            except Exception:  # noqa: BLE001
                logger.exception("destroy(gcp): failed to delete AR repo %s", ar.get("name"))

    async def provision_data_backend(
        self,
        request: DataBackendRequest,
        progress: ProgressCallback | None = None,
    ) -> InfraState:
        """Provision a Cloud SQL Postgres (pgvector) into the agent's BYO VPC.

        Unlike :meth:`provision`, this skips the Artifact Registry / Service
        Account scaffolding and only creates the Cloud SQL instance via
        :meth:`_ensure_cloud_sql` (which manages its own Secret Manager
        password). The instance gets a private IP on ``request.network`` —
        which must already have Private Service Access configured (the
        greenfield path or the operator sets this up).

        Returns an :class:`InfraState` whose ``resources["cloud_sql"]`` matches
        the greenfield shape, so ``pgvector_dsn_from_resources("gcp", ...)``
        and :meth:`destroy` consume it directly.
        """
        from datetime import UTC, datetime

        from engine.provisioners.state import InfraState

        if request.engine != "postgres":
            raise NotImplementedError(
                f"GCP provision_data_backend(engine={request.engine!r}) is not "
                "implemented yet (Memorystore Redis is tracked separately)."
            )

        fields = request.fields
        region = request.region
        project = str(
            fields.get("GCP_PROJECT_ID") or fields.get("GOOGLE_CLOUD_PROJECT") or ""
        ).strip()
        if not project:
            raise ValueError(
                "provision_data_backend(gcp) requires a project "
                "(GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT)"
            )

        vpc_network = str(request.network.get("vpc_network") or "default")
        instance_id = _truncate_cloud_sql_instance_id(request.agent_name)
        tier = str(fields.get("GCP_CLOUD_SQL_TIER", "db-f1-micro"))
        db_name = str(fields.get("GCP_CLOUD_SQL_DATABASE", "agentbreeder_memory"))
        db_user = str(fields.get("GCP_CLOUD_SQL_USER", "agentbreeder"))

        async def _emit(msg: str) -> None:
            logger.info("gcp.provision_data_backend: %s", msg)
            if progress is not None:
                await progress(msg)

        await _emit(
            f"ensuring Cloud SQL instance '{instance_id}' (tier={tier}) on '{vpc_network}'"
        )
        cloud_sql_info = await self._ensure_cloud_sql(
            project=project,
            region=region,
            instance_id=instance_id,
            tier=tier,
            db_name=db_name,
            db_user=db_user,
            vpc_network=vpc_network,
            fields=fields,
        )

        state = InfraState(
            cloud="gcp",
            region=region,
            provisioned_by="agentbreeder.GCPProvisioner.provision_data_backend",
            provisioned_at=datetime.now(UTC),
            mode="provisioned",
            resources={"cloud_sql": cloud_sql_info},
        )
        await _emit("data backend provision complete")
        return state

    # ------------------------------------------------------------------
    # Low-level GCP API wrappers — broken out so unit tests can patch them.
    # ------------------------------------------------------------------

    async def _ensure_artifact_registry_repo(
        self,
        *,
        project: str,
        region: str,
        repo: str,
        fields: dict[str, Any],
    ) -> None:
        from google.api_core.exceptions import AlreadyExists, NotFound
        from google.cloud import artifactregistry_v1

        creds = _credentials(fields)
        client = artifactregistry_v1.ArtifactRegistryAsyncClient(credentials=creds)
        parent = f"projects/{project}/locations/{region}"
        name = f"{parent}/repositories/{repo}"
        try:
            await client.get_repository(
                request=artifactregistry_v1.GetRepositoryRequest(name=name)
            )
            logger.debug("artifact registry repo %s already exists", repo)
            return
        except NotFound:
            pass
        try:
            await client.create_repository(
                request=artifactregistry_v1.CreateRepositoryRequest(
                    parent=parent,
                    repository_id=repo,
                    repository=artifactregistry_v1.Repository(
                        format_=artifactregistry_v1.Repository.Format.DOCKER,
                        description=f"AgentBreeder container images for {project}",
                    ),
                )
            )
        except AlreadyExists:
            logger.debug("artifact registry repo %s created concurrently", repo)

    async def _delete_artifact_registry_repo(self, *, name: str, fields: dict[str, Any]) -> None:
        from google.api_core.exceptions import NotFound
        from google.cloud import artifactregistry_v1

        creds = _credentials(fields)
        client = artifactregistry_v1.ArtifactRegistryAsyncClient(credentials=creds)
        try:
            op = await client.delete_repository(
                request=artifactregistry_v1.DeleteRepositoryRequest(name=name)
            )
            await op.result()
        except NotFound:
            logger.debug("artifact registry repo %s already absent", name)

    async def _ensure_service_account(
        self,
        *,
        project: str,
        sa_id: str,
        agent_name: str,
        fields: dict[str, Any],
    ) -> None:
        from google.api_core.exceptions import AlreadyExists, NotFound
        from google.cloud import iam_admin_v1

        creds = _credentials(fields)
        client = iam_admin_v1.IAMClient(credentials=creds)
        sa_email = f"{sa_id}@{project}.iam.gserviceaccount.com"
        sa_resource = f"projects/{project}/serviceAccounts/{sa_email}"
        try:
            client.get_service_account(name=sa_resource)
            logger.debug("service account %s already exists", sa_email)
            return
        except NotFound:
            pass
        try:
            client.create_service_account(
                name=f"projects/{project}",
                account_id=sa_id,
                service_account=iam_admin_v1.ServiceAccount(
                    display_name=f"AgentBreeder agent: {agent_name}",
                    description=(
                        f"Per-agent runtime identity for AgentBreeder agent '{agent_name}'"
                    ),
                ),
            )
        except AlreadyExists:
            logger.debug("service account %s created concurrently", sa_email)

    async def _delete_service_account(
        self, *, project: str, sa_email: str, fields: dict[str, Any]
    ) -> None:
        from google.api_core.exceptions import NotFound
        from google.cloud import iam_admin_v1

        creds = _credentials(fields)
        client = iam_admin_v1.IAMClient(credentials=creds)
        sa_resource = f"projects/{project}/serviceAccounts/{sa_email}"
        try:
            client.delete_service_account(name=sa_resource)
        except NotFound:
            logger.debug("service account %s already absent", sa_email)

    async def _ensure_cloud_sql(
        self,
        *,
        project: str,
        region: str,
        instance_id: str,
        tier: str,
        db_name: str,
        db_user: str,
        vpc_network: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a private-IP Cloud SQL Postgres instance, database, and user.

        Returns the InfraState record (instance connection name + Secret Manager
        ref). Idempotent: if the instance, database, and user already exist
        they are returned untouched. The generated password is written ONLY to
        Secret Manager — never to disk, logs, or the returned dict. If the
        Secret Manager write fails after a fresh instance was created, the
        method attempts to delete the instance before re-raising.
        """
        import secrets as _secrets

        from google.api_core.exceptions import AlreadyExists, NotFound
        from google.cloud import secretmanager, sql_v1

        creds = _credentials(fields)
        admin = sql_v1.SqlInstancesServiceClient(credentials=creds)
        db_client = sql_v1.SqlDatabasesServiceClient(credentials=creds)
        user_client = sql_v1.SqlUsersServiceClient(credentials=creds)

        secret_name = f"agentbreeder-{instance_id}-db-password"
        secret_resource = f"projects/{project}/secrets/{secret_name}"
        connection_name = f"{project}:{region}:{instance_id}"
        instance_resource_name = f"projects/{project}/locations/{region}/instances/{instance_id}"

        # ---- Instance ---------------------------------------------------
        instance_freshly_created = False
        try:
            admin.get(request=sql_v1.SqlInstancesGetRequest(project=project, instance=instance_id))
            logger.debug("cloud sql instance %s already exists", instance_id)
        except NotFound:
            instance_body = sql_v1.DatabaseInstance(
                name=instance_id,
                database_version=sql_v1.SqlDatabaseVersion.POSTGRES_15,
                region=region,
                settings=sql_v1.Settings(
                    tier=tier,
                    ip_configuration=sql_v1.IpConfiguration(
                        ipv4_enabled=False,
                        private_network=f"projects/{project}/global/networks/{vpc_network}",
                        require_ssl=True,
                    ),
                    backup_configuration=sql_v1.BackupConfiguration(enabled=True),
                    user_labels={
                        "agentbreeder": "true",
                        "agent_name": instance_id,
                    },
                ),
            )
            try:
                op = admin.insert(
                    request=sql_v1.SqlInstancesInsertRequest(project=project, body=instance_body)
                )
                _await_operation(op)
                instance_freshly_created = True
            except AlreadyExists:
                logger.debug("cloud sql instance %s created concurrently", instance_id)

        # ---- Database ---------------------------------------------------
        try:
            db_client.get(
                request=sql_v1.SqlDatabasesGetRequest(
                    project=project, instance=instance_id, database=db_name
                )
            )
        except NotFound:
            try:
                op = db_client.insert(
                    request=sql_v1.SqlDatabasesInsertRequest(
                        project=project,
                        instance=instance_id,
                        body=sql_v1.Database(name=db_name),
                    )
                )
                _await_operation(op)
            except AlreadyExists:
                logger.debug("cloud sql database %s/%s created concurrently", instance_id, db_name)

        # ---- User + password -------------------------------------------
        password = _secrets.token_urlsafe(32)
        user_existed = False
        existing_users = user_client.list(
            request=sql_v1.SqlUsersListRequest(project=project, instance=instance_id)
        )
        for u in getattr(existing_users, "items", []) or []:
            if u.name == db_user:
                user_existed = True
                break

        if user_existed:
            op = user_client.update(
                request=sql_v1.SqlUsersUpdateRequest(
                    project=project,
                    instance=instance_id,
                    name=db_user,
                    body=sql_v1.User(name=db_user, password=password),
                )
            )
            _await_operation(op)
        else:
            op = user_client.insert(
                request=sql_v1.SqlUsersInsertRequest(
                    project=project,
                    instance=instance_id,
                    body=sql_v1.User(name=db_user, password=password),
                )
            )
            _await_operation(op)

        # ---- Secret Manager — write password ---------------------------
        sm = secretmanager.SecretManagerServiceClient(credentials=creds)
        try:
            try:
                sm.get_secret(request={"name": secret_resource})
            except NotFound:
                sm.create_secret(
                    request={
                        "parent": f"projects/{project}",
                        "secret_id": secret_name,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            sm.add_secret_version(
                request={
                    "parent": secret_resource,
                    "payload": {"data": password.encode("utf-8")},
                }
            )
        except Exception:
            if instance_freshly_created:
                logger.exception(
                    "cloud sql: Secret Manager write failed — rolling back fresh instance %s",
                    instance_id,
                )
                with contextlib.suppress(Exception):
                    op = admin.delete(
                        request=sql_v1.SqlInstancesDeleteRequest(
                            project=project, instance=instance_id
                        )
                    )
                    _await_operation(op)
            raise

        # Capture the private IP so the deployer can build a KB_PGVECTOR_DSN /
        # MEMORY_DATABASE_URL without a Cloud SQL Auth Proxy. (#523)
        private_ip: str | None = None
        try:
            inst = admin.get(
                request=sql_v1.SqlInstancesGetRequest(project=project, instance=instance_id)
            )
            private_ip = _select_private_ip(getattr(inst, "ip_addresses", None))
        except Exception as exc:  # noqa: BLE001
            logger.warning("cloud sql: could not read private IP for %s: %s", instance_id, exc)

        return {
            "instance_id": instance_id,
            "instance_name": instance_resource_name,
            "connection_name": connection_name,
            "private_ip": private_ip,
            "database": db_name,
            "user": db_user,
            "tier": tier,
            "region": region,
            "project": project,
            "vpc_network": vpc_network,
            "password_secret": secret_resource,
        }

    async def _delete_cloud_sql(
        self,
        *,
        project: str,
        instance_id: str,
        secret_name: str | None,
        fields: dict[str, Any],
    ) -> None:
        from google.api_core.exceptions import NotFound
        from google.cloud import secretmanager, sql_v1

        creds = _credentials(fields)
        admin = sql_v1.SqlInstancesServiceClient(credentials=creds)
        try:
            op = admin.delete(
                request=sql_v1.SqlInstancesDeleteRequest(project=project, instance=instance_id)
            )
            _await_operation(op)
        except NotFound:
            logger.debug("cloud sql instance %s already absent", instance_id)

        if secret_name:
            sm = secretmanager.SecretManagerServiceClient(credentials=creds)
            try:
                sm.delete_secret(request={"name": secret_name})
            except NotFound:
                logger.debug("cloud sql secret %s already absent", secret_name)

    async def _ensure_vpc_connector(
        self,
        *,
        project: str,
        region: str,
        connector_id: str,
        network: str,
        fields: dict[str, Any],
    ) -> str:
        """Create a Serverless VPC Access connector if absent, return its full name.

        Idempotent: if the connector already exists it is returned untouched
        regardless of its current min/max/machine-type settings. Operators who
        need to resize must delete and re-provision.
        """
        from google.api_core.exceptions import AlreadyExists, NotFound
        from google.cloud import vpcaccess_v1

        creds = _credentials(fields)
        client = vpcaccess_v1.VpcAccessServiceAsyncClient(credentials=creds)
        parent = f"projects/{project}/locations/{region}"
        name = f"{parent}/connectors/{connector_id}"

        try:
            existing = await client.get_connector(
                request=vpcaccess_v1.GetConnectorRequest(name=name)
            )
            logger.debug("vpc connector %s already exists", connector_id)
            return existing.name
        except NotFound:
            pass

        ip_cidr = str(fields.get("GCP_VPC_CONNECTOR_IP_CIDR", "10.8.0.0/28"))
        min_instances = int(fields.get("GCP_VPC_CONNECTOR_MIN_INSTANCES", 2))
        max_instances = int(fields.get("GCP_VPC_CONNECTOR_MAX_INSTANCES", 3))
        machine_type = str(fields.get("GCP_VPC_CONNECTOR_MACHINE_TYPE", "e2-micro"))

        connector = vpcaccess_v1.Connector(
            network=network,
            ip_cidr_range=ip_cidr,
            min_instances=min_instances,
            max_instances=max_instances,
            machine_type=machine_type,
        )
        try:
            op = await client.create_connector(
                request=vpcaccess_v1.CreateConnectorRequest(
                    parent=parent,
                    connector_id=connector_id,
                    connector=connector,
                )
            )
            created = await op.result()
            return created.name
        except AlreadyExists:
            logger.debug("vpc connector %s created concurrently", connector_id)
            return name

    async def _delete_vpc_connector(self, *, name: str, fields: dict[str, Any]) -> None:
        from google.api_core.exceptions import NotFound
        from google.cloud import vpcaccess_v1

        creds = _credentials(fields)
        client = vpcaccess_v1.VpcAccessServiceAsyncClient(credentials=creds)
        try:
            op = await client.delete_connector(
                request=vpcaccess_v1.DeleteConnectorRequest(name=name)
            )
            await op.result()
        except NotFound:
            logger.debug("vpc connector %s already absent", name)

    async def _bind_iam_roles(
        self,
        *,
        project: str,
        sa_email: str,
        roles: list[str],
        fields: dict[str, Any],
    ) -> None:
        """Idempotently bind each role to the SA on the project IAM policy."""
        try:
            from googleapiclient import discovery
        except ImportError:
            logger.warning(
                "google-api-python-client not installed — skipping IAM binding for %s",
                sa_email,
            )
            return

        crm = discovery.build("cloudresourcemanager", "v1", cache_discovery=False)
        policy = (
            crm.projects()
            .getIamPolicy(resource=project, body={"options": {"requestedPolicyVersion": 1}})
            .execute()
        )
        member = f"serviceAccount:{sa_email}"
        mutated = False
        for role in roles:
            binding = next((b for b in policy.get("bindings", []) if b["role"] == role), None)
            if binding:
                if member not in binding["members"]:
                    binding["members"].append(member)
                    mutated = True
            else:
                policy.setdefault("bindings", []).append({"role": role, "members": [member]})
                mutated = True
        if mutated:
            crm.projects().setIamPolicy(resource=project, body={"policy": policy}).execute()


def _should_provision_vpc_connector(fields: dict[str, Any]) -> bool:
    """Trigger predicate: explicit opt-in, or implicit when Cloud SQL needs private IP.

    The Cloud SQL implicit branch is wired in #435; #436 alone honours only the
    explicit flag so the two PRs stack cleanly.
    """
    flag = fields.get("GCP_PROVISION_VPC_CONNECTOR")
    if flag in (True, 1, "1", "true", "True", "yes"):
        return True
    return bool(fields.get("GCP_PROVISION_CLOUD_SQL")) and bool(
        fields.get("GCP_CLOUD_SQL_PRIVATE_IP", True)
    )


def _truncate_connector_id(agent_name: str) -> str:
    """Serverless VPC Access connector IDs are 2-25 chars, lowercase letters/digits/hyphens.

    Mirrors the policy of :func:`_truncate_sa_id` so the connector and SA share
    a derivable identity per agent.
    """
    raw = f"ab-{agent_name[:20]}".rstrip("-").lower()
    safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in raw)
    safe = safe.strip("-")
    if len(safe) < 2:
        safe = (safe + "-c")[:25]
    return safe[:25]


def _await_operation(op: Any, timeout: float = 1800.0) -> Any:
    """Block until a Cloud SQL Admin long-running operation completes.

    Wraps the operation polling so unit tests can patch a single seam.
    Raises if the operation reports an error.
    """
    result = op
    if hasattr(op, "result"):
        result = op.result(timeout=timeout)
    if hasattr(result, "error") and getattr(result, "error", None):
        raise RuntimeError(f"cloud sql operation failed: {result.error}")
    return result


def _truncate_cloud_sql_instance_id(agent_name: str) -> str:
    """Cloud SQL instance IDs are 1-98 chars, lowercase letters/digits/hyphens.

    Suffix with ``-memory`` per the issue's resource-naming contract. Caps at
    50 chars so the suffix always fits even after agent-name sanitisation.
    """
    base = agent_name[:40].lower()
    safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in base)
    safe = safe.strip("-") or "default"
    return f"{safe}-memory"[:50]


def _truncate_sa_id(agent_name: str) -> str:
    """GCP Service Account IDs are 6-30 chars, lowercase letters/digits/hyphens.

    Mirrors the convention in :mod:`engine.deployers.identity` so a future
    consolidation drops one of the two helpers without changing behaviour.
    """
    raw = f"ab-{agent_name[:24]}".rstrip("-").lower()
    safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in raw)
    safe = safe.strip("-")
    return safe[:30] if len(safe) >= 6 else (safe + "-default")[:30]
