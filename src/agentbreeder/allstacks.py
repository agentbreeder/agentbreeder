"""`agentbreeder list --all`: every active deployment on this machine, read-only (guided §14.5).

It reads the default state directory (or `--state-dir`) and, under the stacks root
(`~/.agentbreeder/stacks/`, or `AGENTBREEDER_STACKS_DIR`), the deploy state of every environment
each stack wrote, plus the project path `services up` recorded in the stack's meta. Nothing is
created, migrated or locked: `stack.db` and `deployments.sqlite3` are opened with `mode=ro`.

A stack directory is read only if its name has the `<stack name>-<8 hex>` form `services up`
gives it and it, its `envs/` directory and each environment directory are real directories (not
symlinks) owned by you. Anything else, and any store it cannot read (an older or unsupported
schema, an interrupted write, a busy database), is reported in `skipped` with a code and left
alone; the other stacks are still listed. Names that fail validation are never echoed raw.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from pathlib import Path

from agentbreeder import registry
from agentbreeder.safe_text import excerpt
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import (
    PROJECT_KEY,
    StackState,
    stacks_root,
    written_environments,
)
from agentbreeder.store import (
    UNSUPPORTED_SCHEMA,
    DeploymentError,
    StateBusy,
    StateNeedsMigration,
    Store,
)

SCHEMA_VERSION = 1  # the `list --all --json` shape in docs/commands.md
# What `stack_id` produces: a stack name (services.schema `Stack.name`) and 8 hex digits.
STACK_DIRECTORY = re.compile(r"[a-z][a-z0-9-]{0,30}-[0-9a-f]{8}")
_DB = "deployments.sqlite3"


def rows(store: Store) -> list[dict]:
    """The rows `list` prints for one state directory."""
    result = []
    for row in store.active_all():
        manifest = store.manifest(row["id"])
        result.append(
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "name",
                        "state",
                        "endpoint",
                        "environment",
                        "compliance",
                        "error",
                    )
                },
                "target": manifest.get("target", "local"),
                "image": manifest.get("image"),
                "updated": row["updated"],
            }
        )
    return result


def _owned_directory(path: Path) -> bool | None:
    """True for a real directory you own that others cannot write to, None when absent, False
    for anything else (a symlink, another user's, group- or world-writable)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022


def _reason(error: Exception) -> str:
    """An error's message without a `[CODE] ` prefix (the code is its own field)."""
    return str(error).removeprefix(f"[{getattr(error, 'code', '')}] ")


def _shown(name: str) -> str:
    return excerpt(name, 80)


def _read_state(
    directory: Path,
    where: dict,
    environment: str | None,
    deployments: list[dict],
    skipped: list[dict],
) -> None:
    """Add the active rows of one state directory (each with `where`), or one `skipped` entry
    saying why not. A row keeps its own `environment` column."""
    problem = {"stack": where["stack"], "environment": environment}
    if not os.path.lexists(directory / _DB):
        return  # an environment that never deployed anything
    try:
        store = Store.open_readonly(directory)
    except StateNeedsMigration as error:
        skipped.append({**problem, "code": error.code, "reason": _reason(error)})
        return
    except StateBusy as error:
        skipped.append({**problem, "code": error.code, "reason": _reason(error)})
        return
    except DeploymentError as error:
        code = "STATE_UNSUPPORTED" if str(error) == UNSUPPORTED_SCHEMA else "STATE_UNSAFE"
        skipped.append({**problem, "code": code, "reason": _reason(error)})
        return
    except (OSError, sqlite3.Error):
        skipped.append({**problem, "code": "STATE_UNREADABLE", "reason": "cannot read the state"})
        return
    try:
        found = rows(store)
    # A damaged manifest: not JSON (ValueError), or JSON that is not an object.
    except (sqlite3.Error, ValueError, KeyError, AttributeError, TypeError):
        skipped.append({**problem, "code": "STATE_UNREADABLE", "reason": "cannot read the state"})
        return
    finally:
        store.close()
    deployments += [{**row, **where} for row in found]


def _project(directory: Path) -> str | None:
    """The project path `services up` recorded; None for a stack brought up before it did."""
    state = StackState.open_directory(directory)
    if state is None:
        return None
    try:
        value = state.get(PROJECT_KEY)
    finally:
        state.close()
    # Untrusted data to whoever shows or registers it (they re-check it, as registration does):
    # only an absolute, normalised path with no control, bidi or invisible characters is passed on.
    if (
        isinstance(value, str)
        and os.path.isabs(value)
        and os.path.normpath(value) == value
        and registry.clean(value, name=True)[0] == value
    ):
        return value
    return None


def _stack(directory: Path, deployments: list[dict], skipped: list[dict]) -> None:
    name = directory.name
    base = {"stack": name, "environment": None}
    if not _owned_directory(directory):
        skipped.append(
            {
                **base,
                "code": "STACK_UNSAFE",
                "reason": "not a directory you own that others cannot write to (or a symlink)",
            }
        )
        return
    try:
        project = _project(directory)
    except ServiceError as error:
        skipped.append({**base, "code": error.code, "reason": _reason(error)})
        return
    except (OSError, sqlite3.Error):
        skipped.append({**base, "code": "STACK_UNREADABLE", "reason": "cannot read stack.db"})
        return
    envs = directory / "envs"
    owned = _owned_directory(envs)
    if owned is None:
        return
    if not owned:
        skipped.append(
            {**base, "code": "STACK_UNSAFE", "reason": "envs/ is not a directory you own"}
        )
        return
    try:
        environments = written_environments(directory)
    except OSError:
        skipped.append({**base, "code": "STACK_UNREADABLE", "reason": "cannot list envs/"})
        return
    for environment in environments:
        where = {"stack": name, "project": project}  # merged into rows: no environment
        env_dir = envs / environment
        if not _owned_directory(env_dir):
            skipped.append(
                {
                    "stack": name,
                    "environment": environment,
                    "code": "STACK_UNSAFE",
                    "reason": "not a directory you own",
                }
            )
            continue
        _read_state(env_dir / "state", where, environment, deployments, skipped)


def collect(default_state: Path, root: Path | None = None) -> dict:
    """Every active deployment in `default_state` and under the stacks root (read-only)."""
    deployments: list[dict] = []
    skipped: list[dict] = []
    if os.path.lexists(default_state / _DB):
        _read_state(default_state, {"stack": None, "project": None}, None, deployments, skipped)
    base = root or stacks_root()
    owned = _owned_directory(base)
    if owned is False:
        skipped.append(
            {
                "stack": None,
                "environment": None,
                "code": "STACKS_ROOT_UNSAFE",
                "reason": "the stacks directory is not a directory you own (or a symlink)",
            }
        )
    elif owned:
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            entries = []
            skipped.append(
                {
                    "stack": None,
                    "environment": None,
                    "code": "STACKS_ROOT_UNREADABLE",
                    "reason": "cannot list it",
                }
            )
        for entry in entries:
            if entry.startswith("."):
                continue
            if not STACK_DIRECTORY.fullmatch(entry):
                skipped.append(
                    {
                        "stack": _shown(entry),
                        "environment": None,
                        "code": "STACK_NAME_INVALID",
                        "reason": "not a stack directory name (<stack>-<8 hex>)",
                    }
                )
                continue
            _stack(base / entry, deployments, skipped)
    return {"schema_version": SCHEMA_VERSION, "deployments": deployments, "skipped": skipped}
