"""Tests for the sandbox service — tool execution in isolated containers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from api.models.schemas import SandboxExecuteRequest, SandboxExecuteResponse
from api.services.sandbox_service import (
    DEFAULT_TIMEOUT_SECONDS,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    _build_wrapper_script,
    _extract_output,
    execute,
    execute_in_docker,
    execute_in_subprocess,
)

# ---------------------------------------------------------------------------
# Tests for _extract_output
# ---------------------------------------------------------------------------


class TestExtractOutput:
    """Tests for the _extract_output helper."""

    def test_extracts_tool_output_marker(self) -> None:
        stdout = 'some debug line\n__TOOL_OUTPUT__{"result": 42}\n'
        assert _extract_output(stdout) == '{"result": 42}'

    def test_returns_last_marked_line(self) -> None:
        stdout = '__TOOL_OUTPUT__{"a": 1}\n__TOOL_OUTPUT__{"b": 2}\n'
        assert _extract_output(stdout) == '{"b": 2}'

    def test_returns_full_stdout_when_no_marker(self) -> None:
        stdout = "hello world\nsome output"
        assert _extract_output(stdout) == "hello world\nsome output"

    def test_handles_empty_stdout(self) -> None:
        assert _extract_output("") == ""

    def test_handles_whitespace_only(self) -> None:
        assert _extract_output("   \n  \n") == ""


# ---------------------------------------------------------------------------
# Tests for _build_wrapper_script
# ---------------------------------------------------------------------------


class TestBuildWrapperScript:
    """Tests for the wrapper script builder."""

    def test_wrapper_contains_tool_input_parsing(self) -> None:
        script = _build_wrapper_script("result = tool_input")
        assert "TOOL_INPUT" in script
        assert "json.loads" in script

    def test_wrapper_contains_user_code(self) -> None:
        code = "result = tool_input.get('x', 0) + 1"
        script = _build_wrapper_script(code)
        assert code in script

    def test_wrapper_outputs_result_with_marker(self) -> None:
        script = _build_wrapper_script("result = 42")
        assert "__TOOL_OUTPUT__" in script

    def test_wrapper_handles_error_in_user_code(self) -> None:
        script = _build_wrapper_script("raise ValueError('boom')")
        assert "except Exception" in script
        assert "sys.exit(1)" in script


# ---------------------------------------------------------------------------
# Tests for SandboxExecutionRequest
# ---------------------------------------------------------------------------


class TestSandboxExecutionRequest:
    """Tests for the request dataclass."""

    def test_default_values(self) -> None:
        req = SandboxExecutionRequest(code="print('hi')")
        assert req.code == "print('hi')"
        assert req.input_json == {}
        assert req.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert req.network_enabled is False
        assert req.tool_id is None

    def test_custom_values(self) -> None:
        req = SandboxExecutionRequest(
            code="x = 1",
            input_json={"key": "val"},
            timeout_seconds=60,
            network_enabled=True,
            tool_id="tool-123",
        )
        assert req.input_json == {"key": "val"}
        assert req.timeout_seconds == 60
        assert req.network_enabled is True
        assert req.tool_id == "tool-123"


# ---------------------------------------------------------------------------
# Tests for SandboxExecutionResult
# ---------------------------------------------------------------------------


class TestSandboxExecutionResult:
    """Tests for the result dataclass."""

    def test_successful_result(self) -> None:
        result = SandboxExecutionResult(
            execution_id="abc",
            output='{"ok": true}',
            stdout="done\n",
            stderr="",
            exit_code=0,
            duration_ms=150,
        )
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.error is None

    def test_timed_out_result(self) -> None:
        result = SandboxExecutionResult(
            execution_id="def",
            output="",
            stdout="",
            stderr="Execution timed out",
            exit_code=124,
            duration_ms=30000,
            timed_out=True,
        )
        assert result.timed_out is True
        assert result.exit_code == 124

    def test_error_result(self) -> None:
        result = SandboxExecutionResult(
            execution_id="ghi",
            output="",
            stdout="",
            stderr="",
            exit_code=1,
            duration_ms=0,
            error="Docker not found",
        )
        assert result.error == "Docker not found"


# ---------------------------------------------------------------------------
# Tests for Pydantic schemas
# ---------------------------------------------------------------------------


class TestSandboxSchemas:
    """Tests for the Pydantic request/response schemas."""

    def test_request_defaults(self) -> None:
        req = SandboxExecuteRequest(code="print('hi')")
        assert req.timeout_seconds == 30
        assert req.network_enabled is False
        assert req.input_json == {}
        assert req.tool_id is None

    def test_request_validation_timeout_min(self) -> None:
        with pytest.raises(ValidationError):
            SandboxExecuteRequest(code="x", timeout_seconds=0)

    def test_request_validation_timeout_max(self) -> None:
        with pytest.raises(ValidationError):
            SandboxExecuteRequest(code="x", timeout_seconds=999)

    def test_response_model(self) -> None:
        resp = SandboxExecuteResponse(
            execution_id="abc",
            output="{}",
            stdout="ok\n",
            stderr="",
            exit_code=0,
            duration_ms=100,
        )
        assert resp.timed_out is False
        assert resp.error is None


# ---------------------------------------------------------------------------
# Tests for execute_in_subprocess
# ---------------------------------------------------------------------------


class TestExecuteInSubprocess:
    """Tests for the subprocess-based sandbox fallback."""

    @pytest.mark.asyncio
    async def test_simple_code_execution(self) -> None:
        req = SandboxExecutionRequest(
            code='result = {"sum": tool_input.get("a", 0) + tool_input.get("b", 0)}',
            input_json={"a": 3, "b": 4},
        )
        result = await execute_in_subprocess(req)
        assert result.exit_code == 0
        assert result.duration_ms > 0
        parsed = json.loads(result.output)
        assert parsed["sum"] == 7

    @pytest.mark.asyncio
    async def test_code_with_print_output(self) -> None:
        req = SandboxExecutionRequest(
            code='print("hello from sandbox")\nresult = {"status": "ok"}',
        )
        result = await execute_in_subprocess(req)
        assert result.exit_code == 0
        assert "hello from sandbox" in result.stdout

    @pytest.mark.asyncio
    async def test_code_with_error(self) -> None:
        req = SandboxExecutionRequest(
            code='raise ValueError("intentional error")',
        )
        result = await execute_in_subprocess(req)
        assert result.exit_code == 1
        assert "ValueError" in result.stderr

    @pytest.mark.asyncio
    async def test_code_with_syntax_error(self) -> None:
        req = SandboxExecutionRequest(
            code="def broken(:\n  pass",
        )
        result = await execute_in_subprocess(req)
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self) -> None:
        req = SandboxExecutionRequest(
            code="import time; time.sleep(10)",
            timeout_seconds=1,
        )
        result = await execute_in_subprocess(req)
        assert result.timed_out is True
        assert result.exit_code == 124

    @pytest.mark.asyncio
    async def test_empty_code(self) -> None:
        req = SandboxExecutionRequest(code="pass")
        result = await execute_in_subprocess(req)
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_no_result_returns_stdout(self) -> None:
        req = SandboxExecutionRequest(
            code='print("just printing")',
        )
        result = await execute_in_subprocess(req)
        assert result.exit_code == 0
        assert "just printing" in result.output or "just printing" in result.stdout

    @pytest.mark.asyncio
    async def test_complex_input_json(self) -> None:
        req = SandboxExecutionRequest(
            code='result = {"keys": list(tool_input.keys()), "count": len(tool_input)}',
            input_json={"name": "test", "values": [1, 2, 3], "nested": {"a": True}},
        )
        result = await execute_in_subprocess(req)
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["count"] == 3
        assert "name" in parsed["keys"]


# ---------------------------------------------------------------------------
# Tests for execute_in_docker (mocked)
# ---------------------------------------------------------------------------


class TestExecuteInDocker:
    """Tests for the Docker-based sandbox (with mocked Docker subprocess)."""

    @pytest.mark.asyncio
    async def test_docker_command_structure(self) -> None:
        """Verify the Docker command includes security flags."""
        captured_cmd = []

        async def mock_create_subprocess(*args, **kwargs):
            captured_cmd.extend(args)
            mock_proc = MagicMock()
            mock_proc.returncode = 0

            async def mock_communicate():
                return b'__TOOL_OUTPUT__{"ok": true}\n', b""

            mock_proc.communicate = mock_communicate
            mock_proc.kill = MagicMock()
            return mock_proc

        with patch(
            "api.services.sandbox_service.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            req = SandboxExecutionRequest(
                code="result = True",
                network_enabled=False,
            )
            result = await execute_in_docker(req)

        # The command should contain security flags
        cmd_str = " ".join(str(c) for c in captured_cmd)
        assert "--network=none" in cmd_str
        assert "--memory=256m" in cmd_str
        assert "--read-only" in cmd_str
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_docker_network_enabled(self) -> None:
        """Verify network=bridge when network_enabled is True."""
        captured_cmd = []

        async def mock_create_subprocess(*args, **kwargs):
            captured_cmd.extend(args)
            mock_proc = MagicMock()
            mock_proc.returncode = 0

            async def mock_communicate():
                return b"ok\n", b""

            mock_proc.communicate = mock_communicate
            return mock_proc

        with patch(
            "api.services.sandbox_service.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            req = SandboxExecutionRequest(
                code="result = True",
                network_enabled=True,
            )
            await execute_in_docker(req)

        cmd_str = " ".join(str(c) for c in captured_cmd)
        assert "--network=bridge" in cmd_str

    @pytest.mark.asyncio
    async def test_docker_exception_handling(self) -> None:
        """Verify graceful handling when Docker command fails."""
        with patch(
            "api.services.sandbox_service.asyncio.create_subprocess_exec",
            side_effect=OSError("Docker socket not found"),
        ):
            req = SandboxExecutionRequest(code="result = True")
            result = await execute_in_docker(req)

        assert result.exit_code == 1
        assert result.error is not None
        assert "Docker socket not found" in result.error


# ---------------------------------------------------------------------------
# Tests for execute (auto-selection)
# ---------------------------------------------------------------------------


class TestExecute:
    """Tests for the execute function that auto-selects Docker vs subprocess."""

    @pytest.mark.asyncio
    async def test_uses_docker_when_available(self) -> None:
        with (
            patch(
                "api.services.sandbox_service._check_docker_available",
                return_value=True,
            ),
            patch(
                "api.services.sandbox_service.execute_in_docker",
                return_value=SandboxExecutionResult(
                    execution_id="d1",
                    output="docker",
                    stdout="docker\n",
                    stderr="",
                    exit_code=0,
                    duration_ms=100,
                ),
            ) as mock_docker,
        ):
            req = SandboxExecutionRequest(code="pass")
            result = await execute(req)
            assert result.output == "docker"
            mock_docker.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_subprocess(self) -> None:
        with (
            patch(
                "api.services.sandbox_service._check_docker_available",
                return_value=False,
            ),
            patch(
                "api.services.sandbox_service.execute_in_subprocess",
                return_value=SandboxExecutionResult(
                    execution_id="s1",
                    output="subprocess",
                    stdout="subprocess\n",
                    stderr="",
                    exit_code=0,
                    duration_ms=50,
                ),
            ) as mock_sub,
        ):
            req = SandboxExecutionRequest(code="pass")
            result = await execute(req)
            assert result.output == "subprocess"
            mock_sub.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for API endpoint via TestClient
# ---------------------------------------------------------------------------


class TestSandboxEndpoint:
    """Tests for the POST /api/v1/tools/sandbox/execute endpoint."""

    def test_endpoint_exists(self) -> None:
        from fastapi.testclient import TestClient

        from api.main import app

        client = TestClient(app)

        # Patch execute to avoid actual subprocess/Docker execution
        with patch(
            "api.routes.sandbox.execute",
            return_value=SandboxExecutionResult(
                execution_id="test-id",
                output='{"result": true}',
                stdout="ok\n",
                stderr="",
                exit_code=0,
                duration_ms=42,
            ),
        ):
            resp = client.post(
                "/api/v1/tools/sandbox/execute",
                json={
                    "code": "result = True",
                    "input_json": {},
                    "timeout_seconds": 10,
                    "network_enabled": False,
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["execution_id"] == "test-id"
        assert data["exit_code"] == 0
        assert data["duration_ms"] == 42
        assert data["output"] == '{"result": true}'

    def test_endpoint_with_error_result(self) -> None:
        from fastapi.testclient import TestClient

        from api.main import app

        client = TestClient(app)

        with patch(
            "api.routes.sandbox.execute",
            return_value=SandboxExecutionResult(
                execution_id="err-id",
                output="",
                stdout="",
                stderr="NameError: name 'x' is not defined",
                exit_code=1,
                duration_ms=15,
            ),
        ):
            resp = client.post(
                "/api/v1/tools/sandbox/execute",
                json={"code": "print(x)"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["exit_code"] == 1
        assert "NameError" in data["stderr"]

    def test_endpoint_timeout_result(self) -> None:
        from fastapi.testclient import TestClient

        from api.main import app

        client = TestClient(app)

        with patch(
            "api.routes.sandbox.execute",
            return_value=SandboxExecutionResult(
                execution_id="timeout-id",
                output="",
                stdout="",
                stderr="Execution timed out",
                exit_code=124,
                duration_ms=30000,
                timed_out=True,
            ),
        ):
            resp = client.post(
                "/api/v1/tools/sandbox/execute",
                json={
                    "code": "import time; time.sleep(999)",
                    "timeout_seconds": 30,
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["timed_out"] is True
        assert data["exit_code"] == 124
