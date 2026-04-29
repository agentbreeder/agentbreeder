"""Resolve `prompts/<name>` references in agent.yaml to actual prompt content.

Resolution order (first match wins):

1. **Local file** — ``./prompts/<name>.md`` relative to the agent project root.
   This is the primary path for local development and for production agents
   that bake their prompts into the container at build time.

2. **Registry API** — GET ``${AGENTBREEDER_REGISTRY_URL}/api/v1/registry/prompts``,
   filter by name (optionally version). Falls back to the latest version when
   ``@<version>`` is not specified. Skipped if the env var is unset.

3. **Inline literal** — if the value does not look like a registry ref (no
   leading ``prompts/``), treat it as the prompt text itself. This preserves
   backward compatibility with agents that put the full prompt inline.

A reference may look like:

    prompts/microlearning-system            -> latest version
    prompts/microlearning-system@1.2.0      -> specific version

The function never raises on a "not found" condition for the inline-literal
path; it returns the input unchanged. It does raise ``PromptNotFoundError``
for refs that look like registry refs but cannot be resolved anywhere.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

import httpx

logger = logging.getLogger(__name__)


_REF_PREFIX: Final[str] = "prompts/"
_VERSION_SEP: Final[str] = "@"
_REGISTRY_TIMEOUT_SECONDS: Final[float] = 5.0


class PromptNotFoundError(LookupError):
    """Raised when a registry-style prompt ref cannot be resolved anywhere."""


def is_prompt_ref(value: str) -> bool:
    """Return True if ``value`` looks like a registry ref (``prompts/<name>``)."""
    return value.startswith(_REF_PREFIX) and "\n" not in value and len(value) < 256


def _split_ref(ref: str) -> tuple[str, str | None]:
    """Split ``prompts/<name>[@<version>]`` -> ``(name, version | None)``."""
    body = ref[len(_REF_PREFIX) :]
    if _VERSION_SEP in body:
        name, version = body.split(_VERSION_SEP, 1)
        return name, version
    return body, None


def _resolve_from_file(name: str, project_root: Path) -> str | None:
    candidate = project_root / "prompts" / f"{name}.md"
    if candidate.is_file():
        logger.info("Resolved prompt '%s' from local file: %s", name, candidate)
        return candidate.read_text(encoding="utf-8")
    return None


def _resolve_from_registry(name: str, version: str | None) -> str | None:
    base_url = os.getenv("AGENTBREEDER_REGISTRY_URL", "").strip().rstrip("/")
    if not base_url:
        return None

    token = os.getenv("AGENTBREEDER_REGISTRY_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params: dict[str, str] = {"name": name}
    if version:
        params["version"] = version

    url = f"{base_url}/api/v1/registry/prompts"
    try:
        with httpx.Client(timeout=_REGISTRY_TIMEOUT_SECONDS) as client:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Registry lookup failed for '%s' (%s): %s", name, url, exc)
        return None

    items = payload.get("data") or []
    matches = [item for item in items if item.get("name") == name]
    if version:
        matches = [item for item in matches if item.get("version") == version]
    if not matches:
        return None

    matches.sort(key=lambda item: item.get("version", ""), reverse=True)
    chosen = matches[0]
    content = chosen.get("content")
    if not content:
        return None
    logger.info(
        "Resolved prompt '%s' v%s from registry %s",
        name,
        chosen.get("version", "?"),
        base_url,
    )
    return content


def resolve_prompt(value: str, project_root: Path | str | None = None) -> str:
    """Resolve a prompt value to its content string.

    See module docstring for the resolution order.

    Args:
        value: Either an inline prompt string OR a ``prompts/<name>[@<version>]``
            registry ref.
        project_root: Directory to search for local prompt files. Defaults to
            the current working directory.

    Returns:
        The fully-resolved prompt text.

    Raises:
        PromptNotFoundError: When the value looks like a registry ref but
            cannot be resolved from any source.
    """
    if not is_prompt_ref(value):
        return value

    name, version = _split_ref(value)
    root = Path(project_root) if project_root else Path.cwd()

    content = _resolve_from_file(name, root)
    if content is not None:
        return content

    content = _resolve_from_registry(name, version)
    if content is not None:
        return content

    raise PromptNotFoundError(
        f"Prompt ref '{value}' not found. Looked in {root / 'prompts' / f'{name}.md'} "
        f"and at {os.getenv('AGENTBREEDER_REGISTRY_URL') or '<registry not configured>'}."
    )
