"""Unit tests for agentbreeder teardown --cloud <gcp|aws|azure|all>."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from cli.commands.teardown import (
    CloudProvider,
    TeardownRow,
    _teardown_aws,
    _teardown_azure,
    _teardown_gcp,
    _run_cloud_teardown,
)
from cli.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_REGISTRY = {
    "demo-agent": {"name": "demo-agent", "status": "running"},
    "other-agent": {"name": "other-agent", "status": "running"},
}


def _write_registry(tmpdir: str) -> Path:
    registry_dir = Path(tmpdir) / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "agents.json").write_text(json.dumps(SAMPLE_REGISTRY))
    return registry_dir


# ---------------------------------------------------------------------------
# _teardown_gcp
# ---------------------------------------------------------------------------


def _make_gcp_sys_modules(run_client_cls, ar_client_cls, sm_client_cls, not_found_exc=Exception):
    """Build a sys.modules patch dict that satisfies the GCP imports inside _teardown_gcp."""
    run_v2_mod = MagicMock()
    run_v2_mod.ServicesClient = run_client_cls

    secretmanager_mod = MagicMock()
    secretmanager_mod.SecretManagerServiceClient = sm_client_cls

    ar_mod = MagicMock()
    ar_mod.ArtifactRegistryClient = ar_client_cls

    api_core_exc_mod = MagicMock()
    api_core_exc_mod.NotFound = not_found_exc

    # Python's import machinery requires parent packages to exist in sys.modules
    # before `from google.cloud import run_v2` can resolve the submodule.
    google_mod = MagicMock()
    google_cloud_mod = MagicMock()
    google_cloud_mod.run_v2 = run_v2_mod
    google_cloud_mod.secretmanager = secretmanager_mod
    google_api_core_mod = MagicMock()

    return {
        "google": google_mod,
        "google.cloud": google_cloud_mod,
        "google.api_core": google_api_core_mod,
        "google.api_core.exceptions": api_core_exc_mod,
        "google.cloud.run_v2": run_v2_mod,
        "google.cloud.secretmanager": secretmanager_mod,
        "google.cloud.artifactregistry_v1": ar_mod,
    }


class TestTeardownGcp:
    def _make_gcp_mocks(self) -> dict:
        """Return a dict of mock GCP client instances with empty resource lists."""
        run_svc = MagicMock()
        run_svc.list_services.return_value = []
        run_client_cls = MagicMock(return_value=run_svc)

        ar_repo = MagicMock()
        ar_repo.list_repositories.return_value = []
        ar_client_cls = MagicMock(return_value=ar_repo)

        sm_svc = MagicMock()
        sm_svc.list_secrets.return_value = []
        sm_client_cls = MagicMock(return_value=sm_svc)

        return {
            "run_client_cls": run_client_cls,
            "ar_client_cls": ar_client_cls,
            "sm_client_cls": sm_client_cls,
            "run_svc": run_svc,
            "ar_repo": ar_repo,
            "sm_svc": sm_svc,
        }

    def test_dry_run_prints_but_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            # Build a mock Cloud Run service with the agentbreeder label
            mock_service = MagicMock()
            mock_service.name = "projects/p/locations/r/services/demo-agent"
            mock_service.labels = {"managed-by": "agentbreeder"}

            mocks = self._make_gcp_mocks()
            mocks["run_svc"].list_services.return_value = [mock_service]

            sys_mods = _make_gcp_sys_modules(
                mocks["run_client_cls"], mocks["ar_client_cls"], mocks["sm_client_cls"]
            )
            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict("sys.modules", sys_mods),
            ):
                rows = _teardown_gcp(
                    region="us-central1",
                    project="my-proj",
                    dry_run=True,
                    agent_filter=None,
                )

            # delete_service must NOT have been called
            mocks["run_svc"].delete_service.assert_not_called()

    def test_idempotent_on_missing_resource(self) -> None:
        """NotFound from GCP SDK should produce a 'not_found' row, not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            mock_service = MagicMock()
            mock_service.name = "projects/p/locations/r/services/demo-agent"
            mock_service.labels = {"managed-by": "agentbreeder"}

            not_found_exc = type("NotFound", (Exception,), {})

            mocks = self._make_gcp_mocks()
            mocks["run_svc"].list_services.return_value = [mock_service]
            mocks["run_svc"].delete_service.side_effect = not_found_exc("gone")

            sys_mods = _make_gcp_sys_modules(
                mocks["run_client_cls"],
                mocks["ar_client_cls"],
                mocks["sm_client_cls"],
                not_found_exc=not_found_exc,
            )
            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict("sys.modules", sys_mods),
            ):
                rows = _teardown_gcp(
                    region="us-central1",
                    project="my-proj",
                    dry_run=False,
                    agent_filter=None,
                )

            run_rows = [r for r in rows if r.resource_type == "Cloud Run Service"]
            assert len(run_rows) == 1
            assert run_rows[0].status == "not_found"

    def test_agent_filter_limits_resources(self) -> None:
        """--agent filter should skip services that don't match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            svc_demo = MagicMock()
            svc_demo.name = "projects/p/locations/r/services/demo-agent"
            svc_demo.labels = {"managed-by": "agentbreeder"}

            svc_other = MagicMock()
            svc_other.name = "projects/p/locations/r/services/other-agent"
            svc_other.labels = {"managed-by": "agentbreeder"}

            mocks = self._make_gcp_mocks()
            mocks["run_svc"].list_services.return_value = [svc_demo, svc_other]

            sys_mods = _make_gcp_sys_modules(
                mocks["run_client_cls"], mocks["ar_client_cls"], mocks["sm_client_cls"]
            )
            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict("sys.modules", sys_mods),
            ):
                rows = _teardown_gcp(
                    region="us-central1",
                    project="my-proj",
                    dry_run=True,
                    agent_filter="demo-agent",
                )

            names = [r.name for r in rows]
            assert "demo-agent" in names
            assert "other-agent" not in names


# ---------------------------------------------------------------------------
# _teardown_aws
# ---------------------------------------------------------------------------


class TestTeardownAws:
    def _make_aws_mocks(self) -> dict:
        ecs_mock = MagicMock()
        ecs_mock.list_clusters.return_value = {"clusterArns": []}
        ecs_mock.get_paginator.return_value.paginate.return_value = [{"taskDefinitionArns": []}]

        ecr_mock = MagicMock()
        ecr_mock.describe_repositories.return_value = {"repositories": []}

        sm_mock = MagicMock()
        sm_paginator = MagicMock()
        sm_paginator.paginate.return_value = [{"SecretList": []}]
        sm_mock.get_paginator.return_value = sm_paginator

        iam_mock = MagicMock()
        iam_paginator = MagicMock()
        iam_paginator.paginate.return_value = [{"Roles": []}]
        iam_mock.get_paginator.return_value = iam_paginator

        boto3_mock = MagicMock()

        def _client(service, **_kw):
            return {
                "ecs": ecs_mock,
                "ecr": ecr_mock,
                "secretsmanager": sm_mock,
                "iam": iam_mock,
            }[service]

        boto3_mock.client.side_effect = _client
        return {
            "boto3_mock": boto3_mock,
            "ecs": ecs_mock,
            "ecr": ecr_mock,
            "sm": sm_mock,
            "iam": iam_mock,
        }

    def test_dry_run_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            mocks = self._make_aws_mocks()

            # Add a matching ECR repo
            mocks["ecr"].describe_repositories.return_value = {
                "repositories": [{"repositoryName": "agentbreeder-demo-agent"}]
            }

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict(
                    "sys.modules",
                    {
                        "boto3": mocks["boto3_mock"],
                        "botocore": MagicMock(),
                        "botocore.exceptions": MagicMock(ClientError=Exception),
                    },
                ),
            ):
                rows = _teardown_aws(region="us-east-1", dry_run=True, agent_filter=None)

            # delete_repository should never be called in dry_run mode
            mocks["ecr"].delete_repository.assert_not_called()

    def test_idempotent_missing_ecr_repo(self) -> None:
        """RepositoryNotFoundException should map to 'not_found', not crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            mocks = self._make_aws_mocks()

            client_error_cls = type(
                "ClientError",
                (Exception,),
                {"response": {"Error": {"Code": "RepositoryNotFoundException"}}},
            )

            mocks["ecr"].describe_repositories.return_value = {
                "repositories": [{"repositoryName": "agentbreeder-demo-agent"}]
            }
            mocks["ecr"].delete_repository.side_effect = client_error_cls("not found")

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict(
                    "sys.modules",
                    {
                        "boto3": mocks["boto3_mock"],
                        "botocore": MagicMock(),
                        "botocore.exceptions": MagicMock(ClientError=client_error_cls),
                    },
                ),
            ):
                rows = _teardown_aws(region="us-east-1", dry_run=False, agent_filter=None)

            ecr_rows = [r for r in rows if r.resource_type == "ECR Repository"]
            assert len(ecr_rows) == 1
            assert ecr_rows[0].status == "not_found"

    def test_iam_roles_prefixed_agentbreeder(self) -> None:
        """IAM roles prefixed 'agentbreeder-' should be included in teardown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            mocks = self._make_aws_mocks()

            iam_paginator = MagicMock()
            iam_paginator.paginate.return_value = [
                {"Roles": [{"RoleName": "agentbreeder-demo-agent-role"}]}
            ]
            mocks["iam"].get_paginator.return_value = iam_paginator
            mocks["iam"].list_attached_role_policies.return_value = {"AttachedPolicies": []}
            mocks["iam"].list_role_policies.return_value = {"PolicyNames": []}

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict(
                    "sys.modules",
                    {
                        "boto3": mocks["boto3_mock"],
                        "botocore": MagicMock(),
                        "botocore.exceptions": MagicMock(ClientError=Exception),
                    },
                ),
            ):
                rows = _teardown_aws(region="us-east-1", dry_run=False, agent_filter=None)

            iam_rows = [r for r in rows if r.resource_type == "IAM Role"]
            assert len(iam_rows) == 1
            assert iam_rows[0].name == "agentbreeder-demo-agent-role"
            assert iam_rows[0].status == "deleted"
            mocks["iam"].delete_role.assert_called_once_with(RoleName="agentbreeder-demo-agent-role")

    def test_agent_filter(self) -> None:
        """--agent should restrict ECR repos to those starting with the agent name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            mocks = self._make_aws_mocks()

            mocks["ecr"].describe_repositories.return_value = {
                "repositories": [
                    {"repositoryName": "agentbreeder-demo-agent"},
                    {"repositoryName": "agentbreeder-other-agent"},
                ]
            }

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict(
                    "sys.modules",
                    {
                        "boto3": mocks["boto3_mock"],
                        "botocore": MagicMock(),
                        "botocore.exceptions": MagicMock(ClientError=Exception),
                    },
                ),
            ):
                rows = _teardown_aws(region="us-east-1", dry_run=True, agent_filter="demo-agent")

            names = [r.name for r in rows]
            assert "agentbreeder-demo-agent" in names
            assert "agentbreeder-other-agent" not in names


# ---------------------------------------------------------------------------
# _teardown_azure
# ---------------------------------------------------------------------------


class TestTeardownAzure:
    def _make_azure_mocks(self) -> dict:
        cred_mock = MagicMock()
        cred_cls = MagicMock(return_value=cred_mock)

        sub_mock = MagicMock()
        sub_item = MagicMock()
        sub_item.subscription_id = "sub-123"
        sub_mock.subscriptions.list.return_value = iter([sub_item])
        sub_client_cls = MagicMock(return_value=sub_mock)

        rg_item = MagicMock()
        rg_item.name = "rg-agentbreeder"
        rg_mock = MagicMock()
        rg_mock.resource_groups.list.return_value = [rg_item]
        rg_client_cls = MagicMock(return_value=rg_mock)

        ca_mock = MagicMock()
        ca_mock.container_apps.list_by_resource_group.return_value = []
        ca_mock.managed_environments.list_by_resource_group.return_value = []
        ca_client_cls = MagicMock(return_value=ca_mock)

        acr_mock = MagicMock()
        acr_mock.registries.list_by_resource_group.return_value = []
        acr_client_cls = MagicMock(return_value=acr_mock)

        kv_mock = MagicMock()
        kv_mock.vaults.list_by_resource_group.return_value = []
        kv_client_cls = MagicMock(return_value=kv_mock)

        return {
            "cred_cls": cred_cls,
            "cred_mock": cred_mock,
            "sub_client_cls": sub_client_cls,
            "rg_client_cls": rg_client_cls,
            "rg_mock": rg_mock,
            "ca_client_cls": ca_client_cls,
            "ca_mock": ca_mock,
            "acr_client_cls": acr_client_cls,
            "acr_mock": acr_mock,
            "kv_client_cls": kv_client_cls,
            "kv_mock": kv_mock,
        }

    def _azure_patch(self, mocks: dict) -> dict:
        return {
            "azure.identity": MagicMock(DefaultAzureCredential=mocks["cred_cls"]),
            "azure.mgmt.resource.subscriptions": MagicMock(
                SubscriptionClient=mocks["sub_client_cls"]
            ),
            "azure.mgmt.resource": MagicMock(
                ResourceManagementClient=mocks["rg_client_cls"]
            ),
            "azure.mgmt.appcontainers": MagicMock(
                ContainerAppsAPIClient=mocks["ca_client_cls"]
            ),
            "azure.mgmt.containerregistry": MagicMock(
                ContainerRegistryManagementClient=mocks["acr_client_cls"]
            ),
            "azure.mgmt.keyvault": MagicMock(
                KeyVaultManagementClient=mocks["kv_client_cls"]
            ),
            "azure.core.exceptions": MagicMock(ResourceNotFoundError=Exception),
        }

    def test_dry_run_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            mocks = self._make_azure_mocks()

            # Add a matching Container App
            app_mock = MagicMock()
            app_mock.name = "demo-agent"
            app_mock.tags = {"managed-by": "agentbreeder"}
            mocks["ca_mock"].container_apps.list_by_resource_group.return_value = [app_mock]

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict("sys.modules", self._azure_patch(mocks)),
            ):
                rows = _teardown_azure(dry_run=True, agent_filter=None, destroy_resource_group=False)

            # begin_delete must NOT be called
            mocks["ca_mock"].container_apps.begin_delete.assert_not_called()

    def test_idempotent_missing_container_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            mocks = self._make_azure_mocks()

            not_found_cls = type("ResourceNotFoundError", (Exception,), {})

            app_mock = MagicMock()
            app_mock.name = "demo-agent"
            app_mock.tags = {"managed-by": "agentbreeder"}
            mocks["ca_mock"].container_apps.list_by_resource_group.return_value = [app_mock]
            mocks["ca_mock"].container_apps.begin_delete.return_value.result.side_effect = (
                not_found_cls("gone")
            )

            azure_patches = self._azure_patch(mocks)
            azure_patches["azure.core.exceptions"] = MagicMock(ResourceNotFoundError=not_found_cls)

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict("sys.modules", azure_patches),
            ):
                rows = _teardown_azure(dry_run=False, agent_filter=None, destroy_resource_group=False)

            app_rows = [r for r in rows if r.resource_type == "Container App"]
            assert len(app_rows) == 1
            assert app_rows[0].status == "not_found"

    def test_agent_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            mocks = self._make_azure_mocks()

            app_demo = MagicMock()
            app_demo.name = "demo-agent"
            app_demo.tags = {"managed-by": "agentbreeder"}

            app_other = MagicMock()
            app_other.name = "other-agent"
            app_other.tags = {"managed-by": "agentbreeder"}

            mocks["ca_mock"].container_apps.list_by_resource_group.return_value = [
                app_demo,
                app_other,
            ]

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch.dict("sys.modules", self._azure_patch(mocks)),
            ):
                rows = _teardown_azure(
                    dry_run=True, agent_filter="demo-agent", destroy_resource_group=False
                )

            names = [r.name for r in rows]
            assert "demo-agent" in names
            assert "other-agent" not in names


# ---------------------------------------------------------------------------
# _run_cloud_teardown — calls all three when cloud=all
# ---------------------------------------------------------------------------


class TestRunCloudTeardown:
    def test_cloud_all_calls_all_three_teardowns(self) -> None:
        """--cloud all should invoke GCP, AWS, and Azure teardowns in sequence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_gcp", return_value=[]) as mock_gcp,
                patch("cli.commands.teardown._teardown_aws", return_value=[]) as mock_aws,
                patch("cli.commands.teardown._teardown_azure", return_value=[]) as mock_azure,
                patch("cli.commands.teardown._print_teardown_table"),
            ):
                exit_code = _run_cloud_teardown(
                    cloud=CloudProvider.all,
                    region="us-east-1",
                    project=None,
                    dry_run=True,
                    agent_filter=None,
                    destroy_resource_group=False,
                )

            mock_gcp.assert_called_once()
            mock_aws.assert_called_once()
            mock_azure.assert_called_once()
            assert exit_code == 0

    def test_cloud_gcp_only_calls_gcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_gcp", return_value=[]) as mock_gcp,
                patch("cli.commands.teardown._teardown_aws", return_value=[]) as mock_aws,
                patch("cli.commands.teardown._teardown_azure", return_value=[]) as mock_azure,
                patch("cli.commands.teardown._print_teardown_table"),
            ):
                _run_cloud_teardown(
                    cloud=CloudProvider.gcp,
                    region="us-central1",
                    project="my-proj",
                    dry_run=True,
                    agent_filter=None,
                    destroy_resource_group=False,
                )

            mock_gcp.assert_called_once()
            mock_aws.assert_not_called()
            mock_azure.assert_not_called()

    def test_returns_exit_code_1_on_error_row(self) -> None:
        """If any cloud function returns an 'error' row the exit code should be 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)
            error_row = TeardownRow("demo-agent", "ECS Service", "error", "permission denied")

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_gcp", return_value=[]),
                patch("cli.commands.teardown._teardown_aws", return_value=[error_row]),
                patch("cli.commands.teardown._teardown_azure", return_value=[]),
                patch("cli.commands.teardown._print_teardown_table"),
            ):
                exit_code = _run_cloud_teardown(
                    cloud=CloudProvider.all,
                    region=None,
                    project=None,
                    dry_run=False,
                    agent_filter=None,
                    destroy_resource_group=False,
                )

            assert exit_code == 1


# ---------------------------------------------------------------------------
# CLI integration — teardown --cloud via app runner
# ---------------------------------------------------------------------------


class TestTeardownCloudCli:
    def test_cloud_dry_run_skips_confirmation(self) -> None:
        """--dry-run should not ask for confirmation and should exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_gcp", return_value=[]),
                patch("cli.commands.teardown._print_teardown_table"),
            ):
                result = runner.invoke(
                    app, ["teardown", "--cloud", "gcp", "--dry-run", "--region", "us-central1"]
                )

            assert result.exit_code == 0

    def test_cloud_force_skips_confirmation(self) -> None:
        """--force should bypass the confirmation prompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_aws", return_value=[]),
                patch("cli.commands.teardown._print_teardown_table"),
            ):
                result = runner.invoke(
                    app, ["teardown", "--cloud", "aws", "--force", "--region", "us-east-1"]
                )

            assert result.exit_code == 0

    def test_cloud_abort_on_no(self) -> None:
        """Answering 'n' should abort with exit code 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            with patch("cli.commands.teardown.REGISTRY_DIR", registry_dir):
                result = runner.invoke(
                    app, ["teardown", "--cloud", "gcp"], input="n\n"
                )

            assert result.exit_code == 0
            assert "Aborted" in result.output

    def test_agent_filter_passed_through_cli(self) -> None:
        """--agent flag should be forwarded to _run_cloud_teardown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_dir = _write_registry(tmpdir)

            with (
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._run_cloud_teardown", return_value=0) as mock_run,
            ):
                result = runner.invoke(
                    app,
                    ["teardown", "--cloud", "aws", "--dry-run", "--agent", "demo-agent"],
                )

            assert result.exit_code == 0
            _, kwargs = mock_run.call_args
            assert kwargs.get("agent_filter") == "demo-agent" or mock_run.call_args[0][4] == "demo-agent"

    def test_existing_single_agent_teardown_unaffected(self) -> None:
        """agentbreeder teardown <name> still works as before (no --cloud)."""
        state = {
            "agents": {
                "my-agent": {
                    "port": 8080,
                    "endpoint_url": "http://localhost:8080",
                    "status": "running",
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(state))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            (registry_dir / "agents.json").write_text(json.dumps({"my-agent": {"status": "running"}}))

            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_container", return_value=True),
            ):
                result = runner.invoke(app, ["teardown", "my-agent", "--force"])

            assert result.exit_code == 0
            assert "Torn down" in result.output
            saved = json.loads(state_file.read_text())
            assert saved["agents"]["my-agent"]["status"] == "stopped"

    def test_no_args_shows_error(self) -> None:
        """Running teardown with no args and no --cloud should error."""
        result = runner.invoke(app, ["teardown"])
        assert result.exit_code != 0
