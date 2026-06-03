"""AgentBreeder MCP helper — build MCP tool servers with minimal boilerplate.

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

    def __init__(self, name: str = "agentbreeder-tools") -> None:
        self._server = FastMCP(name=name)
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


# ---------------------------------------------------------------------------
# Client side — load tools from MCP servers the deployer wired in
# ---------------------------------------------------------------------------

# Env var the deployer populates with the resolved MCP forwarding map:
# {"<name>": {"transport": "...", "url": "http://localhost:<port>"}}
MCP_SERVERS_ENV = "AGENTBREEDER_MCP_SERVERS"


def load_mcp_tools(*, attempts: int = 8, delay: float = 3.0) -> list[Any]:
    """Load LangChain tools from the MCP servers attached to this deployment.

    Reads the ``AGENTBREEDER_MCP_SERVERS`` env var (injected by the deployer
    when an agent declares ``mcp_servers``) and returns the servers' tools as
    LangChain ``BaseTool`` objects, ready to hand to ``create_react_agent`` or
    ``model.bind_tools(...)``. This is the supported way for a Full Code
    LangGraph agent to consume co-deployed MCP tools — no hand-rolled client.

    Resilient by design: returns ``[]`` when no servers are configured, and
    retries while a slow-starting MCP sidecar container comes up. ``langchain-
    mcp-adapters`` is imported lazily so the SDK has no hard dependency on it.

    Example::

        from agenthub.mcp import load_mcp_tools
        from langgraph.prebuilt import create_react_agent

        graph = create_react_agent(model, load_mcp_tools())
    """
    import json
    import os

    raw = os.environ.get(MCP_SERVERS_ENV, "").strip()
    if not raw:
        return []
    try:
        servers = json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid %s: %s — no MCP tools loaded", MCP_SERVERS_ENV, exc)
        return []

    connections = {
        name: {
            "url": cfg["url"],
            "transport": cfg.get("transport", "streamable_http"),
        }
        for name, cfg in servers.items()
        if isinstance(cfg, dict) and cfg.get("url")
    }
    if not connections:
        return []

    return _load_tools_blocking(connections, attempts=attempts, delay=delay)


def _load_tools_blocking(
    connections: dict[str, dict[str, str]], *, attempts: int, delay: float
) -> list[Any]:
    """Drive the async MCP client to completion, with startup retries.

    Works whether or not an event loop is already running (the agent module is
    imported from within the server's loop), running in a worker thread when one
    is active so a bare ``asyncio.run`` can't fail.
    """
    import asyncio
    import time

    async def _fetch() -> list[Any]:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        return await MultiServerMCPClient(connections).get_tools()

    def _drive() -> list[Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_fetch())
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_fetch())).result()

    last_err: Exception | None = None
    for i in range(attempts):
        try:
            tools = _drive()
            logger.info("Loaded %d MCP tool(s) from %s", len(tools), ", ".join(connections))
            return tools
        except Exception as exc:  # noqa: BLE001 — startup race with MCP container
            last_err = exc
            logger.warning("MCP tools not ready (attempt %d/%d): %s", i + 1, attempts, exc)
            time.sleep(delay)
    logger.error("Giving up loading MCP tools: %s — running without them", last_err)
    return []
