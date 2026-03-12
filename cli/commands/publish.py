"""garden publish — merge an approved PR, tag, and publish to registry.

Usage:
    garden publish agent my-agent
    garden publish prompt support-v3 --version 2.1.0
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
                f"[bold red]Publish failed[/bold red]\n\n  {detail}",
                title="Error",
                border_style="red",
            )
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


def publish(
    resource_type: str = typer.Argument(
        ...,
        help="Resource type: agent, prompt, tool, model, knowledge-base",
    ),
    name: str = typer.Argument(
        ...,
        help="Resource name (e.g. my-agent, support-prompt-v3)",
    ),
    version: str = typer.Option(
        None,
        "--version",
        "-v",
        help="Explicit semver tag (e.g. 1.2.0). Auto-detected if omitted.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for CI/scripting)",
    ),
) -> None:
    """Merge an approved PR and publish the resource to the registry.

    Finds the most recent approved PR for the given resource, merges the
    branch into main, optionally tags with a semver version, and publishes
    to the Agent Garden registry.

    Examples:
        garden publish agent my-agent
        garden publish prompt support-v3 --version 2.1.0
    """
    if not json_output:
        console.print()
        console.print(
            Panel(
                f"[bold]Publishing[/bold] {resource_type}/[cyan]{name}[/cyan]"
                + (f" as [green]v{version}[/green]" if version else ""),
                title="Agent Garden",
                border_style="blue",
            )
        )
        console.print()

    # Step 1: Find the approved PR for this resource
    try:
        with _get_client() as client:
            # List approved PRs for this resource type
            params: dict[str, str] = {"status": "approved"}
            if resource_type:
                params["resource_type"] = resource_type

            response = client.get("/api/v1/git/prs", params=params)
            response.raise_for_status()
            prs = response.json()["data"].get("prs", [])

    except httpx.HTTPStatusError as exc:
        _handle_http_error(exc, json_output)
        raise typer.Exit(code=1) from None
    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    # Find matching PR
    matching_pr = None
    for pr in prs:
        if pr.get("resource_name") == name and pr.get("resource_type") == resource_type:
            matching_pr = pr
            break

    if not matching_pr:
        msg = f"No approved PR found for {resource_type}/{name}"
        if json_output:
            sys.stdout.write(json.dumps({"error": msg}) + "\n")
        else:
            console.print(
                Panel(
                    f"[bold red]Not found[/bold red]\n\n"
                    f"  {msg}\n\n"
                    f"  [dim]Ensure a PR has been submitted and approved:\n"
                    f"    garden submit {resource_type} {name}\n"
                    f"    garden review approve <pr-id>[/dim]",
                    title="Error",
                    border_style="red",
                )
            )
            console.print()
        raise typer.Exit(code=1)

    pr_id = matching_pr["id"]

    if not json_output:
        console.print(
            f"  [dim]Found approved PR: {pr_id[:8]}... ({matching_pr.get('title', '')})[/dim]"
        )

    # Step 2: Merge the PR
    try:
        with _get_client() as client:
            merge_body: dict[str, str | None] = {}
            if version:
                merge_body["tag_version"] = version

            response = client.post(
                f"/api/v1/git/prs/{pr_id}/merge",
                json=merge_body if merge_body else None,
            )
            response.raise_for_status()
            merged_pr = response.json()["data"]

    except httpx.HTTPStatusError as exc:
        _handle_http_error(exc, json_output)
        raise typer.Exit(code=1) from None
    except httpx.ConnectError:
        _handle_connection_error(json_output)
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json.dumps(merged_pr, indent=2, default=str) + "\n")
        return

    # Success output
    tag = merged_pr.get("tag")
    registry_url = f"{API_BASE}/api/v1/registry/{resource_type}s/{name}"

    detail_lines = [
        "  [bold green]Published successfully![/bold green]",
        "",
        f"  Resource: [cyan]{resource_type}/{name}[/cyan]",
        f"  Status:   {_status_badge(merged_pr.get('status', ''))}",
        f"  PR:       [dim]{pr_id}[/dim]",
    ]

    if tag:
        detail_lines.append(f"  Tag:      [green]{tag}[/green]")

    detail_lines.extend(
        [
            "",
            f"  Registry: [bold]{registry_url}[/bold]",
        ]
    )

    console.print()
    console.print(
        Panel(
            "\n".join(detail_lines),
            title="Published",
            border_style="green",
        )
    )
    console.print()
    console.print(
        f"  [dim]Use in agent.yaml:\n"
        f"    tools:\n"
        f"      - ref: {resource_type}s/{name}"
        + (f"@{tag.split('/')[-1]}" if tag else "")
        + "[/dim]"
    )
    console.print()
