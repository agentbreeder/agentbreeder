"""agentbreeder down — stop the AgentBreeder platform.

Container-runtime aware: honors the binary/compose pair persisted by
`agentbreeder quickstart` (see `cli/commands/quickstart.py` →
RUNTIME_CACHE_PATH). Falls back to live `_detect_runtime()` when the cache
is absent. Fixes issue #560 bug #7 — previously this command hardcoded
`docker`, so podman/nerdctl stacks couldn't be stopped.
"""

from __future__ import annotations

import json
import subprocess
import sys

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

_QS_PROJECT = "agentbreeder-qs"


def _resolve_runtime() -> tuple[str, list[str]] | None:
    """Return (binary, compose_cmd_parts) or None if no runtime is available.

    Prefers the runtime persisted by quickstart; otherwise live-detects.
    Imported lazily so quickstart's heavy imports don't bloat every CLI call.
    """
    from cli.commands.quickstart import (  # noqa: PLC0415
        _detect_runtime,
        _load_runtime_cache,
    )

    cached = _load_runtime_cache()
    if cached is not None:
        binary, compose_cmd = cached
        return binary, compose_cmd.split()

    detected = _detect_runtime()
    if detected is None:
        return None
    binary, compose_cmd = detected
    return binary, compose_cmd.split()


def _qs_is_running() -> bool:
    """Return True if any containers from the quickstart stack are up.

    Uses whichever container runtime quickstart selected (docker, podman, or
    nerdctl). Returns False when no runtime is available — callers should
    surface a clearer error via `_resolve_runtime()` first.
    """
    runtime = _resolve_runtime()
    if runtime is None:
        return False
    binary, _ = runtime
    result = subprocess.run(
        [binary, "ps", "--filter", f"name={_QS_PROJECT}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _stop_qs(volumes: bool) -> int:
    """Stop the quickstart stack by project name. Returns returncode.

    Uses the persisted runtime's compose command (e.g. ``podman compose``)
    rather than hardcoding docker.
    """
    runtime = _resolve_runtime()
    if runtime is None:
        return 1
    _, compose_cmd = runtime
    cmd = [*compose_cmd, "--project-name", _QS_PROJECT, "down"]
    if volumes:
        cmd.append("--volumes")
    return subprocess.run(cmd).returncode


def down(
    clean: bool = typer.Option(
        False,
        "--clean",
        help="Also remove volumes (deletes database data)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """Stop AgentBreeder and all its services.

    Works from any directory — stops the quickstart stack if running,
    or the dev stack if found. Use --clean to also remove volumes.
    """
    stopped_qs = False
    stopped_dev = False

    # ── 1. Stop quickstart stack (project-name-based, no file needed) ──────
    if _qs_is_running():
        runtime = _resolve_runtime()
        binary = runtime[0] if runtime else "docker"
        if not json_output:
            label = "quickstart stack" + (" + volumes" if clean else "")
            console.print(f"  Stopping {label} ({_QS_PROJECT}) via [cyan]{binary}[/cyan]...")
        rc = _stop_qs(clean)
        if rc == 0:
            stopped_qs = True
        elif not json_output:
            console.print("  [red]✗[/red] Failed to stop quickstart stack")

    # ── 2. Stop dev stack (compose file required) ───────────────────────────
    from cli.commands.up import _find_compose_dir  # noqa: PLC0415

    compose_dir = _find_compose_dir()
    if compose_dir is not None:
        runtime = _resolve_runtime()
        # Default to docker compose if no runtime is detected — preserves
        # legacy behavior for users who never ran quickstart.
        if runtime is None:
            binary = "docker"
            compose_cmd = ["docker", "compose"]
        else:
            binary, compose_cmd = runtime
        compose_file = compose_dir / "docker-compose.yml"
        project_root = compose_dir.parent
        cmd = [
            *compose_cmd,
            "-f",
            str(compose_file),
            "--project-directory",
            str(project_root),
            "down",
        ]
        if clean:
            cmd.append("--volumes")
        if not json_output:
            console.print(f"  Stopping dev stack via [cyan]{binary}[/cyan]...")
        rc = subprocess.run(cmd, cwd=str(project_root)).returncode
        if rc == 0:
            stopped_dev = True
        elif not json_output:
            console.print("  [red]✗[/red] Failed to stop dev stack")

    # ── 3. Nothing found ────────────────────────────────────────────────────
    if not stopped_qs and not stopped_dev:
        if json_output:
            sys.stdout.write(json.dumps({"status": "not_running"}) + "\n")
        else:
            console.print(
                Panel(
                    "[bold]No AgentBreeder services are running.[/bold]\n\n"
                    "  [dim]Start them with: [cyan]agentbreeder quickstart[/cyan][/dim]",
                    border_style="dim",
                    padding=(1, 2),
                )
            )
        return

    # On a clean teardown, drop the cached runtime so the next bootstrap
    # re-detects (user may have switched runtimes between sessions).
    if clean and (stopped_qs or stopped_dev):
        from cli.commands.quickstart import _clear_runtime_cache  # noqa: PLC0415

        _clear_runtime_cache()

    if json_output:
        sys.stdout.write(
            json.dumps(
                {"status": "stopped", "quickstart": stopped_qs, "dev": stopped_dev, "clean": clean}
            )
            + "\n"
        )
        return

    console.print()
    parts = []
    if stopped_qs:
        parts.append("[green]✓[/green] Quickstart stack stopped")
    if stopped_dev:
        parts.append("[green]✓[/green] Dev stack stopped")
    if clean:
        parts.append("[dim]Volumes removed — database data deleted[/dim]")
    else:
        parts.append(
            "[dim]Data preserved. Run [bold]agentbreeder quickstart[/bold] to start again.[/dim]"
        )
    console.print(
        Panel(
            "\n".join(parts),
            title="[bold]AgentBreeder stopped[/bold]",
            border_style="blue",
            padding=(1, 2),
        )
    )
