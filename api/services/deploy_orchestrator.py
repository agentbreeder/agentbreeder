"""DeployOrchestrator — drives provision→build→push→deploy→health_checking→registering
and publishes phase / log / complete / error events to the bus.

Real wiring (provisioner_for / deployer_for) is resolved per-cloud inside
start(); tests inject fakes via the `_provisioner` / `_deployer` keyword
arguments. The destroy_partial path walks .agentbreeder/infra-state.json
in reverse via the cloud's destroy() method.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from api.models.deploy_events import DeployEvent, PhaseName
from api.services.deploy_event_bus import DeployEventBus

logger = logging.getLogger(__name__)

_PHASES: tuple[PhaseName, ...] = (
    "provisioning",
    "building",
    "pushing",
    "deploying",
    "health_checking",
    "registering",
)


class DeployOrchestrator:
    def __init__(self, event_bus: DeployEventBus) -> None:
        self._bus = event_bus

    async def start(
        self,
        *,
        job: Any,
        event_bus: DeployEventBus,
        _provisioner: Any | None = None,
        _deployer: Any | None = None,
    ) -> None:
        """Drive the full deploy lifecycle for one job, publishing events as we go."""
        provisioner = _provisioner or self._resolve_provisioner(job)
        deployer = _deployer or self._resolve_deployer(job)

        async def _emit_log(message: str) -> None:
            await event_bus.publish(
                DeployEvent(
                    type="log",
                    job_id=job.job_id,
                    timestamp=datetime.now(UTC),
                    level="info",
                    message=message,
                )
            )

        try:
            for phase in _PHASES:
                await event_bus.publish(_phase(job, phase))
                if phase == "provisioning" and job.payload.infra_mode == "provision":
                    await provisioner.provision(_provision_payload(job), progress=_emit_log)
                elif phase == "building":
                    try:
                        await deployer.build(job, progress=_emit_log)
                    except TypeError:
                        await deployer.build(job)
                elif phase == "deploying":
                    try:
                        endpoint_url = await deployer.deploy(job, progress=_emit_log)
                    except TypeError:
                        endpoint_url = await deployer.deploy(job)
                    job.endpoint_url = endpoint_url
                # pushing / health_checking / registering are no-ops in this MVP wiring;
                # deployers emit log events inline via their ProgressCallback hook (A7).
            await event_bus.publish(_complete(job))
        except Exception as exc:  # noqa: BLE001
            logger.exception("deploy job %s failed", job.job_id)
            await event_bus.publish(_error(job, exc))

    async def destroy_partial(self, job_id: str) -> None:
        """Roll back partially-created infra. Caller has already team-auth'd.

        Reads .agentbreeder/infra-state.json for the job and calls
        provisioner.destroy(state). Detailed wiring out of scope for A5;
        logs a warning so an operator can see the request landed.
        """
        logger.warning("destroy_partial(%s) — not yet wired to real provisioners", job_id)

    def _resolve_provisioner(self, job: Any) -> Any:
        from engine.provisioners import provisioner_for

        return provisioner_for(job.cloud)

    def _resolve_deployer(self, job: Any) -> Any:
        from engine.deployers import deployer_for  # may not exist yet

        return deployer_for(job.cloud)


def _phase(job: Any, phase: PhaseName) -> DeployEvent:
    return DeployEvent(
        type="phase",
        job_id=job.job_id,
        timestamp=datetime.now(UTC),
        phase=phase,
    )


def _complete(job: Any) -> DeployEvent:
    return DeployEvent(
        type="complete",
        job_id=job.job_id,
        timestamp=datetime.now(UTC),
        endpoint_url=getattr(job, "endpoint_url", None),
    )


def _error(job: Any, exc: Exception) -> DeployEvent:
    return DeployEvent(
        type="error",
        job_id=job.job_id,
        timestamp=datetime.now(UTC),
        message=str(exc),
        error_code=type(exc).__name__,
    )


def _provision_payload(job: Any) -> Any:
    from engine.provisioners import InfraValidationInput

    return InfraValidationInput(
        cloud=job.cloud,
        region=job.region,
        mode="simple",
        fields=getattr(job.payload, "byo_fields", {}) or {},
    )
