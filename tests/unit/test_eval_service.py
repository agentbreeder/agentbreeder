"""Tests for the Evaluation Framework service (M18)."""

from __future__ import annotations

import json

import pytest

from api.services.eval_service import (
    EvalStore,
    score_correctness,
    score_cost,
    score_latency,
    score_relevance,
    score_with_judge_model,
)


@pytest.fixture
def store() -> EvalStore:
    """Create a fresh EvalStore for each test (no seed data)."""
    return EvalStore()


@pytest.fixture
def dataset_with_rows(store: EvalStore) -> dict:
    """Create a dataset with sample rows and return both."""
    ds = store.create_dataset(
        name="test-dataset",
        description="A test dataset",
        team="engineering",
        tags=["test"],
    )
    rows = store.add_rows(ds["id"], [
        {
            "input": {"message": "Hello"},
            "expected_output": "Hello! How can I help you?",
            "tags": ["greeting"],
        },
        {
            "input": {"message": "What is 2+2?"},
            "expected_output": "The answer is 4.",
            "tags": ["math"],
        },
        {
            "input": {"message": "Goodbye"},
            "expected_output": "Goodbye! Have a great day!",
            "tags": ["farewell"],
        },
    ])
    return {"dataset": ds, "rows": rows}


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


class TestDatasetCRUD:
    def test_create_dataset(self, store: EvalStore) -> None:
        ds = store.create_dataset(
            name="my-dataset",
            description="Test description",
            team="engineering",
            tags=["test", "v1"],
        )
        assert ds["name"] == "my-dataset"
        assert ds["description"] == "Test description"
        assert ds["team"] == "engineering"
        assert ds["tags"] == ["test", "v1"]
        assert ds["row_count"] == 0
        assert ds["id"] is not None
        assert ds["created_at"] is not None

    def test_create_dataset_duplicate_name_raises(self, store: EvalStore) -> None:
        store.create_dataset(name="unique-name")
        with pytest.raises(ValueError, match="already exists"):
            store.create_dataset(name="unique-name")

    def test_list_datasets(self, store: EvalStore) -> None:
        store.create_dataset(name="ds-1", team="team-a")
        store.create_dataset(name="ds-2", team="team-b")
        store.create_dataset(name="ds-3", team="team-a")

        all_datasets = store.list_datasets()
        assert len(all_datasets) == 3

        team_a = store.list_datasets(team="team-a")
        assert len(team_a) == 2

    def test_get_dataset(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="get-me")
        result = store.get_dataset(ds["id"])
        assert result is not None
        assert result["name"] == "get-me"

    def test_get_dataset_not_found(self, store: EvalStore) -> None:
        result = store.get_dataset("nonexistent-id")
        assert result is None

    def test_delete_dataset(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="to-delete")
        assert store.delete_dataset(ds["id"]) is True
        assert store.get_dataset(ds["id"]) is None

    def test_delete_dataset_not_found(self, store: EvalStore) -> None:
        assert store.delete_dataset("nonexistent") is False


# ---------------------------------------------------------------------------
# Dataset Rows
# ---------------------------------------------------------------------------


class TestDatasetRows:
    def test_add_rows_and_list(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="row-test")
        rows = store.add_rows(ds["id"], [
            {"input": {"q": "a"}, "expected_output": "answer a"},
            {"input": {"q": "b"}, "expected_output": "answer b"},
        ])
        assert len(rows) == 2

        listed = store.list_rows(ds["id"])
        assert len(listed) == 2

        # Check row_count updated
        updated_ds = store.get_dataset(ds["id"])
        assert updated_ds["row_count"] == 2

    def test_add_rows_to_nonexistent_dataset(self, store: EvalStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.add_rows("nonexistent", [{"input": {}, "expected_output": "x"}])

    def test_list_rows_with_tag_filter(self) -> None:
        pass  # covered by test_list_rows_with_tag_filter_direct

    def test_list_rows_with_tag_filter_direct(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="tag-filter-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "a"}, "expected_output": "a", "tags": ["math"]},
            {"input": {"q": "b"}, "expected_output": "b", "tags": ["science"]},
            {"input": {"q": "c"}, "expected_output": "c", "tags": ["math"]},
        ])

        math_rows = store.list_rows(ds["id"], tag="math")
        assert len(math_rows) == 2

        science_rows = store.list_rows(ds["id"], tag="science")
        assert len(science_rows) == 1

    def test_list_rows_pagination(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="page-test")
        store.add_rows(ds["id"], [
            {"input": {"q": str(i)}, "expected_output": str(i)} for i in range(10)
        ])

        page1 = store.list_rows(ds["id"], limit=3, offset=0)
        assert len(page1) == 3

        page2 = store.list_rows(ds["id"], limit=3, offset=3)
        assert len(page2) == 3

        # Pages should not overlap
        page1_ids = {r["id"] for r in page1}
        page2_ids = {r["id"] for r in page2}
        assert page1_ids.isdisjoint(page2_ids)


# ---------------------------------------------------------------------------
# JSONL Import / Export
# ---------------------------------------------------------------------------


class TestJsonlImportExport:
    def test_import_jsonl(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="import-test")
        content = "\n".join([
            json.dumps({"input": {"message": "Hi"}, "expected_output": "Hello"}),
            json.dumps({"input": {"message": "Bye"}, "expected_output": "Goodbye"}),
        ])

        count = store.import_jsonl(ds["id"], content)
        assert count == 2

        rows = store.list_rows(ds["id"])
        assert len(rows) == 2

    def test_import_jsonl_nonexistent_dataset(self, store: EvalStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.import_jsonl("bad-id", '{"input": {}, "expected_output": "x"}')

    def test_export_jsonl(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="export-test")
        store.add_rows(ds["id"], [
            {"input": {"message": "Hi"}, "expected_output": "Hello", "tags": ["greeting"]},
            {"input": {"message": "Bye"}, "expected_output": "Goodbye"},
        ])

        exported = store.export_jsonl(ds["id"])
        lines = exported.strip().split("\n")
        assert len(lines) == 2

        parsed_0 = json.loads(lines[0])
        assert parsed_0["input"] == {"message": "Hi"}
        assert parsed_0["expected_output"] == "Hello"
        assert parsed_0["tags"] == ["greeting"]

    def test_roundtrip_import_export(self, store: EvalStore) -> None:
        ds1 = store.create_dataset(name="roundtrip-1")
        original = [
            {"input": {"q": "What?"}, "expected_output": "Answer!", "tags": ["faq"]},
            {"input": {"q": "How?"}, "expected_output": "Like this."},
        ]
        content = "\n".join(json.dumps(item) for item in original)
        store.import_jsonl(ds1["id"], content)

        exported = store.export_jsonl(ds1["id"])

        ds2 = store.create_dataset(name="roundtrip-2")
        count = store.import_jsonl(ds2["id"], exported)
        assert count == 2


# ---------------------------------------------------------------------------
# Eval Runs & Results
# ---------------------------------------------------------------------------


class TestEvalRuns:
    def test_create_run_and_add_results(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="run-test")
        rows = store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run = store.create_run(
            agent_name="test-agent",
            dataset_id=ds["id"],
            config={"model": "test-model"},
        )
        assert run["status"] == "pending"
        assert run["agent_name"] == "test-agent"

        result = store.add_result(
            run_id=run["id"],
            row_id=rows[0]["id"],
            actual_output="answer",
            scores={"correctness": 1.0, "relevance": 1.0},
            latency_ms=200,
            token_count=50,
            cost_usd=0.001,
        )
        assert result["scores"]["correctness"] == 1.0

        results = store.get_results(run["id"])
        assert len(results) == 1

    def test_create_run_nonexistent_dataset(self, store: EvalStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.create_run(agent_name="a", dataset_id="nonexistent")

    def test_add_result_nonexistent_run(self, store: EvalStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.add_result(
                run_id="nonexistent",
                row_id="any",
                actual_output="x",
                scores={},
            )

    def test_list_runs_with_filters(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="filter-runs")
        store.create_run(agent_name="agent-a", dataset_id=ds["id"])
        store.create_run(agent_name="agent-b", dataset_id=ds["id"])
        store.create_run(agent_name="agent-a", dataset_id=ds["id"])

        all_runs = store.list_runs()
        assert len(all_runs) == 3

        agent_a_runs = store.list_runs(agent_name="agent-a")
        assert len(agent_a_runs) == 2

    def test_update_run_status(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="status-test")
        run = store.create_run(agent_name="agent", dataset_id=ds["id"])

        updated = store.update_run_status(run["id"], "running")
        assert updated["status"] == "running"
        assert updated["started_at"] is not None

        completed = store.update_run_status(run["id"], "completed")
        assert completed["status"] == "completed"
        assert completed["completed_at"] is not None

    def test_execute_run(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="exec-test")
        store.add_rows(ds["id"], [
            {"input": {"message": "Hi"}, "expected_output": "Hello there!"},
            {"input": {"message": "Bye"}, "expected_output": "Goodbye!"},
        ])

        run = store.create_run(agent_name="test-agent", dataset_id=ds["id"])
        result = store.execute_run(run["id"])

        assert result["status"] == "completed"
        assert result["summary"]["total_results"] == 2
        assert "correctness" in result["summary"]["metrics"]

        results = store.get_results(run["id"])
        assert len(results) == 2
        for r in results:
            assert "correctness" in r["scores"]
            assert "relevance" in r["scores"]
            assert "latency_score" in r["scores"]
            assert "cost_score" in r["scores"]


# ---------------------------------------------------------------------------
# Scoring Aggregation
# ---------------------------------------------------------------------------


class TestScoring:
    def test_compute_run_summary(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="summary-test")
        rows = store.add_rows(ds["id"], [
            {"input": {"q": str(i)}, "expected_output": f"answer {i}"} for i in range(5)
        ])

        run = store.create_run(agent_name="test-agent", dataset_id=ds["id"])
        for i, row in enumerate(rows):
            store.add_result(
                run_id=run["id"],
                row_id=row["id"],
                actual_output=f"answer {i}",
                scores={"correctness": 0.8 + i * 0.05, "relevance": 0.9},
                latency_ms=100 + i * 50,
                token_count=50,
                cost_usd=0.001,
            )

        summary = store.compute_run_summary(run["id"])
        assert summary["total_results"] == 5
        assert "correctness" in summary["metrics"]
        assert "relevance" in summary["metrics"]
        assert summary["metrics"]["correctness"]["count"] == 5
        assert summary["metrics"]["relevance"]["mean"] == 0.9
        assert summary["total_cost_usd"] == 0.005

    def test_compute_run_summary_empty(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="empty-summary")
        run = store.create_run(agent_name="agent", dataset_id=ds["id"])
        summary = store.compute_run_summary(run["id"])
        assert summary["total_results"] == 0

    def test_score_trend(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="trend-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        # Create multiple runs with summaries
        for i in range(5):
            run = store.create_run(agent_name="trend-agent", dataset_id=ds["id"])
            store.update_run_status(
                run["id"],
                "completed",
                summary={
                    "metrics": {
                        "correctness": {"mean": 0.7 + i * 0.05, "median": 0.7 + i * 0.05},
                    }
                },
            )

        trend = store.get_score_trend("trend-agent", metric="correctness", limit=10)
        assert len(trend) == 5
        # Scores should be increasing
        means = [t["mean"] for t in trend]
        assert means == sorted(means)

    def test_compare_runs(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="compare-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run_a = store.create_run(agent_name="agent-a", dataset_id=ds["id"])
        store.update_run_status(
            run_a["id"],
            "completed",
            summary={"metrics": {"correctness": {"mean": 0.8}, "relevance": {"mean": 0.7}}},
        )

        run_b = store.create_run(agent_name="agent-b", dataset_id=ds["id"])
        store.update_run_status(
            run_b["id"],
            "completed",
            summary={"metrics": {"correctness": {"mean": 0.9}, "relevance": {"mean": 0.6}}},
        )

        comparison = store.compare_runs(run_a["id"], run_b["id"])
        assert comparison["comparison"]["correctness"]["improved"] is True
        assert comparison["comparison"]["correctness"]["delta"] == 0.1
        assert comparison["comparison"]["relevance"]["improved"] is False
        assert comparison["comparison"]["relevance"]["delta"] == -0.1

    def test_compare_runs_not_found(self, store: EvalStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.compare_runs("bad-a", "bad-b")


# ---------------------------------------------------------------------------
# Built-in Scorers
# ---------------------------------------------------------------------------


class TestBuiltInScorers:
    def test_correctness_exact_match(self) -> None:
        assert score_correctness("hello world", "hello world") == 1.0

    def test_correctness_exact_match_whitespace(self) -> None:
        assert score_correctness("  hello world  ", "hello world") == 1.0

    def test_correctness_fuzzy_match(self) -> None:
        score = score_correctness("hello world", "hello worlds")
        assert 0.8 < score < 1.0

    def test_correctness_no_match(self) -> None:
        score = score_correctness("abc", "xyz")
        assert score < 0.5

    def test_relevance_full_overlap(self) -> None:
        assert score_relevance("the answer is 42", "the answer is 42") == 1.0

    def test_relevance_partial_overlap(self) -> None:
        score = score_relevance("the answer is 42", "the answer is unknown")
        assert 0.5 < score < 1.0

    def test_relevance_no_overlap(self) -> None:
        score = score_relevance("abc def", "xyz uvw")
        assert score == 0.0

    def test_relevance_empty_expected(self) -> None:
        assert score_relevance("anything", "") == 1.0

    def test_latency_score_fast(self) -> None:
        assert score_latency(500) == 1.0

    def test_latency_score_at_threshold(self) -> None:
        assert score_latency(1000) == 1.0

    def test_latency_score_slow(self) -> None:
        assert score_latency(10000) == 0.0

    def test_latency_score_mid(self) -> None:
        score = score_latency(5500)
        assert 0.4 < score < 0.6

    def test_cost_score_cheap(self) -> None:
        assert score_cost(0.005) == 1.0

    def test_cost_score_at_threshold(self) -> None:
        assert score_cost(0.01) == 1.0

    def test_cost_score_expensive(self) -> None:
        assert score_cost(0.10) == 0.0

    def test_cost_score_mid(self) -> None:
        score = score_cost(0.05)
        assert 0.4 < score < 0.7

    def test_judge_model_returns_score(self) -> None:
        score = score_with_judge_model("answer", "answer")
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Cascade Deletion
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


class TestSchedules:
    def test_create_schedule(self, store: EvalStore) -> None:
        schedule = store.create_schedule(
            agent_name="test-agent",
            dataset_id="ds-123",
            cron_expr="0 0 * * *",
            threshold=0.8,
        )
        assert schedule["agent_name"] == "test-agent"
        assert schedule["dataset_id"] == "ds-123"
        assert schedule["cron"] == "0 0 * * *"
        assert schedule["threshold"] == 0.8
        assert schedule["enabled"] is True
        assert schedule["id"] is not None
        assert schedule["created_at"] is not None

    def test_list_schedules(self, store: EvalStore) -> None:
        store.create_schedule("agent-a", "ds-1", "0 0 * * *")
        store.create_schedule("agent-b", "ds-2", "0 6 * * 1")
        store.create_schedule("agent-c", "ds-3", "0 12 * * *")

        schedules = store.list_schedules()
        assert len(schedules) == 3

    def test_list_schedules_empty(self, store: EvalStore) -> None:
        schedules = store.list_schedules()
        assert schedules == []

    def test_delete_schedule(self, store: EvalStore) -> None:
        schedule = store.create_schedule("agent", "ds-1", "0 0 * * *")
        assert store.delete_schedule(schedule["id"]) is True
        assert store.list_schedules() == []

    def test_delete_schedule_not_found(self, store: EvalStore) -> None:
        assert store.delete_schedule("nonexistent") is False


# ---------------------------------------------------------------------------
# Promotion Gate
# ---------------------------------------------------------------------------


class TestPromotionGate:
    def test_promote_check_passes(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="promo-pass-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run = store.create_run(agent_name="promo-agent", dataset_id=ds["id"])
        store.update_run_status(
            run["id"],
            "completed",
            summary={
                "metrics": {
                    "correctness": {"mean": 0.9, "median": 0.9},
                    "relevance": {"mean": 0.85, "median": 0.85},
                }
            },
        )

        result = store.promote_check(
            agent_name="promo-agent",
            min_score=0.7,
            required_metrics=["correctness", "relevance"],
        )
        assert result["passed"] is True
        assert result["blocking_metrics"] == []
        assert result["scores"]["correctness"] == 0.9
        assert result["scores"]["relevance"] == 0.85

    def test_promote_check_fails(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="promo-fail-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run = store.create_run(agent_name="fail-agent", dataset_id=ds["id"])
        store.update_run_status(
            run["id"],
            "completed",
            summary={
                "metrics": {
                    "correctness": {"mean": 0.5, "median": 0.5},
                    "relevance": {"mean": 0.9, "median": 0.9},
                }
            },
        )

        result = store.promote_check(
            agent_name="fail-agent",
            min_score=0.7,
            required_metrics=["correctness", "relevance"],
        )
        assert result["passed"] is False
        assert "correctness" in result["blocking_metrics"]
        assert "relevance" not in result["blocking_metrics"]

    def test_promote_check_no_runs(self, store: EvalStore) -> None:
        result = store.promote_check(agent_name="nonexistent-agent")
        assert result["passed"] is False
        assert "No completed eval runs found" in result.get("reason", "")

    def test_promote_check_uses_latest_run(self, store: EvalStore) -> None:
        ds = store.create_dataset(name="promo-latest-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        # Older run with low scores
        run1 = store.create_run(agent_name="latest-agent", dataset_id=ds["id"])
        store.update_run_status(
            run1["id"],
            "completed",
            summary={"metrics": {"correctness": {"mean": 0.3}}},
        )

        # Newer run with high scores
        run2 = store.create_run(agent_name="latest-agent", dataset_id=ds["id"])
        store.update_run_status(
            run2["id"],
            "completed",
            summary={"metrics": {"correctness": {"mean": 0.95}}},
        )

        result = store.promote_check(
            agent_name="latest-agent",
            min_score=0.7,
            required_metrics=["correctness"],
        )
        assert result["passed"] is True
        assert result["run_id"] == run2["id"]


# ---------------------------------------------------------------------------
# Eval Gate CLI
# ---------------------------------------------------------------------------


class TestEvalGateCLI:
    """Test the gate subcommand logic via the store (mocking CLI invocation)."""

    def _run_gate_check(
        self,
        store: EvalStore,
        run_id: str,
        threshold: float = 0.7,
        metrics: list[str] | None = None,
    ) -> dict:
        """Simulate the gate check logic from the CLI."""
        if metrics is None:
            metrics = ["correctness", "relevance"]

        run = store.get_run(run_id)
        assert run is not None, f"Run '{run_id}' not found"
        assert run["status"] == "completed", "Run is not completed"

        summary = run.get("summary", {})
        run_metrics = summary.get("metrics", {})

        gate_results = []
        all_passed = True

        for metric in metrics:
            metric_data = run_metrics.get(metric, {})
            score = metric_data.get("mean", 0.0)
            passed = score >= threshold
            if not passed:
                all_passed = False
            gate_results.append({
                "metric": metric,
                "score": round(score, 4),
                "threshold": threshold,
                "passed": passed,
            })

        return {
            "run_id": run_id,
            "passed": all_passed,
            "threshold": threshold,
            "results": gate_results,
        }

    def test_eval_gate_cli(self, store: EvalStore) -> None:
        """Gate passes when all metrics meet threshold."""
        ds = store.create_dataset(name="gate-pass-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run = store.create_run(agent_name="gate-agent", dataset_id=ds["id"])
        store.update_run_status(
            run["id"],
            "completed",
            summary={
                "metrics": {
                    "correctness": {"mean": 0.85},
                    "relevance": {"mean": 0.9},
                }
            },
        )

        result = self._run_gate_check(store, run["id"], threshold=0.7)
        assert result["passed"] is True
        assert all(r["passed"] for r in result["results"])

    def test_eval_gate_cli_fails(self, store: EvalStore) -> None:
        """Gate fails when any metric is below threshold."""
        ds = store.create_dataset(name="gate-fail-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run = store.create_run(agent_name="gate-fail-agent", dataset_id=ds["id"])
        store.update_run_status(
            run["id"],
            "completed",
            summary={
                "metrics": {
                    "correctness": {"mean": 0.5},
                    "relevance": {"mean": 0.9},
                }
            },
        )

        result = self._run_gate_check(store, run["id"], threshold=0.7)
        assert result["passed"] is False

        # correctness should fail, relevance should pass
        correctness_result = next(r for r in result["results"] if r["metric"] == "correctness")
        relevance_result = next(r for r in result["results"] if r["metric"] == "relevance")
        assert correctness_result["passed"] is False
        assert relevance_result["passed"] is True

    def test_eval_gate_cli_missing_metric(self, store: EvalStore) -> None:
        """Gate fails when a required metric is not in the run summary."""
        ds = store.create_dataset(name="gate-missing-test")
        store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run = store.create_run(agent_name="gate-missing-agent", dataset_id=ds["id"])
        store.update_run_status(
            run["id"],
            "completed",
            summary={
                "metrics": {
                    "correctness": {"mean": 0.9},
                    # relevance is missing
                }
            },
        )

        result = self._run_gate_check(
            store, run["id"], threshold=0.7, metrics=["correctness", "relevance"]
        )
        assert result["passed"] is False

        # Missing metric defaults to 0.0, which is below threshold
        relevance_result = next(r for r in result["results"] if r["metric"] == "relevance")
        assert relevance_result["score"] == 0.0
        assert relevance_result["passed"] is False


# ---------------------------------------------------------------------------
# Cascade Deletion
# ---------------------------------------------------------------------------


class TestCascadeDeletion:
    def test_delete_dataset_cascades(self, store: EvalStore) -> None:
        """Deleting a dataset should remove rows, runs, and results."""
        ds = store.create_dataset(name="cascade-test")
        rows = store.add_rows(ds["id"], [
            {"input": {"q": "test"}, "expected_output": "answer"},
        ])

        run = store.create_run(agent_name="agent", dataset_id=ds["id"])
        store.add_result(
            run_id=run["id"],
            row_id=rows[0]["id"],
            actual_output="answer",
            scores={"correctness": 1.0},
        )

        # Verify data exists
        assert len(store.list_rows(ds["id"])) == 1
        assert len(store.list_runs(dataset_id=ds["id"])) == 1
        assert len(store.get_results(run["id"])) == 1

        # Delete dataset
        store.delete_dataset(ds["id"])

        # All related data should be gone
        assert store.get_dataset(ds["id"]) is None
        assert len(store.list_rows(ds["id"])) == 0
        assert len(store.list_runs(dataset_id=ds["id"])) == 0
        assert len(store.get_results(run["id"])) == 0
