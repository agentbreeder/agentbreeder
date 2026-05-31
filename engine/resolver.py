"""Dependency resolver.

Resolves registry references (ref: tools/zendesk-mcp) into concrete artifacts.
For v0.1 tool/MCP refs are passed through unchanged; knowledge_base refs are
resolved to RAG index IDs so server templates can perform vector search at
invoke time.
Subagent refs are resolved into auto-generated tool definitions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from engine.a2a.tool_generator import generate_subagent_tools
from engine.config_parser import AgentConfig, KnowledgeBaseRef, ToolRef

logger = logging.getLogger(__name__)


def _resolve_kb_index_ids(kb_refs: list[KnowledgeBaseRef]) -> list[str]:
    """Return RAG store index IDs for the given knowledge-base refs.

    Resolution order:
      1. Try to match against the RAGStore by index name (slug of the ref, e.g.
         "kb/product-docs" → name "product-docs").
      2. If no match is found the ref itself is kept so the server template can
         retry at invoke time (graceful degradation).

    Returns a list of opaque ID strings that the server template stores in
    ``_KB_INDEX_IDS`` and passes to ``_inject_kb_context``.
    """
    if not kb_refs:
        return []

    try:
        from api.services.rag_service import get_rag_store

        store = get_rag_store()
        all_indexes, _ = store.list_indexes(page=1, per_page=1000)
        name_to_id: dict[str, str] = {idx.name: idx.id for idx in all_indexes}
    except Exception:  # RAGStore unavailable (e.g. engine running standalone)
        logger.debug("RAGStore not available; KB refs will be resolved at invoke time")
        name_to_id = {}

    resolved: list[str] = []
    for kb in kb_refs:
        ref: str = kb.ref
        # Derive the slug: "kb/product-docs" → "product-docs"
        slug = ref.split("/")[-1]
        if slug in name_to_id:
            index_id = name_to_id[slug]
            logger.debug("Resolved KB ref %r → index %s", ref, index_id)
            resolved.append(index_id)
        elif ref in name_to_id:
            # Full ref matches index name directly
            resolved.append(name_to_id[ref])
        else:
            # Fallback: pass the slug through; server template will retry
            logger.warning(
                "KB ref %r not found in RAGStore; passing slug %r through for runtime resolution",
                ref,
                slug,
            )
            resolved.append(slug)

    return resolved


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

        return asyncio.run(_fetch())
    except Exception:
        logger.debug("MemoryService not available; using postgresql backend default")
        return "postgresql", 0


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


def resolve_dependencies(config: AgentConfig, project_root: Path | None = None) -> AgentConfig:
    """Resolve all registry references in the config.

    - Tool and knowledge base refs are passed through (stub for v0.1).
    - Subagent refs are resolved into auto-generated call_{name} tools.
    - MCP server refs are passed through for sidecar deployment.
    - System prompt refs are baked into the config at deploy time.
    """
    _bake_prompt_ref(config, project_root)
    _check_tool_refs(config, project_root)
    refs = []
    for tool in config.tools:
        if tool.ref:
            refs.append(tool.ref)
    for kb in config.knowledge_bases:
        refs.append(kb.ref)

    # Resolve subagent refs into auto-generated tools
    if config.subagents:
        subagent_tools = generate_subagent_tools(config.subagents)
        for tool_def in subagent_tools:
            config.tools.append(
                ToolRef(
                    name=tool_def["name"],
                    type=tool_def["type"],
                    description=tool_def["description"],
                    schema=tool_def["schema"],
                )
            )
            refs.append(f"subagent:{tool_def.get('_subagent_ref', '')}")
        logger.info(
            "Generated %d subagent tools: %s",
            len(subagent_tools),
            [t["name"] for t in subagent_tools],
        )

    # MCP server refs (pass through for sidecar deployment)
    for mcp in config.mcp_servers:
        refs.append(mcp.ref)

    # Memory store refs — resolve backend + TTL into agent env vars
    if config.memory:
        if config.deploy.env_vars is None:
            config.deploy.env_vars = {}

        backend, ttl_seconds = _resolve_memory_config(config.memory.stores)
        if backend:
            config.deploy.env_vars.setdefault("MEMORY_BACKEND", backend)
        if ttl_seconds and ttl_seconds > 0:
            config.deploy.env_vars.setdefault("MEMORY_TTL_SECONDS", str(ttl_seconds))

        redis_url = os.environ.get("REDIS_URL")
        db_url = os.environ.get("DATABASE_URL")
        if backend == "redis" and redis_url:
            config.deploy.env_vars.setdefault("REDIS_URL", redis_url)
        elif backend == "postgresql" and db_url:
            config.deploy.env_vars.setdefault("DATABASE_URL", db_url)

        for store_ref in config.memory.stores:
            refs.append(f"memory:{store_ref}")
        logger.debug("Resolved memory stores: backend=%s ttl=%s", backend, ttl_seconds)

    # Resolve knowledge base refs → RAG index IDs and inject into env vars so
    # that server templates can perform vector search at invoke time.
    if config.knowledge_bases:
        if config.deploy.env_vars is None:
            config.deploy.env_vars = {}

        kb_index_ids = _resolve_kb_index_ids(config.knowledge_bases)
        if kb_index_ids:
            config.deploy.env_vars["KB_INDEX_IDS"] = ",".join(kb_index_ids)
            logger.info(
                "Resolved %d knowledge base(s) → KB_INDEX_IDS=%s",
                len(kb_index_ids),
                config.deploy.env_vars["KB_INDEX_IDS"],
            )

        # If NEO4J_URL is set in environment, inject it for graph-augmented KB search
        neo4j_url = os.environ.get("NEO4J_URL")
        if neo4j_url and "NEO4J_URL" not in config.deploy.env_vars:
            config.deploy.env_vars["NEO4J_URL"] = neo4j_url
            logger.debug("Injected NEO4J_URL for agent with knowledge bases")

    if refs:
        logger.debug("Dependency resolution — refs: %s", refs)

    return config
