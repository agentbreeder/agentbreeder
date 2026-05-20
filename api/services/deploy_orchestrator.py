"""DeployOrchestrator — drives provision→build→push→deploy→health_checking→registering
and publishes phase / log / complete / error events to the bus.

``start()``:
- Resolves the per-cloud provisioner + deployer (tests inject fakes via
  the ``_provisioner`` / ``_deployer`` kwargs).
- After the provision phase succeeds, persists the returned ``InfraState``
  onto the job record so ``destroy_partial`` can tear it down later.

``destroy_partial(job_id)``:
- Reads the job record, deserialises ``InfraState``, calls the cloud's
  ``provisioner.destroy(state)`` (the per-cloud impls already refuse
  untagged resources and tolerate 404s).
- Publishes a ``destroying`` phase + log events, then a terminal
  ``complete`` (or ``error``) event so the SSE client can react.
- Idempotent: if the job has no ``infra_state`` (nothing was provisioned,
  or a previous destroy already cleared it), publishes ``complete``
  immediately with a single log line.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from api.models.deploy_events import DeployEvent, PhaseName
from api.services.deploy_event_bus import DeployEventBus
from api.services.deploy_stores import JobStore

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
    def __init__(
        self,
        event_bus: DeployEventBus,
        *,
        job_store: JobStore | None = None,
    ) -> None:
        self._bus = event_bus
        self._job_store = job_store

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
                    state = await provisioner.provision(
                        _provision_payload(job), progress=_emit_log
                    )
                    # Persist InfraState on the job record so destroy_partial
                    # can resolve it later. Tolerate any shape the test
                    # double returns — only the real Pydantic model has
                    # ``model_dump``; everything else is left as-is.
                    if state is not None and self._job_store is not None:
                        job.infra_state = (
                            state.model_dump(mode="json") if hasattr(state, "model_dump") else None
                        )
                        await self._job_store.put(job)
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
                # deployers emit log events inline via their ProgressCallback hook.
            await event_bus.publish(_complete(job))
        except Exception as exc:  # noqa: BLE001
            logger.exception("deploy job %s failed", job.job_id)
            await event_bus.publish(_error(job, exc))

    async def destroy_partial(
        self,
        job_id: str,
        *,
        _provisioner: Any | None = None,
    ) -> None:
        """Roll back partially-created infra. Caller has already team-auth'd.

        Reads the job's persisted ``InfraState`` and hands it to the cloud's
        ``provisioner.destroy(state)``. Publishes a ``destroying`` phase plus
        progress logs, then a terminal ``complete`` or ``error`` event.
        """

        async def _emit_log(message: str, level: str = "info") -> None:
            await self._bus.publish(
                DeployEvent(
                    type="log",
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    level=level,  # type: ignore[arg-type]
                    message=message,
                )
            )

        async def _emit_complete() -> None:
            await self._bus.publish(
                DeployEvent(
                    type="complete",
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                )
            )

        async def _emit_error(exc: Exception) -> None:
            await self._bus.publish(
                DeployEvent(
                    type="error",
                    job_id=job_id,
                    timestamp=datetime.now(UTC),
                    message=str(exc),
                    error_code=type(exc).__name__,
                )
            )

        if self._job_store is None:
            logger.warning("destroy_partial(%s): no job_store wired; skipping", job_id)
            await _emit_log("destroy_partial: no job store wired — nothing to do", "warn")
            await _emit_complete()
            return

        job = await self._job_store.get(job_id)
        if job is None:
            await _emit_log(f"destroy_partial: job {job_id!r} not found", "warn")
            await _emit_complete()
            return

        if not job.infra_state:
            # Nothing was provisioned (or a previous destroy cleared it).
            await _emit_log(
                "destroy_partial: no infra_state recorded — nothing to tear down",
            )
            await _emit_complete()
            return

        await self._bus.publish(
            DeployEvent(
                type="phase",
                job_id=job_id,
                timestamp=datetime.now(UTC),
                phase="destroying",
            )
        )

        try:
            from engine.provisioners.state import InfraState

            state = InfraState.model_validate(job.infra_state)
            provisioner = _provisioner or self._resolve_provisioner_for_cloud(state.cloud)
            await _emit_log(
                f"destroy_partial: tearing down {len(state.resources)} {state.cloud} resource(s)",
            )
            await provisioner.destroy(state)

            # Clear the saved state so a second call is a no-op.
            job.infra_state = None
            await self._job_store.put(job)
            await _emit_log("destroy_partial: complete")
            await _emit_complete()
        except Exception as exc:  # noqa: BLE001
            logger.exception("destroy_partial(%s) failed", job_id)
            await _emit_error(exc)

    def _resolve_provisioner(self, job: Any) -> Any:
        from engine.provisioners import provisioner_for

        return provisioner_for(job.cloud)

    def _resolve_provisioner_for_cloud(self, cloud: str) -> Any:
        from engine.provisioners import provisioner_for

        return provisioner_for(cloud)

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
