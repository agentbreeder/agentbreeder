"""agentbreeder validate — validate an agent.yaml or orchestration.yaml without deploying."""

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
    """Detect whether a YAML file is an agent, orchestration, memory, or other config type."""
    name = path.name.lower()
    if name.startswith("orchestration"):
        return "orchestration"
    if name in ("agent.yaml", "agent.yml"):
        return "agent"
    if name in ("memory.yaml", "memory.yml") or name.startswith("memory."):
        return "memory"
    # Peek at content to detect type
    try:
        yaml = YAML()
        with open(path) as f:
            data = yaml.load(f)
        if isinstance(data, dict):
            if "strategy" in data and "agents" in data:
                return "orchestration"
            # Memory configs have backend + memory_type but no framework/runtime
            has_memory_fields = "memory_type" in data and "backend" in data
            has_agent_fields = (
                "framework" in data or "runtime" in data or "model" in data
            )
            if has_memory_fields and not has_agent_fields:
                return "memory"
            # MCP configs have transport/command but no framework/model
            has_mcp_fields = "transport" in data or "command" in data
            if has_mcp_fields and not has_agent_fields:
                return "mcp"
    except Exception:
        pass
    # Default to agent — let the schema validator catch errors
    return "agent"


def _validate_memory_config(path: Path):
    """Validate a memory.yaml file against engine/schema/memory.schema.json."""
    import json

    import jsonschema

    from engine.config_parser import ConfigValidationError, ValidationResult

    schema_path = (
        Path(__file__).parent.parent.parent / "engine" / "schema" / "memory.schema.json"
    )
    with open(schema_path) as f:
        schema = json.load(f)

    yaml = YAML()
    try:
        with open(path) as f:
            data = yaml.load(f)
    except Exception as exc:
        return ValidationResult(
            valid=False,
            errors=[
                ConfigValidationError(
                    path=str(path),
                    message=f"YAML parse error: {exc}",
                    suggestion="Check YAML syntax (indentation, colons, etc.)",
                )
            ],
        )

    validator = jsonschema.Draft202012Validator(schema)
    errors: list[ConfigValidationError] = []
    for err in validator.iter_errors(data):
        loc = ".".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(
            ConfigValidationError(
                path=loc,
                message=err.message,
                suggestion=err.schema.get("description", ""),
            )
        )
    return ValidationResult(valid=len(errors) == 0, errors=errors)


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
    elif config_type == "memory":
        result = _validate_memory_config(config_path)
    elif config_type in ("mcp", "unknown"):
        # Not an agent or orchestration config — skip with success
        if json_output:
            import json

            output = {
                "valid": True,
                "skipped": True,
                "reason": f"Not an agent config ({config_type})",
            }
            typer.echo(json.dumps(output, indent=2))
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
        typer.echo(json.dumps(output, indent=2))
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
