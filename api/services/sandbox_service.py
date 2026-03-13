"""Sandbox service — execute tool code in isolated Docker containers.

Provides ephemeral, isolated execution of Python tool code with:
- Docker container isolation (no network by default)
- Configurable timeout (default 30s)
- stdout/stderr capture
- Subprocess fallback when Docker is unavailable
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
DOCKER_IMAGE = "python:3.11-slim"


@dataclass
class SandboxExecutionResult:
    """Result of a sandbox tool execution."""

    execution_id: str
    output: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool = False
    error: str | None = None


@dataclass
class SandboxExecutionRequest:
    """Request to execute tool code in the sandbox."""

    code: str
    input_json: dict = field(default_factory=dict)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    network_enabled: bool = False
    tool_id: str | None = None


async def _check_docker_available() -> bool:
    """Check if Docker daemon is available."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except (FileNotFoundError, OSError):
        return False


async def execute_in_docker(
    request: SandboxExecutionRequest,
) -> SandboxExecutionResult:
    """Execute tool code inside an ephemeral Docker container.

    The code is mounted as a volume. Input JSON is passed via
    the TOOL_INPUT environment variable. Network access is denied
    by default (--network=none).
    """
    execution_id = str(uuid.uuid4())
    tmpdir = tempfile.mkdtemp(prefix="garden-sandbox-")

    try:
        # Write the wrapper script that imports and runs the tool code
        wrapper_code = _build_wrapper_script(request.code)
        script_path = Path(tmpdir) / "run_tool.py"
        script_path.write_text(wrapper_code, encoding="utf-8")

        # Write input JSON
        input_path = Path(tmpdir) / "input.json"
        input_path.write_text(json.dumps(request.input_json), encoding="utf-8")

        # Build docker command
        timeout = min(request.timeout_seconds, MAX_TIMEOUT_SECONDS)
        network = "bridge" if request.network_enabled else "none"

        cmd = [
            "docker",
            "run",
            "--rm",
            f"--network={network}",
            "--memory=256m",
            "--cpus=0.5",
            "--pids-limit=64",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "-v",
            f"{tmpdir}:/workspace:ro",
            "-e",
            f"TOOL_INPUT={json.dumps(request.input_json)}",
            "-w",
            "/workspace",
            DOCKER_IMAGE,
            "python",
            "/workspace/run_tool.py",
        ]

        start_time = time.monotonic()
        timed_out = False

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            exit_code = proc.returncode or 0
        except TimeoutError:
            timed_out = True
            proc.kill()
            stdout_bytes, stderr_bytes = b"", b"Execution timed out"
            exit_code = 124

        duration_ms = int((time.monotonic() - start_time) * 1000)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Try to extract structured output (last line of stdout if it's JSON)
        output = _extract_output(stdout)

        return SandboxExecutionResult(
            execution_id=execution_id,
            output=output,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )

    except Exception as e:
        logger.error("Docker sandbox execution failed: %s", e)
        return SandboxExecutionResult(
            execution_id=execution_id,
            output="",
            stdout="",
            stderr="",
            exit_code=1,
            duration_ms=0,
            error=str(e),
        )
    finally:
        # Clean up temp directory
        shutil.rmtree(tmpdir, ignore_errors=True)


async def execute_in_subprocess(
    request: SandboxExecutionRequest,
) -> SandboxExecutionResult:
    """Execute tool code in a local subprocess (fallback when Docker is unavailable).

    WARNING: Less isolated than Docker. Use only for development/testing.
    """
    execution_id = str(uuid.uuid4())
    tmpdir = tempfile.mkdtemp(prefix="garden-sandbox-")

    try:
        # Write the wrapper script
        wrapper_code = _build_wrapper_script(request.code)
        script_path = Path(tmpdir) / "run_tool.py"
        script_path.write_text(wrapper_code, encoding="utf-8")

        # Write input JSON
        input_path = Path(tmpdir) / "input.json"
        input_path.write_text(json.dumps(request.input_json), encoding="utf-8")

        timeout = min(request.timeout_seconds, MAX_TIMEOUT_SECONDS)

        env = {
            "TOOL_INPUT": json.dumps(request.input_json),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }

        start_time = time.monotonic()
        timed_out = False

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3",
                str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=tmpdir,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            exit_code = proc.returncode or 0
        except TimeoutError:
            timed_out = True
            proc.kill()
            stdout_bytes, stderr_bytes = b"", b"Execution timed out"
            exit_code = 124

        duration_ms = int((time.monotonic() - start_time) * 1000)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        output = _extract_output(stdout)

        return SandboxExecutionResult(
            execution_id=execution_id,
            output=output,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )

    except Exception as e:
        logger.error("Subprocess sandbox execution failed: %s", e)
        return SandboxExecutionResult(
            execution_id=execution_id,
            output="",
            stdout="",
            stderr="",
            exit_code=1,
            duration_ms=0,
            error=str(e),
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def execute(request: SandboxExecutionRequest) -> SandboxExecutionResult:
    """Execute tool code in the best available sandbox.

    Tries Docker first; falls back to subprocess if Docker is unavailable.
    """
    if await _check_docker_available():
        logger.info("Executing tool in Docker sandbox")
        return await execute_in_docker(request)
    else:
        logger.warning("Docker not available — falling back to subprocess sandbox")
        return await execute_in_subprocess(request)


def _build_wrapper_script(user_code: str) -> str:
    """Build a wrapper Python script that executes user tool code.

    The wrapper:
    1. Reads TOOL_INPUT from environment variable
    2. Executes the user code with `tool_input` available as a dict
    3. Prints the result as JSON on the last line (prefixed with __TOOL_OUTPUT__)
    """
    return f"""\
import json
import os
import sys

def main():
    # Parse input
    raw_input = os.environ.get("TOOL_INPUT", "{{}}")
    try:
        tool_input = json.loads(raw_input)
    except json.JSONDecodeError:
        tool_input = {{}}

    # Execute user code
    namespace = {{"tool_input": tool_input, "json": json}}
    try:
        exec(\'\'\'
{user_code}
\'\'\', namespace)
    except Exception as e:
        print(f"Error: {{type(e).__name__}}: {{e}}", file=sys.stderr)
        sys.exit(1)

    # Extract result if set
    result = namespace.get("result", namespace.get("output", None))
    if result is not None:
        print(f"__TOOL_OUTPUT__{{json.dumps(result)}}")

if __name__ == "__main__":
    main()
"""


def _extract_output(stdout: str) -> str:
    """Extract the structured output from stdout.

    Looks for lines prefixed with __TOOL_OUTPUT__ and returns the JSON after it.
    If not found, returns the full stdout as the output.
    """
    for line in reversed(stdout.strip().splitlines()):
        if line.startswith("__TOOL_OUTPUT__"):
            return line[len("__TOOL_OUTPUT__") :]
    return stdout.strip()
