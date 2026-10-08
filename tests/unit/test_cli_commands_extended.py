"""Tests for CLI commands — deploy, secret, template, eject, chat."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

VALID_YAML = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""


# ── Deploy Command ──────────────────────────────────────────────────


class TestDeployCommand:
    """Tests for `agentbreeder deploy`."""

    def test_deploy_help(self) -> None:
        result = runner.invoke(app, ["deploy", "--help"])
        assert result.exit_code == 0
        assert "agent.yaml" in result.output.lower() or "config" in result.output.lower()

    def test_deploy_missing_file(self) -> None:
        result = runner.invoke(app, ["deploy", "/nonexistent.yaml"])
        assert result.exit_code != 0

    def test_deploy_invalid_yaml_fails(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\n")
        f.close()
        result = runner.invoke(app, ["deploy", f.name])
        assert result.exit_code == 1

    def test_deploy_valid_yaml_success(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.agent_name = "test-agent"
        mock_result.version = "1.0.0"
        mock_result.endpoint_url = "http://localhost:8080"
        mock_result.model_dump.return_value = {
            "agent_name": "test-agent",
            "version": "1.0.0",
            "endpoint_url": "http://localhost:8080",
        }

        with patch("cli.commands.deploy.DeployEngine") as mock_engine_cls:
            engine_inst = mock_engine_cls.return_value
            engine_inst.deploy = AsyncMock(return_value=mock_result)
            result = runner.invoke(app, ["deploy", f.name])

        assert result.exit_code == 0
        assert "successful" in result.output.lower() or "test-agent" in result.output

    def test_deploy_json_output_success(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "agent_name": "test-agent",
            "version": "1.0.0",
            "endpoint_url": "http://localhost:8080",
        }

        with patch("cli.commands.deploy.DeployEngine") as mock_engine_cls:
            engine_inst = mock_engine_cls.return_value
            engine_inst.deploy = AsyncMock(return_value=mock_result)
            result = runner.invoke(app, ["deploy", f.name, "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["agent_name"] == "test-agent"

    def test_deploy_engine_exception_json(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        with patch("cli.commands.deploy.DeployEngine") as mock_engine_cls:
            engine_inst = mock_engine_cls.return_value
            engine_inst.deploy = AsyncMock(side_effect=RuntimeError("boom"))
            result = runner.invoke(app, ["deploy", f.name, "--json"])

        assert result.exit_code == 1
        found_json = False
        for line in result.output.strip().splitlines():
            try:
                out = json.loads(line)
                assert "error" in out
                found_json = True
                break
            except json.JSONDecodeError:
                continue
        assert found_json or "error" in result.output.lower()

    def test_deploy_target_flag(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.agent_name = "test-agent"
        mock_result.version = "1.0.0"
        mock_result.endpoint_url = "http://localhost:8080"
        mock_result.model_dump.return_value = {}

        with patch("cli.commands.deploy.DeployEngine") as mock_engine_cls:
            engine_inst = mock_engine_cls.return_value
            engine_inst.deploy = AsyncMock(return_value=mock_result)
            result = runner.invoke(app, ["deploy", f.name, "--target", "aws"])

        assert result.exit_code == 0
        call_kwargs = engine_inst.deploy.call_args
        assert call_kwargs[1]["target"] == "aws" or (
            len(call_kwargs[0]) > 1 and call_kwargs[0][1] == "aws"
        )

    def test_deploy_provision_flag_passed_to_engine(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.agent_name = "test-agent"
        mock_result.version = "1.0.0"
        mock_result.endpoint_url = "http://localhost:8080"
        mock_result.model_dump.return_value = {}

        with patch("cli.commands.deploy.DeployEngine") as mock_engine_cls:
            engine_inst = mock_engine_cls.return_value
            engine_inst.deploy = AsyncMock(return_value=mock_result)
            result = runner.invoke(
                app, ["deploy", f.name, "--target", "ecs-fargate", "--provision", "--local"]
            )

        assert result.exit_code == 0
        assert engine_inst.deploy.call_args[1]["provision"] is True

    def test_deploy_no_provision_flag_defaults_false(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.agent_name = "test-agent"
        mock_result.version = "1.0.0"
        mock_result.endpoint_url = "http://localhost:8080"
        mock_result.model_dump.return_value = {}

        with patch("cli.commands.deploy.DeployEngine") as mock_engine_cls:
            engine_inst = mock_engine_cls.return_value
            engine_inst.deploy = AsyncMock(return_value=mock_result)
            result = runner.invoke(app, ["deploy", f.name, "--local"])

        assert result.exit_code == 0
        assert engine_inst.deploy.call_args[1]["provision"] is False


# ── Secret Subcommands ─────────────────────────────────────────────


class TestSecretCommand:
    """Tests for `agentbreeder secret` subcommands."""

    def test_secret_help(self) -> None:
        result = runner.invoke(app, ["secret", "--help"])
        assert result.exit_code == 0
        assert "secret" in result.output.lower()

    def test_secret_no_args_shows_help(self) -> None:
        result = runner.invoke(app, ["secret"])
        # no_args_is_help=True causes exit code 0 or 2
        assert result.exit_code in (0, 2)
        assert "list" in result.output.lower() or "help" in result.output.lower()

    def test_secret_list_help(self) -> None:
        result = runner.invoke(app, ["secret", "list", "--help"])
        assert result.exit_code == 0
        assert "backend" in result.output.lower()

    def test_secret_list_env_backend(self) -> None:
        mock_backend = MagicMock()
        mock_backend.list = AsyncMock(return_value=[])

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "list"])

        assert result.exit_code == 0
        assert "No secrets" in result.output or result.output.strip() == ""

    def test_secret_list_json_empty(self) -> None:
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.list = AsyncMock(return_value=[])

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "list", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        # Track K JSON shape: { workspace, backend, entries }
        assert output["backend"] == "env"
        assert output["entries"] == []

    def test_secret_list_json_with_entries(self) -> None:
        entry = MagicMock()
        entry.to_dict.return_value = {
            "name": "MY_KEY",
            "backend": "env",
            "masked_value": "****abcd",
        }
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.list = AsyncMock(return_value=[entry])

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "list", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        # Track K JSON shape: { workspace, backend, entries }
        assert output["backend"] == "env"
        assert len(output["entries"]) == 1
        assert output["entries"][0]["name"] == "MY_KEY"

    def test_secret_list_invalid_backend(self) -> None:
        result = runner.invoke(app, ["secret", "list", "--backend", "bogus"])
        assert result.exit_code != 0

    def test_secret_set_with_value(self) -> None:
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.set = AsyncMock()
        # Track K: secret_set probes b.get() to decide created vs updated
        mock_backend.get = AsyncMock(return_value=None)

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "set", "MY_KEY", "--value", "s3cr3t"],
            )

        assert result.exit_code == 0
        mock_backend.set.assert_called_once()

    def test_secret_set_json_output(self) -> None:
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.set = AsyncMock()
        mock_backend.get = AsyncMock(return_value=None)

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "set",
                    "MY_KEY",
                    "--value",
                    "s3cr3t",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["name"] == "MY_KEY"
        assert output["status"] == "ok"
        # Track K added "operation" — created when b.get() returned None
        assert output["operation"] == "created"

    def test_secret_get_found(self) -> None:
        mock_backend = MagicMock()
        mock_backend.get = AsyncMock(return_value="secret-value-1234")

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "get", "MY_KEY"])

        assert result.exit_code == 0
        assert "MY_KEY" in result.output

    def test_secret_get_not_found(self) -> None:
        mock_backend = MagicMock()
        mock_backend.get = AsyncMock(return_value=None)

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "get", "MISSING"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_secret_get_json_masked(self) -> None:
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.get = AsyncMock(return_value="secret-value-1234")

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "get", "MY_KEY", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert "masked_value" in output
        assert "value" not in output

    def test_secret_get_json_reveal(self) -> None:
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.get = AsyncMock(return_value="secret-value-1234")

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "get", "MY_KEY", "--json", "--reveal"],
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["value"] == "secret-value-1234"

    def test_secret_delete_force(self) -> None:
        mock_backend = MagicMock()
        mock_backend.delete = AsyncMock()

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "delete", "MY_KEY", "--force"])

        assert result.exit_code == 0
        mock_backend.delete.assert_called_once()

    def test_secret_delete_json(self) -> None:
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.delete = AsyncMock()

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "delete", "MY_KEY", "--force", "--json"],
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["deleted"] is True

    def test_secret_delete_not_found(self) -> None:
        mock_backend = MagicMock()
        mock_backend.delete = AsyncMock(side_effect=KeyError("MY_KEY"))

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "delete", "MY_KEY", "--force"])

        assert result.exit_code == 1

    def test_secret_rotate_with_value_json(self) -> None:
        mock_backend = MagicMock()
        mock_backend.backend_name = "env"
        mock_backend.rotate = AsyncMock()

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "rotate",
                    "MY_KEY",
                    "--value",
                    "new-val",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["rotated"] is True

    def test_secret_rotate_key_not_found(self) -> None:
        mock_backend = MagicMock()
        mock_backend.rotate = AsyncMock(side_effect=KeyError("MY_KEY not found"))

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "rotate",
                    "MY_KEY",
                    "--value",
                    "new-val",
                ],
            )

        assert result.exit_code == 1

    def test_secret_migrate_same_backend_fails(self) -> None:
        result = runner.invoke(
            app,
            ["secret", "migrate", "--from", "env", "--to", "env"],
        )
        assert result.exit_code != 0

    def test_gcp_backend_prefix_slash_is_sanitized(self) -> None:
        """#123: slashes in the prefix must be replaced with underscores for GCP."""
        from cli.commands.secret import _get_backend as real_get_backend

        fake_factory_calls: list[dict] = []

        def capturing_factory(backend: str, **kwargs):
            fake_factory_calls.append({"backend": backend, **kwargs})
            mock_b = MagicMock()
            mock_b.list = AsyncMock(return_value=[])
            return mock_b

        # cli.commands.secret imported `get_backend` into its own namespace
        # at module load, so patching the source path doesn't intercept
        # the call. Patch the local reference instead.
        with patch("cli.commands.secret.get_backend", side_effect=capturing_factory):
            real_get_backend("gcp", prefix="agentbreeder/")

        assert len(fake_factory_calls) == 1
        assert "/" not in fake_factory_calls[0].get("prefix", "")
        assert fake_factory_calls[0].get("prefix") == "agentbreeder_"

    def test_non_gcp_backend_prefix_slash_is_not_sanitized(self) -> None:
        """#123: slash sanitization must NOT apply to non-GCP backends."""
        from cli.commands.secret import _get_backend as real_get_backend

        fake_factory_calls: list[dict] = []

        def capturing_factory(backend: str, **kwargs):
            fake_factory_calls.append({"backend": backend, **kwargs})
            mock_b = MagicMock()
            mock_b.list = AsyncMock(return_value=[])
            return mock_b

        with patch("cli.commands.secret.get_backend", side_effect=capturing_factory):
            real_get_backend("aws", prefix="agentbreeder/")

        assert len(fake_factory_calls) == 1
        # AWS allows slashes; prefix should be untouched
        assert fake_factory_calls[0].get("prefix") == "agentbreeder/"


# ── Secret Sync (workspace → cloud auto-mirror) ────────────────────


def _sync_entry(name: str) -> MagicMock:
    e = MagicMock()
    e.name = name
    return e


class TestSecretSync:
    """Tests for `agentbreeder secret sync` (workspace → cloud auto-mirror)."""

    def test_sync_invalid_target(self) -> None:
        result = runner.invoke(app, ["secret", "sync", "--target", "bogus"])
        assert result.exit_code == 2
        assert "bogus" in result.output.lower() or "unknown" in result.output.lower()

    def test_sync_no_candidates(self) -> None:
        src = MagicMock()
        src.backend_name = "env"
        src.list = AsyncMock(return_value=[])
        dst = MagicMock()
        dst.backend_name = "aws"

        with (
            patch(
                "cli.commands.secret._resolve_backend",
                return_value=(src, "default"),
            ),
            patch("cli.commands.secret._get_backend", return_value=dst),
        ):
            result = runner.invoke(app, ["secret", "sync", "--target", "aws"])

        assert result.exit_code == 0
        assert "no secrets to sync" in result.output.lower()

    def test_sync_dry_run_json(self) -> None:
        src = MagicMock()
        src.backend_name = "env"
        src.list = AsyncMock(return_value=[_sync_entry("KEY_A"), _sync_entry("KEY_B")])
        src.get = AsyncMock(side_effect=lambda n: f"value-{n}")
        dst = MagicMock()
        dst.backend_name = "aws"
        dst.set = AsyncMock()

        with (
            patch(
                "cli.commands.secret._resolve_backend",
                return_value=(src, "default"),
            ),
            patch("cli.commands.secret._get_backend", return_value=dst),
        ):
            result = runner.invoke(
                app,
                ["secret", "sync", "--target", "aws", "--dry-run", "--json"],
            )

        assert result.exit_code == 0
        # dry-run never writes to dst
        dst.set.assert_not_called()
        output = json.loads(result.output)
        assert output["target"] == "aws"
        assert output["dry_run"] is True
        assert output["mirrored"] == 2
        assert all(r["status"] == "would_mirror" for r in output["results"])

    def test_sync_actual_mirrors_all(self) -> None:
        src = MagicMock()
        src.backend_name = "env"
        src.list = AsyncMock(return_value=[_sync_entry("KEY_A"), _sync_entry("KEY_B")])
        src.get = AsyncMock(side_effect=lambda n: f"value-{n}")
        dst = MagicMock()
        dst.backend_name = "gcp"
        dst.set = AsyncMock()

        with (
            patch(
                "cli.commands.secret._resolve_backend",
                return_value=(src, "default"),
            ),
            patch("cli.commands.secret._get_backend", return_value=dst),
        ):
            result = runner.invoke(
                app,
                ["secret", "sync", "--target", "gcp", "--json"],
            )

        assert result.exit_code == 0
        assert dst.set.await_count == 2
        # Every set call carries the managed-by + workspace tag pair
        for call in dst.set.await_args_list:
            tags = call.kwargs.get("tags") or {}
            assert tags.get("managed-by") == "agentbreeder"
            assert "workspace" in tags
        output = json.loads(result.output)
        assert output["mirrored"] == 2
        assert output["errors"] == 0

    def test_sync_with_include_filter(self) -> None:
        src = MagicMock()
        src.backend_name = "env"
        src.list = AsyncMock(
            return_value=[
                _sync_entry("KEEP_THIS"),
                _sync_entry("SKIP_THIS"),
            ]
        )
        src.get = AsyncMock(side_effect=lambda n: f"value-{n}")
        dst = MagicMock()
        dst.backend_name = "aws"
        dst.set = AsyncMock()

        with (
            patch(
                "cli.commands.secret._resolve_backend",
                return_value=(src, "default"),
            ),
            patch("cli.commands.secret._get_backend", return_value=dst),
        ):
            result = runner.invoke(
                app,
                ["secret", "sync", "--target", "aws", "--include", "KEEP_THIS"],
            )

        assert result.exit_code == 0
        # Only the included secret is mirrored
        assert dst.set.await_count == 1
        called_name = dst.set.await_args.args[0]
        assert called_name == "KEEP_THIS"

    def test_sync_partial_error(self) -> None:
        src = MagicMock()
        src.backend_name = "env"
        src.list = AsyncMock(return_value=[_sync_entry("OK_KEY"), _sync_entry("BAD_KEY")])
        src.get = AsyncMock(side_effect=lambda n: f"value-{n}")

        # Cloud rejects BAD_KEY but accepts OK_KEY
        async def flaky_set(name, value, tags=None):
            if name == "BAD_KEY":
                raise RuntimeError("cloud rejected key")

        dst = MagicMock()
        dst.backend_name = "aws"
        dst.set = AsyncMock(side_effect=flaky_set)

        with (
            patch(
                "cli.commands.secret._resolve_backend",
                return_value=(src, "default"),
            ),
            patch("cli.commands.secret._get_backend", return_value=dst),
        ):
            result = runner.invoke(
                app,
                ["secret", "sync", "--target", "aws", "--json"],
            )

        # Per Track K, partial failure does not raise — it surfaces per-secret
        # status so callers can decide what to retry.
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["mirrored"] == 1
        assert output["errors"] == 1
        statuses = {r["name"]: r["status"] for r in output["results"]}
        assert statuses["OK_KEY"] == "mirrored"
        assert statuses["BAD_KEY"] == "error"


# ── Template Subcommands ───────────────────────────────────────────


class TestTemplateCommand:
    """Tests for `agentbreeder template` subcommands."""

    def test_template_help(self) -> None:
        result = runner.invoke(app, ["template", "--help"])
        assert result.exit_code == 0
        assert "template" in result.output.lower()

    def test_template_no_args(self) -> None:
        result = runner.invoke(app, ["template"])
        assert result.exit_code in (0, 2)

    def test_template_list_api_error(self) -> None:
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(app, ["template", "list"])

        assert result.exit_code == 1

    def test_template_list_empty(self) -> None:
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(app, ["template", "list"])

        assert result.exit_code == 0
        assert "No templates" in result.output or "no" in result.output.lower()

    def test_template_create_invalid_yaml(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("- not a mapping\n")
        f.close()

        result = runner.invoke(
            app,
            [
                "template",
                "create",
                f.name,
                "--name",
                "bad-tmpl",
            ],
        )
        assert result.exit_code == 1

    def test_template_create_success(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(
                app,
                [
                    "template",
                    "create",
                    f.name,
                    "--name",
                    "my-tmpl",
                    "--description",
                    "A test",
                    "--category",
                    "starter",
                    "--author",
                    "tester",
                ],
            )

        assert result.exit_code == 0
        assert "created" in result.output.lower()

    def test_template_use_invalid_json_params(self) -> None:
        result = runner.invoke(
            app,
            [
                "template",
                "use",
                "my-tmpl",
                "--params",
                "not-json",
            ],
        )
        assert result.exit_code == 1
        assert "json" in result.output.lower()


# ── Eject Command ──────────────────────────────────────────────────


class TestEjectCommand:
    """Tests for `agentbreeder eject` internal functions.

    eject is not registered in main.py, so we test the
    generation functions directly.
    """

    def test_generate_python_sdk_basic(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        code = _generate_python_sdk(VALID_YAML)
        assert "from agenthub import" in code
        assert "test-agent" in code
        assert "gpt-4o" in code
        assert "langgraph" in code

    def test_generate_python_sdk_with_tools(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        yaml_str = VALID_YAML + (
            "tools:\n  - ref: tools/zendesk-mcp\n  - name: search\n    description: Search KB\n"
        )
        code = _generate_python_sdk(yaml_str)
        assert "zendesk-mcp" in code
        assert "search" in code

    def test_generate_python_sdk_with_guardrails(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        yaml_str = VALID_YAML + ("guardrails:\n  - pii_detection\n  - hallucination_check\n")
        code = _generate_python_sdk(yaml_str)
        assert "pii_detection" in code
        assert "hallucination_check" in code

    def test_generate_python_sdk_invalid_yaml(self) -> None:

        from cli.commands.eject import _generate_python_sdk

        with pytest.raises(typer.BadParameter):
            _generate_python_sdk("- just a list\n")

    def test_generate_typescript_sdk_basic(self) -> None:
        from cli.commands.eject import _generate_typescript_sdk

        code = _generate_typescript_sdk(VALID_YAML)
        assert "@agentbreeder/sdk" in code
        assert "test-agent" in code
        assert "gpt-4o" in code

    def test_generate_typescript_sdk_with_tools(self) -> None:
        from cli.commands.eject import _generate_typescript_sdk

        yaml_str = VALID_YAML + ("tools:\n  - ref: tools/slack\n")
        code = _generate_typescript_sdk(yaml_str)
        assert "tools/slack" in code

    def test_generate_typescript_sdk_invalid_yaml(self) -> None:

        from cli.commands.eject import _generate_typescript_sdk

        with pytest.raises(typer.BadParameter):
            _generate_typescript_sdk("- not a dict\n")

    def test_generate_python_sdk_with_memory(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        yaml_str = VALID_YAML + ("memory:\n  backend: redis\n  max_messages: 50\n")
        code = _generate_python_sdk(yaml_str)
        assert "redis" in code
        assert "50" in code

    def test_generate_python_sdk_with_prompts(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        yaml_str = VALID_YAML + ("prompts:\n  system: prompts/support-v3\n")
        code = _generate_python_sdk(yaml_str)
        assert "prompts/support-v3" in code


# ── Chat Command ───────────────────────────────────────────────────


class TestChatCommand:
    """Tests for `agentbreeder chat`."""

    def test_chat_help(self) -> None:
        result = runner.invoke(app, ["chat", "--help"])
        assert result.exit_code == 0
        assert "agent" in result.output.lower()

    def test_chat_connection_error(self) -> None:
        import httpx

        with patch("cli.commands.chat._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("refused")
            mock_get.return_value = mock_client

            # Provide stdin so it tries one message then EOF
            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )

        # JSON mode writes error to stdout
        assert "error" in result.output.lower()

    def test_chat_json_mode_success(self) -> None:
        with patch("cli.commands.chat._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "data": {
                    "response": "Hi there!",
                    "token_count": 10,
                    "cost_estimate": 0.001,
                }
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_get.return_value = mock_client

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )

        assert result.exit_code == 0
        for line in result.output.strip().splitlines():
            try:
                out = json.loads(line)
                assert "response" in out
                break
            except json.JSONDecodeError:
                continue


# ── Eval Subcommands ───────────────────────────────────────────────


class TestEvalCommand:
    """Tests for `agentbreeder eval` subcommands."""

    def test_eval_no_args(self) -> None:
        result = runner.invoke(app, ["eval"])
        assert result.exit_code in (0, 2)


# ── Publish Command ────────────────────────────────────────────────


# ── Submit Command ─────────────────────────────────────────────────
