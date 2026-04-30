"""agentbreeder model — model lifecycle commands (Track G, #163).

Subcommands:

    agentbreeder model list       [--provider PROVIDER] [--status STATUS] [--json]
    agentbreeder model show       <name> [--json]
    agentbreeder model sync       [--provider NAME ...] [--json]
    agentbreeder model deprecate  <name> [--replacement NAME]

All commands talk to the AgentBreeder API at ``$AGENTBREEDER_API_URL``
(default ``http://localhost:8000``) and require a JWT in
``$AGENTBREEDER_API_TOKEN``. ``sync`` and ``deprecate`` are deployer-gated
server-side.
"""

from __future__ import annotations

import json as _json
import os
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

console = Console()

model_app = typer.Typer(
    name="model",
    no_args_is_help=True,
    help="Manage models — list / sync / deprecate / show.",
    rich_markup_mode="rich",
)


# ── HTTP helpers ────────────────────────────────────────────────────────────


def _api_base() -> str:
    return os.getenv("AGENTBREEDER_API_URL", "http://localhost:8000").rstrip("/")


def _auth_headers() -> dict[str, str]:
    token = os.getenv("AGENTBREEDER_API_TOKEN", "").strip()
    if not token:
        console.print(
            "[red]AGENTBREEDER_API_TOKEN is not set.[/red] "
            "Log in via /api/v1/auth/login and export the token first."
        )
        raise typer.Exit(code=1)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{_api_base()}{path}"
    with httpx.Client(timeout=60.0) as client:
        if method == "GET":
            resp = client.get(url, headers=_auth_headers())
        elif method == "POST":
            resp = client.post(url, headers=_auth_headers(), json=body or {})
        else:
            raise ValueError(f"Unsupported method: {method}")
    if resp.status_code >= 400:
        console.print(f"[red]{method} {path} -> {resp.status_code}[/red]\n{resp.text}")
        raise typer.Exit(code=1)
    try:
        return resp.json()
    except ValueError:
        return {"data": resp.text}


# ── Status badge rendering ──────────────────────────────────────────────────


_STATUS_STYLES = {
    "active": "green",
    "beta": "cyan",
    "deprecated": "yellow",
    "retired": "dim",
}


def _badge(status: str) -> str:
    style = _STATUS_STYLES.get(status, "white")
    return f"[{style}]{status}[/{style}]"


# ── Commands ────────────────────────────────────────────────────────────────


@model_app.command("list")
def list_cmd(
    provider: str | None = typer.Option(None, "--provider", help="Filter by provider name."),
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (active|beta|deprecated|retired)."
    ),
    per_page: int = typer.Option(50, "--per-page", min=1, max=100),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List models in the registry."""
    params: list[str] = [f"per_page={per_page}"]
    if provider:
        params.append(f"provider={provider}")
    if status:
        params.append(f"source={status}")  # backwards-compat: existing list filter
    qs = "&".join(params)
    payload = _request("GET", f"/api/v1/models?{qs}")
    rows: list[dict[str, Any]] = payload.get("data") or []

    # Client-side status filter — the legacy /models endpoint filters on
    # ``source``, not ``status``.  Track G derives status from discovery,
    # so we trim client-side to keep the migration light.
    if status:
        rows = [r for r in rows if r.get("status") == status]

    if json_output:
        console.print(_json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        console.print("[dim]No models match the filter.[/dim]")
        return

    table = Table(title=f"Models ({len(rows)})")
    table.add_column("Name", style="bold")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Context")
    table.add_column("Discovered")
    table.add_column("Last seen")
    for row in rows:
        table.add_row(
            str(row.get("name", "")),
            str(row.get("provider", "")),
            _badge(str(row.get("status", "active"))),
            str(row.get("context_window") or "—"),
            str(row.get("discovered_at") or "—"),
            str(row.get("last_seen_at") or "—"),
        )
    console.print(table)


@model_app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Model name to show."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show a single model's full record."""
    # ``/models/{id}`` accepts a UUID; ``/registry/search`` resolves names.
    payload = _request("GET", "/api/v1/models?per_page=100")
    rows: list[dict[str, Any]] = payload.get("data") or []
    matches = [r for r in rows if r.get("name") == name]
    if not matches:
        console.print(f"[red]Model '{name}' not found in registry.[/red]")
        raise typer.Exit(code=1)
    row = matches[0]
    if json_output:
        console.print(_json.dumps(row, indent=2, default=str))
        return
    table = Table(title=name, show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    for key in (
        "id",
        "provider",
        "status",
        "source",
        "context_window",
        "max_output_tokens",
        "input_price_per_million",
        "output_price_per_million",
        "discovered_at",
        "last_seen_at",
        "deprecated_at",
        "deprecation_replacement_id",
    ):
        if key in row:
            value = row[key]
            if key == "status":
                value = _badge(str(value))
            table.add_row(key, str(value) if value is not None else "—")
    console.print(table)


@model_app.command("sync")
def sync_cmd(
    providers: list[str] = typer.Option(
        [],
        "--provider",
        "-p",
        help="Restrict the sync to one or more providers (repeatable).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Discover models from configured providers and reconcile the registry.

    Without ``--provider``, every provider with a configured api-key is
    synced. Status flips (added → deprecated → retired) emit audit events.
    Deployer role required.
    """
    body = {"providers": providers}
    payload = _request("POST", "/api/v1/models/sync", body=body)
    data = payload.get("data") or {}
    if json_output:
        console.print(_json.dumps(data, indent=2, default=str))
        return

    totals = data.get("totals") or {}
    console.print(
        f"[green]Sync complete.[/green]  "
        f"+{totals.get('added', 0)} added  "
        f"~{totals.get('deprecated', 0)} deprecated  "
        f"-{totals.get('retired', 0)} retired"
    )

    table = Table(title="Per-provider results")
    table.add_column("Provider", style="bold")
    table.add_column("Seen")
    table.add_column("Added")
    table.add_column("Deprecated")
    table.add_column("Retired")
    table.add_column("Error", style="red")
    for prov in data.get("providers") or []:
        table.add_row(
            str(prov.get("provider", "")),
            str(prov.get("total_seen", 0)),
            str(len(prov.get("added") or [])),
            str(len(prov.get("deprecated") or [])),
            str(len(prov.get("retired") or [])),
            str(prov.get("error") or ""),
        )
    console.print(table)


@model_app.command("sync-now")
def sync_now_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Run the daily sync sweep immediately, in-process.

    Bypasses the API server and the background scheduler — talks directly
    to ``api.tasks.models_sync_cron.run_sync_once``. Useful for testing
    the cron and for self-hosted deployments that don't run the daily
    loop. Requires the same env vars as the API process (``DATABASE_URL``
    plus the provider api-key vars).
    """
    import asyncio as _asyncio

    from api.tasks.models_sync_cron import run_sync_once

    try:
        summary = _asyncio.run(run_sync_once(actor="cli:sync-now"))
    except Exception as exc:  # noqa: BLE001 — surface to operator
        console.print(f"[red]sync-now failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        console.print(_json.dumps(summary, indent=2, default=str))
        return

    totals = summary.get("totals") or {}
    skipped = summary.get("skipped_reason")
    if skipped:
        console.print(f"[yellow]Sync skipped:[/yellow] {skipped}")
        return
    console.print(
        f"[green]Sync complete.[/green]  "
        f"+{totals.get('added', 0)} added  "
        f"~{totals.get('deprecated', 0)} deprecated  "
        f"-{totals.get('retired', 0)} retired"
    )

    table = Table(title="Per-provider results")
    table.add_column("Provider", style="bold")
    table.add_column("Seen")
    table.add_column("Added")
    table.add_column("Deprecated")
    table.add_column("Retired")
    table.add_column("Error", style="red")
    for prov in summary.get("providers") or []:
        table.add_row(
            str(prov.get("provider", "")),
            str(prov.get("total_seen", 0)),
            str(len(prov.get("added") or [])),
            str(len(prov.get("deprecated") or [])),
            str(len(prov.get("retired") or [])),
            str(prov.get("error") or ""),
        )
    console.print(table)


@model_app.command("deprecate")
def deprecate_cmd(
    name: str = typer.Argument(..., help="Model name to deprecate."),
    replacement: str | None = typer.Option(
        None, "--replacement", "-r", help="Optional replacement model name."
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Manually mark a model as deprecated. Deployer role required."""
    body: dict[str, Any] = {}
    if replacement:
        body["replacement"] = replacement
    payload = _request("POST", f"/api/v1/models/{name}/deprecate", body=body)
    data = payload.get("data") or {}
    if json_output:
        console.print(_json.dumps(data, indent=2, default=str))
        return
    console.print(
        f"[yellow]{data.get('name')}[/yellow] is now "
        f"[bold]{data.get('status')}[/bold]"
        + (f" (replaced by [green]{replacement}[/green])" if replacement else "")
    )


__all__ = ["model_app"]
