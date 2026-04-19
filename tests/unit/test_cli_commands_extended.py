"""Tests for CLI commands — deploy, secret, template, orchestration,
eject, chat, eval, publish, submit."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

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

VALID_ORCH_YAML = """\
name: test-orch
version: 1.0.0
strategy: sequential
team: engineering
owner: test@example.com
description: test orchestration
agents:
  summarizer:
    ref: agents/summarizer
  reviewer:
    ref: agents/reviewer
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
        mock_backend.list = AsyncMock(return_value=[])

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "list", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output == []

    def test_secret_list_json_with_entries(self) -> None:
        entry = MagicMock()
        entry.to_dict.return_value = {
            "name": "MY_KEY",
            "backend": "env",
            "masked_value": "****abcd",
        }
        mock_backend = MagicMock()
        mock_backend.list = AsyncMock(return_value=[entry])

        with patch(
            "cli.commands.secret._get_backend",
            return_value=mock_backend,
        ):
            result = runner.invoke(app, ["secret", "list", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "MY_KEY"

    def test_secret_list_invalid_backend(self) -> None:
        result = runner.invoke(app, ["secret", "list", "--backend", "bogus"])
        assert result.exit_code != 0

    def test_secret_set_with_value(self) -> None:
        mock_backend = MagicMock()
        mock_backend.set = AsyncMock()

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
        mock_backend.set = AsyncMock()

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


# ── Orchestration Subcommands ──────────────────────────────────────


class TestOrchestrationCommand:
    """Tests for `agentbreeder orchestration` subcommands."""

    def test_orchestration_help(self) -> None:
        result = runner.invoke(app, ["orchestration", "--help"])
        assert result.exit_code == 0
        assert "orchestration" in result.output.lower()

    def test_orchestration_no_args(self) -> None:
        result = runner.invoke(app, ["orchestration"])
        assert result.exit_code in (0, 2)

    def test_orchestration_validate_valid(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []

        with patch(
            "cli.commands.orchestration.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["orchestration", "validate", f.name])

        assert result.exit_code == 0
        assert "Valid" in result.output or "valid" in result.output.lower()

    def test_orchestration_validate_invalid(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: bad\n")
        f.close()

        mock_err = MagicMock()
        mock_err.path = "strategy"
        mock_err.message = "required"
        mock_err.suggestion = "Add strategy field"
        mock_err.line = 1

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_err]

        with patch(
            "cli.commands.orchestration.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["orchestration", "validate", f.name])

        assert result.exit_code == 1

    def test_orchestration_validate_json(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []

        with patch(
            "cli.commands.orchestration.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "validate", f.name, "--json"],
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["valid"] is True

    def test_orchestration_list_connection_error(self) -> None:
        import httpx

        with patch("cli.commands.orchestration._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("refused")
            mock_get.return_value = mock_client

            result = runner.invoke(app, ["orchestration", "list"])

        assert result.exit_code == 1

    def test_orchestration_list_empty(self) -> None:
        with patch("cli.commands.orchestration._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": []}
            mock_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_get.return_value = mock_client

            result = runner.invoke(app, ["orchestration", "list"])

        assert result.exit_code == 0
        assert "No orchestration" in result.output or "no" in result.output.lower()

    def test_orchestration_status_not_found(self) -> None:
        with patch("cli.commands.orchestration._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": []}
            mock_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_get.return_value = mock_client

            result = runner.invoke(app, ["orchestration", "status", "nope"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_orchestration_deploy_validation_fail(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: bad\n")
        f.close()

        mock_err = MagicMock()
        mock_err.path = "strategy"
        mock_err.message = "required"
        mock_err.suggestion = None
        mock_err.line = None

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_err]

        with patch(
            "cli.commands.orchestration.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["orchestration", "deploy", f.name])

        assert result.exit_code == 1


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
        import pytest

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
        import pytest

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

    def test_eval_help(self) -> None:
        result = runner.invoke(app, ["eval", "--help"])
        assert result.exit_code == 0
        assert "eval" in result.output.lower()

    def test_eval_no_args(self) -> None:
        result = runner.invoke(app, ["eval"])
        assert result.exit_code in (0, 2)

    def test_eval_datasets_empty(self) -> None:
        mock_store = MagicMock()
        mock_store.list_datasets.return_value = []

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "datasets"])

        assert result.exit_code == 0
        assert "No datasets" in result.output or "no" in result.output.lower()

    def test_eval_datasets_json(self) -> None:
        mock_store = MagicMock()
        mock_store.list_datasets.return_value = [
            {
                "id": "ds-001",
                "name": "support-eval",
                "team": "eng",
                "row_count": 50,
                "version": "1.0",
                "tags": ["support"],
            }
        ]

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "datasets", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "support-eval"

    def test_eval_results_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = None

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "results", "nonexistent-id"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_eval_run_dataset_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.create_run.side_effect = ValueError("Dataset not found")

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                [
                    "eval",
                    "run",
                    "my-agent",
                    "--dataset",
                    "missing-ds",
                ],
            )

        assert result.exit_code == 1

    def test_eval_run_json_error(self) -> None:
        mock_store = MagicMock()
        mock_store.create_run.side_effect = ValueError("Dataset not found")

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                [
                    "eval",
                    "run",
                    "my-agent",
                    "--dataset",
                    "missing",
                    "--json",
                ],
            )

        assert result.exit_code == 1
        found = False
        for line in result.output.strip().splitlines():
            try:
                out = json.loads(line)
                assert "error" in out
                found = True
                break
            except json.JSONDecodeError:
                continue
        assert found

    def test_eval_gate_pass(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "agent_name": "my-agent",
            "status": "completed",
            "dataset_id": "ds-1",
            "summary": {
                "metrics": {
                    "correctness": {"mean": 0.85},
                    "relevance": {"mean": 0.90},
                },
            },
        }

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                [
                    "eval",
                    "gate",
                    "run-1",
                    "--threshold",
                    "0.7",
                ],
            )

        assert result.exit_code == 0

    def test_eval_gate_fail(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "agent_name": "my-agent",
            "status": "completed",
            "dataset_id": "ds-1",
            "summary": {
                "metrics": {
                    "correctness": {"mean": 0.50},
                    "relevance": {"mean": 0.90},
                },
            },
        }

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                [
                    "eval",
                    "gate",
                    "run-1",
                    "--threshold",
                    "0.7",
                ],
            )

        assert result.exit_code == 1

    def test_eval_gate_json(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "agent_name": "my-agent",
            "status": "completed",
            "dataset_id": "ds-1",
            "summary": {
                "metrics": {
                    "correctness": {"mean": 0.85},
                },
            },
        }

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                [
                    "eval",
                    "gate",
                    "run-1",
                    "--threshold",
                    "0.7",
                    "--metrics",
                    "correctness",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["passed"] is True

    def test_eval_gate_run_not_found(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = None

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "gate", "no-run"])

        assert result.exit_code == 1

    def test_eval_gate_not_completed(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "status": "running",
        }

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "gate", "run-1"])

        assert result.exit_code == 1

    def test_eval_compare_error(self) -> None:
        mock_store = MagicMock()
        mock_store.compare_runs.side_effect = ValueError("Run A not found")

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "compare", "a", "b"])

        assert result.exit_code == 1

    def test_eval_compare_json(self) -> None:
        mock_store = MagicMock()
        mock_store.compare_runs.return_value = {
            "run_a": {"agent_name": "agent-a"},
            "run_b": {"agent_name": "agent-b"},
            "comparison": {
                "correctness": {
                    "run_a_mean": 0.8,
                    "run_b_mean": 0.9,
                    "delta": 0.1,
                    "improved": True,
                }
            },
        }
        mock_store.detect_regression.return_value = {"regressions": [], "has_regression": False}

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "compare", "a", "b", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert "comparison" in output


# ── Publish Command ────────────────────────────────────────────────


class TestPublishCommand:
    """Tests for `agentbreeder publish`."""

    def test_publish_help(self) -> None:
        result = runner.invoke(app, ["publish", "--help"])
        assert result.exit_code == 0
        assert "resource" in result.output.lower()

    def test_publish_connection_error(self) -> None:
        import httpx

        with patch("cli.commands.publish._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("refused")
            mock_get.return_value = mock_client

            result = runner.invoke(app, ["publish", "agent", "my-agent"])

        assert result.exit_code == 1

    def test_publish_no_approved_pr(self) -> None:
        with patch("cli.commands.publish._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": {"prs": []}}
            mock_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_get.return_value = mock_client

            result = runner.invoke(app, ["publish", "agent", "my-agent"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "no approved" in result.output.lower()

    def test_publish_json_no_pr(self) -> None:
        with patch("cli.commands.publish._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": {"prs": []}}
            mock_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_get.return_value = mock_client

            result = runner.invoke(
                app,
                ["publish", "agent", "my-agent", "--json"],
            )

        assert result.exit_code == 1
        found = False
        for line in result.output.strip().splitlines():
            try:
                out = json.loads(line)
                assert "error" in out
                found = True
                break
            except json.JSONDecodeError:
                continue
        assert found


# ── Submit Command ─────────────────────────────────────────────────


class TestSubmitCommand:
    """Tests for `agentbreeder submit`."""

    def test_submit_help(self) -> None:
        result = runner.invoke(app, ["submit", "--help"])
        assert result.exit_code == 0
        assert "resource" in result.output.lower()

    def test_submit_connection_error(self) -> None:
        import httpx

        with patch("cli.commands.submit._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("refused")
            mock_get.return_value = mock_client

            result = runner.invoke(
                app,
                [
                    "submit",
                    "agent",
                    "my-agent",
                    "-m",
                    "Initial submit",
                ],
            )

        assert result.exit_code == 1

    def test_submit_success(self) -> None:
        with patch("cli.commands.submit._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "data": {
                    "id": "pr-123",
                    "status": "submitted",
                    "branch": "draft/user/agent/my-agent",
                    "submitter": "user",
                    "description": "Test",
                    "diff": None,
                }
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_get.return_value = mock_client

            result = runner.invoke(
                app,
                [
                    "submit",
                    "agent",
                    "my-agent",
                    "-m",
                    "Test submit",
                ],
            )

        assert result.exit_code == 0
        assert "pr-123" in result.output

    def test_submit_json_success(self) -> None:
        with patch("cli.commands.submit._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "data": {
                    "id": "pr-456",
                    "status": "submitted",
                    "branch": "draft/user/agent/x",
                    "submitter": "user",
                }
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_get.return_value = mock_client

            result = runner.invoke(
                app,
                [
                    "submit",
                    "agent",
                    "x",
                    "-m",
                    "msg",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["id"] == "pr-456"

    def test_submit_http_error(self) -> None:
        import httpx

        with patch("cli.commands.submit._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)

            mock_response = MagicMock()
            mock_response.status_code = 409
            mock_response.json.return_value = {"detail": "PR already exists"}

            mock_client.post.side_effect = httpx.HTTPStatusError(
                "409", request=MagicMock(), response=mock_response
            )
            mock_get.return_value = mock_client

            result = runner.invoke(
                app,
                ["submit", "agent", "my-agent", "-m", "dup"],
            )

        assert result.exit_code == 1
