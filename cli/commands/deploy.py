"""agentbreeder deploy — the core command.

Runs ``engine.builder.DeployEngine`` in-process with rich step-by-step
progress: parse → RBAC → resolve → build → provision → deploy → health →
register.
"""

from __future__ import annotations

import asyncio
import json as json_lib
import sys
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
        help=(
            "Deploy target: local | gcp | cloud-run | aws | ecs-fargate"
            " | azure | container-apps | kubernetes | eks | gke | aks"
            " | claude-managed"
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for CI/scripting)",
    ),
    local: bool = typer.Option(
        False,
        "--local",
        hidden=True,
        help="Accepted for backwards compatibility; deploys always run in-process.",
    ),
    provision: bool = typer.Option(
        False,
        "--provision",
        "-p",
        help=(
            "Greenfield-provision the cloud footprint (network, registry, cluster/"
            "environment, IAM) for a fresh account before deploying. Supports AWS, "
            "GCP, and Azure."
        ),
    ),
) -> None:
    """Deploy an agent from an agent.yaml configuration file."""
    _deploy_local(config_path, target, json_output, provision)


def _deploy_local(
    config_path: Path, target: str, json_output: bool, provision: bool = False
) -> None:
    """Run the deploy engine in-process.

    When ``provision`` is set, the engine greenfield-provisions the cloud
    footprint before deploying (AWS, GCP, or Azure; #537, parity #505).
    """
    if not json_output:
        provision_note = " [yellow](+ greenfield provisioning)[/yellow]" if provision else ""
        console.print()
        console.print(
            Panel(
                f"[bold]Deploying[/bold] {config_path.name} → [cyan]{target}[/cyan]"
                f"{provision_note}",
                title="AgentBreeder",
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
                console.print(f"\033[A  {icon} {step.name}")
            elif step.status == "failed":
                console.print(f"\033[A  {icon} [red]{step.name} — FAILED[/red]")
        steps.append(step)

    engine = DeployEngine(on_step=on_step)

    try:
        result = asyncio.run(
            engine.deploy(config_path=config_path, target=target, provision=provision)
        )

        if json_output:
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
