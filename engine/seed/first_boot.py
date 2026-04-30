"""First-boot registry seeder.

Populates empty registry tables with starter content from ``examples/seed/``
so a fresh ``docker compose up`` doesn't show empty pages on the dashboard.

Design rules
------------

* Idempotent — for each table, seed only if ``count == 0``. Never merge or
  overwrite existing rows.
* Goes through registry service classes (never raw SQL) so RBAC, audit, and
  versioning behaviour stays consistent with the rest of the platform.
* Per-table failures are logged and skipped — they must not crash API
  startup.

Public API: :func:`seed_registries`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import (
    Agent,
    KnowledgeBase,
    McpServer,
    Prompt,
    Provider,
    Tool,
)
from api.models.enums import ProviderType
from engine.config_parser import AgentConfig, FrameworkType
from registry.agents import AgentRegistry
from registry.mcp_servers import McpServerRegistry
from registry.prompts import PromptRegistry
from registry.providers import ProviderRegistry
from registry.tools import ToolRegistry

logger = logging.getLogger(__name__)


DEFAULT_EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "seed"


@dataclass
class SeedReport:
    """Result of a :func:`seed_registries` run.

    Attributes:
        seeded: Map of registry name -> count of rows newly inserted.
        skipped: Map of registry name -> reason it was skipped (already
            populated, no seed files, error).
        errors: List of human-readable error strings encountered while
            loading individual seed files.
    """

    seeded: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def total_inserted(self) -> int:
        return sum(self.seeded.values())


def _load_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML file into a dict (raises ValueError on malformed input)."""
    yaml = YAML(typ="safe")
    with path.open("r", encoding="utf-8") as f:
        doc = yaml.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"YAML root must be a mapping in {path}")
    return doc


def _list_yaml_files(directory: Path) -> list[Path]:
    """Return all .yaml/.yml files in *directory*, sorted for stable ordering."""
    if not directory.is_dir():
        return []
    return sorted(
        [p for p in directory.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file()]
    )


async def _table_count(session: AsyncSession, model: type) -> int:
    """Return the row count for *model*. Returns 0 on any error."""
    try:
        stmt = select(func.count()).select_from(model)
        result = await session.execute(stmt)
        return int(result.scalar() or 0)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not count %s: %s", model.__name__, exc)
        return 0


# ---------------------------------------------------------------------------
# Per-table seeders
# ---------------------------------------------------------------------------


async def _seed_prompts(session: AsyncSession, base: Path, report: SeedReport) -> None:
    if await _table_count(session, Prompt) > 0:
        report.skipped["prompts"] = "already populated"
        return

    files = _list_yaml_files(base / "prompts")
    if not files:
        report.skipped["prompts"] = "no seed files"
        return

    inserted = 0
    for path in files:
        try:
            doc = _load_yaml(path)
            await PromptRegistry.register(
                session,
                name=doc["name"],
                version=str(doc.get("version", "1.0.0")),
                content=doc.get("content", ""),
                description=doc.get("description", ""),
                team=doc.get("team", ""),
            )
            inserted += 1
        except Exception as exc:
            msg = f"prompts/{path.name}: {exc}"
            logger.warning("Seed prompt failed: %s", msg)
            report.errors.append(msg)

    report.seeded["prompts"] = inserted


async def _seed_tools(session: AsyncSession, base: Path, report: SeedReport) -> None:
    if await _table_count(session, Tool) > 0:
        report.skipped["tools"] = "already populated"
        return

    files = _list_yaml_files(base / "tools")
    if not files:
        report.skipped["tools"] = "no seed files"
        return

    inserted = 0
    for path in files:
        try:
            doc = _load_yaml(path)
            await ToolRegistry.register(
                session,
                name=doc["name"],
                description=doc.get("description", ""),
                tool_type=doc.get("type", "function"),
                schema_definition=doc.get("input_schema") or doc.get("schema") or {},
                endpoint=doc.get("endpoint"),
                source="seed",
            )
            inserted += 1
        except Exception as exc:
            msg = f"tools/{path.name}: {exc}"
            logger.warning("Seed tool failed: %s", msg)
            report.errors.append(msg)

    report.seeded["tools"] = inserted


async def _seed_mcp_servers(session: AsyncSession, base: Path, report: SeedReport) -> None:
    if await _table_count(session, McpServer) > 0:
        report.skipped["mcp_servers"] = "already populated"
        return

    files = _list_yaml_files(base / "mcp_servers")
    if not files:
        report.skipped["mcp_servers"] = "no seed files"
        return

    inserted = 0
    for path in files:
        try:
            doc = _load_yaml(path)
            transport = doc.get("transport", "stdio")
            endpoint = doc.get("endpoint") or f"http://localhost:9000/{doc['name']}"
            await McpServerRegistry.create(
                session,
                name=doc["name"],
                endpoint=endpoint,
                transport=transport,
            )
            inserted += 1
        except Exception as exc:
            msg = f"mcp_servers/{path.name}: {exc}"
            logger.warning("Seed mcp_server failed: %s", msg)
            report.errors.append(msg)

    report.seeded["mcp_servers"] = inserted


async def _seed_providers(session: AsyncSession, base: Path, report: SeedReport) -> None:
    if await _table_count(session, Provider) > 0:
        report.skipped["providers"] = "already populated"
        return

    files = _list_yaml_files(base / "providers")
    if not files:
        report.skipped["providers"] = "no seed files"
        return

    inserted = 0
    for path in files:
        try:
            doc = _load_yaml(path)
            ptype_raw = str(doc.get("provider_type", "ollama"))
            try:
                ptype = ProviderType(ptype_raw)
            except ValueError:
                report.errors.append(f"providers/{path.name}: unknown provider_type '{ptype_raw}'")
                continue

            await ProviderRegistry.create(
                session,
                name=doc["name"],
                provider_type=ptype,
                base_url=doc.get("base_url"),
                config=doc.get("config"),
            )
            inserted += 1
        except Exception as exc:
            msg = f"providers/{path.name}: {exc}"
            logger.warning("Seed provider failed: %s", msg)
            report.errors.append(msg)

    report.seeded["providers"] = inserted


async def _seed_knowledge_bases(session: AsyncSession, base: Path, report: SeedReport) -> None:
    if await _table_count(session, KnowledgeBase) > 0:
        report.skipped["knowledge_bases"] = "already populated"
        return

    files = _list_yaml_files(base / "knowledge_bases")
    if not files:
        report.skipped["knowledge_bases"] = "no seed files"
        return

    inserted = 0
    for path in files:
        try:
            doc = _load_yaml(path)
            kb = KnowledgeBase(
                name=doc["name"],
                description=doc.get("description", ""),
                kb_type=doc.get("kb_type", "document"),
                source_url=doc.get("source_url"),
                config=doc.get("config") or {},
            )
            session.add(kb)
            await session.flush()
            inserted += 1
        except Exception as exc:
            msg = f"knowledge_bases/{path.name}: {exc}"
            logger.warning("Seed knowledge_base failed: %s", msg)
            report.errors.append(msg)

    report.seeded["knowledge_bases"] = inserted


async def _seed_agents(session: AsyncSession, base: Path, report: SeedReport) -> None:
    if await _table_count(session, Agent) > 0:
        report.skipped["agents"] = "already populated"
        return

    files = _list_yaml_files(base / "agents")
    if not files:
        report.skipped["agents"] = "no seed files"
        return

    inserted = 0
    for path in files:
        try:
            doc = _load_yaml(path)
            framework_raw = doc.get("framework")
            framework = FrameworkType(framework_raw) if framework_raw else FrameworkType.langgraph

            config = AgentConfig(
                name=doc["name"],
                version=str(doc.get("version", "1.0.0")),
                description=doc.get("description", ""),
                team=doc.get("team", "platform"),
                owner=doc.get("owner", "demo@agentbreeder.local"),
                framework=framework,
                model=doc.get("model", {"primary": "gpt-4o-mini"}),
                deploy=doc.get("deploy", {"cloud": "local"}),
                tags=doc.get("tags", []),
                tools=doc.get("tools", []),
                prompts=doc.get("prompts", {}),
                guardrails=doc.get("guardrails", []),
            )
            await AgentRegistry.register(session, config, endpoint_url="")
            inserted += 1
        except Exception as exc:
            msg = f"agents/{path.name}: {exc}"
            logger.warning("Seed agent failed: %s", msg)
            report.errors.append(msg)

    report.seeded["agents"] = inserted


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def seed_registries(
    session: AsyncSession,
    examples_dir: Path | None = None,
) -> SeedReport:
    """Idempotently seed empty registry tables from canonical seed YAMLs.

    For each registry table (prompts, tools, mcp_servers, providers,
    knowledge_bases, agents) this function:

    1. Counts existing rows. If non-zero, the table is left untouched.
    2. Otherwise loads every ``.yaml`` / ``.yml`` file under the matching
       subdirectory of *examples_dir* and registers them via the
       corresponding registry service.

    The function commits once at the end after all per-table seeders have
    run. If any seeder raises unexpectedly the transaction is rolled back
    and a :class:`SeedReport` with the error is still returned — the caller
    (typically API startup) must not fail because of a seeding error.

    Args:
        session: An async SQLAlchemy session.
        examples_dir: Directory containing seed subfolders. Defaults to
            ``<repo>/examples/seed``.

    Returns:
        :class:`SeedReport` summarising what was seeded, skipped, or
        errored.

    Order matters: prompts/tools/mcp_servers/providers/knowledge_bases
    are seeded before agents, because the seed agent references them.
    """
    base = Path(examples_dir) if examples_dir else DEFAULT_EXAMPLES_DIR
    report = SeedReport()

    if not base.exists():
        logger.info("Seed directory %s does not exist; nothing to seed", base)
        report.skipped["all"] = f"seed directory {base} does not exist"
        return report

    logger.info("Running first-boot registry seed from %s", base)

    try:
        await _seed_prompts(session, base, report)
        await _seed_tools(session, base, report)
        await _seed_mcp_servers(session, base, report)
        await _seed_providers(session, base, report)
        await _seed_knowledge_bases(session, base, report)
        await _seed_agents(session, base, report)
        await session.commit()
    except Exception as exc:
        logger.error("Seeder hit an unexpected error: %s", exc, exc_info=True)
        report.errors.append(f"unexpected: {exc}")
        try:
            await session.rollback()
        except Exception:  # pragma: no cover - defensive
            pass
        return report

    logger.info(
        "First-boot seed complete: inserted=%d skipped=%s errors=%d",
        report.total_inserted,
        report.skipped,
        len(report.errors),
    )
    return report
