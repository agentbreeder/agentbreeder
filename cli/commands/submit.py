"""garden submit — submit a resource for review (creates a PR).

Usage:
    garden submit agent my-agent -m "Updated system prompt for better tone"
    garden submit prompt support-v3 --message "Fix hallucination guardrail"
"""

from __future__ import annotations

import json
import os
import sys

import httpx
import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

API_BASE = os.environ.get("GARDEN_API_URL", "http://localhost:8000")


def _get_client() -> httpx.Client:
    """Create an httpx client with the configured base URL."""
    return httpx.Client(base_url=API_BASE, timeout=30.0)


def _current_user() -> str:
    """Best-effort guess at the current user identity."""
    return os.environ.get("GARDEN_USER", os.environ.get("USER", "anonymous"))


def submit(
    resource_type: str = typer.Argument(
        ...,
        help="Resource type: agent, prompt, tool, model, knowledge-base",
    ),
    name: str = typer.Argument(
        ...,
        help="Resource name (e.g. my-agent, support-prompt-v3)",
    ),
    message: str = typer.Option(
        "",
        "--message",
        "-m",
        help="Description of the changes being submitted",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for CI/scripting)",
    ),
) -> None:
    """Submit a resource for review by creating a pull request.

    This creates a PR on the draft branch for the specified resource and moves
    it into the review pipeline.

    Examples:
        garden submit agent my-agent -m "Improved error handling"
        garden submit prompt support-v3 --message "Updated tone"
    """
    user = _current_user()
    branch = f"draft/{user}/{resource_type}/{name}"
    title = f"Update {resource_type}/{name}"
    description = message or f"Submit {resource_type}/{name} for review"

    if not json_output:
        console.print()
        console.print(
            Panel(
                f"[bold]Submitting[/bold] {resource_type}/[cyan]{name}[/cyan] for review",
                title="Agent Garden",
                border_style="blue",
            )
        )
        console.print()

    try:
        with _get_client() as client:
            response = client.post(
                "/api/v1/git/prs",
                json={
                    "branch": branch,
                    "title": title,
                    "description": description,
                    "submitter": user,
                },
            )
            response.raise_for_status()
            data = response.json()["data"]

    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)

        if json_output:
            sys.stdout.write(json.dumps({"error": detail}) + "\n")
        else:
            console.print(
                Panel(
                    f"[bold red]Submit failed[/bold red]\n\n  {detail}",
                    title="Error",
                    border_style="red",
                )
            )
            console.print()
        raise typer.Exit(code=1) from None

    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
        return

    # Rich output
    pr_id = data["id"]
    status_val = data["status"]
    diff = data.get("diff")

    status_badge = _status_badge(status_val)

    detail_lines = [
        f"  PR ID:     [bold]{pr_id}[/bold]",
        f"  Status:    {status_badge}",
        f"  Branch:    [dim]{data['branch']}[/dim]",
        f"  Submitter: {data['submitter']}",
    ]

    if data.get("description"):
        detail_lines.append(f"  Message:   {data['description']}")

    # Diff summary
    if diff and diff.get("files"):
        files = diff["files"]
        stats = diff.get("stats", {})
        detail_lines.append("")
        detail_lines.append(f"  [bold]Diff:[/bold] {len(files)} file(s) changed")
        if stats:
            adds = stats.get("insertions", 0)
            dels = stats.get("deletions", 0)
            detail_lines.append(f"         [green]+{adds}[/green] / [red]-{dels}[/red]")
        for f in files[:5]:
            status_char = {"added": "A", "modified": "M", "deleted": "D"}.get(
                f.get("status", ""), "?"
            )
            detail_lines.append(f"    [{status_char}] {f['file_path']}")
        if len(files) > 5:
            detail_lines.append(f"    [dim]... and {len(files) - 5} more[/dim]")

    console.print(
        Panel(
            "\n".join(detail_lines),
            title="[green]Submitted for Review[/green]",
            border_style="green",
        )
    )
    console.print()
    console.print(
        f"  [dim]Next steps: ask a reviewer to run [bold]garden review show {pr_id}[/bold][/dim]"
    )
    console.print()


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
        f"Cannot connect to Agent Garden API at {API_BASE}.\n"
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
