"""Agent Garden MCP helper — build MCP tool servers with minimal boilerplate.

Provides a decorator-based API for exposing Python functions as MCP tools.
Type hints are automatically converted to JSON Schema input definitions.

Usage:
    from agenthub.mcp import serve

    @serve.tool()
    def calculate(expression: str) -> str:
        \"\"\"Evaluate a math expression.\"\"\"
        ...

    @serve.tool()
    def greet(name: str, excited: bool = False) -> str:
        \"\"\"Say hello.\"\"\"
        suffix = "!" if excited else "."
        return f"Hello, {name}{suffix}"

    if __name__ == "__main__":
        serve.run()
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any, get_type_hints

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type-hint to JSON Schema mapping
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _python_type_to_json_schema(py_type: type) -> str:
    """Convert a Python type annotation to a JSON Schema type string."""
    return _TYPE_MAP.get(py_type, "string")


def _build_input_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Auto-generate a JSON Schema object from a function's type hints.

    Inspects the function signature and type annotations to produce a
    schema compatible with the MCP tool input_schema format.
    """
    hints = get_type_hints(func)
    sig = inspect.signature(func)

    properties: dict[str, dict[str, str]] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name == "return":
            continue
        py_type = hints.get(param_name, str)
        json_type = _python_type_to_json_schema(py_type)
        properties[param_name] = {"type": json_type}

        # Parameters without defaults are required
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# MCPServe — the decorator-based tool registry
# ---------------------------------------------------------------------------


class MCPServe:
    """A lightweight wrapper around FastMCP for decorator-based tool registration.

    Collects tool functions via ``@serve.tool()`` and runs them as a
    stdio MCP server with ``serve.run()``.

    The JSON Schema for each tool's inputs is derived automatically from
    the function's type hints. The tool description comes from the docstring.
    """

    def __init__(self, name: str = "agent-garden-tools", version: str = "1.0.0") -> None:
        self._server = FastMCP(name=name, version=version)
        self._tools: list[str] = []

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a function as an MCP tool.

        The function's name becomes the tool name, its docstring becomes
        the tool description, and its type hints define the input schema.

        Returns:
            A decorator that registers the function and returns it unchanged.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            # Build schema for logging/inspection; FastMCP also infers it,
            # but we compute it here so we can log what was registered.
            schema = _build_input_schema(func)
            logger.debug(
                "Registering MCP tool '%s' with schema: %s",
                func.__name__,
                schema,
            )

            # Register with the underlying FastMCP server
            self._server.tool()(func)
            self._tools.append(func.__name__)
            return func

        return decorator

    @property
    def tool_names(self) -> list[str]:
        """List of registered tool names (in registration order)."""
        return list(self._tools)

    def run(self) -> None:
        """Start the MCP server over stdio.

        This blocks until the client disconnects or the process is terminated.
        """
        logger.info(
            "Starting MCP server with %d tool(s): %s",
            len(self._tools),
            ", ".join(self._tools),
        )
        self._server.run()


# ---------------------------------------------------------------------------
# Module-level singleton — import and use directly
# ---------------------------------------------------------------------------

serve = MCPServe()
