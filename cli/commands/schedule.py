"""agentbreeder schedule — run an agent on a cron schedule."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


def schedule(
    agent_dir: Path = typer.Argument(
        Path("."),
        help="Path to agent directory containing agent.yaml",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    cron: Optional[str] = typer.Option(
        None,
        "--cron",
        help='Standard cron expression, e.g. "0 8 * * *" (daily at 8am). Required unless --once.',
    ),
    once: bool = typer.Option(False, "--once/--no-once", help="Run immediately once and exit (ignores --cron)."),
    dry_run: bool = typer.Option(
        False, "--dry-run/--no-dry-run", help="Print next 5 fire times and exit without running."
    ),
) -> None:
    """Run an agent on a cron schedule."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        console.print(
            "[red]APScheduler is required for the schedule command.[/red]\n"
            "Install it with: [bold]pip install 'agentbreeder[schedule]'[/bold]"
        )
        raise typer.Exit(code=1)

    # Validate arguments
    if not once and not cron and not dry_run:
        console.print("[red]Error:[/red] --cron is required unless --once is specified.")
        raise typer.Exit(code=1)

    # Parse agent config
    agent_yaml = agent_dir / "agent.yaml"
    if not agent_yaml.exists():
        console.print(f"[red]Error:[/red] No agent.yaml found in {agent_dir}")
        raise typer.Exit(code=1)

    try:
        from engine.config_parser import parse_config

        config = parse_config(agent_yaml)
        agent_name = config.name
    except Exception as exc:
        console.print(f"[red]Error parsing agent.yaml:[/red] {exc}")
        raise typer.Exit(code=1)

    def run_agent() -> None:
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        console.print(f"[bold]Running {agent_name}[/bold] at {now}")
        console.print("Agent run triggered (chat integration pending)")

    # --once: run immediately and exit
    if once:
        run_agent()
        return

    # Build trigger from cron expression
    assert cron is not None  # validated above
    try:
        trigger = CronTrigger.from_crontab(cron, timezone="UTC")
    except Exception as exc:
        console.print(f"[red]Invalid cron expression '{cron}':[/red] {exc}")
        raise typer.Exit(code=1)

    # --dry-run: show next 5 fire times and exit
    if dry_run:
        console.print(f"\n  Scheduled: {cron}\n")
        console.print("  Next 5 fire times:")
        now_dt = datetime.now(tz=timezone.utc)
        fire_time: Optional[datetime] = now_dt
        for i in range(1, 6):
            fire_time = trigger.get_next_fire_time(fire_time, now_dt)
            if fire_time is None:
                break
            console.print(f"  {i}. {fire_time.strftime('%Y-%m-%d %H:%M:%S')}")
        return

    # Daemon mode: start the scheduler
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_agent, trigger=trigger)

    console.print(f"Scheduled: {cron} — press Ctrl+C to stop")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        console.print("\nScheduler stopped.")
        scheduler.shutdown(wait=False)
