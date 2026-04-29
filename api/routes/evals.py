"""Evaluation Framework API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.services.eval_service import get_eval_store, seed_community_datasets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/eval", tags=["evals"])


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


@router.get("/datasets")
async def list_datasets(
    _user: User = Depends(get_current_user),
    team: str | None = Query(None),
    agent_id: str | None = Query(None),
) -> ApiResponse[list]:
    """List evaluation datasets."""
    store = get_eval_store()
    datasets = store.list_datasets(team=team, agent_id=agent_id)
    return ApiResponse(data=datasets, meta=ApiMeta(total=len(datasets)))


@router.post("/datasets", status_code=201)
async def create_dataset(
    body: dict, _user: User = Depends(require_role("deployer"))
) -> ApiResponse[dict]:
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
async def get_dataset(
    dataset_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Get a dataset by ID, including row count."""
    store = get_eval_store()
    dataset = store.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return ApiResponse(data=dataset)


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: str, _user: User = Depends(require_role("admin"))
) -> ApiResponse[dict]:
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
async def add_rows(
    dataset_id: str, body: dict, _user: User = Depends(require_role("deployer"))
) -> ApiResponse[list]:
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
    _user: User = Depends(get_current_user),
    tag: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ApiResponse[list]:
    """List rows in a dataset with optional filtering."""
    store = get_eval_store()
    rows = store.list_rows(dataset_id, tag=tag, limit=limit, offset=offset)
    return ApiResponse(data=rows, meta=ApiMeta(total=len(rows)))


@router.post("/datasets/{dataset_id}/import", status_code=201)
async def import_jsonl(
    dataset_id: str, body: dict, _user: User = Depends(require_role("deployer"))
) -> ApiResponse[dict]:
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
async def export_jsonl(
    dataset_id: str, _user: User = Depends(get_current_user)
) -> PlainTextResponse:
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
async def create_run(
    body: dict, _user: User = Depends(require_role("deployer"))
) -> ApiResponse[dict]:
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
    _user: User = Depends(get_current_user),
    agent_name: str | None = Query(None),
    dataset_id: str | None = Query(None),
) -> ApiResponse[list]:
    """List eval runs."""
    store = get_eval_store()
    runs = store.list_runs(agent_name=agent_name, dataset_id=dataset_id)
    return ApiResponse(data=runs, meta=ApiMeta(total=len(runs)))


@router.get("/runs/{run_id}")
async def get_run(run_id: str, _user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Get a run with its results."""
    store = get_eval_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    results = store.get_results(run_id)
    run["results"] = results
    return ApiResponse(data=run)


@router.delete("/runs/{run_id}")
async def cancel_run(run_id: str, _user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Cancel a run (mark as cancelled)."""
    store = get_eval_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run["status"] in ("completed", "failed", "cancelled"):
        status = run["status"]
        raise HTTPException(status_code=400, detail=f"Cannot cancel run in '{status}' state")

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
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """Get score trend for an agent over recent runs."""
    store = get_eval_store()
    trend = store.get_score_trend(agent_name=agent_name, metric=metric, limit=limit)
    return ApiResponse(data=trend, meta=ApiMeta(total=len(trend)))


@router.get("/scores/compare")
async def compare_runs(
    run_a: str = Query(..., description="First run ID"),
    run_b: str = Query(..., description="Second run ID"),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Compare two eval runs side-by-side."""
    store = get_eval_store()
    try:
        comparison = store.compare_runs(run_a, run_b)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return ApiResponse(data=comparison)


# ---------------------------------------------------------------------------
# Eval Badge
# ---------------------------------------------------------------------------


def _generate_badge_svg(label: str, value: str, color: str) -> str:
    """Generate a shields.io-style SVG badge."""
    label_width = len(label) * 7 + 10
    value_width = len(value) * 7 + 10
    total_width = label_width + value_width

    lx = label_width / 2
    vx = label_width + value_width / 2
    font = "DejaVu Sans,Verdana,Geneva,sans-serif"

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{total_width}" height="20">\n'
        f'  <linearGradient id="b" x2="0" y2="100%">\n'
        f'    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>\n'
        f'    <stop offset="1" stop-opacity=".1"/>\n'
        f"  </linearGradient>\n"
        f'  <clipPath id="a">\n'
        f'    <rect width="{total_width}" height="20" rx="3"'
        f' fill="#fff"/>\n'
        f"  </clipPath>\n"
        f'  <g clip-path="url(#a)">\n'
        f'    <rect width="{label_width}" height="20" fill="#555"/>\n'
        f'    <rect x="{label_width}" width="{value_width}"'
        f' height="20" fill="{color}"/>\n'
        f'    <rect width="{total_width}" height="20"'
        f' fill="url(#b)"/>\n'
        f"  </g>\n"
        f'  <g fill="#fff" text-anchor="middle"'
        f' font-family="{font}" font-size="11">\n'
        f'    <text x="{lx}" y="15"'
        f' fill="#010101" fill-opacity=".3">{label}</text>\n'
        f'    <text x="{lx}" y="14">{label}</text>\n'
        f'    <text x="{vx}" y="15"'
        f' fill="#010101" fill-opacity=".3">{value}</text>\n'
        f'    <text x="{vx}" y="14">{value}</text>\n'
        f"  </g>\n"
        f"</svg>"
    )


@router.get("/badge/{agent_name}")
async def get_eval_badge(agent_name: str, _user: User = Depends(get_current_user)) -> Response:
    """Return an SVG badge with the latest eval score for an agent."""
    store = get_eval_store()

    # Find the most recent completed run for this agent
    runs = store.list_runs(agent_name=agent_name)
    completed_runs = [r for r in runs if r["status"] == "completed"]

    if not completed_runs:
        svg = _generate_badge_svg("eval", "no data", "#9f9f9f")
        return Response(content=svg, media_type="image/svg+xml")

    latest_run = completed_runs[0]  # list_runs returns newest first
    metrics = latest_run.get("summary", {}).get("metrics", {})

    # Use correctness as the primary badge metric, fall back to first available
    if "correctness" in metrics:
        score = metrics["correctness"].get("mean", 0.0)
    elif metrics:
        first_metric = next(iter(metrics.values()))
        score = first_metric.get("mean", 0.0)
    else:
        svg = _generate_badge_svg("eval", "no data", "#9f9f9f")
        return Response(content=svg, media_type="image/svg+xml")

    pct = f"{int(score * 100)}%"

    if score >= 0.8:
        color = "#4c1"
    elif score >= 0.6:
        color = "#dfb317"
    else:
        color = "#e05d44"

    svg = _generate_badge_svg("eval", pct, color)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ---------------------------------------------------------------------------
# Scheduled Evaluations
# ---------------------------------------------------------------------------


@router.post("/schedules", status_code=201)
async def create_schedule(
    body: dict, _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Create a scheduled evaluation."""
    store = get_eval_store()

    agent_name = body.get("agent_name")
    dataset_id = body.get("dataset_id")
    cron_expr = body.get("cron")

    if not agent_name or not dataset_id or not cron_expr:
        raise HTTPException(
            status_code=400,
            detail="agent_name, dataset_id, and cron are required",
        )

    schedule = store.create_schedule(
        agent_name=agent_name,
        dataset_id=dataset_id,
        cron_expr=cron_expr,
        threshold=body.get("threshold", 0.7),
    )
    return ApiResponse(data=schedule)


@router.get("/schedules")
async def list_schedules(_user: User = Depends(get_current_user)) -> ApiResponse[list]:
    """List all scheduled evaluations."""
    store = get_eval_store()
    schedules = store.list_schedules()
    return ApiResponse(data=schedules, meta=ApiMeta(total=len(schedules)))


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Delete a scheduled evaluation."""
    store = get_eval_store()
    deleted = store.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return ApiResponse(data={"deleted": True, "schedule_id": schedule_id})


# ---------------------------------------------------------------------------
# Promotion Gate
# ---------------------------------------------------------------------------


@router.get("/leaderboard")
async def get_leaderboard(
    dataset_id: str | None = Query(None),
    metric: str = Query("correctness"),
    limit: int = Query(20, ge=1, le=100),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """Return ranked leaderboard of agents by mean metric score.

    Ranks all agents with completed runs by their best mean score on the given metric.
    Filter by dataset_id to get a dataset-specific leaderboard.
    """
    store = get_eval_store()
    leaderboard = store.get_leaderboard(dataset_id=dataset_id, metric=metric, limit=limit)
    return ApiResponse(data=leaderboard, meta=ApiMeta(total=len(leaderboard)))


@router.get("/reports/{run_id}")
async def get_public_report(
    run_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Get a public shareable eval report for a completed run."""
    store = get_eval_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    results = store.get_results(run_id)
    return ApiResponse(
        data={
            "run": run,
            "results": results,
            "total_results": len(results),
            "public_url": f"/api/v1/eval/reports/{run_id}",
        }
    )


@router.get("/runs/{run_id}/export/csv")
async def export_run_csv(run_id: str, _user: User = Depends(get_current_user)) -> Response:
    """Export run results as a CSV file."""
    store = get_eval_store()
    try:
        csv_content = store.export_csv(run_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=eval-run-{run_id[:8]}.csv"},
    )


@router.post("/datasets/seed-community", status_code=201)
async def seed_community_benchmark_datasets(
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Seed the 3 community benchmark datasets (customer support, SQL analyst, code reviewer).

    Safe to call multiple times — skips datasets that already exist.
    """
    store = get_eval_store()
    created_ids = seed_community_datasets(store)
    return ApiResponse(
        data={
            "seeded": len(created_ids),
            "dataset_ids": created_ids,
        }
    )


@router.post("/promote-check")
async def promote_check(body: dict, _user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Check if an agent passes the eval gate for promotion.

    Input: { agent_name, min_score, required_metrics }
    Output: { passed, scores, blocking_metrics }
    """
    store = get_eval_store()

    agent_name = body.get("agent_name")
    if not agent_name:
        raise HTTPException(status_code=400, detail="agent_name is required")

    min_score = body.get("min_score", 0.7)
    required_metrics = body.get("required_metrics", ["correctness", "relevance"])

    result = store.promote_check(
        agent_name=agent_name,
        min_score=min_score,
        required_metrics=required_metrics,
    )
    return ApiResponse(data=result)
