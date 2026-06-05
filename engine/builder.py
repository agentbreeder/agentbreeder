"""Deploy engine — the orchestrator.

Runs the 8-step deploy pipeline:
  1. Parse & Validate YAML
  2. RBAC Check
  3. Dependency Resolution
  4. Container Build
  5. Infrastructure Provision
  6. Deploy & Health Check
  7. Auto-Register in Registry
  8. Return Endpoint URL

Every step is atomic. If any step fails, the deploy rolls back.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from engine.config_parser import AgentConfig, CloudType, parse_config
from engine.deployers import get_deployer
from engine.deployers._autoprovision import (
    build_data_backend_request,
    needs_managed_memory_redis,
    resolve_pgvector_dsn,
    resolve_redis_url,
)
from engine.deployers._greenfield import infra_state_to_env
from engine.deployers._pgvector_dsn import (
    needs_managed_memory_postgres,
    needs_managed_pgvector,
)
from engine.deployers.base import DeployResult
from engine.governance import check_rbac
from engine.provisioners import InfraValidationInput, provisioner_for
from engine.resolver import resolve_dependencies
from engine.runtimes.registry import get_runtime_from_config

logger = logging.getLogger(__name__)

REGISTRY_DIR = Path.home() / ".agentbreeder" / "registry"


def _merge_infra_resources(into: dict[str, Any], extra: dict[str, Any]) -> None:
    """Merge a provisioner's resources dict into an accumulator, in place.

    Top-level keys are copied; the ``security_groups`` sub-dict is deep-merged so
    a Postgres provision (``db_sg_id``) and a Redis provision (``redis_sg_id``)
    in the same deploy both survive into one InfraState.
    """
    for key, value in extra.items():
        if key == "security_groups" and isinstance(value, dict):
            existing = into.setdefault("security_groups", {})
            existing.update(value)
        else:
            into[key] = value


class DeployError(Exception):
    """Raised when a deployment fails."""


class BuildError(Exception):
    """Raised when a container build fails."""


class PipelineStep:
    """Tracks progress of a deploy pipeline step."""

    def __init__(self, name: str, step_number: int, total_steps: int = 8) -> None:
        self.name = name
        self.step_number = step_number
        self.total_steps = total_steps
        self.status = "pending"
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.error: str | None = None

    def start(self) -> None:
        self.status = "running"
        self.started_at = datetime.now()
        logger.info("[%d/%d] %s...", self.step_number, self.total_steps, self.name)

    def complete(self) -> None:
        self.status = "completed"
        self.completed_at = datetime.now()
        logger.info("[%d/%d] %s — done", self.step_number, self.total_steps, self.name)

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.error = error
        self.completed_at = datetime.now()
        logger.error(
            "[%d/%d] %s — FAILED: %s", self.step_number, self.total_steps, self.name, error
        )


def _aws_greenfield_fields(fields: dict[str, Any], config: AgentConfig, region: str) -> None:
    fields.setdefault("AWS_AGENT_NAME", config.name)
    fields.setdefault("AWS_AGENT_VERSION", config.version)
    fields.setdefault("AWS_DEFAULT_REGION", region)
    # CLI deploys reach the task at its public IP (assignPublicIp), no ALB.
    fields.setdefault("AWS_AGENT_PUBLIC_INGRESS", "true")


def _gcp_greenfield_fields(fields: dict[str, Any], config: AgentConfig, region: str) -> None:
    fields.setdefault("GCP_AGENT_NAME", config.name)
    fields.setdefault("GCP_AGENT_VERSION", config.version)
    fields.setdefault("GCP_REGION", region)
    # provision(gcp) requires GOOGLE_CLOUD_PROJECT; recover it from GCP_PROJECT_ID.
    if not fields.get("GOOGLE_CLOUD_PROJECT") and fields.get("GCP_PROJECT_ID"):
        fields["GOOGLE_CLOUD_PROJECT"] = fields["GCP_PROJECT_ID"]
    # Create a dedicated VPC + Serverless connector so the agent and data tier
    # share a private network (matches the AWS VPC story).
    fields.setdefault("GCP_PROVISION_VPC", "true")
    fields.setdefault("GCP_PROVISION_VPC_CONNECTOR", "true")


def _azure_greenfield_fields(fields: dict[str, Any], config: AgentConfig, region: str) -> None:
    fields.setdefault("AZURE_AGENT_NAME", config.name)
    fields.setdefault("AZURE_AGENT_VERSION", config.version)
    fields.setdefault("AZURE_LOCATION", region)


# Per-cloud greenfield descriptors for ``agentbreeder deploy --provision``
# (#537, multi-cloud parity #505). Each maps a cloud to the provisioner provider
# string, the env keys that mean "BYO infra already supplied" (skip greenfield),
# the InfraState.resources key that confirms a prior greenfield footprint, the
# default region + region env key, and the field setter applied before provision.
_GREENFIELD_SPECS: dict[CloudType, dict[str, Any]] = {
    CloudType.aws: {
        "provider": "aws",
        "byo_keys": ("AWS_ECS_CLUSTER", "AWS_VPC_SUBNETS"),
        "reuse_key": "ecs_cluster",
        "default_region": "us-east-1",
        "region_env": "AWS_REGION",
        "set_fields": _aws_greenfield_fields,
    },
    CloudType.gcp: {
        "provider": "gcp",
        "byo_keys": ("GCP_ARTIFACT_REGISTRY_REPO", "GCP_SERVICE_ACCOUNT"),
        "reuse_key": "service_account",
        "default_region": "us-central1",
        "region_env": "GCP_REGION",
        "set_fields": _gcp_greenfield_fields,
    },
    CloudType.azure: {
        "provider": "azure",
        "byo_keys": ("AZURE_RESOURCE_GROUP", "AZURE_CONTAINER_APPS_ENV", "AZURE_REGISTRY_SERVER"),
        "reuse_key": "container_apps_environment",
        "default_region": "eastus",
        "region_env": "AZURE_LOCATION",
        "set_fields": _azure_greenfield_fields,
    },
}


class DeployEngine:
    """Orchestrates the full deploy pipeline."""

    def __init__(self, on_step: Any = None) -> None:
        """Initialize the deploy engine.

        Args:
            on_step: Optional callback(step: PipelineStep) called when step status changes.
        """
        self._on_step = on_step

    def _notify(self, step: PipelineStep) -> None:
        if self._on_step:
            self._on_step(step)

    async def deploy(
        self,
        config_path: Path,
        target: str | None = None,
        user: str = "local",
        provision: bool = False,
    ) -> DeployResult:
        """Run the full deploy pipeline.

        When ``provision`` is set (``agentbreeder deploy --provision``), Step 5
        greenfield-provisions the cloud footprint (AWS only for now, #537) and
        injects its IDs into ``deploy.env_vars`` before the deployer runs, so a
        fresh account needs no pre-existing VPC/cluster/role.
        """
        # Absolutize early so config_path.parent is always an absolute project root,
        # regardless of whether the CLI, API, or orchestrator passed a relative path.
        config_path = config_path.resolve()

        deployer = None
        config: AgentConfig | None = None

        # Step 1: Parse & Validate
        step1 = PipelineStep("Parse & validate YAML", 1)
        step1.start()
        self._notify(step1)
        try:
            config = parse_config(config_path)
            step1.complete()
            self._notify(step1)
        except Exception as e:
            step1.fail(str(e))
            self._notify(step1)
            raise

        # Override target if provided
        if target:
            # Handle runtime-specific targets that map to a cloud provider
            runtime_to_cloud: dict[str, tuple[str, str]] = {
                "cloud-run": ("gcp", "cloud-run"),
                "cloudrun": ("gcp", "cloud-run"),
                "ecs-fargate": ("aws", "ecs-fargate"),
            }
            if target in runtime_to_cloud:
                cloud, runtime = runtime_to_cloud[target]
                config.deploy.cloud = CloudType(cloud)
                config.deploy.runtime = runtime
            else:
                config.deploy.cloud = CloudType(target)

        # Step 2: RBAC Check
        step2 = PipelineStep("RBAC check", 2)
        step2.start()
        self._notify(step2)
        try:
            check_rbac(config, user)
            step2.complete()
            self._notify(step2)
        except Exception as e:
            step2.fail(str(e))
            self._notify(step2)
            raise

        # Step 3: Dependency Resolution
        step3 = PipelineStep("Resolve dependencies", 3)
        step3.start()
        self._notify(step3)
        try:
            config = resolve_dependencies(config, config_path.parent)
            step3.complete()
            self._notify(step3)
        except Exception as e:
            step3.fail(str(e))
            self._notify(step3)
            raise

        # Step 4: Container Build
        # Claude Managed Agents run on Anthropic's infrastructure — no container needed.
        step4 = PipelineStep("Build container", 4)
        step4.start()
        self._notify(step4)
        try:
            if config.deploy.cloud == CloudType.claude_managed:
                logger.info(
                    "Skipping container build for cloud: claude-managed — "
                    "Anthropic manages the runtime"
                )
                image = None
            else:
                runtime = get_runtime_from_config(config)
                validation = runtime.validate(config_path.parent, config)
                if not validation.valid:
                    raise BuildError("Validation failed:\n" + "\n".join(validation.errors))
                image = runtime.build(config_path.parent, config)
            step4.complete()
            self._notify(step4)
        except Exception as e:
            step4.fail(str(e))
            self._notify(step4)
            raise

        # Step 5: Infrastructure Provision
        step5 = PipelineStep("Provision infrastructure", 5)
        step5.start()
        self._notify(step5)
        try:
            # Greenfield: create the cloud footprint and inject its IDs into
            # deploy.env_vars BEFORE the deployer validates/uses them (#537).
            await self._maybe_provision_greenfield(config, config_path.parent, provision)
            deployer = get_deployer(config.deploy.cloud, config.deploy.runtime)
            await deployer.provision(config)
            # Auto-provision managed data backends (pgvector for a KB declared
            # without an explicit backend_url) INTO the agent's BYO network, and
            # inject the connection env so step 6 (which reads env_vars fresh)
            # passes it to the container. Part of "Provision infrastructure".
            await self._auto_provision_data_backends(config, config_path.parent)
            step5.complete()
            self._notify(step5)
        except Exception as e:
            step5.fail(str(e))
            self._notify(step5)
            raise

        # Step 6: Deploy & Health Check
        step6 = PipelineStep("Deploy & health check", 6)
        step6.start()
        self._notify(step6)
        try:
            result = await deployer.deploy(config, image)
            health = await deployer.health_check(result)
            if not health.healthy:
                await deployer.teardown(config.name)
                raise DeployError(
                    f"Health check failed for {config.name}. "
                    f"Checks: {health.checks}. Container has been stopped."
                )
            step6.complete()
            self._notify(step6)
        except Exception as e:
            step6.fail(str(e))
            self._notify(step6)
            raise

        # Step 7: Auto-Register in Registry
        step7 = PipelineStep("Register in registry", 7)
        step7.start()
        self._notify(step7)
        try:
            self._register(config, result.endpoint_url)
            step7.complete()
            self._notify(step7)
        except Exception as e:
            step7.fail(str(e))
            self._notify(step7)
            raise

        # Step 8: Return Endpoint
        step8 = PipelineStep("Return endpoint", 8)
        step8.start()
        self._notify(step8)
        step8.complete()
        self._notify(step8)

        logger.info("Deploy complete: %s → %s", config.name, result.endpoint_url)
        return result

    def _persist_infra_resources(
        self, project_dir: Path, cloud: str, region: str, new_resources: dict[str, Any]
    ) -> None:
        """Merge ``new_resources`` into ``.agentbreeder/infra-state.json``.

        Loads any existing footprint first so a greenfield network/cluster and a
        later auto-provisioned data tier both survive into one state file that
        ``agentbreeder teardown`` can fully reverse.
        """
        from datetime import UTC, datetime

        from engine.provisioners.state import InfraState

        state_path = project_dir / ".agentbreeder" / "infra-state.json"
        merged: dict[str, Any] = {}
        existing = InfraState.load_or_none(state_path)
        if existing is not None:
            _merge_infra_resources(merged, existing.resources)
        _merge_infra_resources(merged, new_resources)
        InfraState(
            cloud=cloud,
            region=region,
            provisioned_by="agentbreeder.DeployEngine",
            provisioned_at=datetime.now(UTC),
            mode="provisioned",
            resources=merged,
        ).save(state_path)

    async def _maybe_provision_greenfield(
        self, config: AgentConfig, project_dir: Path, provision: bool
    ) -> None:
        """Greenfield-provision the cloud footprint for ``--provision`` and feed
        its IDs into ``deploy.env_vars`` so the existing deploy path serves the
        agent into it. AWS only for now (#537); GCP/Azure greenfield ship via the
        Studio wizard.

        No-op unless ``provision`` is set. Respects BYO infra already supplied in
        ``env_vars`` and reuses a previously-recorded greenfield footprint rather
        than provisioning a duplicate.
        """
        if not provision:
            return

        cloud = config.deploy.cloud
        if cloud in (CloudType.local, CloudType.claude_managed):
            return  # nothing to provision

        spec = _GREENFIELD_SPECS.get(cloud)
        if spec is None:
            raise DeployError(f"--provision does not support cloud {cloud.value!r}.")
        provider = spec["provider"]

        from engine.provisioners.state import InfraState

        if config.deploy.env_vars is None:
            config.deploy.env_vars = {}
        env = config.deploy.env_vars

        # BYO infra already supplied → respect it, skip greenfield.
        if all(env.get(k) for k in spec["byo_keys"]):
            logger.info(
                "Existing %s infra in env_vars for '%s'; skipping greenfield provisioning",
                provider,
                config.name,
            )
            return

        state_path = project_dir / ".agentbreeder" / "infra-state.json"
        existing = InfraState.load_or_none(state_path)
        if (
            existing is not None
            and existing.mode == "provisioned"
            and existing.cloud == provider
            and spec["reuse_key"] in existing.resources
        ):
            logger.info("Reusing greenfield infra recorded at %s", state_path)
            state = existing
        else:
            region = config.deploy.region or env.get(spec["region_env"]) or spec["default_region"]
            fields = dict(env)  # carries creds + any user overrides
            spec["set_fields"](fields, config, region)

            async def _emit(msg: str) -> None:
                logger.info("greenfield(%s): %s", provider, msg)

            logger.info("Greenfield-provisioning %s footprint for '%s'", provider, config.name)
            payload = InfraValidationInput(
                cloud=provider, region=region, mode="simple", fields=fields
            )
            state = await provisioner_for(provider).provision(payload, progress=_emit)
            self._persist_infra_resources(project_dir, provider, region, state.resources)

        # Inject the provisioned IDs without clobbering anything the user set.
        for key, value in infra_state_to_env(provider, state).items():
            env.setdefault(key, value)

    async def _auto_provision_data_backends(
        self, config: AgentConfig, project_dir: Path | None = None
    ) -> None:
        """Provision managed data stores for artifacts declared without a backend_url.

        Covers, per managed cloud and into the agent's BYO network:

        * knowledge base → managed Postgres (pgvector) → ``KB_PGVECTOR_DSN``
        * ``memory.backend: postgresql`` → managed Postgres → ``DATABASE_URL`` +
          ``MEMORY_BACKEND=postgresql`` (shares the KB instance when both apply)
        * ``memory.backend: redis`` → managed Redis → ``REDIS_URL`` +
          ``MEMORY_BACKEND=redis``

        Provisioned footprints are merged into one ``.agentbreeder/infra-state.json``
        so ``agentbreeder teardown`` removes them all. No-op for
        local/kubernetes/claude-managed and for artifacts that already pin a
        ``backend_url``.
        """
        wants_kb = needs_managed_pgvector(config)
        wants_memory_pg = needs_managed_memory_postgres(config)
        wants_memory_redis = needs_managed_memory_redis(config)
        if not (wants_kb or wants_memory_pg or wants_memory_redis):
            return

        if config.deploy.env_vars is None:
            config.deploy.env_vars = {}
        env = config.deploy.env_vars
        merged_resources: dict[str, Any] = {}
        cloud: str | None = None
        region: str | None = None

        if wants_kb or wants_memory_pg:
            request = build_data_backend_request(config, engine="postgres")
            if request is not None:
                logger.info(
                    "Auto-provisioning managed Postgres for '%s' on %s (kb=%s, memory=%s)",
                    config.name,
                    request.cloud,
                    wants_kb,
                    wants_memory_pg,
                )
                state = await provisioner_for(request.cloud).provision_data_backend(request)
                cloud, region = request.cloud, request.region
                _merge_infra_resources(merged_resources, state.resources)
                dsn = await resolve_pgvector_dsn(request.cloud, state.resources, request.region)
                if dsn:
                    if wants_kb:
                        env["KB_PGVECTOR_DSN"] = dsn
                        logger.info("Injected KB_PGVECTOR_DSN for '%s'", config.name)
                    if wants_memory_pg:
                        env["DATABASE_URL"] = dsn
                        env.setdefault("MEMORY_BACKEND", "postgresql")
                        logger.info(
                            "Injected DATABASE_URL (memory=postgresql) for '%s'", config.name
                        )
                else:
                    logger.warning(
                        "Provisioned Postgres for '%s' but could not assemble its DSN", config.name
                    )

        if wants_memory_redis:
            request = build_data_backend_request(config, engine="redis")
            if request is not None:
                logger.info(
                    "Auto-provisioning managed Redis for '%s' on %s", config.name, request.cloud
                )
                state = await provisioner_for(request.cloud).provision_data_backend(request)
                cloud, region = request.cloud, request.region
                _merge_infra_resources(merged_resources, state.resources)
                url = await resolve_redis_url(request.cloud, state.resources, request.region)
                if url:
                    env["REDIS_URL"] = url
                    env.setdefault("MEMORY_BACKEND", "redis")
                    logger.info("Injected REDIS_URL (memory=redis) for '%s'", config.name)
                else:
                    logger.warning(
                        "Provisioned Redis for '%s' but could not assemble REDIS_URL", config.name
                    )

        # Persist the merged footprint so teardown can destroy everything created.
        # Merges with any greenfield footprint already recorded for this project.
        if project_dir is not None and merged_resources and cloud and region:
            self._persist_infra_resources(project_dir, cloud, region, merged_resources)

    def _register(self, config: AgentConfig, endpoint_url: str) -> None:
        """Register the agent in the local registry and sync to the AgentBreeder API.

        Step 1: Write to the local JSON file (original behaviour — always executed).
        Step 2: Best-effort upsert to the AgentBreeder API so that
                http://localhost:3001/agents reflects the newly deployed agent.
                If the API is offline this step logs a warning and continues —
                it must never cause the deploy to fail.
        """
        # ── Step 1: local JSON registry ──────────────────────────────────────
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

        registry_file = REGISTRY_DIR / "agents.json"
        registry: dict[str, Any] = {}
        if registry_file.exists():
            registry = json.loads(registry_file.read_text())

        entry: dict[str, Any] = {
            "name": config.name,
            "version": config.version,
            "description": config.description,
            "team": config.team,
            "owner": config.owner,
            "framework": config.framework.value
            if config.framework
            else (config.runtime.framework if config.runtime else "unknown"),
            "model_primary": config.model.primary,
            "model_fallback": config.model.fallback,
            "endpoint_url": endpoint_url,
            "tags": config.tags,
            "status": "running",
            "registered_at": datetime.now().isoformat(),
        }
        registry[config.name] = entry

        registry_file.write_text(json.dumps(registry, indent=2))
        logger.info("Registered agent '%s' in local registry", config.name)

        # ── Step 2: AgentBreeder API upsert (best-effort) ────────────────────────
        api_base = os.environ.get("AGENTBREEDER_API_URL", "http://localhost:8000")
        api_token = os.environ.get("AGENTBREEDER_API_TOKEN", "")
        self._sync_to_api(config, endpoint_url, api_base, api_token)

    def _sync_to_api(
        self, config: AgentConfig, endpoint_url: str, api_base: str, api_token: str = ""
    ) -> None:
        """Upsert the deployed agent into the AgentBreeder API.

        Uses a search-first strategy:
          GET  /api/v1/agents/search?q={name}  — find existing record
          PUT  /api/v1/agents/{id}             — update if found
          POST /api/v1/agents                  — create if not found

        Auth: if ``AGENTBREEDER_API_TOKEN`` is set in the env, attach it as a
        Bearer token. Without it the AgentBreeder API's auth gate (all 247 routes are
        gated) returns 401 and the sync is best-effort/skipped.

        All errors are caught so the deploy is never blocked.
        """
        base = api_base.rstrip("/")
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        try:
            with httpx.Client(timeout=5.0, headers=headers) as client:
                # Search for an existing agent with the exact name
                search_resp = client.get(
                    f"{base}/api/v1/agents/search",
                    params={"q": config.name},
                )
                search_resp.raise_for_status()
                results: list[dict[str, Any]] = search_resp.json().get("data", [])

                # Filter to an exact name match (search is substring-based)
                existing = next((r for r in results if r.get("name") == config.name), None)

                if existing:
                    agent_id = existing["id"]
                    put_resp = client.put(
                        f"{base}/api/v1/agents/{agent_id}",
                        json={
                            "version": config.version,
                            "description": config.description or "",
                            "endpoint_url": endpoint_url,
                            "status": "running",
                            "tags": config.tags,
                        },
                    )
                    put_resp.raise_for_status()
                    logger.info(
                        "Updated agent '%s' (id=%s) in AgentBreeder API",
                        config.name,
                        agent_id,
                    )
                else:
                    post_resp = client.post(
                        f"{base}/api/v1/agents",
                        json={
                            "name": config.name,
                            "version": config.version,
                            "description": config.description or "",
                            "team": config.team,
                            "owner": config.owner,
                            "framework": config.framework.value
                            if config.framework
                            else (config.runtime.framework if config.runtime else "unknown"),
                            "model_primary": config.model.primary,
                            "model_fallback": config.model.fallback,
                            "endpoint_url": endpoint_url,
                            "tags": config.tags,
                        },
                    )
                    post_resp.raise_for_status()
                    logger.info("Created agent '%s' in AgentBreeder API", config.name)

        except Exception as exc:  # noqa: BLE001 — best-effort; never break deploy
            logger.warning(
                "Could not sync agent '%s' to AgentBreeder API at %s: %s",
                config.name,
                api_base,
                exc,
            )
