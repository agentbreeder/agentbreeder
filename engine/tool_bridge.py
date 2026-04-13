"""AgentBreeder tool bridge -- converts ToolRef list to framework-native tool objects.

Each adapter reads TOOL_ENDPOINT_<SLUG> environment variables to locate tool HTTP
endpoints. The slug is derived from the ToolRef ref or name:
  tools/zendesk-mcp  ->  TOOL_ENDPOINT_TOOLS_ZENDESK_MCP
  order-lookup       ->  TOOL_ENDPOINT_ORDER_LOOKUP
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

# Env var prefix used for all tool endpoint URLs.
_ENV_PREFIX = "TOOL_ENDPOINT_"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ref_to_slug(ref_or_name: str) -> str:
    """Convert a tool ref or name to the env-var suffix used for endpoint lookup.

    Examples::

        tools/zendesk-mcp  ->  TOOLS_ZENDESK_MCP
        order-lookup        ->  ORDER_LOOKUP
        search              ->  SEARCH
    """
    slug = re.sub(r"[^a-zA-Z0-9]", "_", ref_or_name)
    return slug.upper()


def _resolve_endpoint(tool_ref: Any) -> str | None:
    """Return the HTTP endpoint URL for *tool_ref*, or None if not configured.

    Precedence: ref field first (full slug, then last path component), then name field.
    """
    ref = tool_ref.ref
    name = tool_ref.name

    # Try ref full slug first, then last path component, then name.
    candidates: list[str] = []
    if ref is not None:
        candidates.append(ref)
        last = ref.split("/")[-1]
        if last != ref:
            candidates.append(last)
    if name is not None:
        candidates.append(name)

    for key in candidates:
        env_key = _ENV_PREFIX + _ref_to_slug(key)
        val = os.environ.get(env_key)
        if val is not None:
            return val
    return None


# ---------------------------------------------------------------------------
# Claude adapter
# ---------------------------------------------------------------------------


def to_claude_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of ToolRef objects to Anthropic ToolParam dicts.

    Each dict has the shape::

        {
            "name": str,
            "description": str,
            "input_schema": {
                "type": "object",
                "properties": {...},
                "required": [...],
            }
        }

    Tools without a resolvable name are skipped with a warning.

    Args:
        tools: List of ToolRef instances from AgentConfig.tools.

    Returns:
        List of anthropic.types.ToolParam-compatible dicts.
    """
    result: list[dict[str, Any]] = []
    for tool_ref in tools:
        # Determine a usable name: prefer explicit name, fall back to the
        # last path component of the ref (e.g. "tools/zendesk-mcp" -> "zendesk-mcp").
        name: str | None = tool_ref.name
        if name is None and tool_ref.ref is not None:
            name = tool_ref.ref.split("/")[-1]
        if name is None:
            logger.warning("Skipping ToolRef with no name or ref: %r", tool_ref)
            continue

        # Sanitise name to match Anthropic's ^[a-zA-Z0-9_-]{1,64}$ constraint.
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]

        description: str = tool_ref.description or f"Tool: {safe_name}"

        # Build input_schema from the inline schema dict, or fall back to an
        # empty object schema so the ToolParam is still valid.
        raw_schema: dict[str, Any] = tool_ref.schema_ or {}
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": raw_schema.get("properties", {}),
        }
        if "required" in raw_schema:
            input_schema["required"] = raw_schema["required"]

        result.append(
            {
                "name": safe_name,
                "description": description,
                "input_schema": input_schema,
            }
        )
        logger.debug(
            "Registered Claude tool %r from ToolRef ref=%r name=%r",
            safe_name,
            tool_ref.ref,
            tool_ref.name,
        )

    return result


# ---------------------------------------------------------------------------
# CrewAI adapter
# ---------------------------------------------------------------------------


def to_crewai_tools(tools: list[Any]) -> list[Any]:
    """Convert a list of ToolRef objects to crewai BaseTool subclass instances.

    Each tool exposes a synchronous _run() that POSTs to the tool's HTTP
    endpoint (resolved from the TOOL_ENDPOINT_* env var) and returns the
    response text.  Tools whose endpoint URL cannot be resolved are skipped
    with a warning.

    Args:
        tools: List of ToolRef instances from AgentConfig.tools.

    Returns:
        List of crewai.tools.BaseTool instances.
    """
    try:
        from crewai.tools import BaseTool as CrewBaseTool  # type: ignore[import-untyped]
        from pydantic import BaseModel as PydanticBaseModel
    except ImportError:
        logger.warning(
            "crewai is not installed -- to_crewai_tools() returns empty list. "
            "Add crewai to your requirements to enable tool injection."
        )
        return []

    result: list[Any] = []

    for tool_ref in tools:
        endpoint = _resolve_endpoint(tool_ref)
        if endpoint is None:
            identifier = tool_ref.ref or tool_ref.name or "<unnamed>"
            logger.warning(
                "No endpoint env var found for tool %r -- skipping. "
                "Set TOOL_ENDPOINT_%s to enable this tool.",
                identifier,
                _ref_to_slug(identifier),
            )
            continue

        raw_name: str | None = tool_ref.name
        if raw_name is None and tool_ref.ref is not None:
            raw_name = tool_ref.ref.split("/")[-1]
        if raw_name is None:
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_name)
        description: str = tool_ref.description or f"Tool: {safe_name}"
        tool_endpoint: str = endpoint

        raw_schema: dict[str, Any] = tool_ref.schema_ or {}
        props: dict[str, Any] = raw_schema.get("properties", {})

        # Dynamically create the pydantic ArgsSchema class.
        if props:
            ArgsSchema = type(  # noqa: N806
                f"{safe_name}Args",
                (PydanticBaseModel,),
                {"__annotations__": {k: str for k in props}},
            )
        else:
            ArgsSchema = type(  # noqa: N806
                f"{safe_name}Args",
                (PydanticBaseModel,),
                {"__annotations__": {"input": str}},
            )

        def _make_run(ep: str, sname: str) -> Any:
            """Factory closes over ep and sname to avoid late-binding."""

            def _run(self: Any, **kwargs: Any) -> str:
                payload = kwargs if kwargs else {"input": getattr(self, "input", "")}
                try:
                    response = httpx.post(ep, json=payload, timeout=30.0)
                    response.raise_for_status()
                    data = response.json()
                    if isinstance(data, dict):
                        return str(data.get("output", data))
                    return str(data)
                except httpx.HTTPError as exc:
                    logger.error("Tool %r HTTP error: %s", sname, exc)
                    return f"Error calling tool {sname!r}: {exc}"

            return _run

        ToolClass = type(  # noqa: N806
            safe_name,
            (CrewBaseTool,),
            {
                "name": safe_name,
                "description": description,
                "args_schema": ArgsSchema,
                "_run": _make_run(tool_endpoint, safe_name),
            },
        )
        result.append(ToolClass())
        logger.debug("Registered CrewAI tool %r -> endpoint %r", safe_name, tool_endpoint)

    return result


# ---------------------------------------------------------------------------
# Google ADK adapter
# ---------------------------------------------------------------------------


def to_adk_tools(tools: list[Any]) -> list[Callable[..., Any]]:
    """Convert a list of ToolRef objects to Google ADK-compatible callables.

    ADK treats any Python callable with type-annotated parameters and a
    docstring as a tool.  For each ToolRef we dynamically construct an async
    function whose:

    - name is the sanitised tool name (valid Python identifier)
    - docstring is the ToolRef description
    - parameters are derived from the ToolRef schema properties (all typed as
      str; ADK performs its own JSON Schema generation from annotations)
    - body makes an async HTTP POST to the TOOL_ENDPOINT_* endpoint

    The function body is built with exec() so that its __annotations__ reflect
    real parameter names (inspect.signature() must return the correct params
    for ADK's schema introspection to work).  All inputs to exec() are
    internally generated and sanitised -- no user data ever enters the string.

    Tools without a resolvable endpoint are skipped with a warning.

    Args:
        tools: List of ToolRef instances from AgentConfig.tools.

    Returns:
        List of async callables compatible with google.adk.agents.Agent(tools=...).
    """
    result: list[Callable[..., Any]] = []

    for tool_ref in tools:
        endpoint = _resolve_endpoint(tool_ref)
        if endpoint is None:
            identifier = tool_ref.ref or tool_ref.name or "<unnamed>"
            logger.warning(
                "No endpoint env var found for tool %r -- skipping. "
                "Set TOOL_ENDPOINT_%s to enable this tool.",
                identifier,
                _ref_to_slug(identifier),
            )
            continue

        raw_name: str | None = tool_ref.name
        if raw_name is None and tool_ref.ref is not None:
            raw_name = tool_ref.ref.split("/")[-1]
        if raw_name is None:
            continue

        # ADK function names must be valid Python identifiers.
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_name).lstrip("_") or "tool"
        description: str = tool_ref.description or f"Tool: {safe_name}"
        tool_endpoint: str = endpoint

        raw_schema: dict[str, Any] = tool_ref.schema_ or {}
        props: dict[str, Any] = raw_schema.get("properties", {})
        required: list[str] = raw_schema.get("required", [])

        # Build parameter list -- required params have no default, optional
        # params default to "".  All are typed as str.
        param_parts: list[str] = []
        for prop_name in props:
            safe_prop = re.sub(r"[^a-zA-Z0-9_]", "_", prop_name)
            if prop_name in required:
                param_parts.append(f"{safe_prop}: str")
            else:
                param_parts.append(f'{safe_prop}: str = ""')

        if not param_parts:
            param_parts = ['input: str = ""']

        # Extract just the parameter name (before the colon) for the payload dict.
        param_names = [p.split(":")[0].strip() for p in param_parts]
        params_str = ", ".join(param_parts)
        payload_str = "{" + ", ".join(f"{repr(n)}: {n}" for n in param_names) + "}"

        # Build the error message string separately to avoid quoting issues inside exec'd code.
        err_msg_expr = '"Error calling tool ' + repr(safe_name) + ': " + str(exc)'
        func_code = (
            f"async def {safe_name}({params_str}) -> str:\n"
            f"    import httpx as _httpx\n"
            f"    payload = {payload_str}\n"
            f"    async with _httpx.AsyncClient(timeout=30.0) as client:\n"
            f"        try:\n"
            f"            resp = await client.post({tool_endpoint!r}, json=payload)\n"
            f"            resp.raise_for_status()\n"
            f"            data = resp.json()\n"
            f"            if isinstance(data, dict):\n"
            f"                return str(data.get('output', data))\n"
            f"            return str(data)\n"
            f"        except _httpx.HTTPError as exc:\n"
            f"            return {err_msg_expr}\n"
        )

        namespace: dict[str, Any] = {}
        exec(func_code, namespace)  # noqa: S102
        func: Callable[..., Any] = namespace[safe_name]
        func.__doc__ = description

        result.append(func)
        logger.debug(
            "Registered ADK tool %r -> endpoint %r (params: %s)",
            safe_name,
            tool_endpoint,
            params_str,
        )

    return result
