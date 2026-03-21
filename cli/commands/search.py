"""agentbreeder search — search across all registry entities."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()

REGISTRY_DIR = Path.home() / ".agentbreeder" / "registry"


def search(
    query: str = typer.Argument(..., help="Search query"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Search across all registered agents, tools, models, and prompts."""
    results = []

    # Search local agent registry
    agents_file = REGISTRY_DIR / "agents.json"
    if agents_file.exists():
        registry = json.loads(agents_file.read_text())
        query_lower = query.lower()
        for agent in registry.values():
            searchable = " ".join(
                str(v)
                for v in [
                    agent.get("name", ""),
                    agent.get("description", ""),
                    agent.get("team", ""),
                    agent.get("framework", ""),
                    " ".join(agent.get("tags", [])),
                ]
            ).lower()
            if query_lower in searchable:
                results.append({"type": "agent", **agent})

    if json_output:
        console.print(json.dumps(results, indent=2))
        return

    if not results:
        console.print(f"[dim]No results found for '{query}'[/dim]")
        return

    table = Table(title=f"Search Results for '{query}'")
    table.add_column("Type", style="blue")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Team", style="yellow")
    table.add_column("Status", style="green")

    for r in results:
        table.add_row(
            r.get("type", ""),
            r.get("name", ""),
            r.get("description", "")[:60],
            r.get("team", ""),
            r.get("status", ""),
        )

    console.print()
    console.print(table)
    console.print()
