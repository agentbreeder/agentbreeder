"""Unit tests for the compliance evidence report API (issue #75)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# /api/v1/compliance/standards
# ---------------------------------------------------------------------------


def test_list_standards():
    resp = client.get("/api/v1/compliance/standards")
    assert resp.status_code == 200
    body = resp.json()
    assert "standards" in body
    assert "soc2" in body["standards"]
    assert "hipaa" in body["standards"]
    assert "gdpr" in body["standards"]
    assert "iso27001" in body["standards"]
    assert "labels" in body


# ---------------------------------------------------------------------------
# SOC 2 report
# ---------------------------------------------------------------------------


def test_generate_soc2_report():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "soc2",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["standard"] == "soc2"
    assert data["team"] is None
    assert "report_id" in data
    assert "generated_at" in data
    assert "CC6_access_controls" in data["sections"]
    assert "CC7_system_operations" in data["sections"]
    assert "CC9_risk_mitigation" in data["sections"]
    assert "summary" in data


def test_generate_soc2_report_with_team():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "soc2",
            "team": "engineering",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["team"] == "engineering"


def test_soc2_report_sections_structure():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "soc2",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert resp.status_code == 200
    sections = resp.json()["sections"]

    cc6 = sections["CC6_access_controls"]
    assert "description" in cc6
    assert "evidence" in cc6
    assert "rbac_configuration" in cc6["evidence"]

    cc7 = sections["CC7_system_operations"]
    assert "deploy_events" in cc7["evidence"]
    assert "failed_rbac_checks" in cc7["evidence"]

    cc9 = sections["CC9_risk_mitigation"]
    assert "cost_anomalies" in cc9["evidence"]
    assert "guardrail_events" in cc9["evidence"]


# ---------------------------------------------------------------------------
# HIPAA report
# ---------------------------------------------------------------------------


def test_generate_hipaa_report():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "hipaa",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["standard"] == "hipaa"
    assert "access_controls" in data["sections"]
    assert "audit_controls" in data["sections"]
    assert "phi_handling" in data["sections"]


def test_hipaa_access_controls_structure():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "hipaa",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    sections = resp.json()["sections"]
    ac = sections["access_controls"]
    assert "unique_user_identification" in ac["evidence"]
    assert "encryption_and_decryption" in ac["evidence"]


# ---------------------------------------------------------------------------
# GDPR report
# ---------------------------------------------------------------------------


def test_generate_gdpr_report():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "gdpr",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["standard"] == "gdpr"
    assert "lawful_basis" in data["sections"]
    assert "data_subject_rights" in data["sections"]
    assert "data_protection" in data["sections"]


# ---------------------------------------------------------------------------
# ISO 27001 report
# ---------------------------------------------------------------------------


def test_generate_iso27001_report():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "iso27001",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["standard"] == "iso27001"
    assert "A9_access_control" in data["sections"]
    assert "A12_operations_security" in data["sections"]
    assert "A14_system_acquisition" in data["sections"]


# ---------------------------------------------------------------------------
# Report retrieval
# ---------------------------------------------------------------------------


def test_get_report_by_id():
    # Generate first
    post_resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "soc2",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert post_resp.status_code == 200
    report_id = post_resp.json()["report_id"]

    # Retrieve by ID
    get_resp = client.get(f"/api/v1/compliance/reports/{report_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["report_id"] == report_id
    assert get_resp.json()["standard"] == "soc2"


def test_get_report_not_found():
    resp = client.get("/api/v1/compliance/reports/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Validation / error cases
# ---------------------------------------------------------------------------


def test_invalid_standard_rejected():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "pci_dss",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    # Pydantic Literal validation → 422
    assert resp.status_code == 422


def test_missing_required_fields():
    resp = client.post("/api/v1/compliance/reports", json={"standard": "soc2"})
    assert resp.status_code == 422


def test_report_summary_present():
    resp = client.post(
        "/api/v1/compliance/reports",
        json={
            "standard": "soc2",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
        },
    )
    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert "standard" in summary
    assert "period" in summary
    assert "data_note" in summary
