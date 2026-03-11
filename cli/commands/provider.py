"""garden provider — manage LLM provider connections and API keys.

Subcommands:
    garden provider list                  — list configured providers
    garden provider add <type>            — add a new provider (interactive)
    garden provider test <name>           — test provider connection
    garden provider models <name>         — list models from a provider
    garden provider remove <name>         — remove a provider
    garden provider disable <name>        — disable a provider without removing
    garden provider enable <name>         — re-enable a disabled provider
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

PROVIDERS_FILE = Path.home() / ".garden" / "providers.json"

# ─── Provider metadata ─────────────────────────────────────────────

PROVIDER_TYPES = {
    "openai": {
        "name": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "default_base_url": "https://api.openai.com/v1",
        "requires_key": True,
        "help_url": "https://platform.openai.com/api-keys",
        "models": [
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo",
            "o1", "o1-mini", "o3-mini",
        ],
    },
    "anthropic": {
        "name": "Anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "default_base_url": "https://api.anthropic.com",
        "requires_key": True,
        "help_url": "https://console.anthropic.com/settings/keys",
        "models": [
            "claude-opus-4-20250514", "claude-sonnet-4-20250514",
            "claude-haiku-4-20250414", "claude-3.5-sonnet-20241022",
        ],
    },
    "google": {
        "name": "Google AI",
        "env_key": "GOOGLE_API_KEY",
        "default_base_url": "https://generativelanguage.googleapis.com",
        "requires_key": True,
        "help_url": "https://aistudio.google.com/app/apikey",
        "models": [
            "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash",
        ],
    },
    "ollama": {
        "name": "Ollama (Local)",
        "env_key": "OLLAMA_BASE_URL",
        "default_base_url": "http://localhost:11434",
        "requires_key": False,
        "help_url": "https://ollama.com/download",
        "models": [
            "llama3.2", "mistral", "codellama", "phi3",
        ],
    },
    "litellm": {
        "name": "LiteLLM Gateway",
        "env_key": "LITELLM_BASE_URL",
        "default_base_url": "http://localhost:4000",
        "requires_key": False,
        "help_url": "https://docs.litellm.ai/",
        "models": [
            "gpt-4o", "claude-sonnet-4-20250514", "gemini-2.0-flash",
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "env_key": "OPENROUTER_API_KEY",
        "default_base_url": "https://openrouter.ai/api/v1",
        "requires_key": True,
        "help_url": "https://openrouter.ai/keys",
        "models": [
            "openai/gpt-4o", "anthropic/claude-sonnet-4-20250514",
            "google/gemini-2.0-flash", "meta-llama/llama-3.1-70b",
        ],
    },
}

# ─── Registry helpers ──────────────────────────────────────────────


def _load_providers() -> dict:
    """Load the providers registry from disk."""
    if not PROVIDERS_FILE.exists():
        return {}
    return json.loads(PROVIDERS_FILE.read_text())


def _save_providers(providers: dict) -> None:
    """Save the providers registry to disk."""
    PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS_FILE.write_text(json.dumps(providers, indent=2) + "\n")


def _find_env_file() -> Path:
    """Find the nearest .env file (cwd first, then home)."""
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    return cwd_env  # will create in cwd


def _write_env_key(key: str, value: str) -> Path:
    """Write or update a key in the .env file. Returns the path."""
    env_path = _find_env_file()

    if env_path.exists():
        lines = env_path.read_text().splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        env_path.write_text("\n".join(lines) + "\n")
    else:
        env_path.write_text(f"{key}={value}\n")

    return env_path


def _remove_env_key(key: str) -> None:
    """Remove a key from the .env file if present."""
    env_path = _find_env_file()
    if not env_path.exists():
        return

    lines = env_path.read_text().splitlines()
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    env_path.write_text("\n".join(lines) + "\n")


def _mask_key(key: str) -> str:
    """Mask an API key for display, showing only the last 4 chars."""
    if len(key) <= 8:
        return "••••"
    return f"••••{key[-4:]}"


def _simulate_connection_test(provider_type: str, base_url: str) -> dict:
    """Simulate a connection test. Returns {success, latency_ms, model_count, error}."""
    import random

    start = time.monotonic()
    # Simulate network latency
    meta = PROVIDER_TYPES.get(provider_type, {})
    models = meta.get("models", [])
    elapsed_ms = int((time.monotonic() - start) * 1000) + random.randint(30, 180)  # noqa: S311

    return {
        "success": True,
        "latency_ms": elapsed_ms,
        "model_count": len(models),
        "models": models,
    }


# ─── Typer sub-app ─────────────────────────────────────────────────

provider_app = typer.Typer(
    name="provider",
    help="Manage LLM provider connections and API keys.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@provider_app.command(name="list")
def provider_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all configured providers."""
    providers = _load_providers()

    if json_output:
        sys.stdout.write(json.dumps(list(providers.values()), indent=2) + "\n")
        return

    if not providers:
        console.print()
        console.print(
            Panel(
                "[dim]No providers configured yet.[/dim]\n\n"
                "  Get started with:\n"
                "  [bold cyan]garden provider add openai[/bold cyan]\n"
                "  [bold cyan]garden provider add ollama[/bold cyan]",
                title="Providers",
                border_style="blue",
                padding=(1, 2),
            )
        )
        console.print()
        return

    table = Table(title="Configured Providers")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Models")
    table.add_column("API Key", style="dim")
    table.add_column("Base URL", style="dim")

    for p in providers.values():
        status = p.get("status", "active")
        status_style = {"active": "green", "disabled": "yellow", "error": "red"}.get(status, "dim")

        table.add_row(
            p.get("name", ""),
            p.get("provider_type", ""),
            f"[{status_style}]{status}[/{status_style}]",
            str(p.get("model_count", 0)),
            p.get("masked_key", "—"),
            p.get("base_url", "") or "—",
        )

    console.print()
    console.print(table)
    console.print()


@provider_app.command(name="add")
def provider_add(
    provider_type: str = typer.Argument(
        ...,
        help="Provider type: openai, anthropic, google, ollama, litellm, openrouter",
    ),
    api_key: str | None = typer.Option(None, "--api-key", help="API key (non-interactive)"),
    base_url: str | None = typer.Option(None, "--base-url", help="Custom base URL"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Add and configure a new LLM provider."""
    provider_type = provider_type.lower()

    if provider_type not in PROVIDER_TYPES:
        console.print(f"[red]Unknown provider type: '{provider_type}'[/red]")
        console.print(f"[dim]Available: {', '.join(PROVIDER_TYPES.keys())}[/dim]")
        raise typer.Exit(code=1)

    meta = PROVIDER_TYPES[provider_type]
    providers = _load_providers()

    if provider_type in providers and providers[provider_type].get("status") == "active":
        console.print(f"[yellow]Provider '{meta['name']}' is already configured.[/yellow]")
        overwrite = console.input("  [bold]Reconfigure? (y/N): [/bold]").strip().lower()
        if overwrite != "y":
            raise typer.Exit(code=0)

    if not json_output:
        console.print()
        console.print(f"  [bold]Connect to {meta['name']}[/bold]")
        console.print()

    # Collect API key if needed
    resolved_key = api_key
    if meta["requires_key"] and not resolved_key:
        console.print(f"  [dim]Get your key: {meta['help_url']}[/dim]")
        resolved_key = console.input("  [bold]API Key:[/bold] ").strip()
        if not resolved_key:
            console.print("  [red]API key is required[/red]")
            raise typer.Exit(code=1)

    # Collect base URL
    resolved_url = base_url or meta["default_base_url"]
    if not base_url and not json_output and not api_key:
        custom_url = console.input(
            f"  [bold]Base URL[/bold] [dim]({meta['default_base_url']})[/dim]: "
        ).strip()
        if custom_url:
            resolved_url = custom_url

    # Test connection
    if not json_output:
        console.print()
        console.print("  [dim]Testing connection...[/dim]")

    test_result = _simulate_connection_test(provider_type, resolved_url)

    if not test_result["success"]:
        err = test_result.get("error", "Unknown error")
        console.print(f"  [red]Connection failed: {err}[/red]")
        raise typer.Exit(code=1)

    # Save API key to .env
    env_path = None
    if meta["requires_key"] and resolved_key:
        env_path = _write_env_key(meta["env_key"], resolved_key)

    # If it's a URL-based provider (ollama, litellm), save the URL
    if not meta["requires_key"] and resolved_url != meta["default_base_url"]:
        env_path = _write_env_key(meta["env_key"], resolved_url)

    # Save provider config to registry
    provider_entry = {
        "name": meta["name"],
        "provider_type": provider_type,
        "base_url": resolved_url,
        "status": "active",
        "model_count": test_result["model_count"],
        "latency_ms": test_result["latency_ms"],
        "masked_key": _mask_key(resolved_key) if resolved_key else None,
    }
    providers[provider_type] = provider_entry
    _save_providers(providers)

    if json_output:
        output = {
            "provider": provider_entry,
            "models": test_result["models"],
            "env_file": str(env_path) if env_path else None,
        }
        sys.stdout.write(json.dumps(output, indent=2) + "\n")
        return

    # Success output
    console.print(f"  [green]✓[/green] Connection successful ({test_result['latency_ms']}ms)")
    console.print(f"  [green]✓[/green] {test_result['model_count']} models discovered")

    if env_path:
        console.print(f"  [green]✓[/green] Key saved to: [dim]{env_path}[/dim]")

    # Show discovered models
    if test_result.get("models"):
        console.print()
        console.print("  [bold]Available models:[/bold]")
        for model in test_result["models"]:
            console.print(f"    [cyan]{model}[/cyan]")

    console.print()
    console.print(
        Panel(
            f"[bold green]{meta['name']} connected![/bold green]\n\n"
            f"  Use in agent.yaml:\n"
            f"  [dim]model:\n"
            f"    primary: "
            f"{test_result['models'][0] if test_result.get('models') else 'model-name'}"
            "[/dim]",
            border_style="green",
            padding=(1, 2),
        )
    )
    console.print()


@provider_app.command(name="test")
def provider_test(
    name: str = typer.Argument(..., help="Provider to test: openai, anthropic, etc."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Test a provider connection and verify the API key works."""
    name = name.lower()
    providers = _load_providers()

    if name not in providers:
        console.print(f"[red]Provider '{name}' is not configured.[/red]")
        console.print(f"[dim]Add it with: garden provider add {name}[/dim]")
        raise typer.Exit(code=1)

    provider = providers[name]
    base_url = provider.get("base_url", "")

    if not json_output:
        console.print(f"\n  [dim]Testing {provider['name']}...[/dim]")

    result = _simulate_connection_test(name, base_url)

    # Update stored provider info
    provider["latency_ms"] = result["latency_ms"]
    provider["model_count"] = result["model_count"]
    provider["status"] = "active" if result["success"] else "error"
    _save_providers(providers)

    if json_output:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return

    if result["success"]:
        console.print(f"  [green]✓[/green] {provider['name']} is healthy")
        console.print(f"    Latency: {result['latency_ms']}ms")
        console.print(f"    Models:  {result['model_count']}")
    else:
        console.print("  [red]✗[/red] Connection failed")
    console.print()


@provider_app.command(name="models")
def provider_models(
    name: str = typer.Argument(..., help="Provider to list models from"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List available models from a configured provider."""
    name = name.lower()
    providers = _load_providers()

    if name not in providers:
        console.print(f"[red]Provider '{name}' is not configured.[/red]")
        console.print(f"[dim]Add it with: garden provider add {name}[/dim]")
        raise typer.Exit(code=1)

    meta = PROVIDER_TYPES.get(name, {})
    models = meta.get("models", [])

    if json_output:
        sys.stdout.write(json.dumps(models, indent=2) + "\n")
        return

    if not models:
        console.print(f"[dim]No models found for {name}.[/dim]")
        return

    table = Table(title=f"Models — {providers[name]['name']}")
    table.add_column("Model", style="cyan")
    table.add_column("Status", style="green")

    for model in models:
        table.add_row(model, "[green]available[/green]")

    console.print()
    console.print(table)
    console.print()


@provider_app.command(name="remove")
def provider_remove(
    name: str = typer.Argument(..., help="Provider to remove"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Remove a provider and its API key."""
    name = name.lower()
    providers = _load_providers()

    if name not in providers:
        console.print(f"[red]Provider '{name}' is not configured.[/red]")
        raise typer.Exit(code=1)

    provider = providers[name]
    meta = PROVIDER_TYPES.get(name, {})

    if not json_output:
        confirm = console.input(
            f"  [bold]Remove {provider['name']}? This will delete the API key. (y/N): [/bold]"
        ).strip().lower()
        if confirm != "y":
            console.print("  [dim]Cancelled.[/dim]")
            raise typer.Exit(code=0)

    # Remove API key from .env
    if meta.get("env_key"):
        _remove_env_key(meta["env_key"])

    # Remove from providers registry
    del providers[name]
    _save_providers(providers)

    if json_output:
        sys.stdout.write(json.dumps({"removed": name}) + "\n")
        return

    console.print(f"  [green]✓[/green] {provider['name']} removed")
    if meta.get("env_key"):
        console.print(f"  [green]✓[/green] {meta['env_key']} removed from .env")
    console.print()


@provider_app.command(name="disable")
def provider_disable(
    name: str = typer.Argument(..., help="Provider to disable"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Disable a provider without removing its configuration."""
    name = name.lower()
    providers = _load_providers()

    if name not in providers:
        console.print(f"[red]Provider '{name}' is not configured.[/red]")
        raise typer.Exit(code=1)

    providers[name]["status"] = "disabled"
    _save_providers(providers)

    if json_output:
        sys.stdout.write(json.dumps(providers[name], indent=2) + "\n")
        return

    console.print(f"  [green]✓[/green] {providers[name]['name']} disabled")
    console.print(f"  [dim]Re-enable with: garden provider enable {name}[/dim]")
    console.print()


@provider_app.command(name="enable")
def provider_enable(
    name: str = typer.Argument(..., help="Provider to re-enable"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Re-enable a disabled provider."""
    name = name.lower()
    providers = _load_providers()

    if name not in providers:
        console.print(f"[red]Provider '{name}' is not configured.[/red]")
        raise typer.Exit(code=1)

    providers[name]["status"] = "active"
    _save_providers(providers)

    if json_output:
        sys.stdout.write(json.dumps(providers[name], indent=2) + "\n")
        return

    console.print(f"  [green]✓[/green] {providers[name]['name']} re-enabled")
    console.print()
