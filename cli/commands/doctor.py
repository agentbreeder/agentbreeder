"""agentbreeder doctor — preflight check for prerequisites.

Verifies that the local machine can run AgentBreeder before any image pulls or
container starts happen. Designed to exit in well under 2 seconds when a
prerequisite is missing, with copy-pasteable fix instructions per platform.

Usage:
    agentbreeder doctor
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

MIN_PYTHON: tuple[int, int] = (3, 11)
MIN_FREE_DISK_BYTES: int = 8 * 1024**3
MIN_RAM_BYTES: int = 4 * 1024**3

QUICKSTART_PORTS: tuple[tuple[int, str], ...] = (
    (3001, "Studio"),
    (8000, "API"),
    (5432, "Postgres"),
    (6379, "Redis"),
    (8001, "ChromaDB"),
    (7474, "Neo4j HTTP"),
    (7687, "Neo4j Bolt"),
    (4000, "LiteLLM gateway"),
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    blocker: bool = True
    fix: tuple[str, ...] = field(default_factory=tuple)


def _check_python() -> CheckResult:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    detail = f"{v.major}.{v.minor}.{v.micro}"
    fix: tuple[str, ...] = ()
    if not ok:
        fix = (
            "Install Python ≥ 3.11 via pyenv (recommended):",
            "  brew install pyenv",
            "  pyenv install 3.11.9 && pyenv shell 3.11.9",
            "  pipx install agentbreeder",
        )
    return CheckResult("Python ≥ 3.11", ok, detail, fix=fix)


def _check_container_runtime() -> CheckResult:
    # Late import so `agentbreeder --help` doesn't pay the quickstart cost.
    from cli.commands.quickstart import (
        _detect_runtime,
        _install_instructions,
        _runtime_is_running,
    )

    detected = _detect_runtime()
    if detected is None:
        return CheckResult(
            "Container runtime",
            False,
            "not found (need Docker, Podman, or nerdctl)",
            fix=tuple(_install_instructions()),
        )
    binary, compose = detected
    if not _runtime_is_running(binary):
        start_hints: tuple[str, ...]
        system = platform.system()
        if system == "Darwin":
            start_hints = (
                f"Start {binary}:",
                "  open -a Docker        # Docker Desktop",
                "  open -a OrbStack      # OrbStack",
            )
        elif system == "Linux":
            start_hints = (
                f"Start {binary} daemon:",
                f"  sudo systemctl start {binary}",
            )
        else:
            start_hints = (f"Start the {binary} daemon and re-run doctor.",)
        return CheckResult(
            "Container runtime",
            False,
            f"{binary} installed but daemon not reachable",
            fix=start_hints,
        )
    return CheckResult("Container runtime", True, f"{binary} (via {compose})")


def _check_disk() -> CheckResult:
    cwd = os.getcwd()
    free = shutil.disk_usage(cwd).free
    ok = free >= MIN_FREE_DISK_BYTES
    detail = f"{free / 1024**3:.1f} GiB free at {cwd}"
    fix: tuple[str, ...] = ()
    if not ok:
        need_gib = MIN_FREE_DISK_BYTES // 1024**3
        fix = (
            f"Free up at least {need_gib} GiB on the volume containing {cwd}.",
            "  Common offenders: stopped containers, dangling images, old logs.",
            "  Try: docker system prune -a   (after backing up anything you need)",
        )
    return CheckResult(f"Free disk ≥ {MIN_FREE_DISK_BYTES // 1024**3} GiB", ok, detail, fix=fix)


def _total_ram_bytes() -> int | None:
    """Best-effort total RAM detection without adding a psutil dependency."""
    try:
        names = getattr(os, "sysconf_names", {})
        if "SC_PAGE_SIZE" in names and "SC_PHYS_PAGES" in names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        pass
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if out.returncode == 0 and out.stdout.strip().isdigit():
                return int(out.stdout.strip())
        except (subprocess.SubprocessError, OSError):
            pass
    return None


def _check_ram() -> CheckResult:
    total = _total_ram_bytes()
    need_gib = MIN_RAM_BYTES // 1024**3
    if total is None:
        # Undetectable — don't block, just note it.
        return CheckResult(
            f"RAM ≥ {need_gib} GiB",
            ok=True,
            detail="could not detect (best-effort)",
            blocker=False,
        )
    ok = total >= MIN_RAM_BYTES
    detail = f"{total / 1024**3:.1f} GiB total"
    fix: tuple[str, ...] = ()
    if not ok:
        fix = (
            f"AgentBreeder recommends ≥ {need_gib} GiB RAM; detected {total / 1024**3:.1f} GiB.",
            "  Close memory-heavy apps, or run cloud-only with `--no-ollama`.",
        )
    return CheckResult(f"RAM ≥ {need_gib} GiB", ok, detail, fix=fix)


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    _check_python,
    _check_container_runtime,
    _check_disk,
    _check_ram,
)


def run_all_checks() -> list[CheckResult]:
    return [check() for check in CHECKS]


def has_blocker(results: list[CheckResult]) -> bool:
    return any(not r.ok and r.blocker for r in results)


def _render_table(results: list[CheckResult]) -> Table:
    table = Table(title="AgentBreeder prerequisites", title_style="bold cyan")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", style="dim")
    for r in results:
        if r.ok:
            status = "[green]✓ pass[/green]"
        elif r.blocker:
            status = "[red]✗ fail[/red]"
        else:
            status = "[yellow]! warn[/yellow]"
        table.add_row(r.name, status, r.detail)
    return table


def render_report(results: list[CheckResult]) -> None:
    console.print()
    console.print(_render_table(results))

    failed_blockers = [r for r in results if not r.ok and r.blocker]
    if not failed_blockers:
        console.print("\n[bold green]✓ All required prerequisites satisfied.[/bold green]")
        console.print(
            "[dim]Next: [bold]agentbreeder quickstart[/bold] to bootstrap the local stack.[/dim]\n"
        )
        return

    lines: list[str] = []
    for r in failed_blockers:
        lines.append(f"[bold red]✗ {r.name}[/bold red] — {r.detail}")
        if r.fix:
            lines.extend(f"  {line}" for line in r.fix)
            lines.append("")
    body = "\n".join(lines).rstrip()
    console.print()
    console.print(
        Panel(
            body,
            title="[bold red]Missing prerequisites[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )
    console.print()


def doctor(
    json_output: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Check that this machine satisfies AgentBreeder's prerequisites.

    Run before `agentbreeder quickstart` (or any time a command exits with a
    missing-prerequisite error) to find out exactly what's missing and how to
    fix it. Exits 0 when everything passes, 1 when a required check fails.

    Examples:
        agentbreeder doctor
        agentbreeder doctor --json
    """
    results = run_all_checks()
    if json_output:
        import json as _json

        payload = {
            "ok": not has_blocker(results),
            "checks": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "detail": r.detail,
                    "blocker": r.blocker,
                    "fix": list(r.fix),
                }
                for r in results
            ],
        }
        typer.echo(_json.dumps(payload, indent=2))
    else:
        render_report(results)

    if has_blocker(results):
        raise typer.Exit(code=1)
