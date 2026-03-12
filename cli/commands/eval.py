"""garden eval — evaluation framework commands.

Subcommands:
    garden eval run <agent-name> --dataset <dataset-id>
    garden eval datasets [--team <team>]
    garden eval results <run-id>
    garden eval compare <run-a> <run-b>
"""

from __future__ import annotations

import json as json_lib
import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

eval_app = typer.Typer(
    name="eval",
    help="Evaluation framework — run evals, view datasets and results.",
    no_args_is_help=True,
)


def _get_store():
    """Import and return the eval store singleton."""
    from api.services.eval_service import get_eval_store

    return get_eval_store()


@eval_app.command(name="run")
def eval_run(
    agent_name: str = typer.Argument(..., help="Name of the agent to evaluate"),
    dataset: str = typer.Option(..., "--dataset", "-d", help="Dataset ID to run against"),
    model: str = typer.Option(None, "--model", "-m", help="Model override for the run"),
    temperature: float = typer.Option(None, "--temperature", "-T", help="Temperature override"),
    judge: str = typer.Option(None, "--judge", help="Judge model for LLM-as-judge scoring"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Run an evaluation of an agent against a dataset."""
    store = _get_store()

    config: dict = {}
    if model:
        config["model"] = model
    if temperature is not None:
        config["temperature"] = temperature
    if judge:
        config["judge_model"] = judge

    try:
        run = store.create_run(
            agent_name=agent_name,
            dataset_id=dataset,
            config=config,
        )
    except ValueError as e:
        if json_output:
            sys.stdout.write(json_lib.dumps({"error": str(e)}) + "\n")
        else:
            console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None

    if not json_output:
        console.print()
        console.print(
            Panel(
                f"[bold]Evaluating[/bold] [cyan]{agent_name}[/cyan] "
                f"against dataset [dim]{dataset[:8]}...[/dim]",
                title="Agent Garden Eval",
                border_style="blue",
            )
        )
        console.print("  Running evaluation...", end="")

    try:
        result = store.execute_run(run["id"])
    except Exception as e:
        if json_output:
            sys.stdout.write(json_lib.dumps({"error": str(e)}) + "\n")
        else:
            console.print(f"\n[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json_lib.dumps(result, indent=2) + "\n")
        return

    console.print(" [green]done[/green]")
    console.print()

    summary = result.get("summary", {})
    metrics = summary.get("metrics", {})

    # Metrics table
    if metrics:
        table = Table(title="Evaluation Scores", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="bold")
        table.add_column("Mean", justify="right")
        table.add_column("Median", justify="right")
        table.add_column("P95", justify="right")
        table.add_column("Min", justify="right")
        table.add_column("Max", justify="right")

        for metric, data in sorted(metrics.items()):
            table.add_row(
                metric,
                f"{data['mean']:.4f}",
                f"{data['median']:.4f}",
                f"{data['p95']:.4f}",
                f"{data['min']:.4f}",
                f"{data['max']:.4f}",
            )

        console.print(table)
        console.print()

    # Summary
    total_results = summary.get("total_results", 0)
    error_count = summary.get("error_count", 0)
    avg_latency = summary.get("avg_latency_ms", 0)
    total_cost = summary.get("total_cost_usd", 0)

    console.print(
        Panel(
            f"  Run ID:      [bold]{result['id']}[/bold]\n"
            f"  Status:      [green]{result['status']}[/green]\n"
            f"  Results:     {total_results} ({error_count} errors)\n"
            f"  Avg Latency: {avg_latency}ms\n"
            f"  Total Cost:  ${total_cost:.6f}",
            title="Summary",
            border_style="green",
        )
    )
    console.print()


@eval_app.command(name="datasets")
def eval_datasets(
    team: str = typer.Option(None, "--team", "-t", help="Filter by team"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List evaluation datasets."""
    store = _get_store()
    datasets = store.list_datasets(team=team)

    if json_output:
        sys.stdout.write(json_lib.dumps(datasets, indent=2) + "\n")
        return

    if not datasets:
        console.print("[dim]No datasets found.[/dim]")
        return

    table = Table(title="Evaluation Datasets", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Name", style="bold")
    table.add_column("Team")
    table.add_column("Rows", justify="right")
    table.add_column("Version")
    table.add_column("Tags")

    for ds in datasets:
        table.add_row(
            ds["id"][:8] + "...",
            ds["name"],
            ds["team"],
            str(ds["row_count"]),
            ds["version"],
            ", ".join(ds.get("tags", [])),
        )

    console.print(table)
    console.print()


@eval_app.command(name="results")
def eval_results(
    run_id: str = typer.Argument(..., help="Eval run ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """View results for an eval run."""
    store = _get_store()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run '{run_id}' not found.[/red]")
        raise typer.Exit(code=1)

    results = store.get_results(run_id)

    if json_output:
        run["results"] = results
        sys.stdout.write(json_lib.dumps(run, indent=2) + "\n")
        return

    console.print()
    console.print(
        Panel(
            f"  Agent:   [cyan]{run['agent_name']}[/cyan]\n"
            f"  Status:  {run['status']}\n"
            f"  Dataset: [dim]{run['dataset_id'][:8]}...[/dim]",
            title=f"Run {run_id[:8]}...",
            border_style="blue",
        )
    )
    console.print()

    if not results:
        console.print("[dim]No results yet.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Output", max_width=40)
    table.add_column("Correctness", justify="right")
    table.add_column("Relevance", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Error", max_width=20)

    for i, r in enumerate(results, 1):
        scores = r.get("scores", {})
        table.add_row(
            str(i),
            r["actual_output"][:37] + "..."
            if len(r["actual_output"]) > 40
            else r["actual_output"],
            f"{scores.get('correctness', 0):.4f}",
            f"{scores.get('relevance', 0):.4f}",
            f"{r['latency_ms']}ms",
            f"${r['cost_usd']:.6f}",
            r.get("error") or "-",
        )

    console.print(table)
    console.print()


@eval_app.command(name="gate")
def eval_gate(
    run_id: str = typer.Argument(..., help="Eval run ID to check"),
    threshold: float = typer.Option(
        0.7, "--threshold", "-t", help="Minimum acceptable score (0.0-1.0)"
    ),
    metrics: str = typer.Option(
        "correctness,relevance",
        "--metrics",
        "-m",
        help="Comma-separated list of metrics to check",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Check if an eval run passes a quality gate.

    Returns exit code 0 if all specified metrics meet the threshold,
    exit code 1 if any metric fails. Used by CI pipelines to block merges.
    """
    store = _get_store()
    run = store.get_run(run_id)
    if not run:
        if json_output:
            sys.stdout.write(json_lib.dumps({"error": f"Run '{run_id}' not found"}) + "\n")
        else:
            console.print(f"[red]Run '{run_id}' not found.[/red]")
        raise typer.Exit(code=1)

    if run["status"] != "completed":
        if json_output:
            sys.stdout.write(
                json_lib.dumps({"error": f"Run is not completed (status: {run['status']})"}) + "\n"
            )
        else:
            console.print(f"[red]Run is not completed (status: {run['status']}).[/red]")
        raise typer.Exit(code=1)

    summary = run.get("summary", {})
    run_metrics = summary.get("metrics", {})
    metric_list = [m.strip() for m in metrics.split(",") if m.strip()]

    gate_results: list[dict] = []
    all_passed = True

    for metric in metric_list:
        metric_data = run_metrics.get(metric, {})
        score = metric_data.get("mean", 0.0)
        passed = score >= threshold
        if not passed:
            all_passed = False
        gate_results.append(
            {
                "metric": metric,
                "score": round(score, 4),
                "threshold": threshold,
                "passed": passed,
            }
        )

    result = {
        "run_id": run_id,
        "passed": all_passed,
        "threshold": threshold,
        "results": gate_results,
    }

    if json_output:
        sys.stdout.write(json_lib.dumps(result, indent=2) + "\n")
    else:
        console.print()
        status_str = "[green]PASSED[/green]" if all_passed else "[red]FAILED[/red]"
        console.print(
            Panel(
                f"  Run:       [bold]{run_id[:8]}...[/bold]\n"
                f"  Status:    {status_str}\n"
                f"  Threshold: {threshold}",
                title="Eval Gate",
                border_style="green" if all_passed else "red",
            )
        )
        console.print()

        table = Table(title="Gate Results", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="bold")
        table.add_column("Score", justify="right")
        table.add_column("Threshold", justify="right")
        table.add_column("Status", justify="center")

        for gr in gate_results:
            status_icon = "[green]PASS[/green]" if gr["passed"] else "[red]FAIL[/red]"
            table.add_row(
                gr["metric"],
                f"{gr['score']:.4f}",
                f"{gr['threshold']:.2f}",
                status_icon,
            )

        console.print(table)
        console.print()

    if not all_passed:
        raise typer.Exit(code=1)


@eval_app.command(name="compare")
def eval_compare(
    run_a: str = typer.Argument(..., help="First run ID"),
    run_b: str = typer.Argument(..., help="Second run ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Compare two eval runs side-by-side."""
    store = _get_store()

    try:
        comparison = store.compare_runs(run_a, run_b)
    except ValueError as e:
        if json_output:
            sys.stdout.write(json_lib.dumps({"error": str(e)}) + "\n")
        else:
            console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None

    if json_output:
        sys.stdout.write(json_lib.dumps(comparison, indent=2) + "\n")
        return

    console.print()
    console.print(
        Panel(
            f"  Run A: [cyan]{comparison['run_a']['agent_name']}[/cyan] ({run_a[:8]}...)\n"
            f"  Run B: [cyan]{comparison['run_b']['agent_name']}[/cyan] ({run_b[:8]}...)",
            title="Run Comparison",
            border_style="blue",
        )
    )
    console.print()

    metrics = comparison.get("comparison", {})
    if not metrics:
        console.print("[dim]No metrics to compare.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Run A (mean)", justify="right")
    table.add_column("Run B (mean)", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("", justify="center")

    for metric, data in sorted(metrics.items()):
        delta = data["delta"]
        if data["improved"]:
            indicator = "[green]+[/green]"
        elif delta < 0:
            indicator = "[red]-[/red]"
        else:
            indicator = "="
        delta_str = f"{delta:+.4f}"
        table.add_row(
            metric,
            f"{data['run_a_mean']:.4f}",
            f"{data['run_b_mean']:.4f}",
            delta_str,
            indicator,
        )

    console.print(table)
    console.print()
