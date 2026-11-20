"""Registry tools for any framework: tool.yaml + src/ → kit functions (spec §7.3).

Each tool artifact (`artifacts/tool/<name>/`) has a `tool.yaml`:

    functions:
      - name: lookup_orders             # what the model calls
        module: orders                  # src/orders.py
        function: lookup_orders         # def lookup_orders(rt, **args)
        description: ...
        parameters: {type: object, properties: {...}, required: [...]}   # JSON Schema
        writes_requests: false          # true: the function only files a request row

The loader is `runtime/adk@1.3.0`'s, without ADK. The declaration a framework gets comes from
`parameters`, never from Python introspection. A function is called as `fn(rt, **args)` in a
worker thread, where `rt` is a ToolRuntime whose `customer` is the server-side session user
(never a model-supplied value).

Every function a framework may call, registry tools and `search_knowledge_base` alike, is a
`KitFunction`. Calling one does, once for every framework: argument checks against the declared
schema, the call, its latency, one `tool:<name>` audit row (`ok: false` on failure), one TOOL span
and the reply's `tools` entry. A failure is returned to the model as `{"error": ...}`, as 1.3.0
does, never raised into the framework. A framework can call only what the kit gives it.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import telemetry
from .context import TurnContext, current, run_to_completion
from .redaction import redact

FUNCTION_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
MODULE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Tool src dirs go on sys.path; a tool module must not shadow the kit or a stdlib module. The
# runtime's own modules sit on the original sys.path, so `shadowed` finds them.
RESERVED = {"kit"} | set(sys.stdlib_module_names)
# sys.path before any tool's src/ is inserted: what a tool's top-level names must not shadow.
ORIGINAL_PATH = list(sys.path)
IMPORTABLE = tuple(importlib.machinery.all_suffixes())
DUNDER = re.compile(r"__\w*__")

# Never copied from what a tool recorded into its audit row: trace lists the runtime owns and
# row fields only the runtime sets.
RUNTIME_KEYS = frozenset(
    {"tools", "evidence", "sources", "mcp_unavailable"}
    | {"args", "agent", "tool", "mcp", "latency_ms", "ok", "error", "writes_request"}
)
MAX_ERROR = 300


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
    # What this one call recorded: its own audit row's additions, kept apart from what a tool
    # running at the same time recorded (as runtime/adk@1.5.0).
    added: dict = field(default_factory=dict)

    def evidence(self, source: str, title: str, text: str, **extra) -> None:
        """Record what the reply may be grounded in (online evals read it)."""
        self.trace.setdefault("evidence", []).append(
            {"source": source, "title": title, "text": text, **extra}
        )

    def record(self, key: str, value: Any) -> None:
        """Append to a per-turn list returned in the response (e.g. tickets, web)."""
        self.trace.setdefault(key, []).append(value)
        self.added.setdefault(key, []).append(value)


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    tool: str  # registry tool name
    version: str
    description: str
    parameters: dict
    writes_requests: bool
    call: Any


def top_level_names(source: Path) -> set[str]:
    """What `import NAME` could find in a tool's src/: module files and directories."""
    names: set[str] = set()
    try:
        entries = list(source.iterdir())
    except FileNotFoundError:
        return names
    except OSError as error:  # src is a file, unreadable, ...
        raise ToolLoadError(f"cannot read {source.name}/: {type(error).__name__}") from error
    for entry in entries:
        if entry.name == "__pycache__":
            continue
        if entry.is_dir():
            names.add(entry.name)
        elif entry.name.endswith(IMPORTABLE):
            names.add(entry.name.split(".", 1)[0])
    return names


def shadowed(name: str) -> bool:
    """Whether a top-level name would hide a module the runtime, its packages or the standard
    library provide (found on the original sys.path, outside every tool's src/)."""
    if DUNDER.fullmatch(name) or name in RESERVED or name in sys.builtin_module_names:
        return True
    try:
        return importlib.machinery.PathFinder.find_spec(name, ORIGINAL_PATH) is not None
    except (ImportError, ValueError):
        return True


def check_shadowing(tool: str, source: Path, owners: dict[str, str]) -> None:
    """Refuse a tool whose src/ would shadow an installed module or another tool's module, before
    its src/ goes first on sys.path."""
    for name in sorted(top_level_names(source)):
        if name in owners:
            raise ToolLoadError(f"tool {tool}: src/{name} is also in tool {owners[name]}")
        if shadowed(name):
            raise ToolLoadError(f"tool {tool}: src/{name} shadows an installed module")
        owners[name] = tool


def load_functions(tools: list[dict], resolve_dir) -> list[FunctionSpec]:
    """Read every tool artifact's tool.yaml and import its functions from src/."""
    specs: list[FunctionSpec] = []
    seen: set[str] = set()
    owners: dict[str, str] = {}  # top-level module name -> tool
    for entry in tools:
        directory: Path = resolve_dir(entry["dir"])
        try:
            definition = yaml.safe_load((directory / "tool.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ToolLoadError(f"tool {entry['name']}: cannot read tool.yaml") from error
        functions = (definition or {}).get("functions") if isinstance(definition, dict) else None
        if not functions or not isinstance(functions, list):
            raise ToolLoadError(f"tool {entry['name']}: tool.yaml declares no functions")
        check_shadowing(entry["name"], directory / "src", owners)
        source = str(directory / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
        for item in functions:
            if not isinstance(item, dict):
                raise ToolLoadError(f"tool {entry['name']}: invalid function entry")
            name, module = item.get("name", ""), item.get("module", "")
            if not (isinstance(name, str) and FUNCTION_NAME.fullmatch(name)) or not (
                isinstance(module, str) and MODULE_NAME.fullmatch(module)
            ):
                raise ToolLoadError(f"tool {entry['name']}: invalid function or module name")
            if module in RESERVED:
                raise ToolLoadError(f"tool {entry['name']}: module name {module!r} is reserved")
            if importlib.machinery.PathFinder.find_spec(module, [source]) is None:
                # Not an installed package's module: only the tool's own src/ is imported.
                raise ToolLoadError(f"tool {entry['name']}: module {module!r} is not in its src/")
            if name in seen:
                raise ToolLoadError(f"function {name} is declared by two tools")
            seen.add(name)
            parameters = item.get("parameters") or {"type": "object", "properties": {}}
            if not isinstance(parameters, dict) or parameters.get("type") != "object":
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


def check_arguments(parameters: dict, args: dict) -> None:
    """1.3.0's check: no argument the schema does not declare, every required one present."""
    allowed = set((parameters.get("properties") or {}).keys())
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ValueError(f"unexpected arguments: {', '.join(unknown)}")
    missing = [k for k in parameters.get("required") or [] if k not in args]
    if missing:
        raise ValueError(f"missing arguments: {', '.join(missing)}")


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


# (turn, args, added) -> result; `added` collects what the call recorded for its audit row.
Invoke = Callable[[TurnContext, dict, dict], Awaitable[Any]]


@dataclass(frozen=True)
class KitFunction:
    """One function a framework may call, with its declared JSON Schema."""

    name: str
    description: str
    parameters: dict
    invoke: Invoke
    tool_ref: str | None = None  # tool/<name>@<version> for a registry tool
    writes_requests: bool = False

    async def __call__(self, **args: Any) -> str:
        return await call(self, args)


def registry_function(spec: FunctionSpec) -> KitFunction:
    async def invoke(turn: TurnContext, args: dict, added: dict) -> Any:
        runtime = ToolRuntime(
            customer=turn.customer,
            session=turn.session,
            turn=turn.id,
            db=turn.memory.db,
            memory=turn.memory,
            config=turn.config,
            services=turn.services,
            trace=turn.trace,
            added=added,
        )
        return await run_to_completion(spec.call, runtime, **args)

    return KitFunction(
        name=spec.name,
        description=spec.description,
        parameters=spec.parameters,
        invoke=invoke,
        tool_ref=f"tool/{spec.tool}@{spec.version}",
        writes_requests=spec.writes_requests,
    )


async def _audit(turn: TurnContext, action: str, detail: dict) -> None:
    await run_to_completion(
        turn.memory.audit, turn.id, turn.session, turn.customer, "agent", action, detail
    )


async def call(function: KitFunction, args: Any) -> str:
    """Run one call for the running turn: checks, the call, its audit row, its TOOL span and
    its `tools` entry. Returns the text the framework gives the model."""
    turn = current()
    if not isinstance(args, dict):
        args = {}
    turn.trace["tools"].append(function.name)
    started = time.monotonic()
    added: dict = {}
    with telemetry.span(
        function.name,
        "TOOL",
        input=args,
        **{"tool.name": function.name, "tool.parameters": json.dumps(args, default=str)},
    ) as current_span:
        detail: dict = {"args": args, "agent": turn.agent}
        if function.tool_ref is not None:
            detail["tool"] = function.tool_ref
        try:
            check_arguments(function.parameters, args)
            result = await function.invoke(turn, args, added)
        except Exception as error:  # noqa: BLE001 - returned to the model, as 1.3.0 does
            message = redact(str(error))[:MAX_ERROR]
            current_span.set_attribute("tool.error", message)
            detail["latency_ms"] = int((time.monotonic() - started) * 1000)
            detail.update(ok=False, error=message)
            await _audit(turn, f"tool:{function.name}", detail)
            failed = {"error": message}
            telemetry.set_output(current_span, failed)
            return as_text(failed)
        text = as_text(result)
        telemetry.set_output(current_span, text)
        detail["latency_ms"] = int((time.monotonic() - started) * 1000)
        recorded = {key: value for key, value in added.items() if key not in RUNTIME_KEYS}
        row = {**recorded, **detail, "ok": True, "error": None}
        if function.writes_requests:
            row["writes_request"] = True
        await _audit(turn, f"tool:{function.name}", row)
        return text
