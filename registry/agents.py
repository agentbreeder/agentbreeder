"""Agent registry service — the ONLY place that writes to the agents table."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field
from ruamel.yaml import YAML
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Agent
from api.models.enums import AgentStatus
from engine.config_parser import AgentConfig, FrameworkType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YAML validation helpers
# ---------------------------------------------------------------------------


class ConfigError(BaseModel):
    """A single validation error or warning."""

    path: str
    message: str
    suggestion: str = ""


class YamlValidationResult(BaseModel):
    """Result of validate_config_yaml()."""

    valid: bool
    errors: list[ConfigError] = Field(default_factory=list)
    warnings: list[ConfigError] = Field(default_factory=list)
    parsed: dict[str, Any] | None = None


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
_KNOWN_FRAMEWORKS = {f.value for f in FrameworkType}
_KNOWN_CLOUDS = {"local", "aws", "gcp", "kubernetes"}


def validate_config_yaml(yaml_string: str) -> YamlValidationResult:
    """Parse a YAML string and validate required fields.

    Returns structured errors and warnings without touching the database.
    """
    errors: list[ConfigError] = []
    warnings: list[ConfigError] = []

    if not yaml_string.strip():
        return YamlValidationResult(
            valid=False,
            errors=[ConfigError(path="(root)", message="Empty YAML content")],
        )

    # Parse YAML
    yaml = YAML()
    try:
        doc = yaml.load(yaml_string)
    except Exception as exc:
        return YamlValidationResult(
            valid=False,
            errors=[
                ConfigError(
                    path="(root)",
                    message=f"YAML parse error: {exc}",
                    suggestion="Check YAML syntax (indentation, colons, etc.)",
                )
            ],
        )

    if not isinstance(doc, dict):
        return YamlValidationResult(
            valid=False,
            errors=[ConfigError(path="(root)", message="YAML root must be a mapping")],
        )

    raw: dict[str, Any] = dict(doc)

    # Required field checks
    for field in ("name", "version", "team", "owner", "framework"):
        if field not in raw or not raw[field]:
            errors.append(
                ConfigError(
                    path=field,
                    message=f"'{field}' is required",
                    suggestion=f"Add '{field}' to your agent YAML",
                )
            )

    # Name format
    name = raw.get("name", "")
    if name and not _NAME_RE.match(str(name)):
        errors.append(
            ConfigError(
                path="name",
                message="Name must be lowercase alphanumeric with hyphens",
                suggestion="Example: my-agent-1",
            )
        )

    # Version semver
    version = str(raw.get("version", ""))
    if version and not _SEMVER_RE.match(version):
        errors.append(
            ConfigError(
                path="version",
                message="Version must be semantic versioning (e.g., 1.0.0)",
                suggestion="Use format: MAJOR.MINOR.PATCH",
            )
        )

    # Model
    model = raw.get("model")
    if not model or not isinstance(model, dict) or not model.get("primary"):
        errors.append(
            ConfigError(
                path="model.primary",
                message="A primary model is required",
                suggestion="Add model.primary (e.g., claude-sonnet-4)",
            )
        )

    # Framework
    fw = raw.get("framework", "")
    if fw and str(fw) not in _KNOWN_FRAMEWORKS:
        errors.append(
            ConfigError(
                path="framework",
                message=f"Unknown framework '{fw}'",
                suggestion=f"Must be one of: {', '.join(sorted(_KNOWN_FRAMEWORKS))}",
            )
        )

    # Deploy section
    deploy = raw.get("deploy")
    if not deploy or not isinstance(deploy, dict):
        errors.append(
            ConfigError(
                path="deploy",
                message="Deploy configuration is required",
                suggestion="Add deploy.cloud (e.g., local, aws, gcp)",
            )
        )
    elif deploy.get("cloud") and str(deploy["cloud"]) not in _KNOWN_CLOUDS:
        errors.append(
            ConfigError(
                path="deploy.cloud",
                message=f"Unknown cloud target '{deploy['cloud']}'",
                suggestion=f"Must be one of: {', '.join(sorted(_KNOWN_CLOUDS))}",
            )
        )

    # Warnings
    tools = raw.get("tools")
    if not tools or (isinstance(tools, list) and len(tools) == 0):
        warnings.append(
            ConfigError(path="tools", message="No tools defined")
        )

    prompts = raw.get("prompts")
    if not prompts or (isinstance(prompts, dict) and not prompts.get("system")):
        warnings.append(
            ConfigError(path="prompts.system", message="No system prompt defined")
        )

    guardrails = raw.get("guardrails")
    if not guardrails or (isinstance(guardrails, list) and len(guardrails) == 0):
        warnings.append(
            ConfigError(path="guardrails", message="No guardrails enabled")
        )

    if not raw.get("description"):
        warnings.append(
            ConfigError(path="description", message="Description is empty")
        )

    return YamlValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        parsed=raw if len(errors) == 0 else None,
    )


async def create_from_yaml(session: AsyncSession, yaml_string: str) -> Agent:
    """Parse YAML and create/update an agent entry in the registry.

    Raises ValueError if validation fails.
    """
    result = validate_config_yaml(yaml_string)
    if not result.valid or result.parsed is None:
        messages = "; ".join(e.message for e in result.errors)
        raise ValueError(f"Validation failed: {messages}")

    raw = result.parsed

    # Build an AgentConfig for the registry
    model_data = raw.get("model", {})
    deploy_data = raw.get("deploy", {})

    config = AgentConfig(
        name=raw["name"],
        version=str(raw["version"]),
        description=raw.get("description", ""),
        team=raw["team"],
        owner=raw["owner"],
        framework=FrameworkType(raw["framework"]),
        model=model_data,
        deploy=deploy_data,
        tags=raw.get("tags", []),
        tools=raw.get("tools", []),
        prompts=raw.get("prompts", {}),
        guardrails=raw.get("guardrails", []),
    )

    agent = await AgentRegistry.register(session, config, endpoint_url="")
    return agent


class AgentRegistry:
    """Service class for agent CRUD operations in the registry."""

    @staticmethod
    async def register(
        session: AsyncSession,
        config: AgentConfig,
        endpoint_url: str,
    ) -> Agent:
        """Register or update an agent after successful deployment."""
        # Check if agent already exists
        stmt = select(Agent).where(Agent.name == config.name)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()

        if agent:
            agent.version = config.version
            agent.description = config.description
            agent.team = config.team
            agent.owner = config.owner
            agent.framework = config.framework.value
            agent.model_primary = config.model.primary
            agent.model_fallback = config.model.fallback
            agent.endpoint_url = endpoint_url
            agent.status = AgentStatus.running
            agent.tags = config.tags
            agent.config_snapshot = config.model_dump(mode="json")
            logger.info("Updated agent '%s' in registry", config.name)
        else:
            agent = Agent(
                name=config.name,
                version=config.version,
                description=config.description,
                team=config.team,
                owner=config.owner,
                framework=config.framework.value,
                model_primary=config.model.primary,
                model_fallback=config.model.fallback,
                endpoint_url=endpoint_url,
                status=AgentStatus.running,
                tags=config.tags,
                config_snapshot=config.model_dump(mode="json"),
            )
            session.add(agent)
            logger.info("Registered new agent '%s' in registry", config.name)

        await session.flush()
        return agent

    @staticmethod
    async def get(session: AsyncSession, name: str) -> Agent | None:
        """Get an agent by name."""
        stmt = select(Agent).where(Agent.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
        """Get an agent by ID."""
        stmt = select(Agent).where(Agent.id == agent_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list(
        session: AsyncSession,
        team: str | None = None,
        framework: str | None = None,
        status: AgentStatus | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Agent], int]:
        """List agents with optional filters. Returns (agents, total_count)."""
        stmt = select(Agent)

        if team:
            stmt = stmt.where(Agent.team == team)
        if framework:
            stmt = stmt.where(Agent.framework == framework)
        if status:
            stmt = stmt.where(Agent.status == status)

        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        # Paginate
        stmt = stmt.order_by(Agent.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        agents = list(result.scalars().all())

        return agents, total

    @staticmethod
    async def update_status(
        session: AsyncSession, agent_id: uuid.UUID, status: AgentStatus
    ) -> None:
        """Update an agent's status."""
        stmt = select(Agent).where(Agent.id == agent_id)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()
        if agent:
            agent.status = status
            await session.flush()

    @staticmethod
    async def search(
        session: AsyncSession,
        query: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Agent], int]:
        """Search agents by name, description, team, or tags."""
        pattern = f"%{query}%"
        stmt = select(Agent).where(
            or_(
                Agent.name.ilike(pattern),
                Agent.description.ilike(pattern),
                Agent.team.ilike(pattern),
                Agent.framework.ilike(pattern),
            )
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Agent.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        agents = list(result.scalars().all())

        return agents, total

    @staticmethod
    async def delete(session: AsyncSession, name: str) -> bool:
        """Soft-delete (archive) an agent by setting status to stopped."""
        stmt = select(Agent).where(Agent.name == name)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()
        if agent:
            agent.status = AgentStatus.stopped
            await session.flush()
            return True
        return False
