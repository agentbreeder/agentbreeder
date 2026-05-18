"""Resolve ``tools/<name>`` references in agent.yaml to callable Python tools.

Resolution order (first match wins):

1. **Local override** — ``./tools/<snake_name>.py`` in the agent project root
   exporting a function named after the file (snake_case). Lets agents customise
   a stock tool without forking the registry.

2. **Standard library** — ``engine.tools.standard.<snake_name>``. First-party
   tools maintained in this repo.

3. **Registry API** — ``${AGENTBREEDER_REGISTRY_URL}/api/v1/registry/tools``.
   Returns metadata (name, description, schema, endpoint) but NOT a callable.
   Useful when the tool runs as an MCP server or remote function — caller must
   wrap the metadata into a callable themselves.

A reference looks like:

    tools/web-search          -> latest version, kebab case
    tools/markdown-writer

Kebab-case in the ref maps to snake_case for the Python module/function name.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, cast

import httpx

logger = logging.getLogger(__name__)

_REF_PREFIX: Final[str] = "tools/"
_REGISTRY_TIMEOUT_SECONDS: Final[float] = 5.0


class ToolNotFoundError(LookupError):
    """Raised when a ``tools/<name>`` ref cannot be resolved anywhere."""


class ToolInputValidationError(ValueError):
    """Raised when a tool's input dict fails JSON-Schema validation."""


def _validate_against_schema(
    tool_input: dict[str, Any],
    schema: dict[str, Any],
    tool_name: str,
) -> None:
    """Validate ``tool_input`` against ``schema`` (a JSON Schema dict).

    Uses the ``jsonschema`` library when available; falls back to a minimal
    type-and-required-field check otherwise. Raises
    :class:`ToolInputValidationError` with a clear message including the
    offending field path on failure.
    """
    if not isinstance(tool_input, dict):
        raise ToolInputValidationError(
            f"Tool '{tool_name}' input must be a dict, got {type(tool_input).__name__}"
        )
    if not isinstance(schema, dict) or not schema:
        return  # Nothing to validate against.

    try:
        import jsonschema  # noqa: PLC0415

        try:
            jsonschema.validate(instance=tool_input, schema=schema)
            return
        except jsonschema.ValidationError as exc:
            path = ".".join(str(p) for p in exc.absolute_path) or "<root>"
            raise ToolInputValidationError(
                f"Tool '{tool_name}' input validation failed at '{path}': {exc.message}"
            ) from exc
        except jsonschema.SchemaError as exc:
            logger.warning("Tool '%s' has an invalid SCHEMA: %s", tool_name, exc.message)
            # Fall through to minimal checker so we still catch missing required fields.
    except ImportError:
        logger.debug("jsonschema not available — falling back to minimal validation")

    # Minimal fallback — required fields + top-level property types.
    required = schema.get("required", []) or []
    if isinstance(required, list):
        for field_name in required:
            if field_name not in tool_input:
                raise ToolInputValidationError(
                    f"Tool '{tool_name}' missing required field '{field_name}'"
                )
    props = schema.get("properties", {}) or {}
    if isinstance(props, dict):
        type_map: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        for key, prop_schema in props.items():
            if key not in tool_input or not isinstance(prop_schema, dict):
                continue
            expected = prop_schema.get("type")
            if expected and expected in type_map:
                if not isinstance(tool_input[key], type_map[expected]):
                    raise ToolInputValidationError(
                        f"Tool '{tool_name}' field '{key}' must be {expected}, "
                        f"got {type(tool_input[key]).__name__}"
                    )


def validate_tool_input(
    ref: str,
    tool_input: dict[str, Any],
    project_root: Path | str | None = None,
) -> None:
    """Validate ``tool_input`` against the declared SCHEMA of ``ref``'s tool.

    Looks up the resolved tool module and reads its top-level ``SCHEMA`` dict
    (the JSON-Schema descriptor maintained by every standard tool). Raises
    :class:`ToolInputValidationError` on failure. A tool with no ``SCHEMA``
    attribute is treated as schemaless (no-op).
    """
    if not is_tool_ref(ref):
        raise ToolNotFoundError(f"'{ref}' is not a tool reference (must start with 'tools/')")

    kebab = _strip_ref(ref)
    snake = _kebab_to_snake(kebab)
    root = Path(project_root) if project_root else Path.cwd()

    schema = _get_tool_schema(snake, root)
    if schema is None:
        return
    _validate_against_schema(tool_input, schema, snake)


def _get_tool_schema(snake_name: str, project_root: Path) -> dict[str, Any] | None:
    """Return the SCHEMA dict for a tool by resolving its module."""
    # Local override first.
    candidate_module = project_root / "tools" / f"{snake_name}.py"
    if candidate_module.is_file():
        spec = importlib.util.spec_from_file_location(
            f"agent_tools.{snake_name}", candidate_module
        )
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
                schema = getattr(module, "SCHEMA", None)
                if isinstance(schema, dict):
                    return schema
            except Exception:  # noqa: BLE001
                pass
    # Standard library.
    try:
        module = importlib.import_module(f"engine.tools.standard.{snake_name}")
    except ImportError:
        return None
    schema = getattr(module, "SCHEMA", None)
    return schema if isinstance(schema, dict) else None


def is_tool_ref(value: str) -> bool:
    """Return True if ``value`` looks like a ``tools/<name>`` registry ref."""
    return (
        isinstance(value, str)
        and value.startswith(_REF_PREFIX)
        and "\n" not in value
        and len(value) < 256
    )


def _kebab_to_snake(name: str) -> str:
    return name.replace("-", "_")


def _strip_ref(ref: str) -> str:
    return ref[len(_REF_PREFIX) :]


def _resolve_from_local(snake_name: str, project_root: Path) -> Callable[..., Any] | None:
    candidate_module = project_root / "tools" / f"{snake_name}.py"
    if not candidate_module.is_file():
        return None
    # Load via importlib, isolating from package state so reruns pick up edits.
    spec = importlib.util.spec_from_file_location(f"agent_tools.{snake_name}", candidate_module)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, snake_name, None)
    if callable(fn):
        logger.info("Resolved tool '%s' from local override: %s", snake_name, candidate_module)
        return cast(Callable[..., Any], fn)
    return None


def _resolve_from_standard(snake_name: str) -> Callable[..., Any] | None:
    try:
        module = importlib.import_module(f"engine.tools.standard.{snake_name}")
    except ImportError:
        return None
    fn = getattr(module, snake_name, None)
    if callable(fn):
        logger.info("Resolved tool '%s' from engine.tools.standard", snake_name)
        return cast(Callable[..., Any], fn)
    return None


def _resolve_from_registry(name: str) -> dict[str, Any] | None:
    """Returns registry metadata. Caller must wrap into a callable themselves."""
    base_url = os.getenv("AGENTBREEDER_REGISTRY_URL", "").strip().rstrip("/")
    if not base_url:
        return None

    token = os.getenv("AGENTBREEDER_REGISTRY_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{base_url}/api/v1/registry/tools"
    try:
        with httpx.Client(timeout=_REGISTRY_TIMEOUT_SECONDS) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Registry tool lookup failed (%s): %s", url, exc)
        return None

    items = payload.get("data") or []
    matches = [item for item in items if item.get("name") == name]
    if not matches:
        return None
    logger.info("Resolved tool '%s' metadata from registry %s", name, base_url)
    return cast(dict[str, Any], matches[0])


def resolve_tool(
    ref: str,
    project_root: Path | str | None = None,
) -> Callable[..., Any]:
    """Resolve a ``tools/<name>`` ref to an actual callable Python function.

    Args:
        ref: A ``tools/<kebab-name>`` reference string.
        project_root: Directory to search for local tool overrides. Defaults
            to the current working directory.

    Returns:
        A callable that the agent runtime can register as a tool. The function
        signature and docstring are preserved (frameworks like ADK introspect
        these to build tool schemas).

    Raises:
        ToolNotFoundError: When the ref cannot be resolved as a local override,
            standard-library tool, or registry metadata.
    """
    if not is_tool_ref(ref):
        raise ToolNotFoundError(f"'{ref}' is not a tool reference (must start with 'tools/')")

    kebab = _strip_ref(ref)
    snake = _kebab_to_snake(kebab)
    root = Path(project_root) if project_root else Path.cwd()

    fn = _resolve_from_local(snake, root)
    if fn is not None:
        return fn

    fn = _resolve_from_standard(snake)
    if fn is not None:
        return fn

    metadata = _resolve_from_registry(kebab)
    if metadata is not None:
        raise ToolNotFoundError(
            f"Tool '{ref}' was found in the registry as metadata only "
            f"(endpoint={metadata.get('endpoint')}). Wrap it into a callable "
            f"yourself, or install the matching package."
        )

    raise ToolNotFoundError(
        f"Tool ref '{ref}' not found. Looked at "
        f"{root / 'tools' / f'{snake}.py'}, engine.tools.standard.{snake}, "
        f"and {os.getenv('AGENTBREEDER_REGISTRY_URL') or '<registry not configured>'}."
    )
