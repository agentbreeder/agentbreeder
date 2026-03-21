"""agentbreeder teardown — remove a deployed agent."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

STATE_FILE = Path.home() / ".agentbreeder" / "state.json"
REGISTRY_DIR = Path.home() / ".agentbreeder" / "registry"


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"agents": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _update_registry(agent_name: str) -> None:
    """Mark the agent as stopped in the registry."""
    registry_file = REGISTRY_DIR / "agents.json"
    if not registry_file.exists():
        return
    registry = json.loads(registry_file.read_text())
    if agent_name in registry:
        registry[agent_name]["status"] = "stopped"
        registry_file.write_text(json.dumps(registry, indent=2))


def teardown(
    agent_name: str = typer.Argument(..., help="Name of the agent to remove"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Remove a deployed agent and clean up its resources.

    Examples:
        agentbreeder teardown my-agent
        agentbreeder teardown my-agent --force
    """
    state = _load_state()
    agents = state.get("agents", {})

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
                msg += "\n\n  No agents deployed."
            console.print(Panel(msg, title="Error", border_style="red"))
            console.print()
        raise typer.Exit(code=1)

    agent_info = agents[agent_name]
    status = agent_info.get("status", "unknown")

    # Confirmation
    if not force and not json_output:
        console.print()
        console.print(
            f"  [bold]Teardown:[/bold] [cyan]{agent_name}[/cyan]  [dim](status: {status})[/dim]"
        )
        endpoint = agent_info.get("endpoint_url", "")
        if endpoint:
            console.print(f"  [dim]Endpoint: {endpoint}[/dim]")
        console.print()

        confirm = console.input("  [bold]Are you sure? (y/N): [/bold]").strip().lower()
        if confirm != "y":
            console.print("  [dim]Aborted.[/dim]\n")
            raise typer.Exit(code=0)

    # Attempt Docker teardown
    container_removed = False
    if status == "running":
        container_removed = _teardown_container(agent_name, json_output)

    # Update state
    agents[agent_name]["status"] = "stopped"
    _save_state(state)

    # Update registry
    _update_registry(agent_name)

    if json_output:
        import sys

        sys.stdout.write(
            json.dumps(
                {
                    "agent": agent_name,
                    "status": "stopped",
                    "container_removed": container_removed,
                }
            )
            + "\n"
        )
        return

    console.print()
    console.print(
        Panel(
            f"[bold green]Torn down:[/bold green] [cyan]{agent_name}[/cyan]\n\n"
            + (
                "  [green]✓[/green] Container stopped and removed\n"
                if container_removed
                else "  [dim]✓ State updated (no running container found)[/dim]\n"
            )
            + "  [green]✓[/green] Registry updated\n"
            + "  [green]✓[/green] Status set to stopped",
            title="Teardown Complete",
            border_style="green",
        )
    )
    console.print()


def _teardown_container(agent_name: str, json_output: bool) -> bool:
    """Stop and remove the Docker container. Returns True if removed."""
    try:
        from engine.deployers.docker_compose import DockerComposeDeployer

        deployer = DockerComposeDeployer()
        asyncio.run(deployer.teardown(agent_name))
        return True
    except RuntimeError as e:
        # Docker SDK not installed or Docker not running
        if not json_output:
            console.print(f"  [yellow]Warning:[/yellow] Could not stop container: {e}")
        return False
    except Exception as e:
        if not json_output:
            console.print(f"  [yellow]Warning:[/yellow] Container cleanup: {e}")
        return False
