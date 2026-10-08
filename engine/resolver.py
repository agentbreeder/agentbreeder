"""Dependency resolver.

Resolves registry references (ref: tools/zendesk-mcp) into concrete artifacts.
Tool/MCP refs are passed through unchanged; memory store refs are resolved
into backend env vars.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from engine.config_parser import AgentConfig

logger = logging.getLogger(__name__)


def _resolve_memory_config(store_refs: list[str]) -> tuple[str, int]:
    """Return (backend, ttl_seconds) for the agent's memory configuration.

    Looks up the first memory store ref from the platform registry. Falls back
    to (postgresql, 0) so agents always get a meaningful default.
    """
    if not store_refs:
        return "none", 0

    try:
        import asyncio  # noqa: PLC0415

        from api.services.memory_service import MemoryService  # noqa: PLC0415

        async def _fetch() -> tuple[str, int]:
            slug = store_refs[0].split("/")[-1]
            configs, _ = await MemoryService.list_configs(per_page=1000)
            for cfg in configs:
                if cfg.name == slug:
                    ttl = (cfg.config or {}).get("ttl_seconds", 0) if hasattr(cfg, "config") else 0
                    return cfg.backend_type, int(ttl)
            return "postgresql", 0

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — safe to drive one directly.
            return asyncio.run(_fetch())
        # A loop is already running in this thread (the CLI wraps deploy in
        # asyncio.run), so asyncio.run() would raise and leak an un-awaited
        # coroutine. Run the fetch in a dedicated worker thread instead.
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_fetch())).result()
    except Exception:
        logger.debug("MemoryService not available; using postgresql backend default")
        return "postgresql", 0


def _infer_memory_backend(backend_url: str | None) -> str | None:
    """Infer the memory backend from an explicit ``backend_url`` scheme.

    Lets a user point an agent at a managed store with just
    ``memory.backend_url: redis://…`` (or ``postgresql://…``) — without also
    restating ``memory.backend``. Returns ``None`` for an unknown/empty scheme.
    """
    if not backend_url:
        return None
    scheme = backend_url.split("://", 1)[0].lower()
    if scheme in ("redis", "rediss"):
        return "redis"
    if scheme in ("postgres", "postgresql"):
        return "postgresql"
    return None


class ResolutionError(Exception):
    """Raised when a registry reference cannot be resolved."""


def _bake_prompt_ref(config: AgentConfig, project_root: Path | None) -> None:
    """Resolve a ``prompts/<name>`` system-prompt ref into a literal string at
    deploy time, so the container receives it via ``AGENT_SYSTEM_PROMPT`` instead
    of resolving over the network at runtime. Unresolvable refs are left as-is
    (the runtime can still try) with a warning."""
    from engine.prompt_resolver import (  # local import avoids import cycles
        PromptNotFoundError,
        is_prompt_ref,
        resolve_prompt,
    )

    system = config.prompts.system
    if not system or not is_prompt_ref(system):
        return
    try:
        resolved = resolve_prompt(system, project_root)
    except PromptNotFoundError:
        logger.warning(
            "Prompt ref %r could not be resolved at deploy time; the container "
            "will attempt runtime resolution.",
            system,
        )
        return
    except Exception as exc:  # deploy-time best-effort: never crash the deploy
        logger.warning(
            "Unexpected error resolving prompt ref %r at deploy time; leaving it "
            "for runtime resolution. Error: %s",
            system,
            exc,
        )
        return
    if resolved:
        config.prompts.system = resolved
        logger.info("Baked prompt ref %r into AGENT_SYSTEM_PROMPT at deploy", system)


def _check_tool_refs(config: AgentConfig, project_root: Path | None) -> None:
    """Best-effort deploy-time check that ``ref: tools/<name>`` entries resolve to a
    local file or a first-party ``engine.tools.standard`` tool (now bundled in the
    image). Missing tools warn rather than raise — registry/network tools may only
    resolve at runtime."""
    from engine.tool_resolver import ToolNotFoundError, is_tool_ref, resolve_tool

    for tool in config.tools:
        ref = tool.ref
        if not ref or not is_tool_ref(ref):
            continue
        try:
            resolve_tool(ref, project_root=project_root)
        except ToolNotFoundError:
            logger.warning(
                "Tool ref %r did not resolve to a local or first-party tool at "
                "deploy; relying on runtime/registry resolution.",
                ref,
            )
        except Exception as exc:  # deploy-time best-effort: never crash the deploy
            logger.warning(
                "Unexpected error checking tool ref %r at deploy time; leaving it "
                "for runtime resolution. Error: %s",
                ref,
                exc,
            )


def _warn_unreachable_local_urls(config: AgentConfig) -> None:
    """Warn if a resolved backend URL points at localhost while deploying to a
    real cloud — a common footgun that yields an agent that cannot reach its store.
    This does not reject the config (an operator may use a cross-network alias)."""
    cloud = str(config.deploy.cloud)
    if cloud in ("local", "claude-managed"):
        return
    env_vars = config.deploy.env_vars or {}
    for key in ("REDIS_URL", "DATABASE_URL"):
        val = env_vars.get(key, "")
        if "localhost" in val or "127.0.0.1" in val:
            logger.warning(
                "Backend URL %s=%r targets localhost but deploy.cloud=%s; the "
                "deployed agent will likely not be able to reach it. Set an "
                "explicit cloud-reachable backend_url.",
                key,
                val,
                cloud,
            )


def resolve_dependencies(config: AgentConfig, project_root: Path | None = None) -> AgentConfig:
    """Resolve all registry references in the config.

    - Tool refs are passed through.
    - MCP server refs are passed through for sidecar deployment.
    - System prompt refs are baked into the config at deploy time.
    """
    _bake_prompt_ref(config, project_root)
    _check_tool_refs(config, project_root)
    allow_local = os.environ.get("AGENTBREEDER_ALLOW_LOCAL_BACKENDS") == "1"
    refs = []
    for tool in config.tools:
        if tool.ref:
            refs.append(tool.ref)

    # MCP server refs (pass through for sidecar deployment)
    for mcp in config.mcp_servers:
        refs.append(mcp.ref)

    # Memory store refs — resolve backend + TTL into agent env vars
    if config.memory:
        if config.deploy.env_vars is None:
            config.deploy.env_vars = {}

        backend, ttl_seconds = _resolve_memory_config(config.memory.stores)
        # Precedence: explicit agent.yaml backend > backend_url scheme > registry.
        backend = (
            config.memory.backend or _infer_memory_backend(config.memory.backend_url) or backend
        )
        if backend:
            config.deploy.env_vars.setdefault("MEMORY_BACKEND", backend)
        if ttl_seconds and ttl_seconds > 0:
            config.deploy.env_vars.setdefault("MEMORY_TTL_SECONDS", str(ttl_seconds))

        # D2 contract: explicit backend_url wins; local host env only behind a flag.
        explicit = config.memory.backend_url
        if backend == "redis":
            url = explicit or (os.environ.get("REDIS_URL") if allow_local else None)
            if url:
                config.deploy.env_vars.setdefault("REDIS_URL", url)
        elif backend == "postgresql":
            url = explicit or (os.environ.get("DATABASE_URL") if allow_local else None)
            if url:
                config.deploy.env_vars.setdefault("DATABASE_URL", url)

        for store_ref in config.memory.stores:
            refs.append(f"memory:{store_ref}")
        logger.debug("Resolved memory stores: backend=%s ttl=%s", backend, ttl_seconds)

    if refs:
        logger.debug("Dependency resolution — refs: %s", refs)

    _warn_unreachable_local_urls(config)
    return config
