"""agentbreeder orchestration — multi-agent orchestration commands.

Usage:
    agentbreeder orchestration validate <path>
    agentbreeder orchestration deploy <path>
    agentbreeder orchestration list
    agentbreeder orchestration status <name>
    agentbreeder orchestration chat <name>
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from engine.orchestration_parser import validate_orchestration

console = Console()

orchestration_app = typer.Typer(
    name="orchestration",
    help="Multi-agent orchestration commands (validate, deploy, list, status, chat).",
    no_args_is_help=True,
)

API_BASE = os.environ.get("AGENTBREEDER_API_URL", "http://localhost:8000")


def _get_client() -> httpx.Client:
    """Create an httpx client with the configured base URL."""
    return httpx.Client(base_url=API_BASE, timeout=120.0)


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


@orchestration_app.command()
def validate(
    config_path: Path = typer.Argument(
        ...,
        help="Path to orchestration.yaml",
        exists=True,
        readable=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for CI/scripting)",
    ),
) -> None:
    """Validate an orchestration.yaml file against the schema."""
    result = validate_orchestration(config_path)

    if json_output:
        out = {
            "valid": result.valid,
            "errors": [
                {"path": e.path, "message": e.message, "suggestion": e.suggestion}
                for e in result.errors
            ],
        }
        console.print(json.dumps(out, indent=2))
        return

    if result.valid:
        console.print()
        console.print(
            Panel(
                f"[bold green]Valid[/bold green] orchestration config: {config_path.name}",
                title="Orchestration Validation",
                border_style="green",
            )
        )
        console.print()
    else:
        console.print()
        error_lines = []
        for e in result.errors:
            line_info = f" (line {e.line})" if e.line else ""
            error_lines.append(f"  [red]x[/red] {e.path}{line_info}: {e.message}")
            if e.suggestion:
                error_lines.append(f"    [dim]{e.suggestion}[/dim]")
        console.print(
            Panel(
                "\n".join(error_lines),
                title="Validation Errors",
                border_style="red",
            )
        )
        console.print()
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------


@orchestration_app.command()
def deploy(
    config_path: Path = typer.Argument(
        ...,
        help="Path to orchestration.yaml",
        exists=True,
        readable=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for CI/scripting)",
    ),
) -> None:
    """Deploy an orchestration from an orchestration.yaml file."""
    # Validate first
    result = validate_orchestration(config_path)
    if not result.valid:
        if json_output:
            console.print(
                json.dumps(
                    {
                        "error": "Validation failed",
                        "errors": [{"path": e.path, "message": e.message} for e in result.errors],
                    },
                    indent=2,
                )
            )
        else:
            console.print(
                Panel(
                    "[bold red]Validation failed[/bold red] — fix errors before deploying.",
                    border_style="red",
                )
            )
        raise typer.Exit(code=1)

    # Parse the YAML and send to API
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    with open(config_path) as f:
        raw = dict(yaml.load(f))

    try:
        with _get_client() as client:
            # Create
            resp = client.post("/api/v1/orchestrations", json=raw)
            resp.raise_for_status()
            created = resp.json()["data"]

            # Deploy
            resp = client.post(f"/api/v1/orchestrations/{created['id']}/deploy")
            resp.raise_for_status()
            deployed = resp.json()["data"]

    except httpx.ConnectError:
        if json_output:
            sys.stdout.write(json.dumps({"error": f"Cannot connect to API at {API_BASE}"}) + "\n")
        else:
            console.print(
                Panel(
                    f"[red]Cannot connect to API at {API_BASE}[/red]\n"
                    "[dim]Ensure the server is running: uvicorn api.main:app --port 8000[/dim]",
                    border_style="red",
                )
            )
        raise typer.Exit(code=1) from None

    except httpx.HTTPStatusError as exc:
        detail = str(exc)
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            pass
        if json_output:
            sys.stdout.write(json.dumps({"error": detail}) + "\n")
        else:
            console.print(Panel(f"[red]{detail}[/red]", border_style="red"))
        raise typer.Exit(code=1) from None

    if json_output:
        console.print(json.dumps(deployed, indent=2))
    else:
        console.print()
        console.print(
            Panel(
                f"[bold green]Orchestration deployed![/bold green]\n\n"
                f"  Name:     [cyan]{deployed['name']}[/cyan]\n"
                f"  Version:  {deployed['version']}\n"
                f"  Strategy: {deployed['strategy']}\n"
                f"  Agents:   {', '.join(deployed.get('agents_config', {}).keys())}\n"
                f"  Endpoint: [bold]{deployed.get('endpoint_url', 'N/A')}[/bold]",
                title="Deployed",
                border_style="green",
            )
        )
        console.print()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@orchestration_app.command(name="list")
def list_orchestrations(
    team: str | None = typer.Option(None, "--team", "-t", help="Filter by team"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all orchestrations."""
    try:
        with _get_client() as client:
            params: dict[str, str] = {}
            if team:
                params["team"] = team
            if status:
                params["status"] = status
            resp = client.get("/api/v1/orchestrations", params=params)
            resp.raise_for_status()
            items = resp.json()["data"]

    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to API at {API_BASE}[/red]")
        raise typer.Exit(code=1) from None

    if json_output:
        console.print(json.dumps(items, indent=2))
        return

    if not items:
        console.print("\n  [dim]No orchestrations found.[/dim]\n")
        return

    table = Table(title="Orchestrations")
    table.add_column("Name", style="cyan")
    table.add_column("Version")
    table.add_column("Strategy")
    table.add_column("Agents")
    table.add_column("Status")
    table.add_column("Team")

    for item in items:
        agent_names = ", ".join(item.get("agents_config", {}).keys())
        status_style = "green" if item["status"] == "deployed" else "yellow"
        table.add_row(
            item["name"],
            item["version"],
            item["strategy"],
            agent_names,
            f"[{status_style}]{item['status']}[/{status_style}]",
            item.get("team") or "",
        )

    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@orchestration_app.command()
def status(
    name: str = typer.Argument(..., help="Orchestration name"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show status of an orchestration and its agents."""
    try:
        with _get_client() as client:
            # Find by listing and matching name
            resp = client.get("/api/v1/orchestrations")
            resp.raise_for_status()
            items = resp.json()["data"]
            match = next((i for i in items if i["name"] == name), None)

    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to API at {API_BASE}[/red]")
        raise typer.Exit(code=1) from None

    if not match:
        console.print(f"\n  [red]Orchestration '{name}' not found.[/red]\n")
        raise typer.Exit(code=1)

    if json_output:
        console.print(json.dumps(match, indent=2))
        return

    status_style = "green" if match["status"] == "deployed" else "yellow"
    agents_config = match.get("agents_config", {})

    info_lines = [
        f"  Name:        [cyan]{match['name']}[/cyan]",
        f"  Version:     {match['version']}",
        f"  Strategy:    {match['strategy']}",
        f"  Status:      [{status_style}]{match['status']}[/{status_style}]",
        f"  Team:        {match.get('team') or 'N/A'}",
        f"  Owner:       {match.get('owner') or 'N/A'}",
        f"  Endpoint:    {match.get('endpoint_url') or 'N/A'}",
        f"  Description: {match.get('description') or 'N/A'}",
    ]

    console.print()
    console.print(Panel("\n".join(info_lines), title="Orchestration", border_style="blue"))

    if agents_config:
        agent_table = Table(title="Agents")
        agent_table.add_column("Name", style="cyan")
        agent_table.add_column("Ref")
        agent_table.add_column("Fallback")

        for agent_name, agent_data in agents_config.items():
            ref = agent_data.get("ref", "N/A") if isinstance(agent_data, dict) else str(agent_data)
            fallback = agent_data.get("fallback", "") if isinstance(agent_data, dict) else ""
            agent_table.add_row(agent_name, ref, fallback or "-")

        console.print(agent_table)

    console.print()


# ---------------------------------------------------------------------------
# Chat (Interactive)
# ---------------------------------------------------------------------------


@orchestration_app.command()
def chat(
    name: str = typer.Argument(
        ...,
        help="Name of the orchestration to chat with",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-V",
        help="Show agent trace, token counts, and latency",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output each turn as JSON (non-interactive, reads from stdin)",
    ),
) -> None:
    """Start an interactive chat session with an orchestration.

    Sends messages through the orchestration's agent graph and displays
    responses in the terminal. Use --verbose to see agent traces.

    Press Ctrl+C to end the session.

    Examples:
        agentbreeder orchestration chat customer-support-pipeline
        agentbreeder orchestration chat customer-support-pipeline --verbose
        echo "help with billing" | agentbreeder orchestration chat my-orch --json
    """
    if json_output:
        _run_json_mode(name)
        return

    _run_interactive(name, verbose)


def _find_orchestration(name: str) -> dict[str, Any] | None:
    """Find an orchestration by name, return its data dict."""
    try:
        with _get_client() as client:
            resp = client.get("/api/v1/orchestrations")
            resp.raise_for_status()
            items = resp.json()["data"]
            return next((i for i in items if i["name"] == name), None)
    except httpx.ConnectError:
        return None


def _run_interactive(name: str, verbose: bool) -> None:
    """Run the interactive chat loop with Rich output."""
    total_tokens = 0
    total_cost = 0.0
    turn_count = 0

    orch = _find_orchestration(name)
    if not orch:
        console.print(f"\n  [red]Orchestration '{name}' not found or API unavailable.[/red]\n")
        raise typer.Exit(code=1)

    orch_id = orch["id"]

    console.print()
    console.print(
        Panel(
            f"  Chatting with orchestration [bold cyan]{name}[/bold cyan]\n"
            f"  Strategy: [dim]{orch['strategy']}[/dim]\n"
            f"  Agents:   [dim]{', '.join(orch.get('agents_config', {}).keys())}[/dim]"
            "\n\n  [dim]Type your message and press Enter. Ctrl+C to quit.[/dim]",
            title="Orchestration Chat",
            border_style="blue",
        )
    )
    console.print()

    def _on_exit(signum: int, frame: Any) -> None:
        _print_session_summary(turn_count, total_tokens, total_cost)
        raise typer.Exit(code=0)

    signal.signal(signal.SIGINT, _on_exit)

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            _print_session_summary(turn_count, total_tokens, total_cost)
            raise typer.Exit(code=0) from None

        if not user_input:
            continue

        if user_input.lower() in ("/quit", "/exit", "/q"):
            _print_session_summary(turn_count, total_tokens, total_cost)
            raise typer.Exit(code=0)

        if user_input.lower() == "/help":
            _print_chat_help()
            continue

        # Call the orchestration execute API
        try:
            with _get_client() as client:
                resp = client.post(
                    f"/api/v1/orchestrations/{orch_id}/execute",
                    json={"input_message": user_input},
                )
                resp.raise_for_status()
                data = resp.json()["data"]

        except httpx.ConnectError:
            console.print(f"\n  [red]Cannot connect to API at {API_BASE}.[/red]\n")
            raise typer.Exit(code=1) from None

        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.json().get("detail", str(exc))
            except Exception:
                detail = str(exc)
            console.print(f"\n  [red]Error: {detail}[/red]\n")
            continue

        output = data.get("output", "")
        agent_trace = data.get("agent_trace", [])
        tokens = data.get("total_tokens", 0)
        cost = data.get("total_cost", 0.0)
        latency = data.get("total_latency_ms", 0)

        total_tokens += tokens
        total_cost += cost
        turn_count += 1

        # Show agent trace in verbose mode
        if verbose and agent_trace:
            console.print()
            for entry in agent_trace:
                status_color = "green" if entry["status"] == "success" else "red"
                if entry["status"] == "fallback":
                    status_color = "yellow"
                console.print(
                    Panel(
                        f"  [bold]{entry['agent_name']}[/bold] "
                        f"[{status_color}]{entry['status']}[/{status_color}] "
                        f"[dim]({entry['latency_ms']}ms, {entry['tokens']} tokens)[/dim]\n"
                        f"  Output: [dim]{entry['output'][:200]}[/dim]",
                        title="[dim]Agent Trace[/dim]",
                        border_style="dim",
                        padding=(0, 1),
                    )
                )

        # Display output
        console.print()
        console.print(f"[bold blue]{name}:[/bold blue]")
        console.print(f"  {output}")

        if verbose:
            meta_parts = [
                f"strategy={data.get('strategy', '')}",
                f"tokens={tokens}",
                f"cost=${cost:.6f}",
                f"latency={latency}ms",
                f"agents={len(agent_trace)}",
            ]
            console.print(f"  [dim]{' | '.join(meta_parts)}[/dim]")

        console.print()


def _run_json_mode(name: str) -> None:
    """Non-interactive JSON mode: read from stdin, write JSON to stdout."""
    orch = _find_orchestration(name)
    if not orch:
        sys.stdout.write(json.dumps({"error": f"Orchestration '{name}' not found"}) + "\n")
        sys.stdout.flush()
        return

    orch_id = orch["id"]

    for line in sys.stdin:
        user_input = line.strip()
        if not user_input:
            continue

        try:
            with _get_client() as client:
                resp = client.post(
                    f"/api/v1/orchestrations/{orch_id}/execute",
                    json={"input_message": user_input},
                )
                resp.raise_for_status()
                data = resp.json()["data"]

        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            detail = str(exc)
            try:
                if hasattr(exc, "response"):
                    detail = exc.response.json().get("detail", detail)
            except Exception:
                pass
            sys.stdout.write(json.dumps({"error": detail}) + "\n")
            sys.stdout.flush()
            continue

        sys.stdout.write(json.dumps(data, default=str) + "\n")
        sys.stdout.flush()


def _print_session_summary(turns: int, tokens: int, cost: float) -> None:
    """Print a session summary on exit."""
    console.print()
    if turns == 0:
        console.print("  [dim]No messages exchanged.[/dim]")
        console.print()
        return

    summary_lines = [
        f"  Turns:  {turns}",
        f"  Tokens: {tokens:,}",
        f"  Cost:   ${cost:.6f}",
    ]

    console.print(
        Panel(
            "\n".join(summary_lines),
            title="Session Summary",
            border_style="blue",
        )
    )
    console.print()


def _print_chat_help() -> None:
    """Print help for in-chat commands."""
    console.print()
    table = Table(title="Chat Commands", show_header=False)
    table.add_column("Command", style="cyan")
    table.add_column("Description")

    table.add_row("/help", "Show this help message")
    table.add_row("/quit", "End the chat session")
    table.add_row("Ctrl+C", "End the chat session")

    console.print(table)
    console.print()
