"""Shared HTTP client + token storage for the AgentBreeder CLI.

Single source of truth for:

* Resolving the API base URL (``AGENTBREEDER_URL`` → ``AGENTBREEDER_API_URL`` →
  ``http://localhost:8000``).
* Reading the bearer token from the OS keychain or the
  ``AGENTBREEDER_API_TOKEN`` env var (env wins, for CI use).
* Reading and writing the active team context from
  ``~/.agentbreeder/context.json``.
* Producing authenticated ``httpx`` clients (sync and async).
* A ``request()`` convenience that replaces the per-file ``_post`` / ``_get`` /
  ``_request`` helpers in ``cli/commands/registry_cmd.py`` and
  ``cli/commands/model.py``.

CLI commands should depend on this module instead of reading the env vars
directly so that token storage can evolve in one place.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import typer
from rich.console import Console

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

__all__ = [
    "ENV_TOKEN",
    "ENV_URL",
    "ENV_URL_LEGACY",
    "api_base",
    "auth_headers",
    "authenticated_client",
    "authenticated_sync_client",
    "clear_active_team",
    "clear_token",
    "context_path",
    "get_active_team",
    "get_token",
    "request",
    "require_token",
    "set_active_team",
    "set_token",
]

console = Console()

ENV_TOKEN = "AGENTBREEDER_API_TOKEN"
ENV_URL = "AGENTBREEDER_URL"
ENV_URL_LEGACY = "AGENTBREEDER_API_URL"

_KEYRING_SERVICE = "agentbreeder"
_KEYRING_USERNAME = "api_token"
_CONTEXT_DIR_ENV = "AGENTBREEDER_HOME"
_DEFAULT_API_URL = "http://localhost:8000"


# ── URL ──────────────────────────────────────────────────────────────────────


def api_base() -> str:
    """Return the API base URL with no trailing slash.

    Honors ``AGENTBREEDER_URL`` first (the documented variable), then the
    legacy ``AGENTBREEDER_API_URL`` for back-compat with v2.3 callers.
    """
    raw = os.getenv(ENV_URL) or os.getenv(ENV_URL_LEGACY) or _DEFAULT_API_URL
    return raw.rstrip("/")


# ── Keychain helpers ─────────────────────────────────────────────────────────


def _try_keyring() -> Any | None:
    """Return the ``keyring`` module if usable, else ``None``.

    Headless environments (Docker, CI) commonly have no backend; we degrade to
    env-var-only token storage silently.
    """
    try:
        import keyring  # noqa: PLC0415 — optional, only used when present

        keyring.get_keyring()  # raises NoKeyringError if no backend installed
    except Exception:
        return None
    return keyring


def get_token() -> str | None:
    """Return the bearer token, or ``None`` if none is configured.

    Priority: env var ``AGENTBREEDER_API_TOKEN`` (so CI can always override) →
    OS keychain.
    """
    env = os.getenv(ENV_TOKEN, "").strip()
    if env:
        return env
    kr = _try_keyring()
    if kr is None:
        return None
    try:
        value = kr.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception:
        return None
    if not value:
        return None
    return str(value)


def set_token(token: str) -> None:
    """Store ``token`` in the OS keychain. Raises ``RuntimeError`` if unavailable."""
    if not token.strip():
        raise ValueError("token must not be empty")
    kr = _try_keyring()
    if kr is None:
        raise RuntimeError(
            "No OS keychain backend available. Install one (e.g. macOS Keychain, "
            "GNOME libsecret) or export AGENTBREEDER_API_TOKEN instead."
        )
    kr.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, token.strip())


def clear_token() -> bool:
    """Delete the stored token. Returns ``True`` if one was removed."""
    kr = _try_keyring()
    if kr is None:
        return False
    try:
        existing = kr.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if not existing:
            return False
        kr.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception:
        return False
    return True


def require_token() -> str:
    """Return the bearer token or exit with a friendly error if none is set."""
    token = get_token()
    if not token:
        console.print(
            f"[red]Not logged in.[/red] Run [bold]agentbreeder login[/bold] or set ${ENV_TOKEN}."
        )
        raise typer.Exit(code=1)
    return token


# ── Active team context ──────────────────────────────────────────────────────


def context_path() -> Path:
    """Return the path to the CLI context file (``~/.agentbreeder/context.json``)."""
    base = os.getenv(_CONTEXT_DIR_ENV)
    root = Path(base) if base else Path.home() / ".agentbreeder"
    return root / "context.json"


def _read_context() -> dict[str, Any]:
    path = context_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _write_context(data: dict[str, Any]) -> None:
    path = context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def get_active_team() -> str | None:
    """Return the active team slug, or ``None`` if none is set."""
    return _read_context().get("team")


def set_active_team(team: str) -> None:
    if not team.strip():
        raise ValueError("team must not be empty")
    data = _read_context()
    data["team"] = team.strip()
    _write_context(data)


def clear_active_team() -> None:
    data = _read_context()
    data.pop("team", None)
    _write_context(data)


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def auth_headers(*, json_body: bool = True) -> dict[str, str]:
    """Return headers with bearer token. Exits if no token is set."""
    token = require_token()
    headers = {"Authorization": f"Bearer {token}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    team = get_active_team()
    if team:
        headers["X-AgentBreeder-Team"] = team
    return headers


@contextmanager
def authenticated_sync_client(*, timeout: float = 30.0) -> Iterator[httpx.Client]:
    """Yield a sync ``httpx.Client`` with bearer + base URL pre-wired."""
    headers = auth_headers()
    with httpx.Client(base_url=api_base(), headers=headers, timeout=timeout) as client:
        yield client


@asynccontextmanager
async def authenticated_client(*, timeout: float = 30.0) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an async ``httpx.AsyncClient`` with bearer + base URL pre-wired.

    Use when streaming SSE or running concurrent requests (e.g. ``deploy
    --remote``). For one-shot sync calls, prefer :func:`request`.
    """
    headers = auth_headers()
    async with httpx.AsyncClient(base_url=api_base(), headers=headers, timeout=timeout) as client:
        yield client


def _handle_error(resp: httpx.Response, method: str, path: str) -> None:
    if resp.status_code < 400:
        return
    if resp.status_code == 401:
        console.print(
            f"[red]{method} {path} -> 401 Unauthorized[/red] — "
            "your token has expired or is invalid. "
            "Run [bold]agentbreeder login[/bold]."
        )
    else:
        console.print(f"[red]{method} {path} -> {resp.status_code}[/red]\n{resp.text}")
    raise typer.Exit(code=1)


def request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    data: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Sync convenience for one-shot authenticated requests.

    Returns the parsed JSON body on success; exits the CLI with a friendly
    error message on any non-2xx response. Supports both JSON bodies (via
    ``body=``) and multipart uploads (via ``files=``).
    """
    method_up = method.upper()
    url = f"{api_base()}{path}"
    if files is not None:
        headers = auth_headers(json_body=False)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, files=files, data=data or {})
    else:
        headers = auth_headers()
        with httpx.Client(timeout=timeout) as client:
            if method_up == "GET":
                resp = client.get(url, headers=headers)
            elif method_up == "POST":
                resp = client.post(url, headers=headers, json=body or {})
            elif method_up == "PUT":
                resp = client.put(url, headers=headers, json=body or {})
            elif method_up == "DELETE":
                resp = client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")
    _handle_error(resp, method_up, path)
    try:
        parsed = resp.json()
    except ValueError:
        return {"data": resp.text}
    if isinstance(parsed, dict):
        return parsed
    return {"data": parsed}
