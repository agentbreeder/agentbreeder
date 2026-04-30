"""agentbreeder seed — seed ChromaDB, Neo4j, or the registry with sample data.

Can be run any time: after quickstart, after a fresh stack restart,
or to load your own documents into the vector store.

Usage:
    agentbreeder seed                          # seed both ChromaDB and Neo4j
    agentbreeder seed --chromadb               # only vector store
    agentbreeder seed --neo4j                  # only graph DB
    agentbreeder seed --registry               # populate agents/prompts/tools/MCP/providers
    agentbreeder seed --chromadb --docs ./my-docs/   # ingest your own files
    agentbreeder seed --chromadb --collection custom-kb  # custom collection
    agentbreeder seed --neo4j --cypher ./graph.cypher    # custom graph
    agentbreeder seed --list                   # show what's currently seeded
    agentbreeder seed --clear                  # drop and re-seed everything
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

SEED_SCRIPT = Path(__file__).parent.parent.parent / "deploy" / "seed" / "seed.py"
CHROMADB_BASE = "http://localhost:8001"
NEO4J_HTTP = "http://localhost:7474"


def seed(
    chromadb: bool = typer.Option(
        False,
        "--chromadb",
        help="Seed the ChromaDB vector store",
    ),
    neo4j: bool = typer.Option(
        False,
        "--neo4j",
        help="Seed the Neo4j knowledge graph",
    ),
    registry: bool = typer.Option(
        False,
        "--registry",
        help="Seed registry tables (agents, prompts, tools, MCP, providers, KBs) from examples/seed/.",
    ),
    examples_dir: Path = typer.Option(
        None,
        "--examples-dir",
        help="Custom path to examples/seed/ (registry mode only).",
        exists=False,
    ),
    docs: Path = typer.Option(
        None,
        "--docs",
        "-d",
        help="Directory of .md/.txt files to ingest (ChromaDB only)",
        exists=False,
    ),
    collection: str = typer.Option(
        "agentbreeder_knowledge",
        "--collection",
        "-c",
        help="ChromaDB collection name",
    ),
    cypher: Path = typer.Option(
        None,
        "--cypher",
        help="Custom .cypher file for Neo4j",
        exists=False,
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Drop existing data before seeding",
    ),
    list_: bool = typer.Option(
        False,
        "--list",
        "-l",
        help="Show what is currently seeded (no writes)",
    ),
    embedding_model: str = typer.Option(
        "default",
        "--embedding-model",
        help=(
            "Embedding model for ChromaDB. "
            "Options: default | openai:text-embedding-3-small | ollama:<model>"
        ),
    ),
) -> None:
    """Seed ChromaDB (vector store) and Neo4j (knowledge graph) with sample data.

    Run after `agentbreeder quickstart` or `agentbreeder up` to populate the
    databases that the rag-agent and graph-agent use.

    Provide your own documents with --docs to ingest custom content into ChromaDB.
    Use --cypher to load a custom Cypher file into Neo4j.

    Examples:
        agentbreeder seed
        agentbreeder seed --chromadb --docs ./company-docs/
        agentbreeder seed --neo4j --cypher ./my-knowledge-graph.cypher
        agentbreeder seed --list
        agentbreeder seed --clear
        agentbreeder seed --registry
    """
    # Registry mode runs the first-boot registry seeder via the API DB and
    # is independent of the ChromaDB/Neo4j flow below.
    if registry:
        _run_registry_seed(examples_dir)
        return

    # Import the seed module directly
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("seed_module", SEED_SCRIPT)
        if spec is None or spec.loader is None:
            raise ImportError("seed.py not found")
        import types

        seed_mod = types.ModuleType("seed_module")
        spec.loader.exec_module(seed_mod)
    except Exception:
        # Fallback: call as subprocess
        _run_via_subprocess(
            chromadb, neo4j, docs, collection, cypher, clear, list_, embedding_model
        )
        return

    _run_via_module(
        seed_mod, chromadb, neo4j, docs, collection, cypher, clear, list_, embedding_model
    )


def _run_via_module(
    mod,
    do_chromadb: bool,
    do_neo4j: bool,
    docs: Path | None,
    collection: str,
    cypher: Path | None,
    clear: bool,
    list_: bool,
    embedding_model: str = "default",
) -> None:
    """Use the seed module directly (no subprocess)."""
    if list_:
        _print_status(mod)
        return

    # Default: seed both if neither flag given
    seed_chroma = do_chromadb or (not do_chromadb and not do_neo4j)
    seed_graph = do_neo4j or (not do_chromadb and not do_neo4j)

    console.print()

    if seed_chroma:
        console.print(
            Panel(
                f"  Target:     [cyan]{CHROMADB_BASE}[/cyan]\n"
                f"  Collection: [cyan]{collection}[/cyan]\n"
                f"  Source:     [dim]{docs or (SEED_SCRIPT.parent / 'docs')}[/dim]"
                + ("  \n  Mode: [yellow]clear + re-seed[/yellow]" if clear else ""),
                title="Seeding ChromaDB",
                border_style="blue",
                padding=(0, 2),
            )
        )
        result = mod.seed_chromadb(
            docs_dir=docs, collection=collection, clear=clear, embedding_model=embedding_model
        )
        if result["ok"]:
            console.print(
                f"  [green]✓[/green] Seeded [bold]{result['documents_seeded']}[/bold] document chunks"
            )
            console.print(
                f"  [green]✓[/green] Collection: [cyan]{collection}[/cyan] ({result['collection_id'][:8]}...)"
            )
            console.print()
        else:
            console.print(f"  [red]✗ ChromaDB seed failed:[/red] {result.get('error')}")
            console.print("  [dim]Is the stack running? Try: agentbreeder up[/dim]")
            console.print()

    if seed_graph:
        console.print(
            Panel(
                f"  Target: [cyan]{NEO4J_HTTP}[/cyan]  (login: neo4j / agentbreeder)\n"
                f"  Source: [dim]{cypher or 'built-in quickstart knowledge graph'}[/dim]"
                + ("  \n  Mode: [yellow]clear + re-seed[/yellow]" if clear else ""),
                title="Seeding Neo4j",
                border_style="blue",
                padding=(0, 2),
            )
        )
        result = mod.seed_neo4j(cypher_file=cypher, clear=clear)
        if result["ok"]:
            console.print(
                f"  [green]✓[/green] Ran [bold]{result['statements_run']}[/bold] Cypher statements"
            )
            summary = mod.list_neo4j()
            if summary["ok"]:
                for label, count in summary["counts"].items():
                    console.print(f"  [green]✓[/green] {label:15} {count}")
            console.print()
        else:
            console.print("  [red]✗ Neo4j seed failed[/red]")
            for err in result.get("errors", []):
                console.print(f"    [dim]{err}[/dim]")
            console.print("  [dim]Is Neo4j running? Try: agentbreeder up[/dim]")
            console.print()

    _print_next_steps()


def _print_status(mod) -> None:
    console.print()

    # ChromaDB
    chroma_result = mod.list_chromadb()
    chroma_table = Table(title="ChromaDB Collections", show_header=True, header_style="bold")
    chroma_table.add_column("Collection")
    chroma_table.add_column("Documents", justify="right")
    if chroma_result["ok"]:
        for col in chroma_result.get("collections", []):
            chroma_table.add_row(f"[cyan]{col['name']}[/cyan]", str(col["count"]))
        if not chroma_result.get("collections"):
            chroma_table.add_row("[dim]empty[/dim]", "0")
    else:
        chroma_table.add_row(f"[red]{chroma_result.get('error')}[/red]", "—")
    console.print(chroma_table)
    console.print()

    # Neo4j
    neo_result = mod.list_neo4j()
    neo_table = Table(title="Neo4j Graph", show_header=True, header_style="bold")
    neo_table.add_column("Entity")
    neo_table.add_column("Count", justify="right")
    if neo_result["ok"]:
        for label, count in neo_result["counts"].items():
            neo_table.add_row(label.capitalize(), str(count))
    else:
        neo_table.add_row(f"[red]{neo_result.get('error')}[/red]", "—")
    console.print(neo_table)
    console.print()


def _print_next_steps() -> None:
    console.print(
        Panel(
            "  [bold]Test the seeded data:[/bold]\n\n"
            "  [cyan]agentbreeder chat rag-agent[/cyan]\n"
            '    Ask: [dim]"How do I deploy an agent to AWS?"[/dim]\n'
            '    Ask: [dim]"What is the agent.yaml format?"[/dim]\n\n'
            "  [cyan]agentbreeder chat graph-agent[/cyan]\n"
            '    Ask: [dim]"Which agents use ChromaDB?"[/dim]\n'
            '    Ask: [dim]"Show me all agents in the quickstart team"[/dim]\n\n'
            "  [bold]Add your own documents:[/bold]\n"
            "  [cyan]agentbreeder seed --chromadb --docs ./my-company-docs/[/cyan]\n\n"
            "  [bold]Explore the graph in Neo4j Browser:[/bold]\n"
            "  [cyan]http://localhost:7474[/cyan]  (neo4j / agentbreeder)\n"
            "  Run: [dim]MATCH (n) RETURN n LIMIT 50[/dim]",
            title="[bold green]Seed complete[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )
    console.print()


def _run_via_subprocess(
    do_chromadb: bool,
    do_neo4j: bool,
    docs: Path | None,
    collection: str,
    cypher: Path | None,
    clear: bool,
    list_: bool,
    embedding_model: str = "default",
) -> None:
    """Fall back to calling seed.py as a subprocess."""
    import subprocess

    args = [sys.executable, str(SEED_SCRIPT)]
    if do_chromadb and not do_neo4j:
        args.append("--chromadb-only")
    elif do_neo4j and not do_chromadb:
        args.append("--neo4j-only")
    if docs:
        args += ["--docs", str(docs)]
    if collection != "agentbreeder_knowledge":
        args += ["--collection", collection]
    if cypher:
        args += ["--cypher", str(cypher)]
    if clear:
        args.append("--clear")
    if list_:
        args.append("--list")
    if embedding_model != "default":
        args += ["--embedding-model", embedding_model]
    subprocess.run(args)


def _run_registry_seed(examples_dir: Path | None) -> None:
    """Populate registry tables (agents, prompts, tools, MCP, providers, KBs).

    Idempotent — re-running is a no-op if any of these tables already
    contain rows (the engine seeder skips populated tables).
    """
    import asyncio

    console.print()
    console.print(
        Panel(
            "  Seeding registry tables (agents, prompts, tools, MCP, providers, KBs)\n"
            f"  Source: [cyan]{examples_dir or '<repo>/examples/seed'}[/cyan]\n"
            "  Idempotent — only empty tables are populated.",
            title="Seeding Registry",
            border_style="blue",
            padding=(0, 2),
        )
    )

    try:
        from api.database import async_session
        from engine.seed import seed_registries
    except Exception as exc:
        console.print(f"  [red]✗ Could not import seeder:[/red] {exc}")
        console.print("  [dim]Is the API package importable from your venv?[/dim]")
        raise typer.Exit(1) from exc

    async def _go() -> None:
        async with async_session() as session:
            report = await seed_registries(session, examples_dir=examples_dir)

        if report.total_inserted == 0 and not report.errors:
            console.print(
                "  [yellow]No new rows inserted[/yellow] — all registry tables already populated."
            )
        else:
            for table, count in report.seeded.items():
                console.print(f"  [green]✓[/green] {table:18} +{count}")
            for table, reason in report.skipped.items():
                console.print(f"  [dim]·[/dim] {table:18} skipped ({reason})")
            for err in report.errors:
                console.print(f"  [red]✗[/red] {err}")
        console.print()

    asyncio.run(_go())
