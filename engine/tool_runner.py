"""Run a registered tool by ID with structured input.

Dispatches by the ``endpoint`` field stored on the tool record:

  * ``engine.tools.standard.<name>``  – in-process Python import
  * ``python:<path/to/file.py>``       – subprocess (Python file outside repo)
  * ``node:<path/to/file.ts>``         – subprocess via ``npx tsx``
  * ``http(s)://...``                  – HTTP POST with JSON body
  * (other)                            – raise ``UnsupportedToolEndpointError``

For Node, the file's exported function name is derived from the filename
(snake_case).

Returns a ``ToolExecutionResult`` capturing stdout/stderr/exit_code/output/
duration so the caller can surface failures (the registry UI shows them as a
red panel).

All subprocess invocations use ``asyncio.create_subprocess_exec`` (the
shell-free, argv-list variant) — never shell-string interpolation.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class UnsupportedToolEndpointError(RuntimeError):
    """Raised when a tool's endpoint cannot be dispatched by tool_runner."""


@dataclass
class ToolExecutionResult:
    output: Any  # parsed JSON result (or stringified non-JSON)
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    error: str | None = None


_DEFAULT_TIMEOUT_S = 30.0


async def execute_tool(endpoint: str, name: str, args: dict[str, Any]) -> ToolExecutionResult:
    """Run a registered tool and return its structured result.

    Args:
        endpoint: The endpoint string from the tool registry record.
        name: The kebab-case tool name (used to derive the function name).
        args: JSON-serialisable input dict.
    """
    started = time.perf_counter()

    if endpoint.startswith("engine.tools.standard."):
        result = await _run_in_process(endpoint, args)
    elif endpoint.startswith("python:"):
        result = await _run_python_subprocess(endpoint[len("python:"):], name, args)
    elif endpoint.startswith("node:"):
        result = await _run_node_subprocess(endpoint[len("node:"):], name, args)
    elif endpoint.startswith(("http://", "https://")):
        result = await _run_http(endpoint, args)
    else:
        raise UnsupportedToolEndpointError(
            f"Cannot dispatch tool '{name}' with endpoint '{endpoint}'. "
            f"Supported prefixes: 'engine.tools.standard.<name>', 'python:<path>', "
            f"'node:<path>', 'http(s)://<url>'."
        )

    result.duration_ms = int((time.perf_counter() - started) * 1000)
    return result


# --- dispatchers ----------------------------------------------------------


async def _run_in_process(import_path: str, args: dict[str, Any]) -> ToolExecutionResult:
    """Import and call a Python function in this process."""
    try:
        module = importlib.import_module(import_path)
        func_name = import_path.rsplit(".", 1)[-1]
        fn = getattr(module, func_name, None)
        if not callable(fn):
            return ToolExecutionResult(
                output=None, stdout="", stderr="",
                exit_code=2, duration_ms=0,
                error=f"{import_path} does not export callable '{func_name}'",
            )
        if inspect.iscoroutinefunction(fn):
            output = await fn(**args)
        else:
            loop = asyncio.get_running_loop()
            output = await loop.run_in_executor(None, lambda: fn(**args))
        return ToolExecutionResult(output=output, stdout="", stderr="", exit_code=0, duration_ms=0)
    except Exception as exc:  # noqa: BLE001 — surface the exact error to the UI
        return ToolExecutionResult(
            output=None, stdout="", stderr="",
            exit_code=1, duration_ms=0,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _run_python_subprocess(file_path: str, name: str, args: dict[str, Any]) -> ToolExecutionResult:
    """Spawn a Python child process to load the file and call its function."""
    abs_path = Path(file_path).resolve()
    if not abs_path.is_file():
        return ToolExecutionResult(
            output=None, stdout="", stderr="",
            exit_code=2, duration_ms=0,
            error=f"Python file not found: {abs_path}",
        )
    snake = name.replace("-", "_")
    code = (
        "import importlib.util, json, sys; "
        f"spec = importlib.util.spec_from_file_location('tool', {str(abs_path)!r}); "
        "mod = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(mod); "
        f"fn = getattr(mod, {snake!r}); "
        f"args = json.loads(sys.stdin.read() or '{{}}'); "
        "print(json.dumps(fn(**args)))"
    )
    return await _spawn(["python3", "-c", code], json.dumps(args))


async def _run_node_subprocess(file_path: str, name: str, args: dict[str, Any]) -> ToolExecutionResult:
    """Spawn a Node child process to load a TS/JS tool and call it."""
    abs_path = Path(file_path).resolve()
    if not abs_path.is_file():
        return ToolExecutionResult(
            output=None, stdout="", stderr="",
            exit_code=2, duration_ms=0,
            error=f"Node tool file not found: {abs_path}",
        )
    snake = name.replace("-", "_")
    snake_json = json.dumps(snake)
    file_url = json.dumps(str(abs_path))
    runner_js = (
        "const { pathToFileURL } = require('node:url');"
        "(async () => {"
        f"  const mod = await import(pathToFileURL({file_url}).href);"
        f"  const fn = mod[{snake_json}] ?? mod.default;"
        "  if (typeof fn !== 'function') {"
        f"    console.error('No exported function named ' + {snake_json} + ' or default');"
        "    process.exit(2);"
        "  }"
        "  let raw = '';"
        "  process.stdin.on('data', (c) => (raw += c));"
        "  process.stdin.on('end', async () => {"
        "    const args = raw ? JSON.parse(raw) : {};"
        "    const out = await fn(args);"
        "    process.stdout.write(JSON.stringify(out));"
        "  });"
        "})().catch((e) => { console.error(e); process.exit(1); });"
    )
    return await _spawn(["npx", "--yes", "tsx", "--eval", runner_js], json.dumps(args))


async def _run_http(url: str, args: dict[str, Any]) -> ToolExecutionResult:
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await client.post(url, json=args)
            text = resp.text
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                payload = text
        return ToolExecutionResult(
            output=payload, stdout=text[:2000], stderr="",
            exit_code=0 if resp.is_success else 1, duration_ms=0,
            error=None if resp.is_success else f"HTTP {resp.status_code}",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolExecutionResult(
            output=None, stdout="", stderr="",
            exit_code=1, duration_ms=0,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _spawn(cmd: list[str], stdin_payload: str) -> ToolExecutionResult:
    """Run a subprocess via the safe argv-list path; pipe stdin in, capture all I/O."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ},
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(stdin_payload.encode()), timeout=_DEFAULT_TIMEOUT_S,
        )
    except TimeoutError:
        proc.kill()
        return ToolExecutionResult(
            output=None, stdout="", stderr="",
            exit_code=124, duration_ms=0,
            error=f"Timeout after {_DEFAULT_TIMEOUT_S}s running: {' '.join(shlex.quote(c) for c in cmd)}",
        )

    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    output: Any = None
    err: str | None = None
    if proc.returncode == 0:
        try:
            output = json.loads(stdout.strip())
        except json.JSONDecodeError:
            output = stdout.strip()
    else:
        err = (stderr or stdout)[-2000:]

    return ToolExecutionResult(
        output=output, stdout=stdout, stderr=stderr,
        exit_code=proc.returncode or 0, duration_ms=0, error=err,
    )
