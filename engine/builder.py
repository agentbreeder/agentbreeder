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
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.config_parser import AgentConfig, CloudType, parse_config
from engine.deployers import get_deployer
from engine.deployers.base import DeployResult
from engine.governance import check_rbac
from engine.resolver import resolve_dependencies
from engine.runtimes import get_runtime

logger = logging.getLogger(__name__)

REGISTRY_DIR = Path.home() / ".garden" / "registry"


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
    ) -> DeployResult:
        """Run the full deploy pipeline."""
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
            config = resolve_dependencies(config)
            step3.complete()
            self._notify(step3)
        except Exception as e:
            step3.fail(str(e))
            self._notify(step3)
            raise

        # Step 4: Container Build
        step4 = PipelineStep("Build container", 4)
        step4.start()
        self._notify(step4)
        try:
            runtime = get_runtime(config.framework)
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
            deployer = get_deployer(config.deploy.cloud, config.deploy.runtime)
            await deployer.provision(config)
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

    def _register(self, config: AgentConfig, endpoint_url: str) -> None:
        """Register the agent in the local registry.

        For v0.1 this writes to a local JSON file.
        PostgreSQL registry is planned for Milestone 2.
        """
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

        registry_file = REGISTRY_DIR / "agents.json"
        registry: dict[str, Any] = {}
        if registry_file.exists():
            registry = json.loads(registry_file.read_text())

        registry[config.name] = {
            "name": config.name,
            "version": config.version,
            "description": config.description,
            "team": config.team,
            "owner": config.owner,
            "framework": config.framework.value,
            "model_primary": config.model.primary,
            "model_fallback": config.model.fallback,
            "endpoint_url": endpoint_url,
            "tags": config.tags,
            "status": "running",
            "registered_at": datetime.now().isoformat(),
        }

        registry_file.write_text(json.dumps(registry, indent=2))
        logger.info("Registered agent '%s' in local registry", config.name)
