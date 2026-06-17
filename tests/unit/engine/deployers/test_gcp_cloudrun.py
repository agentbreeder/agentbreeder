"""Project ID resolution precedence for the GCP Cloud Run deployer.

Covers the five-step precedence chain in
``engine.deployers.gcp_cloudrun._resolve_gcp_project_id``:

    1. deploy.env_vars["GCP_PROJECT_ID"]
    2. deploy.env_vars["GOOGLE_CLOUD_PROJECT"]
    3. shell env $GCP_PROJECT_ID
    4. shell env $GOOGLE_CLOUD_PROJECT
    5. `gcloud config get-value project`

Plus side-effects (env propagation, error message).
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from engine.config_parser import AgentConfig, FrameworkType
from engine.deployers.gcp_cloudrun import (
    _extract_cloudrun_config,
    _resolve_gcp_project_id,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_shell_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure shell env vars don't leak into precedence tests."""
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)


@pytest.fixture
def _no_gcloud() -> object:
    """Patch subprocess.run so gcloud branch returns empty / FileNotFoundError."""
    with patch(
        "engine.deployers.gcp_cloudrun.subprocess.run",
        side_effect=FileNotFoundError("gcloud not on PATH"),
    ) as m:
        yield m


def _make_config(env_vars: dict[str, str] | None = None) -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        version="1.0.0",
        team="platform",
        owner="alice@example.com",
        framework=FrameworkType.langgraph,
        model={"primary": "claude-sonnet-4"},
        deploy={
            "cloud": "gcp",
            "region": "us-central1",
            "env_vars": env_vars or {},
            "scaling": {"min": 0, "max": 5},
            "resources": {"cpu": "1", "memory": "512Mi"},
        },
    )


# ---------------------------------------------------------------------------
# Precedence tests — _resolve_gcp_project_id
# ---------------------------------------------------------------------------


class TestResolveGcpProjectIdPrecedence:
    def test_step1_yaml_gcp_project_id_wins(self, _no_gcloud: object) -> None:
        # Even with all lower-precedence sources populated, YAML wins.
        env = {"GCP_PROJECT_ID": "yaml-primary", "GOOGLE_CLOUD_PROJECT": "yaml-fallback"}
        with patch.dict("os.environ", {"GCP_PROJECT_ID": "shell-1"}):
            pid, source = _resolve_gcp_project_id(env)
        assert pid == "yaml-primary"
        assert source == "deploy.env_vars[GCP_PROJECT_ID]"

    def test_step2_yaml_google_cloud_project(self, _no_gcloud: object) -> None:
        env = {"GOOGLE_CLOUD_PROJECT": "yaml-google"}
        # Shell vars present but YAML still wins ahead of them.
        with patch.dict("os.environ", {"GCP_PROJECT_ID": "shell-1"}):
            pid, source = _resolve_gcp_project_id(env)
        assert pid == "yaml-google"
        assert source == "deploy.env_vars[GOOGLE_CLOUD_PROJECT]"

    def test_step3_shell_gcp_project_id(
        self, monkeypatch: pytest.MonkeyPatch, _no_gcloud: object
    ) -> None:
        monkeypatch.setenv("GCP_PROJECT_ID", "shell-gcp")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "shell-google")
        pid, source = _resolve_gcp_project_id({})
        assert pid == "shell-gcp"
        assert source == "$GCP_PROJECT_ID"

    def test_step4_shell_google_cloud_project(
        self, monkeypatch: pytest.MonkeyPatch, _no_gcloud: object
    ) -> None:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "shell-google")
        pid, source = _resolve_gcp_project_id({})
        assert pid == "shell-google"
        assert source == "$GOOGLE_CLOUD_PROJECT"

    def test_step5_gcloud_config(self) -> None:
        fake = subprocess.CompletedProcess(
            args=["gcloud", "config", "get-value", "project"],
            returncode=0,
            stdout="gcloud-project-id\n",
            stderr="",
        )
        with patch(
            "engine.deployers.gcp_cloudrun.subprocess.run", return_value=fake
        ) as run_mock:
            pid, source = _resolve_gcp_project_id({})
        assert pid == "gcloud-project-id"
        assert source == "gcloud config get-value project"
        # Sanity: we invoked subprocess.run with check=False and capture_output.
        kwargs = run_mock.call_args.kwargs
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    def test_step5_gcloud_returns_unset_is_skipped(self) -> None:
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="(unset)\n", stderr=""
        )
        with patch("engine.deployers.gcp_cloudrun.subprocess.run", return_value=fake):
            with pytest.raises(ValueError, match="GCP project ID is required"):
                _resolve_gcp_project_id({})

    def test_step5_gcloud_empty_output_is_skipped(self) -> None:
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("engine.deployers.gcp_cloudrun.subprocess.run", return_value=fake):
            with pytest.raises(ValueError, match="GCP project ID is required"):
                _resolve_gcp_project_id({})

    def test_gcloud_not_installed_is_swallowed(self) -> None:
        with patch(
            "engine.deployers.gcp_cloudrun.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            with pytest.raises(ValueError, match="GCP project ID is required"):
                _resolve_gcp_project_id({})

    def test_error_message_mentions_all_four_paths(self, _no_gcloud: object) -> None:
        with pytest.raises(ValueError) as exc:
            _resolve_gcp_project_id({})
        msg = str(exc.value)
        assert "GCP_PROJECT_ID" in msg
        assert "GOOGLE_CLOUD_PROJECT" in msg
        assert "gcloud config get-value project" in msg


# ---------------------------------------------------------------------------
# Integration: _extract_cloudrun_config side-effects
# ---------------------------------------------------------------------------


class TestExtractCloudRunConfigSideEffects:
    def test_gcloud_discovery_propagates_into_env_vars(self) -> None:
        config = _make_config(env_vars={})
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="discovered-proj\n", stderr=""
        )
        with patch("engine.deployers.gcp_cloudrun.subprocess.run", return_value=fake):
            gcp = _extract_cloudrun_config(config)
        assert gcp.project_id == "discovered-proj"
        # Propagated so downstream stages (e.g. env injection) see a consistent value.
        assert config.deploy.env_vars["GCP_PROJECT_ID"] == "discovered-proj"

    def test_shell_env_propagates_into_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCP_PROJECT_ID", "shell-proj")
        config = _make_config(env_vars={})
        with patch(
            "engine.deployers.gcp_cloudrun.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            gcp = _extract_cloudrun_config(config)
        assert gcp.project_id == "shell-proj"
        assert config.deploy.env_vars["GCP_PROJECT_ID"] == "shell-proj"

    def test_explicit_yaml_value_is_not_overwritten(self) -> None:
        config = _make_config(env_vars={"GCP_PROJECT_ID": "yaml-proj"})
        gcp = _extract_cloudrun_config(config)
        assert gcp.project_id == "yaml-proj"
        assert config.deploy.env_vars["GCP_PROJECT_ID"] == "yaml-proj"
