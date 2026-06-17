"""agentbreeder list — list agents and other registry entities.

All listing now hits the AgentBreeder API directly (via :mod:`cli._http`).
Reads the bearer token from the OS keychain or ``AGENTBREEDER_API_TOKEN``,
and the base URL from ``AGENTBREEDER_URL`` / ``AGENTBREEDER_API_URL``
(default ``http://localhost:8000``).

Exit codes:

* ``0`` — success
* ``1`` — API unreachable / unexpected error
* ``2`` — not logged in
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from cli import _http

# stdout console for user-friendly tables; stderr console for errors so that
# `--json` consumers can pipe stdout cleanly even on failure paths.
console = Console()
err_console = Console(stderr=True)


# Subcommands → API path. agents/deploys/providers/orchestrations/mcp_servers/
# templates are stored in the AgentBreeder API; tools/models/prompts are
# populated locally by `agentbreeder scan` into ~/.agentbreeder/registry/.
_ENDPOINTS: dict[str, str] = {
    "agents": "/api/v1/agents",
    "deploys": "/api/v1/deploys",
    "providers": "/api/v1/providers",
    "orchestrations": "/api/v1/orchestrations",
    "mcp_servers": "/api/v1/mcp_servers",
    "templates": "/api/v1/templates",
}

# Local JSON registry (written by `agentbreeder scan` and friends).
REGISTRY_DIR = Path.home() / ".agentbreeder" / "registry"

_LOCAL_REGISTRY_FILES: dict[str, str] = {
    "tools": "tools.json",
    "models": "models.json",
    "prompts": "prompts.json",
}


def list_entities(
    entity_type: str = typer.Argument(
        "agents",
        help="Entity type to list: agents, deploys, providers, orchestrations, mcp_servers, templates",
    ),
    team: str | None = typer.Option(None, "--team", help="Filter by team (where supported)"),
    page: int = typer.Option(1, "--page", help="Page number (1-indexed)", min=1),
    per_page: int = typer.Option(
        20, "--per-page", help="Results per page (1–100)", min=1, max=100
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List entities from the AgentBreeder registry via the API."""
    if entity_type in _LOCAL_REGISTRY_FILES:
        _render_local_registry(entity_type, json_output=json_output)
        return

    path = _ENDPOINTS.get(entity_type)
    if path is None:
        valid = sorted(set(_ENDPOINTS) | set(_LOCAL_REGISTRY_FILES))
        err_console.print(
            f"[red]Unknown entity type:[/red] '{entity_type}'. "
            f"Valid types: {', '.join(valid)}."
        )
        raise typer.Exit(code=1)

    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if team and entity_type in {"agents", "orchestrations"}:
        params["team"] = team

    items = _fetch(path, params=params)

    if entity_type == "agents":
        _render_agents(items, json_output=json_output, team_filter=team)
    elif entity_type == "deploys":
        _render_deploys(items, json_output=json_output)
    elif entity_type == "providers":
        _render_providers(items, json_output=json_output)
    elif entity_type == "orchestrations":
        _render_orchestrations(items, json_output=json_output)
    elif entity_type == "mcp_servers":
        _render_mcp_servers(items, json_output=json_output)
    elif entity_type == "templates":
        _render_templates(items, json_output=json_output)


# ── HTTP fetch ──────────────────────────────────────────────────────────────


def _fetch(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """GET ``path`` with the CLI's auth token and return the ``data`` array.

    Exits the process with a clean error message on:

    * Missing token → exit 2 (mirrors documented contract: "not logged in")
    * Connection/timeout/transport errors → exit 1 (API unreachable)
    * 401 / non-2xx responses → exit 1 (with status line on stderr)
    """
    token = _http.get_token()
    if not token:
        err_console.print("Run `agentbreeder login` first.")
        raise typer.Exit(code=2)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    team = _http.get_active_team()
    if team:
        headers["X-AgentBreeder-Team"] = team

    url = f"{_http.api_base()}{path}"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers, params=params or {})
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Could not reach {_http.api_base()}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if resp.status_code == 401:
        err_console.print(
            "[red]Unauthorized.[/red] Run `agentbreeder login` to refresh your token."
        )
        raise typer.Exit(code=2)
    if resp.status_code >= 400:
        err_console.print(
            f"[red]GET {path} -> {resp.status_code}[/red]\n{resp.text}",
        )
        raise typer.Exit(code=1)

    try:
        payload = resp.json()
    except ValueError as exc:
        err_console.print(f"[red]Invalid JSON in response: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    data = payload.get("data") if isinstance(payload, dict) else payload
    if data is None:
        return []
    if not isinstance(data, list):
        err_console.print(f"[red]Unexpected response shape from {path}: {payload}[/red]")
        raise typer.Exit(code=1)
    return data


# ── Renderers ───────────────────────────────────────────────────────────────


def _render_agents(
    agents: list[dict[str, Any]],
    *,
    json_output: bool,
    team_filter: str | None = None,
) -> None:
    if team_filter:
        agents = [a for a in agents if a.get("team") == team_filter]

    if json_output:
        # Use stdout so callers can pipe to ``jq``.
        sys.stdout.write(json.dumps(agents, indent=2, default=str))
        sys.stdout.write("\n")
        return

    if not agents:
        console.print("[dim]No agents found.[/dim]")
        return

    table = Table(title="Registered Agents")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="dim")
    table.add_column("Team", style="yellow")
    table.add_column("Framework")
    table.add_column("Status", style="green")
    table.add_column("Endpoint", style="dim")

    for agent in agents:
        table.add_row(
            str(agent.get("name", "")),
            str(agent.get("version", "")),
            str(agent.get("team", "")),
            str(agent.get("framework", "")),
            str(agent.get("status", "")),
            str(agent.get("endpoint_url", "") or ""),
        )

    console.print()
    console.print(table)
    console.print()


def _render_deploys(deploys: list[dict[str, Any]], *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(json.dumps(deploys, indent=2, default=str))
        sys.stdout.write("\n")
        return
    if not deploys:
        console.print("[dim]No deploys found.[/dim]")
        return
    table = Table(title="Deploy Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Agent", style="dim")
    table.add_column("Target", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Created", style="dim")
    for d in deploys:
        table.add_row(
            str(d.get("id", ""))[:8],
            str(d.get("agent_id", ""))[:8],
            str(d.get("target", "")),
            str(d.get("status", "")),
            str(d.get("created_at", "")),
        )
    console.print()
    console.print(table)
    console.print()


def _render_providers(providers: list[dict[str, Any]], *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(json.dumps(providers, indent=2, default=str))
        sys.stdout.write("\n")
        return
    if not providers:
        console.print("[dim]No providers found.[/dim]")
        return
    table = Table(title="Providers")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Status", style="green")
    for p in providers:
        table.add_row(
            str(p.get("name", "")),
            str(p.get("provider_type", "")),
            str(p.get("status", "")),
        )
    console.print()
    console.print(table)
    console.print()


def _render_orchestrations(items: list[dict[str, Any]], *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(json.dumps(items, indent=2, default=str))
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]No orchestrations found.[/dim]")
        return
    table = Table(title="Orchestrations")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="dim")
    table.add_column("Team", style="yellow")
    table.add_column("Strategy")
    table.add_column("Status", style="green")
    for o in items:
        table.add_row(
            str(o.get("name", "")),
            str(o.get("version", "")),
            str(o.get("team", "")),
            str(o.get("strategy", "")),
            str(o.get("status", "")),
        )
    console.print()
    console.print(table)
    console.print()


def _render_mcp_servers(items: list[dict[str, Any]], *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(json.dumps(items, indent=2, default=str))
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]No MCP servers found.[/dim]")
        return
    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Endpoint", style="yellow")
    table.add_column("Status", style="green")
    for s in items:
        table.add_row(
            str(s.get("name", "")),
            str(s.get("transport", s.get("server_type", ""))),
            str(s.get("endpoint", s.get("url", "")) or ""),
            str(s.get("status", "")),
        )
    console.print()
    console.print(table)
    console.print()


def _render_local_registry(entity_type: str, *, json_output: bool) -> None:
    """Render tools/models/prompts from the local JSON registry written by
    `agentbreeder scan`. Files live under ``REGISTRY_DIR`` and are dict-keyed.
    """
    filename = _LOCAL_REGISTRY_FILES[entity_type]
    path = REGISTRY_DIR / filename
    items: list[dict[str, Any]] = []
    if path.exists():
        try:
            blob = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            err_console.print(f"[red]Could not read {path}: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        if isinstance(blob, dict):
            items = list(blob.values())
        elif isinstance(blob, list):
            items = blob

    if json_output:
        sys.stdout.write(json.dumps(items, indent=2, default=str))
        sys.stdout.write("\n")
        return

    if not items:
        console.print(f"[dim]No {entity_type} found. Run `agentbreeder scan` first.[/dim]")
        return

    if entity_type == "tools":
        table = Table(title="Tools")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Description")
        table.add_column("Source", style="yellow")
        for it in items:
            table.add_row(
                str(it.get("name", "")),
                str(it.get("tool_type", "")),
                str(it.get("description", "") or "")[:60],
                str(it.get("source", "")),
            )
    elif entity_type == "models":
        table = Table(title="Models")
        table.add_column("Name", style="cyan")
        table.add_column("Provider", style="dim")
        table.add_column("Description")
        table.add_column("Source", style="yellow")
        for it in items:
            table.add_row(
                str(it.get("name", it.get("id", ""))),
                str(it.get("provider", "")),
                str(it.get("description", "") or "")[:60],
                str(it.get("source", "")),
            )
    else:  # prompts
        table = Table(title="Prompts")
        table.add_column("Name", style="cyan")
        table.add_column("Version", style="dim")
        table.add_column("Description")
        for it in items:
            table.add_row(
                str(it.get("name", "")),
                str(it.get("version", "")),
                str(it.get("description", "") or "")[:60],
            )

    console.print()
    console.print(table)
    console.print()


def _render_templates(items: list[dict[str, Any]], *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(json.dumps(items, indent=2, default=str))
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]No templates found.[/dim]")
        return
    table = Table(title="Templates")
    table.add_column("Name", style="cyan")
    table.add_column("Framework", style="dim")
    table.add_column("Description")
    for t in items:
        table.add_row(
            str(t.get("name", "")),
            str(t.get("framework", "")),
            str(t.get("description", "") or "")[:60],
        )
    console.print()
    console.print(table)
    console.print()
