"""Coverage-boost tests for low-coverage CLI commands.

Targets:
    - secret.py         (54% -> 80%+)
    - logs.py           (66% -> 80%+)
    - template.py       (62% -> 80%+)
    - eject.py          (77% -> 80%+)
    - validate.py       (75% -> 80%+)
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest
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


# ================================================================
# Secret — list table, set, get, delete, rotate, migrate
# ================================================================


class TestSecretListTable:
    """Lines 80-92: list table rendering."""

    def test_list_table_with_entries(self) -> None:
        entry = MagicMock()
        entry.name = "API_KEY"
        entry.masked_value = "****1234"
        entry.backend = "env"
        entry.updated_at = datetime(2025, 1, 1)
        entry.to_dict.return_value = {
            "name": "API_KEY",
            "masked_value": "****1234",
        }

        backend = MagicMock()
        backend.list = AsyncMock(return_value=[entry])

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(app, ["secret", "list"])

        assert result.exit_code == 0
        assert "API_KEY" in result.output

    def test_list_table_no_updated_at(self) -> None:
        entry = MagicMock()
        entry.name = "KEY"
        entry.masked_value = "****"
        entry.backend = "env"
        entry.updated_at = None

        backend = MagicMock()
        backend.list = AsyncMock(return_value=[entry])

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(app, ["secret", "list"])

        assert result.exit_code == 0


class TestSecretSetPrompted:
    """Lines 110-116: set with prompted value."""

    def test_set_prompts_for_value(self) -> None:
        backend = MagicMock()
        backend.backend_name = "env"
        backend.set = AsyncMock()
        # Track K: secret_set probes b.get() to decide created vs updated
        backend.get = AsyncMock(return_value=None)

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "set", "MY_KEY"],
                input="s3cr3t\n",
            )

        assert result.exit_code == 0
        backend.set.assert_called_once()


class TestSecretDeleteConfirm:
    """Lines 178-181: delete without --force."""

    def test_delete_cancelled(self) -> None:
        backend = MagicMock()
        backend.delete = AsyncMock()

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "delete", "MY_KEY"],
                input="n\n",
            )

        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_delete_confirmed(self) -> None:
        backend = MagicMock()
        backend.delete = AsyncMock()

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "delete", "MY_KEY"],
                input="y\n",
            )

        assert result.exit_code == 0
        backend.delete.assert_called_once()


class TestSecretRotate:
    """Lines 218-233: rotate subcommand."""

    def test_rotate_success_rich(self) -> None:
        backend = MagicMock()
        backend.rotate = AsyncMock()

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
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

        assert result.exit_code == 0
        assert "rotated" in result.output.lower()

    def test_rotate_prompted(self) -> None:
        backend = MagicMock()
        backend.rotate = AsyncMock()

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "rotate", "MY_KEY"],
                input="newval\nnewval\n",
            )

        assert result.exit_code == 0


class TestSecretMigrate:
    """Lines 271-361: migrate subcommand."""

    def test_migrate_dry_run(self) -> None:
        from engine.secrets.env_backend import EnvBackend

        src = MagicMock(spec=EnvBackend)
        src.list_raw.return_value = {
            "API_KEY": "val1",
            "DB_PASS": "val2",
        }

        dst = MagicMock()
        dst.set = AsyncMock()

        def mock_get_backend(name, **kw):
            if name == "env":
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "env",
                    "--to",
                    "aws",
                    "--dry-run",
                ],
            )

        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
        dst.set.assert_not_called()

    def test_migrate_actual(self) -> None:
        from engine.secrets.env_backend import EnvBackend

        src = MagicMock(spec=EnvBackend)
        src.list_raw.return_value = {
            "API_KEY": "val1",
        }

        dst = MagicMock()
        dst.set = AsyncMock()

        def mock_get_backend(name, **kw):
            if name == "env":
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "env",
                    "--to",
                    "aws",
                ],
            )

        assert result.exit_code == 0
        dst.set.assert_called_once()

    def test_migrate_with_include_exclude(self) -> None:
        from engine.secrets.env_backend import EnvBackend

        src = MagicMock(spec=EnvBackend)
        src.list_raw.return_value = {
            "API_KEY": "val1",
            "DB_PASS": "val2",
            "OTHER": "val3",
        }

        dst = MagicMock()
        dst.set = AsyncMock()

        def mock_get_backend(name, **kw):
            if name == "env":
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "env",
                    "--to",
                    "aws",
                    "--include",
                    "API_KEY",
                    "--include",
                    "DB_PASS",
                    "--exclude",
                    "DB_PASS",
                ],
            )

        assert result.exit_code == 0
        # Only API_KEY should be migrated
        assert dst.set.call_count == 1

    def test_migrate_no_candidates(self) -> None:
        from engine.secrets.env_backend import EnvBackend

        src = MagicMock(spec=EnvBackend)
        src.list_raw.return_value = {}

        dst = MagicMock()

        def mock_get_backend(name, **kw):
            if name == "env":
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "env",
                    "--to",
                    "aws",
                ],
            )

        assert result.exit_code == 0
        assert "No secrets" in result.output

    def test_migrate_json_output(self) -> None:
        from engine.secrets.env_backend import EnvBackend

        src = MagicMock(spec=EnvBackend)
        src.list_raw.return_value = {
            "API_KEY": "val1",
        }

        dst = MagicMock()
        dst.set = AsyncMock()

        def mock_get_backend(name, **kw):
            if name == "env":
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "env",
                    "--to",
                    "aws",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        assert "migrated" in result.output
        assert "API_KEY" in result.output

    def test_migrate_with_errors(self) -> None:
        from engine.secrets.env_backend import EnvBackend

        src = MagicMock(spec=EnvBackend)
        src.list_raw.return_value = {
            "KEY1": "v1",
            "KEY2": "v2",
        }

        dst = MagicMock()
        dst.set = AsyncMock(side_effect=[None, RuntimeError("perm denied")])

        def mock_get_backend(name, **kw):
            if name == "env":
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "env",
                    "--to",
                    "aws",
                ],
            )

        assert result.exit_code == 0
        assert "failed" in result.output.lower()

    def test_migrate_from_non_env_backend(self) -> None:
        """When source is not env, iterates entries."""
        entry = MagicMock()
        entry.name = "KEY1"

        src = MagicMock()
        src.list = AsyncMock(return_value=[entry])
        src.get = AsyncMock(return_value="val1")

        dst = MagicMock()
        dst.set = AsyncMock()

        call_count = [0]

        def mock_get_backend(name, **kw):
            if call_count[0] == 0:
                call_count[0] += 1
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "aws",
                    "--to",
                    "gcp",
                ],
            )

        assert result.exit_code == 0

    def test_migrate_dry_run_json(self) -> None:
        from engine.secrets.env_backend import EnvBackend

        src = MagicMock(spec=EnvBackend)
        src.list_raw.return_value = {"K": "v"}

        dst = MagicMock()

        def mock_get_backend(name, **kw):
            if name == "env":
                return src
            return dst

        with patch(
            "cli.commands.secret._get_backend",
            side_effect=mock_get_backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "migrate",
                    "--from",
                    "env",
                    "--to",
                    "aws",
                    "--dry-run",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        assert "dry_run" in result.output
        assert "would_migrate" in result.output


class TestSecretGetMasking:
    """Lines 150-162: get masking and reveal logic."""

    def test_get_reveal_rich(self) -> None:
        backend = MagicMock()
        backend.get = AsyncMock(return_value="mysecretvalue")

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "get",
                    "MY_KEY",
                    "--reveal",
                ],
            )

        assert result.exit_code == 0
        assert "mysecretvalue" in result.output

    def test_get_short_value_masked(self) -> None:
        """Values <= 4 chars show just dots."""
        backend = MagicMock()
        backend.get = AsyncMock(return_value="ab")

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(
                app,
                ["secret", "get", "MY_KEY"],
            )

        assert result.exit_code == 0


class TestSecretSetTags:
    """Lines 112-116: tag parsing."""

    def test_set_with_tags(self) -> None:
        backend = MagicMock()
        backend.backend_name = "env"
        backend.set = AsyncMock()
        backend.get = AsyncMock(return_value=None)

        with patch(
            "cli.commands.secret._get_backend",
            return_value=backend,
        ):
            result = runner.invoke(
                app,
                [
                    "secret",
                    "set",
                    "MY_KEY",
                    "--value",
                    "val",
                    "--tag",
                    "env=prod",
                    "--tag",
                    "team=eng",
                ],
            )

        assert result.exit_code == 0
        call_kwargs = backend.set.call_args
        tags = call_kwargs.kwargs.get("tags", call_kwargs[1].get("tags"))
        assert tags == {"env": "prod", "team": "eng"}


# ================================================================
# Logs — follow, tail, json, --since
# ================================================================


class TestLogsParseSince:
    """Lines 85-89, 92, 111-134."""

    def test_parse_since_valid_minutes(self) -> None:
        from cli.commands.logs import _parse_since

        dt = _parse_since("5m")
        assert dt is not None
        assert isinstance(dt, datetime)

    def test_parse_since_valid_hours(self) -> None:
        from cli.commands.logs import _parse_since

        dt = _parse_since("2h")
        assert dt is not None

    def test_parse_since_valid_days(self) -> None:
        from cli.commands.logs import _parse_since

        dt = _parse_since("3d")
        assert dt is not None

    def test_parse_since_valid_seconds(self) -> None:
        from cli.commands.logs import _parse_since

        dt = _parse_since("30s")
        assert dt is not None

    def test_parse_since_invalid_unit(self) -> None:
        from cli.commands.logs import _parse_since

        assert _parse_since("5x") is None

    def test_parse_since_empty(self) -> None:
        from cli.commands.logs import _parse_since

        assert _parse_since("") is None

    def test_parse_since_non_numeric(self) -> None:
        from cli.commands.logs import _parse_since

        assert _parse_since("abcm") is None


class TestLogsShowLogs:
    """Lines 111-134, 149-209: _show_logs and _follow_logs."""

    def _state_with_agent(self, name="my-agent", status="running"):
        return {
            "agents": {
                name: {"status": status},
            },
        }

    def test_logs_agent_not_found(self) -> None:
        with patch(
            "cli.commands.logs._load_state",
            return_value={"agents": {}},
        ):
            result = runner.invoke(app, ["logs", "nope"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_logs_agent_not_found_json(self) -> None:
        with patch(
            "cli.commands.logs._load_state",
            return_value={"agents": {}},
        ):
            result = runner.invoke(app, ["logs", "nope", "--json"])

        assert result.exit_code == 1

    def test_logs_stopped_agent_warning(self) -> None:
        mock_deployer = MagicMock()
        mock_deployer.get_logs = AsyncMock(return_value=["line1"])

        with (
            patch(
                "cli.commands.logs._load_state",
                return_value=self._state_with_agent(status="stopped"),
            ),
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
        ):
            result = runner.invoke(app, ["logs", "my-agent"])

        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    def test_logs_show_lines(self) -> None:
        lines = [
            "INFO: starting up",
            "WARN: something",
            "ERROR: bad thing",
            "normal line",
        ]
        mock_deployer = MagicMock()
        mock_deployer.get_logs = AsyncMock(return_value=lines)

        with (
            patch(
                "cli.commands.logs._load_state",
                return_value=self._state_with_agent(),
            ),
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
        ):
            result = runner.invoke(app, ["logs", "my-agent"])

        assert result.exit_code == 0

    def test_logs_trim_to_n_lines(self) -> None:
        lines = [f"line-{i}" for i in range(100)]
        mock_deployer = MagicMock()
        mock_deployer.get_logs = AsyncMock(return_value=lines)

        with (
            patch(
                "cli.commands.logs._load_state",
                return_value=self._state_with_agent(),
            ),
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
        ):
            result = runner.invoke(
                app,
                ["logs", "my-agent", "--lines", "10"],
            )

        assert result.exit_code == 0

    def test_logs_json_output(self) -> None:
        mock_deployer = MagicMock()
        mock_deployer.get_logs = AsyncMock(return_value=["line1", "line2"])

        with (
            patch(
                "cli.commands.logs._load_state",
                return_value=self._state_with_agent(),
            ),
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
        ):
            result = runner.invoke(
                app,
                ["logs", "my-agent", "--json"],
            )

        assert result.exit_code == 0

    def test_logs_runtime_error(self) -> None:
        mock_deployer = MagicMock()
        mock_deployer.get_logs = AsyncMock(side_effect=RuntimeError("container gone"))

        with (
            patch(
                "cli.commands.logs._load_state",
                return_value=self._state_with_agent(),
            ),
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
        ):
            result = runner.invoke(app, ["logs", "my-agent"])

        assert result.exit_code == 1

    def test_logs_no_lines_message(self) -> None:
        mock_deployer = MagicMock()
        mock_deployer.get_logs = AsyncMock(return_value=["Container not found"])

        with (
            patch(
                "cli.commands.logs._load_state",
                return_value=self._state_with_agent(),
            ),
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
        ):
            result = runner.invoke(app, ["logs", "my-agent"])

        assert result.exit_code == 0
        assert "No logs" in result.output

    def test_logs_with_since_invalid(self) -> None:
        with patch(
            "cli.commands.logs._load_state",
            return_value=self._state_with_agent(),
        ):
            result = runner.invoke(
                app,
                ["logs", "my-agent", "--since", "xyz"],
            )

        assert result.exit_code == 1

    def test_logs_agent_not_found_no_agents(self) -> None:
        """No agents deployed at all."""
        with patch(
            "cli.commands.logs._load_state",
            return_value={"agents": {}},
        ):
            result = runner.invoke(app, ["logs", "nope"])

        assert result.exit_code == 1
        assert "No agents deployed" in result.output

    def test_logs_agent_not_found_with_suggestions(self) -> None:
        """Other agents exist, suggest them."""
        with patch(
            "cli.commands.logs._load_state",
            return_value={"agents": {"other-agent": {"status": "running"}}},
        ):
            result = runner.invoke(app, ["logs", "nope"])

        assert result.exit_code == 1
        assert "other-agent" in result.output


class TestLogsFollowMode:
    """Lines 149-209: _follow_logs."""

    def test_follow_ctrl_c_breaks(self) -> None:
        mock_deployer = MagicMock()
        mock_deployer.get_logs = AsyncMock(side_effect=RuntimeError("gone"))

        with (
            patch(
                "cli.commands.logs._load_state",
                return_value={"agents": {"my-agent": {"status": "running"}}},
            ),
            patch(
                "engine.deployers.docker_compose.DockerComposeDeployer",
                return_value=mock_deployer,
            ),
        ):
            result = runner.invoke(
                app,
                ["logs", "my-agent", "--follow"],
            )

        assert result.exit_code == 0


class TestLogsPrintLogLine:
    """_print_log_line color-coding."""

    def test_print_error_line(self) -> None:
        from cli.commands.logs import _print_log_line

        _print_log_line("ERROR something broke")

    def test_print_warn_line(self) -> None:
        from cli.commands.logs import _print_log_line

        _print_log_line("WARN potential issue")

    def test_print_info_line(self) -> None:
        from cli.commands.logs import _print_log_line

        _print_log_line("INFO startup complete")

    def test_print_normal_line(self) -> None:
        from cli.commands.logs import _print_log_line

        _print_log_line("just a line")

    def test_print_exception_line(self) -> None:
        from cli.commands.logs import _print_log_line

        _print_log_line("Traceback (most recent call last)")


# ================================================================
# Template — list internals, use subcommand
# ================================================================


class TestTemplateList:
    """Lines 56-74: list rendering with data."""

    def test_list_with_data(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "name": "qa-bot",
                    "version": "1.0",
                    "category": "support",
                    "framework": "langgraph",
                    "status": "published",
                    "use_count": 42,
                },
            ],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(app, ["template", "list"])

        assert result.exit_code == 0
        assert "qa-bot" in result.output

    def test_list_with_filters(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(
                app,
                [
                    "template",
                    "list",
                    "--category",
                    "support",
                    "--framework",
                    "langgraph",
                    "--status",
                    "published",
                ],
            )

        assert result.exit_code == 0


class TestTemplateCreateErrors:
    """Lines 121-122: create API error."""

    def test_create_api_error(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        mock_client = AsyncMock()
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
                    "my-tpl",
                ],
            )

        assert result.exit_code == 1


class TestTemplateUse:
    """Lines 146-176: use subcommand."""

    def test_use_template_success(self) -> None:
        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {
            "data": [
                {"id": "tpl-1", "name": "qa-bot"},
            ],
        }

        inst_resp = MagicMock()
        inst_resp.status_code = 200
        inst_resp.json.return_value = {
            "data": {"yaml_content": VALID_YAML},
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=list_resp)
        mock_client.post = AsyncMock(return_value=inst_resp)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "agent.yaml"
            with patch("httpx.AsyncClient") as mock_cls:
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                result = runner.invoke(
                    app,
                    [
                        "template",
                        "use",
                        "qa-bot",
                        "--output",
                        str(out),
                    ],
                )

        assert result.exit_code == 0
        assert "Generated" in result.output

    def test_use_template_not_found(self) -> None:
        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {"data": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=list_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(
                app,
                ["template", "use", "nonexistent"],
            )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_use_template_list_api_error(self) -> None:
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Server Error"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(
                app,
                ["template", "use", "my-tpl"],
            )

        assert result.exit_code == 1

    def test_use_template_instantiate_error(self) -> None:
        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {
            "data": [
                {"id": "tpl-1", "name": "qa-bot"},
            ],
        }

        inst_resp = MagicMock()
        inst_resp.status_code = 422
        inst_resp.text = "Invalid params"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=list_resp)
        mock_client.post = AsyncMock(return_value=inst_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = runner.invoke(
                app,
                ["template", "use", "qa-bot"],
            )

        assert result.exit_code == 1


# ================================================================
# Eject — remaining branches
# ================================================================


class TestEjectRemainingBranches:
    """Lines 126-127, 228-236, 247-248, 289-329."""

    def test_python_sdk_with_deploy(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        yaml_str = VALID_YAML + (
            "deploy:\n  cloud: aws\n  runtime: ecs-fargate\n  region: us-east-1\n"
        )
        code = _generate_python_sdk(yaml_str)
        assert "aws" in code
        assert "ecs-fargate" in code
        assert "us-east-1" in code

    def test_python_sdk_with_tags(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        yaml_str = VALID_YAML + ("tags:\n  - production\n  - support\n")
        code = _generate_python_sdk(yaml_str)
        assert ".tag(" in code
        assert "production" in code

    def test_python_sdk_model_with_all_params(self) -> None:
        from cli.commands.eject import _generate_python_sdk

        yaml_str = (
            "name: x\nversion: 1.0.0\n"
            "team: eng\nowner: a@b.com\n"
            "framework: langgraph\n"
            "model:\n"
            "  primary: gpt-4o\n"
            "  fallback: claude-sonnet-4\n"
            "  temperature: 0.3\n"
            "  max_tokens: 2048\n"
            "deploy:\n  cloud: local\n"
        )
        code = _generate_python_sdk(yaml_str)
        assert "fallback" in code
        assert "temperature=0.3" in code
        assert "max_tokens=2048" in code

    def test_typescript_sdk_with_model_opts(self) -> None:
        from cli.commands.eject import (
            _generate_typescript_sdk,
        )

        yaml_str = (
            "name: x\nversion: 1.0.0\n"
            "team: eng\nowner: a@b.com\n"
            "framework: langgraph\n"
            "model:\n"
            "  primary: gpt-4o\n"
            "  fallback: claude-sonnet-4\n"
            "  temperature: 0.3\n"
            "deploy:\n  cloud: local\n"
        )
        code = _generate_typescript_sdk(yaml_str)
        assert "fallback" in code
        assert "temperature" in code

    def test_typescript_sdk_with_deploy(self) -> None:
        from cli.commands.eject import (
            _generate_typescript_sdk,
        )

        yaml_str = VALID_YAML.replace("cloud: local", "cloud: aws")
        code = _generate_typescript_sdk(yaml_str)
        assert "withDeploy" in code
        assert "aws" in code

    def test_typescript_sdk_with_tags(self) -> None:
        from cli.commands.eject import (
            _generate_typescript_sdk,
        )

        yaml_str = VALID_YAML + ("tags:\n  - production\n")
        code = _generate_typescript_sdk(yaml_str)
        assert ".tag(" in code

    def test_typescript_sdk_with_guardrails(self) -> None:
        from cli.commands.eject import (
            _generate_typescript_sdk,
        )

        yaml_str = VALID_YAML + ("guardrails:\n  - pii_detection\n")
        code = _generate_typescript_sdk(yaml_str)
        assert "withGuardrail" in code

    def test_typescript_sdk_with_prompts(self) -> None:
        from cli.commands.eject import (
            _generate_typescript_sdk,
        )

        yaml_str = VALID_YAML + ("prompts:\n  system: prompts/support-v3\n")
        code = _generate_typescript_sdk(yaml_str)
        assert "withPrompt" in code

    def test_typescript_sdk_tool_by_name(self) -> None:
        from cli.commands.eject import (
            _generate_typescript_sdk,
        )

        yaml_str = VALID_YAML + ("tools:\n  - name: calc\n")
        code = _generate_typescript_sdk(yaml_str)
        assert "calc" in code

    def test_eject_unsupported_sdk(self) -> None:
        """eject command with unsupported --sdk value."""
        # The command raises typer.Exit; catch that directly. typer >=0.26
        # bundles its own click, so typer.Exit is no longer click.exceptions.Exit.
        from typer import Exit as TyperExit

        from cli.commands.eject import eject as eject_fn

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        with pytest.raises(TyperExit) as exc_info:
            eject_fn(
                config_path=Path(f.name),
                sdk="ruby",
                output=None,
            )
        assert exc_info.value.exit_code == 1

    def test_eject_default_output_path(self) -> None:
        """eject without --output uses agents/<name>/..."""
        from cli.commands.eject import eject as eject_fn

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        with tempfile.TemporaryDirectory() as td:
            import os

            old_cwd = os.getcwd()
            try:
                os.chdir(td)
                eject_fn(
                    config_path=Path(f.name),
                    sdk="python",
                    output=None,
                )
                expected = Path(td) / "agents" / "test-agent" / "agent_sdk.py"
                assert expected.exists()
            finally:
                os.chdir(old_cwd)

    def test_eject_with_output_path(self) -> None:
        from cli.commands.eject import eject as eject_fn

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "my_agent.py"
            eject_fn(
                config_path=Path(f.name),
                sdk="python",
                output=str(out),
            )
            assert out.exists()
            content = out.read_text()
            assert "test-agent" in content

    def test_eject_typescript_output(self) -> None:
        from cli.commands.eject import eject as eject_fn

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "my_agent.ts"
            eject_fn(
                config_path=Path(f.name),
                sdk="typescript",
                output=str(out),
            )
            assert out.exists()
            content = out.read_text()
            assert "@agentbreeder/sdk" in content


# ================================================================
# Validate — mcp config, json output
# ================================================================


class TestValidateMcpConfig:
    """MCP server configs are skipped by `agentbreeder validate`."""

    def test_validate_mcp_config_skipped(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: my-mcp\ntransport: stdio\ncommand: npx\n")
        f.close()

        result = runner.invoke(app, ["validate", f.name])

        assert result.exit_code == 0
        assert "Skipped" in result.output

    def test_validate_mcp_config_skipped_json(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: my-mcp\ntransport: stdio\ncommand: npx\n")
        f.close()

        result = runner.invoke(app, ["validate", f.name, "--json"])

        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["skipped"] is True

    def test_validate_detect_raises_returns_agent(
        self,
    ) -> None:
        """_detect_config_type falls back to agent."""
        from cli.commands.validate import _detect_config_type

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("not: valid: yaml: [[[\n")
        f.close()

        result = _detect_config_type(Path(f.name))
        assert result == "agent"
