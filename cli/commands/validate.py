"""agentbreeder validate — validate an agent.yaml or orchestration.yaml without deploying."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from ruamel.yaml import YAML

from engine.config_parser import (
    AgentConfig,
    ConfigValidationError,
    ValidationResult,
    validate_config,
)

console = Console()
logger = logging.getLogger(__name__)


def _runtime_files_required(config: AgentConfig) -> bool:
    """Return True when this config's runtime materializes a container image.

    Claude Managed Agents (``deploy.cloud == "claude-managed"``) are
    deployed without building a container — Anthropic manages the runtime,
    so ``agent.py`` / ``requirements.txt`` are not required.
    """
    try:
        cloud = (config.deploy.cloud or "").lower() if config.deploy else ""
    except AttributeError:
        cloud = ""
    return cloud != "claude-managed"


def _validate_agent_files(config: AgentConfig, agent_dir: Path) -> list[ConfigValidationError]:
    """Run the matching runtime's on-disk validate() and translate to CLI errors.

    Mirrors what ``agentbreeder deploy`` does at step 4/6 (the build step) so
    `validate` catches missing ``agent.py`` / ``requirements.txt`` / ``main.go``
    BEFORE the user hits the build phase.

    Returns an empty list when the runtime accepts the directory.
    """
    if not _runtime_files_required(config):
        return []

    # Import lazily so missing runtime deps (e.g. in slim CLI installs that
    # don't ship every framework runtime) don't break schema-only validation.
    try:
        from engine.runtimes.registry import (
            UnsupportedLanguageError,
            get_runtime_from_config,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("runtime registry import failed: %s", exc)
        return []

    try:
        runtime = get_runtime_from_config(config)
    except UnsupportedLanguageError as exc:
        return [
            ConfigValidationError(
                path="runtime.language",
                message=str(exc),
                suggestion=(
                    "Set runtime.language to one of the supported values "
                    "(python, node, go), or remove the runtime block to "
                    "default to Python."
                ),
            )
        ]
    except Exception as exc:
        logger.debug("get_runtime_from_config raised: %s", exc)
        return [
            ConfigValidationError(
                path="framework",
                message=f"Could not resolve runtime for framework/runtime: {exc}",
                suggestion="Check that 'framework' or 'runtime' is set correctly.",
            )
        ]

    try:
        result = runtime.validate(agent_dir, config)
    except Exception as exc:
        logger.debug("runtime.validate raised: %s", exc)
        return [
            ConfigValidationError(
                path=str(agent_dir),
                message=f"Runtime validation crashed: {exc}",
                suggestion=(
                    "This is a bug in the runtime. Please report it with your agent.yaml."
                ),
            )
        ]

    if result.valid:
        return []

    errors: list[ConfigValidationError] = []
    # Prefer the structured ``error_items`` when available — they carry
    # path / suggestion hints. Fall back to the plain string list for
    # legacy runtimes that haven't migrated yet.
    items = list(getattr(result, "error_items", []) or [])
    if items:
        for item in items:
            errors.append(
                ConfigValidationError(
                    path=item.path or str(agent_dir),
                    message=item.message,
                    suggestion=item.suggestion
                    or "Add the missing file next to agent.yaml and re-run validate.",
                )
            )
    else:
        for msg in result.errors or []:
            errors.append(
                ConfigValidationError(
                    path=str(agent_dir),
                    message=msg,
                    suggestion="Add the missing file next to agent.yaml and re-run validate.",
                )
            )
    return errors


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
            has_agent_fields = "framework" in data or "runtime" in data or "model" in data
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

    schema_path = Path(__file__).parent.parent.parent / "engine" / "schema" / "memory.schema.json"
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
    schema_only: bool = typer.Option(
        False,
        "--schema-only",
        help=(
            "Validate the YAML against the schema only — skip the runtime-file"
            " prereq check (agent.py / requirements.txt). Use for standalone"
            " snippet YAMLs that aren't full agent bundles."
        ),
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
        # Runtime-file prereq check — only when schema parse succeeded and we
        # have a parsed AgentConfig. Catches the "validate passes but deploy
        # fails at step 4/6 with 'Missing agent.py'" class of bug.
        # Bypassed via --schema-only for snippet YAMLs that aren't bundles.
        if not schema_only and result.valid and result.config is not None:
            runtime_errors = _validate_agent_files(result.config, config_path.parent)
            if runtime_errors:
                result = ValidationResult(
                    valid=False,
                    errors=runtime_errors,
                    config=result.config,
                )

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
