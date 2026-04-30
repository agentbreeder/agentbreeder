"""Compliance scanning subsystem.

This package replaces the ``_SEED_COMPLIANCE_CONTROLS`` static seed list in
``api.services.agentops_service`` with a real, executable registry of SOC2 /
HIPAA control checks (``controls.py``) and an orchestration entry-point
(``scanner.py``) that runs every control against the live database, returning
a structured result that gets persisted to the ``compliance_scans`` table
(migration ``021_compliance_scans_table``).

See issue #208.
"""

from __future__ import annotations

from engine.compliance.controls import (
    CONTROL_REGISTRY,
    ControlResult,
    ControlStatus,
)
from engine.compliance.scanner import ScanSummary, run_compliance_scan

__all__ = [
    "CONTROL_REGISTRY",
    "ControlResult",
    "ControlStatus",
    "ScanSummary",
    "run_compliance_scan",
]
