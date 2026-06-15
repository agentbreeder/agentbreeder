# Wave 3 — Eject-to-Code Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-side, resumable `BuilderSession` resource and an "eject to code" capability that lets the selected coding agent (Claude or Codex) write `agent.py` / `tools/` / tests into a sandbox, stream file diffs into the chat, and deploy from the same thread.

**Architecture:** A pluggable `Sandbox` interface (`LocalSandbox` = temp dir + subprocess) holds the scaffold workspace. A single provider-loop `CodingAgentEngine` drives codegen over the existing `engine/providers/*.generate_stream()` abstraction with a `{write,read,list,exec}` tool surface bound to the sandbox — Claude vs Codex is a strategy choice of which provider+model the same loop uses (no vendor SDK deps, the security boundary stays in our code). A persisted `BuilderSession` (Postgres + in-process SSE event bus, mirroring the existing `deploy_event_bus`) ties together conversation history, the evolving `agent.yaml`, the sandbox workspace, the selected engine, and the deploy job. The frontend grows a two-pane build console with a **Code** artifact tab.

**Tech Stack:** Python 3.11 / FastAPI / async SQLAlchemy / Alembic / sse-starlette · React 18 / TypeScript / Tailwind / Vitest · pytest

**Decision (locked this session):** Coding-agent engine = **provider-loop over `generate_stream()`** (Option 1), not vendor SDKs and not CLI subprocess. Rationale: keeps the code-execution security surface in code we own + test, honors the provider abstraction and anti-lock-in rules, makes Claude↔Codex a one-line strategy swap.

**Security rail (load-bearing):** `LocalSandbox` executes code on the host running Studio. That is acceptable **only** in local single-user mode. A new env gate `AGENTBREEDER_SANDBOX` (`local` default | `cloud` | `disabled`) controls which sandbox the server may construct. In cloud mode, `LocalSandbox` construction raises — only `CloudSandbox` (Wave 4) is allowed. This prevents the multi-tenant cloud from ever running user code in-process.

---

## Part overview

| Part | Scope | Independently testable milestone |
|---|---|---|
| **A** | `Sandbox` interface + `LocalSandbox` + `FakeSandbox` test double | Sandbox unit tests green; path-containment + timeout + snapshot proven |
| **B** | `CodingAgentEngine` protocol + provider-loop + Claude/Codex strategies | Coding loop unit tests green against a fake provider + `FakeSandbox` |
| **C** | `BuilderSession` model + migration + service + §6 API routes + SSE | Integration tests for create/get/messages/eject/deploy green |
| **D** | Frontend two-pane build console + Code artifact tab + eject UI + client | Vitest green; `tsc` clean |
| **E** | Cross-repo (cloud proxy) + docs sync + model-e2e gated test + analytics events | Cloud passthrough test green; docs updated in same commit |

Parts A→C are sequential (C depends on A+B). D depends on C's API contract. E closes out cross-repo + docs. Commit after every task.

---

## PART A — Sandbox interface + LocalSandbox

**File structure:**
- Create: `engine/sandbox/__init__.py`
- Create: `engine/sandbox/base.py` — `ExecResult`, `Sandbox` protocol, `SandboxDisabledError`, `select_sandbox_mode()`
- Create: `engine/sandbox/local.py` — `LocalSandbox`
- Create: `tests/unit/test_sandbox_local.py`
- Create: `tests/unit/fakes/fake_sandbox.py` — `FakeSandbox` reused by Part B/C tests

### Task A1: Sandbox interface + ExecResult + mode gate

**Files:**
- Create: `engine/sandbox/__init__.py`
- Create: `engine/sandbox/base.py`
- Test: `tests/unit/test_sandbox_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sandbox_base.py
import os
import pytest
from engine.sandbox.base import (
    ExecResult,
    SandboxDisabledError,
    select_sandbox_mode,
)


def test_exec_result_defaults():
    r = ExecResult(stdout="hi", stderr="", exit_code=0)
    assert r.timed_out is False
    assert r.ok is True


def test_exec_result_nonzero_not_ok():
    assert ExecResult(stdout="", stderr="boom", exit_code=1).ok is False


def test_select_sandbox_mode_defaults_local(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_SANDBOX", raising=False)
    assert select_sandbox_mode() == "local"


def test_select_sandbox_mode_reads_env(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "cloud")
    assert select_sandbox_mode() == "cloud"


def test_select_sandbox_mode_rejects_unknown(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "bogus")
    with pytest.raises(SandboxDisabledError):
        select_sandbox_mode()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sandbox_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.sandbox'`

- [ ] **Step 3: Write minimal implementation**

```python
# engine/sandbox/__init__.py
"""Pluggable sandbox interface for the conversational builder's eject-to-code flow."""
```

```python
# engine/sandbox/base.py
"""Sandbox interface — a pluggable scaffold workspace for the coding agent.

LocalSandbox runs on the host (local single-user Studio only). CloudSandbox
(Wave 4) runs in a managed microVM. The AGENTBREEDER_SANDBOX env gate decides
which the server is permitted to construct so the multi-tenant cloud can never
run user code in-process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SandboxMode = Literal["local", "cloud", "disabled"]
_VALID_MODES: frozenset[str] = frozenset({"local", "cloud", "disabled"})


class SandboxDisabledError(RuntimeError):
    """Raised when a sandbox is requested but disallowed by configuration."""


@dataclass
class ExecResult:
    """Result of running a command inside a sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def select_sandbox_mode() -> SandboxMode:
    """Return the configured sandbox mode (defaults to ``local``).

    Raises SandboxDisabledError on an unrecognised value rather than silently
    falling back, so a misconfigured cloud deploy fails closed.
    """
    raw = os.environ.get("AGENTBREEDER_SANDBOX", "local").strip().lower()
    if raw not in _VALID_MODES:
        raise SandboxDisabledError(
            f"AGENTBREEDER_SANDBOX={raw!r} is not one of {sorted(_VALID_MODES)}"
        )
    return raw  # type: ignore[return-value]


@runtime_checkable
class Sandbox(Protocol):
    """A scaffold workspace the coding agent reads from and writes into."""

    async def write(self, path: str, content: str) -> None: ...
    async def read(self, path: str) -> str: ...
    async def list(self, directory: str = ".") -> list[str]: ...
    async def exec(self, cmd: list[str], timeout: float = 30.0) -> ExecResult: ...
    async def snapshot(self) -> bytes: ...
    async def close(self) -> None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sandbox_base.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/sandbox/__init__.py engine/sandbox/base.py tests/unit/test_sandbox_base.py
git commit -m "feat(builder): add Sandbox interface + ExecResult + mode gate (W3)"
```

### Task A2: LocalSandbox — write/read/list with path containment

**Files:**
- Create: `engine/sandbox/local.py`
- Test: `tests/unit/test_sandbox_local.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sandbox_local.py
import pytest
from engine.sandbox.local import LocalSandbox


@pytest.mark.asyncio
async def test_write_then_read_roundtrip():
    sb = LocalSandbox()
    try:
        await sb.write("agent.py", "print('hi')\n")
        assert await sb.read("agent.py") == "print('hi')\n"
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_write_creates_nested_dirs():
    sb = LocalSandbox()
    try:
        await sb.write("tools/search.py", "x = 1\n")
        assert "tools/search.py" in await sb.list(".")
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_read_missing_raises_filenotfound():
    sb = LocalSandbox()
    try:
        with pytest.raises(FileNotFoundError):
            await sb.read("nope.py")
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_path_traversal_is_rejected():
    sb = LocalSandbox()
    try:
        with pytest.raises(ValueError):
            await sb.write("../escape.py", "danger")
        with pytest.raises(ValueError):
            await sb.read("/etc/passwd")
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_close_removes_workspace():
    sb = LocalSandbox()
    root = sb.root
    await sb.write("a.txt", "1")
    await sb.close()
    assert not root.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sandbox_local.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.sandbox.local'`

- [ ] **Step 3: Write minimal implementation**

```python
# engine/sandbox/local.py
"""LocalSandbox — temp-dir + subprocess scaffold workspace for local Studio.

SECURITY: runs commands on the host. Only constructed when AGENTBREEDER_SANDBOX
is 'local'. The server guards construction (see api/services/builder_session_service).
All paths are contained within the workspace root; absolute paths and '..' escapes
are rejected.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from engine.sandbox.base import ExecResult

logger = logging.getLogger(__name__)

_MAX_EXEC_TIMEOUT = 120.0
_MAX_FILE_BYTES = 1_000_000  # 1 MB per file cap


class LocalSandbox:
    """In-process sandbox backed by a temporary directory."""

    def __init__(self, prefix: str = "agentbreeder-builder-") -> None:
        self.root: Path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()

    def _resolve(self, path: str) -> Path:
        """Resolve ``path`` strictly inside the workspace root."""
        if path.startswith("/") or path.startswith("\\"):
            raise ValueError(f"absolute paths are not allowed: {path!r}")
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"path escapes the sandbox workspace: {path!r}")
        return candidate

    async def write(self, path: str, content: str) -> None:
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {_MAX_FILE_BYTES} byte cap: {path!r}")
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return target.read_text(encoding="utf-8")

    async def list(self, directory: str = ".") -> list[str]:
        base = self._resolve(directory)
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
        )

    async def exec(self, cmd: list[str], timeout: float = 30.0) -> ExecResult:
        timeout = min(timeout, _MAX_EXEC_TIMEOUT)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return ExecResult(stdout="", stderr=str(exc), exit_code=127)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(stdout="", stderr="timed out", exit_code=124, timed_out=True)
        return ExecResult(
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )

    async def snapshot(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.root.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(self.root)))
        return buf.getvalue()

    async def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sandbox_local.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/sandbox/local.py tests/unit/test_sandbox_local.py
git commit -m "feat(builder): LocalSandbox with path containment + caps (W3)"
```

### Task A3: LocalSandbox exec/snapshot + FakeSandbox test double

**Files:**
- Create: `tests/unit/fakes/__init__.py`
- Create: `tests/unit/fakes/fake_sandbox.py`
- Modify: `tests/unit/test_sandbox_local.py` (add exec + snapshot tests)

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_sandbox_local.py`)

```python
@pytest.mark.asyncio
async def test_exec_runs_and_captures_stdout():
    sb = LocalSandbox()
    try:
        await sb.write("hello.py", "print('from sandbox')\n")
        res = await sb.exec(["python", "hello.py"], timeout=10)
        assert res.ok
        assert "from sandbox" in res.stdout
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_exec_timeout_is_flagged():
    sb = LocalSandbox()
    try:
        res = await sb.exec(["python", "-c", "import time; time.sleep(5)"], timeout=0.5)
        assert res.timed_out is True
        assert res.exit_code == 124
    finally:
        await sb.close()


@pytest.mark.asyncio
async def test_snapshot_contains_written_files():
    import io, zipfile
    sb = LocalSandbox()
    try:
        await sb.write("agent.py", "x = 1\n")
        await sb.write("tools/t.py", "y = 2\n")
        data = await sb.snapshot()
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        assert {"agent.py", "tools/t.py"} <= names
    finally:
        await sb.close()
```

And the FakeSandbox double:

```python
# tests/unit/fakes/__init__.py
```

```python
# tests/unit/fakes/fake_sandbox.py
"""In-memory Sandbox double for engine + service tests (no subprocess)."""

from __future__ import annotations

from engine.sandbox.base import ExecResult


class FakeSandbox:
    """Deterministic in-memory sandbox. exec() returns scripted results."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.exec_calls: list[list[str]] = []
        self.exec_results: dict[str, ExecResult] = {}
        self.closed = False

    async def write(self, path: str, content: str) -> None:
        self.files[path] = content

    async def read(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def list(self, directory: str = ".") -> list[str]:
        return sorted(self.files)

    async def exec(self, cmd: list[str], timeout: float = 30.0) -> ExecResult:
        self.exec_calls.append(cmd)
        key = " ".join(cmd)
        return self.exec_results.get(key, ExecResult(stdout="", stderr="", exit_code=0))

    async def snapshot(self) -> bytes:
        return repr(self.files).encode("utf-8")

    async def close(self) -> None:
        self.closed = True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sandbox_local.py -k "exec or snapshot" -v`
Expected: PASS for the new LocalSandbox tests (LocalSandbox already implements exec/snapshot). If `python` is not on PATH in CI, the exec test will need `sys.executable` — if it FAILS on command-not-found, change `["python", ...]` to `[sys.executable, ...]` and add `import sys`.

- [ ] **Step 3: (only if exec test failed on PATH)** swap `"python"` → `sys.executable` in the two exec tests.

- [ ] **Step 4: Run full Part A suite**

Run: `pytest tests/unit/test_sandbox_base.py tests/unit/test_sandbox_local.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/fakes/ tests/unit/test_sandbox_local.py
git commit -m "test(builder): LocalSandbox exec/snapshot tests + FakeSandbox double (W3)"
```

---

## PART B — CodingAgentEngine (provider-loop) + Claude/Codex strategies

**File structure:**
- Modify: `engine/providers/anthropic_provider.py` — `_build_payload` tool round-trip (Task B0)
- Modify: `engine/providers/openai_provider.py` — `_build_payload` tool round-trip (Task B0)
- Create: `engine/coding_agent/__init__.py`
- Create: `engine/coding_agent/base.py` — `AgentEvent`, `AgentBounds`, `CodingAgentEngine` protocol, `CODING_TOOLS`
- Create: `engine/coding_agent/loop.py` — `run_coding_loop()` (the shared driver)
- Create: `engine/coding_agent/engines.py` — `ClaudeAgentEngine`, `CodexEngine`, `engine_for(name)`
- Create: `tests/unit/test_coding_loop.py`
- Create: `tests/unit/fakes/fake_provider.py` — scripted streaming provider double

### Task B0: Provider tool-message round-trip (foundational — spiked + confirmed required)

**Why first:** The coding loop (Task B2) feeds tool results back as OpenAI-format messages —
an assistant message carrying `tool_calls` (our `ToolCall` shape: `function_name` /
`function_arguments`) followed by `{"role": "tool", "tool_call_id", "content"}`. A spike on
2026-06-14 confirmed **neither** provider's `_build_payload` handles these today:
- `anthropic_provider._build_payload` (lines 253-259) copies only `role` + `content` (string),
  dropping `tool_calls` and never emitting Anthropic `tool_use` / `tool_result` blocks.
- `openai_provider._build_payload` (lines 166-168) passes messages verbatim, but our `ToolCall`
  shape (`function_name`/`function_arguments`) ≠ OpenAI's wire shape (`function.name`/`.arguments`).

Without this task the loop's unit tests still pass (FakeProvider), but Part C integration and
the E3 live e2e fail. Build it first.

**Files:**
- Modify: `engine/providers/anthropic_provider.py` (`_build_payload`)
- Modify: `engine/providers/openai_provider.py` (`_build_payload`)
- Test: `tests/unit/test_provider_tool_roundtrip.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_provider_tool_roundtrip.py
import json
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.openai_provider import OpenAIProvider
from engine.providers.models import ProviderConfig, ProviderType


def _messages():
    # assistant turn with a tool call, then a tool result, OpenAI-format (loop output)
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "build it"},
        {
            "role": "assistant",
            "content": "Creating agent.py",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function_name": "write_file",
                "function_arguments": json.dumps({"path": "agent.py", "content": "x=1\n"}),
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "wrote agent.py (4 bytes)"},
    ]


def test_anthropic_builds_tool_use_and_tool_result_blocks():
    p = AnthropicProvider(ProviderConfig(provider_type=ProviderType.anthropic, api_key="sk-x"))
    payload = p._build_payload(_messages(), "claude-sonnet-4-6", None, 1024, None)
    msgs = payload["messages"]
    # assistant turn carries a tool_use block
    asst = next(m for m in msgs if m["role"] == "assistant")
    tu = [b for b in asst["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
    assert tu and tu[0]["id"] == "call_1" and tu[0]["name"] == "write_file"
    assert tu[0]["input"] == {"path": "agent.py", "content": "x=1\n"}
    # tool result becomes a user turn with a tool_result block referencing the id
    tr_turns = [m for m in msgs if m["role"] == "user"
                and isinstance(m["content"], list)
                and any(b.get("type") == "tool_result" for b in m["content"])]
    assert tr_turns
    block = next(b for b in tr_turns[-1]["content"] if b.get("type") == "tool_result")
    assert block["tool_use_id"] == "call_1"


def test_openai_translates_toolcall_shape():
    p = OpenAIProvider(ProviderConfig(provider_type=ProviderType.openai, api_key="sk-x"))
    payload = p._build_payload(_messages(), "gpt-4o", None, None, None, False)
    asst = next(m for m in payload["messages"] if m["role"] == "assistant")
    fn = asst["tool_calls"][0]["function"]
    assert fn["name"] == "write_file"
    assert json.loads(fn["arguments"])["path"] == "agent.py"
    tool_msg = next(m for m in payload["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_provider_tool_roundtrip.py -v`
Expected: FAIL — assistant content is a plain string; no `tool_use`/`tool_result` blocks.

- [ ] **Step 3: Implement the Anthropic translation** — replace the message loop in
`anthropic_provider._build_payload` (currently lines 252-259) with tool-aware translation.
Consecutive `role="tool"` messages must be merged into a single user turn.

```python
        system_content: str | None = None
        non_system: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def _flush_tool_results() -> None:
            if pending_tool_results:
                non_system.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                system_content = msg.get("content", "")
                continue
            if role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
                continue
            # any non-tool message flushes buffered tool_results first
            _flush_tool_results()
            if role == "assistant" and msg.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    name = tc.get("function_name") or tc.get("function", {}).get("name", "")
                    raw = tc.get("function_arguments") or tc.get("function", {}).get("arguments", "{}")
                    try:
                        parsed = json.loads(raw) if isinstance(raw, str) else raw
                    except json.JSONDecodeError:
                        parsed = {}
                    blocks.append({
                        "type": "tool_use", "id": tc.get("id", ""),
                        "name": name, "input": parsed,
                    })
                non_system.append({"role": "assistant", "content": blocks})
            else:
                non_system.append({"role": role, "content": msg.get("content", "")})
        _flush_tool_results()
```

- [ ] **Step 4: Implement the OpenAI translation** — in `openai_provider._build_payload`, normalise
each message before assigning to `payload["messages"]` (replace the `"messages": messages` line):

```python
        norm_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                norm_messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("function_name") or tc.get("function", {}).get("name", ""),
                                "arguments": tc.get("function_arguments")
                                or tc.get("function", {}).get("arguments", "{}"),
                            },
                        }
                        for tc in msg["tool_calls"]
                    ],
                })
            else:
                norm_messages.append(dict(msg))
        payload: dict[str, Any] = {"model": model, "messages": norm_messages}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_provider_tool_roundtrip.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the existing provider suites to confirm no regression** (plain text turns must still work)

Run: `pytest tests/unit/ -k "anthropic or openai or provider" -v`
Expected: PASS (no regressions — non-tool messages take the unchanged `else` branch)

- [ ] **Step 7: Commit**

```bash
git add engine/providers/anthropic_provider.py engine/providers/openai_provider.py tests/unit/test_provider_tool_roundtrip.py
git commit -m "feat(providers): tool-message round-trip for multi-turn tool loops (W3 B0)"
```

### Task B1: AgentEvent + tool surface + engine protocol

**Files:**
- Create: `engine/coding_agent/__init__.py`
- Create: `engine/coding_agent/base.py`
- Test: `tests/unit/test_coding_agent_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_coding_agent_base.py
from engine.coding_agent.base import AgentEvent, AgentBounds, CODING_TOOLS, TOOL_NAMES


def test_agent_event_token_defaults():
    e = AgentEvent(type="token", text="hi")
    assert e.path == "" and e.diff == "" and e.error == ""


def test_coding_tools_cover_fs_surface():
    assert TOOL_NAMES == {"write_file", "read_file", "list_files", "run_command"}
    # every tool is an OpenAI-format ToolDefinition
    assert all(t.function.name in TOOL_NAMES for t in CODING_TOOLS)


def test_bounds_defaults_are_sane():
    b = AgentBounds()
    assert b.max_turns >= 1 and b.wall_clock_s > 0 and b.max_tokens > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_coding_agent_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.coding_agent'`

- [ ] **Step 3: Write minimal implementation**

```python
# engine/coding_agent/__init__.py
"""Provider-loop coding agent for the builder's eject-to-code flow."""
```

```python
# engine/coding_agent/base.py
"""Coding-agent contracts: events, bounds, the engine protocol, and the
sandbox-scoped tool surface (write/read/list/exec) shared by all engines."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from engine.providers.models import ToolDefinition, ToolFunction
from engine.sandbox.base import Sandbox

AgentEventType = Literal["token", "tool_call", "file_change", "done", "error"]


@dataclass
class AgentEvent:
    """One event emitted by a coding-agent run."""

    type: AgentEventType
    text: str = ""          # token text / done summary
    tool_name: str = ""     # for tool_call
    path: str = ""          # for file_change
    diff: str = ""          # unified diff for file_change
    error: str = ""         # for error


@dataclass
class AgentBounds:
    """Hard bounds on a coding-agent run."""

    max_turns: int = 12
    max_tokens: int = 200_000
    wall_clock_s: float = 180.0


def _tool(name: str, description: str, params: dict) -> ToolDefinition:
    return ToolDefinition(
        type="function",
        function=ToolFunction(name=name, description=description, parameters=params),
    )


CODING_TOOLS: list[ToolDefinition] = [
    _tool(
        "write_file",
        "Create or overwrite a file in the agent project workspace.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "content": {"type": "string", "description": "Full file contents."},
            },
            "required": ["path", "content"],
        },
    ),
    _tool(
        "read_file",
        "Read a file from the workspace.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    _tool(
        "list_files",
        "List files in the workspace (recursively).",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"directory": {"type": "string", "default": "."}},
        },
    ),
    _tool(
        "run_command",
        "Run a shell command in the workspace (e.g. run tests). Bounded by a timeout.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cmd": {"type": "array", "items": {"type": "string"}},
                "timeout": {"type": "number", "default": 30},
            },
            "required": ["cmd"],
        },
    ),
]

TOOL_NAMES: set[str] = {t.function.name for t in CODING_TOOLS}


class CodingAgentEngine(Protocol):
    """Strategy: which provider+model+system-prompt drives the shared loop."""

    name: str

    def run(
        self,
        instruction: str,
        history: list[dict[str, str]],
        sandbox: Sandbox,
        bounds: AgentBounds | None = None,
    ) -> AsyncIterator[AgentEvent]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_coding_agent_base.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/coding_agent/__init__.py engine/coding_agent/base.py tests/unit/test_coding_agent_base.py
git commit -m "feat(builder): coding-agent events, bounds, tool surface (W3)"
```

### Task B2: The provider-loop driver `run_coding_loop()`

**Files:**
- Create: `engine/coding_agent/loop.py`
- Create: `tests/unit/fakes/fake_provider.py`
- Test: `tests/unit/test_coding_loop.py`

- [ ] **Step 1: Write the FakeProvider double**

```python
# tests/unit/fakes/fake_provider.py
"""Scripted streaming provider double.

Each entry in ``script`` is a list of StreamChunk objects representing one
generate_stream() turn. Successive generate_stream() calls pop the next turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from engine.providers.models import StreamChunk, ToolCall


class FakeProvider:
    def __init__(self, script: list[list[StreamChunk]]) -> None:
        self._script = list(script)
        self.calls: list[list[dict]] = []
        self.closed = False

    async def generate_stream(self, messages, model=None, temperature=None,
                              max_tokens=None, tools=None) -> AsyncIterator[StreamChunk]:
        self.calls.append(list(messages))
        turn = self._script.pop(0) if self._script else []
        for chunk in turn:
            yield chunk

    async def close(self) -> None:
        self.closed = True


def text(s: str) -> StreamChunk:
    return StreamChunk(content=s)


def call(tool_id: str, name: str, args_json: str) -> StreamChunk:
    return StreamChunk(
        tool_calls=[ToolCall(id=tool_id, function_name=name, function_arguments=args_json)]
    )
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_coding_loop.py
import json
import pytest

from engine.coding_agent.base import AgentBounds
from engine.coding_agent.loop import run_coding_loop
from tests.unit.fakes.fake_provider import FakeProvider, text, call
from tests.unit.fakes.fake_sandbox import FakeSandbox


@pytest.mark.asyncio
async def test_loop_writes_file_and_emits_diff():
    sandbox = FakeSandbox()
    provider = FakeProvider([
        # turn 1: model narrates then calls write_file
        [text("Creating agent.py"),
         call("c1", "write_file", json.dumps({"path": "agent.py", "content": "print(1)\n"}))],
        # turn 2: model finishes (no tool calls)
        [text("Done.")],
    ])

    events = [e async for e in run_coding_loop(
        provider=provider, model="m", system_prompt="sys",
        instruction="build it", history=[], sandbox=sandbox,
        bounds=AgentBounds(max_turns=5),
    )]

    types = [e.type for e in events]
    assert "token" in types
    assert "file_change" in types
    assert types[-1] == "done"
    fc = next(e for e in events if e.type == "file_change")
    assert fc.path == "agent.py"
    assert "+print(1)" in fc.diff
    assert sandbox.files["agent.py"] == "print(1)\n"


@pytest.mark.asyncio
async def test_loop_respects_max_turns():
    sandbox = FakeSandbox()
    # every turn calls a tool → never terminates on its own
    provider = FakeProvider([
        [call(f"c{i}", "list_files", "{}")] for i in range(20)
    ])
    events = [e async for e in run_coding_loop(
        provider=provider, model="m", system_prompt="s",
        instruction="x", history=[], sandbox=sandbox,
        bounds=AgentBounds(max_turns=3),
    )]
    # 3 turns then a forced done
    assert events[-1].type == "done"
    assert len([e for e in events if e.type == "tool_call"]) == 3


@pytest.mark.asyncio
async def test_loop_run_command_feeds_result_back():
    sandbox = FakeSandbox()
    provider = FakeProvider([
        [call("c1", "run_command", json.dumps({"cmd": ["pytest"], "timeout": 5}))],
        [text("tests pass")],
    ])
    events = [e async for e in run_coding_loop(
        provider=provider, model="m", system_prompt="s",
        instruction="run tests", history=[], sandbox=sandbox,
    )]
    assert sandbox.exec_calls == [["pytest"]]
    assert events[-1].type == "done"
    # the tool result must have been appended to the messages of turn 2
    assert any(m.get("role") == "tool" for m in provider.calls[1])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_coding_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.coding_agent.loop'`

- [ ] **Step 4: Write minimal implementation**

```python
# engine/coding_agent/loop.py
"""The shared provider-loop driver.

Drives one ``generate_stream()`` turn at a time, executing the model's
write/read/list/exec tool calls against the sandbox and feeding the results
back as OpenAI-format tool messages (role="tool", tool_call_id=...), which the
provider abstraction normalises per backend. Bounded by AgentBounds.
"""

from __future__ import annotations

import difflib
import json
import logging
import time
from collections.abc import AsyncIterator

from engine.coding_agent.base import (
    CODING_TOOLS,
    TOOL_NAMES,
    AgentBounds,
    AgentEvent,
)
from engine.providers.models import ToolCall
from engine.sandbox.base import Sandbox

logger = logging.getLogger(__name__)


def _unified_diff(path: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


async def _apply_tool(sandbox: Sandbox, tc: ToolCall) -> tuple[str, AgentEvent | None]:
    """Execute one tool call. Returns (result_text_for_model, optional file_change event)."""
    try:
        args = json.loads(tc.function_arguments or "{}")
    except json.JSONDecodeError:
        return (f"ERROR: malformed arguments for {tc.function_name}", None)

    name = tc.function_name
    if name == "write_file":
        path, content = args.get("path", ""), args.get("content", "")
        try:
            old = await sandbox.read(path)
        except FileNotFoundError:
            old = ""
        await sandbox.write(path, content)
        diff = _unified_diff(path, old, content)
        return (f"wrote {path} ({len(content)} bytes)",
                AgentEvent(type="file_change", path=path, diff=diff))
    if name == "read_file":
        try:
            return (await sandbox.read(args.get("path", "")), None)
        except FileNotFoundError:
            return (f"ERROR: file not found: {args.get('path')}", None)
    if name == "list_files":
        files = await sandbox.list(args.get("directory", "."))
        return ("\n".join(files), None)
    if name == "run_command":
        res = await sandbox.exec(args.get("cmd", []), timeout=float(args.get("timeout", 30)))
        body = f"exit={res.exit_code} timed_out={res.timed_out}\n{res.stdout}\n{res.stderr}"
        return (body, None)
    return (f"ERROR: unknown tool {name}", None)


async def run_coding_loop(
    *,
    provider,
    model: str,
    system_prompt: str,
    instruction: str,
    history: list[dict[str, str]],
    sandbox: Sandbox,
    bounds: AgentBounds | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run the coding agent until it stops calling tools or a bound trips."""
    bounds = bounds or AgentBounds()
    started = time.monotonic()
    tokens_seen = 0

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": instruction})

    for _turn in range(bounds.max_turns):
        if time.monotonic() - started > bounds.wall_clock_s:
            yield AgentEvent(type="done", text="stopped: wall-clock bound reached")
            return

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        async for chunk in provider.generate_stream(
            messages, model=model, tools=CODING_TOOLS
        ):
            if chunk.content:
                text_parts.append(chunk.content)
                tokens_seen += len(chunk.content)
                yield AgentEvent(type="token", text=chunk.content)
            if chunk.tool_calls:
                tool_calls.extend(chunk.tool_calls)

        # No tool calls → the agent is done.
        if not tool_calls:
            yield AgentEvent(type="done", text="".join(text_parts))
            return

        # Record the assistant turn (text + the tool_use calls).
        messages.append({
            "role": "assistant",
            "content": "".join(text_parts),
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        })

        for tc in tool_calls:
            if tc.function_name not in TOOL_NAMES:
                result = f"ERROR: tool {tc.function_name} is not available"
                fc = None
            else:
                yield AgentEvent(type="tool_call", tool_name=tc.function_name)
                result, fc = await _apply_tool(sandbox, tc)
            if fc is not None:
                yield fc
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result[:20_000],
            })

        if tokens_seen > bounds.max_tokens:
            yield AgentEvent(type="done", text="stopped: token bound reached")
            return

    yield AgentEvent(type="done", text="stopped: max turns reached")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_coding_loop.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add engine/coding_agent/loop.py tests/unit/fakes/fake_provider.py tests/unit/test_coding_loop.py
git commit -m "feat(builder): provider-loop coding driver with bounds + diffs (W3)"
```

### Task B3: Claude + Codex engine strategies + `engine_for()`

**Files:**
- Create: `engine/coding_agent/engines.py`
- Test: `tests/unit/test_coding_engines.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_coding_engines.py
import pytest
from engine.coding_agent.engines import engine_for, ClaudeAgentEngine, CodexEngine
from tests.unit.fakes.fake_provider import FakeProvider, text
from tests.unit.fakes.fake_sandbox import FakeSandbox


def test_engine_for_claude():
    e = engine_for("claude", provider=FakeProvider([]))
    assert isinstance(e, ClaudeAgentEngine)
    assert e.name == "claude"


def test_engine_for_codex():
    e = engine_for("codex", provider=FakeProvider([]))
    assert isinstance(e, CodexEngine)
    assert e.name == "codex"


def test_engine_for_unknown_raises():
    with pytest.raises(ValueError):
        engine_for("bard", provider=FakeProvider([]))


@pytest.mark.asyncio
async def test_engine_run_streams_done():
    provider = FakeProvider([[text("hi")]])
    engine = engine_for("claude", provider=provider)
    events = [e async for e in engine.run("build", [], FakeSandbox())]
    assert events[-1].type == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_coding_engines.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.coding_agent.engines'`

- [ ] **Step 3: Write minimal implementation**

```python
# engine/coding_agent/engines.py
"""Claude / Codex coding-agent strategies over the shared provider loop.

Each engine is a thin strategy: it owns the model id + system prompt and hands
an injected provider to run_coding_loop(). The provider is constructed by the
API layer from the BYO key in the secrets backend (never here)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from engine.coding_agent.base import AgentBounds, AgentEvent
from engine.coding_agent.loop import run_coding_loop
from engine.sandbox.base import Sandbox

_CODING_SYSTEM_PROMPT = """\
You are AgentBreeder's coding agent. You are ejecting a validated agent.yaml into
real code. Write agent.py, any tools/ modules, and tests into the workspace using
the provided tools. Keep the code framework-correct for the chosen framework, runnable,
and covered by at least one test. Use write_file to create files, run_command to run
tests, and stop when the project is complete. Never print secrets."""


class _BaseEngine:
    name: str = ""
    model: str = ""

    def __init__(self, provider) -> None:
        self._provider = provider

    def run(
        self,
        instruction: str,
        history: list[dict[str, str]],
        sandbox: Sandbox,
        bounds: AgentBounds | None = None,
    ) -> AsyncIterator[AgentEvent]:
        return run_coding_loop(
            provider=self._provider,
            model=self.model,
            system_prompt=_CODING_SYSTEM_PROMPT,
            instruction=instruction,
            history=history,
            sandbox=sandbox,
            bounds=bounds,
        )


class ClaudeAgentEngine(_BaseEngine):
    name = "claude"
    model = "claude-sonnet-4-6"


class CodexEngine(_BaseEngine):
    name = "codex"
    model = "gpt-4o"


def engine_for(name: str, *, provider) -> _BaseEngine:
    if name == "claude":
        return ClaudeAgentEngine(provider)
    if name == "codex":
        return CodexEngine(provider)
    raise ValueError(f"unknown coding engine: {name!r} (expected 'claude' or 'codex')")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_coding_engines.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full Part B suite + commit**

Run: `pytest tests/unit/test_coding_agent_base.py tests/unit/test_coding_loop.py tests/unit/test_coding_engines.py -v`
Expected: PASS (all)

```bash
git add engine/coding_agent/engines.py tests/unit/test_coding_engines.py
git commit -m "feat(builder): Claude + Codex coding-engine strategies (W3)"
```

---

## PART C — BuilderSession model + service + §6 API

> **TEST-INFRA CORRECTION (discovered 2026-06-14, supersedes the test snippets below):**
> The integration conftest (`tests/integration/conftest.py`) mocks **auth only** via autouse
> (injects an admin with `team="engineering"`); there is **no `async_client` / `auth_headers` /
> `other_team_headers` / `fake_builder_key` fixture and no DB-session override**. The harness uses
> the **synchronous** `fastapi.testclient.TestClient`. Postgres is NOT available in this dev env.
> Therefore Part C integration tests MUST follow the repo's established no-DB pattern:
> - `@pytest.fixture def client() -> TestClient: return TestClient(app)` (per-file, like `test_builders_chat_stream.py`).
> - Override the DB dep: `from api.database import get_db; app.dependency_overrides[get_db] = lambda: AsyncMock()` (set in a fixture, popped in teardown) — the real DB is never hit.
> - `@patch`/`monkeypatch` the `BuilderSessionService` methods (`create`/`get`/`list_for_team`/`run_interview_turn`/`run_eject`/`save_state`) to return fakes, asserting route wiring + SSE framing (NOT real persistence). Mirror `_override_db_dep()` + `@patch("api.routes.agentops.FleetService...")` in `tests/unit/test_api_routes_coverage_boost.py`.
> - BYO key: monkeypatch `get_workspace_backend` to a fake backend (as `test_builders_chat_stream.py` does), not a `fake_builder_key` fixture.
> - Team-scoping 404: simulate by having the patched `service.get` return `None` (the autouse user is always team `engineering`; there is no real second tenant).
> - `SessionEventBus` is tested directly (real, no DB).
> The production `BuilderSessionService(db, bus)` design (DB-backed) is UNCHANGED; only the test approach is corrected. Migration apply (`alembic upgrade head`) is deferred to a DB-enabled run (note it; don't block).

**File structure:**
- Modify: `api/models/database.py` — add `BuilderSession` table
- Create: `alembic/versions/0NN_add_builder_sessions.py` — migration (NN = next number)
- Modify: `api/models/schemas.py` — request/response Pydantic models
- Create: `api/services/builder_session_service.py` — CRUD + event bus + orchestration
- Create: `api/routes/builder_sessions.py` — §6 endpoints
- Modify: `api/main.py` — register router
- Create: `tests/integration/test_builder_sessions.py`

### Task C1: BuilderSession DB model + migration

**Files:**
- Modify: `api/models/database.py` (add after `DeployJob`, ~line 163)
- Create: `alembic/versions/0NN_add_builder_sessions.py`

- [ ] **Step 1: Determine the next migration number**

Run: `ls alembic/versions/ | grep -oE '^[0-9]+' | sort -n | tail -1`
Use `next = that + 1`, zero-padded to 3 digits (the explorer saw `022` as latest, so likely `023`). Substitute for `0NN` below and set `down_revision` to the latest existing revision id.

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_builder_sessions.py  (model import smoke first)
def test_builder_session_model_importable():
    from api.models.database import BuilderSession
    assert BuilderSession.__tablename__ == "builder_sessions"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/integration/test_builder_sessions.py::test_builder_session_model_importable -v`
Expected: FAIL — `ImportError: cannot import name 'BuilderSession'`

- [ ] **Step 4: Add the model** (in `api/models/database.py`, after the `DeployJob` class)

```python
class BuilderSession(Base):
    """A resumable conversational-builder session (Wave 3)."""

    __tablename__ = "builder_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(20), default="claude", nullable=False)
    # state JSON: {history: [...], agent_yaml: str|None, files: {path: content},
    #              deploy_job_id: str|None, satisfied: [...refs]}
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 5: Write the migration**

```python
# alembic/versions/0NN_add_builder_sessions.py
"""add builder_sessions

Revision ID: 0NN
Revises: <latest>
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0NN"
down_revision = "<latest>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "builder_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("team", sa.String(100), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("engine", sa.String(20), nullable=False, server_default="claude"),
        sa.Column("state", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_builder_sessions_team", "builder_sessions", ["team"])


def downgrade() -> None:
    op.drop_index("ix_builder_sessions_team", table_name="builder_sessions")
    op.drop_table("builder_sessions")
```

- [ ] **Step 6: Run test + apply migration**

Run: `pytest tests/integration/test_builder_sessions.py::test_builder_session_model_importable -v` → PASS
Run: `alembic upgrade head` → applies cleanly (requires local Postgres up).

- [ ] **Step 7: Commit**

```bash
git add api/models/database.py alembic/versions/0NN_add_builder_sessions.py tests/integration/test_builder_sessions.py
git commit -m "feat(builder): BuilderSession table + migration (W3)"
```

### Task C2: Session service — CRUD + event bus

**Files:**
- Modify: `api/models/schemas.py` — add response models
- Create: `api/services/builder_session_service.py`
- Test: `tests/integration/test_builder_sessions.py` (add service tests)

- [ ] **Step 1: Add Pydantic schemas** (`api/models/schemas.py`, near `DeployJobResponse`)

```python
class BuilderSessionResponse(BaseModel):
    id: str
    team: str
    engine: str
    agent_yaml: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    deploy_job_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class BuilderSessionCreateRequest(BaseModel):
    engine: str = "claude"  # "claude" | "codex"


class BuilderEjectRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=4000)
    engine: str | None = None  # override session engine for this run
```

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_builder_sessions.py  (append)
import pytest
from api.services.builder_session_service import BuilderSessionService, SessionEventBus


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    bus = SessionEventBus()
    async with bus.subscribe("s1") as q:
        await bus.publish("s1", {"event": "token", "data": "{}"})
        evt = await q.get()
        assert evt["event"] == "token"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/integration/test_builder_sessions.py::test_event_bus_publish_subscribe -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.builder_session_service'`

- [ ] **Step 4: Write minimal implementation**

```python
# api/services/builder_session_service.py
"""BuilderSession persistence, the per-session SSE event bus, and orchestration
of interview / eject / deploy turns. Mirrors the deploy_event_bus pattern.

Governance: the coding agent writes into a sandbox; nothing is auto-deployed.
Deploy still flows through the existing /deploys pipeline (Parse → RBAC →
Resolve → Build → Provision → Deploy → Health → Register)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import BuilderSession

logger = logging.getLogger(__name__)


class SessionEventBus:
    """In-process pub/sub keyed by session id (swap for Redis in cloud, W4)."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    @contextlib.asynccontextmanager
    async def subscribe(self, sid: str) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(sid, set()).add(q)
        try:
            yield q
        finally:
            self._subs.get(sid, set()).discard(q)

    async def publish(self, sid: str, event: dict[str, str]) -> None:
        for q in list(self._subs.get(sid, set())):
            await q.put(event)


class BuilderSessionService:
    def __init__(self, db: AsyncSession, bus: SessionEventBus) -> None:
        self._db = db
        self._bus = bus

    async def create(self, *, team: str, user_id: uuid.UUID, engine: str) -> BuilderSession:
        sess = BuilderSession(
            team=team, user_id=user_id, engine=engine,
            state={"history": [], "agent_yaml": None, "files": {},
                   "deploy_job_id": None, "satisfied": []},
        )
        self._db.add(sess)
        await self._db.flush()
        return sess

    async def get(self, sid: uuid.UUID, *, team: str) -> BuilderSession | None:
        row = await self._db.get(BuilderSession, sid)
        if row is None or row.team != team:
            return None
        return row

    async def list_for_team(self, team: str) -> list[BuilderSession]:
        res = await self._db.execute(
            select(BuilderSession).where(BuilderSession.team == team)
        )
        return list(res.scalars().all())

    async def save_state(self, sess: BuilderSession, state: dict[str, Any]) -> None:
        sess.state = state
        await self._db.flush()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_builder_sessions.py::test_event_bus_publish_subscribe -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/models/schemas.py api/services/builder_session_service.py tests/integration/test_builder_sessions.py
git commit -m "feat(builder): BuilderSession service + SSE event bus (W3)"
```

### Task C3: §6 routes — create / get / list

**Files:**
- Create: `api/routes/builder_sessions.py`
- Modify: `api/main.py` (register router + attach bus to `app.state`)
- Test: `tests/integration/test_builder_sessions.py`

- [ ] **Step 1: Write the failing test** (uses the existing integration test client + auth fixtures — mirror the auth/team fixtures already used in `tests/integration/test_deploys.py`)

```python
# tests/integration/test_builder_sessions.py  (append)
@pytest.mark.asyncio
async def test_create_and_get_session(async_client, auth_headers):
    r = await async_client.post("/api/v1/builder/sessions",
                                json={"engine": "claude"}, headers=auth_headers)
    assert r.status_code == 200
    sid = r.json()["data"]["id"]
    g = await async_client.get(f"/api/v1/builder/sessions/{sid}", headers=auth_headers)
    assert g.status_code == 200
    assert g.json()["data"]["engine"] == "claude"


@pytest.mark.asyncio
async def test_get_other_teams_session_404(async_client, auth_headers, other_team_headers):
    r = await async_client.post("/api/v1/builder/sessions",
                                json={"engine": "claude"}, headers=auth_headers)
    sid = r.json()["data"]["id"]
    g = await async_client.get(f"/api/v1/builder/sessions/{sid}", headers=other_team_headers)
    assert g.status_code == 404
```

> If `async_client`/`auth_headers`/`other_team_headers` fixtures don't exist by those names, reuse whatever `tests/integration/test_deploys.py` and `tests/integration/test_builders*.py` use (same conftest). Match the existing fixture names — do not invent new ones.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_builder_sessions.py -k "create_and_get or other_teams" -v`
Expected: FAIL — 404 (route not registered)

- [ ] **Step 3: Write the router**

```python
# api/routes/builder_sessions.py
"""BuilderSession API (Wave 3, spec §6).

POST   /api/v1/builder/sessions                  create
GET    /api/v1/builder/sessions                  list (team-scoped)
GET    /api/v1/builder/sessions/{id}             get state
POST   /api/v1/builder/sessions/{id}/messages    interview turn (SSE)
POST   /api/v1/builder/sessions/{id}/eject       eject-to-code (SSE)
GET    /api/v1/builder/sessions/{id}/stream      SSE for all session events
POST   /api/v1/builder/sessions/{id}/deploy      deploy from current spec
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import User
from api.models.schemas import (
    ApiResponse,
    BuilderSessionCreateRequest,
    BuilderSessionResponse,
)
from api.services.builder_session_service import BuilderSessionService, SessionEventBus

router = APIRouter(prefix="/api/v1/builder/sessions", tags=["builder-sessions"])


def _bus(request: Request) -> SessionEventBus:
    return request.app.state.builder_event_bus


def _to_response(sess) -> BuilderSessionResponse:
    st = sess.state or {}
    return BuilderSessionResponse(
        id=str(sess.id), team=sess.team, engine=sess.engine,
        agent_yaml=st.get("agent_yaml"), files=st.get("files", {}),
        deploy_job_id=st.get("deploy_job_id"), history=st.get("history", []),
    )


@router.post("", response_model=ApiResponse[BuilderSessionResponse])
async def create_session(
    body: BuilderSessionCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BuilderSessionResponse]:
    if body.engine not in ("claude", "codex"):
        raise HTTPException(400, "engine must be 'claude' or 'codex'")
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.create(team=user.team, user_id=user.id, engine=body.engine)
    return ApiResponse(data=_to_response(sess))


@router.get("", response_model=ApiResponse[list[BuilderSessionResponse]])
async def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[BuilderSessionResponse]]:
    svc = BuilderSessionService(db, _bus(request))
    rows = await svc.list_for_team(user.team)
    return ApiResponse(data=[_to_response(s) for s in rows])


@router.get("/{session_id}", response_model=ApiResponse[BuilderSessionResponse])
async def get_session(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BuilderSessionResponse]:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(404, "Builder session not found")
    return ApiResponse(data=_to_response(sess))
```

- [ ] **Step 4: Register router + bus** (`api/main.py` — follow the existing `include_router` block and the `app.state.deploy_event_bus` assignment)

```python
from api.routes import builder_sessions
from api.services.builder_session_service import SessionEventBus
# ... where other routers are included:
app.include_router(builder_sessions.router)
# ... where deploy_event_bus is set on app.state (startup):
app.state.builder_event_bus = SessionEventBus()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_builder_sessions.py -k "create_and_get or other_teams" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/builder_sessions.py api/main.py tests/integration/test_builder_sessions.py
git commit -m "feat(builder): BuilderSession create/get/list routes (W3 §6)"
```

### Task C4: `/messages` interview turn over the session (SSE)

**Files:**
- Modify: `api/routes/builder_sessions.py` (add `/messages`)
- Modify: `api/services/builder_session_service.py` (add `run_interview_turn`)
- Test: `tests/integration/test_builder_sessions.py`

This reuses `run_chat_turn_stream` (the W1 engine) but persists history into the session and re-emits events onto the session bus. The BYO key is read via the existing `_builder_key_name` + `get_workspace_backend()` pattern.

- [ ] **Step 1: Write the failing test** (mock the provider so no network — patch `run_chat_turn_stream` to a scripted async generator, mirroring `tests/integration/test_builders_chat_stream.py`)

```python
# tests/integration/test_builder_sessions.py  (append)
@pytest.mark.asyncio
async def test_messages_turn_persists_history(async_client, auth_headers, monkeypatch, fake_builder_key):
    from engine.agent_chat_builder import ChatStreamEvent, ChatTurnResult

    async def fake_stream(provider, history):
        yield ChatStreamEvent(type="token", text="Hello")
        yield ChatStreamEvent(type="done",
            result=ChatTurnResult(assistant_message="Hello", agent_yaml=None, valid=False))

    monkeypatch.setattr("api.services.builder_session_service.run_chat_turn_stream", fake_stream)

    sid = (await async_client.post("/api/v1/builder/sessions",
            json={"engine": "claude"}, headers=auth_headers)).json()["data"]["id"]
    r = await async_client.post(f"/api/v1/builder/sessions/{sid}/messages",
            json={"content": "build a support agent"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.text
    assert "event: token" in body and "event: done" in body

    g = await async_client.get(f"/api/v1/builder/sessions/{sid}", headers=auth_headers)
    hist = g.json()["data"]["history"]
    assert hist[0]["role"] == "user"
    assert hist[-1]["role"] == "assistant"
```

> `fake_builder_key` fixture: seed the workspace secrets backend with `AGENTBREEDER_CLAUDE_BUILDER_KEY__{user.id}` = `"sk-test"`. If such a fixture exists in the builders-chat tests, reuse it; otherwise add it to the integration conftest.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_builder_sessions.py::test_messages_turn_persists_history -v`
Expected: FAIL — 405/404 (no `/messages` route)

- [ ] **Step 3: Add service method** (`builder_session_service.py`)

```python
from dataclasses import asdict

from engine.agent_chat_builder import run_chat_turn_stream


class BuilderSessionService:
    # ... existing ...

    async def run_interview_turn(self, sess, provider, user_text: str):
        """Async-generate SSE frames for one interview turn; persists history."""
        state = dict(sess.state or {})
        history = list(state.get("history", []))
        history.append({"role": "user", "content": user_text})

        assistant_text = ""
        async for evt in run_chat_turn_stream(provider, history):
            if evt.type == "token":
                assistant_text += evt.text
                yield {"event": "token", "data": _json({"text": evt.text})}
            elif evt.type == "setup_request" and evt.setup is not None:
                yield {"event": "setup_request", "data": _json(asdict(evt.setup))}
            elif evt.type == "done" and evt.result is not None:
                r = evt.result
                if r.agent_yaml:
                    state["agent_yaml"] = r.agent_yaml
                    yield {"event": "spec_update", "data": _json(
                        {"agent_yaml": r.agent_yaml, "valid": r.valid, "errors": r.errors})}
                history.append({"role": "assistant", "content": r.assistant_message})
                yield {"event": "done", "data": _json(asdict(r))}

        state["history"] = history
        await self.save_state(sess, state)
```

Add a tiny json helper at module top:

```python
import json as _jsonlib
def _json(obj) -> str:
    return _jsonlib.dumps(obj)
```

- [ ] **Step 4: Add the route** (`builder_sessions.py`)

```python
import json
from collections.abc import AsyncGenerator
from dataclasses import asdict

from sse_starlette.sse import EventSourceResponse

from api.models.schemas import BuilderMessageRequest  # add this schema (content: str)
from api.routes.builders import _builder_key_name, _no_key_detail
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.models import ProviderConfig, ProviderType
from engine.secrets.factory import get_workspace_backend


@router.post("/{session_id}/messages")
async def post_message(
    session_id: uuid.UUID,
    body: BuilderMessageRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(404, "Builder session not found")

    secret_name = _builder_key_name(user)
    backend, _ws = get_workspace_backend()
    api_key = await backend.get(secret_name)
    if not api_key:
        raise HTTPException(400, _no_key_detail(secret_name))

    async def generator() -> AsyncGenerator[dict, None]:
        provider = AnthropicProvider(
            ProviderConfig(provider_type=ProviderType.anthropic, api_key=api_key))
        try:
            async for frame in svc.run_interview_turn(sess, provider, body.content):
                yield frame
        finally:
            await provider.close()

    return EventSourceResponse(generator())
```

Add `BuilderMessageRequest` to `schemas.py`:

```python
class BuilderMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_builder_sessions.py::test_messages_turn_persists_history -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/builder_sessions.py api/services/builder_session_service.py api/models/schemas.py tests/integration/test_builder_sessions.py
git commit -m "feat(builder): session /messages interview turn with persisted history (W3 §6)"
```

### Task C5: `/eject` — coding-agent run streaming file diffs

**Files:**
- Modify: `api/routes/builder_sessions.py` (add `/eject`)
- Modify: `api/services/builder_session_service.py` (add `run_eject` + sandbox gating)
- Test: `tests/integration/test_builder_sessions.py`

- [ ] **Step 1: Write the failing test** (mock `engine_for` to return a fake engine emitting file_change + done; assert files persist into session state)

```python
# tests/integration/test_builder_sessions.py  (append)
@pytest.mark.asyncio
async def test_eject_persists_generated_files(async_client, auth_headers, monkeypatch, fake_builder_key):
    from engine.coding_agent.base import AgentEvent

    class FakeEngine:
        name = "claude"
        async def run(self, instruction, history, sandbox, bounds=None):
            await sandbox.write("agent.py", "print('hi')\n")
            yield AgentEvent(type="file_change", path="agent.py", diff="+print('hi')")
            yield AgentEvent(type="done", text="done")

    monkeypatch.setattr(
        "api.services.builder_session_service.engine_for",
        lambda name, provider: FakeEngine())

    sid = (await async_client.post("/api/v1/builder/sessions",
            json={"engine": "claude"}, headers=auth_headers)).json()["data"]["id"]
    r = await async_client.post(f"/api/v1/builder/sessions/{sid}/eject",
            json={"instruction": "add a custom tool"}, headers=auth_headers)
    assert r.status_code == 200
    assert "event: file_change" in r.text

    files = (await async_client.get(f"/api/v1/builder/sessions/{sid}",
             headers=auth_headers)).json()["data"]["files"]
    assert files["agent.py"] == "print('hi')\n"


@pytest.mark.asyncio
async def test_eject_blocked_when_sandbox_cloud(async_client, auth_headers, monkeypatch, fake_builder_key):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "cloud")
    sid = (await async_client.post("/api/v1/builder/sessions",
            json={"engine": "claude"}, headers=auth_headers)).json()["data"]["id"]
    r = await async_client.post(f"/api/v1/builder/sessions/{sid}/eject",
            json={"instruction": "x"}, headers=auth_headers)
    # cloud sandbox not available until W4 → 409
    assert r.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_builder_sessions.py -k eject -v`
Expected: FAIL — no `/eject` route

- [ ] **Step 3: Add service method** (`builder_session_service.py`)

```python
from engine.coding_agent.engines import engine_for
from engine.sandbox.base import select_sandbox_mode
from engine.sandbox.local import LocalSandbox


class CloudSandboxUnavailable(RuntimeError):
    """Raised when cloud sandbox is selected but not yet available (pre-W4)."""


def _make_sandbox():
    mode = select_sandbox_mode()
    if mode == "local":
        return LocalSandbox()
    if mode == "cloud":
        raise CloudSandboxUnavailable("CloudSandbox is not available yet (Wave 4)")
    raise CloudSandboxUnavailable("Sandbox is disabled (AGENTBREEDER_SANDBOX=disabled)")


class BuilderSessionService:
    # ... existing ...

    async def run_eject(self, sess, provider, instruction: str, engine_name: str):
        """Run the coding agent in a sandbox; stream events; persist files."""
        sandbox = _make_sandbox()
        state = dict(sess.state or {})
        # seed the sandbox with files already generated this session
        for path, content in (state.get("files") or {}).items():
            await sandbox.write(path, content)
        if state.get("agent_yaml"):
            await sandbox.write("agent.yaml", state["agent_yaml"])

        engine = engine_for(engine_name, provider=provider)
        try:
            async for evt in engine.run(instruction, state.get("history", []), sandbox):
                if evt.type == "token":
                    yield {"event": "token", "data": _json({"text": evt.text})}
                elif evt.type == "tool_call":
                    yield {"event": "tool_call", "data": _json({"tool": evt.tool_name})}
                elif evt.type == "file_change":
                    yield {"event": "file_change",
                           "data": _json({"path": evt.path, "diff": evt.diff})}
                elif evt.type == "done":
                    yield {"event": "complete", "data": _json({"summary": evt.text})}
                elif evt.type == "error":
                    yield {"event": "error", "data": _json({"detail": evt.error})}
            # snapshot the workspace back into session state
            state["files"] = {p: await sandbox.read(p) for p in await sandbox.list(".")}
            await self.save_state(sess, state)
        finally:
            await sandbox.close()
```

- [ ] **Step 4: Add the route** (`builder_sessions.py`)

```python
from api.models.schemas import BuilderEjectRequest
from api.services.builder_session_service import CloudSandboxUnavailable


@router.post("/{session_id}/eject")
async def eject_to_code(
    session_id: uuid.UUID,
    body: BuilderEjectRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(404, "Builder session not found")

    engine_name = body.engine or sess.engine
    if engine_name not in ("claude", "codex"):
        raise HTTPException(400, "engine must be 'claude' or 'codex'")

    # Fail fast on sandbox availability before opening the stream.
    try:
        from engine.sandbox.base import select_sandbox_mode
        if select_sandbox_mode() != "local":
            raise CloudSandboxUnavailable()
    except CloudSandboxUnavailable:
        raise HTTPException(409, "Code generation requires local sandbox mode (cloud sandbox lands in Wave 4).")

    secret_name = _builder_key_name(user)
    backend, _ws = get_workspace_backend()
    api_key = await backend.get(secret_name)
    if not api_key:
        raise HTTPException(400, _no_key_detail(secret_name))

    async def generator():
        provider = AnthropicProvider(
            ProviderConfig(provider_type=ProviderType.anthropic, api_key=api_key))
        try:
            async for frame in svc.run_eject(sess, provider, body.instruction, engine_name):
                yield frame
        finally:
            await provider.close()

    return EventSourceResponse(generator())
```

> NOTE (Codex parity): the `/eject` route above constructs an `AnthropicProvider`. For `engine="codex"`, construct an `OpenAIProvider` from the user's OpenAI key secret instead. Add a `_provider_for_engine(engine_name, user)` helper that returns the right provider + the right BYO-key secret name (`AGENTBREEDER_CLAUDE_BUILDER_KEY__{id}` for claude, an `AGENTBREEDER_CODEX_BUILDER_KEY__{id}` for codex). Implement it now so Codex isn't a stub — mirror `_builder_key_name`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_builder_sessions.py -k eject -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add api/routes/builder_sessions.py api/services/builder_session_service.py api/models/schemas.py tests/integration/test_builder_sessions.py
git commit -m "feat(builder): /eject coding-agent run with sandbox gating (W3 §6)"
```

### Task C6: `/deploy` from session + `/stream` aggregate SSE

**Files:**
- Modify: `api/routes/builder_sessions.py`
- Test: `tests/integration/test_builder_sessions.py`

- [ ] **Step 1: Write the failing test** (deploy reuses the existing deploy pipeline by POSTing the session's `agent_yaml`; mock `DeployJobService` create to return a fake job id, mirroring `tests/integration/test_deploys.py` mocking)

```python
# tests/integration/test_builder_sessions.py  (append)
@pytest.mark.asyncio
async def test_deploy_from_session_uses_pipeline(async_client, auth_headers, monkeypatch):
    # seed a session with a valid agent_yaml directly via the service
    sid = (await async_client.post("/api/v1/builder/sessions",
            json={"engine": "claude"}, headers=auth_headers)).json()["data"]["id"]
    # patch get to inject agent_yaml, and patch deploy creation
    import api.routes.builder_sessions as mod

    async def fake_create_deploy_from_yaml(yaml_content, team, user, db, bus):
        return "job-123"
    monkeypatch.setattr(mod, "_create_deploy_from_yaml", fake_create_deploy_from_yaml)
    monkeypatch.setattr(mod.BuilderSessionService, "get",
        lambda self, sid_, team: _fake_session_with_yaml(sid_, team))

    r = await async_client.post(f"/api/v1/builder/sessions/{sid}/deploy", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["deploy_job_id"] == "job-123"


@pytest.mark.asyncio
async def test_deploy_without_spec_400(async_client, auth_headers):
    sid = (await async_client.post("/api/v1/builder/sessions",
            json={"engine": "claude"}, headers=auth_headers)).json()["data"]["id"]
    r = await async_client.post(f"/api/v1/builder/sessions/{sid}/deploy", headers=auth_headers)
    assert r.status_code == 400  # no agent_yaml yet
```

> The mocking shape here is illustrative — match the actual `DeployJobService` constructor + create signature used in `api/routes/deploys.py::create_deploy`. The real route should call the same service the deploys route uses (do NOT duplicate pipeline logic — reuse `DeployJobService`). Adjust the test's monkeypatch targets to the real symbols.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_builder_sessions.py -k "deploy_from_session or deploy_without_spec" -v`
Expected: FAIL — no `/deploy` route

- [ ] **Step 3: Add `/deploy` + `/stream` routes** (reuse `DeployJobService` exactly as `deploys.py` does — RBAC, team resolution, audit, registry are all preserved by going through it)

```python
@router.post("/{session_id}/deploy", response_model=ApiResponse[BuilderSessionResponse])
async def deploy_from_session(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BuilderSessionResponse]:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(404, "Builder session not found")
    agent_yaml = (sess.state or {}).get("agent_yaml")
    if not agent_yaml:
        raise HTTPException(400, "Session has no validated agent.yaml to deploy yet.")

    # Reuse the exact deploy entrypoint the /deploys route uses so the full
    # governance pipeline (RBAC, team check, audit, registry) is preserved.
    from api.models.schemas import DeployRequest
    from api.routes.deploys import _resolve_deploy_team
    from api.services.deploy_service import DeployJobService

    body = DeployRequest(config_yaml=agent_yaml, target="local")
    team, agent = await _resolve_deploy_team(body, db)
    # enforce_team_role(user, team, "deployer") — call the same guard deploys.py calls
    deploy_svc = DeployJobService(db, request.app.state.deploy_event_bus)
    job = await deploy_svc.create(body, team=team, agent=agent, user=user)

    state = dict(sess.state or {})
    state["deploy_job_id"] = str(job.id)
    await svc.save_state(sess, state)
    return ApiResponse(data=_to_response(sess))


@router.get("/{session_id}/stream")
async def stream_session(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(404, "Builder session not found")
    bus = _bus(request)

    async def generator():
        import asyncio
        async with bus.subscribe(str(session_id)) as queue:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield evt

    return EventSourceResponse(generator())
```

> Match `DeployJobService(...)` and `.create(...)` to their real signatures in `api/services/deploy_service.py`. If the deploys route resolves team + enforces role inline rather than via a service method, replicate that exact sequence (don't skip `enforce_team_role`). The governance pipeline MUST NOT be bypassed (CLAUDE.md §2).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_builder_sessions.py -k "deploy or stream" -v`
Expected: PASS

- [ ] **Step 5: Run the full Part C suite + the existing builders/deploys suites (no regressions)**

Run: `pytest tests/integration/test_builder_sessions.py tests/integration/test_deploys.py tests/unit/test_sandbox_*.py tests/unit/test_coding_*.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add api/routes/builder_sessions.py tests/integration/test_builder_sessions.py
git commit -m "feat(builder): /deploy from session + aggregate /stream (W3 §6)"
```

---

## PART D — Frontend: two-pane build console + Code tab + eject UI

**File structure:**
- Modify: `dashboard/src/lib/api.ts` — `builderSessions` client + types
- Create: `dashboard/src/components/agent-wizard/CodeArtifactPanel.tsx` — file tree + diff viewer
- Create: `dashboard/src/components/agent-wizard/CodeArtifactPanel.test.tsx`
- Modify: `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx` — add artifact panel tabs (Spec | Code | Deploy) + "Eject to code" affordance
- Modify: `dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx`

### Task D1: `builderSessions` API client + types

**Files:**
- Modify: `dashboard/src/lib/api.ts`
- Test: `dashboard/src/lib/api.test.ts` (if present; else cover via component tests)

- [ ] **Step 1: Add types + client** (`api.ts`, near the `builders` object ~line 2421)

```typescript
export interface BuilderSession {
  id: string;
  team: string;
  engine: "claude" | "codex";
  agent_yaml: string | null;
  files: Record<string, string>;
  deploy_job_id: string | null;
  history: { role: string; content: string }[];
}

export interface BuilderFileChange {
  path: string;
  diff: string;
}

// in the api object, after `builders: { ... },`
builderSessions: {
  create: (engine: "claude" | "codex" = "claude") =>
    request<BuilderSession>("/builder/sessions", {
      method: "POST",
      body: JSON.stringify({ engine }),
    }),
  get: (id: string) => request<BuilderSession>(`/builder/sessions/${id}`),
  sendMessage: (
    id: string,
    content: string,
    onEvent: (event: string, data: unknown) => void,
  ) =>
    streamSSE(
      `/builder/sessions/${id}/messages`,
      { method: "POST", body: JSON.stringify({ content }) },
      onEvent,
    ),
  eject: (
    id: string,
    instruction: string,
    onEvent: (event: string, data: unknown) => void,
    engine?: "claude" | "codex",
  ) =>
    streamSSE(
      `/builder/sessions/${id}/eject`,
      { method: "POST", body: JSON.stringify({ instruction, engine }) },
      onEvent,
    ),
  deploy: (id: string) =>
    request<BuilderSession>(`/builder/sessions/${id}/deploy`, { method: "POST" }),
},
```

- [ ] **Step 2: Typecheck**

Run: `cd dashboard && npm run typecheck`
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/lib/api.ts
git commit -m "feat(builder): builderSessions API client + types (W3)"
```

### Task D2: CodeArtifactPanel — file tree + diff viewer

**Files:**
- Create: `dashboard/src/components/agent-wizard/CodeArtifactPanel.tsx`
- Test: `dashboard/src/components/agent-wizard/CodeArtifactPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// CodeArtifactPanel.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { CodeArtifactPanel } from "./CodeArtifactPanel";

const files = { "agent.py": "print('hi')\n", "tools/search.py": "x = 1\n" };

describe("CodeArtifactPanel", () => {
  it("lists generated files", () => {
    render(<CodeArtifactPanel files={files} />);
    expect(screen.getByText("agent.py")).toBeInTheDocument();
    expect(screen.getByText("tools/search.py")).toBeInTheDocument();
  });

  it("shows file contents when a file is selected", () => {
    render(<CodeArtifactPanel files={files} />);
    fireEvent.click(screen.getByText("tools/search.py"));
    expect(screen.getByText(/x = 1/)).toBeInTheDocument();
  });

  it("renders an empty state when there are no files", () => {
    render(<CodeArtifactPanel files={{}} />);
    expect(screen.getByText(/No code generated yet/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/CodeArtifactPanel.test.tsx`
Expected: FAIL — cannot find `./CodeArtifactPanel`

- [ ] **Step 3: Write minimal implementation**

```tsx
// CodeArtifactPanel.tsx
import { useState } from "react";

interface CodeArtifactPanelProps {
  files: Record<string, string>;
}

export function CodeArtifactPanel({ files }: CodeArtifactPanelProps) {
  const paths = Object.keys(files).sort();
  const [selected, setSelected] = useState<string | null>(paths[0] ?? null);

  if (paths.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-500">
        No code generated yet. Use “Eject to code” to generate agent.py and tools.
      </div>
    );
  }

  const active = selected && files[selected] !== undefined ? selected : paths[0];

  return (
    <div className="flex h-full">
      <ul className="w-48 shrink-0 overflow-auto border-r border-gray-200 text-sm">
        {paths.map((p) => (
          <li key={p}>
            <button
              type="button"
              onClick={() => setSelected(p)}
              className={`block w-full truncate px-3 py-2 text-left hover:bg-gray-50 ${
                p === active ? "bg-gray-100 font-medium" : ""
              }`}
            >
              {p}
            </button>
          </li>
        ))}
      </ul>
      <pre className="flex-1 overflow-auto bg-gray-50 p-4 text-xs leading-relaxed">
        {files[active]}
      </pre>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/CodeArtifactPanel.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/agent-wizard/CodeArtifactPanel.tsx dashboard/src/components/agent-wizard/CodeArtifactPanel.test.tsx
git commit -m "feat(builder): CodeArtifactPanel file tree + viewer (W3)"
```

### Task D3: Wire eject + Code tab into ChatBuildPanel

**Files:**
- Modify: `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx`
- Modify: `dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx`

The W1/W2 `ChatBuildPanel` is single-pane (thread + inline `SpecReadyCard`). W3 adds: a session id (created lazily on first message via `api.builderSessions`), an artifact panel with tabs **Spec | Code | Deploy**, an "Eject to code" button on the validated spec card that calls `api.builderSessions.eject(...)` and accumulates `file_change` events into a `files` state feeding `CodeArtifactPanel`.

- [ ] **Step 1: Write the failing test** (add to `ChatBuildPanel.test.tsx` — mock `api.builderSessions.eject` to invoke its `onEvent` with a `file_change` then `complete`, assert the Code tab shows the file). Follow the existing mock structure in that 548-line file (key guard + `builders.chatStream` mock already present).

```tsx
// ChatBuildPanel.test.tsx (add within the existing describe)
it("eject-to-code streams a file into the Code tab", async () => {
  // ...arrange: render with a key present and a validated spec already shown
  // mock api.builderSessions.eject:
  vi.mocked(api.builderSessions.eject).mockImplementation(
    async (_id, _instr, onEvent) => {
      onEvent("file_change", { path: "agent.py", diff: "+print('hi')" });
      onEvent("complete", { summary: "done" });
    },
  );
  // act: click "Eject to code", then the "Code" tab
  fireEvent.click(await screen.findByRole("button", { name: /eject to code/i }));
  fireEvent.click(await screen.findByRole("tab", { name: /code/i }));
  // assert
  expect(await screen.findByText("agent.py")).toBeInTheDocument();
});
```

> Extend the existing `api` mock object in this file to include `builderSessions: { create, get, eject, deploy }` (vi.fn()). Mirror how `builders.chatStream` is already mocked.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/ChatBuildPanel.test.tsx -t "eject-to-code"`
Expected: FAIL — no "Eject to code" button / no Code tab

- [ ] **Step 3: Implement** — in `ChatBuildPanel.tsx`:
  1. Add state: `const [sessionId, setSessionId] = useState<string|null>(null)`, `const [files, setFiles] = useState<Record<string,string>>({})`, `const [artifactTab, setArtifactTab] = useState<"spec"|"code"|"deploy">("spec")`, `const [ejecting, setEjecting] = useState(false)`.
  2. Ensure a session exists before eject: a `ensureSession()` helper that calls `api.builderSessions.create(engine)` once and stores the id. (For W3, interview can still run via the existing `builders.chatStream`; only eject needs the session — minimal coupling. A later pass can migrate the interview onto `builderSessions.sendMessage`.)
  3. Add the artifact panel markup: a tab bar (`role="tablist"` with three `role="tab"` buttons Spec/Code/Deploy) and a body that renders the existing spec `<pre>` for "spec", `<CodeArtifactPanel files={files} />` for "code", and the existing deploy logs for "deploy".
  4. Add an "Eject to code" button inside `SpecReadyCard` (only when `valid`) that sets `ejecting`, calls `ensureSession()`, then `api.builderSessions.eject(id, instruction, (event, data) => {...})` accumulating `file_change` into `files` (keyed by path; reconstruct content by applying the write — since the backend sends only a diff, ALSO have the backend include the full content: update the `/eject` `file_change` frame to carry `content` alongside `diff`, and update `BuilderFileChange` + this handler to store `data.content`). On `complete`, `setEjecting(false)` and switch to the Code tab.

  > IMPORTANT contract fix: the W3 `file_change` SSE frame must include `content` (full file) in addition to `diff`, so the Code tab can render the file without replaying diffs. Update `run_eject` in `builder_session_service.py` to emit `{"path", "diff", "content"}` and `BuilderFileChange` accordingly. Add a backend test asserting `content` is present in the frame.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/ChatBuildPanel.test.tsx`
Expected: PASS (existing + new)

- [ ] **Step 5: Typecheck + lint + full FE suite**

Run: `cd dashboard && npm run typecheck && npm run lint && npx vitest run`
Expected: 0 type errors, 0 lint errors, all tests pass

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/agent-wizard/ChatBuildPanel.tsx dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx api/routes/builder_sessions.py api/services/builder_session_service.py tests/integration/test_builder_sessions.py
git commit -m "feat(builder): eject-to-code UI + Code artifact tab; file_change carries content (W3)"
```

---

## PART E — Cross-repo (cloud), docs, model-e2e, analytics

### Task E1: Cloud proxy passthrough for builder sessions

Per `feedback_cross_repo_sync` + CLAUDE.md §Cross-Repo Sync. The cloud repo currently only proxies `/builders/chat` (+recommend) via a buffered `_forward()`. Wave 3 adds streaming session endpoints. Cloud must passthrough `/cloud/builder/sessions/*` with `httpx.stream` (no event-name filtering) + per-turn / per-eject metering.

**Files (in `/Users/rajit/personal-github/agentbreeder-cloud`):**
- Modify: `agentbreeder-cloud/api/routes/builders.py` — add session passthrough routes
- Test: `agentbreeder-cloud/tests/...` matching its layout

- [ ] **Step 1:** Read `agentbreeder-cloud/api/routes/builders.py` to learn its `_forward()`, `TenantMiddleware`, and `QuotaCounter` usage.
- [ ] **Step 2:** Add an SSE passthrough helper (`_forward_stream(request, path)`) using `httpx.AsyncClient.stream`, relaying `event:`/`data:` frames verbatim, counting one metered unit per `/messages` and per `/eject` against `QuotaCounter` (and, for eject, sandbox-minutes — stub the minute counter to 0 until W4 cloud sandbox exists, with a `# TODO(W4)` note).
- [ ] **Step 3:** Add routes mirroring the six OSS endpoints under `/cloud/builder/sessions`.
- [ ] **Step 4:** Test the passthrough with a mocked upstream SSE response asserting frames are relayed unbuffered and quota incremented.
- [ ] **Step 5:** Commit in the cloud repo:

```bash
git -C /Users/rajit/personal-github/agentbreeder-cloud add -A
git -C /Users/rajit/personal-github/agentbreeder-cloud commit -m "feat(builder): proxy BuilderSession streaming + metering (OSS W3 parity)"
```

> This is a companion change in a separate repo — do NOT bundle into the OSS commit. Keep it on a matching branch in the cloud repo.

### Task E2: Docs sync

Per CLAUDE.md docs-sync table (new feature → `how-to.mdx`; the chat builder section already exists from W1/W2).

**Files:**
- Modify: `website/content/docs/how-to.mdx` — add an "Eject to code" subsection under the chat-builder flow: how to click eject, choose engine (Claude/Codex), what gets generated (`agent.py`/`tools/`/tests), that it runs in a local sandbox, and that cloud codegen needs `AGENTBREEDER_SANDBOX` (lands fully in cloud at W4).
- Modify: `website/content/docs/agent-yaml.mdx` — only if a field changed (none in W3; skip if so).
- Create (optional): `website/content/docs/conversational-builder.mdx` if the how-to section grows too large — otherwise keep it inline.

- [ ] **Step 1:** Read the existing chat-builder section in `how-to.mdx` (W2 added "Inline setup" after step 6).
- [ ] **Step 2:** Add the "Eject to code" subsection with a worked example and a note on the `AGENTBREEDER_SANDBOX` env gate.
- [ ] **Step 3:** Commit (same branch as the OSS code):

```bash
git add website/content/docs/how-to.mdx
git commit -m "docs(builder): document eject-to-code flow (W3)"
```

### Task E3: Model-e2e gated test (BYO key) + analytics events

Per spec §11 (one transcript per engine) and §12 (funnel events).

**Files:**
- Create: `tests/e2e/test_builder_eject_e2e.py` — gated behind an env flag + real key (skipped in CI by default), asserts: create session → seed a tiny valid `agent.yaml` → eject → at least one `file_change` with `agent.py` → snapshot contains `agent.py`. Mirror any existing gated model-e2e test (`model-e2e-test` skill / existing `tests/e2e`).
- Modify: frontend analytics hook — emit `eject_to_code_started`, `coding_agent_turn`, `deploy_started/succeeded/failed` where the existing W1/W2 funnel events are emitted (search for the existing `builder_session_started`/`spec_validated` emit sites; if analytics wiring doesn't exist yet, add a thin `track()` call alongside the new eject handler and note it).

- [ ] **Step 1:** Write the gated e2e test with `@pytest.mark.skipif(not os.environ.get("AGENTBREEDER_E2E_ANTHROPIC_KEY"), reason=...)`.
- [ ] **Step 2:** Run it locally with a real key once to confirm the loop genuinely writes a file end-to-end (manual verification — this is the real proof the provider-loop works against a live model).
- [ ] **Step 3:** Add the analytics emits.
- [ ] **Step 4:** Commit:

```bash
git add tests/e2e/test_builder_eject_e2e.py dashboard/src/...
git commit -m "test(builder): gated eject e2e + analytics funnel events (W3)"
```

### Task E4: Wave 3 gate + memory update

- [ ] **Step 1:** Run the full gate (per the `gate` skill): backend `pytest`, frontend `vitest` + `tsc` + `lint`, `ruff check . && ruff format --check .`, `mypy` on changed files, security scan, FE build. All green.
- [ ] **Step 2:** Update the epic memory file `project_conversational_builder_epic.md`: mark W3 done, list what shipped, note W4 (CloudSandbox + metering + analytics dashboard) as the remaining wave, and that the PR is still deferred until W4 per `feedback_defer_pr_until_done`.
- [ ] **Step 3:** Do NOT open the PR yet (epic not complete). Confirm the branch is committed and clean.

---

## Self-review notes (gaps the implementer must watch)

1. **Tool-message format round-trip (highest risk) — RESOLVED into Task B0.** Spiked 2026-06-14: confirmed neither `AnthropicProvider._build_payload` nor `OpenAIProvider._build_payload` translates the loop's `role="tool"` + assistant `tool_calls` messages into native tool_result/tool_use blocks. Task B0 adds that provider-layer translation with unit tests and runs **before** the loop (B2). Do not skip or reorder B0 — the whole provider-loop rests on it, and loop unit tests (FakeProvider) won't catch a regression here.
2. **Codex provider parity.** Part B's `CodexEngine` uses `OpenAIProvider`; ensure `_provider_for_engine` (Task C5) constructs the right provider + BYO-key secret for `engine="codex"`. Don't leave Codex as a Claude alias.
3. **Sandbox security.** `LocalSandbox` runs host commands; the `AGENTBREEDER_SANDBOX` gate (Task C5) is the only thing keeping cloud from running user code in-process. The gate check must be **before** the stream opens and must fail closed. Cloud deploy config must set `AGENTBREEDER_SANDBOX=cloud` (note this in E1).
4. **`file_change` frame carries `content`** (Task D3 contract fix) — make sure the backend emits it and a backend test asserts it, or the Code tab can't render files.
5. **Governance preserved.** `/deploy` (Task C6) must route through the real `DeployJobService` used by `/deploys` — RBAC, team check, audit, registry are non-negotiable (CLAUDE.md §2). No duplicated pipeline.
6. **Fixture names.** Integration tests assume `async_client` / `auth_headers` / `other_team_headers` / `fake_builder_key`. Before writing tests, open the integration conftest + `tests/integration/test_deploys.py` and use the real fixture names; don't invent.
7. **Migration number.** Confirm the next alembic revision id + correct `down_revision` (Task C1 Step 1) — don't hard-code `023` without checking.
