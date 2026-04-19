"""agentbreeder review — review workflow for submitted resources.

Subcommands:
    agentbreeder review list                      — list pending reviews
    agentbreeder review show <pr-id>              — show PR detail with diff
    agentbreeder review approve <pr-id>           — approve a PR
    agentbreeder review reject <pr-id> -m "why"   — reject a PR
    agentbreeder review comment <pr-id> -m "msg"  — add a comment
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()

API_BASE = os.environ.get("AGENTBREEDER_API_URL", "http://localhost:8000")

review_app = typer.Typer(
    name="review",
    help="Review workflow for submitted resources.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client() -> httpx.Client:
    """Create an httpx client with the configured base URL."""
    return httpx.Client(base_url=API_BASE, timeout=30.0)


def _current_user() -> str:
    """Best-effort guess at the current user identity."""
    return os.environ.get("AGENTBREEDER_USER", os.environ.get("USER", "anonymous"))


def _status_badge(status: str) -> str:
    """Return a Rich-formatted status badge."""
    styles = {
        "draft": "[dim]draft[/dim]",
        "submitted": "[yellow]submitted[/yellow]",
        "in_review": "[blue]in review[/blue]",
        "approved": "[green]approved[/green]",
        "changes_requested": "[yellow]changes requested[/yellow]",
        "rejected": "[red]rejected[/red]",
        "published": "[bold green]published[/bold green]",
    }
    return styles.get(status, f"[dim]{status}[/dim]")


def _handle_connection_error(json_output: bool) -> None:
    """Handle API connection errors."""
    msg = (
        f"Cannot connect to AgentBreeder API at {API_BASE}.\n"
        "  Ensure the server is running: uvicorn api.main:app --port 8000"
    )
    if json_output:
        sys.stdout.write(json.dumps({"error": msg}) + "\n")
    else:
        console.print()
        console.print(
            Panel(
                f"[bold red]Connection error[/bold red]\n\n  {msg}",
                title="Error",
                border_style="red",
            )
        )
        console.print()


def _handle_http_error(exc: httpx.HTTPStatusError, json_output: bool) -> None:
    """Handle HTTP status errors from the API."""
    detail = ""
    try:
        detail = exc.response.json().get("detail", str(exc))
    except Exception:
        detail = str(exc)

    if json_output:
        sys.stdout.write(json.dumps({"error": detail}) + "\n")
    else:
        console.print()
        console.print(
            Panel(
                f"[bold red]Request failed[/bold red]\n\n  {detail}",
                title="Error",
                border_style="red",
            )
        )
        console.print()


def _format_time(iso_str: str) -> str:
    """Format an ISO timestamp for display."""
    if not iso_str:
        return "N/A"
    try:
        from datetime import datetime

        # Handle various ISO formats
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_str


def _validate_pr_id(pr_id: str) -> str:
    """Validate that pr_id looks like a UUID."""
    try:
        uuid.UUID(pr_id)
    except ValueError:
        console.print(f"[red]Invalid PR ID: '{pr_id}' (expected UUID)[/red]")
        raise typer.Exit(code=1) from None
    return pr_id


# ---------------------------------------------------------------------------
# agentbreeder review list
# ---------------------------------------------------------------------------


@review_app.command(name="list")
def review_list(
    status_filter: str = typer.Option(
        "submitted",
        "--status",
        "-s",
        help="Filter by status: submitted, in_review, approved, rejected, all",
    ),
    resource_type: str = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by resource type: agent, prompt, tool, etc.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List pull requests pending review.

    Examples:
        agentbreeder review list
        agentbreeder review list --status all
        agentbreeder review list --type agent
    """
    params: dict[str, str] = {}
    if status_filter and status_filter != "all":
        params["status"] = status_filter
    if resource_type:
        params["resource_type"] = resource_type

    try:
        with _get_client() as client:
            response = client.get("/api/v1/git/prs", params=params)
            response.raise_for_status()
            data = response.json()["data"]
            prs = data.get("prs", [])

    except httpx.HTTPStatusError as exc:
        _handle_http_error(exc, json_output)
        raise typer.Exit(code=1) from None
    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json.dumps(prs, indent=2, default=str) + "\n")
        return

    if not prs:
        console.print()
        console.print(
            f"  [dim]No pull requests found"
            f"{f' with status={status_filter}' if status_filter != 'all' else ''}.[/dim]"
        )
        console.print()
        return

    table = Table(title="Pull Requests")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Resource", style="cyan")
    table.add_column("Title")
    table.add_column("Submitter", style="yellow")
    table.add_column("Status")
    table.add_column("Updated", style="dim")

    for pr in prs:
        pr_id_short = pr["id"][:8]
        resource = ""
        if pr.get("resource_type") and pr.get("resource_name"):
            resource = f"{pr['resource_type']}/{pr['resource_name']}"

        table.add_row(
            pr_id_short,
            resource,
            pr.get("title", "")[:50],
            pr.get("submitter", ""),
            _status_badge(pr.get("status", "")),
            _format_time(pr.get("updated_at", "")),
        )

    console.print()
    console.print(table)
    console.print()
    console.print(
        f"  [dim]{len(prs)} result(s). View details: "
        "[bold]agentbreeder review show <pr-id>[/bold][/dim]"
    )
    console.print()


# ---------------------------------------------------------------------------
# agentbreeder review show <pr-id>
# ---------------------------------------------------------------------------


@review_app.command(name="show")
def review_show(
    pr_id: str = typer.Argument(..., help="Pull request ID (UUID)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show detailed pull request information including diff.

    Examples:
        agentbreeder review show abc12345-...
    """
    pr_id = _validate_pr_id(pr_id)

    try:
        with _get_client() as client:
            response = client.get(f"/api/v1/git/prs/{pr_id}")
            response.raise_for_status()
            pr = response.json()["data"]

    except httpx.HTTPStatusError as exc:
        _handle_http_error(exc, json_output)
        raise typer.Exit(code=1) from None
    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json.dumps(pr, indent=2, default=str) + "\n")
        return

    # Header panel
    status_val = pr.get("status", "unknown")
    border = {
        "approved": "green",
        "rejected": "red",
        "published": "green",
    }.get(status_val, "blue")

    resource = ""
    if pr.get("resource_type") and pr.get("resource_name"):
        resource = f"{pr['resource_type']}/{pr['resource_name']}"

    header_lines = [
        f"  [bold]{pr.get('title', '')}[/bold]",
        "",
        f"  PR ID:       [dim]{pr['id']}[/dim]",
        f"  Status:      {_status_badge(status_val)}",
        f"  Branch:      [dim]{pr.get('branch', '')}[/dim]",
        f"  Submitter:   {pr.get('submitter', '')}",
    ]

    if resource:
        header_lines.append(f"  Resource:    [cyan]{resource}[/cyan]")
    if pr.get("reviewer"):
        header_lines.append(f"  Reviewer:    {pr['reviewer']}")
    if pr.get("reject_reason"):
        header_lines.append(f"  Reason:      [red]{pr['reject_reason']}[/red]")
    if pr.get("tag"):
        header_lines.append(f"  Tag:         [green]{pr['tag']}[/green]")
    if pr.get("description"):
        header_lines.append(f"  Description: {pr['description']}")

    header_lines.append(f"  Created:     {_format_time(pr.get('created_at', ''))}")
    header_lines.append(f"  Updated:     {_format_time(pr.get('updated_at', ''))}")

    console.print()
    console.print(Panel("\n".join(header_lines), title="Pull Request", border_style=border))

    # Commits
    commits = pr.get("commits", [])
    if commits:
        console.print()
        commit_table = Table(title="Commits")
        commit_table.add_column("SHA", style="dim", max_width=8)
        commit_table.add_column("Author", style="yellow")
        commit_table.add_column("Message")
        commit_table.add_column("Date", style="dim")

        for c in commits:
            commit_table.add_row(
                c.get("sha", "")[:8],
                c.get("author", ""),
                c.get("message", "")[:60],
                _format_time(c.get("date", "")),
            )

        console.print(commit_table)

    # Diff
    diff = pr.get("diff")
    if diff and diff.get("files"):
        console.print()
        stats = diff.get("stats", {})
        stats_str = ""
        if stats:
            adds = stats.get("insertions", 0)
            dels = stats.get("deletions", 0)
            stats_str = f"  [green]+{adds}[/green] / [red]-{dels}[/red]"

        console.print(f"  [bold]Changes:[/bold] {len(diff['files'])} file(s) {stats_str}")
        console.print()

        for f in diff["files"]:
            status_char = {"added": "A", "modified": "M", "deleted": "D"}.get(
                f.get("status", ""), "?"
            )
            console.print(f"  [{status_char}] [cyan]{f['file_path']}[/cyan]")
            if f.get("diff_text"):
                console.print(
                    Syntax(
                        f["diff_text"],
                        "diff",
                        theme="monokai",
                        line_numbers=False,
                        padding=1,
                    )
                )

    # Comments
    comments = pr.get("comments", [])
    if comments:
        console.print()
        console.print("  [bold]Comments:[/bold]")
        for c in comments:
            console.print()
            console.print(
                Panel(
                    f"  {c.get('text', '')}",
                    title=f"{c.get('author', '')} - {_format_time(c.get('created_at', ''))}",
                    border_style="dim",
                    padding=(0, 1),
                )
            )

    console.print()


# ---------------------------------------------------------------------------
# agentbreeder review approve <pr-id>
# ---------------------------------------------------------------------------


@review_app.command(name="approve")
def review_approve(
    pr_id: str = typer.Argument(..., help="Pull request ID (UUID)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Approve a submitted pull request.

    Examples:
        agentbreeder review approve abc12345-...
    """
    pr_id = _validate_pr_id(pr_id)
    reviewer = _current_user()

    try:
        with _get_client() as client:
            response = client.post(
                f"/api/v1/git/prs/{pr_id}/approve",
                json={"reviewer": reviewer},
            )
            response.raise_for_status()
            pr = response.json()["data"]

    except httpx.HTTPStatusError as exc:
        _handle_http_error(exc, json_output)
        raise typer.Exit(code=1) from None
    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json.dumps(pr, indent=2, default=str) + "\n")
        return

    console.print()
    console.print(
        Panel(
            f"[bold green]PR approved![/bold green]\n\n"
            f"  PR:       [dim]{pr['id']}[/dim]\n"
            f"  Title:    {pr.get('title', '')}\n"
            f"  Reviewer: {reviewer}\n"
            f"  Status:   {_status_badge(pr.get('status', ''))}\n\n"
            f"  [dim]Next: [bold]agentbreeder publish {pr.get('resource_type', 'resource')} "
            f"{pr.get('resource_name', 'name')}[/bold] to merge and tag[/dim]",
            title="Approved",
            border_style="green",
        )
    )
    console.print()


# ---------------------------------------------------------------------------
# agentbreeder review reject <pr-id>
# ---------------------------------------------------------------------------


@review_app.command(name="reject")
def review_reject(
    pr_id: str = typer.Argument(..., help="Pull request ID (UUID)"),
    message: str = typer.Option(
        ...,
        "--message",
        "-m",
        help="Reason for rejection",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Reject a submitted pull request with a reason.

    Examples:
        agentbreeder review reject abc12345-... -m "Missing test coverage"
    """
    pr_id = _validate_pr_id(pr_id)
    reviewer = _current_user()

    try:
        with _get_client() as client:
            response = client.post(
                f"/api/v1/git/prs/{pr_id}/reject",
                json={"reviewer": reviewer, "reason": message},
            )
            response.raise_for_status()
            pr = response.json()["data"]

    except httpx.HTTPStatusError as exc:
        _handle_http_error(exc, json_output)
        raise typer.Exit(code=1) from None
    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json.dumps(pr, indent=2, default=str) + "\n")
        return

    console.print()
    console.print(
        Panel(
            f"[bold red]PR rejected[/bold red]\n\n"
            f"  PR:       [dim]{pr['id']}[/dim]\n"
            f"  Title:    {pr.get('title', '')}\n"
            f"  Reviewer: {reviewer}\n"
            f"  Reason:   {message}",
            title="Rejected",
            border_style="red",
        )
    )
    console.print()


# ---------------------------------------------------------------------------
# agentbreeder review comment <pr-id>
# ---------------------------------------------------------------------------


@review_app.command(name="comment")
def review_comment(
    pr_id: str = typer.Argument(..., help="Pull request ID (UUID)"),
    message: str = typer.Option(
        ...,
        "--message",
        "-m",
        help="Comment text",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Add a comment to a pull request.

    Examples:
        agentbreeder review comment abc12345-... -m "Looks good, minor nit on line 42"
    """
    pr_id = _validate_pr_id(pr_id)
    author = _current_user()

    try:
        with _get_client() as client:
            response = client.post(
                f"/api/v1/git/prs/{pr_id}/comments",
                json={"author": author, "text": message},
            )
            response.raise_for_status()
            comment = response.json()["data"]

    except httpx.HTTPStatusError as exc:
        _handle_http_error(exc, json_output)
        raise typer.Exit(code=1) from None
    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json.dumps(comment, indent=2, default=str) + "\n")
        return

    console.print()
    console.print(
        Panel(
            f"  {message}",
            title=f"[green]Comment added[/green] by {author}",
            border_style="green",
        )
    )
    console.print()
