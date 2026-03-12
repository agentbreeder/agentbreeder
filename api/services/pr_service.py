"""Pull Request service — internal PR model for resource version governance.

Manages the full lifecycle:
    draft -> submitted -> in_review -> approved | changes_requested | rejected -> published

Every PR is backed by a git branch and merged via the GitService on approval.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from api.services.git_service import CommitInfo, DiffResult, GitService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PR Status state machine
# ---------------------------------------------------------------------------


class PRStatus(StrEnum):
    draft = "draft"
    submitted = "submitted"
    in_review = "in_review"
    approved = "approved"
    changes_requested = "changes_requested"
    rejected = "rejected"
    published = "published"


# Valid status transitions
_TRANSITIONS: dict[PRStatus, set[PRStatus]] = {
    PRStatus.draft: {PRStatus.submitted},
    PRStatus.submitted: {PRStatus.in_review, PRStatus.draft},
    PRStatus.in_review: {
        PRStatus.approved,
        PRStatus.changes_requested,
        PRStatus.rejected,
    },
    PRStatus.approved: {PRStatus.published},
    PRStatus.changes_requested: {PRStatus.submitted, PRStatus.draft},
    PRStatus.rejected: {PRStatus.draft},
    PRStatus.published: set(),  # terminal state
}


# ---------------------------------------------------------------------------
# Data models (in-memory — will be backed by DB table later)
# ---------------------------------------------------------------------------


class PRComment(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    pr_id: uuid.UUID
    author: str
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PullRequest(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    branch: str
    title: str
    description: str = ""
    submitter: str
    resource_type: str = ""
    resource_name: str = ""
    status: PRStatus = PRStatus.draft
    reviewer: str | None = None
    reject_reason: str | None = None
    tag: str | None = None
    comments: list[PRComment] = Field(default_factory=list)
    commits: list[CommitInfo] = Field(default_factory=list)
    diff: DiffResult | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PRError(Exception):
    """Raised when a PR operation is invalid."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PRService:
    """Internal Pull Request management.

    Uses an in-memory store keyed by PR id.  In production this will be
    backed by the ``pull_requests`` and ``pr_comments`` database tables.
    """

    def __init__(self, git: GitService) -> None:
        self.git = git
        self._store: dict[uuid.UUID, PullRequest] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(self, pr: PullRequest, target: PRStatus) -> None:
        """Validate and apply a status transition."""
        allowed = _TRANSITIONS.get(pr.status, set())
        if target not in allowed:
            raise PRError(
                f"Cannot transition from '{pr.status}' to '{target}'. "
                f"Allowed: {sorted(allowed) if allowed else 'none (terminal state)'}"
            )
        pr.status = target
        pr.updated_at = datetime.now(UTC)

    @staticmethod
    def _parse_branch(branch: str) -> tuple[str, str]:
        """Extract (resource_type, resource_name) from a draft branch name."""
        # Expected format: draft/{user}/{resource_type}/{resource_name}
        parts = branch.split("/")
        if len(parts) >= 4 and parts[0] == "draft":
            return parts[2], "/".join(parts[3:])
        return "", ""

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_pr(
        self,
        branch: str,
        title: str,
        description: str,
        submitter: str,
    ) -> PullRequest:
        """Create a new pull request for *branch*.

        The PR starts in ``submitted`` status.
        """
        if not await self.git.branch_exists(branch):
            raise PRError(f"Branch '{branch}' does not exist")

        resource_type, resource_name = self._parse_branch(branch)

        # Gather commits and diff from git
        commits = await self.git.get_log(branch, limit=50)
        diff = await self.git.diff(branch)

        pr = PullRequest(
            branch=branch,
            title=title,
            description=description,
            submitter=submitter,
            resource_type=resource_type,
            resource_name=resource_name,
            status=PRStatus.submitted,
            commits=commits,
            diff=diff,
        )
        self._store[pr.id] = pr
        logger.info("Created PR %s: %s (branch=%s)", pr.id, title, branch)
        return pr

    async def get_pr(self, pr_id: uuid.UUID) -> PullRequest | None:
        """Retrieve a PR by ID, refreshing its diff and commit list."""
        pr = self._store.get(pr_id)
        if pr and pr.status != PRStatus.published:
            # Refresh live data from git
            try:
                pr.commits = await self.git.get_log(pr.branch, limit=50)
                pr.diff = await self.git.diff(pr.branch)
            except Exception:
                logger.debug("Could not refresh PR %s git data", pr_id)
        return pr

    async def list_prs(
        self,
        status: PRStatus | None = None,
        resource_type: str | None = None,
    ) -> list[PullRequest]:
        """List PRs with optional filters."""
        prs = list(self._store.values())
        if status is not None:
            prs = [p for p in prs if p.status == status]
        if resource_type is not None:
            prs = [p for p in prs if p.resource_type == resource_type]
        return sorted(prs, key=lambda p: p.updated_at, reverse=True)

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    async def add_comment(
        self,
        pr_id: uuid.UUID,
        author: str,
        text: str,
    ) -> PRComment:
        """Add a comment to a PR."""
        pr = self._store.get(pr_id)
        if not pr:
            raise PRError(f"PR '{pr_id}' not found")

        comment = PRComment(pr_id=pr_id, author=author, text=text)
        pr.comments.append(comment)
        pr.updated_at = datetime.now(UTC)
        logger.info("Comment added to PR %s by %s", pr_id, author)
        return comment

    # ------------------------------------------------------------------
    # Review actions
    # ------------------------------------------------------------------

    async def submit_for_review(self, pr_id: uuid.UUID) -> PullRequest:
        """Move a draft PR to submitted."""
        pr = self._store.get(pr_id)
        if not pr:
            raise PRError(f"PR '{pr_id}' not found")
        self._transition(pr, PRStatus.submitted)
        return pr

    async def start_review(self, pr_id: uuid.UUID, reviewer: str) -> PullRequest:
        """Move a submitted PR to in_review."""
        pr = self._store.get(pr_id)
        if not pr:
            raise PRError(f"PR '{pr_id}' not found")
        self._transition(pr, PRStatus.in_review)
        pr.reviewer = reviewer
        return pr

    async def approve(self, pr_id: uuid.UUID, reviewer: str) -> PullRequest:
        """Approve a PR that is in_review."""
        pr = self._store.get(pr_id)
        if not pr:
            raise PRError(f"PR '{pr_id}' not found")

        # Auto-transition to in_review if submitted
        if pr.status == PRStatus.submitted:
            self._transition(pr, PRStatus.in_review)
            pr.reviewer = reviewer

        self._transition(pr, PRStatus.approved)
        pr.reviewer = reviewer
        logger.info("PR %s approved by %s", pr_id, reviewer)
        return pr

    async def request_changes(
        self,
        pr_id: uuid.UUID,
        reviewer: str,
        reason: str,
    ) -> PullRequest:
        """Request changes on a PR that is in_review."""
        pr = self._store.get(pr_id)
        if not pr:
            raise PRError(f"PR '{pr_id}' not found")

        if pr.status == PRStatus.submitted:
            self._transition(pr, PRStatus.in_review)
            pr.reviewer = reviewer

        self._transition(pr, PRStatus.changes_requested)
        pr.reviewer = reviewer
        pr.reject_reason = reason
        return pr

    async def reject(
        self,
        pr_id: uuid.UUID,
        reviewer: str,
        reason: str,
    ) -> PullRequest:
        """Reject a PR that is in_review."""
        pr = self._store.get(pr_id)
        if not pr:
            raise PRError(f"PR '{pr_id}' not found")

        if pr.status == PRStatus.submitted:
            self._transition(pr, PRStatus.in_review)
            pr.reviewer = reviewer

        self._transition(pr, PRStatus.rejected)
        pr.reviewer = reviewer
        pr.reject_reason = reason
        logger.info("PR %s rejected by %s: %s", pr_id, reviewer, reason)
        return pr

    # ------------------------------------------------------------------
    # Merge & publish
    # ------------------------------------------------------------------

    async def merge_pr(
        self,
        pr_id: uuid.UUID,
        tag_version: str | None = None,
    ) -> PullRequest:
        """Merge an approved PR into main.

        Performs a fast-forward merge via GitService, optionally tags the
        result with a semver tag, and transitions the PR to ``published``.
        """
        pr = self._store.get(pr_id)
        if not pr:
            raise PRError(f"PR '{pr_id}' not found")

        if pr.status != PRStatus.approved:
            raise PRError(f"PR must be approved before merging (current status: {pr.status})")

        # Merge
        merge_commit = await self.git.merge(pr.branch)
        logger.info("PR %s merged: %s", pr_id, merge_commit.sha)

        # Tag if version supplied
        if tag_version:
            tag_name = tag_version
            if pr.resource_type and pr.resource_name:
                tag_name = f"{pr.resource_type}/{pr.resource_name}/{tag_version}"
            await self.git.tag(
                tag_name,
                ref=merge_commit.sha,
                message=f"Publish {pr.title}",
            )
            pr.tag = tag_name

        # Transition to published
        self._transition(pr, PRStatus.published)

        # Clean up draft branch
        try:
            await self.git.delete_branch(pr.branch)
        except Exception:
            logger.warning("Could not delete branch %s after merge", pr.branch)

        return pr
