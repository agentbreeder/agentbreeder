"""agentbreeder describe — show full detail for a registry entity."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

REGISTRY_DIR = Path.home() / ".agentbreeder" / "registry"


def describe(
    name: str = typer.Argument(..., help="Name of the agent to describe"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show full details for a registered agent."""
    registry_file = REGISTRY_DIR / "agents.json"

    if not registry_file.exists():
        console.print("[red]No agents registered. Deploy one first.[/red]")
        raise typer.Exit(code=1)

    registry = json.loads(registry_file.read_text())
    agent = registry.get(name)

    if not agent:
        console.print(f"[red]Agent '{name}' not found in registry.[/red]")
        available = ", ".join(registry.keys())
        if available:
            console.print(f"[dim]Available agents: {available}[/dim]")
        raise typer.Exit(code=1)

    if json_output:
        console.print(json.dumps(agent, indent=2))
        return

    table = Table(show_header=False, border_style="blue", pad_edge=True)
    table.add_column("Field", style="cyan", width=16)
    table.add_column("Value")

    for key, value in agent.items():
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        table.add_row(key, str(value))

    console.print()
    console.print(Panel(table, title=f"Agent: {name}", border_style="blue"))
    console.print()
