"""Orchestration API routes — CRUD, validation, deploy, and execution."""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.models.schemas import ApiMeta, ApiResponse
from api.services.orchestration_service import get_orchestration_store
from engine.orchestration_parser import validate_orchestration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrations", tags=["orchestrations"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
async def list_orchestrations(
    team: str | None = Query(None, description="Filter by team"),
    status: str | None = Query(None, description="Filter by status"),
) -> ApiResponse[list[dict[str, Any]]]:
    """List all orchestrations with optional filters."""
    store = get_orchestration_store()
    items = store.list(team=team, status=status)
    return ApiResponse(
        data=items,
        meta=ApiMeta(total=len(items)),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_orchestration(body: dict[str, Any]) -> ApiResponse[dict[str, Any]]:
    """Create an orchestration from a config dict (parsed from YAML)."""
    store = get_orchestration_store()

    name = body.get("name")
    version = body.get("version")
    strategy = body.get("strategy")
    agents = body.get("agents")

    if not all([name, version, strategy, agents]):
        raise HTTPException(
            status_code=400,
            detail="name, version, strategy, and agents are required",
        )

    result = store.create(
        name=name,
        version=version,
        description=body.get("description", ""),
        team=body.get("team"),
        owner=body.get("owner"),
        strategy=strategy,
        agents=agents,
        shared_state=body.get("shared_state"),
        deploy=body.get("deploy"),
        tags=body.get("tags"),
    )
    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# Get Detail
# ---------------------------------------------------------------------------


@router.get("/{orch_id}")
async def get_orchestration(orch_id: str) -> ApiResponse[dict[str, Any]]:
    """Get a single orchestration by ID."""
    store = get_orchestration_store()
    item = store.get(orch_id)
    if not item:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    return ApiResponse(data=item)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@router.put("/{orch_id}")
async def update_orchestration(
    orch_id: str, body: dict[str, Any]
) -> ApiResponse[dict[str, Any]]:
    """Update an orchestration."""
    store = get_orchestration_store()
    result = store.update(orch_id, **body)
    if not result:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{orch_id}")
async def delete_orchestration(orch_id: str) -> ApiResponse[dict[str, str]]:
    """Delete an orchestration."""
    store = get_orchestration_store()
    success = store.delete(orch_id)
    if not success:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    return ApiResponse(data={"deleted": orch_id})


# ---------------------------------------------------------------------------
# Validate YAML
# ---------------------------------------------------------------------------


@router.post("/validate")
async def validate_orchestration_yaml(
    body: dict[str, Any],
) -> ApiResponse[dict[str, Any]]:
    """Validate orchestration YAML content.

    Accepts {"yaml_content": "..."} and validates against the schema.
    """
    yaml_content = body.get("yaml_content", "")
    if not yaml_content:
        raise HTTPException(status_code=400, detail="yaml_content is required")

    # Write to temp file for validation (parser expects a file path)
    with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        tmp_path = Path(f.name)

    try:
        result = validate_orchestration(tmp_path)
        errors = [
            {
                "path": e.path,
                "message": e.message,
                "suggestion": e.suggestion,
            }
            for e in result.errors
        ]
        return ApiResponse(
            data={
                "valid": result.valid,
                "errors": errors,
            }
        )
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------


@router.post("/{orch_id}/deploy")
async def deploy_orchestration(orch_id: str) -> ApiResponse[dict[str, Any]]:
    """Deploy an orchestration (marks as deployed and assigns endpoint)."""
    store = get_orchestration_store()
    result = store.deploy(orch_id)
    if not result:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


@router.post("/{orch_id}/execute")
async def execute_orchestration(
    orch_id: str, body: dict[str, Any]
) -> ApiResponse[dict[str, Any]]:
    """Execute an orchestration — send a message through the agent graph."""
    store = get_orchestration_store()

    input_message = body.get("input_message", body.get("message", ""))
    if not input_message:
        raise HTTPException(status_code=400, detail="input_message is required")

    context = body.get("context", {})

    try:
        result = await store.execute(orch_id, input_message, context)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return ApiResponse(data=result)
