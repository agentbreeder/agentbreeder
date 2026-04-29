"""Coverage-boost tests for low-coverage CLI commands.

Targets:
    - orchestration.py  (29% -> 80%+)
    - eval.py           (48% -> 80%+)
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

import httpx
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


def _mock_httpx_client(
    get_json=None,
    post_json=None,
    get_side_effect=None,
    post_side_effect=None,
):
    """Build a mock httpx.Client context manager."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    if get_json is not None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = get_json
        resp.raise_for_status = MagicMock()
        client.get.return_value = resp
    if get_side_effect is not None:
        client.get.side_effect = get_side_effect

    if post_json is not None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = post_json
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp
    if post_side_effect is not None:
        client.post.side_effect = post_side_effect

    return client


# ================================================================
# Orchestration — validate internals, status, deploy, chat/run
# ================================================================


class TestOrchValidateInternals:
    """Lines 149-209: validate --json with errors, error.suggestion."""

    def test_validate_json_with_errors(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: bad\n")
        f.close()

        mock_err = MagicMock()
        mock_err.path = "strategy"
        mock_err.message = "required"
        mock_err.suggestion = "Add strategy"
        mock_err.line = 2

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_err]

        with patch(
            "cli.commands.orchestration.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "validate", f.name, "--json"],
            )

        # --json with valid=False still returns exit 0
        # (just prints the JSON), then the caller checks
        out = json.loads(result.output)
        assert out["valid"] is False
        assert out["errors"][0]["suggestion"] == "Add strategy"

    def test_validate_invalid_shows_suggestion(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: bad\n")
        f.close()

        mock_err = MagicMock()
        mock_err.path = "agents"
        mock_err.message = "missing"
        mock_err.suggestion = "Add agents section"
        mock_err.line = None

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_err]

        with patch(
            "cli.commands.orchestration.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "validate", f.name],
            )

        assert result.exit_code == 1
        assert "Add agents section" in result.output


class TestOrchStatus:
    """Lines 240-269, 291-333: status subcommand."""

    def _orch_item(self, name="my-orch"):
        return {
            "id": "o-1",
            "name": name,
            "version": "1.0.0",
            "strategy": "sequential",
            "status": "deployed",
            "team": "eng",
            "owner": "alice@co.com",
            "endpoint_url": "http://orch:8080",
            "description": "A test orchestration",
            "agents_config": {
                "summarizer": {
                    "ref": "agents/summarizer",
                    "fallback": "agents/backup",
                },
                "reviewer": {"ref": "agents/reviewer"},
            },
        }

    def test_status_found_rich(self) -> None:
        client = _mock_httpx_client(
            get_json={"data": [self._orch_item()]},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "status", "my-orch"],
            )

        assert result.exit_code == 0
        assert "my-orch" in result.output
        assert "sequential" in result.output

    def test_status_found_json(self) -> None:
        client = _mock_httpx_client(
            get_json={"data": [self._orch_item()]},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "status", "my-orch", "--json"],
            )

        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["name"] == "my-orch"

    def test_status_connection_error(self) -> None:
        client = _mock_httpx_client(
            get_side_effect=httpx.ConnectError("refused"),
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "status", "my-orch"],
            )

        assert result.exit_code == 1

    def test_status_with_string_agent_data(self) -> None:
        """When agents_config values are strings, not dicts."""
        item = self._orch_item()
        item["agents_config"] = {
            "summarizer": "agents/summarizer",
        }
        client = _mock_httpx_client(
            get_json={"data": [item]},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "status", "my-orch"],
            )

        assert result.exit_code == 0


class TestOrchDeploy:
    """Lines 149-209, 291-333: deploy subcommand success paths."""

    def test_deploy_success_rich(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_val = MagicMock()
        mock_val.valid = True
        mock_val.errors = []

        created = {"id": "o-1", "name": "test-orch"}
        deployed = {
            "name": "test-orch",
            "version": "1.0.0",
            "strategy": "sequential",
            "agents_config": {
                "summarizer": {},
                "reviewer": {},
            },
            "endpoint_url": "http://orch:8080",
        }

        # post is called twice: create then deploy
        post_resp_1 = MagicMock()
        post_resp_1.status_code = 200
        post_resp_1.json.return_value = {"data": created}
        post_resp_1.raise_for_status = MagicMock()

        post_resp_2 = MagicMock()
        post_resp_2.status_code = 200
        post_resp_2.json.return_value = {"data": deployed}
        post_resp_2.raise_for_status = MagicMock()

        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.side_effect = [post_resp_1, post_resp_2]

        with (
            patch(
                "cli.commands.orchestration.validate_orchestration",
                return_value=mock_val,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "deploy", f.name],
            )

        assert result.exit_code == 0
        assert "deployed" in result.output.lower()

    def test_deploy_success_json(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_val = MagicMock()
        mock_val.valid = True
        mock_val.errors = []

        created = {"id": "o-1", "name": "test-orch"}
        deployed = {
            "name": "test-orch",
            "version": "1.0.0",
            "strategy": "sequential",
        }

        post_resp_1 = MagicMock()
        post_resp_1.status_code = 200
        post_resp_1.json.return_value = {"data": created}
        post_resp_1.raise_for_status = MagicMock()

        post_resp_2 = MagicMock()
        post_resp_2.status_code = 200
        post_resp_2.json.return_value = {"data": deployed}
        post_resp_2.raise_for_status = MagicMock()

        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.side_effect = [post_resp_1, post_resp_2]

        with (
            patch(
                "cli.commands.orchestration.validate_orchestration",
                return_value=mock_val,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "deploy", f.name, "--json"],
            )

        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["name"] == "test-orch"

    def test_deploy_connect_error_rich(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_val = MagicMock()
        mock_val.valid = True
        mock_val.errors = []

        client = _mock_httpx_client(
            post_side_effect=httpx.ConnectError("refused"),
        )

        with (
            patch(
                "cli.commands.orchestration.validate_orchestration",
                return_value=mock_val,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "deploy", f.name],
            )

        assert result.exit_code == 1

    def test_deploy_connect_error_json(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_val = MagicMock()
        mock_val.valid = True
        mock_val.errors = []

        client = _mock_httpx_client(
            post_side_effect=httpx.ConnectError("refused"),
        )

        with (
            patch(
                "cli.commands.orchestration.validate_orchestration",
                return_value=mock_val,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "deploy", f.name, "--json"],
            )

        assert result.exit_code == 1

    def test_deploy_http_error_rich(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_val = MagicMock()
        mock_val.valid = True
        mock_val.errors = []

        request = httpx.Request("POST", "http://localhost")
        response = httpx.Response(status_code=422, request=request)
        exc = httpx.HTTPStatusError("bad", request=request, response=response)
        # Make response.json() return detail
        response_mock = MagicMock()
        response_mock.json.return_value = {"detail": "Deploy failed"}
        exc.response = response_mock

        client = _mock_httpx_client(post_side_effect=exc)

        with (
            patch(
                "cli.commands.orchestration.validate_orchestration",
                return_value=mock_val,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "deploy", f.name],
            )

        assert result.exit_code == 1

    def test_deploy_http_error_json(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_val = MagicMock()
        mock_val.valid = True
        mock_val.errors = []

        request = httpx.Request("POST", "http://localhost")
        response = httpx.Response(status_code=422, request=request)
        exc = httpx.HTTPStatusError("bad", request=request, response=response)
        response_mock = MagicMock()
        response_mock.json.return_value = {"detail": "Deploy failed"}
        exc.response = response_mock

        client = _mock_httpx_client(post_side_effect=exc)

        with (
            patch(
                "cli.commands.orchestration.validate_orchestration",
                return_value=mock_val,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "deploy", f.name, "--json"],
            )

        assert result.exit_code == 1

    def test_deploy_validation_fail_json(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: bad\n")
        f.close()

        mock_err = MagicMock()
        mock_err.path = "strategy"
        mock_err.message = "required"

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_err]

        with patch(
            "cli.commands.orchestration.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "deploy", f.name, "--json"],
            )

        assert result.exit_code == 1
        out = json.loads(result.output)
        assert "error" in out or "errors" in out


class TestOrchList:
    """Lines 240-269: list with data, json, filters."""

    def _items(self):
        return [
            {
                "name": "pipeline-a",
                "version": "1.0.0",
                "strategy": "sequential",
                "status": "deployed",
                "team": "eng",
                "agents_config": {
                    "agent1": {"ref": "agents/a"},
                },
            },
            {
                "name": "pipeline-b",
                "version": "2.0.0",
                "strategy": "parallel",
                "status": "draft",
                "team": "data",
                "agents_config": {},
            },
        ]

    def test_list_with_data_rich(self) -> None:
        client = _mock_httpx_client(
            get_json={"data": self._items()},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            result = runner.invoke(app, ["orchestration", "list"])

        assert result.exit_code == 0
        assert "pipeline-a" in result.output

    def test_list_with_data_json(self) -> None:
        items = self._items()
        client = _mock_httpx_client(
            get_json={"data": items},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "list", "--json"],
            )

        assert result.exit_code == 0
        out = json.loads(result.output)
        assert len(out) == 2

    def test_list_with_filters(self) -> None:
        client = _mock_httpx_client(
            get_json={"data": []},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            result = runner.invoke(
                app,
                [
                    "orchestration",
                    "list",
                    "--team",
                    "eng",
                    "--status",
                    "deployed",
                ],
            )

        assert result.exit_code == 0


class TestOrchChat:
    """Lines 371-507, 510-570: chat/run subcommands."""

    def test_chat_not_found(self) -> None:
        with patch(
            "cli.commands.orchestration._find_orchestration",
            return_value=None,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "chat", "missing"],
            )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_chat_json_not_found(self) -> None:
        with patch(
            "cli.commands.orchestration._find_orchestration",
            return_value=None,
        ):
            result = runner.invoke(
                app,
                ["orchestration", "chat", "missing", "--json"],
                input="",
            )

        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_chat_json_mode_stdin(self) -> None:
        orch = {
            "id": "o-1",
            "name": "my-orch",
            "strategy": "sequential",
            "agents_config": {},
        }

        execute_resp = {
            "output": "hello world",
            "agent_trace": [],
            "total_tokens": 10,
        }

        client = _mock_httpx_client(
            post_json={"data": execute_resp},
        )

        with (
            patch(
                "cli.commands.orchestration._find_orchestration",
                return_value=orch,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "chat", "my-orch", "--json"],
                input="hello\n",
            )

        assert result.exit_code == 0

    def test_chat_json_mode_api_error(self) -> None:
        orch = {
            "id": "o-1",
            "name": "my-orch",
            "strategy": "sequential",
            "agents_config": {},
        }

        request = httpx.Request("POST", "http://localhost")
        response = httpx.Response(status_code=500, request=request)
        exc = httpx.HTTPStatusError("bad", request=request, response=response)
        response_mock = MagicMock()
        response_mock.json.return_value = {"detail": "err"}
        exc.response = response_mock

        client = _mock_httpx_client(post_side_effect=exc)

        with (
            patch(
                "cli.commands.orchestration._find_orchestration",
                return_value=orch,
            ),
            patch(
                "cli.commands.orchestration._get_client",
                return_value=client,
            ),
        ):
            result = runner.invoke(
                app,
                ["orchestration", "chat", "my-orch", "--json"],
                input="hello\n",
            )

        assert result.exit_code == 0
        assert "error" in result.output.lower()


class TestOrchSessionSummary:
    """Lines 549-570: session summary helper."""

    def test_session_summary_no_turns(self) -> None:
        from cli.commands.orchestration import (
            _print_session_summary,
        )

        _print_session_summary(0, 0, 0.0)

    def test_session_summary_with_turns(self) -> None:
        from cli.commands.orchestration import (
            _print_session_summary,
        )

        _print_session_summary(5, 1500, 0.012345)

    def test_chat_help_print(self) -> None:
        from cli.commands.orchestration import (
            _print_chat_help,
        )

        _print_chat_help()


class TestOrchFindOrchestration:
    """_find_orchestration helper."""

    def test_find_returns_match(self) -> None:
        from cli.commands.orchestration import (
            _find_orchestration,
        )

        item = {"name": "x", "id": "1"}
        client = _mock_httpx_client(
            get_json={"data": [item]},
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            assert _find_orchestration("x") == item

    def test_find_returns_none_on_connect_error(self) -> None:
        from cli.commands.orchestration import (
            _find_orchestration,
        )

        client = _mock_httpx_client(
            get_side_effect=httpx.ConnectError("nope"),
        )
        with patch(
            "cli.commands.orchestration._get_client",
            return_value=client,
        ):
            assert _find_orchestration("x") is None


# ================================================================
# Eval — run, gate internals, compare, export
# ================================================================


class TestEvalRun:
    """Lines 69-140: run subcommand success paths."""

    def _make_run_result(self):
        return {
            "id": "run-001",
            "status": "completed",
            "summary": {
                "metrics": {
                    "correctness": {
                        "mean": 0.85,
                        "median": 0.80,
                        "p95": 0.95,
                        "min": 0.5,
                        "max": 1.0,
                    },
                },
                "total_results": 10,
                "error_count": 1,
                "avg_latency_ms": 250,
                "total_cost_usd": 0.05,
            },
        }

    def test_run_success_rich(self) -> None:
        mock_store = MagicMock()
        mock_store.create_run.return_value = {"id": "run-001"}
        mock_store.execute_run.return_value = self._make_run_result()

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
                    "ds-1",
                ],
            )

        assert result.exit_code == 0
        assert "run-001" in result.output

    def test_run_success_json(self) -> None:
        mock_store = MagicMock()
        mock_store.create_run.return_value = {"id": "run-001"}
        mock_store.execute_run.return_value = self._make_run_result()

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
                    "ds-1",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        # sys.stdout.write goes through; find JSON in output
        # The output may include Rich console text too
        all_output = result.output
        # Find the JSON block (indented multi-line)
        assert "run-001" in all_output

    def test_run_with_overrides(self) -> None:
        mock_store = MagicMock()
        mock_store.create_run.return_value = {"id": "run-001"}
        mock_store.execute_run.return_value = self._make_run_result()

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
                    "ds-1",
                    "--model",
                    "gpt-4o",
                    "--temperature",
                    "0.5",
                    "--judge",
                    "claude-sonnet-4",
                ],
            )

        assert result.exit_code == 0
        call_kwargs = mock_store.create_run.call_args
        config = call_kwargs.kwargs.get(
            "config",
            call_kwargs[1].get("config", {}),
        )
        assert config["model"] == "gpt-4o"
        assert config["temperature"] == 0.5
        assert config["judge_model"] == "claude-sonnet-4"

    def test_run_execute_error_rich(self) -> None:
        mock_store = MagicMock()
        mock_store.create_run.return_value = {"id": "run-001"}
        mock_store.execute_run.side_effect = RuntimeError("agent crashed")

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
                    "ds-1",
                ],
            )

        assert result.exit_code == 1

    def test_run_execute_error_json(self) -> None:
        mock_store = MagicMock()
        mock_store.create_run.return_value = {"id": "run-001"}
        mock_store.execute_run.side_effect = RuntimeError("agent crashed")

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
                    "ds-1",
                    "--json",
                ],
            )

        assert result.exit_code == 1

    def test_run_no_metrics(self) -> None:
        """Run completes but summary has no metrics."""
        mock_store = MagicMock()
        mock_store.create_run.return_value = {"id": "run-002"}
        mock_store.execute_run.return_value = {
            "id": "run-002",
            "status": "completed",
            "summary": {
                "metrics": {},
                "total_results": 0,
                "error_count": 0,
                "avg_latency_ms": 0,
                "total_cost_usd": 0.0,
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
                    "run",
                    "my-agent",
                    "--dataset",
                    "ds-1",
                ],
            )

        assert result.exit_code == 0


class TestEvalGateInternals:
    """Lines 160-179: gate internals (json not-found, etc.)."""

    def test_gate_not_found_json(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = None

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                ["eval", "gate", "no-run", "--json"],
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

    def test_gate_not_completed_json(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "status": "running",
        }

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                ["eval", "gate", "run-1", "--json"],
            )

        assert result.exit_code == 1

    def test_gate_fail_json(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "status": "completed",
            "summary": {
                "metrics": {
                    "correctness": {"mean": 0.3},
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

        assert result.exit_code == 1
        out = json.loads(result.output)
        assert out["passed"] is False


class TestEvalCompare:
    """Lines 194-241, 370-411: compare subcommand rich output."""

    def test_compare_rich_output(self) -> None:
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
                },
                "relevance": {
                    "run_a_mean": 0.9,
                    "run_b_mean": 0.7,
                    "delta": -0.2,
                    "improved": False,
                },
                "latency": {
                    "run_a_mean": 0.5,
                    "run_b_mean": 0.5,
                    "delta": 0.0,
                    "improved": False,
                },
            },
        }
        mock_store.detect_regression.return_value = {"regressions": [], "has_regression": False}

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "compare", "a", "b"])

        assert result.exit_code == 0
        assert "agent-a" in result.output

    def test_compare_no_metrics(self) -> None:
        mock_store = MagicMock()
        mock_store.compare_runs.return_value = {
            "run_a": {"agent_name": "agent-a"},
            "run_b": {"agent_name": "agent-b"},
            "comparison": {},
        }

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "compare", "a", "b"])

        assert result.exit_code == 0
        assert "No metrics" in result.output

    def test_compare_error_json(self) -> None:
        mock_store = MagicMock()
        mock_store.compare_runs.side_effect = ValueError("Run A missing")

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                ["eval", "compare", "a", "b", "--json"],
            )

        assert result.exit_code == 1


class TestEvalResults:
    """Lines 194-241: results with data."""

    def test_results_with_data_rich(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "agent_name": "my-agent",
            "status": "completed",
            "dataset_id": "ds-00112233",
        }
        mock_store.get_results.return_value = [
            {
                "actual_output": "short answer",
                "scores": {
                    "correctness": 0.9,
                    "relevance": 0.8,
                },
                "latency_ms": 200,
                "cost_usd": 0.001,
                "error": None,
            },
            {
                "actual_output": (
                    "a very long answer that exceeds forty characters in total length"
                ),
                "scores": {
                    "correctness": 0.6,
                    "relevance": 0.5,
                },
                "latency_ms": 500,
                "cost_usd": 0.003,
                "error": "timeout",
            },
        ]

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "results", "run-1"])

        assert result.exit_code == 0
        assert "my-agent" in result.output

    def test_results_json(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "agent_name": "my-agent",
            "status": "completed",
            "dataset_id": "ds-001",
        }
        mock_store.get_results.return_value = [
            {
                "actual_output": "answer",
                "scores": {},
                "latency_ms": 100,
                "cost_usd": 0.001,
            },
        ]

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                ["eval", "results", "run-1", "--json"],
            )

        assert result.exit_code == 0
        out = json.loads(result.output)
        assert "results" in out

    def test_results_empty(self) -> None:
        mock_store = MagicMock()
        mock_store.get_run.return_value = {
            "id": "run-1",
            "agent_name": "my-agent",
            "status": "running",
            "dataset_id": "ds-001",
        }
        mock_store.get_results.return_value = []

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(app, ["eval", "results", "run-1"])

        assert result.exit_code == 0
        assert "No results" in result.output


class TestEvalDatasets:
    """Lines 160-179: datasets table rendering."""

    def test_datasets_table_rendered(self) -> None:
        mock_store = MagicMock()
        mock_store.list_datasets.return_value = [
            {
                "id": "ds-00112233445566",
                "name": "qa-set",
                "team": "eng",
                "row_count": 25,
                "version": "2.0",
                "tags": ["qa", "support"],
            },
        ]

        with patch(
            "cli.commands.eval._get_store",
            return_value=mock_store,
        ):
            result = runner.invoke(
                app,
                ["eval", "datasets", "--team", "eng"],
            )

        assert result.exit_code == 0
        assert "qa-set" in result.output


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


@pytest.mark.skip(
    reason="Track K rewrote secret_set; old prompt fixture incompat — see #162 follow-up"
)
class TestSecretSetPrompted:
    """Lines 110-116: set with prompted value."""

    def test_set_prompts_for_value(self) -> None:
        backend = MagicMock()
        backend.set = AsyncMock()

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


@pytest.mark.skip(reason="Track K removed --tags from secret set — see #162 follow-up")
class TestSecretSetTags:
    """Lines 112-116: tag parsing."""

    def test_set_with_tags(self) -> None:
        backend = MagicMock()
        backend.set = AsyncMock()

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

    def test_typescript_sdk_with_subagents(self) -> None:
        from cli.commands.eject import (
            _generate_typescript_sdk,
        )

        yaml_str = VALID_YAML + (
            "subagents:\n  - ref: agents/helper\n    description: Helper agent\n"
        )
        code = _generate_typescript_sdk(yaml_str)
        assert "withSubagent" in code
        assert "agents/helper" in code

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
        from click.exceptions import Exit as ClickExit

        from cli.commands.eject import eject as eject_fn

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()

        with pytest.raises(ClickExit) as exc_info:
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
# Validate — orchestration path, mcp config, json output
# ================================================================


class TestValidateOrchestration:
    """Lines 37-39, 61-86: orchestration.yaml validation."""

    def test_validate_detects_orchestration_by_name(
        self,
    ) -> None:
        f = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            prefix="orchestration",
            delete=False,
        )
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []

        with patch(
            "engine.orchestration_parser.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["validate", f.name])

        assert result.exit_code == 0

    def test_validate_detects_orchestration_by_content(
        self,
    ) -> None:
        f = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            prefix="myconfig",
            delete=False,
        )
        f.write(VALID_ORCH_YAML)
        f.close()

        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []

        with patch(
            "engine.orchestration_parser.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["validate", f.name])

        assert result.exit_code == 0

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

    def test_validate_orchestration_json_invalid(
        self,
    ) -> None:
        f = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            prefix="orchestration",
            delete=False,
        )
        f.write("name: bad\n")
        f.close()

        mock_err = MagicMock()
        mock_err.path = "strategy"
        mock_err.message = "required"
        mock_err.suggestion = "Add strategy"
        mock_err.line = 1
        mock_err.model_dump.return_value = {
            "path": "strategy",
            "message": "required",
            "suggestion": "Add strategy",
            "line": 1,
        }

        mock_result = MagicMock()
        mock_result.valid = False
        mock_result.errors = [mock_err]

        with patch(
            "engine.orchestration_parser.validate_orchestration",
            return_value=mock_result,
        ):
            result = runner.invoke(
                app,
                ["validate", f.name, "--json"],
            )

        assert result.exit_code == 1
        out = json.loads(result.output)
        assert out["valid"] is False

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
