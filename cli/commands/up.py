"""garden up — start the entire Agent Garden platform locally.

One command to launch PostgreSQL, Redis, API, Dashboard, and LiteLLM.
Users can configure API keys interactively or via .env file.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# ─── Paths ──────────────────────────────────────────────────────────

GARDEN_HOME = Path.home() / ".garden"


def _find_compose_dir() -> Path | None:
    """Find the deploy/ directory containing docker-compose.yml.

    Checks in order:
    1. Current working directory (source checkout)
    2. Git repo root
    3. Bundled files in the package
    """
    # 1. Check cwd or parent
    for candidate in [Path.cwd(), Path.cwd().parent]:
        compose = candidate / "deploy" / "docker-compose.yml"
        if compose.exists():
            return candidate / "deploy"

    # 2. Check git root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            root = Path(result.stdout.strip())
            compose = root / "deploy" / "docker-compose.yml"
            if compose.exists():
                return root / "deploy"
    except Exception:
        pass

    # 3. Check bundled (pip-installed package)
    bundled = Path(__file__).parent.parent / "_bundled"
    if (bundled / "docker-compose.yml").exists():
        return bundled

    return None


def _check_docker() -> bool:
    """Verify Docker and Docker Compose are available."""
    if not shutil.which("docker"):
        console.print(
            Panel(
                "[bold red]Docker not found[/bold red]\n\n"
                "Agent Garden requires Docker to run locally.\n\n"
                "Install Docker Desktop:\n"
                "  macOS/Windows: https://docker.com/products/docker-desktop\n"
                "  Linux:         https://docs.docker.com/engine/install",
                title="Missing Dependency",
                border_style="red",
            )
        )
        return False

    # Check Docker Compose v2
    result = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(
            Panel(
                "[bold red]Docker Compose v2 not found[/bold red]\n\n"
                "Agent Garden requires Docker Compose v2 (the `docker compose` subcommand).\n"
                "Upgrade Docker Desktop or install the compose plugin.",
                title="Missing Dependency",
                border_style="red",
            )
        )
        return False

    # Check Docker daemon is running
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(
            Panel(
                "[bold red]Docker daemon is not running[/bold red]\n\n"
                "Start Docker Desktop or the Docker service, then try again.",
                title="Docker Not Running",
                border_style="red",
            )
        )
        return False

    return True


def _generate_env(env_path: Path, interactive: bool = True) -> None:
    """Create a .env file with secure defaults, optionally prompting for API keys."""
    secret_key = secrets.token_hex(32)
    jwt_key = secrets.token_hex(32)

    # Defaults
    env_vars: dict[str, str] = {
        "DATABASE_URL": "postgresql+asyncpg://garden:garden@localhost:5432/agentgarden",
        "REDIS_URL": "redis://localhost:6379",
        "SECRET_KEY": secret_key,
        "JWT_SECRET_KEY": jwt_key,
        "JWT_ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "1440",
        "GARDEN_ENV": "development",
        "LITELLM_BASE_URL": "http://localhost:4000",
        "LITELLM_MASTER_KEY": f"sk-garden-{secrets.token_hex(16)}",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GOOGLE_AI_API_KEY": "",
    }

    if interactive:
        console.print()
        console.print(
            Panel(
                "[bold]API Key Setup[/bold] [dim](all optional — skip with Enter)[/dim]\n\n"
                "You can also configure these later in the dashboard at\n"
                "[cyan]http://localhost:3001/settings[/cyan]",
                border_style="blue",
                padding=(1, 2),
            )
        )
        console.print()

        api_keys = [
            ("OPENAI_API_KEY", "OpenAI API key", "sk-..."),
            ("ANTHROPIC_API_KEY", "Anthropic API key", "sk-ant-..."),
            ("GOOGLE_AI_API_KEY", "Google AI API key", "AI..."),
        ]

        for var, label, hint in api_keys:
            raw = console.input(f"  [bold]{label}[/bold] [dim]({hint})[/dim]: ").strip()
            if raw:
                env_vars[var] = raw
                console.print(f"  [green]✓[/green] {label} set")
            else:
                console.print("  [dim]  Skipped[/dim]")

    # Write .env file
    lines = [
        "# Agent Garden — Local Environment",
        "# Generated by `garden up`. Edit freely or configure via dashboard.",
        "",
        "# Infrastructure (managed by docker compose — don't change unless custom setup)",
        f"DATABASE_URL={env_vars['DATABASE_URL']}",
        f"REDIS_URL={env_vars['REDIS_URL']}",
        "",
        "# Security",
        f"SECRET_KEY={env_vars['SECRET_KEY']}",
        f"JWT_SECRET_KEY={env_vars['JWT_SECRET_KEY']}",
        f"JWT_ALGORITHM={env_vars['JWT_ALGORITHM']}",
        f"ACCESS_TOKEN_EXPIRE_MINUTES={env_vars['ACCESS_TOKEN_EXPIRE_MINUTES']}",
        "",
        "# Environment",
        f"GARDEN_ENV={env_vars['GARDEN_ENV']}",
        "",
        "# LiteLLM Model Gateway",
        f"LITELLM_BASE_URL={env_vars['LITELLM_BASE_URL']}",
        f"LITELLM_MASTER_KEY={env_vars['LITELLM_MASTER_KEY']}",
        "",
        "# LLM Provider API Keys (configure here or in dashboard Settings)",
        f"OPENAI_API_KEY={env_vars['OPENAI_API_KEY']}",
        f"ANTHROPIC_API_KEY={env_vars['ANTHROPIC_API_KEY']}",
        f"GOOGLE_AI_API_KEY={env_vars['GOOGLE_AI_API_KEY']}",
        "",
    ]

    env_path.write_text("\n".join(lines))


def _wait_for_health(url: str, label: str, timeout: int = 90) -> bool:
    """Poll a health endpoint until it responds 200."""
    import httpx

    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = httpx.get(url, timeout=3)
            if resp.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
            pass
        time.sleep(2)
    return False


# ─── Main command ────────────────────────────────────────────────────


def up(
    no_input: bool = typer.Option(
        False,
        "--no-input",
        help="Skip interactive prompts, use defaults",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't open browser after startup",
    ),
    env_file: Path = typer.Option(
        None,
        "--env-file",
        help="Path to an existing .env file to use",
    ),
    build: bool = typer.Option(
        True,
        "--build/--no-build",
        help="Build images from source (disable to use cached images)",
    ),
) -> None:
    """Start Agent Garden locally — API, Dashboard, Database, and more.

    Launches the full Agent Garden platform with a single command.
    Prompts for API keys on first run (or use --no-input for defaults).

    \b
    Services started:
      - PostgreSQL (port 5432)
      - Redis (port 6379)
      - API server (port 8000)
      - Dashboard (port 3001)
      - LiteLLM gateway (port 4000)
    """
    console.print()
    console.print(
        Panel(
            "[bold]Agent Garden[/bold]\n[dim]Starting the platform locally...[/dim]",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # ── Step 1: Check Docker ──────────────────────────────────────
    if not _check_docker():
        raise typer.Exit(code=1)
    console.print("  [green]✓[/green] Docker is running")

    # ── Step 2: Find compose file ────────────────────────────────
    compose_dir = _find_compose_dir()
    if compose_dir is None:
        console.print(
            Panel(
                "[bold red]Could not find docker-compose.yml[/bold red]\n\n"
                "Run this command from the Agent Garden repository root,\n"
                "or install via: pip install agent-garden",
                title="Not Found",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    compose_file = compose_dir / "docker-compose.yml"
    project_root = compose_dir.parent
    console.print(f"  [green]✓[/green] Found compose file: [dim]{compose_file}[/dim]")

    # ── Step 3: Handle .env ──────────────────────────────────────
    if env_file:
        target_env = env_file.resolve()
        if not target_env.exists():
            console.print(f"  [red]✗[/red] Env file not found: {target_env}")
            raise typer.Exit(code=1)
    else:
        target_env = project_root / ".env"

    if not target_env.exists():
        console.print("  [dim]  No .env file found — creating one...[/dim]")
        _generate_env(target_env, interactive=not no_input)
        console.print(f"  [green]✓[/green] Created {target_env}")
    else:
        console.print(f"  [green]✓[/green] Using existing .env: [dim]{target_env}[/dim]")

    # ── Step 4: Start services ───────────────────────────────────
    console.print()
    console.print("  [bold]Starting services...[/bold]")
    console.print()

    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "--project-directory",
        str(project_root),
        "up",
        "-d",
    ]
    if build:
        cmd.append("--build")

    result = subprocess.run(cmd, cwd=str(project_root))  # noqa: S603

    if result.returncode != 0:
        console.print(
            Panel(
                "[bold red]Failed to start services[/bold red]\n\n"
                "Check the output above for errors.\n"
                "Common issues:\n"
                "  - Port already in use (5432, 6379, 8000, 3001, 4000)\n"
                "  - Docker out of disk space\n"
                "  - Missing Dockerfile",
                title="Startup Failed",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    # ── Step 5: Wait for health ──────────────────────────────────
    console.print()
    with console.status("[bold blue]Waiting for services to be healthy..."):
        api_ok = _wait_for_health("http://localhost:8000/health", "API")
        dash_ok = _wait_for_health("http://localhost:3001", "Dashboard")

    if not api_ok:
        console.print("  [yellow]⚠[/yellow] API did not become healthy within 90s")
        console.print(
            "    [dim]Check logs: docker compose -f deploy/docker-compose.yml logs api[/dim]"
        )
    else:
        console.print("  [green]✓[/green] API is healthy")

    if not dash_ok:
        console.print("  [yellow]⚠[/yellow] Dashboard did not become healthy within 90s")
        console.print(
            "    [dim]Check logs: docker compose -f deploy/docker-compose.yml logs dashboard[/dim]"
        )
    else:
        console.print("  [green]✓[/green] Dashboard is healthy")

    # ── Step 6: Success ──────────────────────────────────────────
    console.print()

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 3))
    table.add_column("Service", style="bold")
    table.add_column("URL")
    table.add_column("Status")

    table.add_row(
        "Dashboard",
        "[cyan]http://localhost:3001[/cyan]",
        "[green]●[/green] Running" if dash_ok else "[yellow]●[/yellow] Starting",
    )
    table.add_row(
        "API",
        "[cyan]http://localhost:8000[/cyan]",
        "[green]●[/green] Running" if api_ok else "[yellow]●[/yellow] Starting",
    )
    table.add_row(
        "API Docs",
        "[cyan]http://localhost:8000/docs[/cyan]",
        "[green]●[/green] Running" if api_ok else "[yellow]●[/yellow] Starting",
    )
    table.add_row(
        "LiteLLM",
        "[cyan]http://localhost:4000[/cyan]",
        "[dim]●[/dim] Background",
    )

    console.print(
        Panel(
            table,
            title="[bold green]Agent Garden is running[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )

    console.print(
        "  [dim]Stop with:[/dim]  garden down\n"
        "  [dim]Logs:[/dim]      garden logs   [dim]or[/dim]   "
        "docker compose -f deploy/docker-compose.yml logs -f\n"
        "  [dim]Config:[/dim]    Edit .env and restart with [bold]garden up[/bold]\n"
    )

    # Open browser
    if not no_browser and dash_ok:
        webbrowser.open("http://localhost:3001")
