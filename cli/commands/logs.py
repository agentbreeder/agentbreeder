"""garden logs — tail logs from a deployed agent."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

STATE_FILE = Path.home() / ".garden" / "state.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"agents": {}}


def _get_agent_names() -> list[str]:
    state = _load_state()
    return list(state.get("agents", {}).keys())


def logs(
    agent_name: str = typer.Argument(..., help="Name of the deployed agent"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of recent lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (stream)"),
    since: str = typer.Option(
        None,
        "--since",
        help="Show logs since duration (e.g., 5m, 1h, 2d)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show logs from a deployed agent.

    Examples:
        garden logs my-agent
        garden logs my-agent --lines 100
        garden logs my-agent --follow
        garden logs my-agent --since 5m
    """
    state = _load_state()
    agents = state.get("agents", {})

    if agent_name not in agents:
        available = _get_agent_names()
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
                msg += "\n\n  No agents deployed yet. Run: [cyan]garden deploy[/cyan]"
            console.print(Panel(msg, title="Error", border_style="red"))
            console.print()
        raise typer.Exit(code=1)

    agent_state = agents[agent_name]
    status = agent_state.get("status", "unknown")

    if status == "stopped":
        if not json_output:
            console.print(
                f"\n  [yellow]Warning:[/yellow] Agent '{agent_name}' is stopped. "
                "Showing last available logs.\n"
            )

    # Parse --since into a datetime
    since_dt = None
    if since:
        since_dt = _parse_since(since)
        if since_dt is None:
            if not json_output:
                console.print("[red]Invalid --since format. Use: 5m, 1h, 2d[/red]")
            raise typer.Exit(code=1)

    if follow:
        _follow_logs(agent_name, since_dt, json_output)
    else:
        _show_logs(agent_name, lines, since_dt, json_output)


def _parse_since(since_str: str) -> datetime | None:
    """Parse a duration string like '5m', '1h', '2d' into a datetime."""
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    since_str = since_str.strip()

    if not since_str:
        return None

    unit_char = since_str[-1].lower()
    if unit_char not in units:
        return None

    try:
        value = int(since_str[:-1])
    except ValueError:
        return None

    delta = timedelta(**{units[unit_char]: value})
    return datetime.now() - delta


def _show_logs(
    agent_name: str,
    lines: int,
    since_dt: datetime | None,
    json_output: bool,
) -> None:
    """Fetch and display logs."""
    from engine.deployers.docker_compose import DockerComposeDeployer

    deployer = DockerComposeDeployer()

    try:
        log_lines = asyncio.run(deployer.get_logs(agent_name, since=since_dt))
    except RuntimeError as e:
        if not json_output:
            console.print(Panel(f"[red]{e}[/red]", title="Error", border_style="red"))
        raise typer.Exit(code=1)

    # Trim to requested line count
    if len(log_lines) > lines:
        log_lines = log_lines[-lines:]

    if json_output:
        import sys

        sys.stdout.write(
            json.dumps({"agent": agent_name, "lines": log_lines, "count": len(log_lines)})
            + "\n"
        )
        return

    if not log_lines or (len(log_lines) == 1 and "not found" in log_lines[0].lower()):
        console.print(f"\n  [dim]No logs available for '{agent_name}'[/dim]\n")
        return

    console.print()
    console.print(f"  [bold]Logs:[/bold] [cyan]{agent_name}[/cyan]  [dim]({len(log_lines)} lines)[/dim]\n")

    for line in log_lines:
        _print_log_line(line)

    console.print()


def _follow_logs(
    agent_name: str,
    since_dt: datetime | None,
    json_output: bool,
) -> None:
    """Stream logs continuously until Ctrl+C."""
    from engine.deployers.docker_compose import DockerComposeDeployer

    deployer = DockerComposeDeployer()

    if not json_output:
        console.print(
            f"\n  [bold]Following logs:[/bold] [cyan]{agent_name}[/cyan]  "
            "[dim](Ctrl+C to stop)[/dim]\n"
        )

    last_count = 0
    try:
        while True:
            try:
                log_lines = asyncio.run(deployer.get_logs(agent_name, since=since_dt))
            except RuntimeError:
                break

            # Print only new lines
            new_lines = log_lines[last_count:]
            for line in new_lines:
                if json_output:
                    import sys

                    sys.stdout.write(json.dumps({"line": line}) + "\n")
                    sys.stdout.flush()
                else:
                    _print_log_line(line)

            last_count = len(log_lines)

            try:
                asyncio.run(asyncio.sleep(1))
            except KeyboardInterrupt:
                break

    except KeyboardInterrupt:
        pass

    if not json_output:
        console.print("\n  [dim]Stopped.[/dim]\n")


def _print_log_line(line: str) -> None:
    """Print a single log line with color coding."""
    lower = line.lower()
    if "error" in lower or "exception" in lower or "traceback" in lower:
        console.print(f"  [red]{line}[/red]")
    elif "warn" in lower:
        console.print(f"  [yellow]{line}[/yellow]")
    elif "info" in lower:
        console.print(f"  [dim]{line}[/dim]")
    else:
        console.print(f"  {line}")
