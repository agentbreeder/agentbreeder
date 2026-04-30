"""Daily model-sync cron — issue #199.

Runs the same reconciliation logic as ``POST /api/v1/models/sync`` once a
day inside a long-running API server. Cloud workspaces enable it by
default (``AGENTBREEDER_MODELS_DAILY_SYNC=true`` is implied for
``AGENTBREEDER_INSTALL_MODE=cloud``); self-hosted users opt in via the
same env var.

Design notes:

* No new heavy dependency. Plain :mod:`asyncio` ``create_task`` + ``sleep``.
* Single-provider failures are isolated — we delegate to
  :class:`ModelLifecycleService.sync` which already runs each provider in a
  ``try/except`` guard.
* The very first sweep is delayed by a random jitter (0-1 hour) so that a
  fleet of co-deployed API replicas don't all hammer the upstream provider
  endpoints in the same minute.
* Each sweep emits ``model.sync.scheduled`` with a summary of totals so the
  audit log shows the cron actually ran.
* The CLI command ``agentbreeder model sync-now`` reuses the same entry
  point — useful for testing and for environments that disable the
  background loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


_DAY_SECONDS = 24 * 60 * 60
_MAX_INITIAL_JITTER_SECONDS = 60 * 60  # 0–1h spread for replica fleets
SYNC_AUDIT_ACTION = "model.sync.scheduled"


# ─── Public flags ──────────────────────────────────────────────────────────


def daily_sync_enabled() -> bool:
    """Return ``True`` when the daily sync should run for this process.

    Resolution order:

    1. ``AGENTBREEDER_MODELS_DAILY_SYNC`` (explicit on/off): ``true``,
       ``1``, ``yes`` → enabled; ``false``, ``0``, ``no`` → disabled.
    2. ``AGENTBREEDER_INSTALL_MODE`` — defaults to ``true`` when the value
       is ``cloud``; otherwise ``false``.
    """
    explicit = os.environ.get("AGENTBREEDER_MODELS_DAILY_SYNC")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    install_mode = os.environ.get("AGENTBREEDER_INSTALL_MODE", "").strip().lower()
    return install_mode == "cloud"


# ─── One-shot sweep ────────────────────────────────────────────────────────


async def run_sync_once(
    *,
    actor: str = "system:cron",
) -> dict[str, Any]:
    """Run a single discover-and-reconcile sweep across every provider.

    Builds the discovery adapter set the same way the ``/models/sync``
    route does (catalog presets gated on api-key env vars + first-class
    providers + DB ``providers`` rows), commits the resulting changes, and
    writes a ``model.sync.scheduled`` audit event with the totals.

    Returns the :meth:`SyncResult.as_dict` payload. On a sweep that finds
    no providers at all, returns an empty totals dict and skips the audit
    event.
    """
    # Local imports — avoid pulling SQLAlchemy / FastAPI into module import
    # time so the CLI ``--help`` stays snappy and tests can stub freely.
    from api.database import async_session
    from api.routes.models import _build_discoveries
    from registry.model_lifecycle import ModelLifecycleService

    started = datetime.now(UTC)
    async with async_session() as db:
        try:
            discoveries = await _build_discoveries(db, [])
        except Exception:  # pragma: no cover — defensive
            logger.exception("models-sync-cron: failed to build discoveries; skipping sweep")
            return _empty_summary(started, reason="discovery-build-failed")

        if not discoveries:
            logger.info("models-sync-cron: no providers configured; nothing to sync")
            return _empty_summary(started, reason="no-providers")

        service = ModelLifecycleService(actor=actor)
        try:
            result = await service.sync(db, discoveries=discoveries)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("models-sync-cron: sweep failed; rolled back")
            raise

    summary = result.as_dict()
    totals = summary.get("totals", {})
    logger.info(
        "models-sync-cron: providers=%d added=%d deprecated=%d retired=%d",
        len(summary.get("providers", [])),
        totals.get("added", 0),
        totals.get("deprecated", 0),
        totals.get("retired", 0),
    )
    await _emit_summary_audit(actor=actor, summary=summary)
    return summary


async def _emit_summary_audit(*, actor: str, summary: dict[str, Any]) -> None:
    """Best-effort audit emission — never let audit failures break the cron."""
    try:
        from api.services.audit_service import AuditService

        totals = summary.get("totals", {})
        provider_names = [p.get("provider", "?") for p in summary.get("providers", [])]
        await AuditService.log_event(
            actor=actor,
            action=SYNC_AUDIT_ACTION,
            resource_type="model",
            resource_name="*",
            details={
                "providers": provider_names,
                "added": totals.get("added", 0),
                "deprecated": totals.get("deprecated", 0),
                "retired": totals.get("retired", 0),
                "duration_seconds": summary.get("duration_seconds"),
            },
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("models-sync-cron: failed to emit audit event")


def _empty_summary(started: datetime, *, reason: str) -> dict[str, Any]:
    finished = datetime.now(UTC)
    return {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "providers": [],
        "totals": {"added": 0, "deprecated": 0, "retired": 0},
        "skipped_reason": reason,
    }


# ─── Background loop ───────────────────────────────────────────────────────


async def _sleep_with_jitter(base_seconds: int, *, max_jitter: int) -> None:
    """Sleep ``base_seconds`` plus a uniform jitter in ``[0, max_jitter]``."""
    jitter = random.uniform(0, max_jitter) if max_jitter > 0 else 0
    await asyncio.sleep(base_seconds + jitter)


async def daily_sync_loop(
    *,
    interval_seconds: int = _DAY_SECONDS,
    initial_jitter_seconds: int = _MAX_INITIAL_JITTER_SECONDS,
) -> None:
    """Run :func:`run_sync_once` once every ``interval_seconds``.

    Sleeps for a random initial jitter window (0–``initial_jitter_seconds``)
    before the first sweep so co-deployed replicas don't synchronise.
    Failures are logged and swallowed — the loop keeps running.
    """
    logger.info(
        "models-sync-cron: starting (interval=%ds, initial-jitter≤%ds)",
        interval_seconds,
        initial_jitter_seconds,
    )
    if initial_jitter_seconds > 0:
        await asyncio.sleep(random.uniform(0, initial_jitter_seconds))

    while True:
        try:
            await run_sync_once()
        except asyncio.CancelledError:
            logger.info("models-sync-cron: cancelled; exiting loop")
            raise
        except Exception:
            logger.exception("models-sync-cron: sweep crashed; will retry next cycle")
        # Small extra jitter (≤5 min) on each cycle — keeps replicas drifting apart.
        try:
            await _sleep_with_jitter(interval_seconds, max_jitter=5 * 60)
        except asyncio.CancelledError:
            logger.info("models-sync-cron: cancelled during sleep; exiting loop")
            raise


def start_background_task() -> asyncio.Task[None] | None:
    """Schedule the loop on the running event loop.

    Returns the created :class:`asyncio.Task` so the API lifespan can
    cancel it on shutdown, or ``None`` when the cron is disabled.
    """
    if not daily_sync_enabled():
        logger.info("models-sync-cron: disabled (AGENTBREEDER_MODELS_DAILY_SYNC unset/false)")
        return None
    loop = asyncio.get_event_loop()
    return loop.create_task(daily_sync_loop(), name="models-sync-cron")


__all__ = [
    "SYNC_AUDIT_ACTION",
    "daily_sync_enabled",
    "daily_sync_loop",
    "run_sync_once",
    "start_background_task",
]
