"""agentbreeder login / logout / whoami — authentication commands.

Replaces the previous workflow of "go to Studio, copy a JWT,
``export AGENTBREEDER_API_TOKEN=...``". Tokens now live in the OS keychain;
the env var still wins when set, so CI configurations keep working.

Designed to compose with sibling work:

* ``agentbreeder deploy --remote`` (#416) reads ``get_token()`` from
  :mod:`cli._http` to authenticate POSTs to ``/api/v1/deploys``.
* ``agentbreeder context use <team>`` (this module) sets the active team
  that's surfaced by ``whoami`` and sent on every API call as the
  ``X-AgentBreeder-Team`` header.
"""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from cli import _http

console = Console()

auth_app = typer.Typer(name="auth", help="Log in, log out, and inspect the current user.")


# ── helpers ─────────────────────────────────────────────────────────────────


def _fetch_me(token: str, api_url: str | None = None) -> dict[str, Any]:
    """Call ``GET /api/v1/auth/me`` with the given token.

    Used by both ``login --token`` (to validate the pasted token) and by
    ``whoami`` (to display profile info). Returns the user payload or exits
    with a friendly error on auth failure.
    """
    base = (api_url or _http.api_base()).rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{base}/api/v1/auth/me", headers=headers)
    except httpx.HTTPError as exc:
        console.print(f"[red]Could not reach {base}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if resp.status_code == 401:
        console.print("[red]Invalid or expired token.[/red]")
        raise typer.Exit(code=1)
    if resp.status_code >= 400:
        console.print(f"[red]GET /auth/me -> {resp.status_code}[/red]\n{resp.text}")
        raise typer.Exit(code=1)
    payload = resp.json()
    user = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        console.print(f"[red]Unexpected /auth/me response shape: {payload}[/red]")
        raise typer.Exit(code=1)
    return user


def _store_or_warn(token: str) -> None:
    """Persist ``token`` to keychain; warn but do not fail if unavailable."""
    try:
        _http.set_token(token)
    except RuntimeError as exc:
        console.print(
            f"[yellow]Could not store token in keychain: {exc}[/yellow]\n"
            f"Set [bold]{_http.ENV_TOKEN}[/bold] in your shell instead."
        )


def _force_password_change(
    access_token: str,
    *,
    api_url: str,
    current_password: str | None,
    is_default_admin: bool,
) -> None:
    """Walk the user through a forced password rotation.

    Called when ``must_change_password`` is set on the authenticated user
    (issue #464). Prompts for the new password (twice, hidden), POSTs
    ``/api/v1/auth/change-password`` with the temp access token, and
    returns on success. Exits non-zero on any failure so the caller does
    not persist a token tied to a still-unsafe credential.

    ``current_password`` is the password the user just authenticated with;
    we already know it works, so we pass it as ``old_password`` without
    a second prompt. ``None`` covers the ``--token`` paste path where the
    CLI never saw the underlying password — there we prompt for it.
    """
    console.print()
    if is_default_admin:
        console.print(
            "[bold yellow]⚠  Password change required.[/bold yellow]\n"
            "[dim]You are signed in with the default seeded admin credential.\n"
            "The default password is publicly documented — set a new one before continuing.[/dim]"
        )
    else:
        console.print(
            "[bold yellow]⚠  Password change required.[/bold yellow]\n"
            "[dim]An administrator requires you to rotate your password before continuing.[/dim]"
        )
    console.print()

    if current_password is None:
        current_password = typer.prompt("Current password", hide_input=True)

    while True:
        new_password = typer.prompt("New password (8+ chars)", hide_input=True)
        confirm = typer.prompt("Confirm new password", hide_input=True)
        if new_password != confirm:
            console.print("[red]Passwords do not match. Try again.[/red]")
            continue
        if len(new_password) < 8:
            console.print("[red]Password must be at least 8 characters.[/red]")
            continue
        if new_password == current_password:
            console.print("[red]New password must differ from the old one.[/red]")
            continue
        break

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = {"old_password": current_password, "new_password": new_password}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{api_url}/api/v1/auth/change-password",
                json=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        console.print(f"[red]Could not reach {api_url}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if resp.status_code == 401:
        console.print("[red]Current password was rejected by the server.[/red]")
        raise typer.Exit(code=1)
    if resp.status_code >= 400:
        console.print(
            f"[red]POST /auth/change-password -> {resp.status_code}[/red]\n{resp.text}"
        )
        raise typer.Exit(code=1)

    console.print("[green]Password updated.[/green]")


# ── commands ────────────────────────────────────────────────────────────────


@auth_app.command("login")
def login(
    email: str | None = typer.Option(None, "--email", "-e", help="Account email."),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="Account password (prompted if omitted).",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help="Paste an existing JWT instead of using email/password.",
    ),
    api_url: str | None = typer.Option(
        None, "--api-url", help="Override API URL (defaults to $AGENTBREEDER_URL)."
    ),
) -> None:
    """Authenticate against the AgentBreeder API and store the resulting token.

    Two modes:

    * Interactive: prompts for email/password and POSTs ``/api/v1/auth/login``.
    * ``--token``: validates the pasted JWT against ``/api/v1/auth/me``.

    On success the token is written to the OS keychain. ``AGENTBREEDER_API_TOKEN``
    in the environment still overrides keychain storage at request time, so CI
    can short-circuit this command entirely.
    """
    base = (api_url or _http.api_base()).rstrip("/")

    if token is not None:
        token = token.strip()
        if not token:
            console.print("[red]--token cannot be empty[/red]")
            raise typer.Exit(code=1)
        user = _fetch_me(token, api_url=base)
        if user.get("must_change_password"):
            _force_password_change(
                token,
                api_url=base,
                current_password=None,
                is_default_admin=user.get("email") == "admin@agentbreeder.local",
            )
            user = _fetch_me(token, api_url=base)
        _store_or_warn(token)
        console.print(
            f"[green]Logged in as[/green] [bold]{user.get('email')}[/bold] "
            f"(team={user.get('team')}, role={user.get('role')})"
        )
        return

    if not email:
        email = typer.prompt("Email")
    if not password:
        password = typer.prompt("Password", hide_input=True)

    body = {"email": email, "password": password}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{base}/api/v1/auth/login",
                json=body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        console.print(f"[red]Could not reach {base}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if resp.status_code == 401:
        console.print("[red]Invalid email or password.[/red]")
        raise typer.Exit(code=1)
    if resp.status_code >= 400:
        console.print(f"[red]POST /auth/login -> {resp.status_code}[/red]\n{resp.text}")
        raise typer.Exit(code=1)

    payload = resp.json()
    token_payload = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(token_payload, dict) or not token_payload.get("access_token"):
        console.print(f"[red]Unexpected /auth/login response: {payload}[/red]")
        raise typer.Exit(code=1)
    access_token: str = token_payload["access_token"]
    if token_payload.get("must_change_password"):
        _force_password_change(
            access_token,
            api_url=base,
            current_password=password,
            is_default_admin=email == "admin@agentbreeder.local",
        )
    _store_or_warn(access_token)
    user = _fetch_me(access_token, api_url=base)
    console.print(
        f"[green]Logged in as[/green] [bold]{user.get('email')}[/bold] "
        f"(team={user.get('team')}, role={user.get('role')})"
    )


@auth_app.command("logout")
def logout() -> None:
    """Delete the stored token from the OS keychain."""
    removed = _http.clear_token()
    if removed:
        console.print("[green]Logged out.[/green]")
    else:
        console.print("[yellow]No stored token to remove.[/yellow]")


@auth_app.command("whoami")
def whoami(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Show the authenticated user, role, team, and active team context."""
    token = _http.require_token()
    user = _fetch_me(token)
    active_team = _http.get_active_team()
    if json_output:
        typer.echo(_json.dumps({**user, "active_team": active_team}, default=str, indent=2))
        return

    table = Table(show_header=False, box=None)
    table.add_column("field", style="dim")
    table.add_column("value")
    table.add_row("email", str(user.get("email", "")))
    table.add_row("name", str(user.get("name", "")))
    table.add_row("role", str(user.get("role", "")))
    table.add_row("team (primary)", str(user.get("team", "")))
    table.add_row("active team", active_team or "[dim](unset — using primary)[/dim]")
    table.add_row("id", str(user.get("id", "")))
    console.print(table)
