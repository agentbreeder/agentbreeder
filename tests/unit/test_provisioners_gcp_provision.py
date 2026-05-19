"""#382 — GCP greenfield provisioner unit tests.

The low-level google-cloud SDK calls are patched at the
``GCPProvisioner._ensure_*`` / ``_bind_*`` boundary so the test suite runs
without the GCP SDKs installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from engine.provisioners import InfraState, InfraValidationInput
from engine.provisioners.gcp import GCPProvisioner, _truncate_sa_id

# -- _truncate_sa_id --------------------------------------------------------


def test_truncate_sa_id_basic() -> None:
    assert _truncate_sa_id("simple") == "ab-simple"


def test_truncate_sa_id_lowercases() -> None:
    assert _truncate_sa_id("MyAgent") == "ab-myagent"


def test_truncate_sa_id_replaces_disallowed_chars() -> None:
    assert _truncate_sa_id("billing_agent.v2") == "ab-billing-agent-v2"


def test_truncate_sa_id_caps_length() -> None:
    sa_id = _truncate_sa_id("x" * 50)
    assert 6 <= len(sa_id) <= 30


def test_truncate_sa_id_pads_short_input() -> None:
    sa_id = _truncate_sa_id("a")
    assert len(sa_id) >= 6


# -- provision() orchestration ---------------------------------------------


def _payload(extra: dict | None = None) -> InfraValidationInput:
    fields = {
        "GOOGLE_CLOUD_PROJECT": "test-proj",
        "GCP_REGION": "us-central1",
        "GCP_AGENT_NAME": "demo",
    }
    if extra:
        fields.update(extra)
    return InfraValidationInput(cloud="gcp", region="us-central1", mode="simple", fields=fields)


@pytest.fixture
def patched_provisioner():
    """GCPProvisioner with every low-level SDK helper patched out."""
    p = GCPProvisioner()
    with (
        patch.object(p, "_ensure_artifact_registry_repo", new=AsyncMock(return_value=None)),
        patch.object(p, "_ensure_service_account", new=AsyncMock(return_value=None)),
        patch.object(p, "_bind_iam_roles", new=AsyncMock(return_value=None)),
        patch.object(p, "_delete_artifact_registry_repo", new=AsyncMock(return_value=None)),
        patch.object(p, "_delete_service_account", new=AsyncMock(return_value=None)),
    ):
        yield p


@pytest.mark.asyncio
async def test_provision_returns_state_with_expected_resources(patched_provisioner) -> None:
    state = await patched_provisioner.provision(_payload())

    assert isinstance(state, InfraState)
    assert state.cloud == "gcp"
    assert state.region == "us-central1"
    assert state.mode == "provisioned"
    # Resources are populated for the two things we actually create today.
    assert "artifact_registry" in state.resources
    assert "service_account" in state.resources
    # Cloud SQL + VPC Connector are deferred and absent unless requested.
    assert "cloud_sql" not in state.resources
    assert "vpc_connector" not in state.resources


@pytest.mark.asyncio
async def test_provision_creates_artifact_registry(patched_provisioner) -> None:
    await patched_provisioner.provision(_payload())
    patched_provisioner._ensure_artifact_registry_repo.assert_awaited_once()
    kwargs = patched_provisioner._ensure_artifact_registry_repo.await_args.kwargs
    assert kwargs["project"] == "test-proj"
    assert kwargs["region"] == "us-central1"
    assert kwargs["repo"] == "agentbreeder"
    assert kwargs["fields"]["GOOGLE_CLOUD_PROJECT"] == "test-proj"


@pytest.mark.asyncio
async def test_provision_creates_service_account_with_four_default_roles(
    patched_provisioner,
) -> None:
    await patched_provisioner.provision(_payload())
    patched_provisioner._ensure_service_account.assert_awaited_once()
    patched_provisioner._bind_iam_roles.assert_awaited_once()
    roles = patched_provisioner._bind_iam_roles.await_args.kwargs["roles"]
    assert set(roles) == {
        "roles/storage.objectViewer",
        "roles/cloudbuild.builds.builder",
        "roles/logging.logWriter",
        "roles/secretmanager.secretAccessor",
    }


@pytest.mark.asyncio
async def test_provision_honours_custom_repo_name(patched_provisioner) -> None:
    await patched_provisioner.provision(_payload({"GCP_ARTIFACT_REGISTRY_REPO": "team-x"}))
    args = patched_provisioner._ensure_artifact_registry_repo.await_args.kwargs
    assert args["repo"] == "team-x"


@pytest.mark.asyncio
async def test_provision_calls_progress_callback(patched_provisioner) -> None:
    messages: list[str] = []

    async def _capture(msg: str) -> None:
        messages.append(msg)

    await patched_provisioner.provision(_payload(), progress=_capture)
    assert any("Artifact Registry" in m for m in messages)
    assert any("Service Account" in m for m in messages)
    assert any("provision complete" in m for m in messages)


@pytest.mark.asyncio
async def test_provision_records_deferred_markers_when_requested(patched_provisioner) -> None:
    state = await patched_provisioner.provision(
        _payload({"GCP_PROVISION_CLOUD_SQL": "1", "GCP_PROVISION_VPC_CONNECTOR": "1"})
    )
    assert state.resources["cloud_sql"]["status"] == "deferred"
    assert state.resources["vpc_connector"]["status"] == "deferred"


@pytest.mark.asyncio
async def test_provision_raises_when_project_missing() -> None:
    p = GCPProvisioner()
    payload = InfraValidationInput(cloud="gcp", region="us-central1", mode="simple", fields={})
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        await p.provision(payload)


@pytest.mark.asyncio
async def test_provision_is_idempotent(patched_provisioner) -> None:
    """Re-running provision is safe because every helper is itself idempotent."""
    state1 = await patched_provisioner.provision(_payload())
    state2 = await patched_provisioner.provision(_payload())
    # Resources are identical across runs.
    assert (
        state1.resources["service_account"]["email"]
        == state2.resources["service_account"]["email"]
    )


# -- destroy() reverses ----------------------------------------------------


def _state_for_demo() -> InfraState:
    return InfraState(
        cloud="gcp",
        region="us-central1",
        provisioned_by="agentbreeder.GCPProvisioner",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources={
            "artifact_registry": {
                "name": "projects/test-proj/locations/us-central1/repositories/agentbreeder",
                "repo": "agentbreeder",
                "region": "us-central1",
            },
            "service_account": {
                "email": "ab-demo@test-proj.iam.gserviceaccount.com",
                "sa_id": "ab-demo",
                "project": "test-proj",
                "roles": [],
            },
        },
    )


@pytest.mark.asyncio
async def test_destroy_invokes_each_resource_delete(patched_provisioner) -> None:
    await patched_provisioner.destroy(_state_for_demo())
    patched_provisioner._delete_service_account.assert_awaited_once()
    patched_provisioner._delete_artifact_registry_repo.assert_awaited_once()


@pytest.mark.asyncio
async def test_destroy_rejects_non_gcp_state() -> None:
    p = GCPProvisioner()
    state = InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="t",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
    )
    with pytest.raises(ValueError, match="state.cloud is"):
        await p.destroy(state)


@pytest.mark.asyncio
async def test_destroy_swallows_individual_delete_failures(patched_provisioner) -> None:
    """One resource failing to delete must not block the rest of teardown."""
    patched_provisioner._delete_artifact_registry_repo.side_effect = RuntimeError("boom")
    # Should not raise.
    await patched_provisioner.destroy(_state_for_demo())
    # Service account delete still attempted.
    patched_provisioner._delete_service_account.assert_awaited_once()
