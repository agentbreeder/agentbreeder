"""garden secret — manage secrets across backends.

Subcommands:
    garden secret list [--backend env|aws|gcp|vault]
    garden secret set <name> [--value VAL] [--backend ...]
    garden secret get <name> [--backend ...]
    garden secret delete <name> [--backend ...]
    garden secret rotate <name> [--backend ...]
    garden secrets migrate --from env --to aws|gcp|vault [--prefix PREFIX] [--dry-run]
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

VALID_BACKENDS = ["env", "aws", "gcp", "vault"]

secret_app = typer.Typer(
    name="secret",
    help="Manage secrets across backends (env, AWS, GCP, Vault).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _get_backend(backend: str, **kwargs):  # type: ignore[return]
    from engine.secrets.factory import get_backend

    if backend not in VALID_BACKENDS:
        choices = ", ".join(VALID_BACKENDS)
        console.print(f"[red]Unknown backend '{backend}'. Choose from: {choices}[/red]")
        raise typer.Exit(code=2)
    try:
        return get_backend(backend, **kwargs)
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except (ValueError, PermissionError) as exc:
        console.print(f"[red]Backend init failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


def _run(coro):  # type: ignore[return]
    return asyncio.run(coro)


# ── list ────────────────────────────────────────────────────────────────────


@secret_app.command(name="list")
def secret_list(
    backend: str = typer.Option("env", "--backend", "-b", help="Backend: env, aws, gcp, vault"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    prefix: str = typer.Option("garden/", "--prefix", help="Secret prefix (AWS/GCP/Vault)"),
) -> None:
    """List secrets in the configured backend (names only — values are masked)."""
    b = _get_backend(backend, prefix=prefix) if backend != "env" else _get_backend("env")
    entries = _run(b.list())

    if json_output:
        sys.stdout.write(json.dumps([e.to_dict() for e in entries], indent=2) + "\n")
        return

    if not entries:
        console.print(f"\n  [dim]No secrets found in '{backend}' backend.[/dim]\n")
        return

    table = Table(title=f"Secrets — {backend}")
    table.add_column("Name", style="cyan")
    table.add_column("Value", style="dim")
    table.add_column("Backend")
    table.add_column("Updated")

    for e in entries:
        updated = e.updated_at.strftime("%Y-%m-%d") if e.updated_at else "—"
        table.add_row(e.name, e.masked_value, e.backend, updated)

    console.print()
    console.print(table)
    console.print()


# ── set ─────────────────────────────────────────────────────────────────────


@secret_app.command(name="set")
def secret_set(
    name: str = typer.Argument(..., help="Secret name (e.g. OPENAI_API_KEY)"),
    value: str | None = typer.Option(None, "--value", "-v", help="Value (prompted if omitted)"),
    backend: str = typer.Option("env", "--backend", "-b", help="Backend: env, aws, gcp, vault"),
    prefix: str = typer.Option("garden/", "--prefix", help="Prefix (AWS/GCP/Vault)"),
    tag: list[str] = typer.Option([], "--tag", "-t", help="key=value tags (cloud backends)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Create or update a secret."""
    resolved_value = value
    if not resolved_value:
        resolved_value = typer.prompt(f"Value for '{name}'", hide_input=True)

    tags: dict[str, str] = {}
    for t in tag:
        if "=" in t:
            k, _, v = t.partition("=")
            tags[k.strip()] = v.strip()

    b = _get_backend(backend, prefix=prefix) if backend != "env" else _get_backend("env")
    _run(b.set(name, resolved_value, tags=tags or None))

    if json_output:
        sys.stdout.write(json.dumps({"name": name, "backend": backend, "status": "ok"}) + "\n")
        return

    console.print(
        f"\n  [green]✓[/green] Secret [bold]{name}[/bold] saved to [bold]{backend}[/bold]\n"
        f"  Reference in agent.yaml: [dim]secret://{name}[/dim]\n"
    )


# ── get ─────────────────────────────────────────────────────────────────────


@secret_app.command(name="get")
def secret_get(
    name: str = typer.Argument(..., help="Secret name"),
    backend: str = typer.Option("env", "--backend", "-b", help="Backend: env, aws, gcp, vault"),
    prefix: str = typer.Option("garden/", "--prefix", help="Secret prefix (AWS/GCP/Vault)"),
    reveal: bool = typer.Option(False, "--reveal", help="Print the actual value (use with care)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Get a secret value (masked by default)."""
    b = _get_backend(backend, prefix=prefix) if backend != "env" else _get_backend("env")
    value = _run(b.get(name))

    if value is None:
        console.print(f"\n  [red]Secret '{name}' not found in '{backend}' backend.[/red]\n")
        raise typer.Exit(code=1)

    masked = value if reveal else (f"••••{value[-4:]}" if len(value) > 4 else "••••")

    if json_output:
        out = {"name": name, "backend": backend}
        if reveal:
            out["value"] = value
        else:
            out["masked_value"] = masked
        sys.stdout.write(json.dumps(out) + "\n")
        return

    label = "Value" if reveal else "Masked"
    console.print(f"\n  [cyan]{name}[/cyan]  {label}: [dim]{masked}[/dim]\n")


# ── delete ───────────────────────────────────────────────────────────────────


@secret_app.command(name="delete")
def secret_delete(
    name: str = typer.Argument(..., help="Secret name to delete"),
    backend: str = typer.Option("env", "--backend", "-b", help="Backend: env, aws, gcp, vault"),
    prefix: str = typer.Option("garden/", "--prefix", help="Secret prefix (AWS/GCP/Vault)"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Delete a secret."""
    if not force and not json_output:
        confirm = typer.confirm(f"Delete secret '{name}' from '{backend}'?", default=False)
        if not confirm:
            console.print("  [dim]Cancelled.[/dim]")
            raise typer.Exit(code=0)

    b = _get_backend(backend, prefix=prefix) if backend != "env" else _get_backend("env")
    try:
        _run(b.delete(name))
    except KeyError as exc:
        console.print(f"\n  [red]{exc}[/red]\n")
        raise typer.Exit(code=1) from exc

    if json_output:
        sys.stdout.write(json.dumps({"name": name, "backend": backend, "deleted": True}) + "\n")
        return

    console.print(
        f"\n  [green]✓[/green] Secret [bold]{name}[/bold] "
        f"deleted from [bold]{backend}[/bold]\n"
    )


# ── rotate ───────────────────────────────────────────────────────────────────


@secret_app.command(name="rotate")
def secret_rotate(
    name: str = typer.Argument(..., help="Secret name to rotate"),
    new_value: str | None = typer.Option(None, "--value", "-v", help="New value (prompted if omitted)"),  # noqa: E501
    backend: str = typer.Option("env", "--backend", "-b", help="Backend: env, aws, gcp, vault"),
    prefix: str = typer.Option("garden/", "--prefix", help="Secret prefix (AWS/GCP/Vault)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Rotate a secret to a new value.

    All agents using this secret pick up the new value on next request.
    """
    resolved_value = new_value
    if not resolved_value:
        resolved_value = typer.prompt(
            f"New value for '{name}'", hide_input=True, confirmation_prompt=True
        )

    b = _get_backend(backend, prefix=prefix) if backend != "env" else _get_backend("env")
    try:
        _run(b.rotate(name, resolved_value))
    except KeyError as exc:
        console.print(f"\n  [red]{exc}[/red]\n")
        raise typer.Exit(code=1) from exc

    if json_output:
        sys.stdout.write(json.dumps({"name": name, "backend": backend, "rotated": True}) + "\n")
        return

    console.print(
        f"\n  [green]✓[/green] Secret [bold]{name}[/bold] rotated in [bold]{backend}[/bold]\n"
        "  [dim]Agents using this secret will pick up the new value on next request.[/dim]\n"
    )


# ── migrate ──────────────────────────────────────────────────────────────────


@secret_app.command(name="migrate")
def secret_migrate(
    from_backend: str = typer.Option(..., "--from", help="Source backend (env, aws, gcp, vault)"),
    to_backend: str = typer.Option(..., "--to", help="Target backend (aws, gcp, vault)"),
    prefix: str = typer.Option("garden/", "--prefix", help="Prefix for secrets in cloud backend"),
    include: list[str] = typer.Option([], "--include", "-i", help="Only migrate these keys"),
    exclude: list[str] = typer.Option([], "--exclude", "-e", help="Skip these keys"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Migrate secrets from one backend to another.

    Example — move all .env API keys to AWS Secrets Manager:

        garden secret migrate --from env --to aws --prefix garden/ --dry-run
        garden secret migrate --from env --to aws --prefix garden/

    After migration, update agent.yaml to use secret:// references:

        model:
          primary: claude-sonnet-4
        deploy:
          secrets:
            - OPENAI_API_KEY    # resolved from secret://OPENAI_API_KEY at deploy time
    """
    if from_backend == to_backend:
        console.print("[red]Source and target backends must be different.[/red]")
        raise typer.Exit(code=2)

    src = _get_backend(from_backend)
    dst = _get_backend(to_backend, prefix=prefix)

    # For env backend, use list_raw() to get actual values for migration
    if from_backend == "env":
        from engine.secrets.env_backend import EnvBackend
        assert isinstance(src, EnvBackend)
        raw = src.list_raw()
        candidates = dict(raw.items())
    else:
        entries = _run(src.list())
        # For non-env backends we need actual values — fetch each one
        candidates: dict[str, str] = {}  # type: ignore[no-redef]
        for e in entries:
            val = _run(src.get(e.name))
            if val is not None:
                candidates[e.name] = val

    # Apply include/exclude filters
    if include:
        candidates = {k: v for k, v in candidates.items() if k in include}
    if exclude:
        candidates = {k: v for k, v in candidates.items() if k not in exclude}

    if not candidates:
        console.print("\n  [yellow]No secrets found to migrate.[/yellow]\n")
        return

    results: list[dict] = []
    errors: list[dict] = []

    if not json_output:
        console.print()
        action = "[dim](dry-run)[/dim]" if dry_run else ""
        console.print(
            Panel(
                f"Migrating [bold]{len(candidates)}[/bold] secret(s)\n"
                f"  From: [cyan]{from_backend}[/cyan] → To: [cyan]{to_backend}[/cyan] {action}",
                border_style="blue",
                padding=(1, 2),
            )
        )
        console.print()

    for name, value in candidates.items():
        if dry_run:
            results.append({"name": name, "status": "would_migrate"})
            if not json_output:
                console.print(f"  [dim]→[/dim] [cyan]{name}[/cyan]  [dim](dry-run)[/dim]")
        else:
            try:
                _run(dst.set(name, value))
                results.append({"name": name, "status": "migrated"})
                if not json_output:
                    console.print(f"  [green]✓[/green] [cyan]{name}[/cyan]")
            except Exception as exc:
                errors.append({"name": name, "status": "error", "error": str(exc)})
                if not json_output:
                    console.print(f"  [red]✗[/red] [cyan]{name}[/cyan]  [dim]{exc}[/dim]")

    if json_output:
        sys.stdout.write(
            json.dumps(
                {
                    "migrated": len(results),
                    "errors": len(errors),
                    "dry_run": dry_run,
                    "results": results + errors,
                },
                indent=2,
            )
            + "\n"
        )
        return

    console.print()
    if errors:
        console.print(
            f"  [yellow]Done — {len(results)} migrated, {len(errors)} failed.[/yellow]\n"
        )
    elif dry_run:
        console.print(
            f"  [dim]Dry-run complete. {len(results)} secret(s) would be migrated.[/dim]\n"
            f"  Run without [bold]--dry-run[/bold] to apply.\n"
        )
    else:
        console.print(
            f"  [green]✓[/green] {len(results)} secret(s) migrated to [bold]{to_backend}[/bold]\n"
        )
        console.print(
            "  [dim]Next: update agent.yaml to use secret:// references:[/dim]\n"
            "  [dim]  secrets:[/dim]\n"
            + "".join(f"  [dim]    - {n}[/dim]\n" for n in candidates)
        )
