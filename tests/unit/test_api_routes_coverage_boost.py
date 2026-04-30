"""Coverage-boost tests for API routes with < 80% coverage.

Covers: evals, git, marketplace, agentops, memory, tracing,
providers, rag, mcp_servers, a2a, audit, registry, auth service,
and main app health endpoint.

Uses the same TestClient + mock pattern as test_api_routes.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from api.models.enums import UserRole
from api.services.auth import create_access_token

client = TestClient(app)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_NOW_ISO = _NOW.isoformat()


# -- Helpers --------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    token = create_access_token(str(uuid.uuid4()), "test@test.com", "viewer")
    return {"Authorization": f"Bearer {token}"}


def _make_mock_user(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "email": "test@test.com",
        "name": "Test User",
        "role": UserRole.viewer,
        "team": "engineering",
        "is_active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ===================================================================
# 1. EVALS  (api/routes/evals.py)
# ===================================================================


def _eval_store():
    """Return a MagicMock that behaves like EvalStore."""
    return MagicMock()


class TestEvalDatasets:
    @patch("api.routes.evals.get_eval_store")
    def test_list_datasets(self, mock_gs):
        store = _eval_store()
        store.list_datasets.return_value = [{"id": "ds-1", "name": "set-a"}]
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/datasets")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch("api.routes.evals.get_eval_store")
    def test_list_datasets_with_filters(self, mock_gs):
        store = _eval_store()
        store.list_datasets.return_value = []
        mock_gs.return_value = store
        resp = client.get(
            "/api/v1/eval/datasets",
            params={"team": "eng", "agent_id": "a1"},
        )
        assert resp.status_code == 200
        store.list_datasets.assert_called_once_with(team="eng", agent_id="a1")

    @patch("api.routes.evals.get_eval_store")
    def test_create_dataset(self, mock_gs):
        store = _eval_store()
        store.create_dataset.return_value = {"id": "ds-1", "name": "new-ds"}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/datasets",
            json={"name": "new-ds", "team": "eng"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "new-ds"

    @patch("api.routes.evals.get_eval_store")
    def test_create_dataset_missing_name(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post("/api/v1/eval/datasets", json={})
        assert resp.status_code == 400

    @patch("api.routes.evals.get_eval_store")
    def test_create_dataset_duplicate(self, mock_gs):
        store = _eval_store()
        store.create_dataset.side_effect = ValueError("dup")
        mock_gs.return_value = store
        resp = client.post("/api/v1/eval/datasets", json={"name": "dup"})
        assert resp.status_code == 409

    @patch("api.routes.evals.get_eval_store")
    def test_get_dataset(self, mock_gs):
        store = _eval_store()
        store.get_dataset.return_value = {"id": "ds-1", "name": "s"}
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/datasets/ds-1")
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_get_dataset_not_found(self, mock_gs):
        store = _eval_store()
        store.get_dataset.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/datasets/bad")
        assert resp.status_code == 404

    @patch("api.routes.evals.get_eval_store")
    def test_delete_dataset(self, mock_gs):
        store = _eval_store()
        store.delete_dataset.return_value = True
        mock_gs.return_value = store
        resp = client.delete("/api/v1/eval/datasets/ds-1")
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    @patch("api.routes.evals.get_eval_store")
    def test_delete_dataset_not_found(self, mock_gs):
        store = _eval_store()
        store.delete_dataset.return_value = False
        mock_gs.return_value = store
        resp = client.delete("/api/v1/eval/datasets/bad")
        assert resp.status_code == 404


class TestEvalDatasetRows:
    @patch("api.routes.evals.get_eval_store")
    def test_add_rows(self, mock_gs):
        store = _eval_store()
        store.add_rows.return_value = [{"id": "r1"}]
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/datasets/ds-1/rows",
            json={"rows": [{"input": "hi"}]},
        )
        assert resp.status_code == 201

    @patch("api.routes.evals.get_eval_store")
    def test_add_rows_empty(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post(
            "/api/v1/eval/datasets/ds-1/rows",
            json={"rows": []},
        )
        assert resp.status_code == 400

    @patch("api.routes.evals.get_eval_store")
    def test_add_rows_dataset_not_found(self, mock_gs):
        store = _eval_store()
        store.add_rows.side_effect = ValueError("not found")
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/datasets/ds-1/rows",
            json={"rows": [{"input": "x"}]},
        )
        assert resp.status_code == 404

    @patch("api.routes.evals.get_eval_store")
    def test_list_rows(self, mock_gs):
        store = _eval_store()
        store.list_rows.return_value = [{"id": "r1"}]
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/datasets/ds-1/rows")
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_import_jsonl(self, mock_gs):
        store = _eval_store()
        store.import_jsonl.return_value = 3
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/datasets/ds-1/import",
            json={"content": '{"input":"a"}\n{"input":"b"}'},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["imported"] == 3

    @patch("api.routes.evals.get_eval_store")
    def test_import_jsonl_empty(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post(
            "/api/v1/eval/datasets/ds-1/import",
            json={"content": ""},
        )
        assert resp.status_code == 400

    @patch("api.routes.evals.get_eval_store")
    def test_import_jsonl_dataset_missing(self, mock_gs):
        store = _eval_store()
        store.import_jsonl.side_effect = ValueError("nope")
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/datasets/ds-1/import",
            json={"content": '{"a":1}'},
        )
        assert resp.status_code == 404

    @patch("api.routes.evals.get_eval_store")
    def test_export_jsonl(self, mock_gs):
        store = _eval_store()
        store.get_dataset.return_value = {"id": "ds-1"}
        store.export_jsonl.return_value = '{"a":1}\n'
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/datasets/ds-1/export")
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_export_jsonl_not_found(self, mock_gs):
        store = _eval_store()
        store.get_dataset.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/datasets/bad/export")
        assert resp.status_code == 404


class TestEvalRuns:
    @patch("api.routes.evals.get_eval_store")
    def test_create_run(self, mock_gs):
        store = _eval_store()
        store.create_run.return_value = {"id": "run-1"}
        store.execute_run.return_value = {"id": "run-1", "status": "completed"}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/runs",
            json={
                "agent_name": "bot",
                "dataset_id": "ds-1",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.evals.get_eval_store")
    def test_create_run_missing_fields(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post("/api/v1/eval/runs", json={})
        assert resp.status_code == 400

    @patch("api.routes.evals.get_eval_store")
    def test_create_run_dataset_missing(self, mock_gs):
        store = _eval_store()
        store.create_run.side_effect = ValueError("no ds")
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/runs",
            json={"agent_name": "x", "dataset_id": "bad"},
        )
        assert resp.status_code == 404

    @patch("api.routes.evals.get_eval_store")
    def test_list_runs(self, mock_gs):
        store = _eval_store()
        store.list_runs.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/runs")
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_get_run(self, mock_gs):
        store = _eval_store()
        store.get_run.return_value = {"id": "run-1", "status": "completed"}
        store.get_results.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/runs/run-1")
        assert resp.status_code == 200
        assert "results" in resp.json()["data"]

    @patch("api.routes.evals.get_eval_store")
    def test_get_run_not_found(self, mock_gs):
        store = _eval_store()
        store.get_run.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/runs/bad")
        assert resp.status_code == 404

    @patch("api.routes.evals.get_eval_store")
    def test_cancel_run(self, mock_gs):
        store = _eval_store()
        store.get_run.return_value = {"id": "run-1", "status": "running"}
        store.update_run_status.return_value = {"id": "run-1", "status": "cancelled"}
        mock_gs.return_value = store
        resp = client.delete("/api/v1/eval/runs/run-1")
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_cancel_run_not_found(self, mock_gs):
        store = _eval_store()
        store.get_run.return_value = None
        mock_gs.return_value = store
        resp = client.delete("/api/v1/eval/runs/bad")
        assert resp.status_code == 404

    @patch("api.routes.evals.get_eval_store")
    def test_cancel_run_completed(self, mock_gs):
        store = _eval_store()
        store.get_run.return_value = {"id": "run-1", "status": "completed"}
        mock_gs.return_value = store
        resp = client.delete("/api/v1/eval/runs/run-1")
        assert resp.status_code == 400


class TestEvalScores:
    @patch("api.routes.evals.get_eval_store")
    def test_score_trend(self, mock_gs):
        store = _eval_store()
        store.get_score_trend.return_value = [{"run_id": "r1", "score": 0.9}]
        mock_gs.return_value = store
        resp = client.get(
            "/api/v1/eval/scores/trend",
            params={"agent_name": "bot"},
        )
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_compare_runs(self, mock_gs):
        store = _eval_store()
        store.compare_runs.return_value = {"diff": {}}
        mock_gs.return_value = store
        resp = client.get(
            "/api/v1/eval/scores/compare",
            params={"run_a": "r1", "run_b": "r2"},
        )
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_compare_runs_not_found(self, mock_gs):
        store = _eval_store()
        store.compare_runs.side_effect = ValueError("nope")
        mock_gs.return_value = store
        resp = client.get(
            "/api/v1/eval/scores/compare",
            params={"run_a": "r1", "run_b": "bad"},
        )
        assert resp.status_code == 404


class TestEvalBadge:
    @patch("api.routes.evals.get_eval_store")
    def test_badge_no_runs(self, mock_gs):
        store = _eval_store()
        store.list_runs.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/badge/bot")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]

    @patch("api.routes.evals.get_eval_store")
    def test_badge_with_score(self, mock_gs):
        store = _eval_store()
        store.list_runs.return_value = [
            {
                "status": "completed",
                "summary": {"metrics": {"correctness": {"mean": 0.85}}},
            }
        ]
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/badge/bot")
        assert resp.status_code == 200
        assert "85%" in resp.text

    @patch("api.routes.evals.get_eval_store")
    def test_badge_low_score(self, mock_gs):
        store = _eval_store()
        store.list_runs.return_value = [
            {
                "status": "completed",
                "summary": {"metrics": {"correctness": {"mean": 0.4}}},
            }
        ]
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/badge/bot")
        assert resp.status_code == 200
        assert "#e05d44" in resp.text

    @patch("api.routes.evals.get_eval_store")
    def test_badge_medium_score(self, mock_gs):
        store = _eval_store()
        store.list_runs.return_value = [
            {
                "status": "completed",
                "summary": {"metrics": {"relevance": {"mean": 0.65}}},
            }
        ]
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/badge/bot")
        assert resp.status_code == 200
        assert "#dfb317" in resp.text

    @patch("api.routes.evals.get_eval_store")
    def test_badge_no_metrics(self, mock_gs):
        store = _eval_store()
        store.list_runs.return_value = [
            {
                "status": "completed",
                "summary": {"metrics": {}},
            }
        ]
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/badge/bot")
        assert resp.status_code == 200
        assert "no data" in resp.text


class TestEvalSchedules:
    @patch("api.routes.evals.get_eval_store")
    def test_create_schedule(self, mock_gs):
        store = _eval_store()
        store.create_schedule.return_value = {"id": "s1", "cron": "0 0 * * *"}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/schedules",
            json={
                "agent_name": "bot",
                "dataset_id": "ds-1",
                "cron": "0 0 * * *",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.evals.get_eval_store")
    def test_create_schedule_missing(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post("/api/v1/eval/schedules", json={})
        assert resp.status_code == 400

    @patch("api.routes.evals.get_eval_store")
    def test_list_schedules(self, mock_gs):
        store = _eval_store()
        store.list_schedules.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/eval/schedules")
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_delete_schedule(self, mock_gs):
        store = _eval_store()
        store.delete_schedule.return_value = True
        mock_gs.return_value = store
        resp = client.delete("/api/v1/eval/schedules/s1")
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_delete_schedule_not_found(self, mock_gs):
        store = _eval_store()
        store.delete_schedule.return_value = False
        mock_gs.return_value = store
        resp = client.delete("/api/v1/eval/schedules/bad")
        assert resp.status_code == 404


class TestEvalPromoteCheck:
    @patch("api.routes.evals.get_eval_store")
    def test_promote_check(self, mock_gs):
        store = _eval_store()
        store.promote_check.return_value = {"passed": True, "scores": {}}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/eval/promote-check",
            json={"agent_name": "bot"},
        )
        assert resp.status_code == 200

    @patch("api.routes.evals.get_eval_store")
    def test_promote_check_missing_name(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post("/api/v1/eval/promote-check", json={})
        assert resp.status_code == 400


# ===================================================================
# 2. GIT  (api/routes/git.py)
# ===================================================================


def _mock_git_service():
    svc = AsyncMock()
    svc.ensure_repo = AsyncMock()
    return svc


def _mock_pr_service():
    return AsyncMock()


def _make_pr_obj(**kw):
    """Build a real PullRequest instance."""
    from api.services.pr_service import PRStatus, PullRequest

    defaults = {
        "branch": "draft/alice/agent/bot",
        "title": "Update bot",
        "description": "",
        "submitter": "alice",
        "resource_type": "agent",
        "resource_name": "bot",
        "status": PRStatus.draft,
    }
    defaults.update(kw)
    return PullRequest(**defaults)


class TestGitBranches:
    @patch("api.routes.git._get_git")
    def test_create_branch(self, mock_gg):
        svc = _mock_git_service()
        svc.create_branch.return_value = "draft/alice/agent/bot"
        mock_gg.return_value = svc
        resp = client.post(
            "/api/v1/git/branches",
            json={
                "user": "alice",
                "resource_type": "agent",
                "resource_name": "bot",
            },
        )
        assert resp.status_code == 201
        assert "branch" in resp.json()["data"]

    @patch("api.routes.git._get_git")
    def test_create_branch_error(self, mock_gg):
        from api.services.git_service import GitError

        svc = _mock_git_service()
        svc.create_branch.side_effect = GitError("exists")
        mock_gg.return_value = svc
        resp = client.post(
            "/api/v1/git/branches",
            json={
                "user": "a",
                "resource_type": "agent",
                "resource_name": "x",
            },
        )
        assert resp.status_code == 400

    @patch("api.routes.git._get_git")
    def test_list_branches(self, mock_gg):
        svc = _mock_git_service()
        svc.list_branches.return_value = ["b1", "b2"]
        mock_gg.return_value = svc
        resp = client.get("/api/v1/git/branches")
        assert resp.status_code == 200


class TestGitCommits:
    @patch("api.routes.git._get_git")
    def test_create_commit(self, mock_gg):
        svc = _mock_git_service()
        info = MagicMock()
        info.sha = "abc123"
        info.author = "alice"
        info.date = _NOW_ISO
        info.message = "update"
        svc.commit.return_value = info
        mock_gg.return_value = svc
        resp = client.post(
            "/api/v1/git/commits",
            json={
                "branch": "draft/alice/agent/bot",
                "file_path": "agent.yaml",
                "content": "name: bot",
                "message": "update",
                "author": "alice",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["sha"] == "abc123"

    @patch("api.routes.git._get_git")
    def test_create_commit_error(self, mock_gg):
        from api.services.git_service import GitError

        svc = _mock_git_service()
        svc.commit.side_effect = GitError("bad branch")
        mock_gg.return_value = svc
        resp = client.post(
            "/api/v1/git/commits",
            json={
                "branch": "x",
                "file_path": "a.yaml",
                "content": "x",
                "message": "m",
                "author": "a",
            },
        )
        assert resp.status_code == 400


class TestGitDiff:
    @patch("api.routes.git._get_git")
    def test_get_diff(self, mock_gg):
        svc = _mock_git_service()
        diff = MagicMock()
        diff.base = "main"
        diff.head = "draft/x"
        diff.files = []
        diff.stats = "1 file changed, 2 insertions(+)"
        svc.diff.return_value = diff
        mock_gg.return_value = svc
        resp = client.get("/api/v1/git/diff/draft%2Fx")
        assert resp.status_code == 200

    @patch("api.routes.git._get_git")
    def test_get_diff_error(self, mock_gg):
        from api.services.git_service import GitError

        svc = _mock_git_service()
        svc.diff.side_effect = GitError("no branch")
        mock_gg.return_value = svc
        resp = client.get("/api/v1/git/diff/bad")
        assert resp.status_code == 400


class TestGitPRs:
    @patch("api.routes.git._get_pr")
    def test_create_pr(self, mock_gp):
        svc = _mock_pr_service()
        svc.create_pr.return_value = _make_pr_obj()
        mock_gp.return_value = svc
        resp = client.post(
            "/api/v1/git/prs",
            json={
                "branch": "draft/alice/agent/bot",
                "title": "Update bot",
                "description": "desc",
                "submitter": "alice",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.git._get_pr")
    def test_create_pr_error(self, mock_gp):
        from api.services.pr_service import PRError

        svc = _mock_pr_service()
        svc.create_pr.side_effect = PRError("bad")
        mock_gp.return_value = svc
        resp = client.post(
            "/api/v1/git/prs",
            json={
                "branch": "x",
                "title": "t",
                "description": "",
                "submitter": "a",
            },
        )
        assert resp.status_code == 400

    @patch("api.routes.git._get_pr")
    def test_list_prs(self, mock_gp):
        svc = _mock_pr_service()
        svc.list_prs.return_value = [_make_pr_obj()]
        mock_gp.return_value = svc
        resp = client.get("/api/v1/git/prs")
        assert resp.status_code == 200

    @patch("api.routes.git._get_pr")
    def test_get_pr(self, mock_gp):
        pr = _make_pr_obj()
        svc = _mock_pr_service()
        svc.get_pr.return_value = pr
        mock_gp.return_value = svc
        resp = client.get(f"/api/v1/git/prs/{pr.id}")
        assert resp.status_code == 200

    @patch("api.routes.git._get_pr")
    def test_get_pr_not_found(self, mock_gp):
        svc = _mock_pr_service()
        svc.get_pr.return_value = None
        mock_gp.return_value = svc
        resp = client.get(f"/api/v1/git/prs/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch("api.routes.git._get_pr")
    def test_approve_pr(self, mock_gp):
        from api.services.pr_service import PRStatus

        pr = _make_pr_obj(status=PRStatus.approved)
        svc = _mock_pr_service()
        svc.approve.return_value = pr
        mock_gp.return_value = svc
        resp = client.post(
            f"/api/v1/git/prs/{pr.id}/approve",
            json={"reviewer": "bob"},
        )
        assert resp.status_code == 200

    @patch("api.routes.git._get_pr")
    def test_reject_pr(self, mock_gp):
        from api.services.pr_service import PRStatus

        pr = _make_pr_obj(status=PRStatus.rejected)
        svc = _mock_pr_service()
        svc.reject.return_value = pr
        mock_gp.return_value = svc
        resp = client.post(
            f"/api/v1/git/prs/{pr.id}/reject",
            json={"reviewer": "bob", "reason": "bad"},
        )
        assert resp.status_code == 200

    @patch("api.routes.git._get_pr")
    def test_merge_pr(self, mock_gp):
        from api.services.pr_service import PRStatus

        pr = _make_pr_obj(status=PRStatus.published)
        svc = _mock_pr_service()
        svc.merge_pr.return_value = pr
        mock_gp.return_value = svc
        resp = client.post(
            f"/api/v1/git/prs/{pr.id}/merge",
            json={},
        )
        assert resp.status_code == 200

    @patch("api.routes.git._get_pr")
    def test_add_comment(self, mock_gp):
        pr_id = uuid.uuid4()
        comment = MagicMock()
        comment.id = uuid.uuid4()
        comment.pr_id = pr_id
        comment.author = "alice"
        comment.text = "looks good"
        comment.created_at = _NOW
        svc = _mock_pr_service()
        svc.add_comment.return_value = comment
        mock_gp.return_value = svc
        resp = client.post(
            f"/api/v1/git/prs/{pr_id}/comments",
            json={"author": "alice", "text": "looks good"},
        )
        assert resp.status_code == 201


# ===================================================================
# 3. AGENTOPS  (api/routes/agentops.py)
# ===================================================================


class TestAgentOpsFleet:
    @patch("api.routes.agentops.get_agentops_store")
    def test_fleet_overview(self, mock_gs):
        store = _eval_store()
        store.get_fleet_overview.return_value = {
            "summary": {"total": 3},
            "agents": [],
        }
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/fleet")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 3

    @patch("api.routes.agentops.get_agentops_store")
    def test_fleet_heatmap(self, mock_gs):
        store = _eval_store()
        store.get_fleet_heatmap.return_value = {"total": 2, "cells": []}
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/fleet/heatmap")
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_top_agents(self, mock_gs):
        store = _eval_store()
        store.get_top_agents.return_value = [{"name": "bot", "cost": 10}]
        mock_gs.return_value = store
        resp = client.get(
            "/api/v1/agentops/top-agents",
            params={"metric": "cost", "limit": 3},
        )
        assert resp.status_code == 200


class TestAgentOpsEvents:
    @patch("api.routes.agentops.get_agentops_store")
    def test_get_events(self, mock_gs):
        store = _eval_store()
        store.get_events.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/events")
        assert resp.status_code == 200


class TestAgentOpsTeams:
    @patch("api.routes.agentops.get_agentops_store")
    def test_team_comparison(self, mock_gs):
        store = _eval_store()
        store.get_team_comparison.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/teams")
        assert resp.status_code == 200


class TestAgentOpsIncidents:
    @patch("api.routes.agentops.get_agentops_store")
    def test_list_incidents(self, mock_gs):
        store = _eval_store()
        store.list_incidents.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/incidents")
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_create_incident(self, mock_gs):
        store = _eval_store()
        store.create_incident.return_value = {"id": "inc-1", "title": "Down"}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/agentops/incidents",
            json={
                "agent_name": "bot",
                "title": "Down",
                "severity": "high",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.agentops.get_agentops_store")
    def test_create_incident_missing(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post("/api/v1/agentops/incidents", json={})
        assert resp.status_code == 400

    @patch("api.routes.agentops.get_agentops_store")
    def test_get_incident(self, mock_gs):
        store = _eval_store()
        store.get_incident.return_value = {"id": "inc-1"}
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/incidents/inc-1")
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_get_incident_not_found(self, mock_gs):
        store = _eval_store()
        store.get_incident.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/incidents/bad")
        assert resp.status_code == 404

    @patch("api.routes.agentops.get_agentops_store")
    def test_update_incident(self, mock_gs):
        store = _eval_store()
        store.update_incident.return_value = {"id": "inc-1"}
        mock_gs.return_value = store
        resp = client.put(
            "/api/v1/agentops/incidents/inc-1",
            json={"status": "resolved"},
        )
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_update_incident_not_found(self, mock_gs):
        store = _eval_store()
        store.update_incident.return_value = None
        mock_gs.return_value = store
        resp = client.put(
            "/api/v1/agentops/incidents/bad",
            json={"status": "resolved"},
        )
        assert resp.status_code == 404

    @patch("api.routes.agentops.get_agentops_store")
    def test_execute_action(self, mock_gs):
        store = _eval_store()
        store.execute_action.return_value = {"success": True}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/agentops/incidents/inc-1/actions",
            json={"action": "restart"},
        )
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_execute_action_missing(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post(
            "/api/v1/agentops/incidents/inc-1/actions",
            json={},
        )
        assert resp.status_code == 400

    @patch("api.routes.agentops.get_agentops_store")
    def test_execute_action_failed(self, mock_gs):
        store = _eval_store()
        store.execute_action.return_value = {"success": False, "error": "not found"}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/agentops/incidents/inc-1/actions",
            json={"action": "restart"},
        )
        assert resp.status_code == 404


class TestAgentOpsCanary:
    @patch("api.routes.agentops.get_agentops_store")
    def test_start_canary(self, mock_gs):
        store = _eval_store()
        store.start_canary.return_value = {"id": "c1", "status": "active"}
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/agentops/canary",
            json={
                "agent_name": "bot",
                "version": "2.0.0",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.agentops.get_agentops_store")
    def test_start_canary_missing(self, mock_gs):
        mock_gs.return_value = _eval_store()
        resp = client.post("/api/v1/agentops/canary", json={})
        assert resp.status_code == 400

    @patch("api.routes.agentops.get_agentops_store")
    def test_update_canary(self, mock_gs):
        store = _eval_store()
        store.update_canary.return_value = {"id": "c1"}
        mock_gs.return_value = store
        resp = client.put(
            "/api/v1/agentops/canary/c1",
            json={"traffic_percent": 50},
        )
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_update_canary_not_found(self, mock_gs):
        store = _eval_store()
        store.update_canary.return_value = None
        mock_gs.return_value = store
        resp = client.put(
            "/api/v1/agentops/canary/bad",
            json={"traffic_percent": 50},
        )
        assert resp.status_code == 404

    @patch("api.routes.agentops.get_agentops_store")
    def test_get_canary(self, mock_gs):
        store = _eval_store()
        store.get_canary.return_value = {"id": "c1"}
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/canary/c1")
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_get_canary_not_found(self, mock_gs):
        store = _eval_store()
        store.get_canary.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/canary/bad")
        assert resp.status_code == 404


class TestAgentOpsCosts:
    @patch("api.routes.agentops.get_agentops_store")
    def test_cost_forecast(self, mock_gs):
        store = _eval_store()
        store.get_cost_forecast.return_value = {"projected": 100}
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/costs/forecast")
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_cost_anomalies(self, mock_gs):
        store = _eval_store()
        store.get_cost_anomalies.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/costs/anomalies")
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_cost_suggestions(self, mock_gs):
        store = _eval_store()
        store.get_cost_suggestions.return_value = []
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/costs/suggestions")
        assert resp.status_code == 200


class TestAgentOpsCompliance:
    @patch("api.routes.agentops.get_agentops_store")
    def test_compliance_status(self, mock_gs):
        store = _eval_store()
        store.get_compliance_status.return_value = {"passed": 5}
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/compliance/status")
        assert resp.status_code == 200

    @patch("api.routes.agentops.get_agentops_store")
    def test_compliance_report(self, mock_gs):
        store = _eval_store()
        store.generate_compliance_report.return_value = {"report": "ok"}
        mock_gs.return_value = store
        resp = client.get("/api/v1/agentops/compliance/report")
        assert resp.status_code == 200


# ===================================================================
# 4. MEMORY  (api/routes/memory.py)
# ===================================================================


def _mem_config_model(**kw):
    """Build a mock that acts like a MemoryConfig pydantic model."""
    defaults = {
        "id": str(uuid.uuid4()),
        "name": "default",
        "backend_type": "buffer_window",
        "memory_type": "conversation",
        "max_messages": 100,
        "namespace_pattern": "{agent_id}:{session_id}",
        "scope": "agent",
        "linked_agents": [],
        "description": "",
        "status": "active",
        "created_at": _NOW_ISO,
        "updated_at": _NOW_ISO,
    }
    defaults.update(kw)
    m = MagicMock()
    m.model_dump.return_value = defaults
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _mem_msg_model(**kw):
    defaults = {
        "id": str(uuid.uuid4()),
        "config_id": "cfg1",
        "session_id": "sess1",
        "role": "user",
        "content": "hi",
        "agent_id": None,
        "metadata": {},
        "timestamp": _NOW_ISO,
    }
    defaults.update(kw)
    m = MagicMock()
    m.model_dump.return_value = defaults
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestMemoryConfigs:
    @patch(
        "api.routes.memory.MemoryService.create_config",
        new_callable=AsyncMock,
    )
    def test_create_config(self, mock_cc):
        mock_cc.return_value = _mem_config_model()
        resp = client.post(
            "/api/v1/memory/configs",
            json={
                "name": "default",
                "backend_type": "buffer_window",
                "memory_type": "conversation",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.memory.MemoryService.list_configs",
        new_callable=AsyncMock,
    )
    def test_list_configs(self, mock_lc):
        mock_lc.return_value = ([_mem_config_model()], 1)
        resp = client.get("/api/v1/memory/configs")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 1

    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_get_config(self, mock_gc):
        mock_gc.return_value = _mem_config_model()
        resp = client.get("/api/v1/memory/configs/cfg1")
        assert resp.status_code == 200

    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_get_config_not_found(self, mock_gc):
        mock_gc.return_value = None
        resp = client.get("/api/v1/memory/configs/bad")
        assert resp.status_code == 404

    @patch(
        "api.routes.memory.MemoryService.delete_config",
        new_callable=AsyncMock,
    )
    def test_delete_config(self, mock_dc):
        mock_dc.return_value = True
        resp = client.delete("/api/v1/memory/configs/cfg1")
        assert resp.status_code == 200

    @patch(
        "api.routes.memory.MemoryService.delete_config",
        new_callable=AsyncMock,
    )
    def test_delete_config_not_found(self, mock_dc):
        mock_dc.return_value = False
        resp = client.delete("/api/v1/memory/configs/bad")
        assert resp.status_code == 404


class TestMemoryStats:
    @patch(
        "api.routes.memory.MemoryService.get_stats",
        new_callable=AsyncMock,
    )
    def test_get_stats(self, mock_gs):
        stats = MagicMock()
        stats.model_dump.return_value = {
            "config_id": "cfg1",
            "backend_type": "buffer_window",
            "memory_type": "conversation",
            "message_count": 50,
            "session_count": 5,
            "storage_size_bytes": 1024,
            "linked_agent_count": 2,
        }
        mock_gs.return_value = stats
        resp = client.get("/api/v1/memory/configs/cfg1/stats")
        assert resp.status_code == 200

    @patch(
        "api.routes.memory.MemoryService.get_stats",
        new_callable=AsyncMock,
    )
    def test_get_stats_not_found(self, mock_gs):
        mock_gs.return_value = None
        resp = client.get("/api/v1/memory/configs/bad/stats")
        assert resp.status_code == 404


class TestMemoryMessages:
    @patch(
        "api.routes.memory.MemoryService.store_message",
        new_callable=AsyncMock,
    )
    def test_store_message(self, mock_sm):
        mock_sm.return_value = _mem_msg_model()
        resp = client.post(
            "/api/v1/memory/configs/cfg1/messages",
            json={
                "session_id": "sess1",
                "role": "user",
                "content": "hello",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.memory.MemoryService.store_message",
        new_callable=AsyncMock,
    )
    def test_store_message_not_found(self, mock_sm):
        mock_sm.return_value = None
        resp = client.post(
            "/api/v1/memory/configs/bad/messages",
            json={
                "session_id": "s",
                "role": "user",
                "content": "x",
            },
        )
        assert resp.status_code == 404


class TestMemoryConversations:
    @patch(
        "api.routes.memory.MemoryService.list_conversations",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_list_conversations(self, mock_gc, mock_lc):
        mock_gc.return_value = _mem_config_model()
        conv = MagicMock()
        conv.model_dump.return_value = {
            "session_id": "s1",
            "message_count": 5,
            "first_message_at": _NOW_ISO,
            "last_message_at": _NOW_ISO,
            "agent_id": None,
        }
        mock_lc.return_value = ([conv], 1)
        resp = client.get("/api/v1/memory/configs/cfg1/conversations")
        assert resp.status_code == 200

    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_list_conversations_no_config(self, mock_gc):
        mock_gc.return_value = None
        resp = client.get("/api/v1/memory/configs/bad/conversations")
        assert resp.status_code == 404

    @patch(
        "api.routes.memory.MemoryService.get_conversation",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_get_conversation(self, mock_gc, mock_gconv):
        mock_gc.return_value = _mem_config_model()
        mock_gconv.return_value = [_mem_msg_model()]
        resp = client.get("/api/v1/memory/configs/cfg1/conversations/s1")
        assert resp.status_code == 200

    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_get_conversation_no_config(self, mock_gc):
        mock_gc.return_value = None
        resp = client.get("/api/v1/memory/configs/bad/conversations/s1")
        assert resp.status_code == 404

    @patch(
        "api.routes.memory.MemoryService.delete_conversations",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_delete_conversations(self, mock_gc, mock_dc):
        mock_gc.return_value = _mem_config_model()
        mock_dc.return_value = 3
        resp = client.request(
            "DELETE",
            "/api/v1/memory/configs/cfg1/conversations",
            json={"session_id": "s1"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted_count"] == 3

    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_delete_conversations_no_config(self, mock_gc):
        mock_gc.return_value = None
        resp = client.request(
            "DELETE",
            "/api/v1/memory/configs/bad/conversations",
            json={},
        )
        assert resp.status_code == 404


class TestMemorySearch:
    @patch(
        "api.routes.memory.MemoryService.search_messages",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_search_messages(self, mock_gc, mock_sm):
        mock_gc.return_value = _mem_config_model()
        hit = MagicMock()
        hit.message = _mem_msg_model()
        hit.score = 0.95
        hit.highlight = "hi"
        mock_sm.return_value = [hit]
        resp = client.get(
            "/api/v1/memory/configs/cfg1/search",
            params={"q": "hello"},
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.memory.MemoryService.get_config",
        new_callable=AsyncMock,
    )
    def test_search_messages_no_config(self, mock_gc):
        mock_gc.return_value = None
        resp = client.get(
            "/api/v1/memory/configs/bad/search",
            params={"q": "hi"},
        )
        assert resp.status_code == 404


# ===================================================================
# 5. TRACING  (api/routes/tracing.py)
# ===================================================================


def _trace_data(**kw):
    defaults = {
        "id": str(uuid.uuid4()),
        "trace_id": "t-001",
        "agent_id": None,
        "agent_name": "bot",
        "status": "success",
        "duration_ms": 120,
        "total_tokens": 500,
        "input_tokens": 200,
        "output_tokens": 300,
        "cost_usd": 0.01,
        "model_name": "gpt-4o",
        "input_preview": "hi",
        "output_preview": "hello",
        "error_message": None,
        "metadata": {},
        "started_at": _NOW_ISO,
        "ended_at": _NOW_ISO,
        "created_at": _NOW_ISO,
    }
    defaults.update(kw)
    m = MagicMock()
    m.to_dict.return_value = defaults
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _span_data(**kw):
    defaults = {
        "id": str(uuid.uuid4()),
        "trace_id": "t-001",
        "span_id": "s-001",
        "parent_span_id": None,
        "name": "llm_call",
        "span_type": "llm",
        "status": "success",
        "duration_ms": 100,
        "input_data": None,
        "output_data": None,
        "model_name": "gpt-4o",
        "input_tokens": 200,
        "output_tokens": 300,
        "cost_usd": 0.01,
        "metadata": {},
        "started_at": _NOW_ISO,
        "ended_at": _NOW_ISO,
        "children": [],
    }
    defaults.update(kw)
    m = MagicMock()
    m.to_dict.return_value = defaults
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestTracingList:
    @patch("api.routes.tracing.get_tracing_store")
    def test_list_traces(self, mock_gs):
        store = MagicMock()
        store.list_traces.return_value = ([_trace_data()], 1)
        mock_gs.return_value = store
        resp = client.get("/api/v1/traces")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 1

    @patch("api.routes.tracing.get_tracing_store")
    def test_list_traces_search(self, mock_gs):
        store = MagicMock()
        store.search_traces.return_value = ([], 0)
        mock_gs.return_value = store
        resp = client.get("/api/v1/traces", params={"q": "err"})
        assert resp.status_code == 200
        store.search_traces.assert_called_once()

    @patch("api.routes.tracing.get_tracing_store")
    def test_list_traces_filters(self, mock_gs):
        store = MagicMock()
        store.list_traces.return_value = ([], 0)
        mock_gs.return_value = store
        resp = client.get(
            "/api/v1/traces",
            params={
                "agent_name": "bot",
                "status": "error",
            },
        )
        assert resp.status_code == 200


class TestTracingDetail:
    @patch("api.routes.tracing.get_tracing_store")
    def test_get_trace(self, mock_gs):
        store = MagicMock()
        store.get_trace.return_value = _trace_data()
        store.get_trace_spans.return_value = [_span_data()]
        mock_gs.return_value = store
        resp = client.get("/api/v1/traces/t-001")
        assert resp.status_code == 200

    @patch("api.routes.tracing.get_tracing_store")
    def test_get_trace_not_found(self, mock_gs):
        store = MagicMock()
        store.get_trace.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/traces/bad")
        assert resp.status_code == 404


class TestTracingCreate:
    @patch("api.routes.tracing.get_tracing_store")
    def test_create_trace(self, mock_gs):
        store = MagicMock()
        store.get_trace.return_value = None
        store.create_trace.return_value = _trace_data()
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/traces",
            json={
                "trace_id": "t-new",
                "agent_name": "bot",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.tracing.get_tracing_store")
    def test_create_trace_duplicate(self, mock_gs):
        store = MagicMock()
        store.get_trace.return_value = _trace_data()
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/traces",
            json={
                "trace_id": "t-001",
                "agent_name": "bot",
            },
        )
        assert resp.status_code == 409


class TestTracingSpans:
    @patch("api.routes.tracing.get_tracing_store")
    def test_create_span(self, mock_gs):
        store = MagicMock()
        store.get_trace.return_value = _trace_data()
        store.create_span.return_value = _span_data()
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/traces/t-001/spans",
            json={
                "span_id": "s-new",
                "name": "tool_call",
            },
        )
        assert resp.status_code == 201

    @patch("api.routes.tracing.get_tracing_store")
    def test_create_span_trace_missing(self, mock_gs):
        store = MagicMock()
        store.get_trace.return_value = None
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/traces/bad/spans",
            json={"span_id": "s", "name": "x"},
        )
        assert resp.status_code == 404


class TestTracingMetrics:
    @patch("api.routes.tracing.get_tracing_store")
    def test_agent_metrics(self, mock_gs):
        store = MagicMock()
        store.get_agent_metrics.return_value = {
            "agent_name": "bot",
            "total_traces": 10,
            "success_rate": 0.9,
            "avg_duration_ms": 150,
            "total_tokens": 5000,
            "total_cost_usd": 0.5,
            "p50_duration_ms": 100,
            "p95_duration_ms": 300,
            "p99_duration_ms": 500,
            "error_count": 1,
            "daily_breakdown": [],
        }
        mock_gs.return_value = store
        resp = client.get("/api/v1/traces/metrics/bot")
        assert resp.status_code == 200


class TestTracingDelete:
    @patch("api.routes.tracing.get_tracing_store")
    def test_delete_traces(self, mock_gs):
        store = MagicMock()
        store.delete_traces.return_value = 5
        mock_gs.return_value = store
        resp = client.delete(
            "/api/v1/traces",
            params={"before": "2026-01-01T00:00:00"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted_count"] == 5


# ===================================================================
# 6. RAG  (api/routes/rag.py)
# ===================================================================


def _rag_index(**kw):
    defaults = {
        "id": "idx-1",
        "name": "docs",
        "description": "",
        "embedding_model": "openai/text-embedding-3-small",
        "chunk_strategy": "fixed_size",
        "chunk_size": 512,
        "chunk_overlap": 64,
        "source": "manual",
        "status": "active",
        "document_count": 0,
        "chunk_count": 0,
        "created_at": _NOW_ISO,
        "updated_at": _NOW_ISO,
    }
    defaults.update(kw)
    m = MagicMock()
    m.to_dict.return_value = defaults
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestRagIndexes:
    @patch("api.routes.rag.get_rag_store")
    def test_create_index(self, mock_gs):
        store = MagicMock()
        store.create_index.return_value = _rag_index()
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/rag/indexes",
            json={"name": "docs"},
        )
        assert resp.status_code == 201

    @patch("api.routes.rag.get_rag_store")
    def test_create_index_no_name(self, mock_gs):
        mock_gs.return_value = MagicMock()
        resp = client.post("/api/v1/rag/indexes", json={})
        assert resp.status_code == 400

    @patch("api.routes.rag.get_rag_store")
    def test_list_indexes(self, mock_gs):
        store = MagicMock()
        store.list_indexes.return_value = ([_rag_index()], 1)
        mock_gs.return_value = store
        resp = client.get("/api/v1/rag/indexes")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 1

    @patch("api.routes.rag.get_rag_store")
    def test_get_index(self, mock_gs):
        store = MagicMock()
        store.get_index.return_value = _rag_index()
        mock_gs.return_value = store
        resp = client.get("/api/v1/rag/indexes/idx-1")
        assert resp.status_code == 200

    @patch("api.routes.rag.get_rag_store")
    def test_get_index_not_found(self, mock_gs):
        store = MagicMock()
        store.get_index.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/rag/indexes/bad")
        assert resp.status_code == 404

    @patch("api.routes.rag.get_rag_store")
    def test_delete_index(self, mock_gs):
        store = MagicMock()
        store.delete_index.return_value = True
        mock_gs.return_value = store
        resp = client.delete("/api/v1/rag/indexes/idx-1")
        assert resp.status_code == 200

    @patch("api.routes.rag.get_rag_store")
    def test_delete_index_not_found(self, mock_gs):
        store = MagicMock()
        store.delete_index.return_value = False
        mock_gs.return_value = store
        resp = client.delete("/api/v1/rag/indexes/bad")
        assert resp.status_code == 404


class TestRagSearch:
    @patch("api.routes.rag.get_rag_store")
    def test_search(self, mock_gs):
        store = MagicMock()
        store.get_index.return_value = _rag_index()
        hit = MagicMock()
        hit.to_dict.return_value = {"chunk_id": "c1", "score": 0.9}
        store.search = AsyncMock(return_value=[hit])
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/rag/search",
            json={
                "index_id": "idx-1",
                "query": "hello",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 1

    @patch("api.routes.rag.get_rag_store")
    def test_search_missing_fields(self, mock_gs):
        mock_gs.return_value = MagicMock()
        resp = client.post("/api/v1/rag/search", json={})
        assert resp.status_code == 400

    @patch("api.routes.rag.get_rag_store")
    def test_search_index_not_found(self, mock_gs):
        store = MagicMock()
        store.get_index.return_value = None
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/rag/search",
            json={"index_id": "bad", "query": "x"},
        )
        assert resp.status_code == 404


class TestRagIngest:
    @patch("api.routes.rag.get_rag_store")
    def test_ingest_index_not_found(self, mock_gs):
        store = MagicMock()
        store.get_index.return_value = None
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/rag/indexes/bad/ingest",
            files=[("files", ("doc.txt", b"hello", "text/plain"))],
        )
        assert resp.status_code == 404

    @patch("api.routes.rag.get_rag_store")
    def test_ingest_bad_extension(self, mock_gs):
        store = MagicMock()
        store.get_index.return_value = _rag_index()
        mock_gs.return_value = store
        resp = client.post(
            "/api/v1/rag/indexes/idx-1/ingest",
            files=[("files", ("img.png", b"\x89PNG", "image/png"))],
        )
        assert resp.status_code == 400

    @patch("api.routes.rag.get_rag_store")
    def test_get_ingest_job(self, mock_gs):
        store = MagicMock()
        job = MagicMock()
        job.index_id = "idx-1"
        job.to_dict.return_value = {"id": "j1", "status": "completed"}
        store.get_ingest_job.return_value = job
        mock_gs.return_value = store
        resp = client.get("/api/v1/rag/indexes/idx-1/ingest/j1")
        assert resp.status_code == 200

    @patch("api.routes.rag.get_rag_store")
    def test_get_ingest_job_not_found(self, mock_gs):
        store = MagicMock()
        store.get_ingest_job.return_value = None
        mock_gs.return_value = store
        resp = client.get("/api/v1/rag/indexes/idx-1/ingest/bad")
        assert resp.status_code == 404


# ===================================================================
# 7. AUDIT  (api/routes/audit.py)
# ===================================================================


def _audit_event_model(**kw):
    defaults = {
        "id": uuid.uuid4(),
        "actor": "alice",
        "actor_id": None,
        "action": "deploy",
        "resource_type": "agent",
        "resource_id": "a1",
        "resource_name": "bot",
        "team": "eng",
        "details": {},
        "ip_address": None,
        "created_at": _NOW,
    }
    defaults.update(kw)
    m = MagicMock()
    m.model_dump.return_value = defaults
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestAuditEvents:
    @patch(
        "api.routes.audit.AuditService.list_events",
        new_callable=AsyncMock,
    )
    def test_list_events(self, mock_le):
        mock_le.return_value = ([_audit_event_model()], 1)
        resp = client.get("/api/v1/audit")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 1

    @patch(
        "api.routes.audit.AuditService.list_events",
        new_callable=AsyncMock,
    )
    def test_list_events_with_filters(self, mock_le):
        mock_le.return_value = ([], 0)
        resp = client.get(
            "/api/v1/audit",
            params={
                "actor": "alice",
                "action": "deploy",
                "team": "eng",
            },
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.audit.AuditService.list_events",
        new_callable=AsyncMock,
    )
    def test_list_events_date_filters(self, mock_le):
        mock_le.return_value = ([], 0)
        resp = client.get(
            "/api/v1/audit",
            params={
                "date_from": "2026-01-01",
                "date_to": "2026-02-01",
            },
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.audit.AuditService.get_events_for_resource",
        new_callable=AsyncMock,
    )
    def test_get_events_for_resource(self, mock_ge):
        mock_ge.return_value = [_audit_event_model()]
        resp = client.get("/api/v1/audit/resource/agent/a1")
        assert resp.status_code == 200

    @patch(
        "api.routes.audit.AuditService.log_event",
        new_callable=AsyncMock,
    )
    def test_record_audit_event(self, mock_log):
        mock_log.return_value = _audit_event_model()
        resp = client.post(
            "/api/v1/audit",
            json={
                "actor": "alice",
                "action": "deploy",
                "resource_type": "agent",
                "resource_name": "bot",
            },
        )
        assert resp.status_code == 201


class TestLineage:
    @patch(
        "api.routes.audit.AuditService.get_lineage_graph",
        new_callable=AsyncMock,
    )
    def test_get_lineage_graph(self, mock_lg):
        graph = MagicMock()
        node = MagicMock()
        node.model_dump.return_value = {
            "id": "n1",
            "name": "bot",
            "type": "agent",
            "status": "active",
        }
        edge = MagicMock()
        edge.model_dump.return_value = {
            "source_id": "n1",
            "target_id": "n2",
            "dependency_type": "uses",
        }
        graph.nodes = [node]
        graph.edges = [edge]
        mock_lg.return_value = graph
        resp = client.get("/api/v1/lineage/agent/a1")
        assert resp.status_code == 200

    @patch(
        "api.routes.audit.AuditService.get_impact_analysis",
        new_callable=AsyncMock,
    )
    def test_get_impact_analysis(self, mock_ia):
        analysis = MagicMock()
        analysis.resource_name = "tool-a"
        analysis.resource_type = "tool"
        affected = MagicMock()
        affected.name = "bot"
        affected.dependency_type = "uses"
        analysis.affected_agents = [affected]
        mock_ia.return_value = analysis
        resp = client.get("/api/v1/lineage/impact/tool/tool-a")
        assert resp.status_code == 200

    @patch(
        "api.routes.audit.AuditService.register_dependency",
        new_callable=AsyncMock,
    )
    def test_register_dependency(self, mock_rd):
        dep = MagicMock()
        dep.model_dump.return_value = {
            "id": uuid.uuid4(),
            "source_type": "agent",
            "source_id": "a1",
            "source_name": "bot",
            "target_type": "tool",
            "target_id": "t1",
            "target_name": "search",
            "dependency_type": "uses",
            "created_at": _NOW,
        }
        mock_rd.return_value = dep
        resp = client.post(
            "/api/v1/lineage/dependencies",
            json={
                "source_type": "agent",
                "source_id": "a1",
                "source_name": "bot",
                "target_type": "tool",
                "target_id": "t1",
                "target_name": "search",
                "dependency_type": "uses",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.audit.AuditService.sync_agent_dependencies",
        new_callable=AsyncMock,
    )
    def test_sync_agent_dependencies(self, mock_sd):
        dep = MagicMock()
        dep.model_dump.return_value = {
            "id": uuid.uuid4(),
            "source_type": "agent",
            "source_id": "a1",
            "source_name": "bot",
            "target_type": "tool",
            "target_id": "t1",
            "target_name": "s",
            "dependency_type": "uses",
            "created_at": _NOW,
        }
        mock_sd.return_value = [dep]
        resp = client.post(
            "/api/v1/lineage/sync/bot",
            json={"tools": ["search"]},
        )
        assert resp.status_code == 200


# ===================================================================
# 8. MCP SERVERS  (api/routes/mcp_servers.py)
# ===================================================================


def _mcp_server_mock(**kw):
    defaults = {
        "id": str(uuid.uuid4()),
        "name": "test-mcp",
        "endpoint": "http://localhost:3000",
        "transport": "stdio",
        "status": "active",
        "tools": [],
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kw)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestMcpServersCRUD:
    @patch(
        "api.routes.mcp_servers.McpServerRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list(self, mock_list):
        mock_list.return_value = ([_mcp_server_mock()], 1)
        resp = client.get("/api/v1/mcp-servers")
        assert resp.status_code == 200

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get(self, mock_get):
        mock_get.return_value = _mcp_server_mock()
        resp = client.get("/api/v1/mcp-servers/s1")
        assert resp.status_code == 200

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.get("/api/v1/mcp-servers/bad")
        assert resp.status_code == 404

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.create",
        new_callable=AsyncMock,
    )
    def test_create(self, mock_create):
        mock_create.return_value = _mcp_server_mock()
        resp = client.post(
            "/api/v1/mcp-servers",
            json={
                "name": "new-mcp",
                "endpoint": "http://localhost:3001",
                "transport": "stdio",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update(self, mock_upd):
        mock_upd.return_value = _mcp_server_mock()
        resp = client.put(
            "/api/v1/mcp-servers/s1",
            json={"name": "updated"},
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update_not_found(self, mock_upd):
        mock_upd.return_value = None
        resp = client.put(
            "/api/v1/mcp-servers/bad",
            json={"name": "x"},
        )
        assert resp.status_code == 404

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete(self, mock_del):
        mock_del.return_value = True
        resp = client.delete("/api/v1/mcp-servers/s1")
        assert resp.status_code == 200

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete_not_found(self, mock_del):
        mock_del.return_value = False
        resp = client.delete("/api/v1/mcp-servers/bad")
        assert resp.status_code == 404


class TestMcpServersActions:
    @patch(
        "api.routes.mcp_servers.McpServerRegistry.test_connection",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_test_connection(self, mock_get, mock_tc):
        mock_get.return_value = _mcp_server_mock()
        mock_tc.return_value = {
            "success": True,
            "latency_ms": 42,
            "error": None,
        }
        resp = client.post("/api/v1/mcp-servers/s1/test")
        assert resp.status_code == 200

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_test_connection_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.post("/api/v1/mcp-servers/bad/test")
        assert resp.status_code == 404

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.discover_tools",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_discover_tools(self, mock_get, mock_dt):
        mock_get.return_value = _mcp_server_mock()
        mock_dt.return_value = {
            "tools": [
                {
                    "name": "read",
                    "description": "Read file",
                    "input_schema": {},
                }
            ],
            "total": 1,
        }
        resp = client.post("/api/v1/mcp-servers/s1/discover")
        assert resp.status_code == 200

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_discover_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.post("/api/v1/mcp-servers/bad/discover")
        assert resp.status_code == 404

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.execute_tool",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_execute_tool(self, mock_get, mock_et):
        mock_get.return_value = _mcp_server_mock()
        mock_et.return_value = {"result": "ok"}
        resp = client.post(
            "/api/v1/mcp-servers/s1/execute",
            params={"tool_name": "read"},
            json={"path": "/tmp"},
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.mcp_servers.McpServerRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_execute_tool_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.post(
            "/api/v1/mcp-servers/bad/execute",
            params={"tool_name": "read"},
        )
        assert resp.status_code == 404


# ===================================================================
# 9. A2A  (api/routes/a2a.py)
# ===================================================================


def _a2a_agent_mock(**kw):
    defaults = {
        "id": uuid.uuid4(),
        "name": "helper",
        "endpoint_url": "http://localhost:9090",
        "agent_id": uuid.uuid4(),
        "agent_card": {},
        "capabilities": ["chat"],
        "auth_scheme": None,
        "team": "eng",
        "status": "active",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kw)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestA2AAgents:
    @patch(
        "api.routes.a2a.A2AAgentRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list(self, mock_list):
        mock_list.return_value = ([_a2a_agent_mock()], 1)
        resp = client.get("/api/v1/a2a/agents")
        assert resp.status_code == 200

    @patch(
        "api.routes.a2a.A2AAgentRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get(self, mock_get):
        mock_get.return_value = _a2a_agent_mock()
        resp = client.get("/api/v1/a2a/agents/a1")
        assert resp.status_code == 200

    @patch(
        "api.routes.a2a.A2AAgentRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.get("/api/v1/a2a/agents/bad")
        assert resp.status_code == 404

    @patch(
        "api.routes.a2a.A2AAgentRegistry.create",
        new_callable=AsyncMock,
    )
    def test_create(self, mock_create):
        mock_create.return_value = _a2a_agent_mock()
        resp = client.post(
            "/api/v1/a2a/agents",
            json={
                "name": "helper",
                "endpoint_url": "http://localhost:9090",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.a2a.A2AAgentRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update(self, mock_upd):
        mock_upd.return_value = _a2a_agent_mock()
        resp = client.put(
            "/api/v1/a2a/agents/a1",
            json={
                "endpoint_url": "http://new:9090",
            },
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.a2a.A2AAgentRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update_not_found(self, mock_upd):
        mock_upd.return_value = None
        resp = client.put(
            "/api/v1/a2a/agents/bad",
            json={"endpoint_url": "http://x"},
        )
        assert resp.status_code == 404

    @patch(
        "api.routes.a2a.A2AAgentRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete(self, mock_del):
        mock_del.return_value = True
        resp = client.delete("/api/v1/a2a/agents/a1")
        assert resp.status_code == 200

    @patch(
        "api.routes.a2a.A2AAgentRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete_not_found(self, mock_del):
        mock_del.return_value = False
        resp = client.delete("/api/v1/a2a/agents/bad")
        assert resp.status_code == 404


class TestA2AInvoke:
    @patch(
        "api.routes.a2a.AgentInvocationClient",
    )
    @patch(
        "api.routes.a2a.A2AAgentRegistry.get_by_name",
        new_callable=AsyncMock,
    )
    def test_invoke(self, mock_gbn, mock_client_cls):
        mock_gbn.return_value = _a2a_agent_mock()
        mock_client = AsyncMock()
        result = MagicMock()
        result.output = "hi"
        result.tokens = 100
        result.latency_ms = 50
        result.status = "success"
        result.error = None
        mock_client.invoke.return_value = result
        mock_client_cls.return_value = mock_client
        resp = client.post(
            "/api/v1/a2a/invoke",
            params={"agent_name": "helper"},
            json={"input_message": "hello"},
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.a2a.A2AAgentRegistry.get_by_name",
        new_callable=AsyncMock,
    )
    def test_invoke_not_found(self, mock_gbn):
        mock_gbn.return_value = None
        resp = client.post(
            "/api/v1/a2a/invoke",
            params={"agent_name": "bad"},
            json={"input_message": "x"},
        )
        assert resp.status_code == 404


# ===================================================================
# 10. MARKETPLACE  (api/routes/marketplace.py)
# ===================================================================


class TestMarketplaceBrowse:
    @patch(
        "api.routes.marketplace.MarketplaceRegistry.browse",
        new_callable=AsyncMock,
    )
    def test_browse_empty(self, mock_browse):
        mock_browse.return_value = ([], 0)
        resp = client.get("/api/v1/marketplace/browse")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @patch(
        "api.routes.marketplace.MarketplaceRegistry.browse",
        new_callable=AsyncMock,
    )
    def test_browse_with_results(self, mock_browse):
        listing = MagicMock()
        tmpl = MagicMock()
        tmpl.id = uuid.uuid4()
        tmpl.name = "chatbot"
        tmpl.description = "A chatbot"
        tmpl.category = "customer_support"
        tmpl.framework = "langgraph"
        tmpl.tags = ["chat"]
        tmpl.author = "alice"
        listing.id = uuid.uuid4()
        listing.template = tmpl
        listing.avg_rating = 4.5
        listing.review_count = 10
        listing.install_count = 100
        listing.featured = True
        listing.published_at = _NOW
        mock_browse.return_value = ([listing], 1)
        resp = client.get("/api/v1/marketplace/browse")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1


class TestMarketplaceListings:
    @patch(
        "api.routes.marketplace.MarketplaceRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_listing(self, mock_get):
        listing = MagicMock()
        listing.id = uuid.uuid4()
        listing.template_id = uuid.uuid4()
        listing.submitted_by = "alice"
        listing.status = "approved"
        listing.reviewed_by = "bob"
        listing.reject_reason = None
        listing.featured = False
        listing.avg_rating = 4.0
        listing.review_count = 5
        listing.install_count = 50
        listing.published_at = _NOW
        listing.created_at = _NOW
        listing.updated_at = _NOW
        listing.template = None
        mock_get.return_value = listing
        resp = client.get(f"/api/v1/marketplace/listings/{listing.id}")
        assert resp.status_code == 200

    @patch(
        "api.routes.marketplace.MarketplaceRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_listing_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.get(f"/api/v1/marketplace/listings/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch(
        "api.routes.marketplace.MarketplaceRegistry.increment_install_count",
        new_callable=AsyncMock,
    )
    def test_install(self, mock_inc):
        mock_inc.return_value = None
        lid = uuid.uuid4()
        resp = client.post(f"/api/v1/marketplace/listings/{lid}/install")
        assert resp.status_code == 200
        assert resp.json()["data"]["installed"] is True

    @patch(
        "api.routes.marketplace.MarketplaceRegistry.get_reviews",
        new_callable=AsyncMock,
    )
    def test_list_reviews(self, mock_gr):
        mock_gr.return_value = ([], 0)
        lid = uuid.uuid4()
        resp = client.get(f"/api/v1/marketplace/listings/{lid}/reviews")
        assert resp.status_code == 200


# ===================================================================
# 11. PROVIDERS  (api/routes/providers.py)
# ===================================================================


def _provider_mock(**kw):
    defaults = {
        "id": uuid.uuid4(),
        "name": "OpenAI",
        "provider_type": "openai",
        "base_url": "https://api.openai.com/v1",
        "status": "active",
        "is_enabled": True,
        "config": {},
        "models_count": 5,
        "last_health_check": _NOW,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kw)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestProvidersCRUD:
    @patch(
        "api.routes.providers.ProviderRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_providers(self, mock_list):
        mock_list.return_value = ([_provider_mock()], 1)
        resp = client.get("/api/v1/providers")
        assert resp.status_code == 200

    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    def test_get_provider(self, mock_get):
        p = _provider_mock()
        mock_get.return_value = p
        resp = client.get(f"/api/v1/providers/{p.id}")
        assert resp.status_code == 200

    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    def test_get_provider_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.get(f"/api/v1/providers/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch(
        "api.routes.providers.ProviderRegistry.create",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_create_provider(self, mock_gu, mock_cr):
        mock_gu.return_value = _make_mock_user()
        mock_cr.return_value = _provider_mock()
        resp = client.post(
            "/api/v1/providers",
            headers=_auth_headers(),
            json={
                "name": "OpenAI",
                "provider_type": "openai",
                "base_url": "https://api.openai.com/v1",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.providers.ProviderRegistry.update",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_update_provider(self, mock_gu, mock_get, mock_upd):
        mock_gu.return_value = _make_mock_user()
        p = _provider_mock()
        mock_get.return_value = p
        mock_upd.return_value = p
        resp = client.put(
            f"/api/v1/providers/{p.id}",
            headers=_auth_headers(),
            json={"name": "Updated"},
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_update_provider_not_found(self, mock_gu, mock_get):
        mock_gu.return_value = _make_mock_user()
        mock_get.return_value = None
        resp = client.put(
            f"/api/v1/providers/{uuid.uuid4()}",
            headers=_auth_headers(),
            json={"name": "x"},
        )
        assert resp.status_code == 404

    @patch(
        "api.routes.providers.ProviderRegistry.delete",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_delete_provider(self, mock_gu, mock_get, mock_del):
        mock_gu.return_value = _make_mock_user()
        p = _provider_mock()
        mock_get.return_value = p
        mock_del.return_value = None
        resp = client.delete(
            f"/api/v1/providers/{p.id}",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_delete_provider_not_found(self, mock_gu, mock_get):
        mock_gu.return_value = _make_mock_user()
        mock_get.return_value = None
        resp = client.delete(
            f"/api/v1/providers/{uuid.uuid4()}",
            headers=_auth_headers(),
        )
        assert resp.status_code == 404


class TestProviderActions:
    @patch(
        "api.routes.providers.ProviderRegistry.test_connection",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_test_provider(self, mock_gu, mock_get, mock_tc):
        mock_gu.return_value = _make_mock_user()
        p = _provider_mock()
        mock_get.return_value = p
        mock_tc.return_value = {
            "success": True,
            "latency_ms": 100,
            "error": None,
        }
        resp = client.post(
            f"/api/v1/providers/{p.id}/test",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_test_provider_not_found(self, mock_gu, mock_get):
        mock_gu.return_value = _make_mock_user()
        mock_get.return_value = None
        resp = client.post(
            f"/api/v1/providers/{uuid.uuid4()}/test",
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    @patch(
        "api.routes.providers.ProviderRegistry.auto_register_models",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.providers.ProviderRegistry.discover_models",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_discover_models(self, mock_gu, mock_get, mock_dm, mock_ar):
        mock_gu.return_value = _make_mock_user()
        p = _provider_mock()
        mock_get.return_value = p
        mock_dm.return_value = [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "context_window": 128000,
            }
        ]
        mock_ar.return_value = None
        resp = client.post(
            f"/api/v1/providers/{p.id}/discover",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.providers.ProviderRegistry.toggle",
        new_callable=AsyncMock,
    )
    @patch(
        "api.routes.providers.ProviderRegistry.get",
        new_callable=AsyncMock,
    )
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_toggle_provider(self, mock_gu, mock_get, mock_toggle):
        mock_gu.return_value = _make_mock_user()
        p = _provider_mock()
        mock_get.return_value = p
        mock_toggle.return_value = p
        resp = client.post(
            f"/api/v1/providers/{p.id}/toggle",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200


class TestPullOllamaModel:
    """Tests for ``POST /api/v1/providers/{id}/pull-model`` (#214)."""

    @patch("api.routes.providers.ProviderRegistry.get", new_callable=AsyncMock)
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_returns_404_when_provider_missing(self, mock_gu, mock_get):
        mock_gu.return_value = _make_mock_user()
        mock_get.return_value = None
        resp = client.post(
            f"/api/v1/providers/{uuid.uuid4()}/pull-model",
            json={"model": "llama3.2"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    @patch("api.routes.providers.ProviderRegistry.get", new_callable=AsyncMock)
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_returns_400_when_provider_not_ollama(self, mock_gu, mock_get):
        mock_gu.return_value = _make_mock_user()
        # _provider_mock defaults to provider_type="openai"
        mock_get.return_value = _provider_mock()
        resp = client.post(
            f"/api/v1/providers/{uuid.uuid4()}/pull-model",
            json={"model": "llama3.2"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        assert "ollama" in resp.json()["detail"].lower()

    @patch("api.routes.providers.ProviderRegistry.get", new_callable=AsyncMock)
    @patch("api.auth.get_user_by_id", new_callable=AsyncMock)
    def test_streams_pull_events_for_ollama_provider(self, mock_gu, mock_get):
        from api.models.enums import ProviderType

        mock_gu.return_value = _make_mock_user()
        mock_get.return_value = _provider_mock(
            provider_type=ProviderType.ollama, base_url="http://localhost:11434"
        )

        async def fake_pull(self, model_name):
            yield {"status": "pulling manifest"}
            yield {"status": "downloading", "digest": "abc", "total": 1000, "completed": 500}
            yield {"status": "success"}

        async def fake_close(self):
            return None

        with (
            patch(
                "engine.providers.ollama_provider.OllamaProvider.pull_model",
                fake_pull,
            ),
            patch(
                "engine.providers.ollama_provider.OllamaProvider.close",
                fake_close,
            ),
        ):
            resp = client.post(
                f"/api/v1/providers/{uuid.uuid4()}/pull-model",
                json={"model": "llama3.2"},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        body = resp.text
        # SSE format: each event prefixed with "data: " and terminated by blank line
        assert "data: " in body
        assert "pulling manifest" in body
        assert "downloading" in body
        assert "success" in body

    def test_validates_empty_model_field(self):
        # Pydantic validation should reject empty string before we hit the route
        resp = client.post(
            f"/api/v1/providers/{uuid.uuid4()}/pull-model",
            json={"model": ""},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422


class TestProviderStatus:
    @patch(
        "api.routes.providers.ProviderRegistry.list",
        new_callable=AsyncMock,
    )
    def test_provider_status(self, mock_list):
        """Status endpoint uses raw SQL count;
        override get_db dependency."""
        from api.database import get_db

        mock_list.return_value = ([_provider_mock()], 1)
        mock_db = AsyncMock()
        result = MagicMock()
        result.scalar.return_value = 3
        mock_db.execute.return_value = result

        async def _override_db():
            return mock_db

        app.dependency_overrides[get_db] = _override_db
        try:
            resp = client.get("/api/v1/providers/status")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["has_providers"] is True
        finally:
            app.dependency_overrides.pop(get_db, None)


# ===================================================================
# 12. REGISTRY (additional endpoints)
# ===================================================================


class TestRegistryModels:
    @patch(
        "api.routes.registry.ModelRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_models(self, mock_list):
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/registry/models")
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.ModelRegistry.register",
        new_callable=AsyncMock,
    )
    def test_register_model(self, mock_reg):
        m = MagicMock()
        m.id = uuid.uuid4()
        m.name = "gpt-4o"
        m.provider = "openai"
        m.description = "GPT-4o"
        m.config = {}
        m.source = "manual"
        m.status = "active"
        m.context_window = 128000
        m.max_output_tokens = 4096
        m.input_price_per_million = 5.0
        m.output_price_per_million = 15.0
        m.capabilities = ["chat"]
        m.created_at = _NOW
        m.updated_at = _NOW
        # Track G lifecycle fields default to None
        m.discovered_at = None
        m.last_seen_at = None
        m.deprecated_at = None
        m.deprecation_replacement_id = None
        mock_reg.return_value = m
        resp = client.post(
            "/api/v1/registry/models",
            json={
                "name": "gpt-4o",
                "provider": "openai",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.registry.ModelRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_model(self, mock_get):
        m = MagicMock()
        m.id = uuid.uuid4()
        m.name = "gpt-4o"
        m.provider = "openai"
        m.description = ""
        m.config = {}
        m.source = "manual"
        m.status = "active"
        m.context_window = 128000
        m.max_output_tokens = 4096
        m.input_price_per_million = 5.0
        m.output_price_per_million = 15.0
        m.capabilities = []
        m.created_at = _NOW
        m.updated_at = _NOW
        # Track G lifecycle fields default to None
        m.discovered_at = None
        m.last_seen_at = None
        m.deprecated_at = None
        m.deprecation_replacement_id = None
        mock_get.return_value = m
        resp = client.get(f"/api/v1/registry/models/{m.id}")
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.ModelRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_model_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.get(f"/api/v1/registry/models/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestRegistryPrompts:
    @patch(
        "api.routes.registry.PromptRegistry.list",
        new_callable=AsyncMock,
    )
    def test_list_prompts(self, mock_list):
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/registry/prompts")
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.PromptRegistry.register",
        new_callable=AsyncMock,
    )
    def test_register_prompt(self, mock_reg):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.name = "sys-prompt"
        p.version = "1.0.0"
        p.content = "You are helpful"
        p.description = ""
        p.team = "eng"
        p.status = "active"
        p.created_at = _NOW
        p.updated_at = _NOW
        mock_reg.return_value = p
        resp = client.post(
            "/api/v1/registry/prompts",
            json={
                "name": "sys-prompt",
                "version": "1.0.0",
                "content": "You are helpful",
                "team": "eng",
            },
        )
        assert resp.status_code == 201

    @patch(
        "api.routes.registry.PromptRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_prompt(self, mock_get):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.name = "sys-prompt"
        p.version = "1.0.0"
        p.content = "hi"
        p.description = ""
        p.team = "eng"
        p.status = "active"
        p.created_at = _NOW
        p.updated_at = _NOW
        mock_get.return_value = p
        resp = client.get(f"/api/v1/registry/prompts/{p.id}")
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.PromptRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_prompt_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.get(f"/api/v1/registry/prompts/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch(
        "api.routes.registry.PromptRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update_prompt(self, mock_upd):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.name = "sys-prompt"
        p.version = "1.0.0"
        p.content = "updated"
        p.description = "new"
        p.team = "eng"
        p.status = "active"
        p.created_at = _NOW
        p.updated_at = _NOW
        mock_upd.return_value = p
        resp = client.put(
            f"/api/v1/registry/prompts/{p.id}",
            json={"content": "updated"},
        )
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.PromptRegistry.update",
        new_callable=AsyncMock,
    )
    def test_update_prompt_not_found(self, mock_upd):
        mock_upd.return_value = None
        resp = client.put(
            f"/api/v1/registry/prompts/{uuid.uuid4()}",
            json={"content": "x"},
        )
        assert resp.status_code == 404

    @patch(
        "api.routes.registry.PromptRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete_prompt(self, mock_del):
        mock_del.return_value = True
        resp = client.delete(f"/api/v1/registry/prompts/{uuid.uuid4()}")
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.PromptRegistry.delete",
        new_callable=AsyncMock,
    )
    def test_delete_prompt_not_found(self, mock_del):
        mock_del.return_value = False
        resp = client.delete(f"/api/v1/registry/prompts/{uuid.uuid4()}")
        assert resp.status_code == 404

    @patch(
        "api.routes.registry.PromptRegistry.duplicate",
        new_callable=AsyncMock,
    )
    def test_duplicate_prompt(self, mock_dup):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.name = "sys-prompt"
        p.version = "1.0.1"
        p.content = "hi"
        p.description = ""
        p.team = "eng"
        p.status = "active"
        p.created_at = _NOW
        p.updated_at = _NOW
        mock_dup.return_value = p
        resp = client.post(f"/api/v1/registry/prompts/{uuid.uuid4()}/duplicate")
        assert resp.status_code == 201

    @patch(
        "api.routes.registry.PromptRegistry.duplicate",
        new_callable=AsyncMock,
    )
    def test_duplicate_prompt_not_found(self, mock_dup):
        mock_dup.return_value = None
        resp = client.post(f"/api/v1/registry/prompts/{uuid.uuid4()}/duplicate")
        assert resp.status_code == 404

    @patch(
        "api.routes.registry.PromptRegistry.get_versions",
        new_callable=AsyncMock,
    )
    def test_list_prompt_versions(self, mock_gv):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.name = "sys"
        p.version = "1.0.0"
        p.content = "x"
        p.description = ""
        p.team = "eng"
        p.status = "active"
        p.created_at = _NOW
        p.updated_at = _NOW
        mock_gv.return_value = [p]
        resp = client.get(f"/api/v1/registry/prompts/{uuid.uuid4()}/versions")
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.PromptRegistry.get_versions",
        new_callable=AsyncMock,
    )
    def test_list_prompt_versions_not_found(self, mock_gv):
        mock_gv.return_value = []
        resp = client.get(f"/api/v1/registry/prompts/{uuid.uuid4()}/versions")
        assert resp.status_code == 404


class TestRegistryToolDetail:
    @patch(
        "api.routes.registry.ToolRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_tool(self, mock_get):
        t = MagicMock()
        t.id = uuid.uuid4()
        t.name = "search"
        t.description = "Search"
        t.tool_type = "function"
        t.schema_definition = {}
        t.endpoint = "http://x"
        t.status = "active"
        t.source = "manual"
        t.created_at = _NOW
        t.updated_at = _NOW
        mock_get.return_value = t
        resp = client.get(f"/api/v1/registry/tools/{t.id}")
        assert resp.status_code == 200

    @patch(
        "api.routes.registry.ToolRegistry.get_by_id",
        new_callable=AsyncMock,
    )
    def test_get_tool_not_found(self, mock_get):
        mock_get.return_value = None
        resp = client.get(f"/api/v1/registry/tools/{uuid.uuid4()}")
        assert resp.status_code == 404


# ===================================================================
# 13. AUTH SERVICE  (api/services/auth.py)
# ===================================================================


class TestAuthService:
    def test_create_and_decode_token(self):
        from api.services.auth import (
            create_access_token,
            decode_access_token,
        )

        token = create_access_token("u1", "a@b.com", "admin")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "u1"
        assert payload["email"] == "a@b.com"
        assert payload["role"] == "admin"

    def test_decode_invalid_token(self):
        from api.services.auth import decode_access_token

        assert decode_access_token("bad.token.here") is None

    def test_hash_and_verify_password(self):
        from api.services.auth import (
            hash_password,
            verify_password,
        )

        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed)
        assert not verify_password("wrong", hashed)


# ===================================================================
# 14. MAIN APP  (api/main.py)
# ===================================================================


class TestHealthEndpoint:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "agentbreeder-api"
        assert "version" in body


class TestOpenAPISchema:
    def test_openapi_schema_available(self):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert "paths" in resp.json()
