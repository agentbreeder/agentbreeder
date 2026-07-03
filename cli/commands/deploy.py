"""agentbreeder deploy — the core command.

Two execution modes:

* **local** (current behavior) — runs ``engine.builder.DeployEngine`` in-process
  with rich step-by-step progress. RBAC and audit logging are *not* enforced.
* **remote** — POSTs ``/api/v1/deploys`` against an API server and polls
  ``GET /api/v1/deploys/{job_id}`` until the deploy terminates. This is the
  mode that exercises the team-scoped RBAC gates (tightened in #414) and
  writes audit-log entries; bearer token comes from :mod:`cli._http`.

Mode selection (lowest to highest precedence):

1. ``--local`` → local mode regardless of env
2. ``--remote`` → remote mode regardless of env
3. otherwise: remote when ``AGENTBREEDER_URL`` is set, else local

This is the second half of closing the "shell access + local cloud creds is
enough to deploy" bypass flagged during the Phase A audit. The first half
(#415) added CLI login so a real bearer token is available; this PR makes
``agentbreeder deploy`` actually use it. The third (#414) tightens the
server-side gate so cross-team deploys 403 even with a valid token.
"""

from __future__ import annotations

import asyncio
import json as json_lib
import os
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel

from cli import _http
from engine.builder import DeployEngine, PipelineStep

console = Console()

STEP_ICONS = {
    "pending": "[dim]  [/dim]",
    "running": "[blue]  [/blue]",
    "completed": "[green]  [/green]",
    "failed": "[red]  [/red]",
}

_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "completed", "error"}
_POLL_INTERVAL_SECONDS = 2.0
_POLL_TIMEOUT_SECONDS = 60 * 30  # 30 min ceiling per deploy


def _resolve_mode(remote: bool, local: bool) -> bool:
    """Return ``True`` for remote mode, ``False`` for local.

    ``--remote`` and ``--local`` are mutually exclusive. If neither flag is
    given, remote wins when ``AGENTBREEDER_URL`` (or its legacy alias) is set.
    """
    if remote and local:
        console.print("[red]--remote and --local are mutually exclusive[/red]")
        raise typer.Exit(code=2)
    if remote:
        return True
    if local:
        return False
    return bool(os.getenv(_http.ENV_URL) or os.getenv(_http.ENV_URL_LEGACY))


def deploy(
    config_path: Path = typer.Argument(
        ...,
        help="Path to agent.yaml",
        exists=True,
        readable=True,
    ),
    target: str = typer.Option(
        "local",
        "--target",
        "-t",
        help=(
            "Deploy target: local | gcp | cloud-run | aws | ecs-fargate"
            " | azure | container-apps | kubernetes | eks | gke | aks"
            " | claude-managed"
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (for CI/scripting)",
    ),
    remote: bool = typer.Option(
        False,
        "--remote",
        help=(
            "Submit the deploy to the API server (RBAC + audit enforced). "
            "Default when $AGENTBREEDER_URL is set."
        ),
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="Run the deploy engine in-process (no RBAC, dev/offline use only).",
    ),
    provision: bool = typer.Option(
        False,
        "--provision",
        "-p",
        help=(
            "Greenfield-provision the cloud footprint (network, registry, cluster/"
            "environment, IAM) for a fresh account before deploying. Supports AWS, "
            "GCP, and Azure; local mode only."
        ),
    ),
) -> None:
    """Deploy an agent from an agent.yaml configuration file."""
    use_remote = _resolve_mode(remote, local)
    if use_remote and provision:
        console.print(
            "[red]--provision is local-only for now[/red] — greenfield provisioning "
            "from the API/Studio path is tracked separately (#537). Re-run with "
            "[bold]--local --provision[/bold], or use the Studio deploy wizard."
        )
        raise typer.Exit(code=2)
    if use_remote:
        _deploy_remote(config_path, target, json_output)
    else:
        _deploy_local(config_path, target, json_output, provision)


# ── Local mode (preserved verbatim from the pre-#416 implementation) ────────


def _deploy_local(
    config_path: Path, target: str, json_output: bool, provision: bool = False
) -> None:
    """Run the deploy engine in-process — same flow as before #416.

    Kept for dev/offline use; explicitly bypasses every server-side gate
    (RBAC, audit log, team-scoped credentials). Production deploys should
    always go through ``--remote``.

    When ``provision`` is set, the engine greenfield-provisions the cloud
    footprint before deploying (AWS, GCP, or Azure; #537, parity #505).
    """
    if not json_output:
        provision_note = " [yellow](+ greenfield provisioning)[/yellow]" if provision else ""
        console.print()
        console.print(
            Panel(
                f"[bold]Deploying[/bold] {config_path.name} → [cyan]{target}[/cyan]"
                f"{provision_note} "
                "[dim](local mode — RBAC/audit bypassed)[/dim]",
                title="AgentBreeder",
                border_style="blue",
            )
        )
        console.print()

    steps: list[PipelineStep] = []

    def on_step(step: PipelineStep) -> None:
        if not json_output:
            icon = STEP_ICONS.get(step.status, "")
            if step.status == "running":
                console.print(f"  {icon} [blue]{step.name}...[/blue]")
            elif step.status == "completed":
                console.print(f"\033[A  {icon} {step.name}")
            elif step.status == "failed":
                console.print(f"\033[A  {icon} [red]{step.name} — FAILED[/red]")
        steps.append(step)

    engine = DeployEngine(on_step=on_step)

    try:
        result = asyncio.run(
            engine.deploy(config_path=config_path, target=target, provision=provision)
        )

        if json_output:
            console.print(json_lib.dumps(result.model_dump(), indent=2))
        else:
            console.print()
            console.print(
                Panel(
                    f"[bold green]Deploy successful![/bold green]\n\n"
                    f"  Agent:    [cyan]{result.agent_name}[/cyan]\n"
                    f"  Version:  {result.version}\n"
                    f"  Endpoint: [bold]{result.endpoint_url}[/bold]\n\n"
                    f"  Invoke:   [dim]curl -X POST {result.endpoint_url}/invoke "
                    f'-d \'{{"input": {{"message": "hello"}}}}\' '
                    f"-H 'Content-Type: application/json'[/dim]",
                    title="Deployed",
                    border_style="green",
                )
            )
            console.print()

    except Exception as e:
        if json_output:
            sys.stdout.write(json_lib.dumps({"error": str(e)}) + "\n")
        else:
            console.print()
            console.print(
                Panel(
                    f"[bold red]Deploy failed[/bold red]\n\n  {e}",
                    title="Error",
                    border_style="red",
                )
            )
            console.print()
        raise typer.Exit(code=1) from None


# ── Remote mode ─────────────────────────────────────────────────────────────


def _read_yaml(config_path: Path) -> str:
    try:
        return config_path.read_text()
    except OSError as exc:
        console.print(f"[red]Could not read {config_path}: {exc}[/red]")
        raise typer.Exit(code=1) from exc


def _format_summary(detail: dict[str, Any]) -> str:
    rows = [
        ("Job ID", detail.get("id", "")),
        ("Agent", detail.get("agent_name") or detail.get("agent_id") or "—"),
        ("Target", detail.get("target", "")),
        ("Status", detail.get("status", "")),
    ]
    return "\n".join(f"  {k:<10} {v}" for k, v in rows)


def _deploy_remote(config_path: Path, target: str, json_output: bool) -> None:
    """Submit the deploy through the API server.

    Bearer token is resolved by :mod:`cli._http` (env var or OS keychain) —
    if neither is present the user is exited with a "run agentbreeder login"
    hint. After the POST we poll the detail endpoint until the job reaches
    a terminal status; SSE streaming is the dedicated scope of #387.
    """
    base = _http.api_base()
    yaml_text = _read_yaml(config_path)
    body = {"config_yaml": yaml_text, "target": target}

    if not json_output:
        console.print()
        console.print(
            Panel(
                f"[bold]Deploying[/bold] {config_path.name} → [cyan]{target}[/cyan]\n"
                f"[dim]via {base} (remote mode — RBAC + audit enforced)[/dim]",
                title="AgentBreeder",
                border_style="blue",
            )
        )
        console.print()

    created = _http.request("POST", "/api/v1/deploys", body=body, timeout=60.0)
    job = created.get("data") if isinstance(created, dict) else None
    if not isinstance(job, dict) or not job.get("id"):
        console.print(f"[red]Unexpected /deploys response: {created}[/red]")
        raise typer.Exit(code=1)
    job_id = str(job["id"])

    if not json_output:
        console.print(f"  Job [bold]{job_id}[/bold] submitted. Polling for completion…")
        console.print()

    detail = _poll_until_terminal(job_id, json_output=json_output)
    status = str(detail.get("status", "")).lower()

    if json_output:
        console.print(json_lib.dumps(detail, indent=2, default=str))
        if status not in {"succeeded", "completed"}:
            raise typer.Exit(code=1)
        return

    if status in {"succeeded", "completed"}:
        console.print(
            Panel(
                f"[bold green]Deploy successful![/bold green]\n\n{_format_summary(detail)}",
                title="Deployed",
                border_style="green",
            )
        )
        console.print()
        return

    error_msg = detail.get("error_message") or status
    console.print(
        Panel(
            f"[bold red]Deploy failed[/bold red]\n\n{_format_summary(detail)}\n\n"
            f"  Reason:   {error_msg}",
            title="Error",
            border_style="red",
        )
    )
    console.print()
    raise typer.Exit(code=1)


def _poll_until_terminal(job_id: str, *, json_output: bool) -> dict[str, Any]:
    """Poll ``GET /api/v1/deploys/{job_id}`` until status is terminal.

    Status terms map onto either side of the wizard/legacy split: the v2.3
    ``deploys`` route exposes ``succeeded`` / ``failed`` / ``cancelled`` while
    the v2.4 ``deployments`` event bus uses ``complete`` / ``error``. Both are
    treated as stop conditions here so the same CLI works against either
    surface during the migration.
    """
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    last_status: str | None = None
    while time.monotonic() < deadline:
        resp = _http.request("GET", f"/api/v1/deploys/{job_id}")
        detail = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(detail, dict):
            console.print(f"[red]Unexpected /deploys/{job_id} response: {resp}[/red]")
            raise typer.Exit(code=1)
        status = str(detail.get("status", "")).lower()
        if not json_output and status != last_status:
            console.print(f"  status: [cyan]{status}[/cyan]")
            last_status = status
        if status in _TERMINAL_STATUSES:
            return detail
        time.sleep(_POLL_INTERVAL_SECONDS)
    console.print(
        f"[red]Timed out after {_POLL_TIMEOUT_SECONDS // 60} min waiting for "
        f"job {job_id} to complete.[/red]"
    )
    raise typer.Exit(code=1)
