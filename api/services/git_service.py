"""Git backend service — branch management, commits, diffs, tags.

Uses subprocess calls to the `git` CLI (no gitpython dependency).
Operates on a configurable repository root directory where resources
are stored in type-based directories: agents/, prompts/, tools/, rag/, memory/.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default repo root — can be overridden via GARDEN_GIT_REPO_ROOT env var
_DEFAULT_REPO_ROOT = Path(os.environ.get("GARDEN_GIT_REPO_ROOT", "/var/lib/garden/registry"))

# Valid resource types that map to top-level directories in the git repo
RESOURCE_TYPES = frozenset({"agents", "prompts", "tools", "rag", "memory"})

# Semver regex for tag validation
_SEMVER_RE = re.compile(r"^v?\d+\.\d+\.\d+(-[\w.]+)?$")


@dataclass
class CommitInfo:
    """A single git commit."""

    sha: str
    author: str
    date: str
    message: str


@dataclass
class DiffEntry:
    """A single file diff."""

    file_path: str
    status: str  # A(dded), M(odified), D(eleted)
    diff_text: str = ""


@dataclass
class DiffResult:
    """Full diff between two refs."""

    base: str
    head: str
    files: list[DiffEntry] = field(default_factory=list)
    stats: str = ""


class GitError(Exception):
    """Raised when a git operation fails."""


class GitService:
    """Core git operations for the resource registry.

    Parameters
    ----------
    repo_root : Path | None
        Root of the bare/working git repository.  Falls back to the
        ``GARDEN_GIT_REPO_ROOT`` environment variable or a sensible default.
    """

    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or _DEFAULT_REPO_ROOT

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    async def _run(self, *args: str, check: bool = True) -> str:
        """Run a git command and return stdout."""
        cmd = ["git", "-C", str(self.repo_root), *args]
        logger.debug("git: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode().strip()
        stderr = stderr_bytes.decode().strip()

        if check and proc.returncode != 0:
            raise GitError(f"git {args[0]} failed (rc={proc.returncode}): {stderr}")

        return stdout

    async def ensure_repo(self) -> None:
        """Initialise the repository if it does not exist yet."""
        self.repo_root.mkdir(parents=True, exist_ok=True)
        git_dir = self.repo_root / ".git"
        if not git_dir.exists():
            await self._run("init", check=True)
            # Create initial empty commit so main exists
            await self._run("commit", "--allow-empty", "-m", "Initial commit")
            logger.info("Initialised git repository at %s", self.repo_root)

    # ------------------------------------------------------------------
    # Branch management
    # ------------------------------------------------------------------

    async def create_branch(
        self,
        user: str,
        resource_type: str,
        resource_name: str,
    ) -> str:
        """Create a draft branch ``draft/{user}/{type}/{name}`` off main.

        Returns the branch name.
        """
        if resource_type not in RESOURCE_TYPES:
            raise GitError(
                f"Invalid resource_type '{resource_type}'. Must be one of {sorted(RESOURCE_TYPES)}"
            )

        branch = f"draft/{user}/{resource_type}/{resource_name}"
        await self._run("branch", branch, "main")
        logger.info("Created branch %s", branch)
        return branch

    async def list_branches(self, user: str | None = None) -> list[str]:
        """List draft branches, optionally filtered by user."""
        raw = await self._run("branch", "--list", "draft/*", "--format=%(refname:short)")
        if not raw:
            return []
        branches = raw.splitlines()
        if user:
            prefix = f"draft/{user}/"
            branches = [b for b in branches if b.startswith(prefix)]
        return sorted(branches)

    async def delete_branch(self, branch: str) -> None:
        """Delete a branch (only draft branches allowed)."""
        if not branch.startswith("draft/"):
            raise GitError("Can only delete draft/* branches")
        await self._run("branch", "-D", branch)
        logger.info("Deleted branch %s", branch)

    async def current_branch(self) -> str:
        """Return the current branch name."""
        return await self._run("rev-parse", "--abbrev-ref", "HEAD")

    # ------------------------------------------------------------------
    # Commit operations
    # ------------------------------------------------------------------

    async def commit(
        self,
        branch: str,
        file_path: str,
        content: str,
        message: str,
        author: str,
    ) -> CommitInfo:
        """Write *content* to *file_path* on *branch* and commit.

        Switches to branch, writes file, stages, commits, then switches back.
        """
        original_branch = await self.current_branch()
        try:
            await self._run("checkout", branch)

            # Ensure parent directories exist
            abs_path = self.repo_root / file_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")

            await self._run("add", file_path)
            await self._run(
                "commit",
                "-m",
                message,
                f"--author={author} <{author}>",
            )

            sha = await self._run("rev-parse", "HEAD")
            date = await self._run("log", "-1", "--format=%ci")

            return CommitInfo(sha=sha, author=author, date=date, message=message)
        finally:
            # Return to original branch
            await self._run("checkout", original_branch, check=False)

    async def get_log(self, branch: str, limit: int = 20) -> list[CommitInfo]:
        """Return recent commits on *branch*."""
        fmt = "%H|%an|%ci|%s"
        raw = await self._run(
            "log",
            branch,
            f"--format={fmt}",
            f"-{limit}",
        )
        if not raw:
            return []

        commits: list[CommitInfo] = []
        for line in raw.splitlines():
            parts = line.split("|", maxsplit=3)
            if len(parts) == 4:
                commits.append(
                    CommitInfo(
                        sha=parts[0],
                        author=parts[1],
                        date=parts[2],
                        message=parts[3],
                    )
                )
        return commits

    # ------------------------------------------------------------------
    # Diff engine
    # ------------------------------------------------------------------

    async def diff(self, branch: str, base: str = "main") -> DiffResult:
        """Compute the diff between *base* and *branch*.

        Returns a ``DiffResult`` with per-file diffs and summary stats.
        """
        # Name-status summary
        name_status = await self._run(
            "diff",
            "--name-status",
            f"{base}...{branch}",
            check=False,
        )

        files: list[DiffEntry] = []
        for line in (name_status or "").splitlines():
            parts = line.split("\t", maxsplit=1)
            if len(parts) == 2:
                status, fpath = parts
                # Get per-file diff text
                diff_text = await self._run(
                    "diff",
                    f"{base}...{branch}",
                    "--",
                    fpath,
                    check=False,
                )
                files.append(
                    DiffEntry(
                        file_path=fpath,
                        status=status,
                        diff_text=diff_text,
                    )
                )

        # Stat summary
        stats = await self._run(
            "diff",
            "--stat",
            f"{base}...{branch}",
            check=False,
        )

        return DiffResult(base=base, head=branch, files=files, stats=stats)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    async def merge(self, branch: str, target: str = "main") -> CommitInfo:
        """Fast-forward merge *branch* into *target*.

        Raises ``GitError`` if fast-forward is not possible.
        """
        original_branch = await self.current_branch()
        try:
            await self._run("checkout", target)
            await self._run("merge", "--ff-only", branch)

            sha = await self._run("rev-parse", "HEAD")
            date = await self._run("log", "-1", "--format=%ci")
            message = await self._run("log", "-1", "--format=%s")

            return CommitInfo(sha=sha, author="merge", date=date, message=message)
        finally:
            await self._run("checkout", original_branch, check=False)

    # ------------------------------------------------------------------
    # Tagging
    # ------------------------------------------------------------------

    async def tag(self, name: str, ref: str = "HEAD", message: str | None = None) -> str:
        """Create an annotated tag.

        Parameters
        ----------
        name : str
            Tag name, e.g. ``v1.2.0`` or ``agents/my-agent/v1.2.0``.
        ref : str
            The commit ref to tag (defaults to HEAD).
        message : str | None
            Optional tag message.  If omitted a default is generated.

        Returns
        -------
        str
            The tag name that was created.
        """
        msg = message or f"Release {name}"
        await self._run("tag", "-a", name, ref, "-m", msg)
        logger.info("Created tag %s at %s", name, ref)
        return name

    async def list_tags(self, pattern: str | None = None) -> list[str]:
        """List tags, optionally matching a glob *pattern*."""
        args = ["tag", "--list"]
        if pattern:
            args.append(pattern)
        raw = await self._run(*args)
        return raw.splitlines() if raw else []

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def file_content(self, branch: str, file_path: str) -> str | None:
        """Read a file's content on a given branch without checking out."""
        try:
            return await self._run("show", f"{branch}:{file_path}")
        except GitError:
            return None

    async def branch_exists(self, branch: str) -> bool:
        """Check if a branch exists."""
        result = await self._run(
            "rev-parse",
            "--verify",
            f"refs/heads/{branch}",
            check=False,
        )
        return bool(result)
