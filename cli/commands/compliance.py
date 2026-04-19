"""agentbreeder compliance — generate and export compliance evidence reports."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

compliance_app = typer.Typer(
    name="compliance",
    help="Generate and export compliance evidence report packs (SOC 2, HIPAA, GDPR, ISO 27001).",
    no_args_is_help=True,
)

_SUPPORTED_STANDARDS = ["soc2", "hipaa", "gdpr", "iso27001"]
_STANDARD_LABELS = {
    "soc2": "SOC 2 Type II",
    "hipaa": "HIPAA Security Rule",
    "gdpr": "GDPR",
    "iso27001": "ISO/IEC 27001:2022",
}


def _parse_since(since: str) -> date:
    """Parse a duration string like '90d', '30d', '1y' into a start date."""
    since = since.strip().lower()
    today = date.today()

    if since.endswith("y"):
        years = int(since[:-1])
        return today.replace(year=today.year - years)
    if since.endswith("d"):
        days = int(since[:-1])
        return today - timedelta(days=days)
    if since.endswith("m"):
        months = int(since[:-1])
        # Approximate: 30 days per month
        return today - timedelta(days=months * 30)

    raise typer.BadParameter(
        f"Cannot parse duration '{since}'. Use formats like: 90d, 30d, 6m, 1y"
    )


def _build_report_locally(
    standard: str,
    team: str | None,
    period_start: date,
    period_end: date,
) -> dict:
    """Build compliance report data locally (mirrors the API logic)."""
    # Import the builder functions from the API module so logic stays DRY.
    # Falls back to a lightweight in-process build if the API is not importable.
    try:
        from api.routes.compliance import (
            ReportRequest,
            _BUILDERS,
            _build_summary,
        )

        req = ReportRequest(
            standard=standard,  # type: ignore[arg-type]
            team=team,
            period_start=period_start,
            period_end=period_end,
        )
        sections = _BUILDERS[standard](req)
        summary = _build_summary(standard, sections, req)
        return {
            "standard": standard,
            "team": team,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "sections": sections,
            "summary": summary,
        }
    except ImportError:
        # API package not available in this environment — return a minimal skeleton
        return {
            "standard": standard,
            "team": team,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "sections": {},
            "summary": {
                "standard": _STANDARD_LABELS.get(standard, standard),
                "period": f"{period_start} to {period_end}",
                "team": team or "all teams",
                "data_note": "API package not available. Run with the full agentbreeder install.",
            },
        }


@compliance_app.command(name="export")
def export_report(
    standard: str = typer.Option(
        "soc2",
        "--standard",
        "-s",
        help="Compliance standard: soc2, hipaa, gdpr, iso27001",
    ),
    team: Optional[str] = typer.Option(
        None,
        "--team",
        "-t",
        help="Filter evidence by team name",
    ),
    since: str = typer.Option(
        "90d",
        "--since",
        help="Look-back period: e.g. 90d, 30d, 6m, 1y",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write report to this file path (default: stdout)",
    ),
    fmt: str = typer.Option(
        "json",
        "--format",
        "-f",
        help="Output format: json (default)",
    ),
) -> None:
    """Generate a compliance evidence report and export it.

    Examples:
        agentbreeder compliance export --standard soc2
        agentbreeder compliance export --standard hipaa --team customer-success --since 30d
        agentbreeder compliance export --standard gdpr -o gdpr-report.json
    """
    standard = standard.lower()
    if standard not in _SUPPORTED_STANDARDS:
        console.print(
            f"[red]Unsupported standard '{standard}'. "
            f"Choose from: {', '.join(_SUPPORTED_STANDARDS)}[/red]"
        )
        raise typer.Exit(code=1)

    try:
        period_start = _parse_since(since)
    except (typer.BadParameter, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    period_end = date.today()

    console.print(
        f"\n  [bold]Generating {_STANDARD_LABELS.get(standard, standard)} report…[/bold]"
    )

    report = _build_report_locally(standard, team, period_start, period_end)

    if fmt == "json":
        report_text = json.dumps(report, indent=2, default=str)
    else:
        console.print(f"[yellow]Format '{fmt}' not yet supported; falling back to JSON.[/yellow]")
        report_text = json.dumps(report, indent=2, default=str)

    if output:
        out_path = Path(output)
        out_path.write_text(report_text, encoding="utf-8")
        console.print(
            Panel(
                f"[green]Report saved to:[/green] [cyan]{out_path.resolve()}[/cyan]\n\n"
                f"  Standard : {_STANDARD_LABELS.get(standard, standard)}\n"
                f"  Period   : {period_start} → {period_end}\n"
                f"  Team     : {team or 'all teams'}\n"
                f"  Sections : {len(report.get('sections', {}))}",
                title="Compliance Report Exported",
                border_style="green",
            )
        )
    else:
        sys.stdout.write(report_text + "\n")

    console.print()


@compliance_app.command(name="list-standards")
def list_standards() -> None:
    """List supported compliance standards and their evidence sections."""
    table = Table(title="Supported Compliance Standards", show_lines=True)
    table.add_column("Standard", style="cyan", no_wrap=True)
    table.add_column("Label", style="bold")
    table.add_column("Key Sections")

    _sections_map = {
        "soc2": "CC6 Access Controls, CC7 Operations, CC9 Risk Mitigation",
        "hipaa": "Access Controls, Audit Controls, PHI Handling",
        "gdpr": "Lawful Basis, Data Subject Rights, Data Protection",
        "iso27001": "A.9 Access Control, A.12 Operations Security, A.14 System Acquisition",
    }

    for std, label in _STANDARD_LABELS.items():
        table.add_row(std, label, _sections_map.get(std, "—"))

    console.print()
    console.print(table)
    console.print(
        "\n  Run [cyan]agentbreeder compliance export --standard <standard>[/cyan] to generate a report.\n"
    )
