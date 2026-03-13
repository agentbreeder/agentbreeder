"""garden validate — validate an agent.yaml or orchestration.yaml without deploying."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from ruamel.yaml import YAML

from engine.config_parser import validate_config

console = Console()


def _detect_config_type(path: Path) -> str:
    """Detect whether a YAML file is an agent, orchestration, or other config type."""
    name = path.name.lower()
    if name.startswith("orchestration"):
        return "orchestration"
    if name in ("agent.yaml", "agent.yml"):
        return "agent"
    # Peek at content to detect type
    try:
        yaml = YAML()
        with open(path) as f:
            data = yaml.load(f)
        if isinstance(data, dict):
            if "strategy" in data and "agents" in data:
                return "orchestration"
            # MCP configs have transport/command but no framework/model
            has_mcp_fields = "transport" in data or "command" in data
            has_agent_fields = "framework" in data or "model" in data
            if has_mcp_fields and not has_agent_fields:
                return "mcp"
    except Exception:
        pass
    # Default to agent — let the schema validator catch errors
    return "agent"


def validate(
    config_path: Path = typer.Argument(
        ...,
        help="Path to agent.yaml or orchestration.yaml",
        exists=True,
        readable=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """Validate an agent.yaml or orchestration.yaml configuration file."""
    config_type = _detect_config_type(config_path)

    if config_type == "orchestration":
        from engine.orchestration_parser import validate_orchestration

        result = validate_orchestration(config_path)
    elif config_type in ("mcp", "unknown"):
        # Not an agent or orchestration config — skip with success
        if json_output:
            import json

            output = {
                "valid": True,
                "skipped": True,
                "reason": f"Not an agent config ({config_type})",
            }
            console.print(json.dumps(output, indent=2))
            return
        console.print()
        console.print(
            Panel(
                f"[bold yellow]Skipped[/bold yellow] {config_path.name}"
                " — not an agent or orchestration config.",
                title="Validation",
                border_style="yellow",
            )
        )
        console.print()
        return
    else:
        result = validate_config(config_path)

    if json_output:
        import json

        output = {
            "valid": result.valid,
            "errors": [e.model_dump() for e in result.errors],
        }
        console.print(json.dumps(output, indent=2))
        if not result.valid:
            raise typer.Exit(code=1)
        return

    if result.valid:
        console.print()
        console.print(
            Panel(
                f"[bold green]Valid![/bold green] {config_path.name} passed all checks.",
                title="Validation",
                border_style="green",
            )
        )
        console.print()
    else:
        console.print()
        table = Table(title="Validation Errors", border_style="red")
        table.add_column("Field", style="cyan")
        table.add_column("Error", style="red")
        table.add_column("Suggestion", style="yellow")
        table.add_column("Line", style="dim")

        for error in result.errors:
            table.add_row(
                error.path,
                error.message,
                error.suggestion,
                str(error.line) if error.line else "-",
            )

        console.print(table)
        console.print()
        raise typer.Exit(code=1)
