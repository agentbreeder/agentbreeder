"""Git workflow API routes — branches, commits, diffs, PRs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    GitBranchCreateRequest,
    GitBranchListResponse,
    GitBranchResponse,
    GitCommitRequest,
    GitCommitResponse,
    GitDiffEntry,
    GitDiffResponse,
    GitPRApproveRequest,
    GitPRCommentRequest,
    GitPRCommentResponse,
    GitPRCreateRequest,
    GitPRListResponse,
    GitPRMergeRequest,
    GitPRRejectRequest,
    GitPRResponse,
)
from api.services.git_service import GitError, GitService
from api.services.pr_service import PRError, PRService, PRStatus

router = APIRouter(prefix="/api/v1/git", tags=["git"])

# ---------------------------------------------------------------------------
# Singleton services (replaced by DI / lifespan in production)
# ---------------------------------------------------------------------------
_git_service: GitService | None = None
_pr_service: PRService | None = None


def _get_git() -> GitService:
    global _git_service
    if _git_service is None:
        _git_service = GitService()
    return _git_service


def _get_pr() -> PRService:
    global _pr_service
    if _pr_service is None:
        _pr_service = PRService(git=_get_git())
    return _pr_service


def set_services(git: GitService, pr: PRService) -> None:
    """Override the module-level singletons (useful for testing)."""
    global _git_service, _pr_service
    _git_service = git
    _pr_service = pr


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------


def _pr_to_response(pr: object) -> GitPRResponse:
    """Convert a PullRequest model to the API response schema."""
    from api.services.pr_service import PullRequest  # noqa: F811

    assert isinstance(pr, PullRequest)
    diff_resp = None
    if pr.diff:
        diff_resp = GitDiffResponse(
            base=pr.diff.base,
            head=pr.diff.head,
            files=[
                GitDiffEntry(
                    file_path=f.file_path,
                    status=f.status,
                    diff_text=f.diff_text,
                )
                for f in pr.diff.files
            ],
            stats=pr.diff.stats,
        )

    return GitPRResponse(
        id=pr.id,
        branch=pr.branch,
        title=pr.title,
        description=pr.description,
        submitter=pr.submitter,
        resource_type=pr.resource_type,
        resource_name=pr.resource_name,
        status=pr.status,
        reviewer=pr.reviewer,
        reject_reason=pr.reject_reason,
        tag=pr.tag,
        comments=[
            GitPRCommentResponse(
                id=c.id,
                pr_id=c.pr_id,
                author=c.author,
                text=c.text,
                created_at=c.created_at,
            )
            for c in pr.comments
        ],
        commits=[
            GitCommitResponse(
                sha=c.sha,
                author=c.author,
                date=c.date,
                message=c.message,
            )
            for c in pr.commits
        ],
        diff=diff_resp,
        created_at=pr.created_at,
        updated_at=pr.updated_at,
    )


# ---------------------------------------------------------------------------
# Branch endpoints
# ---------------------------------------------------------------------------


@router.post("/branches", response_model=ApiResponse[GitBranchResponse], status_code=201)
async def create_branch(
    body: GitBranchCreateRequest, _user: User = Depends(require_role("deployer"))
) -> ApiResponse[GitBranchResponse]:
    """Create a draft branch for editing a resource."""
    try:
        git = _get_git()
        await git.ensure_repo()
        branch = await git.create_branch(body.user, body.resource_type, body.resource_name)
        return ApiResponse(data=GitBranchResponse(branch=branch))
    except GitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/branches", response_model=ApiResponse[GitBranchListResponse])
async def list_branches(
    _user: User = Depends(get_current_user),
    user: str | None = Query(None),
) -> ApiResponse[GitBranchListResponse]:
    """List draft branches, optionally filtered by user."""
    git = _get_git()
    branches = await git.list_branches(user=user)
    return ApiResponse(data=GitBranchListResponse(branches=branches))


# ---------------------------------------------------------------------------
# Commit endpoint
# ---------------------------------------------------------------------------


@router.post("/commits", response_model=ApiResponse[GitCommitResponse], status_code=201)
async def create_commit(
    body: GitCommitRequest, _user: User = Depends(require_role("deployer"))
) -> ApiResponse[GitCommitResponse]:
    """Commit a file change on a branch."""
    try:
        git = _get_git()
        info = await git.commit(
            branch=body.branch,
            file_path=body.file_path,
            content=body.content,
            message=body.message,
            author=body.author,
        )
        return ApiResponse(
            data=GitCommitResponse(
                sha=info.sha,
                author=info.author,
                date=info.date,
                message=info.message,
            )
        )
    except GitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Diff endpoint
# ---------------------------------------------------------------------------


@router.get("/diff/{branch:path}", response_model=ApiResponse[GitDiffResponse])
async def get_diff(
    branch: str,
    _user: User = Depends(get_current_user),
    base: str = Query("main"),
) -> ApiResponse[GitDiffResponse]:
    """Get diff between a branch and base (default: main)."""
    try:
        git = _get_git()
        result = await git.diff(branch=branch, base=base)
        return ApiResponse(
            data=GitDiffResponse(
                base=result.base,
                head=result.head,
                files=[
                    GitDiffEntry(
                        file_path=f.file_path,
                        status=f.status,
                        diff_text=f.diff_text,
                    )
                    for f in result.files
                ],
                stats=result.stats,
            )
        )
    except GitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# PR endpoints
# ---------------------------------------------------------------------------


@router.post("/prs", response_model=ApiResponse[GitPRResponse], status_code=201)
async def create_pr(
    body: GitPRCreateRequest, _user: User = Depends(require_role("deployer"))
) -> ApiResponse[GitPRResponse]:
    """Create a pull request for a draft branch."""
    try:
        pr_svc = _get_pr()
        pr = await pr_svc.create_pr(
            branch=body.branch,
            title=body.title,
            description=body.description,
            submitter=body.submitter,
        )
        return ApiResponse(data=_pr_to_response(pr))
    except PRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/prs", response_model=ApiResponse[GitPRListResponse])
async def list_prs(
    _user: User = Depends(get_current_user),
    status: str | None = Query(None),
    resource_type: str | None = Query(None),
) -> ApiResponse[GitPRListResponse]:
    """List pull requests with optional filters."""
    pr_svc = _get_pr()
    status_enum = PRStatus(status) if status else None
    prs = await pr_svc.list_prs(status=status_enum, resource_type=resource_type)
    return ApiResponse(
        data=GitPRListResponse(prs=[_pr_to_response(p) for p in prs]),
        meta=ApiMeta(total=len(prs)),
    )


@router.get("/prs/{pr_id}", response_model=ApiResponse[GitPRResponse])
async def get_pr(
    pr_id: uuid.UUID, _user: User = Depends(get_current_user)
) -> ApiResponse[GitPRResponse]:
    """Get pull request detail with diff and commits."""
    pr_svc = _get_pr()
    pr = await pr_svc.get_pr(pr_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Pull request not found")
    return ApiResponse(data=_pr_to_response(pr))


@router.post("/prs/{pr_id}/approve", response_model=ApiResponse[GitPRResponse])
async def approve_pr(
    pr_id: uuid.UUID,
    body: GitPRApproveRequest,
    _user: User = Depends(require_role("admin")),
) -> ApiResponse[GitPRResponse]:
    """Approve a pull request."""
    try:
        pr_svc = _get_pr()
        pr = await pr_svc.approve(pr_id, reviewer=body.reviewer)
        return ApiResponse(data=_pr_to_response(pr))
    except PRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/prs/{pr_id}/reject", response_model=ApiResponse[GitPRResponse])
async def reject_pr(
    pr_id: uuid.UUID,
    body: GitPRRejectRequest,
    _user: User = Depends(get_current_user),
) -> ApiResponse[GitPRResponse]:
    """Reject a pull request."""
    try:
        pr_svc = _get_pr()
        pr = await pr_svc.reject(pr_id, reviewer=body.reviewer, reason=body.reason)
        return ApiResponse(data=_pr_to_response(pr))
    except PRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/prs/{pr_id}/merge", response_model=ApiResponse[GitPRResponse])
async def merge_pr(
    pr_id: uuid.UUID,
    body: GitPRMergeRequest | None = None,
    _user: User = Depends(get_current_user),
) -> ApiResponse[GitPRResponse]:
    """Merge an approved pull request into main."""
    try:
        pr_svc = _get_pr()
        tag_version = body.tag_version if body else None
        pr = await pr_svc.merge_pr(pr_id, tag_version=tag_version)
        return ApiResponse(data=_pr_to_response(pr))
    except PRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/prs/{pr_id}/comments",
    response_model=ApiResponse[GitPRCommentResponse],
    status_code=201,
)
async def add_comment(
    pr_id: uuid.UUID,
    body: GitPRCommentRequest,
    _user: User = Depends(get_current_user),
) -> ApiResponse[GitPRCommentResponse]:
    """Add a comment to a pull request."""
    try:
        pr_svc = _get_pr()
        comment = await pr_svc.add_comment(pr_id, author=body.author, text=body.text)
        return ApiResponse(
            data=GitPRCommentResponse(
                id=comment.id,
                pr_id=comment.pr_id,
                author=comment.author,
                text=comment.text,
                created_at=comment.created_at,
            )
        )
    except PRError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
