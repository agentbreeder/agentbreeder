"""Unit tests for engine.seed.first_boot — auto-seed registries on first boot.

Covers issue #180. Verifies that the seeder:

* Populates empty tables from ``examples/seed/`` YAMLs.
* Is idempotent: a second run inserts nothing when tables are non-empty.
* Skips a table cleanly when it already has rows.
* Returns a structured :class:`SeedReport` for the API and CLI to consume.
* Logs and continues when an individual seed YAML is malformed.
* Skips entirely when the examples directory does not exist.
* Handles a custom ``examples_dir`` override.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api.models.database import (
    Agent,
    Base,
    KnowledgeBase,
    McpServer,
    Prompt,
    Provider,
    Tool,
)
from engine.seed import SeedReport, seed_registries

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_session():
    """Spin up an in-memory SQLite database for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _seed_examples_dir() -> Path:
    """Path to the canonical seed YAMLs shipped in this repo."""
    return Path(__file__).resolve().parents[2] / "examples" / "seed"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_populates_empty_registries(async_session: AsyncSession) -> None:
    """First run on empty DB should insert at least one row per table that has seed files."""
    report = await seed_registries(async_session, examples_dir=_seed_examples_dir())

    assert isinstance(report, SeedReport)
    assert report.total_inserted > 0, "expected some rows to be inserted from canonical seeds"

    # Every table that we ship a seed for should have produced rows.
    assert report.seeded.get("prompts", 0) >= 1
    assert report.seeded.get("tools", 0) >= 2  # web-search + order-lookup
    assert report.seeded.get("mcp_servers", 0) >= 1
    assert report.seeded.get("providers", 0) >= 1
    assert report.seeded.get("knowledge_bases", 0) >= 1
    assert report.seeded.get("agents", 0) >= 1


@pytest.mark.asyncio
async def test_seed_is_idempotent(async_session: AsyncSession) -> None:
    """Running twice in a row should not duplicate rows."""
    base = _seed_examples_dir()

    first = await seed_registries(async_session, examples_dir=base)
    assert first.total_inserted > 0

    snapshot: dict[str, int] = {}
    for label, model in (
        ("prompts", Prompt),
        ("tools", Tool),
        ("mcp_servers", McpServer),
        ("providers", Provider),
        ("knowledge_bases", KnowledgeBase),
        ("agents", Agent),
    ):
        rows = (await async_session.execute(select(model))).scalars().all()
        snapshot[label] = len(rows)

    second = await seed_registries(async_session, examples_dir=base)
    assert second.total_inserted == 0, "second run must not insert anything"
    # Every table should be reported as already populated (or have no seed files).
    for label in snapshot:
        assert label in second.skipped

    # Counts must be unchanged.
    for label, model in (
        ("prompts", Prompt),
        ("tools", Tool),
        ("mcp_servers", McpServer),
        ("providers", Provider),
        ("knowledge_bases", KnowledgeBase),
        ("agents", Agent),
    ):
        rows = (await async_session.execute(select(model))).scalars().all()
        assert len(rows) == snapshot[label], f"{label} row count changed on re-run"


@pytest.mark.asyncio
async def test_seed_skips_table_when_already_populated(
    async_session: AsyncSession,
) -> None:
    """If a single table already has rows, that one is skipped but others still seed."""
    # Pre-populate the tools table with one row, leave others empty.
    async_session.add(Tool(name="pre-existing", description="", tool_type="function"))
    await async_session.commit()

    report = await seed_registries(async_session, examples_dir=_seed_examples_dir())

    assert "tools" in report.skipped
    assert report.skipped["tools"] == "already populated"
    # Other tables should still have been seeded.
    assert report.seeded.get("prompts", 0) >= 1
    assert report.seeded.get("providers", 0) >= 1


@pytest.mark.asyncio
async def test_seed_returns_structured_report(async_session: AsyncSession) -> None:
    """SeedReport must expose seeded/skipped/errors and a total_inserted property."""
    report = await seed_registries(async_session, examples_dir=_seed_examples_dir())

    assert isinstance(report.seeded, dict)
    assert isinstance(report.skipped, dict)
    assert isinstance(report.errors, list)
    assert report.total_inserted == sum(report.seeded.values())


@pytest.mark.asyncio
async def test_seed_handles_missing_directory(async_session: AsyncSession, tmp_path: Path) -> None:
    """Pointing at a non-existent dir should produce no inserts and no exceptions."""
    fake = tmp_path / "does-not-exist"
    report = await seed_registries(async_session, examples_dir=fake)

    assert report.total_inserted == 0
    assert "all" in report.skipped
    assert "does not exist" in report.skipped["all"]


@pytest.mark.asyncio
async def test_seed_logs_and_continues_on_malformed_yaml(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    """A bad seed file should be reported in errors but not abort the whole run."""
    base = tmp_path / "seed"
    (base / "prompts").mkdir(parents=True)
    (base / "tools").mkdir()

    # One valid prompt seed, one malformed seed.
    (base / "prompts" / "good.yaml").write_text(
        "name: ok-prompt\nversion: 1.0.0\ncontent: hello\nteam: t\ndescription: d\n",
        encoding="utf-8",
    )
    (base / "prompts" / "bad.yaml").write_text("not: [valid", encoding="utf-8")
    (base / "tools" / "good.yaml").write_text(
        "name: ok-tool\nversion: 1.0.0\ndescription: d\ntype: function\n",
        encoding="utf-8",
    )

    report = await seed_registries(async_session, examples_dir=base)

    assert report.seeded.get("prompts", 0) == 1
    assert report.seeded.get("tools", 0) == 1
    assert any("bad.yaml" in err for err in report.errors)


@pytest.mark.asyncio
async def test_seed_uses_custom_examples_dir(async_session: AsyncSession, tmp_path: Path) -> None:
    """examples_dir override must be honoured (not just the default)."""
    base = tmp_path / "my-seeds"
    (base / "providers").mkdir(parents=True)
    (base / "providers" / "p1.yaml").write_text(
        "name: custom-provider\nprovider_type: ollama\nbase_url: http://localhost:11434\n",
        encoding="utf-8",
    )

    report = await seed_registries(async_session, examples_dir=base)

    assert report.seeded.get("providers", 0) == 1
    rows = (await async_session.execute(select(Provider))).scalars().all()
    assert any(p.name == "custom-provider" for p in rows)


@pytest.mark.asyncio
async def test_seed_skips_unknown_provider_type_without_crashing(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    """An invalid provider_type must surface as an error and not abort the run."""
    base = tmp_path / "seeds"
    (base / "providers").mkdir(parents=True)
    (base / "providers" / "bogus.yaml").write_text(
        "name: bogus\nprovider_type: not-a-real-type\nbase_url: http://x\n",
        encoding="utf-8",
    )
    (base / "providers" / "ok.yaml").write_text(
        "name: ok-provider\nprovider_type: openai\nbase_url: https://api.openai.com\n",
        encoding="utf-8",
    )

    report = await seed_registries(async_session, examples_dir=base)

    assert report.seeded.get("providers", 0) == 1
    assert any("not-a-real-type" in err for err in report.errors)
