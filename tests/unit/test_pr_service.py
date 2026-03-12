"""Tests for the Pull Request service."""

from __future__ import annotations

import asyncio
import uuid as uuid_mod
from pathlib import Path

import pytest

from api.services.git_service import GitService
from api.services.pr_service import PRError, PRService, PRStatus, PullRequest


def _run(coro):
    """Helper to run async in sync tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def git_repo(tmp_path: Path) -> GitService:
    """Create a real temporary git repo for testing."""
    svc = GitService(repo_root=tmp_path)
    _run(svc.ensure_repo())
    return svc


@pytest.fixture
def pr_service(git_repo: GitService) -> PRService:
    return PRService(git=git_repo)


def _setup_branch(
    git: GitService,
    user: str = "alice",
    rtype: str = "agents",
    name: str = "test",
) -> str:
    """Helper: create branch and commit a file."""
    branch = _run(git.create_branch(user, rtype, name))
    _run(
        git.commit(
            branch=branch,
            file_path=f"{rtype}/{name}/agent.yaml",
            content=f"name: {name}\nversion: 1.0.0\n",
            message=f"Add {name}",
            author=user,
        )
    )
    return branch


def _make_pr(svc: PRService, branch: str, **kwargs) -> PullRequest:
    """Helper: create a PR with short defaults."""
    defaults = {
        "title": "T",
        "description": "",
        "submitter": "alice",
    }
    defaults.update(kwargs)
    return _run(svc.create_pr(branch=branch, **defaults))


# ------------------------------------------------------------------
# Create PR
# ------------------------------------------------------------------


class TestCreatePR:
    def test_create_pr(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo)
        pr = _run(
            pr_service.create_pr(
                branch=branch,
                title="Add test agent",
                description="Initial version",
                submitter="alice",
            )
        )
        assert isinstance(pr, PullRequest)
        assert pr.title == "Add test agent"
        assert pr.submitter == "alice"
        assert pr.status == PRStatus.submitted
        assert pr.resource_type == "agents"
        assert pr.resource_name == "test"
        assert pr.branch == branch

    def test_create_pr_nonexistent_branch(
        self,
        pr_service: PRService,
    ) -> None:
        with pytest.raises(PRError, match="does not exist"):
            _run(
                pr_service.create_pr(
                    branch="draft/ghost/agents/nope",
                    title="Bad PR",
                    description="",
                    submitter="ghost",
                )
            )

    def test_create_pr_has_commits(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo)
        pr = _make_pr(pr_service, branch, description="D")
        assert len(pr.commits) > 0

    def test_create_pr_has_diff(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo)
        pr = _make_pr(pr_service, branch, description="D")
        assert pr.diff is not None
        assert len(pr.diff.files) > 0


# ------------------------------------------------------------------
# Get / List
# ------------------------------------------------------------------


class TestGetAndList:
    def test_get_pr(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo)
        pr = _make_pr(pr_service, branch, description="D")
        fetched = _run(pr_service.get_pr(pr.id))
        assert fetched is not None
        assert fetched.id == pr.id

    def test_get_pr_not_found(self, pr_service: PRService) -> None:
        result = _run(pr_service.get_pr(uuid_mod.uuid4()))
        assert result is None

    def test_list_prs(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        b1 = _setup_branch(git_repo, name="a1")
        b2 = _setup_branch(git_repo, name="a2")
        _make_pr(pr_service, b1, title="PR1")
        _make_pr(pr_service, b2, title="PR2", submitter="bob")
        prs = _run(pr_service.list_prs())
        assert len(prs) == 2

    def test_list_prs_filter_by_status(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        b1 = _setup_branch(git_repo, name="f1")
        b2 = _setup_branch(git_repo, name="f2")
        pr1 = _make_pr(pr_service, b1, title="PR1")
        _make_pr(pr_service, b2, title="PR2", submitter="bob")
        _run(pr_service.approve(pr1.id, reviewer="reviewer"))

        submitted = _run(pr_service.list_prs(status=PRStatus.submitted))
        assert len(submitted) == 1
        approved = _run(pr_service.list_prs(status=PRStatus.approved))
        assert len(approved) == 1

    def test_list_prs_filter_by_resource_type(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        ba = _setup_branch(git_repo, rtype="agents", name="rt1")
        bp = _setup_branch(git_repo, rtype="prompts", name="rt2")
        _make_pr(pr_service, ba, title="Agent PR")
        _make_pr(pr_service, bp, title="Prompt PR", submitter="bob")

        agents = _run(pr_service.list_prs(resource_type="agents"))
        assert len(agents) == 1
        assert agents[0].resource_type == "agents"


# ------------------------------------------------------------------
# Comments
# ------------------------------------------------------------------


class TestComments:
    def test_add_comment(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="cmt")
        pr = _make_pr(pr_service, branch)
        comment = _run(
            pr_service.add_comment(pr.id, author="bob", text="Looks good"),
        )
        assert comment.author == "bob"
        assert comment.text == "Looks good"
        assert comment.pr_id == pr.id

    def test_add_comment_pr_not_found(
        self,
        pr_service: PRService,
    ) -> None:
        with pytest.raises(PRError, match="not found"):
            _run(
                pr_service.add_comment(
                    uuid_mod.uuid4(),
                    author="a",
                    text="t",
                )
            )

    def test_comments_appear_on_pr(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="cmt2")
        pr = _make_pr(pr_service, branch)
        _run(pr_service.add_comment(pr.id, author="bob", text="C1"))
        _run(pr_service.add_comment(pr.id, author="carol", text="C2"))
        fetched = _run(pr_service.get_pr(pr.id))
        assert fetched is not None
        assert len(fetched.comments) == 2


# ------------------------------------------------------------------
# Status transitions
# ------------------------------------------------------------------


class TestStatusTransitions:
    def test_approve_from_submitted(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="appr")
        pr = _make_pr(pr_service, branch)
        assert pr.status == PRStatus.submitted
        pr = _run(pr_service.approve(pr.id, reviewer="reviewer"))
        assert pr.status == PRStatus.approved
        assert pr.reviewer == "reviewer"

    def test_reject_from_submitted(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="rej")
        pr = _make_pr(pr_service, branch)
        pr = _run(
            pr_service.reject(
                pr.id,
                reviewer="rev",
                reason="Not ready",
            )
        )
        assert pr.status == PRStatus.rejected
        assert pr.reject_reason == "Not ready"

    def test_request_changes(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="chg")
        pr = _make_pr(pr_service, branch)
        pr = _run(
            pr_service.request_changes(
                pr.id,
                reviewer="rev",
                reason="Fix YAML",
            )
        )
        assert pr.status == PRStatus.changes_requested
        assert pr.reject_reason == "Fix YAML"

    def test_cannot_approve_published(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="pub")
        pr = _make_pr(pr_service, branch)
        _run(pr_service.approve(pr.id, reviewer="rev"))
        _run(pr_service.merge_pr(pr.id))
        with pytest.raises(PRError, match="Cannot transition"):
            _run(pr_service.approve(pr.id, reviewer="rev"))

    def test_resubmit_after_changes_requested(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="resub")
        pr = _make_pr(pr_service, branch)
        _run(
            pr_service.request_changes(
                pr.id,
                reviewer="rev",
                reason="Fix it",
            )
        )
        pr = _run(pr_service.submit_for_review(pr.id))
        assert pr.status == PRStatus.submitted

    def test_resubmit_after_rejected(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="rerej")
        pr = _make_pr(pr_service, branch)
        _run(pr_service.reject(pr.id, reviewer="rev", reason="No"))
        # Rejected -> draft is a valid transition
        pr_obj = pr_service._store[pr.id]
        pr_obj.status = PRStatus.draft
        pr = _run(pr_service.submit_for_review(pr.id))
        assert pr.status == PRStatus.submitted


# ------------------------------------------------------------------
# Merge
# ------------------------------------------------------------------


class TestMerge:
    def test_merge_approved_pr(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="mrg")
        pr = _make_pr(pr_service, branch, title="Merge me")
        _run(pr_service.approve(pr.id, reviewer="rev"))
        pr = _run(pr_service.merge_pr(pr.id))
        assert pr.status == PRStatus.published

        content = _run(
            git_repo.file_content("main", "agents/mrg/agent.yaml"),
        )
        assert content is not None

    def test_merge_unapproved_fails(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="unapp")
        pr = _make_pr(pr_service, branch)
        with pytest.raises(PRError, match="must be approved"):
            _run(pr_service.merge_pr(pr.id))

    def test_merge_with_tag(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="tagged")
        pr = _make_pr(pr_service, branch)
        _run(pr_service.approve(pr.id, reviewer="rev"))
        pr = _run(pr_service.merge_pr(pr.id, tag_version="v1.0.0"))
        assert pr.tag == "agents/tagged/v1.0.0"
        assert pr.status == PRStatus.published

        tags = _run(git_repo.list_tags(pattern="agents/tagged/*"))
        assert "agents/tagged/v1.0.0" in tags

    def test_merge_deletes_branch(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        branch = _setup_branch(git_repo, name="delbr")
        pr = _make_pr(pr_service, branch)
        _run(pr_service.approve(pr.id, reviewer="rev"))
        _run(pr_service.merge_pr(pr.id))
        exists = _run(git_repo.branch_exists(branch))
        assert exists is False

    def test_merge_not_found(self, pr_service: PRService) -> None:
        with pytest.raises(PRError, match="not found"):
            _run(pr_service.merge_pr(uuid_mod.uuid4()))


# ------------------------------------------------------------------
# Full workflow: create -> commit -> PR -> review -> merge -> tag
# ------------------------------------------------------------------


class TestFullWorkflow:
    def test_end_to_end(
        self,
        pr_service: PRService,
        git_repo: GitService,
    ) -> None:
        # 1. Create branch
        branch = _run(
            git_repo.create_branch("alice", "agents", "e2e-agent"),
        )

        # 2. Commit a YAML file
        _run(
            git_repo.commit(
                branch=branch,
                file_path="agents/e2e-agent/agent.yaml",
                content="name: e2e-agent\nversion: 1.0.0\n",
                message="Initial e2e agent",
                author="alice",
            )
        )

        # 3. Create PR
        pr = _run(
            pr_service.create_pr(
                branch=branch,
                title="Add e2e-agent",
                description="Full workflow test",
                submitter="alice",
            )
        )
        assert pr.status == PRStatus.submitted

        # 4. Add a review comment
        _run(
            pr_service.add_comment(
                pr.id,
                author="bob",
                text="LGTM",
            )
        )

        # 5. Approve
        pr = _run(pr_service.approve(pr.id, reviewer="bob"))
        assert pr.status == PRStatus.approved

        # 6. Merge with tag
        pr = _run(
            pr_service.merge_pr(pr.id, tag_version="v1.0.0"),
        )
        assert pr.status == PRStatus.published
        assert pr.tag == "agents/e2e-agent/v1.0.0"

        # 7. Verify file on main
        content = _run(
            git_repo.file_content(
                "main",
                "agents/e2e-agent/agent.yaml",
            )
        )
        assert content is not None
        assert "e2e-agent" in content

        # 8. Verify tag exists
        tags = _run(git_repo.list_tags())
        assert "agents/e2e-agent/v1.0.0" in tags

        # 9. Branch cleaned up
        assert _run(git_repo.branch_exists(branch)) is False
