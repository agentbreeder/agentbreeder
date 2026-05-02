"""AgentBreeder CLI — the developer interface.

Usage:
    agentbreeder deploy ./agent.yaml --target local
    agentbreeder validate ./agent.yaml
    agentbreeder list agents
    agentbreeder describe agent <name>
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import typer


def _version_callback(value: bool) -> None:
    if value:
        try:
            v = _pkg_version("agentbreeder")
        except PackageNotFoundError:
            v = "dev"
        typer.echo(f"agentbreeder {v}")
        raise typer.Exit()


# ── pyenv environment check ─────────────────────────────────────────────────
# AgentBreeder recommends pyenv-managed Pythons for reproducibility across
# machines. Warn (don't fail) when the active interpreter isn't from pyenv.


def _is_pyenv_python() -> bool:
    """Return True if sys.executable looks like a pyenv-managed interpreter."""
    try:
        exe = Path(sys.executable).resolve()
    except OSError:
        return False
    pyenv_root = Path(os.environ.get("PYENV_ROOT") or (Path.home() / ".pyenv")).resolve()
    if pyenv_root in exe.parents:
        return True
    # Also accept if the user has activated a pyenv shim
    return ".pyenv/" in str(exe) or shutil.which("pyenv") is not None and "pyenv" in str(exe)


def _print_pyenv_warning() -> None:
    """Show a one-line note when not running under pyenv."""
    if _is_pyenv_python():
        return
    from rich.console import Console
    from rich.panel import Panel

    Console().print(
        Panel(
            f"[yellow]Not running under pyenv.[/yellow]\n\n"
            f"  Current Python: [dim]{sys.executable}[/dim]\n\n"
            "AgentBreeder recommends pyenv for reproducible Python versions across\n"
            "macOS, Linux, and CI:\n\n"
            "  [cyan]brew install pyenv pyenv-virtualenv[/cyan]\n"
            "  [cyan]pyenv install 3.11.9 && pyenv shell 3.11.9[/cyan]\n"
            "  [cyan]pip install -e .[/cyan]\n\n"
            "[dim]This is a recommendation, not a hard requirement — "
            "AgentBreeder will continue to work in any Python ≥ 3.11.[/dim]",
            title="[bold yellow]Python Environment[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


# ── First-run "Getting Started" banner ──────────────────────────────────────
# Printed the first time the user invokes bare `agentbreeder` (or `--help`)
# after `pip install`. Suppressed on subsequent runs via a marker file.
# Re-show any time with `agentbreeder welcome`.

_WELCOME_MARKER = Path(
    os.environ.get("AGENTBREEDER_HOME") or (Path.home() / ".config" / "agentbreeder")
) / ".welcomed"


def _print_welcome() -> None:
    """Render the Getting Started panel."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print()
    console.print(
        Panel(
            "[bold cyan]Welcome to AgentBreeder![/bold cyan]\n"
            "[dim]Define Once. Deploy Anywhere. Govern Automatically.[/dim]\n\n"
            "[bold]Just want to see it work? (recommended for first-timers)[/bold]\n\n"
            "  [bold cyan]agentbreeder quickstart[/bold cyan]\n"
            "    [dim]Bootstraps the full local stack (API, dashboard, RAG, GraphRAG,[/dim]\n"
            "    [dim]MCP servers) and deploys 5 sample agents. ~3 min on first run.[/dim]\n\n"
            "[bold]Building from scratch? Follow the path:[/bold]\n\n"
            "  1. [bold cyan]agentbreeder setup[/bold cyan]"
            "       [dim]Configure Ollama / cloud API keys (interactive)[/dim]\n"
            "  2. [bold cyan]agentbreeder init[/bold cyan]"
            "        [dim]Scaffold a new agent project (agent.yaml + code)[/dim]\n"
            "  3. [bold cyan]agentbreeder validate[/bold cyan]"
            "    [dim]Validate your agent.yaml[/dim]\n"
            "  4. [bold cyan]agentbreeder deploy --target local[/bold cyan]\n"
            "    [dim]Deploy to local Docker / Podman (or --target aws | gcp | azure)[/dim]\n"
            "  5. [bold cyan]agentbreeder ui[/bold cyan]"
            "          [dim]Open the dashboard at http://localhost:3001[/dim]\n\n"
            "[bold]Useful anytime:[/bold]\n"
            "  [cyan]agentbreeder list agents[/cyan]    [dim]· see what's registered[/dim]\n"
            "  [cyan]agentbreeder chat <name>[/cyan]    [dim]· talk to an agent[/dim]\n"
            "  [cyan]agentbreeder down[/cyan]           [dim]· stop the local stack[/dim]\n"
            "  [cyan]agentbreeder --help[/cyan]         [dim]· full command list[/dim]\n\n"
            "[dim]Docs: https://agentbreeder.io/docs/quickstart[/dim]\n"
            "[dim]Re-show this guide any time with [bold]agentbreeder welcome[/bold].[/dim]",
            title="[bold green]Getting Started[/bold green]",
            border_style="green",
            padding=(1, 3),
        )
    )
    console.print()


def _maybe_show_welcome() -> None:
    """Print the welcome panel only once per machine (per AGENTBREEDER_HOME).

    On first run, also offer to launch `agentbreeder quickstart` immediately so
    a clean machine goes from `pip install` → fully working stack with one Y.
    """
    if _WELCOME_MARKER.exists():
        return
    try:
        _WELCOME_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _WELCOME_MARKER.touch()
    except OSError:
        # If we can't write the marker, still show the banner — but this means
        # it'll show every run. Better than silently swallowing the welcome.
        pass

    _print_pyenv_warning()
    _print_welcome()

    # Auto-launch quickstart on first run if STDIN is a TTY.
    if not sys.stdin.isatty():
        return
    from rich.console import Console

    console = Console()
    ans = (
        console.input(
            "  [bold]Run [cyan]agentbreeder quickstart[/cyan] now?[/bold] "
            "[dim](full local stack + sample agents, ~3 min)[/dim] [Y/n]: "
        )
        .strip()
        .lower()
    )
    if ans in ("n", "no", "skip"):
        console.print(
            "  [dim]No problem. Run [bold cyan]agentbreeder quickstart[/bold cyan] "
            "any time you're ready.[/dim]\n"
        )
        return
    binary = shutil.which("agentbreeder") or sys.argv[0]
    result = subprocess.run([binary, "quickstart"])
    raise typer.Exit(code=result.returncode)


def _welcome_cmd() -> None:
    """Show the AgentBreeder Getting Started guide."""
    _print_welcome()


from cli.commands import (
    chat,
    compliance,
    deploy,
    describe,
    down,
    init_cmd,
    list_cmd,
    logs,
    model,
    orchestration,
    provider,
    publish,
    quickstart,
    registry_cmd,
    review,
    scan,
    schedule,
    search,
    secret,
    seed,
    setup,
    status,
    submit,
    teardown,
    template,
    ui,
    up,
    validate,
)
from cli.commands import (
    eval as eval_cmd,
)


def _main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    # First-run Getting Started banner: only when the user runs bare
    # `agentbreeder` (which Typer turns into help via no_args_is_help=True).
    # Skip if a subcommand was given so we don't spam real workflows.
    if ctx.invoked_subcommand is None:
        _maybe_show_welcome()


app = typer.Typer(
    name="agentbreeder",
    help="AgentBreeder — Define Once. Deploy Anywhere. Govern Automatically.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    callback=_main_callback,
)

app.command(name="welcome")(_welcome_cmd)
app.command(name="quickstart")(quickstart.quickstart)
app.command(name="seed")(seed.seed)
app.command(name="setup")(setup.setup)
app.command(name="ui")(ui.ui)
app.command(name="up")(up.up)
app.command(name="down")(down.down)
app.command(name="init")(init_cmd.init)
app.command(name="deploy")(deploy.deploy)
app.command(name="validate")(validate.validate)
app.command(name="list")(list_cmd.list_entities)
app.command(name="describe")(describe.describe)
app.command(name="search")(search.search)
app.command(name="scan")(scan.scan)
app.command(name="schedule")(schedule.schedule)
app.command(name="logs")(logs.logs)
app.command(name="status")(status.status)
app.command(name="teardown")(teardown.teardown)
app.command(name="submit")(submit.submit)
app.command(name="publish")(publish.publish)
app.command(name="chat")(chat.chat)
app.add_typer(provider.provider_app, name="provider")
app.add_typer(model.model_app, name="model")
app.add_typer(review.review_app, name="review")
app.add_typer(eval_cmd.eval_app, name="eval")
app.add_typer(orchestration.orchestration_app, name="orchestration")
app.add_typer(template.template_app, name="template")
app.add_typer(secret.secret_app, name="secret")
app.add_typer(compliance.compliance_app, name="compliance")
app.add_typer(registry_cmd.registry_app, name="registry")


if __name__ == "__main__":
    app()
