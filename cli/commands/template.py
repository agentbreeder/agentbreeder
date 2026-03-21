"""agentbreeder template — manage agent templates."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

template_app = typer.Typer(
    name="template",
    help="Manage agent templates.",
    no_args_is_help=True,
)


@template_app.command(name="list")
def list_templates(
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category"),
    framework: str | None = typer.Option(None, "--framework", "-f", help="Filter by framework"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
) -> None:
    """List available templates."""
    asyncio.run(_list_templates(category, framework, status))


async def _list_templates(category: str | None, framework: str | None, status: str | None) -> None:
    import httpx

    params: dict[str, str] = {}
    if category:
        params["category"] = category
    if framework:
        params["framework"] = framework
    if status:
        params["status"] = status

    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        resp = await client.get("/api/v1/templates", params=params)
        if resp.status_code != 200:
            console.print(f"[red]Error: {resp.text}[/red]")
            raise typer.Exit(code=1)
        data = resp.json()

    templates = data.get("data", [])
    if not templates:
        console.print("[dim]No templates found.[/dim]")
        return

    table = Table(title="Templates", border_style="blue")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="dim")
    table.add_column("Category", style="yellow")
    table.add_column("Framework", style="green")
    table.add_column("Status", style="magenta")
    table.add_column("Uses", justify="right")

    for t in templates:
        table.add_row(
            t["name"],
            t["version"],
            t["category"],
            t["framework"],
            t["status"],
            str(t.get("use_count", 0)),
        )

    console.print(table)


@template_app.command(name="create")
def create_template(
    config_path: Path = typer.Argument(
        ..., help="Path to agent.yaml to convert into a template", exists=True, readable=True
    ),
    name: str = typer.Option(..., "--name", "-n", help="Template name"),
    description: str = typer.Option("", "--description", "-d", help="Template description"),
    category: str = typer.Option("other", "--category", "-c", help="Template category"),
    author: str = typer.Option("", "--author", "-a", help="Author name"),
) -> None:
    """Create a template from an existing agent.yaml."""
    from ruamel.yaml import YAML

    yaml = YAML()
    with open(config_path) as f:
        config = yaml.load(f)

    if not isinstance(config, dict):
        console.print("[red]Error: Invalid YAML config[/red]")
        raise typer.Exit(code=1)

    framework = config.get("framework", "custom")

    template_data = {
        "name": name,
        "description": description,
        "category": category,
        "framework": framework,
        "config_template": dict(config),
        "parameters": [],
        "tags": config.get("tags", []),
        "author": author or config.get("owner", "unknown"),
        "team": config.get("team", "default"),
    }

    asyncio.run(_create_template(template_data))


async def _create_template(template_data: dict) -> None:
    import httpx

    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        resp = await client.post("/api/v1/templates", json=template_data)
        if resp.status_code not in (200, 201):
            console.print(f"[red]Error: {resp.text}[/red]")
            raise typer.Exit(code=1)

    console.print(
        Panel(
            f"[bold green]Template created![/bold green] {template_data['name']}",
            title="Template",
            border_style="green",
        )
    )


@template_app.command(name="use")
def use_template(
    template_name: str = typer.Argument(..., help="Template name"),
    output: Path = typer.Option(Path("agent.yaml"), "--output", "-o", help="Output path"),
    params: str = typer.Option("{}", "--params", "-p", help="JSON parameters"),
) -> None:
    """Instantiate a template to generate agent.yaml."""
    try:
        values = json.loads(params)
    except json.JSONDecodeError as exc:
        console.print("[red]Error: --params must be valid JSON[/red]")
        raise typer.Exit(code=1) from exc

    asyncio.run(_use_template(template_name, output, values))


async def _use_template(name: str, output: Path, values: dict) -> None:
    import httpx

    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Find template by name
        resp = await client.get("/api/v1/templates", params={"page": 1, "per_page": 100})
        if resp.status_code != 200:
            console.print(f"[red]Error: {resp.text}[/red]")
            raise typer.Exit(code=1)

        templates = resp.json().get("data", [])
        template = next((t for t in templates if t["name"] == name), None)
        if not template:
            console.print(f"[red]Template '{name}' not found[/red]")
            raise typer.Exit(code=1)

        # Instantiate
        resp = await client.post(
            f"/api/v1/templates/{template['id']}/instantiate",
            json={"values": values},
        )
        if resp.status_code != 200:
            console.print(f"[red]Error: {resp.text}[/red]")
            raise typer.Exit(code=1)

    result = resp.json()["data"]
    output.write_text(result["yaml_content"])
    console.print(
        Panel(
            f"[bold green]Generated![/bold green] {output} from template '{name}'",
            title="Template",
            border_style="green",
        )
    )
