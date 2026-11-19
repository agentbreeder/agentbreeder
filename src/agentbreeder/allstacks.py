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

import functools
import json
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


RUNTIME_REF = re.compile(r"runtime/[a-z0-9][a-z0-9-]{0,62}@[0-9]+\.[0-9]+\.[0-9]+")
RECORD_LIMIT = 1024 * 1024
_PACKAGE_REF = re.compile(r"agent/([a-z0-9][a-z0-9-]{0,62})@([0-9]+\.[0-9]+\.[0-9]+)")


@functools.lru_cache(maxsize=256)
def _json_file(path: Path) -> dict:
    """A project file as a JSON object; {} when absent, unsafe or not one. Read once per
    `rows` call, however many rows name it (`rows` clears the cache, so a long-lived caller
    never sees an old record)."""
    try:
        raw = registry.read_project_file(path, RECORD_LIMIT)
        data = json.loads(raw) if raw else None
    except (registry.RegistryError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _ref(value: object) -> str | None:
    return value if isinstance(value, str) and RUNTIME_REF.fullmatch(value) else None


def runtime_ref(manifest: dict, name: str, package: object, project: Path | None) -> str | None:
    """The runtime version a deployment's image was built on (multi-framework spec §11), from:

    1. the manifest's `runtime.ref` (`build` writes it for a kit runtime);
    2. else, with a project, its build record `deploy/<agent>.build.json` when that record is
       for this image (a `<agent>-candidate` deployment uses `<agent>`'s record);
    3. else, with a project, the agent package the deployment is linked to
       (`registry/agent/<name>/<version>/package.json`), when it is for this image;
    4. else None.

    Only a well-formed `runtime/<name>@X.Y.Z` is returned. The image's own
    `org.agentbreeder.runtime` label is not read: `list` never calls Docker."""
    declared = manifest.get("runtime")
    found = _ref(declared.get("ref")) if isinstance(declared, dict) else None
    image = manifest.get("image")
    if found or project is None or not isinstance(image, str):
        return found
    agent = name.removesuffix("-candidate")
    if registry.AGENT_NAME.fullmatch(agent):
        record = _json_file(project / "deploy" / f"{agent}.build.json")
        if record.get("image") == image and record.get("name") == agent:
            found = _ref(record.get("runtime"))
    linked = _PACKAGE_REF.fullmatch(str(package.get("ref"))) if isinstance(package, dict) else None
    if found is None and linked:
        path = project / "registry" / "agent" / linked[1] / linked[2] / "package.json"
        data = _json_file(path)
        built, about = data.get("image"), data.get("agent")
        if isinstance(built, dict) and built.get("id") == image and isinstance(about, dict):
            found = _ref(about.get("runtime"))
    return found


def _package(text: object) -> dict | None:
    try:
        value = json.loads(text) if isinstance(text, str) and text else None
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def rows(store: Store, project: Path | None = None) -> list[dict]:
    """The rows `list` prints for one state directory. `project` is where a build record or a
    package for the deployments may be (see `runtime_ref`)."""
    _json_file.cache_clear()
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
                "runtime": runtime_ref(
                    manifest, row["name"], _package(row.get("package")), project
                ),
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
    project: Path | None = None,
) -> None:
    """Add the active rows of one state directory (each with `where`), or one `skipped` entry
    saying why not. A row keeps its own `environment` column. `project` is where the rows'
    build records may be."""
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
        found = rows(store, project)
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
        _read_state(
            env_dir / "state",
            where,
            environment,
            deployments,
            skipped,
            Path(project) if project else None,
        )


def collect(default_state: Path, root: Path | None = None) -> dict:
    """Every active deployment in `default_state` and under the stacks root (read-only)."""
    deployments: list[dict] = []
    skipped: list[dict] = []
    if os.path.lexists(default_state / _DB):
        where = {"stack": None, "project": None}
        _read_state(default_state, where, None, deployments, skipped, Path.cwd())
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
