# Contributing a Tool to AgentBreeder

This is the contributor guide for adding a **first-party tool** to the AgentBreeder standard library. First-party tools live under `engine/tools/standard/` and are resolved via `tools/<kebab-name>` references in `agent.yaml`.

For the higher-level architecture of how tools are resolved at deploy time, see [`engine/tool_resolver.py`](../engine/tool_resolver.py).

> **Where do user-defined tools go?**
> Users put project-local overrides in `./tools/<snake_name>.py` in their agent project root. Local overrides take precedence over the standard library. Everything in this guide also applies to user-defined tools — the contract is the same.

---

## Tool Anatomy

Every tool module exports **two things at module top level**:

1. A **callable** with the same name as the file (snake_case).
2. A `SCHEMA` dict describing the parameters in JSON-Schema (OpenAPI subset).

```python
# engine/tools/standard/my_tool.py
"""One-line summary of what the tool does and when to use it.

Required env: ``MY_TOOL_API_KEY``.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict


class MyToolResult(TypedDict):
    """Structured output. TypedDict is structurally compatible with dict[str, Any]."""

    value: str
    count: int


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What to look up. Be specific.",
        },
        "limit": {
            "type": "integer",
            "description": "Max items to return (1-50).",
            "default": 10,
            "minimum": 1,
            "maximum": 50,
        },
    },
    "required": ["query"],
}


def my_tool(query: str, limit: int = 10) -> MyToolResult:
    """One-line summary. Used as the tool description by frameworks like ADK.

    Args:
        query: What to look up.
        limit: Max items to return (1-50).

    Returns:
        A ``MyToolResult`` with the lookup value and count.

    Raises:
        RuntimeError: When the required env var is not set.
    """
    api_key = os.getenv("MY_TOOL_API_KEY")
    if not api_key:
        raise RuntimeError("MY_TOOL_API_KEY is not set.")
    # ... do the work ...
    return MyToolResult(value="hello", count=1)
```

### Naming Rules

| Where | Style | Example |
|---|---|---|
| File name | `snake_case.py` | `web_search.py` |
| Function name | matches file name | `web_search` |
| `agent.yaml` ref | `tools/<kebab-case>` | `tools/web-search` |

Kebab-case in the ref maps to snake_case for the Python module/function (handled automatically by [`engine/tool_resolver.py`](../engine/tool_resolver.py)).

### Module Exposure

Do **not** re-export functions from `engine/tools/standard/__init__.py`. Re-exporting shadows the submodules of the same name (Python import quirk) and breaks `import engine.tools.standard.web_search as ws_mod` access that registry-seeding scripts use to read the `SCHEMA`. See the note in [`engine/tools/standard/__init__.py`](../engine/tools/standard/__init__.py).

---

## `SCHEMA` Format

`SCHEMA` is a JSON-Schema dict (OpenAPI subset). It is stored in the registry so the dashboard, visual builders, and agent eval framework can render a typed editor for the tool's inputs.

Minimal requirements:

- Top-level `type` must be `"object"`.
- `properties` is a dict of parameter name → field schema.
- `required` is a list of property names that must be present.
- Each property should declare `type` and `description`.
- Use `default`, `minimum`, `maximum`, `enum` where meaningful — they show up in the UI.

### Supported field types

| JSON-Schema `type` | Python type | Example |
|---|---|---|
| `"string"` | `str` | `{"type": "string"}` |
| `"integer"` | `int` | `{"type": "integer", "minimum": 1}` |
| `"number"` | `float` | `{"type": "number"}` |
| `"boolean"` | `bool` | `{"type": "boolean", "default": false}` |
| `"array"` | `list` | `{"type": "array", "items": {"type": "string"}}` |
| `"object"` | `dict` | `{"type": "object", "properties": {...}}` |

### Example — web_search SCHEMA

```python
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query. Be specific.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of sources to return (1-10).",
            "default": 6,
            "minimum": 1,
            "maximum": 10,
        },
        "search_depth": {
            "type": "string",
            "description": "'basic' (fast) or 'advanced' (more thorough, slower).",
            "enum": ["basic", "advanced"],
            "default": "advanced",
        },
    },
    "required": ["query"],
}
```

The resolver enforces this schema at call time via [`validate_tool_input()`](../engine/tool_resolver.py) — passing in unknown or wrong-typed arguments raises `ToolInputValidationError` before your function runs.

---

## Input / Output Contract

### Inputs

- Function arguments must be **JSON-serializable primitives**: `str`, `int`, `float`, `bool`, `list`, `dict`, or `None`.
- Every keyword argument must appear in `SCHEMA.properties`.
- Defaults declared in the Python signature should match `SCHEMA.default`.
- The resolver validates input against `SCHEMA` before calling your function — you do not need to re-validate.

### Outputs

- Return a `TypedDict` (preferred) or plain `dict[str, Any]`.
- `TypedDict` is **structurally compatible** with `dict[str, Any]`, so adding one is backward-compatible.
- All values in the returned dict must be JSON-serializable — they get serialized when crossing the sandbox or A2A boundary.
- Avoid returning model objects, file handles, or anything with state.

### Errors

- Raise `RuntimeError` for missing config / env vars (e.g. an unset API key).
- Raise `ValueError` for invalid user input that the schema couldn't catch (e.g. a `subdir` containing path traversal — see `engine/util/path_safety.safe_relative_subdir`).
- Let `httpx.HTTPError` and other transport errors propagate — the runtime layer surfaces them in traces.
- **Do not** swallow exceptions or return error sentinels like `{"error": "..."}` — the sidecar and audit log rely on raised exceptions to mark a tool call as failed.

---

## Environment-Variable Conventions

| Convention | Example | Notes |
|---|---|---|
| `<SERVICE>_API_KEY` | `TAVILY_API_KEY`, `OPENAI_API_KEY` | One env var per external service. |
| `<TOOL>_OUTPUT_DIR` | `DOCUMENT_OUTPUT_DIR` | Default to a relative `./output` path; never default to an absolute path. |
| `<TOOL>_TIMEOUT_SECONDS` | `WEB_SEARCH_TIMEOUT_SECONDS` | Optional; document the default in the docstring. |

Read env vars **inside** the tool function — not at module import time — so tests can monkeypatch them and so the deploy pipeline can inject them at runtime.

```python
# Good — read at call time
def web_search(query: str) -> WebSearchResult:
    api_key = os.getenv("TAVILY_API_KEY")
    ...

# Bad — frozen at import time, breaks tests
API_KEY = os.getenv("TAVILY_API_KEY")
def web_search(query: str) -> WebSearchResult:
    ...
```

Secrets must be referenced in `agent.yaml` under `deploy.secrets:` so the deploy pipeline can fetch them from the configured secrets backend (AWS Secrets Manager, GCP Secret Manager, Vault, or `.env`).

---

## Testing Patterns

Unit tests live in [`tests/unit/test_standard_tools.py`](../tests/unit/test_standard_tools.py). One test class or block per tool.

### Fixture — point env vars at tmp dirs

```python
import pytest
from pathlib import Path
from engine.tools.standard.markdown_writer import markdown_writer


@pytest.fixture
def tmp_output_dir(tmp_path, monkeypatch):
    """Point DOCUMENT_OUTPUT_DIR at an isolated tmp dir."""
    monkeypatch.setenv("DOCUMENT_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def test_markdown_writer_writes_into_base_dir(tmp_output_dir: Path) -> None:
    result = markdown_writer(title="My Note", content="# hi")
    assert Path(result["path"]).is_file()
    assert Path(result["path"]).is_relative_to(tmp_output_dir.resolve())
    assert result["byte_size"] > 0
```

### What to test

1. **Happy path** — valid input produces the expected output shape.
2. **Edge cases** — empty input, max-size input, optional args omitted.
3. **Error paths** — missing env var raises `RuntimeError`; invalid input raises `ValueError`.
4. **Security** — for any tool that touches the filesystem or shell, test that path traversal / null bytes / absolute paths are rejected.
5. **No network calls in unit tests** — use `monkeypatch` to replace `httpx.Client` / `httpx.AsyncClient`, or use `respx` to mock HTTP. Tests must run offline.

### Integration tests

End-to-end tests covering the resolver + sandbox pipeline live in [`tests/integration/test_tool_execution.py`](../tests/integration/test_tool_execution.py). Add a case there when your tool needs to verify behaviour across module boundaries.

---

## Before Opening a PR

- [ ] Module file lives at `engine/tools/standard/<snake_name>.py`.
- [ ] Function name matches the file name.
- [ ] `SCHEMA: dict[str, Any]` is defined at module top level.
- [ ] Function has type-annotated parameters and a `TypedDict` (or `dict[str, Any]`) return type.
- [ ] Function has a docstring with `Args:`, `Returns:`, and `Raises:` sections.
- [ ] All env vars are read inside the function, not at import time.
- [ ] Env vars follow the `<SERVICE>_API_KEY` / `<TOOL>_OUTPUT_DIR` conventions.
- [ ] Unit tests added to `tests/unit/test_standard_tools.py` — happy path, edge cases, errors, security.
- [ ] Tests run offline (no real network or filesystem access outside `tmp_path`).
- [ ] Tool listed in [`engine/tools/standard/__init__.py`](../engine/tools/standard/__init__.py) module docstring.
- [ ] Nothing re-exported from `__init__.py` (would shadow submodules).
- [ ] If the tool needs new env vars, documented in `.env.example` and the relevant `website/content/docs/` page.
- [ ] If the tool is generally useful, mention it in `website/content/docs/agent-yaml.mdx` or a new docs page.
- [ ] `pytest tests/unit/test_standard_tools.py -q` passes.
- [ ] `ruff check engine/tools/` and `ruff format engine/tools/` are clean.
- [ ] `mypy engine/tools/standard/<snake_name>.py` is clean (or any new mypy errors are documented).

---

## Reference

- [`engine/tool_resolver.py`](../engine/tool_resolver.py) — resolution order (local override → standard library → registry API) and input validation.
- [`engine/tools/standard/web_search.py`](../engine/tools/standard/web_search.py) — reference tool with HTTP I/O.
- [`engine/tools/standard/markdown_writer.py`](../engine/tools/standard/markdown_writer.py) — reference tool with filesystem I/O.
- [`engine/util/path_safety.py`](../engine/util/path_safety.py) — `safe_relative_subdir()` for tools that take user-supplied paths.
- [`api/services/sandbox_service.py`](../api/services/sandbox_service.py) — how tool code is executed at runtime (Docker isolation + subprocess fallback).
