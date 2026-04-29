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
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

_REF_PREFIX: Final[str] = "tools/"
_REGISTRY_TIMEOUT_SECONDS: Final[float] = 5.0


class ToolNotFoundError(LookupError):
    """Raised when a ``tools/<name>`` ref cannot be resolved anywhere."""


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
    return ref[len(_REF_PREFIX):]


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
        return fn
    return None


def _resolve_from_standard(snake_name: str) -> Callable[..., Any] | None:
    try:
        module = importlib.import_module(f"engine.tools.standard.{snake_name}")
    except ImportError:
        return None
    fn = getattr(module, snake_name, None)
    if callable(fn):
        logger.info("Resolved tool '%s' from engine.tools.standard", snake_name)
        return fn
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
    return matches[0]


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
        raise ToolNotFoundError(
            f"'{ref}' is not a tool reference (must start with 'tools/')"
        )

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
