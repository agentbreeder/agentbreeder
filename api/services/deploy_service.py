"""Deploy service — manages deploy jobs with pipeline step tracking.

Runs the 8-step pipeline asynchronously, updating job status and logs
as each step progresses. Supports cancellation and rollback.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.database import async_session
from api.models.database import Agent, DeployJob
from api.models.enums import AgentStatus, DeployJobStatus
from api.services import litellm_key_service

logger = logging.getLogger(__name__)

# In-memory tracking for active deploy tasks and logs
_active_tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
_job_logs: dict[uuid.UUID, list[dict[str, Any]]] = {}

# Pipeline step definitions (matches the 8-step sacred pipeline)
PIPELINE_STEPS: list[dict[str, Any]] = [
    {"key": "parsing", "label": "Parse & Validate YAML", "duration": 1.2},
    {"key": "rbac", "label": "RBAC Check", "duration": 0.8},
    {"key": "resolving", "label": "Resolve Dependencies", "duration": 1.5},
    {"key": "building", "label": "Build Container", "duration": 3.0},
    {"key": "provisioning", "label": "Provision Infrastructure", "duration": 2.5},
    {"key": "deploying", "label": "Deploy & Health Check", "duration": 2.0},
    {"key": "health_checking", "label": "Health Check", "duration": 1.0},
    {"key": "registering", "label": "Register in Registry", "duration": 0.8},
]

# Map step keys to DeployJobStatus enum values
STEP_TO_STATUS: dict[str, DeployJobStatus] = {
    "parsing": DeployJobStatus.parsing,
    "rbac": DeployJobStatus.parsing,  # RBAC is part of the parsing phase
    "resolving": DeployJobStatus.parsing,
    "building": DeployJobStatus.building,
    "provisioning": DeployJobStatus.provisioning,
    "deploying": DeployJobStatus.deploying,
    "health_checking": DeployJobStatus.health_checking,
    "registering": DeployJobStatus.registering,
}


def _append_log(
    job_id: uuid.UUID,
    level: str,
    message: str,
    step: str | None = None,
) -> None:
    """Append a log entry for a deploy job."""
    if job_id not in _job_logs:
        _job_logs[job_id] = []
    _job_logs[job_id].append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
            "step": step,
        }
    )


def get_job_logs(job_id: uuid.UUID) -> list[dict[str, Any]]:
    """Get all logs for a deploy job."""
    return _job_logs.get(job_id, [])


async def _update_job_status(
    job_id: uuid.UUID,
    status: DeployJobStatus,
    error_message: str | None = None,
    completed: bool = False,
) -> None:
    """Update deploy job status in the database."""
    async with async_session() as session:
        values: dict[str, Any] = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if completed:
            values["completed_at"] = datetime.now(UTC)
        stmt = update(DeployJob).where(DeployJob.id == job_id).values(**values)
        await session.execute(stmt)
        await session.commit()


async def _run_pipeline(job_id: uuid.UUID, agent_name: str, target: str) -> None:
    """Execute the deploy pipeline step by step."""
    _append_log(job_id, "info", f"Starting deployment of '{agent_name}' to {target}")

    for i, step in enumerate(PIPELINE_STEPS):
        step_key = step["key"]
        step_label = step["label"]
        duration = step["duration"]

        # Check for cancellation
        if job_id not in _active_tasks:
            _append_log(job_id, "warn", "Deployment cancelled by user")
            await _update_job_status(
                job_id, DeployJobStatus.failed, error_message="Cancelled by user", completed=True
            )
            return

        # Update status
        db_status = STEP_TO_STATUS[step_key]
        await _update_job_status(job_id, db_status)
        _append_log(job_id, "info", f"[{i + 1}/8] {step_label}...", step=step_key)

        # Simulate step execution with progress logs
        elapsed = 0.0
        tick = 0.3
        while elapsed < duration:
            await asyncio.sleep(tick)
            elapsed += tick

            # Add detailed logs for each step
            sk = step_key
            first_tick = elapsed < tick * 2
            if sk == "parsing" and first_tick:
                msg = "Validating agent.yaml schema"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "rbac" and first_tick:
                msg = "Checking RBAC permissions for team"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "resolving" and first_tick:
                msg = "Resolving tool/model refs from registry"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "building" and first_tick:
                msg = "Building container image with runtime"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "building" and abs(elapsed - tick * 4) < 0.01:
                msg = "Injecting observability sidecar"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "provisioning" and first_tick:
                if target == "local":
                    msg = "Creating Docker Compose config"
                else:
                    msg = f"Provisioning {target} infra"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "deploying" and first_tick:
                msg = "Deploying container to target"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "health_checking" and first_tick:
                msg = "Running health check on agent"
                _append_log(job_id, "info", msg, step=sk)
            elif sk == "registering" and first_tick:
                msg = "Registering agent in registry"
                _append_log(job_id, "info", msg, step=sk)
                # Auto-mint a scoped LiteLLM virtual key for this agent and
                # inject it as LITELLM_VIRTUAL_KEY into the container env.
                try:
                    async with async_session() as ks:
                        # Look up the agent to get team info
                        agent_row = await ks.execute(select(Agent).where(Agent.name == agent_name))
                        ag = agent_row.scalar_one_or_none()
                        if ag:
                            _key_alias, _key_value = (
                                await litellm_key_service.get_or_create_agent_key(
                                    ks,
                                    agent_name=agent_name,
                                    team_id=ag.team or "default",
                                    created_by="deploy-engine",
                                )
                            )
                            if _key_value:
                                # Persist the virtual key into config_snapshot so that
                                # the engine deployer can inject LITELLM_VIRTUAL_KEY
                                # into the container environment at deploy time.
                                snapshot: dict = ag.config_snapshot or {}
                                env_overrides: dict = snapshot.get("env_overrides", {})
                                env_overrides["LITELLM_VIRTUAL_KEY"] = _key_value
                                snapshot["env_overrides"] = env_overrides
                                ag.config_snapshot = snapshot
                                await ks.commit()
                                _append_log(
                                    job_id,
                                    "info",
                                    f"LiteLLM virtual key minted for '{agent_name}' "
                                    f"(alias: {_key_alias}); injected as "
                                    "LITELLM_VIRTUAL_KEY",
                                    step=sk,
                                )
                            else:
                                _append_log(
                                    job_id,
                                    "info",
                                    f"Reusing existing LiteLLM key for '{agent_name}' "
                                    f"(alias: {_key_alias})",
                                    step=sk,
                                )
                except Exception as _ke:
                    logger.warning("Could not mint LiteLLM key for %s: %s", agent_name, _ke)

        _append_log(job_id, "info", f"[{i + 1}/8] {step_label} -- done", step=step_key)

    # Complete
    await _update_job_status(job_id, DeployJobStatus.completed, completed=True)
    if target == "local":
        endpoint = f"http://localhost:8080/{agent_name}"
    else:
        endpoint = f"https://{agent_name}.run.app"
    _append_log(job_id, "info", f"Deploy complete: {agent_name} -> {endpoint}")

    # Update agent status
    async with async_session() as session:
        stmt = (
            select(DeployJob).options(selectinload(DeployJob.agent)).where(DeployJob.id == job_id)
        )
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()
        if job and job.agent:
            job.agent.status = AgentStatus.running
            job.agent.endpoint_url = endpoint
            await session.commit()

    # Clean up active task reference
    _active_tasks.pop(job_id, None)


class DeployService:
    """Service for managing deploy jobs."""

    @staticmethod
    async def create_deploy(
        session: AsyncSession,
        agent_id: uuid.UUID,
        target: str = "local",
        config_yaml: str | None = None,
    ) -> DeployJob:
        """Create a new deploy job and start the pipeline."""
        # Verify agent exists
        agent_stmt = select(Agent).where(Agent.id == agent_id)
        result = await session.execute(agent_stmt)
        agent = result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        # Create deploy job
        job = DeployJob(
            agent_id=agent_id,
            target=target,
            status=DeployJobStatus.pending,
        )
        session.add(job)

        # Update agent status to deploying
        agent.status = AgentStatus.deploying
        await session.flush()

        job_id = job.id
        agent_name = agent.name

        # Initialize logs
        _job_logs[job_id] = []

        # Start async pipeline
        task = asyncio.create_task(_run_pipeline(job_id, agent_name, target))
        _active_tasks[job_id] = task

        return job

    @staticmethod
    async def get_deploy_status(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any] | None:
        """Get deploy job status with logs."""
        stmt = (
            select(DeployJob).options(selectinload(DeployJob.agent)).where(DeployJob.id == job_id)
        )
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()
        if not job:
            return None

        return {
            "id": str(job.id),
            "agent_id": str(job.agent_id),
            "agent_name": job.agent.name if job.agent else None,
            "status": job.status.value,
            "target": job.target,
            "error_message": job.error_message,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "logs": get_job_logs(job.id),
        }

    @staticmethod
    async def cancel_deploy(session: AsyncSession, job_id: uuid.UUID) -> bool:
        """Cancel an in-progress deployment."""
        stmt = select(DeployJob).where(DeployJob.id == job_id)
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()
        if not job:
            return False

        # Cancel the async task
        task = _active_tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()

        # Update job status
        job.status = DeployJobStatus.failed
        job.error_message = "Cancelled by user"
        job.completed_at = datetime.now(UTC)

        _append_log(job_id, "warn", "Deployment cancelled by user")

        return True

    @staticmethod
    async def rollback_deploy(session: AsyncSession, job_id: uuid.UUID) -> bool:
        """Rollback a failed deployment."""
        stmt = (
            select(DeployJob).options(selectinload(DeployJob.agent)).where(DeployJob.id == job_id)
        )
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()
        if not job or job.status != DeployJobStatus.failed:
            return False

        _append_log(job_id, "info", "Rolling back failed deployment...")

        # Reset agent status
        if job.agent:
            job.agent.status = AgentStatus.stopped
            job.agent.endpoint_url = None

        _append_log(job_id, "info", "Rollback complete: agent status reset to stopped")

        return True

    @staticmethod
    async def create_agent_and_deploy(
        session: AsyncSession,
        yaml_content: str,
        target: str = "local",
    ) -> tuple[Agent, DeployJob]:
        """Create an agent from YAML and immediately start a deployment.

        Used by the Agent Builder 'Deploy' action, which sends the YAML
        content directly without first saving the agent.
        """
        # Parse YAML to extract agent fields (simple extraction)
        agent_data = _parse_yaml_fields(yaml_content)

        # Check if agent already exists by name
        existing_stmt = select(Agent).where(Agent.name == agent_data["name"])
        existing_result = await session.execute(existing_stmt)
        agent = existing_result.scalar_one_or_none()

        if agent:
            # Update existing agent
            agent.version = agent_data["version"]
            agent.description = agent_data["description"]
            agent.team = agent_data["team"]
            agent.owner = agent_data["owner"]
            agent.framework = agent_data["framework"]
            agent.model_primary = agent_data["model_primary"]
            agent.model_fallback = agent_data.get("model_fallback")
            agent.tags = agent_data.get("tags", [])
            agent.status = AgentStatus.deploying
        else:
            # Create new agent
            agent = Agent(
                name=agent_data["name"],
                version=agent_data["version"],
                description=agent_data["description"],
                team=agent_data["team"],
                owner=agent_data["owner"],
                framework=agent_data["framework"],
                model_primary=agent_data["model_primary"],
                model_fallback=agent_data.get("model_fallback"),
                tags=agent_data.get("tags", []),
                status=AgentStatus.deploying,
            )
            session.add(agent)

        await session.flush()

        # Create deploy job
        job = DeployJob(
            agent_id=agent.id,
            target=target,
            status=DeployJobStatus.pending,
        )
        session.add(job)
        await session.flush()

        job_id = job.id
        agent_name = agent.name

        # Initialize logs
        _job_logs[job_id] = []

        # Start async pipeline
        task = asyncio.create_task(_run_pipeline(job_id, agent_name, target))
        _active_tasks[job_id] = task

        return agent, job


def _parse_yaml_fields(yaml_content: str) -> dict[str, Any]:
    """Extract agent fields from YAML content (simple parser)."""
    data: dict[str, Any] = {
        "name": "untitled",
        "version": "0.1.0",
        "description": "",
        "team": "default",
        "owner": "unknown",
        "framework": "langgraph",
        "model_primary": "claude-sonnet-4",
        "model_fallback": None,
        "tags": [],
    }

    current_section = ""
    for raw_line in yaml_content.split("\n"):
        line = raw_line.rstrip()
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        if not line.startswith(" ") and not line.startswith("\t"):
            colon_idx = trimmed.find(":")
            if colon_idx == -1:
                continue
            key = trimmed[:colon_idx].strip()
            val = trimmed[colon_idx + 1 :].strip().strip('"')
            current_section = key

            if key == "name":
                data["name"] = val
            elif key == "version":
                data["version"] = val
            elif key == "description":
                data["description"] = val
            elif key == "team":
                data["team"] = val
            elif key == "owner":
                data["owner"] = val
            elif key == "framework":
                data["framework"] = val
            elif key == "tags":
                import re

                match = re.search(r"\[([^\]]*)\]", val)
                if match:
                    data["tags"] = [t.strip() for t in match.group(1).split(",") if t.strip()]
        else:
            colon_idx = trimmed.find(":")
            if colon_idx == -1:
                continue
            key = trimmed[:colon_idx].strip()
            val = trimmed[colon_idx + 1 :].strip().strip('"')
            if current_section == "model":
                if key == "primary":
                    data["model_primary"] = val
                elif key == "fallback":
                    data["model_fallback"] = val or None

    return data
