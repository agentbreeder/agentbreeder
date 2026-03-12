"""garden deploy — the core command.

Runs the full deploy pipeline with rich progress output.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from engine.builder import DeployEngine, PipelineStep

console = Console()

STEP_ICONS = {
    "pending": "[dim]  [/dim]",
    "running": "[blue]  [/blue]",
    "completed": "[green]  [/green]",
    "failed": "[red]  [/red]",
}


def deploy(
    config_path: Path = typer.Argument(
        ...,
        help="Path to agent.yaml",
        exists=True,
        readable=True,
    ),
    target: str = typer.Option(
        "local",
        "--target",
        "-t",
        help="Deploy target: local, kubernetes, aws, gcp, cloud-run",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for CI/scripting)",
    ),
) -> None:
    """Deploy an agent from an agent.yaml configuration file."""
    if not json_output:
        console.print()
        console.print(
            Panel(
                f"[bold]Deploying[/bold] {config_path.name} → [cyan]{target}[/cyan]",
                title="Agent Garden",
                border_style="blue",
            )
        )
        console.print()

    steps: list[PipelineStep] = []

    def on_step(step: PipelineStep) -> None:
        if not json_output:
            icon = STEP_ICONS.get(step.status, "")
            if step.status == "running":
                console.print(f"  {icon} [blue]{step.name}...[/blue]")
            elif step.status == "completed":
                # Move cursor up and overwrite
                console.print(f"\033[A  {icon} {step.name}")
            elif step.status == "failed":
                console.print(f"\033[A  {icon} [red]{step.name} — FAILED[/red]")
        steps.append(step)

    engine = DeployEngine(on_step=on_step)

    try:
        result = asyncio.run(engine.deploy(config_path=config_path, target=target))

        if json_output:
            import json as json_lib

            console.print(json_lib.dumps(result.model_dump(), indent=2))
        else:
            console.print()
            console.print(
                Panel(
                    f"[bold green]Deploy successful![/bold green]\n\n"
                    f"  Agent:    [cyan]{result.agent_name}[/cyan]\n"
                    f"  Version:  {result.version}\n"
                    f"  Endpoint: [bold]{result.endpoint_url}[/bold]\n\n"
                    f"  Invoke:   [dim]curl -X POST {result.endpoint_url}/invoke "
                    f'-d \'{{"input": {{"message": "hello"}}}}\' '
                    f"-H 'Content-Type: application/json'[/dim]",
                    title="Deployed",
                    border_style="green",
                )
            )
            console.print()

    except Exception as e:
        if json_output:
            import json as json_lib
            import sys

            # Write directly to stdout to avoid Rich formatting in JSON mode
            sys.stdout.write(json_lib.dumps({"error": str(e)}) + "\n")
        else:
            console.print()
            console.print(
                Panel(
                    f"[bold red]Deploy failed[/bold red]\n\n  {e}",
                    title="Error",
                    border_style="red",
                )
            )
            console.print()
        raise typer.Exit(code=1) from None
