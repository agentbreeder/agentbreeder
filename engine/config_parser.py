"""AgentBreeder configuration parser.

Parses and validates agent.yaml files into typed AgentConfig objects.
This is the foundation of the deploy pipeline — everything depends on it.
"""

from __future__ import annotations

import enum
import json
import re
from pathlib import Path
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from ruamel.yaml import YAML

SCHEMA_PATH = Path(__file__).parent / "schema" / "agent.schema.json"


class FrameworkType(enum.StrEnum):
    langgraph = "langgraph"
    crewai = "crewai"
    claude_sdk = "claude_sdk"
    openai_agents = "openai_agents"
    google_adk = "google_adk"
    custom = "custom"


class LanguageType(enum.StrEnum):
    python = "python"
    node = "node"
    # Tier-2 polyglot SDKs — Track I (#165). Go is shipped; Kotlin/Rust/.NET
    # are in flight but accepted by the parser so the schema and parser stay
    # in lock-step with engine/schema/agent.schema.json.
    go = "go"
    kotlin = "kotlin"
    rust = "rust"
    csharp = "csharp"


class AgentType(enum.StrEnum):
    agent = "agent"
    mcp_server = "mcp-server"


class RuntimeConfig(BaseModel):
    language: LanguageType
    framework: str
    version: str | None = None
    entrypoint: str | None = None


class CloudType(enum.StrEnum):
    aws = "aws"
    azure = "azure"
    gcp = "gcp"
    kubernetes = "kubernetes"
    local = "local"
    claude_managed = "claude-managed"


class Visibility(enum.StrEnum):
    public = "public"
    team = "team"
    private = "private"


class ModelConfig(BaseModel):
    primary: str
    fallback: str | None = None
    gateway: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class GatewayConfig(BaseModel):
    """Per-gateway configuration block (Track H / #164).

    Lets ``agent.yaml`` (and Track A's ``workspace.yaml``) override the
    catalog default for a gateway — typically the URL of a self-hosted
    LiteLLM proxy or a regional OpenRouter endpoint.

    All fields are optional. Missing fields fall back to the catalog
    default and the env-var declared in ``catalog.yaml``.

    TODO(track-a): the canonical home for this block is ``workspace.yaml``;
    repeating it per-agent is a stop-gap until Track A ships #146.
    """

    url: str | None = None
    api_key_env: str | None = None
    fallback_policy: str | None = (
        None  # "fastest" | "cheapest" | "first" — advisory, not enforced yet
    )
    default_headers: dict[str, str] = Field(default_factory=dict)


class ToolRef(BaseModel):
    ref: str | None = None
    name: str | None = None
    type: str | None = None
    description: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}


class KnowledgeBaseRef(BaseModel):
    ref: str


class SubagentRef(BaseModel):
    """Reference to a subagent for A2A communication."""

    ref: str
    name: str | None = None
    description: str | None = None

    @property
    def slug(self) -> str:
        """Agent name derived from ref (e.g., 'agents/summarizer' -> 'summarizer')."""
        return self.name or self.ref.split("/")[-1]


class McpServerRef(BaseModel):
    """Reference to an MCP server to attach as a sidecar."""

    ref: str
    transport: str = "stdio"


class MemoryConfig(BaseModel):
    """Memory configuration — references to memory.yaml store entries."""

    stores: list[str] = Field(default_factory=list)


class PromptsConfig(BaseModel):
    system: str | None = None


class ScalingConfig(BaseModel):
    min: int = 1
    max: int = 10
    target_cpu: int = 70


class ResourceConfig(BaseModel):
    cpu: str = "0.5"
    memory: str = "1Gi"


class DeployConfig(BaseModel):
    cloud: CloudType
    runtime: str | None = None
    region: str | None = None
    scaling: ScalingConfig = Field(default_factory=ScalingConfig)
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    env_vars: dict[str, str] = Field(default_factory=dict)
    secrets: list[str] = Field(default_factory=list)


class AccessConfig(BaseModel):
    visibility: Visibility = Visibility.team
    allowed_callers: list[str] = Field(default_factory=list)
    require_approval: bool = False


class GuardrailConfig(BaseModel):
    name: str
    endpoint: str | None = None


class ClaudeSDKThinkingConfig(BaseModel):
    enabled: bool = False
    effort: str = "high"  # "high" | "medium" | "low"  (adaptive mode for Opus 4.6 / Sonnet 4.6)


class ClaudeSDKRoutingConfig(BaseModel):
    provider: str = "anthropic"  # "anthropic" | "vertex_ai" | "bedrock"
    project_id: str | None = None  # GCP project ID (required for vertex_ai)
    region: str | None = None  # Cloud region (required for vertex_ai / bedrock)


class ClaudeSDKConfig(BaseModel):
    thinking: ClaudeSDKThinkingConfig = Field(default_factory=ClaudeSDKThinkingConfig)
    prompt_caching: bool = False
    routing: ClaudeSDKRoutingConfig = Field(default_factory=ClaudeSDKRoutingConfig)


class CrewAIConfig(BaseModel):
    """Optional CrewAI-specific configuration block."""

    process: str = Field(
        default="sequential",
        description="Crew execution process. One of: sequential, hierarchical, parallel.",
    )
    manager_llm: str | None = Field(
        default=None,
        description="Model ref for the manager agent. Required when process=hierarchical.",
    )
    verbose: bool = False
    memory: bool = False
    memory_config: dict[str, Any] | None = None

    @field_validator("process")
    @classmethod
    def validate_process(cls, v: str) -> str:
        allowed = {"sequential", "hierarchical", "parallel"}
        if v not in allowed:
            raise ValueError(f"crewai.process must be one of {sorted(allowed)}, got {v!r}")
        return v


class ADKSessionBackend(enum.StrEnum):
    memory = "memory"
    database = "database"
    vertex_ai = "vertex_ai"


class ADKMemoryService(enum.StrEnum):
    memory = "memory"
    vertex_ai_bank = "vertex_ai_bank"
    vertex_ai_rag = "vertex_ai_rag"


class ADKArtifactService(enum.StrEnum):
    memory = "memory"
    gcs = "gcs"


class ADKStreamingMode(enum.StrEnum):
    none = "none"
    sse = "sse"
    bidi = "bidi"


class GoogleADKConfig(BaseModel):
    """Framework-specific configuration for Google ADK agents."""

    session_backend: ADKSessionBackend = ADKSessionBackend.memory
    session_db_url: str | None = None  # required when session_backend=database
    memory_service: ADKMemoryService = ADKMemoryService.memory
    artifact_service: ADKArtifactService = ADKArtifactService.memory
    gcs_bucket: str | None = None  # required when artifact_service=gcs
    streaming: ADKStreamingMode = ADKStreamingMode.none

    @model_validator(mode="after")
    def check_backend_deps(self) -> GoogleADKConfig:
        if self.session_backend == ADKSessionBackend.database and not self.session_db_url:
            raise ValueError("session_db_url is required when session_backend=database")
        if self.artifact_service == ADKArtifactService.gcs and not self.gcs_bucket:
            raise ValueError("gcs_bucket is required when artifact_service=gcs")
        return self


class ClaudeManagedEnvironmentConfig(BaseModel):
    """Environment configuration for a Claude Managed Agent."""

    networking: Literal["unrestricted", "restricted"] = "unrestricted"


class ClaudeManagedToolConfig(BaseModel):
    """A single tool entry in a Claude Managed Agent definition."""

    type: str = "agent_toolset_20260401"


class ClaudeManagedConfig(BaseModel):
    """Top-level claude_managed: block in agent.yaml.

    Only read when deploy.cloud == "claude-managed".
    No container is built — Anthropic manages the runtime entirely.
    """

    environment: ClaudeManagedEnvironmentConfig = Field(
        default_factory=ClaudeManagedEnvironmentConfig
    )
    tools: list[ClaudeManagedToolConfig] = Field(
        default_factory=lambda: [ClaudeManagedToolConfig()]
    )


class AgentConfig(BaseModel):
    """The complete agent configuration parsed from agent.yaml."""

    spec_version: str = "v1"
    name: str
    version: str
    description: str = ""
    team: str
    owner: EmailStr
    tags: list[str] = Field(default_factory=list)
    type: AgentType = AgentType.agent
    framework: FrameworkType | None = None
    runtime: RuntimeConfig | None = None
    model: ModelConfig
    tools: list[ToolRef] = Field(default_factory=list)
    knowledge_bases: list[KnowledgeBaseRef] = Field(default_factory=list)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    guardrails: list[str | GuardrailConfig] = Field(default_factory=list)
    subagents: list[SubagentRef] = Field(default_factory=list)
    mcp_servers: list[McpServerRef] = Field(default_factory=list)
    memory: MemoryConfig | None = None
    deploy: DeployConfig
    access: AccessConfig = Field(default_factory=AccessConfig)
    # Track H (#164) — per-agent override of catalog gateway settings.
    # Long-term home is workspace.yaml (Track A / #146); accepting it here
    # too means agents can be self-contained for now.
    gateways: dict[str, GatewayConfig] = Field(default_factory=dict)
    crewai: CrewAIConfig | None = None
    google_adk: GoogleADKConfig | None = None
    claude_sdk: ClaudeSDKConfig = Field(default_factory=ClaudeSDKConfig)
    claude_managed: ClaudeManagedConfig | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", v) and len(v) >= 2:
            msg = (
                f"Agent name '{v}' must be lowercase alphanumeric with hyphens, "
                "start and end with alphanumeric (e.g., 'my-agent-1')"
            )
            raise ValueError(msg)
        return v

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        if not re.match(r"^\d+\.\d+\.\d+$", v):
            msg = f"Version '{v}' must be semantic versioning (e.g., '1.0.0')"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_framework_or_runtime(self) -> AgentConfig:
        has_framework = self.framework is not None
        has_runtime = self.runtime is not None
        if has_framework and has_runtime:
            raise ValueError(
                "Only one of 'framework' or 'runtime' may be set, not both. "
                "Use 'framework' for Python agents, 'runtime' for polyglot (Node.js, etc.) agents."
            )
        if not has_framework and not has_runtime:
            raise ValueError(
                "One of 'framework' or 'runtime' must be set. "
                "Use 'framework' for Python agents, 'runtime' for polyglot (Node.js, etc.) agents."
            )
        return self


class ConfigValidationError(BaseModel):
    """A single validation error with location info."""

    path: str
    message: str
    suggestion: str = ""
    line: int | None = None
    column: int | None = None


class ValidationResult(BaseModel):
    """Result of validating an agent.yaml file."""

    valid: bool
    errors: list[ConfigValidationError] = Field(default_factory=list)
    config: AgentConfig | None = None


class ConfigParseError(Exception):
    """Raised when config parsing fails."""

    def __init__(self, errors: list[ConfigValidationError]) -> None:
        self.errors = errors
        messages = [f"  - {e.path}: {e.message}" for e in errors]
        super().__init__("Configuration validation failed:\n" + "\n".join(messages))


def _load_schema() -> dict[str, Any]:
    """Load the JSON Schema for agent.yaml."""
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


def validate_config(path: Path) -> ValidationResult:
    """Validate an agent.yaml file and return structured results.

    This does NOT raise exceptions — it returns a ValidationResult with errors.
    Use parse_config() if you want exceptions on invalid input.
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
                    suggestion=(
                        "Add required fields: name, version, team, owner, framework, model, deploy"
                    ),
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
        message = error.message
        if error.validator == "required":
            missing = error.message.split("'")[1] if "'" in error.message else ""
            suggestion = f"Add '{missing}' field to your agent.yaml"
        elif error.validator == "enum":
            allowed = error.schema.get("enum", [])
            suggestion = f"Must be one of: {', '.join(str(a) for a in allowed)}"
        elif error.validator == "pattern":
            suggestion = f"Must match pattern: {error.schema.get('pattern', '')}"
        elif error.validator == "oneOf":
            if "not valid under any of the given schemas" in message:
                message = "Must specify either 'framework' (for Python agents) or 'runtime' (for polyglot agents), not neither or both"
                suggestion = "Set 'framework' (e.g. langgraph) for Python agents, OR set 'runtime' with language+framework for other languages"

        errors.append(
            ConfigValidationError(
                path=friendly_path,
                message=message,
                suggestion=suggestion,
                line=line,
                column=col,
            )
        )

    if errors:
        return ValidationResult(valid=False, errors=errors)

    # Parse into Pydantic model
    try:
        config = AgentConfig(**raw)
        return ValidationResult(valid=True, config=config)
    except Exception as e:
        return ValidationResult(
            valid=False,
            errors=[
                ConfigValidationError(
                    path="(root)",
                    message=str(e),
                    suggestion="Check your agent.yaml against the specification",
                )
            ],
        )


def parse_config(path: Path) -> AgentConfig:
    """Parse an agent.yaml file into a typed AgentConfig.

    Raises ConfigParseError if validation fails.
    """
    result = validate_config(path)
    if not result.valid or result.config is None:
        raise ConfigParseError(result.errors)
    return result.config
