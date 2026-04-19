"""Compliance evidence report generation API."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])

# In-memory store for generated reports (keyed by report_id)
_report_store: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReportRequest(BaseModel):
    standard: Literal["soc2", "hipaa", "gdpr", "iso27001"]
    team: str | None = None
    period_start: date
    period_end: date
    format: Literal["json", "csv"] = "json"


class ReportResponse(BaseModel):
    report_id: str
    standard: str
    team: str | None
    period_start: date
    period_end: date
    generated_at: datetime
    sections: dict
    summary: dict


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------


def _build_soc2_report(request: ReportRequest) -> dict:
    """Build SOC 2 Type II evidence sections (CC6, CC7, CC9)."""
    return {
        "CC6_access_controls": {
            "description": "Logical and Physical Access Controls",
            "note": "DEMO DATA — replace with live registry query in production",
            "evidence": {
                "rbac_configuration": {
                    "teams": [],
                    "roles": ["admin", "developer", "viewer"],
                    "note": "Exported from AgentBreeder registry",
                },
                "permission_changes": [],
                "api_key_rotations": [],
                "failed_login_attempts": 0,
            },
        },
        "CC7_system_operations": {
            "description": "System Operations",
            "note": "DEMO DATA — replace with live audit log query in production",
            "evidence": {
                "deploy_events": [],
                "rollback_events": [],
                "failed_rbac_checks": [],
                "health_check_results": {
                    "total_checks": 0,
                    "passed": 0,
                    "failed": 0,
                },
            },
        },
        "CC9_risk_mitigation": {
            "description": "Risk Mitigation",
            "note": "DEMO DATA — replace with live cost/guardrail query in production",
            "evidence": {
                "cost_anomalies": [],
                "guardrail_events": [],
                "health_check_failures": [],
                "pii_detections": 0,
                "content_filter_blocks": 0,
            },
        },
    }


def _build_hipaa_report(request: ReportRequest) -> dict:
    """Build HIPAA compliance evidence sections."""
    return {
        "access_controls": {
            "description": "HIPAA § 164.312(a)(1) — Access Control",
            "note": "DEMO DATA — replace with live registry query in production",
            "evidence": {
                "unique_user_identification": {
                    "implemented": True,
                    "mechanism": "JWT-based authentication with per-user identity",
                },
                "emergency_access_procedure": {
                    "documented": True,
                    "last_reviewed": str(request.period_end),
                },
                "automatic_logoff": {
                    "implemented": True,
                    "timeout_minutes": 1440,
                },
                "encryption_and_decryption": {
                    "in_transit": "TLS 1.3",
                    "at_rest": "AES-256",
                },
            },
        },
        "audit_controls": {
            "description": "HIPAA § 164.312(b) — Audit Controls",
            "note": "DEMO DATA — replace with live audit log query in production",
            "evidence": {
                "audit_log_enabled": True,
                "events_logged": [
                    "agent_deploy",
                    "agent_invoke",
                    "rbac_check",
                    "cost_attribution",
                    "registry_write",
                ],
                "total_events_in_period": 0,
                "log_integrity": "append-only audit trail via AgentBreeder AuditService",
            },
        },
        "phi_handling": {
            "description": "HIPAA § 164.312(e)(1) — Transmission Security",
            "note": "DEMO DATA — replace with live guardrail event query in production",
            "evidence": {
                "pii_detection_guardrail": {
                    "enabled": True,
                    "triggers_in_period": 0,
                },
                "phi_fields_masked": True,
                "data_minimisation_policy": "Only agent I/O necessary for task is logged",
                "business_associate_agreements": [],
            },
        },
    }


def _build_gdpr_report(request: ReportRequest) -> dict:
    """Build GDPR compliance evidence sections."""
    return {
        "lawful_basis": {
            "description": "GDPR Art. 6 — Lawfulness of processing",
            "note": "DEMO DATA — replace with live data-processing records in production",
            "evidence": {
                "processing_activities": [],
                "consent_records": 0,
                "legitimate_interest_assessments": [],
            },
        },
        "data_subject_rights": {
            "description": "GDPR Art. 15-22 — Rights of the data subject",
            "note": "DEMO DATA",
            "evidence": {
                "access_requests_received": 0,
                "access_requests_fulfilled": 0,
                "erasure_requests_received": 0,
                "erasure_requests_fulfilled": 0,
                "portability_requests": 0,
            },
        },
        "data_protection": {
            "description": "GDPR Art. 25, 32 — Data protection by design and by default",
            "note": "DEMO DATA",
            "evidence": {
                "encryption_at_rest": True,
                "encryption_in_transit": True,
                "pseudonymisation": False,
                "data_retention_policy": "Configurable per-team, default 90 days",
                "dpia_completed": False,
            },
        },
    }


def _build_iso27001_report(request: ReportRequest) -> dict:
    """Build ISO 27001 compliance evidence sections."""
    return {
        "A9_access_control": {
            "description": "ISO 27001 Annex A.9 — Access Control",
            "note": "DEMO DATA — replace with live registry query in production",
            "evidence": {
                "access_control_policy": "RBAC enforced at deploy time via AgentBreeder governance engine",
                "user_access_management": {
                    "provisioning_process": "Team-based RBAC via AgentBreeder teams API",
                    "privileged_access_management": "Admin role with restricted endpoints",
                },
                "user_access_reviews": [],
                "password_policy": "JWT-based; no password reuse tracked in this period",
            },
        },
        "A12_operations_security": {
            "description": "ISO 27001 Annex A.12 — Operations Security",
            "note": "DEMO DATA — replace with live deploy/audit query in production",
            "evidence": {
                "change_management": {
                    "deploy_events": 0,
                    "rollback_events": 0,
                    "approval_required": False,
                },
                "capacity_management": {
                    "autoscaling_enabled": True,
                    "cost_anomaly_alerts": 0,
                },
                "malware_protection": "Container images scanned at build time",
                "audit_logging": "All operations logged via AgentBreeder AuditService",
            },
        },
        "A14_system_acquisition": {
            "description": "ISO 27001 Annex A.14 — System Acquisition, Development and Maintenance",
            "note": "DEMO DATA",
            "evidence": {
                "secure_development_policy": "GitHub Actions CI/CD with automated security checks",
                "change_control_procedures": "PR-based workflow via agentbreeder submit/review/publish",
                "technical_vulnerability_management": {
                    "dependency_scanning": True,
                    "last_scan": str(request.period_end),
                },
            },
        },
    }


_BUILDERS = {
    "soc2": _build_soc2_report,
    "hipaa": _build_hipaa_report,
    "gdpr": _build_gdpr_report,
    "iso27001": _build_iso27001_report,
}

_STANDARD_LABELS = {
    "soc2": "SOC 2 Type II",
    "hipaa": "HIPAA Security Rule",
    "gdpr": "GDPR",
    "iso27001": "ISO/IEC 27001:2022",
}


def _build_summary(standard: str, sections: dict, request: ReportRequest) -> dict:
    return {
        "standard": _STANDARD_LABELS.get(standard, standard),
        "period": f"{request.period_start} to {request.period_end}",
        "team": request.team or "all teams",
        "section_count": len(sections),
        "sections": list(sections.keys()),
        "data_note": (
            "This report contains DEMO DATA. "
            "Connect the compliance API to your live AgentBreeder audit, cost, and registry "
            "services to populate real evidence."
        ),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/reports", response_model=ReportResponse)
async def generate_report(request: ReportRequest) -> ReportResponse:
    """Generate a compliance evidence report pack for the requested standard."""
    builder = _BUILDERS.get(request.standard)
    if builder is None:
        raise HTTPException(status_code=422, detail=f"Unsupported standard: {request.standard}")

    report_id = str(uuid.uuid4())
    generated_at = datetime.now(UTC)

    sections = builder(request)
    summary = _build_summary(request.standard, sections, request)

    response = ReportResponse(
        report_id=report_id,
        standard=request.standard,
        team=request.team,
        period_start=request.period_start,
        period_end=request.period_end,
        generated_at=generated_at,
        sections=sections,
        summary=summary,
    )

    # Cache for later retrieval
    _report_store[report_id] = response.model_dump(mode="json")

    return response


@router.get("/reports/{report_id}", response_model=ReportResponse)
async def get_report(report_id: str) -> ReportResponse:
    """Retrieve a previously generated compliance report by ID."""
    entry = _report_store.get(report_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
    return ReportResponse(**entry)


@router.get("/standards")
async def list_standards() -> dict:
    """List supported compliance standards."""
    return {
        "standards": ["soc2", "hipaa", "gdpr", "iso27001"],
        "labels": _STANDARD_LABELS,
    }
