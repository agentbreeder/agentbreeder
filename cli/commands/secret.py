"""agentbreeder secret — manage secrets across backends.

Subcommands:
    agentbreeder secret list   [--backend env|keychain|aws|gcp|vault] [--workspace NAME]
    agentbreeder secret set    <name> [--workspace NAME]
    agentbreeder secret get    <name> [--reveal] [--workspace NAME]
    agentbreeder secret delete <name> [--backend ...]
    agentbreeder secret rotate <name> [--workspace NAME]
    agentbreeder secret sync   --target {aws,gcp,vault} [--workspace NAME] [--include KEY ...]
    agentbreeder secret migrate --from env --to aws|gcp|vault [--prefix PREFIX] [--dry-run]

When ``--backend``/``--workspace`` are omitted the workspace's configured
backend (``~/.agentbreeder/workspace.yaml`` or the install-mode default) is
used. The local CLI default is the OS keychain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Coroutine
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from engine.secrets.base import SecretsBackend
from engine.secrets.factory import (
    SUPPORTED_BACKENDS,
    get_backend,
)

logger = logging.getLogger(__name__)
console = Console()

# Backends accepted as ``--backend`` overrides on commands that allow it.
VALID_BACKENDS = list(SUPPORTED_BACKENDS)

# Backends accepted as ``--target`` for ``sync``.
SYNC_TARGETS = ("aws", "gcp", "vault")


secret_app = typer.Typer(
    name="secret",
    help="Manage secrets across backends (env, keychain, AWS, GCP, Vault).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _validate_backend(backend: str) -> None:
    if backend not in VALID_BACKENDS:
        choices = ", ".join(VALID_BACKENDS)
        console.print(f"[red]Unknown backend '{backend}'. Choose from: {choices}[/red]")
        raise typer.Exit(code=2)


def _normalise_kwargs(backend: str, **kwargs: Any) -> dict[str, Any]:
    """Strip cloud-specific kwargs the env/keychain backends don't accept."""
    out = dict(kwargs)
    if backend == "gcp" and "prefix" in out:
        # GCP secret IDs cannot contain slashes — replace with underscore.
        out["prefix"] = out["prefix"].replace("/", "_")
    if backend in ("env", "keychain"):
        out.pop("prefix", None)
    return out


def _get_backend(backend: str, **kwargs: Any) -> SecretsBackend:
    _validate_backend(backend)
    try:
        return get_backend(backend, **_normalise_kwargs(backend, **kwargs))
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except (ValueError, PermissionError) as exc:
        console.print(f"[red]Backend init failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


# Alias kept so K's own test_secrets_keychain.py (which patches _make_backend)
# continues to work alongside the older fixtures that patch _get_backend.
_make_backend = _get_backend


def _resolve_backend(
    explicit_backend: str | None,
    *,
    workspace: str | None = None,
    prefix: str | None = None,
) -> tuple[SecretsBackend, str]:
    """Return ``(backend, workspace_name)`` honouring CLI overrides.

    * If ``explicit_backend`` is given, build that backend directly using
      kwargs supplied by the caller (``prefix`` / ``workspace``).
    * Otherwise consult the workspace config (which itself falls back to a
      sensible default).
    """
    if explicit_backend:
        kwargs: dict[str, Any] = {}
        if prefix is not None:
            kwargs["prefix"] = prefix
        if explicit_backend == "keychain" and workspace is not None:
            kwargs["workspace"] = workspace
        backend = _get_backend(explicit_backend, **kwargs)
        return backend, workspace or "default"

    # Workspace path also routes through _get_backend so test mocks that
    # patch cli.commands.secret._get_backend intercept this code path too.
    try:
        from engine.secrets.workspace import load_workspace_secrets_config

        ws = load_workspace_secrets_config(workspace=workspace)
        backend_kwargs: dict[str, Any] = dict(ws.options)
        if ws.backend == "keychain" and "workspace" not in backend_kwargs:
            backend_kwargs["workspace"] = ws.workspace
        if prefix is not None and ws.backend in ("aws", "gcp", "vault"):
            backend_kwargs.setdefault("prefix", prefix)
        backend = _get_backend(ws.backend, **backend_kwargs)
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except (ValueError, PermissionError) as exc:
        console.print(f"[red]Backend init failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    return backend, ws.workspace


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _emit_audit(
    *,
    action: str,
    secret_name: str,
    backend_name: str,
    workspace: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit event emit — non-fatal in offline use.

    Tries the in-process AuditService first (when the CLI is invoked inside
    the API process), then falls back to a structured logger.warning.
    """
    actor = os.environ.get("AGENTBREEDER_USER") or os.environ.get("USER") or "cli"
    details = {
        "secret_name": secret_name,
        "backend": backend_name,
        "workspace": workspace,
        **(extra or {}),
    }
    try:
        from api.services.audit_service import AuditService

        asyncio.run(
            AuditService.log_event(
                actor=actor,
                action=action,
                resource_type="secret",
                resource_name=secret_name,
                details=details,
            )
        )
    except Exception:  # pragma: no cover - api package may be unavailable in CLI
        logger.warning(
            "audit_event",
            extra={
                "audit_action": action,
                "actor": actor,
                "details": details,
            },
        )


# ── list ────────────────────────────────────────────────────────────────────


@secret_app.command(name="list")
def secret_list(
    backend: str | None = typer.Option(
        None, "--backend", "-b", help=f"Backend override: {', '.join(VALID_BACKENDS)}"
    ),
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Workspace name (default: workspace config)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    prefix: str = typer.Option("agentbreeder/", "--prefix", help="Prefix (AWS/GCP/Vault)"),
) -> None:
    """List secrets in the configured backend (names only — values are masked)."""
    b, ws = _resolve_backend(backend, workspace=workspace, prefix=prefix)
    entries = _run(b.list())

    if json_output:
        sys.stdout.write(
            json.dumps(
                {
                    "workspace": ws,
                    "backend": b.backend_name,
                    "entries": [e.to_dict() for e in entries],
                },
                indent=2,
            )
            + "\n"
        )
        return

    if not entries:
        console.print(f"\n  [dim]No secrets found in '{b.backend_name}' backend.[/dim]\n")
        return

    table = Table(title=f"Secrets — {b.backend_name} (workspace: {ws})")
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
    value: str | None = typer.Option(
        None, "--value", "-v", help="Value (prompted securely if omitted; do not echo)"
    ),
    backend: str | None = typer.Option(
        None, "--backend", "-b", help=f"Backend override: {', '.join(VALID_BACKENDS)}"
    ),
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Workspace name (default: workspace config)"
    ),
    prefix: str = typer.Option("agentbreeder/", "--prefix", help="Prefix (AWS/GCP/Vault)"),
    tag: list[str] = typer.Option([], "--tag", "-t", help="key=value tags (cloud backends)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Create or update a secret in the workspace backend.

    The value is read securely via ``getpass`` when not supplied via ``--value``;
    the prompt never echoes input to the terminal.
    """
    resolved_value = value
    if not resolved_value:
        # ``hide_input=True`` uses getpass under the hood — no echoing.
        resolved_value = typer.prompt(f"Value for '{name}'", hide_input=True)

    tags: dict[str, str] = {}
    for t in tag:
        if "=" in t:
            k, _, v = t.partition("=")
            tags[k.strip()] = v.strip()

    b, ws = _resolve_backend(backend, workspace=workspace, prefix=prefix)
    is_new = _run(b.get(name)) is None
    _run(b.set(name, resolved_value, tags=tags or None))

    _emit_audit(
        action="secret.created" if is_new else "secret.rotated",
        secret_name=name,
        backend_name=b.backend_name,
        workspace=ws,
    )

    if json_output:
        sys.stdout.write(
            json.dumps(
                {
                    "name": name,
                    "backend": b.backend_name,
                    "workspace": ws,
                    # Keep "status: ok" for backward compat with downstream
                    # tooling; "operation" surfaces the new/updated detail.
                    "status": "ok",
                    "operation": "created" if is_new else "updated",
                }
            )
            + "\n"
        )
        return

    console.print(
        f"\n  [green]✓[/green] Secret [bold]{name}[/bold] saved to "
        f"[bold]{b.backend_name}[/bold] (workspace: {ws})\n"
        f"  Reference in agent.yaml: [dim]secret://{name}[/dim]\n"
    )


# ── get ─────────────────────────────────────────────────────────────────────


@secret_app.command(name="get")
def secret_get(
    name: str = typer.Argument(..., help="Secret name"),
    backend: str | None = typer.Option(
        None, "--backend", "-b", help=f"Backend override: {', '.join(VALID_BACKENDS)}"
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace name"),
    prefix: str = typer.Option("agentbreeder/", "--prefix", help="Prefix (AWS/GCP/Vault)"),
    reveal: bool = typer.Option(False, "--reveal", help="Print the actual value (use with care)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Get a secret value (masked by default)."""
    b, _ = _resolve_backend(backend, workspace=workspace, prefix=prefix)
    value = _run(b.get(name))

    if value is None:
        console.print(f"\n  [red]Secret '{name}' not found in '{b.backend_name}' backend.[/red]\n")
        raise typer.Exit(code=1)

    masked = value if reveal else (f"••••{value[-4:]}" if len(value) > 4 else "••••")

    if json_output:
        out: dict[str, Any] = {"name": name, "backend": b.backend_name}
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
    backend: str | None = typer.Option(
        None, "--backend", "-b", help=f"Backend override: {', '.join(VALID_BACKENDS)}"
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace name"),
    prefix: str = typer.Option("agentbreeder/", "--prefix", help="Prefix (AWS/GCP/Vault)"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Delete a secret."""
    b, ws = _resolve_backend(backend, workspace=workspace, prefix=prefix)

    if not force and not json_output:
        confirm = typer.confirm(f"Delete secret '{name}' from '{b.backend_name}'?", default=False)
        if not confirm:
            console.print("  [dim]Cancelled.[/dim]")
            raise typer.Exit(code=0)

    try:
        _run(b.delete(name))
    except KeyError as exc:
        console.print(f"\n  [red]{exc}[/red]\n")
        raise typer.Exit(code=1) from exc

    _emit_audit(
        action="secret.deleted",
        secret_name=name,
        backend_name=b.backend_name,
        workspace=ws,
    )

    if json_output:
        sys.stdout.write(
            json.dumps({"name": name, "backend": b.backend_name, "deleted": True}) + "\n"
        )
        return

    console.print(
        f"\n  [green]✓[/green] Secret [bold]{name}[/bold] deleted from "
        f"[bold]{b.backend_name}[/bold]\n"
    )


# ── rotate ───────────────────────────────────────────────────────────────────


@secret_app.command(name="rotate")
def secret_rotate(
    name: str = typer.Argument(..., help="Secret name to rotate"),
    new_value: str | None = typer.Option(
        None, "--value", "-v", help="New value (prompted securely if omitted)"
    ),
    backend: str | None = typer.Option(
        None, "--backend", "-b", help=f"Backend override: {', '.join(VALID_BACKENDS)}"
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace name"),
    prefix: str = typer.Option("agentbreeder/", "--prefix", help="Prefix (AWS/GCP/Vault)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Rotate a secret to a new value.

    Most LLM provider keys cannot be regenerated programmatically — paste the
    rotated value when prompted. The new value is never echoed.
    """
    resolved_value = new_value
    if not resolved_value:
        resolved_value = typer.prompt(
            f"New value for '{name}'", hide_input=True, confirmation_prompt=True
        )

    b, ws = _resolve_backend(backend, workspace=workspace, prefix=prefix)
    try:
        _run(b.rotate(name, resolved_value))
    except KeyError as exc:
        console.print(f"\n  [red]{exc}[/red]\n")
        raise typer.Exit(code=1) from exc

    _emit_audit(
        action="secret.rotated",
        secret_name=name,
        backend_name=b.backend_name,
        workspace=ws,
    )

    if json_output:
        sys.stdout.write(
            json.dumps(
                {
                    "name": name,
                    "backend": b.backend_name,
                    "workspace": ws,
                    "rotated": True,
                }
            )
            + "\n"
        )
        return

    console.print(
        f"\n  [green]✓[/green] Secret [bold]{name}[/bold] rotated in "
        f"[bold]{b.backend_name}[/bold]\n"
        "  [dim]Agents using this secret will pick up the new value on next request.[/dim]\n"
    )


# ── sync (workspace → cloud auto-mirror) ─────────────────────────────────────


@secret_app.command(name="sync")
def secret_sync(
    target: str = typer.Option(..., "--target", help=f"Target: {', '.join(SYNC_TARGETS)}"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace name"),
    include: list[str] = typer.Option(
        [], "--include", "-i", help="Restrict to specific secret names"
    ),
    exclude: list[str] = typer.Option([], "--exclude", "-e", help="Skip these secret names"),
    prefix: str = typer.Option(
        "agentbreeder/", "--prefix", help="Prefix in target backend (deterministic name)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Mirror workspace secrets to a cloud secrets manager.

    This is the same operation that runs automatically inside ``agentbreeder
    deploy`` when an agent declares ``deploy.secrets`` and targets ``aws``/
    ``gcp``. Run it manually to pre-populate a new environment.
    """
    if target not in SYNC_TARGETS:
        console.print(
            f"[red]Unknown target '{target}'. Choose from: {', '.join(SYNC_TARGETS)}[/red]"
        )
        raise typer.Exit(code=2)

    src, ws = _resolve_backend(None, workspace=workspace)
    dst = _get_backend(target, prefix=prefix)

    entries = _run(src.list())
    candidates: dict[str, str] = {}
    for entry in entries:
        if include and entry.name not in include:
            continue
        if exclude and entry.name in exclude:
            continue
        value = _run(src.get(entry.name))
        if value is None:
            continue
        candidates[entry.name] = value

    if not candidates:
        console.print(
            "\n  [yellow]No secrets to sync (workspace empty or filters excluded all).[/yellow]\n"
        )
        return

    if not json_output:
        console.print()
        action = "[dim](dry-run)[/dim]" if dry_run else ""
        console.print(
            Panel(
                f"Mirroring [bold]{len(candidates)}[/bold] secret(s)\n"
                f"  Workspace: [cyan]{ws}[/cyan] ({src.backend_name})\n"
                f"  → Target:  [cyan]{target}[/cyan]  prefix=[dim]{prefix}[/dim] {action}",
                border_style="blue",
                padding=(1, 2),
            )
        )
        console.print()

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for name, value in candidates.items():
        if dry_run:
            results.append({"name": name, "status": "would_mirror"})
            if not json_output:
                console.print(f"  [dim]→[/dim] [cyan]{name}[/cyan]  [dim](dry-run)[/dim]")
            continue
        try:
            _run(
                dst.set(
                    name,
                    value,
                    tags={"managed-by": "agentbreeder", "workspace": ws},
                )
            )
            results.append({"name": name, "status": "mirrored"})
            _emit_audit(
                action="secret.mirrored",
                secret_name=name,
                backend_name=target,
                workspace=ws,
                extra={"source_backend": src.backend_name, "prefix": prefix},
            )
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
                    "workspace": ws,
                    "source": src.backend_name,
                    "target": target,
                    "dry_run": dry_run,
                    "mirrored": len(results),
                    "errors": len(errors),
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
            f"  [yellow]Done — {len(results)} mirrored, {len(errors)} failed.[/yellow]\n"
        )
    elif dry_run:
        console.print(
            f"  [dim]Dry-run complete. {len(results)} secret(s) would be mirrored.[/dim]\n"
            f"  Run without [bold]--dry-run[/bold] to apply.\n"
        )
    else:
        console.print(
            f"  [green]✓[/green] {len(results)} secret(s) mirrored to [bold]{target}[/bold]\n"
        )


# ── migrate ──────────────────────────────────────────────────────────────────


@secret_app.command(name="migrate")
def secret_migrate(
    from_backend: str = typer.Option(
        ..., "--from", help=f"Source backend ({', '.join(VALID_BACKENDS)})"
    ),
    to_backend: str = typer.Option(..., "--to", help="Target backend (aws, gcp, vault)"),
    prefix: str = typer.Option(
        "agentbreeder/", "--prefix", help="Prefix for secrets in cloud backend"
    ),
    include: list[str] = typer.Option([], "--include", "-i", help="Only migrate these keys"),
    exclude: list[str] = typer.Option([], "--exclude", "-e", help="Skip these keys"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Migrate secrets from one backend to another.

    Example — move all .env API keys to AWS Secrets Manager:

        agentbreeder secret migrate --from env --to aws --prefix agentbreeder/ --dry-run
        agentbreeder secret migrate --from env --to aws --prefix agentbreeder/

    After migration, update agent.yaml to use secret:// references:

        deploy:
          secrets:
            - OPENAI_API_KEY    # resolved from secret://OPENAI_API_KEY at deploy time
    """
    if from_backend == to_backend:
        console.print("[red]Source and target backends must be different.[/red]")
        raise typer.Exit(code=2)

    src = _get_backend(from_backend)
    dst = _get_backend(to_backend, prefix=prefix)

    if from_backend == "env":
        from engine.secrets.env_backend import EnvBackend

        assert isinstance(src, EnvBackend)
        candidates = dict(src.list_raw().items())
    else:
        entries = _run(src.list())
        candidates = {}
        for e in entries:
            val = _run(src.get(e.name))
            if val is not None:
                candidates[e.name] = val

    if include:
        candidates = {k: v for k, v in candidates.items() if k in include}
    if exclude:
        candidates = {k: v for k, v in candidates.items() if k not in exclude}

    if not candidates:
        console.print("\n  [yellow]No secrets found to migrate.[/yellow]\n")
        return

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

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
            "  [dim]  secrets:[/dim]\n" + "".join(f"  [dim]    - {n}[/dim]\n" for n in candidates)
        )
