"""garden status — show deploy status for agents."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

STATE_FILE = Path.home() / ".garden" / "state.json"
REGISTRY_DIR = Path.home() / ".garden" / "registry"


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"agents": {}}


def _load_registry() -> dict:
    registry_file = REGISTRY_DIR / "agents.json"
    if registry_file.exists():
        return json.loads(registry_file.read_text())
    return {}


STATUS_STYLES = {
    "running": "[bold green]● running[/bold green]",
    "provisioned": "[blue]◐ provisioned[/blue]",
    "stopped": "[red]○ stopped[/red]",
    "failed": "[bold red]✗ failed[/bold red]",
    "unknown": "[dim]? unknown[/dim]",
}


def status(
    agent_name: str = typer.Argument(
        None,
        help="Agent name (omit to show all agents)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show deploy status of agents.

    Examples:
        garden status              # show all agents
        garden status my-agent     # show one agent's detail
    """
    state = _load_state()
    registry = _load_registry()
    agents = state.get("agents", {})

    if agent_name:
        _show_agent_status(agent_name, agents, registry, json_output)
    else:
        _show_all_status(agents, registry, json_output)


def _show_all_status(agents: dict, registry: dict, json_output: bool) -> None:
    """Show summary status for all agents."""
    if not agents:
        if json_output:
            import sys

            sys.stdout.write("[]\n")
        else:
            console.print("\n  [dim]No agents deployed yet. Run: garden deploy[/dim]\n")
        return

    if json_output:
        import sys

        result = []
        for name, info in agents.items():
            reg = registry.get(name, {})
            result.append({
                "name": name,
                "status": info.get("status", "unknown"),
                "endpoint_url": info.get("endpoint_url", ""),
                "deployed_at": info.get("deployed_at", ""),
                "version": reg.get("version", ""),
                "framework": reg.get("framework", ""),
            })
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return

    table = Table(title="Agent Status")
    table.add_column("Agent", style="cyan")
    table.add_column("Status")
    table.add_column("Endpoint", style="dim")
    table.add_column("Deployed At", style="dim")

    for name, info in agents.items():
        status_str = info.get("status", "unknown")
        styled = STATUS_STYLES.get(status_str, f"[dim]{status_str}[/dim]")
        table.add_row(
            name,
            styled,
            info.get("endpoint_url", ""),
            _format_time(info.get("deployed_at", "")),
        )

    console.print()
    console.print(table)
    console.print()


def _show_agent_status(
    agent_name: str,
    agents: dict,
    registry: dict,
    json_output: bool,
) -> None:
    """Show detailed status for a single agent."""
    if agent_name not in agents:
        available = list(agents.keys())
        if json_output:
            import sys

            sys.stdout.write(
                json.dumps({"error": f"Agent '{agent_name}' not found", "available": available})
                + "\n"
            )
        else:
            console.print()
            msg = f"[bold red]Agent '{agent_name}' not found[/bold red]"
            if available:
                msg += f"\n\n  Available agents: [cyan]{', '.join(available)}[/cyan]"
            else:
                msg += "\n\n  No agents deployed. Run: [cyan]garden deploy[/cyan]"
            console.print(Panel(msg, title="Error", border_style="red"))
            console.print()
        raise typer.Exit(code=1)

    info = agents[agent_name]
    reg = registry.get(agent_name, {})
    status_val = info.get("status", "unknown")

    if json_output:
        import sys

        result = {**info, "name": agent_name}
        result.update({
            "version": reg.get("version", ""),
            "framework": reg.get("framework", ""),
            "team": reg.get("team", ""),
            "model_primary": reg.get("model_primary", ""),
        })
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return

    styled_status = STATUS_STYLES.get(status_val, f"[dim]{status_val}[/dim]")

    # Try to get live container status
    container_status = _get_container_status(agent_name)

    detail_lines = [
        f"  [bold]{agent_name}[/bold]",
        "",
        f"  Status:      {styled_status}",
    ]

    if container_status:
        detail_lines.append(f"  Container:   {container_status}")

    detail_lines.extend([
        f"  Endpoint:    [bold]{info.get('endpoint_url', 'N/A')}[/bold]",
        f"  Deployed:    {_format_time(info.get('deployed_at', ''))}",
    ])

    if info.get("container_id"):
        detail_lines.append(f"  Container ID: [dim]{info['container_id'][:12]}[/dim]")

    if info.get("image_tag"):
        detail_lines.append(f"  Image:       [dim]{info['image_tag']}[/dim]")

    if reg:
        detail_lines.append("")
        detail_lines.append(f"  Version:     {reg.get('version', '')}")
        detail_lines.append(f"  Framework:   {reg.get('framework', '')}")
        detail_lines.append(f"  Team:        {reg.get('team', '')}")
        detail_lines.append(f"  Model:       {reg.get('model_primary', '')}")

    border = "green" if status_val == "running" else "red" if status_val in ("stopped", "failed") else "blue"

    console.print()
    console.print(Panel("\n".join(detail_lines), title="Agent Status", border_style=border))
    console.print()


def _get_container_status(agent_name: str) -> str | None:
    """Try to get live Docker container status."""
    try:
        import docker

        client = docker.from_env()
        container = client.containers.get(f"garden-{agent_name}")
        return f"[green]{container.status}[/green]"
    except Exception:
        return None


def _format_time(iso_str: str) -> str:
    """Format an ISO timestamp for display."""
    if not iso_str:
        return "N/A"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_str
