"""Base interface for framework-specific runtime builders.

Every supported agent framework implements this interface.
Framework-specific logic MUST stay inside engine/runtimes/ — never leak it elsewhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field

from engine.config_parser import AgentConfig

# Model string prefixes that indicate LiteLLM should handle routing instead of the
# framework's native SDK.  Any model string starting with one of these values will
# have litellm>=1.40.0 added to its requirements and, where applicable, the server
# template will route the call through LiteLLM instead of the native client.
LITELLM_PREFIXES: tuple[str, ...] = (
    "ollama/",
    "groq/",
    "bedrock/",
    "openai/",
    "anthropic/",
    "huggingface/",
    "vertex_ai/",
    "azure/",
    "cohere/",
    "mistral/",
    "together_ai/",
    "replicate/",
)


def _is_litellm_model(model: str) -> bool:
    """Return True if the model string should be routed through LiteLLM."""
    return model.startswith(LITELLM_PREFIXES)


def _get_litellm_requirements() -> list[str]:
    """Return the pip dependencies needed for LiteLLM model routing."""
    return ["litellm>=1.40.0"]


def _should_add_litellm_sdk(config: AgentConfig) -> bool:
    """Return True only when the LiteLLM Python SDK should be injected.

    When ``model.gateway`` is ``"litellm"``, inference goes through the
    LiteLLM proxy via HTTP and the in-process SDK must NOT be added to avoid
    double-routing conflicts.  The SDK is only needed when the agent model
    string carries a LiteLLM prefix (e.g. ``ollama/``, ``groq/``) but is
    NOT routed through the proxy gateway.
    """
    return _is_litellm_model(config.model.primary) and config.model.gateway != "litellm"


class RuntimeValidationError(BaseModel):
    """Structured runtime validation error with optional path hint.

    ``path`` is a JSON-path-style location of the offending field
    (e.g. ``"model.primary"``, ``"deploy.cloud"``) or a filesystem hint
    (e.g. ``"agent.py"``) when applicable. ``message`` is the human-
    readable description; ``suggestion`` is an optional fix hint that the
    dashboard / CLI can render alongside the error.
    """

    message: str
    path: str = ""
    suggestion: str = ""


class RuntimeValidationResult(BaseModel):
    """Result of validating agent code for a specific framework.

    ``errors`` is the legacy plain-string list and is preserved for backwards
    compatibility with existing callers and tests. ``error_items`` carries the
    same errors with structured ``path`` / ``suggestion`` hints so the
    dashboard's YAML editor can point at the right field. Whenever a new
    error is appended, prefer ``add_error`` which keeps both views in sync.
    """

    valid: bool
    errors: list[str] = Field(default_factory=list)
    error_items: list[RuntimeValidationError] = Field(default_factory=list)

    def add_error(self, message: str, path: str = "", suggestion: str = "") -> None:
        """Append a structured error and keep ``errors`` in sync."""
        self.errors.append(message)
        self.error_items.append(
            RuntimeValidationError(message=message, path=path, suggestion=suggestion)
        )
        self.valid = False

    @classmethod
    def from_items(cls, items: list[RuntimeValidationError]) -> RuntimeValidationResult:
        """Build a result from a list of structured errors.

        ``valid`` is set to ``len(items) == 0`` and ``errors`` is derived
        from the items so legacy consumers continue to work.
        """
        return cls(
            valid=len(items) == 0,
            errors=[i.message for i in items],
            error_items=items,
        )


class ContainerImage(BaseModel):
    """Represents a built container image ready for deployment."""

    tag: str
    dockerfile_content: str
    context_dir: Path

    model_config = {"arbitrary_types_allowed": True}


def build_env_block(config: AgentConfig, framework: str) -> str:
    """Generate Dockerfile ENV lines from agent.yaml model + deploy config.

    All string values are sanitised against Dockerfile injection:
    newlines and carriage returns are replaced with spaces, double-quotes are escaped.
    """
    lines: list[str] = [
        f'ENV AGENT_NAME="{config.name}"',
        f'ENV AGENT_VERSION="{config.version}"',
        f'ENV AGENT_FRAMEWORK="{framework}"',
    ]
    if config.model.primary:
        safe_model = config.model.primary.replace("\n", " ").replace("\r", " ").replace('"', '\\"')
        lines.append(f'ENV AGENT_MODEL="{safe_model}"')
    if config.model.temperature is not None:
        lines.append(f"ENV AGENT_TEMPERATURE={config.model.temperature}")
    if config.model.max_tokens is not None:
        lines.append(f"ENV AGENT_MAX_TOKENS={config.model.max_tokens}")
    if config.prompts.system:
        safe = config.prompts.system.replace("\n", " ").replace("\r", " ").replace('"', '\\"')
        lines.append(f'ENV AGENT_SYSTEM_PROMPT="{safe}"')
    for key, val in config.deploy.env_vars.items():
        safe_key = key.replace("\n", "").replace("\r", "").replace(" ", "_")
        safe_val = str(val).replace("\n", " ").replace("\r", " ").replace('"', '\\"')
        lines.append(f'ENV {safe_key}="{safe_val}"')
    return "\n".join(lines)


class RuntimeBuilder(ABC):
    """Abstract base class for framework-specific runtime builders.

    Each framework (LangGraph, CrewAI, etc.) implements this to handle:
    - Validating agent source code for the framework
    - Generating a Dockerfile to build the agent container
    - Specifying the framework's entrypoint command
    - Listing required dependencies
    """

    @staticmethod
    def _check_agent_dir(agent_dir: Path) -> RuntimeValidationResult | None:
        """Precondition: return a failure result if ``agent_dir`` does not exist
        or is not a directory; otherwise return ``None`` so the caller proceeds.

        Subclasses should call this at the top of their ``validate()`` method
        and early-return if the result is non-``None`` (audit finding A11).
        """
        if not agent_dir.exists():
            return RuntimeValidationResult.from_items(
                [
                    RuntimeValidationError(
                        path=str(agent_dir),
                        message=f"Agent directory does not exist: {agent_dir}",
                        suggestion=(
                            "Check the path you passed to `agentbreeder deploy` / "
                            "`agentbreeder validate`."
                        ),
                    )
                ]
            )
        if not agent_dir.is_dir():
            return RuntimeValidationResult.from_items(
                [
                    RuntimeValidationError(
                        path=str(agent_dir),
                        message=f"Agent path is not a directory: {agent_dir}",
                        suggestion="Pass the path to the agent directory, not a file.",
                    )
                ]
            )
        return None

    @abstractmethod
    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        """Validate that the agent directory contains valid code for this framework."""
        ...

    def validate_config(self, config: AgentConfig) -> RuntimeValidationResult:
        """Validate the parsed ``AgentConfig`` *without* an on-disk agent directory.

        Called from ``registry.agents.validate_config_yaml`` so framework-
        specific config constraints surface at YAML-parse time instead of
        only at container build (audit finding A8).

        Default implementation accepts every config — runtimes override to
        flag framework-incompatible model / tool / deploy combinations.
        """
        return RuntimeValidationResult(valid=True, errors=[], error_items=[])

    @abstractmethod
    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage:
        """Generate a Dockerfile and prepare the build context for the agent container."""
        ...

    @abstractmethod
    def get_entrypoint(self, config: AgentConfig) -> str:
        """Return the framework-specific container startup command."""
        ...

    @abstractmethod
    def get_requirements(self, config: AgentConfig) -> list[str]:
        """Return the list of pip dependencies for this framework."""
        ...
