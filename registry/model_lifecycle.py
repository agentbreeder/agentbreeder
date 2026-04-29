"""Model lifecycle service — Track G (#163).

Owns the diff between a discovery sync's output and the registry's state:

* New model → ``status="active"``, ``discovered_at = now``, audit ``model.added``.
* Still present → ``last_seen_at = now`` (and bring ``deprecated`` rows back to
  ``active`` with ``deprecated_at = NULL`` if the upstream un-retired them).
* Absent → first absence sets ``status="deprecated"`` + ``deprecated_at = now``
  and emits ``model.deprecated``. After ``RETIREMENT_GRACE_DAYS`` (30 days) of
  continuous absence, the row flips to ``status="retired"`` and emits
  ``model.retired``.

The service is intentionally registry-aware (talks SQLAlchemy directly) — it
is NOT called from application code; agents and routes go through
:class:`registry.models.ModelRegistry`. Callers are: the API ``/sync`` route,
the ``agentbreeder model sync`` CLI command, and the future cloud cron.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Model
from engine.providers.discovery import DiscoveredModel, DiscoveryError, ProviderDiscovery

logger = logging.getLogger(__name__)


RETIREMENT_GRACE_DAYS = 30
"""Days a deprecated model can be absent from discovery before being retired."""

ACTIVE = "active"
BETA = "beta"
DEPRECATED = "deprecated"
RETIRED = "retired"


# ─── Result shapes ────────────────────────────────────────────────────────


@dataclass
class SyncProviderResult:
    """Per-provider outcome of a single sync run."""

    provider: str
    added: list[str] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def total_seen(self) -> int:
        return len(self.seen) + len(self.added)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "added": list(self.added),
            "seen": list(self.seen),
            "deprecated": list(self.deprecated),
            "retired": list(self.retired),
            "error": self.error,
            "total_seen": self.total_seen,
        }


@dataclass
class SyncResult:
    """Top-level outcome of a multi-provider sync."""

    started_at: datetime
    finished_at: datetime
    providers: list[SyncProviderResult] = field(default_factory=list)

    @property
    def total_added(self) -> int:
        return sum(len(p.added) for p in self.providers)

    @property
    def total_deprecated(self) -> int:
        return sum(len(p.deprecated) for p in self.providers)

    @property
    def total_retired(self) -> int:
        return sum(len(p.retired) for p in self.providers)

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": (self.finished_at - self.started_at).total_seconds(),
            "providers": [p.as_dict() for p in self.providers],
            "totals": {
                "added": self.total_added,
                "deprecated": self.total_deprecated,
                "retired": self.total_retired,
            },
        }


# ─── Audit hook ────────────────────────────────────────────────────────────


async def _emit_audit(
    *,
    actor: str,
    action: str,
    model: Model,
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit emission — failures must never break the sync."""
    try:
        from api.services.audit_service import AuditService

        await AuditService.log_event(
            actor=actor,
            action=action,
            resource_type="model",
            resource_id=str(model.id),
            resource_name=model.name,
            details={"provider": model.provider, **(details or {})},
        )
    except Exception:  # pragma: no cover — audit is best-effort
        logger.exception("Failed to emit audit event %s for model %s", action, model.name)


# ─── Core service ──────────────────────────────────────────────────────────


class ModelLifecycleService:
    """Reconciles discovery output with the ``models`` table."""

    def __init__(
        self,
        *,
        retirement_grace_days: int = RETIREMENT_GRACE_DAYS,
        actor: str = "system",
    ) -> None:
        self._retirement_grace_days = retirement_grace_days
        self._actor = actor

    # -- Public entry points ------------------------------------------------

    async def sync_provider(
        self,
        session: AsyncSession,
        *,
        provider_name: str,
        discovery: ProviderDiscovery,
        now: datetime | None = None,
    ) -> SyncProviderResult:
        """Sync a single provider's discovery output into the registry.

        On discovery failure (network / auth), records the error on the
        result and leaves existing rows untouched so a transient outage
        cannot mass-deprecate everything.
        """
        result = SyncProviderResult(provider=provider_name)
        ts = now or datetime.now(UTC)
        try:
            discovered = await discovery.list_models()
        except DiscoveryError as exc:
            result.error = str(exc)
            logger.warning("Discovery failed for %s: %s", provider_name, exc)
            return result

        # Pull every existing model for this provider in one query.
        rows = await session.execute(select(Model).where(Model.provider == provider_name))
        existing = {m.name: m for m in rows.scalars().all()}
        seen_now: set[str] = set()

        for dm in discovered:
            seen_now.add(dm.id)
            row = existing.get(dm.id)
            if row is None:
                row = self._create_row(dm, ts)
                session.add(row)
                result.added.append(dm.id)
                await session.flush()
                await _emit_audit(
                    actor=self._actor,
                    action="model.added",
                    model=row,
                    details={"context_window": dm.context_window},
                )
                continue

            # Model is present in the upstream sync.
            row.last_seen_at = ts
            self._refresh_metadata(row, dm)
            if row.status in (DEPRECATED, RETIRED):
                # Provider un-retired the model — bring it back as active.
                row.status = ACTIVE
                row.deprecated_at = None
                logger.info("Model %s un-deprecated by upstream sync", row.name)
            result.seen.append(dm.id)

        # Anything in the registry for this provider that was NOT in the
        # discovery payload becomes a candidate for deprecation / retirement.
        retirement_cutoff = ts - timedelta(days=self._retirement_grace_days)
        for name, row in existing.items():
            if name in seen_now:
                continue
            if row.source not in {"discovery", "manual"}:
                # Curated / catalog-only entries: leave alone.
                continue
            if row.status == ACTIVE:
                row.status = DEPRECATED
                row.deprecated_at = ts
                result.deprecated.append(name)
                await _emit_audit(
                    actor=self._actor,
                    action="model.deprecated",
                    model=row,
                    details={"reason": "absent_from_discovery"},
                )
            elif row.status == DEPRECATED and row.deprecated_at is not None:
                # Already deprecated — retire after grace window.
                # Normalise tz: SQLite drops tzinfo on round-trip; Postgres
                # preserves it. We compare wall-clock to keep the contract
                # consistent across both.
                deprecated_at = row.deprecated_at
                if deprecated_at.tzinfo is None:
                    deprecated_at = deprecated_at.replace(tzinfo=UTC)
                if deprecated_at <= retirement_cutoff:
                    row.status = RETIRED
                    result.retired.append(name)
                    await _emit_audit(
                        actor=self._actor,
                        action="model.retired",
                        model=row,
                        details={
                            "reason": "absent_for_grace_period",
                            "grace_days": self._retirement_grace_days,
                        },
                    )
            # Already-retired rows are left as-is.

        await session.flush()
        return result

    async def sync(
        self,
        session: AsyncSession,
        *,
        discoveries: dict[str, ProviderDiscovery],
        now: datetime | None = None,
    ) -> SyncResult:
        """Run discovery + reconciliation across many providers.

        Each provider runs in isolation — one failing adapter does not abort
        the others. The caller commits the session.
        """
        started = now or datetime.now(UTC)
        provider_results: list[SyncProviderResult] = []
        for provider_name, adapter in discoveries.items():
            res = await self.sync_provider(
                session,
                provider_name=provider_name,
                discovery=adapter,
                now=started,
            )
            provider_results.append(res)
        finished = datetime.now(UTC)
        return SyncResult(started_at=started, finished_at=finished, providers=provider_results)

    async def deprecate(
        self,
        session: AsyncSession,
        *,
        model_name: str,
        replacement_name: str | None = None,
        actor: str | None = None,
    ) -> Model:
        """Mark a model as deprecated by hand (operator override).

        Optionally points at a replacement model. Both rows must already
        exist. Emits ``model.deprecated``.
        """
        rows = await session.execute(select(Model).where(Model.name == model_name))
        model = rows.scalar_one_or_none()
        if model is None:
            msg = f"Model '{model_name}' not found"
            raise LookupError(msg)
        replacement: Model | None = None
        if replacement_name:
            r = await session.execute(select(Model).where(Model.name == replacement_name))
            replacement = r.scalar_one_or_none()
            if replacement is None:
                msg = f"Replacement model '{replacement_name}' not found"
                raise LookupError(msg)

        model.status = DEPRECATED
        model.deprecated_at = datetime.now(UTC)
        if replacement is not None:
            model.deprecation_replacement_id = replacement.id
        await session.flush()
        await _emit_audit(
            actor=actor or self._actor,
            action="model.deprecated",
            model=model,
            details={
                "reason": "manual",
                "replacement": replacement_name,
            },
        )
        return model

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _create_row(dm: DiscoveredModel, ts: datetime) -> Model:
        """Build a new ``Model`` row from a discovery result."""
        return Model(
            name=dm.id,
            provider=dm.provider,
            description=dm.name,
            source="discovery",
            status=ACTIVE,
            context_window=dm.context_window,
            max_output_tokens=dm.max_output_tokens,
            capabilities=list(dm.capabilities),
            discovered_at=ts,
            last_seen_at=ts,
        )

    @staticmethod
    def _refresh_metadata(row: Model, dm: DiscoveredModel) -> None:
        """Refresh metadata that the upstream may have updated.

        We only overwrite when the upstream gives us a non-null value —
        callers can override pricing / display name in the registry without
        being clobbered on every sync.
        """
        if dm.context_window is not None:
            row.context_window = dm.context_window
        if dm.max_output_tokens is not None:
            row.max_output_tokens = dm.max_output_tokens
        if dm.capabilities:
            row.capabilities = list(dm.capabilities)


__all__ = [
    "ACTIVE",
    "BETA",
    "DEPRECATED",
    "RETIRED",
    "RETIREMENT_GRACE_DAYS",
    "ModelLifecycleService",
    "SyncProviderResult",
    "SyncResult",
]
