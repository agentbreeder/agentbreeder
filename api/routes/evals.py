"""Evaluation Framework API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from api.models.schemas import ApiMeta, ApiResponse
from api.services.eval_service import get_eval_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/eval", tags=["evals"])


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


@router.get("/datasets")
async def list_datasets(
    team: str | None = Query(None),
    agent_id: str | None = Query(None),
) -> ApiResponse[list]:
    """List evaluation datasets."""
    store = get_eval_store()
    datasets = store.list_datasets(team=team, agent_id=agent_id)
    return ApiResponse(data=datasets, meta=ApiMeta(total=len(datasets)))


@router.post("/datasets", status_code=201)
async def create_dataset(body: dict) -> ApiResponse[dict]:
    """Create a new evaluation dataset."""
    store = get_eval_store()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    try:
        dataset = store.create_dataset(
            name=name,
            description=body.get("description", ""),
            agent_id=body.get("agent_id"),
            version=body.get("version", "1.0.0"),
            fmt=body.get("format", "jsonl"),
            team=body.get("team", "default"),
            tags=body.get("tags", []),
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return ApiResponse(data=dataset)


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str) -> ApiResponse[dict]:
    """Get a dataset by ID, including row count."""
    store = get_eval_store()
    dataset = store.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return ApiResponse(data=dataset)


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str) -> ApiResponse[dict]:
    """Delete a dataset and all related rows, runs, and results."""
    store = get_eval_store()
    deleted = store.delete_dataset(dataset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return ApiResponse(data={"deleted": True, "dataset_id": dataset_id})


# ---------------------------------------------------------------------------
# Dataset Rows
# ---------------------------------------------------------------------------


@router.post("/datasets/{dataset_id}/rows", status_code=201)
async def add_rows(dataset_id: str, body: dict) -> ApiResponse[list]:
    """Add rows to a dataset."""
    store = get_eval_store()
    rows = body.get("rows", [])
    if not rows:
        raise HTTPException(status_code=400, detail="rows list is required and cannot be empty")

    try:
        created = store.add_rows(dataset_id, rows)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return ApiResponse(data=created, meta=ApiMeta(total=len(created)))


@router.get("/datasets/{dataset_id}/rows")
async def list_rows(
    dataset_id: str,
    tag: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ApiResponse[list]:
    """List rows in a dataset with optional filtering."""
    store = get_eval_store()
    rows = store.list_rows(dataset_id, tag=tag, limit=limit, offset=offset)
    return ApiResponse(data=rows, meta=ApiMeta(total=len(rows)))


@router.post("/datasets/{dataset_id}/import", status_code=201)
async def import_jsonl(dataset_id: str, body: dict) -> ApiResponse[dict]:
    """Import rows from JSONL content."""
    store = get_eval_store()
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content (JSONL string) is required")

    try:
        count = store.import_jsonl(dataset_id, content)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSONL: {e}") from e

    return ApiResponse(data={"imported": count, "dataset_id": dataset_id})


@router.get("/datasets/{dataset_id}/export")
async def export_jsonl(dataset_id: str) -> PlainTextResponse:
    """Export dataset rows as JSONL."""
    store = get_eval_store()
    dataset = store.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    content = store.export_jsonl(dataset_id)
    return PlainTextResponse(content=content, media_type="application/jsonl")


# ---------------------------------------------------------------------------
# Eval Runs
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=201)
async def create_run(body: dict) -> ApiResponse[dict]:
    """Create and execute an eval run."""
    store = get_eval_store()

    agent_name = body.get("agent_name")
    dataset_id = body.get("dataset_id")
    if not agent_name or not dataset_id:
        raise HTTPException(status_code=400, detail="agent_name and dataset_id are required")

    try:
        run = store.create_run(
            agent_name=agent_name,
            dataset_id=dataset_id,
            config=body.get("config", {}),
            agent_id=body.get("agent_id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # Execute the run (simulated)
    try:
        result = store.execute_run(run["id"])
    except Exception as e:
        store.update_run_status(run["id"], "failed")
        raise HTTPException(status_code=500, detail=f"Run execution failed: {e}") from e

    return ApiResponse(data=result)


@router.get("/runs")
async def list_runs(
    agent_name: str | None = Query(None),
    dataset_id: str | None = Query(None),
) -> ApiResponse[list]:
    """List eval runs."""
    store = get_eval_store()
    runs = store.list_runs(agent_name=agent_name, dataset_id=dataset_id)
    return ApiResponse(data=runs, meta=ApiMeta(total=len(runs)))


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> ApiResponse[dict]:
    """Get a run with its results."""
    store = get_eval_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    results = store.get_results(run_id)
    run["results"] = results
    return ApiResponse(data=run)


@router.delete("/runs/{run_id}")
async def cancel_run(run_id: str) -> ApiResponse[dict]:
    """Cancel a run (mark as cancelled)."""
    store = get_eval_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run["status"] in ("completed", "failed", "cancelled"):
        status = run["status"]
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel run in '{status}' state"
        )

    updated = store.update_run_status(run_id, "cancelled")
    return ApiResponse(data=updated)


# ---------------------------------------------------------------------------
# Scores & Comparison
# ---------------------------------------------------------------------------


@router.get("/scores/trend")
async def get_score_trend(
    agent_name: str = Query(..., description="Agent name to get trend for"),
    metric: str = Query("correctness"),
    limit: int = Query(20, ge=1, le=100),
) -> ApiResponse[list]:
    """Get score trend for an agent over recent runs."""
    store = get_eval_store()
    trend = store.get_score_trend(agent_name=agent_name, metric=metric, limit=limit)
    return ApiResponse(data=trend, meta=ApiMeta(total=len(trend)))


@router.get("/scores/compare")
async def compare_runs(
    run_a: str = Query(..., description="First run ID"),
    run_b: str = Query(..., description="Second run ID"),
) -> ApiResponse[dict]:
    """Compare two eval runs side-by-side."""
    store = get_eval_store()
    try:
        comparison = store.compare_runs(run_a, run_b)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return ApiResponse(data=comparison)
