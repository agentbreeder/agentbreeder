"""`registry used-by`, deployed part: which recorded deployments run an image built with a ref.

Kept out of `registry.py` on purpose: its views are pure reads the console calls in-process,
while this one reads local deploy state. It only reads. A state database must be a private
file you own in a private directory you own (as `store.py` requires), in rollback-journal mode
(a WAL database is refused: reading it would create `-wal`/`-shm` files); it is opened with
SQLite's `mode=ro` and `query_only`, the `deployments` table must be a real table (not a view),
rows and the manifest column are capped, and a progress handler aborts a query that runs too
long. The build record `<project>/deploy/<agent>.build.json` (written by `build` and `release`)
is read as a regular, non-symlink, size-capped file.

Buckets, for deployments of the project's agent:
- deployed: state `ready`, its image is the build record's image, the record lists the ref, and
  the record is for the current `agent.yaml` and `agent.lock` (their hashes match);
- recorded_not_running: the same match, in another state (pending, starting, failed,
  removing);
- deployed_undetermined: anything that cannot be matched (no or unreadable build record, the
  record is stale or for another image, or it does not list the ref), with a `reason`.
`<agent>-candidate` deployments (release's candidates) count only when they run the record's
image, so an unrelated agent of that name is never reported. States are the last recorded
observation, not a live check (`status` and `reconcile` re-check). Only agent images are
checked: a `service` ref used by a stack is never "deployed" here.
An `agent/` ref (a package, M13 #763) is matched through the store v4 `package` column that
`deploy agent/…` and `release` write, not through a build record.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

from agentbreeder.registry import (
    AGENT_NAME,
    SHA256,
    STACK_NAME,
    Ref,
    RegistryError,
    clean,
    copy_and_hash,
    read_project_file,
)
from agentbreeder.services.state import stack_id, stacks_root

BUILD_RECORD_LIMIT = 1024 * 1024
STATE_ROW_LIMIT = 1000
MANIFEST_COLUMN_LIMIT = 256 * 1024
COLUMN_LIMIT = 512  # id, name, state, environment, endpoint: display values
QUERY_SECONDS = 5.0
PROGRESS_STEPS = 1000  # SQLite VM steps between deadline checks
# store.py schema versions this reader understands: v2 added the `environment` column (with
# the gate columns), v3 the held_calls table, v4 the `package` column (M13 #763). A v1 state has
# no `environment`; any command that opens it read-write (e.g. `status`) migrates it.
_STATE_SCHEMAS = (2, 3, 4)
_PACKAGE_SCHEMA = 4
_SQLITE_HEADER = b"SQLite format 3\x00"
RUNNING = "ready"  # the state store.py records for a healthy, started deployment


def _shown(value) -> str:
    return clean(str(value), name=True)[0]


def state_dirs(project: Path, stack: str | None, extra: list[Path]) -> list[Path]:
    """`extra` (the --state-dir values) plus, for a project with a `stack.yaml`, the deploy
    state of every environment `services up` wrote for it (what `--stack DIR` uses), each once
    by resolved path."""
    found = list(extra)
    if stack is not None and STACK_NAME.fullmatch(stack):
        envs = stacks_root() / stack_id(stack, project) / "envs"
        if envs.is_dir() and not envs.is_symlink():
            found += [env / "state" for env in sorted(envs.iterdir()) if env.is_dir()]
    unique: dict[Path, Path] = {}
    for directory in found:
        unique.setdefault(directory.resolve(), directory)
    return list(unique.values())


def _check_private(path: Path, info: os.stat_result, kind) -> None:
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RegistryError(
            "STATE_UNSAFE", f"{_shown(path)} must be a private (0700/0600) path you own"
        )


def _open_checked(path: Path) -> tuple[int, os.stat_result]:
    """An O_NOFOLLOW descriptor on the state database and its stat, after the ownership,
    mode, header and journal-mode checks."""
    info = os.lstat(path)
    _check_private(path, info, stat.S_ISREG)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        now = os.fstat(descriptor)
        if (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
            raise RegistryError("STATE_UNSAFE", f"{_shown(path)} changed while opening")
        header = os.pread(descriptor, 100, 0)
        if header and not header.startswith(_SQLITE_HEADER):
            raise RegistryError("STATE_UNREADABLE", f"{_shown(path)} is not a state database")
        if len(header) >= 20 and 2 in (header[18], header[19]):
            raise RegistryError(
                "STATE_UNSUPPORTED",
                f"{_shown(path)} is in WAL mode; AgentBreeder state uses a rollback journal",
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, now


def _query(path: Path, checked: os.stat_result) -> list[tuple]:
    """Up to STATE_ROW_LIMIT + 1 non-removed rows, newest first, every column capped."""
    deadline = time.monotonic() + QUERY_SECONDS
    try:
        db = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as error:
        raise RegistryError("STATE_UNREADABLE", f"cannot read {_shown(path)}") from error
    try:
        db.set_progress_handler(lambda: time.monotonic() > deadline, PROGRESS_STEPS)
        db.execute("PRAGMA query_only=ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in _STATE_SCHEMAS:
            raise RegistryError("STATE_UNREADABLE", f"{_shown(path)}: unsupported state schema")
        # The file SQLite opened by name must still be the one that was checked.
        after = os.lstat(path)
        if (after.st_dev, after.st_ino) != (checked.st_dev, checked.st_ino):
            raise RegistryError("STATE_UNSAFE", f"{_shown(path)} changed while reading")
        kind = db.execute("SELECT type FROM sqlite_master WHERE name = 'deployments'").fetchone()
        if kind is None:
            return []
        if kind[0] != "table":
            raise RegistryError("STATE_UNREADABLE", f"{_shown(path)}: deployments is not a table")
        cap = COLUMN_LIMIT
        # Only v4 has the column; a capped value that is cut short fails to parse in `_row`.
        package = "substr(package, 1, ?)" if version >= _PACKAGE_SCHEMA else "substr(NULL, 1, ?)"
        return db.execute(
            "SELECT substr(id, 1, ?), substr(name, 1, ?), "
            "CASE WHEN length(manifest) <= ? THEN manifest END, length(manifest) > ?, "
            "substr(state, 1, ?), substr(environment, 1, ?), substr(endpoint, 1, ?), "
            f"{package} "
            "FROM deployments WHERE state != 'removed' ORDER BY created DESC LIMIT ?",
            (cap, cap, MANIFEST_COLUMN_LIMIT, MANIFEST_COLUMN_LIMIT, cap, cap, cap, cap)
            + (STATE_ROW_LIMIT + 1,),
        ).fetchall()
    except sqlite3.Error as error:
        raise RegistryError("STATE_UNREADABLE", f"cannot read {_shown(path)}") from error
    finally:
        db.close()


def _package(value) -> dict | None:
    """The `package` column as {ref, sha256}, or None when unset or not well formed."""
    try:
        data = json.loads(value) if isinstance(value, str) else None
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    ref, digest = data.get("ref"), data.get("sha256")
    if not (isinstance(ref, str) and isinstance(digest, str) and SHA256.fullmatch(digest)):
        return None
    try:
        return {"ref": str(Ref.parse(ref)), "sha256": digest}
    except RegistryError:
        return None


def _row(state_dir: Path, values: tuple) -> dict:
    identifier, name, manifest, oversized, state, environment, endpoint, package = values
    try:
        data = json.loads(manifest) if isinstance(manifest, str) else {}
    except (ValueError, RecursionError):
        data = {}
    data = data if isinstance(data, dict) else {}
    image, target = data.get("image"), data.get("target", "local")
    return {
        "deployment": _shown(identifier),
        "name": _shown(name),
        "state": _shown(state),
        "target": _shown(target),
        "environment": _shown(environment) if environment else None,
        "endpoint": _shown(endpoint) if endpoint else None,
        "image": _shown(image) if isinstance(image, str) else None,
        "state_dir": _shown(state_dir.resolve()),
        "_manifest_over_cap": bool(oversized),
        "_package": _package(package),
    }


def read_state(state_dir: Path) -> tuple[list[dict], bool]:
    """(deployments not removed, oldest first; whether there were more than STATE_ROW_LIMIT,
    in which case only the newest are returned). Read-only; a missing state is empty. Entries
    carry `_manifest_over_cap` for `deployed`."""
    try:
        directory = os.lstat(state_dir)
    except FileNotFoundError:
        return [], False
    except OSError as error:
        raise RegistryError("STATE_UNREADABLE", f"cannot read {_shown(state_dir)}") from error
    _check_private(state_dir, directory, stat.S_ISDIR)
    path = state_dir / "deployments.sqlite3"
    try:
        descriptor, info = _open_checked(path)
    except FileNotFoundError:
        return [], False
    except OSError as error:
        raise RegistryError("STATE_UNREADABLE", f"cannot read {_shown(path)}") from error
    try:
        rows = [] if info.st_size == 0 else _query(path, info)
    finally:
        os.close(descriptor)
    truncated = len(rows) > STATE_ROW_LIMIT
    newest = rows[:STATE_ROW_LIMIT]
    return [_row(state_dir, values) for values in reversed(newest)], truncated


def deployments(state_dir: Path) -> list[dict]:
    """Every deployment not removed (the newest STATE_ROW_LIMIT), oldest first, read-only."""
    entries, _ = read_state(state_dir)
    for entry in entries:
        entry.pop("_manifest_over_cap")
        entry.pop("_package")
    return entries


def _sha256(data: bytes | None) -> str | None:
    return None if data is None else "sha256:" + hashlib.sha256(data).hexdigest()


def build_record(project: Path, agent: str) -> dict | None:
    """`deploy/<agent>.build.json` as {path, image, refs: set, agent_yaml_sha256,
    lock_sha256}; None when there is none. Raises RegistryError when it is unsafe or
    malformed (including a record for another agent)."""
    if not AGENT_NAME.fullmatch(agent):
        return None
    relative = f"deploy/{agent}.build.json"
    raw = read_project_file(project / relative, BUILD_RECORD_LIMIT)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise RegistryError("BUILD_RECORD_INVALID", f"{relative} is not valid JSON") from error
    refs = data.get("refs") if isinstance(data, dict) else None
    lock = data.get("lock_sha256") if isinstance(data, dict) else None
    if (
        not isinstance(refs, list)
        or data.get("name") != agent
        or not isinstance(data.get("image"), str)
        or not SHA256.fullmatch(str(data.get("agent_yaml_sha256")))
        or not (lock is None or SHA256.fullmatch(str(lock)))
    ):
        raise RegistryError("BUILD_RECORD_INVALID", f"{relative} is not a build record of {agent}")
    return {
        "path": relative,
        "image": data["image"],
        "refs": {r["ref"] for r in refs if isinstance(r, dict) and isinstance(r.get("ref"), str)},
        "agent_yaml_sha256": data["agent_yaml_sha256"],
        "lock_sha256": lock,
    }


def _stale(project: Path, record: dict) -> bool:
    """Whether agent.yaml or agent.lock changed since the record was written."""
    current = _sha256(read_project_file(project / "agent.yaml", BUILD_RECORD_LIMIT))
    lock = _sha256(read_project_file(project / "agent.lock", BUILD_RECORD_LIMIT))
    return (current, lock) != (record["agent_yaml_sha256"], record["lock_sha256"])


def _record(project: Path, agent: str, row: dict) -> tuple[dict | None, str | None]:
    """(build record, reason every match is undetermined, or None when it can be trusted)."""
    try:
        record = build_record(project, agent)
        if record is None:
            return None, "no build record (deploy/<agent>.build.json) in this project"
        if _stale(project, record):
            return record, "agent.yaml or agent.lock changed since the build record was written"
        return record, None
    except RegistryError as error:
        row["errors"].append({"file": f"deploy/{agent}.build.json", "error": str(error)})
        return None, "the build record could not be read"


def deployed(ref: Ref, row: dict, project: Path, directories: list[Path]) -> dict:
    """Add `deployed`, `recorded_not_running`, `deployed_undetermined` and state `errors` to a
    `registry.usage` row.

    Entries are {deployment, name, state, target, environment, endpoint, image, state_dir};
    `deployed` and `recorded_not_running` entries add `build_record`, undetermined ones add
    `reason`."""
    row = {
        **row,
        "deployed": [],
        "recorded_not_running": [],
        "deployed_undetermined": [],
        "errors": list(row["errors"]),
    }
    agent = row.get("agent")
    if not agent or ref.kind == "service":
        return row  # a service ref is used by the stack, never by an agent image (#854)
    if ref.kind == "agent":
        return _package_deployments(ref, row, project, directories)
    record, problem = _record(project, agent, row)
    candidate = f"{agent}-candidate"
    for directory in directories:
        try:
            found, truncated = read_state(directory)
        except RegistryError as error:
            row["errors"].append({"file": _shown(directory), "error": clean(str(error))[0]})
            continue
        if truncated:
            error = RegistryError(
                "STATE_TRUNCATED",
                f"more than {STATE_ROW_LIMIT} deployments; only the newest were read",
            )
            row["errors"].append({"file": _shown(directory), "error": str(error)})
        for entry in found:
            over_cap = entry.pop("_manifest_over_cap")
            entry.pop("_package")
            matches = record is not None and entry["image"] == record["image"]
            if entry["name"] not in (agent, candidate) or (
                entry["name"] == candidate and not matches
            ):
                continue  # another agent (a "-candidate" only counts with this build's image)
            reason = problem
            if over_cap:
                reason = "its recorded manifest is over the size cap, so its image is unknown"
            if reason is None and not matches:
                reason = "its image is not the build record's (rebuilt since, or built elsewhere)"
            elif reason is None and str(ref) not in record["refs"]:
                reason = "the build record for its image does not list this ref"
            if reason:
                row["deployed_undetermined"].append({**entry, "reason": reason})
            else:
                bucket = "deployed" if entry["state"] == RUNNING else "recorded_not_running"
                row[bucket].append({**entry, "build_record": record["path"]})
    return row


def _package_deployments(ref: Ref, row: dict, project: Path, directories: list[Path]) -> dict:
    """A package is deployed by ref, never built into an image: its deployments are the ones
    whose store v4 `package` column names it (written by `deploy agent/…` and `release`, M13
    spec §7.3). Entries add `package_sha256`; one whose hash differs from the project's copy
    of the package is undetermined."""
    local = None
    path = project / "registry" / ref.relative()
    try:
        if path.is_dir() and not path.is_symlink():
            local = copy_and_hash(path)
    except (OSError, RegistryError) as error:
        row["errors"].append({"file": _shown(path), "error": clean(str(error))[0]})
    for directory in directories:
        try:
            found, truncated = read_state(directory)
        except RegistryError as error:
            row["errors"].append({"file": _shown(directory), "error": clean(str(error))[0]})
            continue
        if truncated:
            error = RegistryError(
                "STATE_TRUNCATED",
                f"more than {STATE_ROW_LIMIT} deployments; only the newest were read",
            )
            row["errors"].append({"file": _shown(directory), "error": str(error)})
        for entry in found:
            entry.pop("_manifest_over_cap")
            linked = entry.pop("_package")
            if linked is None or linked["ref"] != str(ref) or entry["name"] != ref.name:
                continue
            entry["package_sha256"] = linked["sha256"]
            if local != linked["sha256"]:
                reason = (
                    "this project has no copy of the package"
                    if local is None
                    else "this project's copy of the package differs from the deployed one"
                )
                row["deployed_undetermined"].append({**entry, "reason": reason})
            else:
                bucket = "deployed" if entry["state"] == RUNNING else "recorded_not_running"
                row[bucket].append(entry)
    return row
