"""Integration tests for /api/v1/deployments/* endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# -- GET /cloud-requirements/{cloud} -----------------------------------------


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure"])
def test_cloud_requirements_default_mode_is_simple(client: TestClient, cloud: str) -> None:
    resp = client.get(f"/api/v1/deployments/cloud-requirements/{cloud}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["cloud"] == cloud
    assert data["mode"] == "simple"
    assert data["required"]


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure"])
def test_cloud_requirements_full_mode(client: TestClient, cloud: str) -> None:
    resp = client.get(f"/api/v1/deployments/cloud-requirements/{cloud}?mode=full")
    assert resp.status_code == 200
    assert resp.json()["data"]["mode"] == "full"


def test_cloud_requirements_unknown_cloud_returns_422_or_404(client: TestClient) -> None:
    # FastAPI's Literal validation returns 422 before the route is reached.
    resp = client.get("/api/v1/deployments/cloud-requirements/ibmcloud")
    assert resp.status_code in (404, 422)


# -- POST /validate-infra ----------------------------------------------------


@pytest.fixture
def mock_audit_log():
    with patch(
        "api.routes.deployments.AuditService.log_event",
        new=AsyncMock(return_value=MagicMock()),
    ) as m:
        yield m


def test_validate_infra_returns_200_when_provisioner_reports_valid(
    client: TestClient, mock_audit_log
) -> None:
    from engine.provisioners.base import ValidationResult

    result = ValidationResult(valid=True, cloud="aws", region="us-east-1", checks=[])
    mock_provisioner = MagicMock()
    mock_provisioner.validate_existing = AsyncMock(return_value=result)

    with patch("api.routes.deployments.provisioner_for", return_value=mock_provisioner):
        resp = client.post(
            "/api/v1/deployments/validate-infra",
            json={
                "team_id": "engineering",
                "cloud": "aws",
                "region": "us-east-1",
                "mode": "simple",
                "fields": {"AWS_ACCOUNT_ID": "123"},
            },
        )
    assert resp.status_code == 200
    mock_audit_log.assert_called_once()


def test_validate_infra_returns_with_errors_when_invalid(
    client: TestClient, mock_audit_log
) -> None:
    from engine.provisioners.base import ValidationCheck, ValidationResult

    result = ValidationResult(
        valid=False,
        cloud="aws",
        region="us-east-1",
        checks=[
            ValidationCheck(resource="subnet-x", status="missing", detail="not found"),
        ],
    )
    mock_provisioner = MagicMock()
    mock_provisioner.validate_existing = AsyncMock(return_value=result)

    with patch("api.routes.deployments.provisioner_for", return_value=mock_provisioner):
        resp = client.post(
            "/api/v1/deployments/validate-infra",
            json={
                "team_id": "engineering",
                "cloud": "aws",
                "region": "us-east-1",
                "mode": "full",
                "fields": {"AWS_ACCOUNT_ID": "1", "AWS_VPC_SUBNETS": "subnet-x"},
            },
        )
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"]["valid"] is False
    assert body["errors"], "invalid checks must surface in the errors envelope"


def test_validate_infra_returns_502_on_cloud_sdk_error(client: TestClient, mock_audit_log) -> None:
    mock_provisioner = MagicMock()
    mock_provisioner.validate_existing = AsyncMock(side_effect=RuntimeError("SDK down"))

    with patch("api.routes.deployments.provisioner_for", return_value=mock_provisioner):
        resp = client.post(
            "/api/v1/deployments/validate-infra",
            json={
                "team_id": "engineering",
                "cloud": "aws",
                "region": "us-east-1",
                "fields": {},
            },
        )
    assert resp.status_code == 502
    mock_audit_log.assert_called_once()
