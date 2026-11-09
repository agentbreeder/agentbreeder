"""Registry tool loader: tool.yaml + src/ → ADK tools with explicit declarations.

Each tool artifact (`artifacts/tool/<name>/`) has a `tool.yaml`:

    functions:
      - name: lookup_orders             # what the model calls
        module: orders                  # src/orders.py
        function: lookup_orders         # def lookup_orders(rt, **args)
        description: ...
        parameters: {type: object, properties: {...}, required: [...]}   # JSON Schema
        writes_requests: false          # true: the function only files a request row

The declaration sent to the model comes from `parameters`, never from Python introspection. The
function is called as `fn(rt, **args)` in a worker thread, where `rt` is a ToolRuntime whose
`customer` is the server-side session user (never a model-supplied value). There is no execute
path for irreversible actions: a `writes_requests` tool can only insert a request that a person
decides elsewhere. Tools run in-process with the agent's privileges; that is a documented limit.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

FUNCTION_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
MODULE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Tool src dirs go on sys.path; a tool module must not shadow a runtime or stdlib module.
RESERVED = {"agent", "config", "memory", "rag", "server", "telemetry", "tools"} | set(
    sys.stdlib_module_names
)


async def run_to_completion(function, *args, **kwargs):
    """Run blocking work (a tool, a DB query) in a thread. If the turn is cancelled meanwhile,
    wait for the thread to finish before propagating the cancellation: a thread cannot be
    interrupted, so the turn must not report "stopped" while its write is still in flight."""
    work = asyncio.ensure_future(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        await asyncio.wait({work})
        raise


class ToolLoadError(RuntimeError):
    pass


@dataclass
class ToolRuntime:
    """What a tool function may use. `customer` is set by the runtime, never by the model."""

    customer: str
    session: str
    turn: str
    db: Any  # psycopg connection (autocommit) to the agent database
    memory: Any  # runtime Memory: add_note, open_ticket, notes
    config: dict
    services: dict
    trace: dict = field(default_factory=dict)

    def evidence(self, source: str, title: str, text: str, **extra) -> None:
        """Record what the reply may be grounded in (online evals read it)."""
        self.trace.setdefault("evidence", []).append(
            {"source": source, "title": title, "text": text, **extra}
        )

    def record(self, key: str, value: Any) -> None:
        """Append to a per-turn list returned in the response (e.g. tickets, web)."""
        self.trace.setdefault(key, []).append(value)


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    tool: str  # registry tool name
    version: str
    description: str
    parameters: dict
    writes_requests: bool
    call: Any


def load_functions(tools: list[dict], resolve_dir) -> list[FunctionSpec]:
    """Read every tool artifact's tool.yaml and import its functions from src/."""
    specs: list[FunctionSpec] = []
    seen: set[str] = set()
    for entry in tools:
        directory: Path = resolve_dir(entry["dir"])
        try:
            definition = yaml.safe_load((directory / "tool.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ToolLoadError(f"tool {entry['name']}: cannot read tool.yaml") from error
        functions = (definition or {}).get("functions") or []
        if not functions:
            raise ToolLoadError(f"tool {entry['name']}: tool.yaml declares no functions")
        source = str(directory / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
        for item in functions:
            name, module = item.get("name", ""), item.get("module", "")
            if not FUNCTION_NAME.fullmatch(name) or not MODULE_NAME.fullmatch(module):
                raise ToolLoadError(f"tool {entry['name']}: invalid function or module name")
            if module in RESERVED:
                raise ToolLoadError(f"tool {entry['name']}: module name {module!r} is reserved")
            if name in seen:
                raise ToolLoadError(f"function {name} is declared by two tools")
            seen.add(name)
            parameters = item.get("parameters") or {"type": "object", "properties": {}}
            if parameters.get("type") != "object":
                raise ToolLoadError(f"{name}: parameters must be a JSON Schema object")
            try:
                call = getattr(importlib.import_module(module), item.get("function") or name)
            except (ImportError, AttributeError) as error:
                raise ToolLoadError(f"{name}: cannot import {module}") from error
            specs.append(
                FunctionSpec(
                    name=name,
                    tool=entry["name"],
                    version=entry.get("version", ""),
                    description=str(item.get("description", "")).strip(),
                    parameters=parameters,
                    writes_requests=bool(item.get("writes_requests", False)),
                    call=call,
                )
            )
    return specs


class RegistryTool(BaseTool):
    """One registry function exposed to ADK with an explicit JSON-Schema declaration."""

    def __init__(self, spec: FunctionSpec, runtime_for):
        super().__init__(name=spec.name, description=spec.description)
        self.spec = spec
        self.runtime_for = runtime_for  # (tool_context) -> ToolRuntime

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.spec.name,
            description=self.spec.description,
            parameters_json_schema=self.spec.parameters,
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        allowed = set((self.spec.parameters.get("properties") or {}).keys())
        unknown = sorted(set(args) - allowed)
        if unknown:
            raise ValueError(f"unexpected arguments: {', '.join(unknown)}")
        missing = [k for k in self.spec.parameters.get("required") or [] if k not in args]
        if missing:
            raise ValueError(f"missing arguments: {', '.join(missing)}")
        runtime = self.runtime_for(tool_context)
        return await run_to_completion(self.spec.call, runtime, **args)
