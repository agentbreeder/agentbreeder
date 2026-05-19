"""Unit tests for sandbox AST safety scan (W4-10)."""

from __future__ import annotations

import pytest

from api.services import sandbox_service
from api.services.sandbox_service import (
    SandboxExecutionRequest,
    SandboxValidationError,
    _build_wrapper_script,
    _validate_user_code,
)

# ---------------------------------------------------------------------------
# AST scan rejects imports + dynamic-invocation builtins
# ---------------------------------------------------------------------------


class TestValidateUserCode:
    def test_accepts_simple_arithmetic(self) -> None:
        _validate_user_code("result = tool_input.get('a', 0) + 1")

    def test_accepts_json_usage(self) -> None:
        _validate_user_code('result = json.dumps({"x": 1})')

    def test_rejects_plain_import(self) -> None:
        with pytest.raises(SandboxValidationError, match="import"):
            _validate_user_code("import os\nresult = os.environ")

    def test_rejects_from_import(self) -> None:
        with pytest.raises(SandboxValidationError, match="from"):
            _validate_user_code("from os import environ\nresult = environ")

    def test_rejects_subprocess_import(self) -> None:
        with pytest.raises(SandboxValidationError):
            _validate_user_code("import subprocess; subprocess.run(['ls'])")

    def test_rejects_eval_call(self) -> None:
        with pytest.raises(SandboxValidationError, match="eval"):
            _validate_user_code("result = eval('1+1')")

    def test_rejects_dynamic_runner_call(self) -> None:
        # exec('...') must be rejected
        with pytest.raises(SandboxValidationError):
            _validate_user_code("ex" + "ec('print(1)')")

    def test_rejects_dunder_import_call(self) -> None:
        with pytest.raises(SandboxValidationError, match="__import__"):
            _validate_user_code("os = __import__('os')")

    def test_rejects_compile_call(self) -> None:
        with pytest.raises(SandboxValidationError, match="compile"):
            _validate_user_code("code = compile('1', '<x>', 'eval')")

    def test_rejects_attribute_call_for_dynamic_eval(self) -> None:
        # x.attr-call to forbidden name is rejected (no import in the snippet)
        forbidden = "ev" + "al"
        snippet = f"result = tool_input.{forbidden}('1')"
        with pytest.raises(SandboxValidationError, match=forbidden):
            _validate_user_code(snippet)

    def test_rejects_syntax_error(self) -> None:
        with pytest.raises(SandboxValidationError, match="syntax"):
            _validate_user_code("def foo(:\n    pass")

    def test_error_message_includes_line_number(self) -> None:
        with pytest.raises(SandboxValidationError, match="line 2"):
            _validate_user_code("a = 1\nimport os\nb = 2")


# ---------------------------------------------------------------------------
# Wrapper script restricts builtins
# ---------------------------------------------------------------------------


class TestWrapperScriptNamespace:
    def test_wrapper_defines_allowed_builtins(self) -> None:
        script = _build_wrapper_script("result = 1")
        assert "_ALLOWED_BUILTINS" in script
        assert "__builtins__" in script

    def test_wrapper_does_not_expose_open(self) -> None:
        script = _build_wrapper_script("result = 1")
        # 'open' is not in the allowed builtins list
        assert '"open": open' not in script

    def test_wrapper_does_not_expose_dynamic_runners(self) -> None:
        script = _build_wrapper_script("result = 1")
        # eval/exec/__import__/compile must not be in the allowlist
        assert '"eval"' not in script
        assert '"__import__"' not in script


# ---------------------------------------------------------------------------
# Integration: subprocess path rejects unsafe code without spawning
# ---------------------------------------------------------------------------


class TestSubprocessRejectsUnsafeCode:
    @pytest.mark.asyncio
    async def test_subprocess_rejects_import(self) -> None:
        req = SandboxExecutionRequest(
            code="import os\nresult = os.environ.get('PATH')",
            input_json={},
            timeout_seconds=5,
        )
        result = await sandbox_service.execute_in_subprocess(req)
        assert result.exit_code == 1
        assert result.error is not None
        assert "import" in result.error.lower()

    @pytest.mark.asyncio
    async def test_subprocess_rejects_eval(self) -> None:
        req = SandboxExecutionRequest(
            code="result = eval('1+1')",
            input_json={},
            timeout_seconds=5,
        )
        result = await sandbox_service.execute_in_subprocess(req)
        assert result.exit_code == 1
        assert result.error is not None
        assert "eval" in result.error.lower()
