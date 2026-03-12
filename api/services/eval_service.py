"""Evaluation Framework Service — in-memory store for eval datasets, runs, and results.

Provides:
- Eval dataset CRUD and row management
- Eval run lifecycle (create, execute, complete)
- Built-in scorers (correctness, relevance, latency, cost)
- Run summary aggregation, trend tracking, and run comparison
- JSONL import/export
"""

from __future__ import annotations

import json
import logging
import statistics
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Built-in Scorers
# ---------------------------------------------------------------------------


def score_correctness(actual: str, expected: str) -> float:
    """Score correctness via exact match or fuzzy match (SequenceMatcher ratio).

    Returns 1.0 for exact match, otherwise the SequenceMatcher ratio.
    """
    if actual.strip() == expected.strip():
        return 1.0
    return round(SequenceMatcher(None, actual.strip(), expected.strip()).ratio(), 4)


def score_relevance(actual: str, expected: str) -> float:
    """Score relevance via keyword overlap ratio.

    Computes the fraction of expected keywords present in the actual output.
    """
    expected_words = set(expected.lower().split())
    if not expected_words:
        return 1.0
    actual_words = set(actual.lower().split())
    overlap = expected_words & actual_words
    return round(len(overlap) / len(expected_words), 4)


def score_latency(latency_ms: int) -> float:
    """Score latency: 1.0 if < 1000ms, scales linearly to 0.0 at 10000ms."""
    if latency_ms <= 1000:
        return 1.0
    if latency_ms >= 10000:
        return 0.0
    return round(1.0 - (latency_ms - 1000) / 9000, 4)


def score_cost(cost_usd: float) -> float:
    """Score cost: 1.0 if < $0.01, scales linearly to 0.0 at $0.10."""
    if cost_usd <= 0.01:
        return 1.0
    if cost_usd >= 0.10:
        return 0.0
    return round(1.0 - (cost_usd - 0.01) / 0.09, 4)


def score_with_judge_model(actual: str, expected: str, judge_model: str | None = None) -> float:
    """Score using an LLM judge model.

    TODO: Integrate with real LLM judge (via LiteLLM or provider gateway).
    Currently returns a simulated score based on fuzzy match with slight randomness.
    """
    # Stub: use correctness as base, add slight boost for longer responses
    base = score_correctness(actual, expected)
    # Simulate judge giving slightly different scores
    length_bonus = min(0.1, len(actual) / 10000)
    return round(min(1.0, base + length_bonus), 4)


BUILT_IN_SCORERS = {
    "correctness": score_correctness,
    "relevance": score_relevance,
    "latency_score": score_latency,
    "cost_score": score_cost,
}


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


class EvalDatasetRecord:
    """In-memory eval dataset record."""

    def __init__(
        self,
        *,
        dataset_id: str,
        name: str,
        description: str = "",
        agent_id: str | None = None,
        version: str = "1.0.0",
        fmt: str = "jsonl",
        row_count: int = 0,
        team: str = "default",
        tags: list[str] | None = None,
        created_at: str,
        updated_at: str,
    ) -> None:
        self.id = dataset_id
        self.name = name
        self.description = description
        self.agent_id = agent_id
        self.version = version
        self.format = fmt
        self.row_count = row_count
        self.team = team
        self.tags = tags or []
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_id": self.agent_id,
            "version": self.version,
            "format": self.format,
            "row_count": self.row_count,
            "team": self.team,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class EvalDatasetRowRecord:
    """In-memory dataset row record."""

    def __init__(
        self,
        *,
        row_id: str,
        dataset_id: str,
        row_input: dict[str, Any],
        expected_output: str,
        expected_tool_calls: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str,
    ) -> None:
        self.id = row_id
        self.dataset_id = dataset_id
        self.input = row_input
        self.expected_output = expected_output
        self.expected_tool_calls = expected_tool_calls
        self.tags = tags or []
        self.metadata = metadata or {}
        self.created_at = created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dataset_id": self.dataset_id,
            "input": self.input,
            "expected_output": self.expected_output,
            "expected_tool_calls": self.expected_tool_calls,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


class EvalRunRecord:
    """In-memory eval run record."""

    def __init__(
        self,
        *,
        run_id: str,
        agent_id: str | None = None,
        agent_name: str,
        dataset_id: str,
        status: str = "pending",
        config: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        created_at: str,
    ) -> None:
        self.id = run_id
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.dataset_id = dataset_id
        self.status = status
        self.config = config or {}
        self.summary = summary or {}
        self.started_at = started_at
        self.completed_at = completed_at
        self.created_at = created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "config": self.config,
            "summary": self.summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
        }


class EvalResultRecord:
    """In-memory eval result record."""

    def __init__(
        self,
        *,
        result_id: str,
        run_id: str,
        row_id: str,
        actual_output: str,
        scores: dict[str, float],
        latency_ms: int = 0,
        token_count: int = 0,
        cost_usd: float = 0.0,
        error: str | None = None,
        created_at: str,
    ) -> None:
        self.id = result_id
        self.run_id = run_id
        self.row_id = row_id
        self.actual_output = actual_output
        self.scores = scores
        self.latency_ms = latency_ms
        self.token_count = token_count
        self.cost_usd = cost_usd
        self.error = error
        self.created_at = created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "row_id": self.row_id,
            "actual_output": self.actual_output,
            "scores": self.scores,
            "latency_ms": self.latency_ms,
            "token_count": self.token_count,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# In-Memory Store
# ---------------------------------------------------------------------------


class EvalStore:
    """In-memory store for evaluation datasets, runs, and results.

    Will be replaced by PostgreSQL when the real DB is connected.
    """

    def __init__(self) -> None:
        self._datasets: dict[str, EvalDatasetRecord] = {}
        self._rows: dict[str, EvalDatasetRowRecord] = {}  # keyed by row_id
        self._runs: dict[str, EvalRunRecord] = {}
        self._results: dict[str, EvalResultRecord] = {}  # keyed by result_id
        self._schedules: dict[str, dict[str, Any]] = {}  # keyed by schedule_id

    # --- Dataset CRUD ---

    def create_dataset(
        self,
        name: str,
        description: str = "",
        agent_id: str | None = None,
        version: str = "1.0.0",
        fmt: str = "jsonl",
        team: str = "default",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new evaluation dataset."""
        # Check uniqueness
        for ds in self._datasets.values():
            if ds.name == name:
                raise ValueError(f"Dataset with name '{name}' already exists")

        dataset_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        dataset = EvalDatasetRecord(
            dataset_id=dataset_id,
            name=name,
            description=description,
            agent_id=agent_id,
            version=version,
            fmt=fmt,
            team=team,
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        self._datasets[dataset_id] = dataset
        logger.info("Eval dataset created", extra={"name": name, "team": team})
        return dataset.to_dict()

    def list_datasets(
        self,
        team: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List datasets, optionally filtered by team or agent_id."""
        results = []
        for ds in self._datasets.values():
            if team and ds.team != team:
                continue
            if agent_id and ds.agent_id != agent_id:
                continue
            results.append(ds.to_dict())
        results.sort(key=lambda d: d["created_at"], reverse=True)
        return results

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        """Get a single dataset by ID."""
        ds = self._datasets.get(dataset_id)
        return ds.to_dict() if ds else None

    def delete_dataset(self, dataset_id: str) -> bool:
        """Delete a dataset and cascade to rows, runs, and results."""
        ds = self._datasets.get(dataset_id)
        if not ds:
            return False

        # Cascade: delete rows
        row_ids_to_delete = [r.id for r in self._rows.values() if r.dataset_id == dataset_id]
        for rid in row_ids_to_delete:
            # Also delete results referencing these rows
            result_ids = [res.id for res in self._results.values() if res.row_id == rid]
            for result_id in result_ids:
                del self._results[result_id]
            del self._rows[rid]

        # Cascade: delete runs and their results
        run_ids_to_delete = [r.id for r in self._runs.values() if r.dataset_id == dataset_id]
        for run_id in run_ids_to_delete:
            result_ids = [res.id for res in self._results.values() if res.run_id == run_id]
            for result_id in result_ids:
                del self._results[result_id]
            del self._runs[run_id]

        del self._datasets[dataset_id]
        logger.info("Eval dataset deleted", extra={"dataset_id": dataset_id})
        return True

    # --- Dataset Rows ---

    def add_rows(self, dataset_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add rows to a dataset. Returns the created row records."""
        ds = self._datasets.get(dataset_id)
        if not ds:
            raise ValueError(f"Dataset '{dataset_id}' not found")

        now = datetime.now(UTC).isoformat()
        created = []

        for row_data in rows:
            row_id = str(uuid.uuid4())
            row = EvalDatasetRowRecord(
                row_id=row_id,
                dataset_id=dataset_id,
                row_input=row_data.get("input", {}),
                expected_output=row_data.get("expected_output", ""),
                expected_tool_calls=row_data.get("expected_tool_calls"),
                tags=row_data.get("tags", []),
                metadata=row_data.get("metadata", {}),
                created_at=now,
            )
            self._rows[row_id] = row
            created.append(row.to_dict())

        ds.row_count += len(created)
        ds.updated_at = now
        return created

    def list_rows(
        self,
        dataset_id: str,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List rows in a dataset with optional tag filter and pagination."""
        results = []
        for row in self._rows.values():
            if row.dataset_id != dataset_id:
                continue
            if tag and tag not in row.tags:
                continue
            results.append(row.to_dict())
        results.sort(key=lambda r: r["created_at"])
        return results[offset : offset + limit]

    def import_jsonl(self, dataset_id: str, content: str) -> int:
        """Import rows from JSONL content. Returns the number of rows imported."""
        ds = self._datasets.get(dataset_id)
        if not ds:
            raise ValueError(f"Dataset '{dataset_id}' not found")

        lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
        rows_to_add = []
        for line in lines:
            data = json.loads(line)
            rows_to_add.append(
                {
                    "input": data.get("input", {}),
                    "expected_output": data.get("expected_output", ""),
                    "expected_tool_calls": data.get("expected_tool_calls"),
                    "tags": data.get("tags", []),
                    "metadata": data.get("metadata", {}),
                }
            )

        self.add_rows(dataset_id, rows_to_add)
        return len(rows_to_add)

    def export_jsonl(self, dataset_id: str) -> str:
        """Export dataset rows as JSONL string."""
        rows = self.list_rows(dataset_id, limit=100_000)
        lines = []
        for row in rows:
            entry = {
                "input": row["input"],
                "expected_output": row["expected_output"],
            }
            if row.get("expected_tool_calls"):
                entry["expected_tool_calls"] = row["expected_tool_calls"]
            if row.get("tags"):
                entry["tags"] = row["tags"]
            if row.get("metadata"):
                entry["metadata"] = row["metadata"]
            lines.append(json.dumps(entry))
        return "\n".join(lines)

    # --- Eval Runs ---

    def create_run(
        self,
        agent_name: str,
        dataset_id: str,
        config: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new eval run."""
        ds = self._datasets.get(dataset_id)
        if not ds:
            raise ValueError(f"Dataset '{dataset_id}' not found")

        run_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        run = EvalRunRecord(
            run_id=run_id,
            agent_id=agent_id,
            agent_name=agent_name,
            dataset_id=dataset_id,
            status="pending",
            config=config or {},
            created_at=now,
        )
        self._runs[run_id] = run
        logger.info("Eval run created", extra={"run_id": run_id, "agent": agent_name})
        return run.to_dict()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Get a single run by ID."""
        run = self._runs.get(run_id)
        return run.to_dict() if run else None

    def list_runs(
        self,
        agent_name: str | None = None,
        dataset_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List eval runs with optional filters."""
        results = []
        for run in self._runs.values():
            if agent_name and run.agent_name != agent_name:
                continue
            if dataset_id and run.dataset_id != dataset_id:
                continue
            results.append(run.to_dict())
        results.sort(key=lambda r: r["created_at"], reverse=True)
        return results

    def update_run_status(
        self,
        run_id: str,
        status: str,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a run's status and optionally its summary."""
        run = self._runs.get(run_id)
        if not run:
            return None

        now = datetime.now(UTC).isoformat()
        run.status = status
        if summary is not None:
            run.summary = summary
        if status == "running" and not run.started_at:
            run.started_at = now
        if status in ("completed", "failed", "cancelled"):
            run.completed_at = now
        return run.to_dict()

    # --- Eval Results ---

    def add_result(
        self,
        run_id: str,
        row_id: str,
        actual_output: str,
        scores: dict[str, float],
        latency_ms: int = 0,
        token_count: int = 0,
        cost_usd: float = 0.0,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Add a single evaluation result for a run/row pair."""
        if run_id not in self._runs:
            raise ValueError(f"Run '{run_id}' not found")

        result_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        result = EvalResultRecord(
            result_id=result_id,
            run_id=run_id,
            row_id=row_id,
            actual_output=actual_output,
            scores=scores,
            latency_ms=latency_ms,
            token_count=token_count,
            cost_usd=cost_usd,
            error=error,
            created_at=now,
        )
        self._results[result_id] = result
        return result.to_dict()

    def get_results(self, run_id: str) -> list[dict[str, Any]]:
        """Get all results for a run."""
        results = [r.to_dict() for r in self._results.values() if r.run_id == run_id]
        results.sort(key=lambda r: r["created_at"])
        return results

    # --- Scoring & Aggregation ---

    def compute_run_summary(self, run_id: str) -> dict[str, Any]:
        """Compute aggregate scores for a run: mean, median, p95, min, max per metric."""
        results = [r for r in self._results.values() if r.run_id == run_id]
        if not results:
            return {"metrics": {}, "total_results": 0}

        # Collect all metric values
        metric_values: dict[str, list[float]] = {}
        for r in results:
            for metric, value in r.scores.items():
                metric_values.setdefault(metric, []).append(value)

        metrics_summary: dict[str, dict[str, Any]] = {}
        for metric, values in metric_values.items():
            sorted_vals = sorted(values)
            p95_idx = max(0, int(len(sorted_vals) * 0.95) - 1)
            metrics_summary[metric] = {
                "mean": round(statistics.mean(values), 4),
                "median": round(statistics.median(values), 4),
                "p95": round(sorted_vals[p95_idx], 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "count": len(values),
            }

        # Aggregate latency and cost
        total_latency = sum(r.latency_ms for r in results)
        total_tokens = sum(r.token_count for r in results)
        total_cost = sum(r.cost_usd for r in results)
        error_count = sum(1 for r in results if r.error)

        summary = {
            "metrics": metrics_summary,
            "total_results": len(results),
            "error_count": error_count,
            "total_latency_ms": total_latency,
            "avg_latency_ms": round(total_latency / len(results)),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
        }

        # Also store it on the run
        run = self._runs.get(run_id)
        if run:
            run.summary = summary

        return summary

    def get_score_trend(
        self,
        agent_name: str,
        metric: str = "correctness",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get score trend for an agent over its recent runs."""
        runs = [r for r in self._runs.values() if r.agent_name == agent_name]
        runs.sort(key=lambda r: r.created_at)

        trend = []
        for run in runs[-limit:]:
            metric_data = run.summary.get("metrics", {}).get(metric, {})
            if metric_data:
                trend.append(
                    {
                        "run_id": run.id,
                        "agent_name": run.agent_name,
                        "metric": metric,
                        "mean": metric_data.get("mean", 0),
                        "median": metric_data.get("median", 0),
                        "created_at": run.created_at,
                    }
                )
        return trend

    def compare_runs(self, run_id_a: str, run_id_b: str) -> dict[str, Any]:
        """Compare two runs side-by-side."""
        run_a = self._runs.get(run_id_a)
        run_b = self._runs.get(run_id_b)

        if not run_a or not run_b:
            raise ValueError("One or both runs not found")

        metrics_a = run_a.summary.get("metrics", {})
        metrics_b = run_b.summary.get("metrics", {})

        all_metrics = set(list(metrics_a.keys()) + list(metrics_b.keys()))

        comparison: dict[str, Any] = {}
        for metric in sorted(all_metrics):
            a_data = metrics_a.get(metric, {})
            b_data = metrics_b.get(metric, {})
            a_mean = a_data.get("mean", 0)
            b_mean = b_data.get("mean", 0)
            delta = round(b_mean - a_mean, 4)
            comparison[metric] = {
                "run_a_mean": a_mean,
                "run_b_mean": b_mean,
                "delta": delta,
                "improved": delta > 0,
            }

        return {
            "run_a": run_a.to_dict(),
            "run_b": run_b.to_dict(),
            "comparison": comparison,
        }

    # --- Schedules ---

    def create_schedule(
        self,
        agent_name: str,
        dataset_id: str,
        cron_expr: str,
        threshold: float = 0.7,
    ) -> dict[str, Any]:
        """Create a scheduled evaluation."""
        schedule_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        schedule = {
            "id": schedule_id,
            "agent_name": agent_name,
            "dataset_id": dataset_id,
            "cron": cron_expr,
            "threshold": threshold,
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        self._schedules[schedule_id] = schedule
        logger.info(
            "Eval schedule created",
            extra={"schedule_id": schedule_id, "agent": agent_name, "cron": cron_expr},
        )
        return schedule

    def list_schedules(self) -> list[dict[str, Any]]:
        """List all scheduled evaluations."""
        schedules = list(self._schedules.values())
        schedules.sort(key=lambda s: s["created_at"], reverse=True)
        return schedules

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a scheduled evaluation."""
        if schedule_id not in self._schedules:
            return False
        del self._schedules[schedule_id]
        logger.info("Eval schedule deleted", extra={"schedule_id": schedule_id})
        return True

    # --- Promotion Gate ---

    def promote_check(
        self,
        agent_name: str,
        min_score: float = 0.7,
        required_metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check if an agent passes the eval gate for promotion.

        Looks at the most recent completed run for the agent and checks
        whether all required metrics meet the minimum score.
        """
        if required_metrics is None:
            required_metrics = ["correctness", "relevance"]

        # Find the most recent completed run
        runs = [
            r
            for r in self._runs.values()
            if r.agent_name == agent_name and r.status == "completed"
        ]
        runs.sort(key=lambda r: r.created_at, reverse=True)

        if not runs:
            return {
                "passed": False,
                "agent_name": agent_name,
                "scores": {},
                "blocking_metrics": required_metrics,
                "reason": "No completed eval runs found",
            }

        latest_run = runs[0]
        run_metrics = latest_run.summary.get("metrics", {})

        scores: dict[str, float] = {}
        blocking: list[str] = []

        for metric in required_metrics:
            metric_data = run_metrics.get(metric, {})
            mean_score = metric_data.get("mean", 0.0)
            scores[metric] = round(mean_score, 4)
            if mean_score < min_score:
                blocking.append(metric)

        passed = len(blocking) == 0

        return {
            "passed": passed,
            "agent_name": agent_name,
            "run_id": latest_run.id,
            "scores": scores,
            "blocking_metrics": blocking,
            "min_score": min_score,
        }

    # --- Eval Runner (simulated) ---

    def execute_run(self, run_id: str) -> dict[str, Any]:
        """Execute an eval run: iterate rows, score each, update status.

        This is a simulated runner — in production, this would call the actual agent.
        """
        run = self._runs.get(run_id)
        if not run:
            raise ValueError(f"Run '{run_id}' not found")

        # Update to running
        self.update_run_status(run_id, "running")

        # Get dataset rows
        rows = self.list_rows(run.dataset_id, limit=100_000)
        if not rows:
            empty_summary = {"metrics": {}, "total_results": 0}
            self.update_run_status(run_id, "completed", summary=empty_summary)
            return self.get_run(run_id)  # type: ignore[return-value]

        judge_model = run.config.get("judge_model")

        for row in rows:
            # Simulate agent response (in production, would invoke the actual agent)
            actual_output = self._simulate_agent_response(row["input"], row["expected_output"])
            latency_ms = 350 + len(actual_output)  # simulated latency
            token_count = len(actual_output.split()) * 2  # rough token estimate
            cost_usd = token_count * 0.00001  # simulated cost

            # Score with built-in scorers
            scores: dict[str, float] = {
                "correctness": score_correctness(actual_output, row["expected_output"]),
                "relevance": score_relevance(actual_output, row["expected_output"]),
                "latency_score": score_latency(latency_ms),
                "cost_score": score_cost(cost_usd),
            }

            # If judge model configured, add judge score
            if judge_model:
                scores["judge"] = score_with_judge_model(
                    actual_output, row["expected_output"], judge_model
                )

            self.add_result(
                run_id=run_id,
                row_id=row["id"],
                actual_output=actual_output,
                scores=scores,
                latency_ms=latency_ms,
                token_count=token_count,
                cost_usd=cost_usd,
            )

        # Compute summary and complete
        summary = self.compute_run_summary(run_id)
        self.update_run_status(run_id, "completed", summary=summary)
        logger.info("Eval run completed", extra={"run_id": run_id, "results": len(rows)})
        return self.get_run(run_id)  # type: ignore[return-value]

    def _simulate_agent_response(self, input_data: dict[str, Any], expected: str) -> str:
        """Simulate an agent response for evaluation.

        In production, this would call the actual deployed agent endpoint.
        Returns a response that is similar but not identical to the expected output.
        """
        # Simulate by returning a slightly modified version of the expected output
        # Return a plausible response based on the expected output
        words = expected.split()
        if len(words) > 3:
            # Simulate slight variation — drop or shuffle some words
            return " ".join(words[: max(1, len(words) - 1)])
        return expected


# ---------------------------------------------------------------------------
# Global Singleton
# ---------------------------------------------------------------------------

_store: EvalStore | None = None


def get_eval_store() -> EvalStore:
    """Get the global eval store singleton."""
    global _store
    if _store is None:
        _store = EvalStore()
        _seed_demo_data(_store)
    return _store


def _seed_demo_data(store: EvalStore) -> None:
    """Seed the store with demo data for the dashboard."""
    # Create a demo dataset
    dataset = store.create_dataset(
        name="customer-support-qa",
        description="QA test cases for the customer support agent",
        team="customer-success",
        tags=["support", "qa", "production"],
        version="1.0.0",
    )
    dataset_id = dataset["id"]

    # Add sample rows
    store.add_rows(
        dataset_id,
        [
            {
                "input": {"message": "How do I reset my password?"},
                "expected_output": (
                    "To reset your password, go to Settings > "
                    "Security > Reset Password. You'll receive "
                    "an email with a reset link."
                ),
                "tags": ["password", "account"],
            },
            {
                "input": {"message": "What is your refund policy?"},
                "expected_output": (
                    "We offer a 30-day money-back guarantee "
                    "on all plans. Contact support to initiate "
                    "a refund."
                ),
                "tags": ["billing", "refund"],
            },
            {
                "input": {"message": "How do I upgrade my plan?"},
                "expected_output": (
                    "To upgrade, go to Settings > Billing > "
                    "Change Plan. Select your desired plan "
                    "and confirm."
                ),
                "tags": ["billing", "upgrade"],
            },
            {
                "input": {"message": "My agent is stuck deploying"},
                "expected_output": (
                    "Check the deployment logs with "
                    "'garden logs <agent-name>'. Common causes "
                    "include misconfigured secrets or "
                    "insufficient resources."
                ),
                "tags": ["technical", "deploy"],
            },
            {
                "input": {"message": "How do I add a team member?"},
                "expected_output": (
                    "Go to Settings > Team > Invite Member. "
                    "Enter their email and assign a role "
                    "(viewer, contributor, deployer, or admin)."
                ),
                "tags": ["team", "account"],
            },
        ],
    )

    # Create and execute a demo run
    run = store.create_run(
        agent_name="customer-support-agent",
        dataset_id=dataset_id,
        config={"model": "claude-sonnet-4", "temperature": 0.7},
    )
    store.execute_run(run["id"])

    logger.info("Eval demo data seeded")
