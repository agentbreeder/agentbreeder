"""garden list — list agents and other registry entities."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()

REGISTRY_DIR = Path.home() / ".garden" / "registry"


def list_entities(
    entity_type: str = typer.Argument(
        "agents",
        help="Entity type to list: agents, tools, models, prompts",
    ),
    team: str | None = typer.Option(None, "--team", help="Filter by team"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List agents, tools, models, or prompts from the registry."""
    if entity_type == "agents":
        _list_agents(team=team, json_output=json_output)
    elif entity_type == "tools":
        _list_tools(json_output=json_output)
    elif entity_type == "models":
        _list_models(json_output=json_output)
    else:
        console.print(f"[yellow]'{entity_type}' listing not yet implemented[/yellow]")


def _list_agents(team: str | None = None, json_output: bool = False) -> None:
    registry_file = REGISTRY_DIR / "agents.json"

    if not registry_file.exists():
        if json_output:
            console.print("[]")
        else:
            console.print("[dim]No agents registered yet. Deploy one with: garden deploy[/dim]")
        return

    registry = json.loads(registry_file.read_text())
    agents = list(registry.values())

    if team:
        agents = [a for a in agents if a.get("team") == team]

    if json_output:
        console.print(json.dumps(agents, indent=2))
        return

    if not agents:
        console.print("[dim]No agents found.[/dim]")
        return

    table = Table(title="Registered Agents")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="dim")
    table.add_column("Team", style="yellow")
    table.add_column("Framework")
    table.add_column("Status", style="green")
    table.add_column("Endpoint", style="dim")

    for agent in agents:
        table.add_row(
            agent.get("name", ""),
            agent.get("version", ""),
            agent.get("team", ""),
            agent.get("framework", ""),
            agent.get("status", ""),
            agent.get("endpoint_url", ""),
        )

    console.print()
    console.print(table)
    console.print()


def _list_tools(json_output: bool = False) -> None:
    registry_file = REGISTRY_DIR / "tools.json"

    if not registry_file.exists():
        if json_output:
            console.print("[]")
        else:
            console.print("[dim]No tools registered yet. Run MCP scanner to discover tools.[/dim]")
        return

    registry = json.loads(registry_file.read_text())
    tools = list(registry.values())

    if json_output:
        console.print(json.dumps(tools, indent=2))
        return

    if not tools:
        console.print("[dim]No tools found.[/dim]")
        return

    table = Table(title="Registered Tools / MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Description")
    table.add_column("Source", style="yellow")
    table.add_column("Endpoint", style="dim")

    for tool in tools:
        table.add_row(
            tool.get("name", ""),
            tool.get("tool_type", ""),
            tool.get("description", ""),
            tool.get("source", ""),
            tool.get("endpoint", "") or "",
        )

    console.print()
    console.print(table)
    console.print()


def _list_models(json_output: bool = False) -> None:
    registry_file = REGISTRY_DIR / "models.json"

    if not registry_file.exists():
        if json_output:
            console.print("[]")
        else:
            console.print("[dim]No models registered yet. Connect LiteLLM to discover models.[/dim]")
        return

    registry = json.loads(registry_file.read_text())
    models = list(registry.values())

    if json_output:
        console.print(json.dumps(models, indent=2))
        return

    if not models:
        console.print("[dim]No models found.[/dim]")
        return

    table = Table(title="Registered Models")
    table.add_column("Name", style="cyan")
    table.add_column("Provider", style="yellow")
    table.add_column("Description")
    table.add_column("Source", style="dim")

    for model in models:
        table.add_row(
            model.get("name", ""),
            model.get("provider", ""),
            model.get("description", ""),
            model.get("source", ""),
        )

    console.print()
    console.print(table)
    console.print()
