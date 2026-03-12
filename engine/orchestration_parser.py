"""Agent Garden orchestration configuration parser.

Parses and validates orchestration.yaml files into typed OrchestrationConfig objects.
This enables multi-agent orchestration — routing, sequencing, parallelism, and hierarchy.
"""

from __future__ import annotations

import enum
import json
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel, Field, field_validator
from ruamel.yaml import YAML

from engine.config_parser import (
    ConfigValidationError,
    ResourceConfig,
    ValidationResult,
)

SCHEMA_PATH = Path(__file__).parent / "schema" / "orchestration.schema.json"


class OrchestrationStrategy(enum.StrEnum):
    router = "router"
    sequential = "sequential"
    parallel = "parallel"
    hierarchical = "hierarchical"


class RoutingRule(BaseModel):
    condition: str
    target: str


class AgentRef(BaseModel):
    ref: str
    routes: list[RoutingRule] = Field(default_factory=list)
    fallback: str | None = None


class SharedStateConfig(BaseModel):
    type: str = "dict"
    backend: str = "in_memory"


class OrchestrationDeployConfig(BaseModel):
    target: str = "local"
    resources: ResourceConfig = Field(default_factory=ResourceConfig)


class OrchestrationConfig(BaseModel):
    """The complete orchestration configuration parsed from orchestration.yaml."""

    spec_version: str = "v1"
    name: str
    version: str
    description: str = ""
    team: str | None = None
    owner: str | None = None
    strategy: OrchestrationStrategy
    agents: dict[str, AgentRef]
    shared_state: SharedStateConfig = Field(default_factory=SharedStateConfig)
    deploy: OrchestrationDeployConfig = Field(default_factory=OrchestrationDeployConfig)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        import re

        if len(v) >= 2 and not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", v):
            msg = (
                f"Orchestration name '{v}' must be lowercase alphanumeric with hyphens, "
                "start and end with alphanumeric (e.g., 'my-orchestration-1')"
            )
            raise ValueError(msg)
        return v

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        import re

        if not re.match(r"^\d+\.\d+\.\d+$", v):
            msg = f"Version '{v}' must be semantic versioning (e.g., '1.0.0')"
            raise ValueError(msg)
        return v


class OrchestrationParseError(Exception):
    """Raised when orchestration config parsing fails."""

    def __init__(self, errors: list[ConfigValidationError]) -> None:
        self.errors = errors
        messages = [f"  - {e.path}: {e.message}" for e in errors]
        super().__init__("Orchestration validation failed:\n" + "\n".join(messages))


def _load_schema() -> dict[str, Any]:
    """Load the JSON Schema for orchestration.yaml."""
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _load_yaml(path: Path) -> tuple[dict[str, Any], Any]:
    """Load YAML file, returning both the parsed dict and the ruamel document (for line info)."""
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path) as f:
        doc = yaml.load(f)
    if doc is None:
        return {}, None
    return dict(doc), doc


def _get_line_info(doc: Any, json_path: str) -> tuple[int | None, int | None]:
    """Try to get line/column info for a JSON path from the ruamel document."""
    if doc is None:
        return None, None
    parts = json_path.strip("/").split("/") if json_path else []
    current = doc
    for part in parts:
        if hasattr(current, "__getitem__"):
            try:
                if part.isdigit():
                    current = current[int(part)]
                else:
                    current = current[part]
            except (KeyError, IndexError, TypeError):
                return None, None
        else:
            return None, None
    if hasattr(current, "lc"):
        return current.lc.line + 1, current.lc.col
    return None, None


def _schema_path_to_friendly(path: str) -> str:
    """Convert a JSON Schema path to a friendly dotted path."""
    return path.strip("/").replace("/", ".")


def validate_orchestration(path: Path) -> ValidationResult:
    """Validate an orchestration.yaml file and return structured results.

    This does NOT raise exceptions — it returns a ValidationResult with errors.
    Use parse_orchestration() if you want exceptions on invalid input.
    """
    errors: list[ConfigValidationError] = []

    if not path.exists():
        return ValidationResult(
            valid=False,
            errors=[
                ConfigValidationError(
                    path=str(path),
                    message=f"File not found: {path}",
                    suggestion="Check the file path and try again",
                )
            ],
        )

    # Load YAML
    try:
        raw, doc = _load_yaml(path)
    except Exception as e:
        return ValidationResult(
            valid=False,
            errors=[
                ConfigValidationError(
                    path=str(path),
                    message=f"YAML parse error: {e}",
                    suggestion="Check YAML syntax (indentation, colons, etc.)",
                )
            ],
        )

    if not raw:
        return ValidationResult(
            valid=False,
            errors=[
                ConfigValidationError(
                    path=str(path),
                    message="Empty configuration file",
                    suggestion=("Add required fields: name, version, strategy, agents"),
                )
            ],
        )

    # Validate against JSON Schema for detailed field-level errors
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(raw), key=lambda e: list(e.path)):
        json_path = "/".join(str(p) for p in error.absolute_path)
        friendly_path = _schema_path_to_friendly(json_path) if json_path else "(root)"
        line, col = _get_line_info(doc, json_path)

        suggestion = ""
        if error.validator == "required":
            missing = error.message.split("'")[1] if "'" in error.message else ""
            suggestion = f"Add '{missing}' field to your orchestration.yaml"
        elif error.validator == "enum":
            allowed = error.schema.get("enum", [])
            suggestion = f"Must be one of: {', '.join(str(a) for a in allowed)}"
        elif error.validator == "pattern":
            suggestion = f"Must match pattern: {error.schema.get('pattern', '')}"

        errors.append(
            ConfigValidationError(
                path=friendly_path,
                message=error.message,
                suggestion=suggestion,
                line=line,
                column=col,
            )
        )

    if errors:
        return ValidationResult(valid=False, errors=errors)

    # Parse into Pydantic model
    try:
        OrchestrationConfig(**raw)
        return ValidationResult(valid=True, config=None)
    except Exception as e:
        return ValidationResult(
            valid=False,
            errors=[
                ConfigValidationError(
                    path="(root)",
                    message=str(e),
                    suggestion="Check your orchestration.yaml against the specification",
                )
            ],
        )


def parse_orchestration(path: Path) -> OrchestrationConfig:
    """Parse an orchestration.yaml file into a typed OrchestrationConfig.

    Raises OrchestrationParseError if validation fails.
    """
    result = validate_orchestration(path)
    if not result.valid:
        raise OrchestrationParseError(result.errors)

    # Re-load and parse (validate_orchestration checks validity but
    # stores config=None since OrchestrationConfig != AgentConfig)
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path) as f:
        raw = dict(yaml.load(f))
    return OrchestrationConfig(**raw)
