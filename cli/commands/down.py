"""agentbreeder down — stop the AgentBreeder platform."""

from __future__ import annotations

import subprocess

import typer
from rich.console import Console
from rich.panel import Panel

from cli.commands.up import _find_compose_dir

console = Console()


def down(
    clean: bool = typer.Option(
        False,
        "--clean",
        help="Also remove volumes (deletes database data)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """Stop AgentBreeder and all its services.

    Use --clean to also remove database volumes (full reset).
    """
    compose_dir = _find_compose_dir()
    if compose_dir is None:
        console.print(
            Panel(
                "[bold red]Could not find docker-compose.yml[/bold red]\n\n"
                "Run this command from the AgentBreeder repository root.",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    compose_file = compose_dir / "docker-compose.yml"
    project_root = compose_dir.parent

    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(project_root),
        "down",
    ]
    if clean:
        cmd.append("--volumes")

    if not json_output:
        if clean:
            console.print("  [yellow]Stopping services and removing volumes...[/yellow]")
        else:
            console.print("  Stopping services...")

    result = subprocess.run(cmd, cwd=str(project_root))  # noqa: S603

    if result.returncode != 0:
        if json_output:
            import json
            import sys

            sys.stdout.write(json.dumps({"status": "error"}) + "\n")
        else:
            console.print("  [red]✗[/red] Failed to stop services")
        raise typer.Exit(code=1)

    if json_output:
        import json
        import sys

        sys.stdout.write(json.dumps({"status": "stopped", "clean": clean}) + "\n")
    else:
        console.print()
        msg = "[bold]AgentBreeder stopped.[/bold]"
        if clean:
            msg += "\n[dim]Volumes removed — database data has been deleted.[/dim]"
        else:
            msg += "\n[dim]Data preserved. Run [bold]agentbreeder up[/bold] to start again.[/dim]"
        console.print(Panel(msg, border_style="blue", padding=(1, 2)))
