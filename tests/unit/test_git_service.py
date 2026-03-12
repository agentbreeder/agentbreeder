"""Tests for the Git backend service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from api.services.git_service import (
    RESOURCE_TYPES,
    CommitInfo,
    DiffResult,
    GitError,
    GitService,
)


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


# ---------------------------------------------------------------------------
# Repository initialisation
# ---------------------------------------------------------------------------


class TestEnsureRepo:
    def test_creates_git_directory(self, tmp_path: Path) -> None:
        svc = GitService(repo_root=tmp_path)
        _run(svc.ensure_repo())
        assert (tmp_path / ".git").is_dir()

    def test_idempotent(self, git_repo: GitService) -> None:
        # Second call should not raise
        _run(git_repo.ensure_repo())
        assert (git_repo.repo_root / ".git").is_dir()


# ---------------------------------------------------------------------------
# Branch management
# ---------------------------------------------------------------------------


class TestBranches:
    def test_create_branch(self, git_repo: GitService) -> None:
        branch = _run(git_repo.create_branch("alice", "agents", "my-agent"))
        assert branch == "draft/alice/agents/my-agent"

    def test_create_branch_invalid_resource_type(self, git_repo: GitService) -> None:
        with pytest.raises(GitError, match="Invalid resource_type"):
            _run(git_repo.create_branch("alice", "invalid", "foo"))

    def test_create_branch_all_resource_types(self, git_repo: GitService) -> None:
        for rtype in sorted(RESOURCE_TYPES):
            branch = _run(git_repo.create_branch("bob", rtype, f"test-{rtype}"))
            assert branch == f"draft/bob/{rtype}/test-{rtype}"

    def test_list_branches_empty(self, git_repo: GitService) -> None:
        branches = _run(git_repo.list_branches())
        assert branches == []

    def test_list_branches(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "a1"))
        _run(git_repo.create_branch("bob", "prompts", "p1"))
        branches = _run(git_repo.list_branches())
        assert len(branches) == 2
        assert "draft/alice/agents/a1" in branches
        assert "draft/bob/prompts/p1" in branches

    def test_list_branches_filtered_by_user(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "a1"))
        _run(git_repo.create_branch("bob", "prompts", "p1"))
        branches = _run(git_repo.list_branches(user="alice"))
        assert len(branches) == 1
        assert branches[0] == "draft/alice/agents/a1"

    def test_delete_branch(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "del-me"))
        _run(git_repo.delete_branch("draft/alice/agents/del-me"))
        branches = _run(git_repo.list_branches())
        assert "draft/alice/agents/del-me" not in branches

    def test_delete_non_draft_branch_rejected(self, git_repo: GitService) -> None:
        with pytest.raises(GitError, match="only delete draft"):
            _run(git_repo.delete_branch("main"))

    def test_branch_exists(self, git_repo: GitService) -> None:
        assert _run(git_repo.branch_exists("main")) is True
        assert _run(git_repo.branch_exists("nonexistent")) is False

    def test_current_branch(self, git_repo: GitService) -> None:
        branch = _run(git_repo.current_branch())
        # After init, should be on main (or master depending on git config)
        assert branch in ("main", "master")


# ---------------------------------------------------------------------------
# Commit operations
# ---------------------------------------------------------------------------


class TestCommit:
    def test_commit_creates_file(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "test-agent"))
        info = _run(
            git_repo.commit(
                branch="draft/alice/agents/test-agent",
                file_path="agents/test-agent/agent.yaml",
                content="name: test-agent\nversion: 1.0.0\n",
                message="Add test agent",
                author="alice",
            )
        )
        assert isinstance(info, CommitInfo)
        assert info.sha
        assert info.author == "alice"
        assert info.message == "Add test agent"

    def test_commit_updates_file(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "upd"))
        _run(
            git_repo.commit(
                branch="draft/alice/agents/upd",
                file_path="agents/upd/agent.yaml",
                content="name: upd\nversion: 1.0.0\n",
                message="v1",
                author="alice",
            )
        )
        info = _run(
            git_repo.commit(
                branch="draft/alice/agents/upd",
                file_path="agents/upd/agent.yaml",
                content="name: upd\nversion: 2.0.0\n",
                message="v2",
                author="alice",
            )
        )
        assert info.message == "v2"

    def test_commit_returns_to_original_branch(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "ret"))
        original = _run(git_repo.current_branch())
        _run(
            git_repo.commit(
                branch="draft/alice/agents/ret",
                file_path="agents/ret/agent.yaml",
                content="name: ret\n",
                message="test",
                author="alice",
            )
        )
        after = _run(git_repo.current_branch())
        assert after == original


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


class TestLog:
    def test_get_log(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "log-test"))
        _run(
            git_repo.commit(
                branch="draft/alice/agents/log-test",
                file_path="agents/log-test/agent.yaml",
                content="name: log-test\n",
                message="first commit",
                author="alice",
            )
        )
        _run(
            git_repo.commit(
                branch="draft/alice/agents/log-test",
                file_path="agents/log-test/agent.yaml",
                content="name: log-test\nversion: 2.0.0\n",
                message="second commit",
                author="bob",
            )
        )
        log = _run(git_repo.get_log("draft/alice/agents/log-test", limit=10))
        assert len(log) >= 2
        messages = [c.message for c in log]
        assert "second commit" in messages
        assert "first commit" in messages

    def test_get_log_respects_limit(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "lim"))
        for i in range(5):
            _run(
                git_repo.commit(
                    branch="draft/alice/agents/lim",
                    file_path="agents/lim/agent.yaml",
                    content=f"v{i}\n",
                    message=f"commit {i}",
                    author="alice",
                )
            )
        log = _run(git_repo.get_log("draft/alice/agents/lim", limit=2))
        assert len(log) == 2


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_shows_added_file(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "diff-test"))
        _run(
            git_repo.commit(
                branch="draft/alice/agents/diff-test",
                file_path="agents/diff-test/agent.yaml",
                content="name: diff-test\n",
                message="add agent",
                author="alice",
            )
        )
        result = _run(git_repo.diff("draft/alice/agents/diff-test"))
        assert isinstance(result, DiffResult)
        assert result.base == "main"
        assert result.head == "draft/alice/agents/diff-test"
        assert len(result.files) == 1
        assert result.files[0].file_path == "agents/diff-test/agent.yaml"
        assert result.files[0].status == "A"

    def test_diff_empty_when_no_changes(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "no-diff"))
        result = _run(git_repo.diff("draft/alice/agents/no-diff"))
        assert len(result.files) == 0


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


class TestMerge:
    def test_fast_forward_merge(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "merge-test"))
        _run(
            git_repo.commit(
                branch="draft/alice/agents/merge-test",
                file_path="agents/merge-test/agent.yaml",
                content="name: merge-test\n",
                message="add agent for merge",
                author="alice",
            )
        )
        result = _run(git_repo.merge("draft/alice/agents/merge-test"))
        assert isinstance(result, CommitInfo)
        assert result.sha

        # The file should now exist on main
        content = _run(git_repo.file_content("main", "agents/merge-test/agent.yaml"))
        assert content is not None
        assert "merge-test" in content


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestTags:
    def test_create_tag(self, git_repo: GitService) -> None:
        tag = _run(git_repo.tag("v1.0.0", message="Release v1.0.0"))
        assert tag == "v1.0.0"

    def test_list_tags(self, git_repo: GitService) -> None:
        _run(git_repo.tag("v1.0.0"))
        _run(git_repo.tag("v1.1.0"))
        tags = _run(git_repo.list_tags())
        assert "v1.0.0" in tags
        assert "v1.1.0" in tags

    def test_list_tags_with_pattern(self, git_repo: GitService) -> None:
        _run(git_repo.tag("v1.0.0"))
        _run(git_repo.tag("agents/my-agent/v1.0.0"))
        tags = _run(git_repo.list_tags(pattern="agents/*"))
        assert len(tags) == 1
        assert tags[0] == "agents/my-agent/v1.0.0"


# ---------------------------------------------------------------------------
# File content
# ---------------------------------------------------------------------------


class TestFileContent:
    def test_read_file_on_branch(self, git_repo: GitService) -> None:
        _run(git_repo.create_branch("alice", "agents", "read-test"))
        _run(
            git_repo.commit(
                branch="draft/alice/agents/read-test",
                file_path="agents/read-test/agent.yaml",
                content="hello: world\n",
                message="test content",
                author="alice",
            )
        )
        content = _run(
            git_repo.file_content(
                "draft/alice/agents/read-test",
                "agents/read-test/agent.yaml",
            )
        )
        assert content == "hello: world"

    def test_read_nonexistent_file(self, git_repo: GitService) -> None:
        content = _run(git_repo.file_content("main", "does/not/exist.yaml"))
        assert content is None
