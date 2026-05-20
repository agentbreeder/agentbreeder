"""agentbreeder context — set or inspect the active team context.

The active team is stored in ``~/.agentbreeder/context.json`` and is sent on
every authenticated API call as the ``X-AgentBreeder-Team`` header. It lets a
user who belongs to multiple teams say "act as team X for this session"
without re-passing ``--team`` on every command.
"""

from __future__ import annotations

import typer
from rich.console import Console

from cli import _http

console = Console()

context_app = typer.Typer(
    name="context", help="Manage the active team context for subsequent commands."
)


@context_app.command("use")
def use(team: str = typer.Argument(..., help="Team slug to activate.")) -> None:
    """Set the active team for subsequent CLI calls."""
    _http.set_active_team(team)
    console.print(f"[green]Active team set to[/green] [bold]{team}[/bold]")


@context_app.command("show")
def show() -> None:
    """Print the active team (or note that none is set)."""
    team = _http.get_active_team()
    if team:
        console.print(f"Active team: [bold]{team}[/bold]")
    else:
        console.print("[dim]No active team set. Use `agentbreeder context use <team>`.[/dim]")


@context_app.command("clear")
def clear() -> None:
    """Remove the active team override; fall back to the user's primary team."""
    _http.clear_active_team()
    console.print("[green]Active team cleared.[/green]")
