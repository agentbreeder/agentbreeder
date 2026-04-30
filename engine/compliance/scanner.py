"""Compliance scan orchestration.

Runs every control in :data:`engine.compliance.controls.CONTROL_REGISTRY`
against a live ``AsyncSession`` and produces a :class:`ScanSummary` ready to
persist to the ``compliance_scans`` table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from engine.compliance.controls import (
    CONTROL_REGISTRY,
    Control,
    ControlResult,
)

logger = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    """Aggregate result of a single compliance scan run."""

    ran_at: str
    overall_status: str  # "compliant" | "partial" | "non_compliant"
    controls_total: int
    controls_passed: int
    controls_failed: int
    controls_partial: int
    controls_skipped: int
    results: list[ControlResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at,
            "overall_status": self.overall_status,
            "controls_total": self.controls_total,
            "controls_passed": self.controls_passed,
            "controls_failed": self.controls_failed,
            "controls_partial": self.controls_partial,
            "controls_skipped": self.controls_skipped,
            "results": [r.to_dict() for r in self.results],
        }

    def summary_dict(self) -> dict[str, Any]:
        """The compact summary persisted to ``compliance_scans.summary``."""
        return {
            "overall_status": self.overall_status,
            "controls_total": self.controls_total,
            "controls_passed": self.controls_passed,
            "controls_failed": self.controls_failed,
            "controls_partial": self.controls_partial,
            "controls_skipped": self.controls_skipped,
        }


def _overall_status(results: list[ControlResult]) -> str:
    """Roll-up rule: any fail -> ``non_compliant``; any partial -> ``partial``;
    otherwise ``compliant``. ``skipped`` controls don't move the needle."""
    has_fail = any(r.status == "fail" for r in results)
    has_partial = any(r.status == "partial" for r in results)
    if has_fail:
        return "non_compliant"
    if has_partial:
        return "partial"
    return "compliant"


async def run_compliance_scan(
    db: AsyncSession,
    *,
    controls: tuple[Control, ...] | None = None,
) -> ScanSummary:
    """Run every registered control, capturing exceptions per-control.

    A control that raises is recorded with status ``skipped`` so a single
    broken probe never aborts the whole scan.
    """
    selected = controls if controls is not None else CONTROL_REGISTRY
    ran_at = datetime.now(UTC).isoformat()
    results: list[ControlResult] = []

    for control in selected:
        try:
            result = await control.check(db)
        except Exception as exc:  # noqa: BLE001 — defensive: never crash the scan
            logger.exception("Control '%s' raised during scan", control.control_id)
            result = ControlResult(
                control_id=control.control_id,
                name=control.name,
                category=control.category,
                status="skipped",
                evidence={"error": str(exc)},
                details=f"Control raised an exception: {exc}",
                last_checked=ran_at,
            )
        results.append(result)

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    partial = sum(1 for r in results if r.status == "partial")
    skipped = sum(1 for r in results if r.status == "skipped")

    return ScanSummary(
        ran_at=ran_at,
        overall_status=_overall_status(results),
        controls_total=len(results),
        controls_passed=passed,
        controls_failed=failed,
        controls_partial=partial,
        controls_skipped=skipped,
        results=results,
    )
