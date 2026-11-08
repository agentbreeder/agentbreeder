"""Local secret files for `secret://name` manifest references (target: local).

Values are set with `agentbreeder secret set NAME < value` and stored as `<state>/secrets/<name>`
(0600, owned by the caller, never followed through a symlink). At deploy, after the gate, each
referenced value is copied to `<state>/deployments/<id>/secrets/<name>` (0444 inside a 0700
directory, so the agent's uid 65532 can read the bind-mounted file while nobody else on the host
can reach it) and mounted read-only at `/run/secrets/<name>`. The container gets
`<ENV>_FILE=/run/secrets/<name>`; values never appear in environment variables, labels, build
arguments, logs or the audit log (which records names only).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
MAX_BYTES = 64 * 1024
MOUNT_ROOT = "/run/secrets"


class SecretError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SecretMount:
    envs: tuple[str, ...]  # e.g. ("DATABASE_URL",); the container gets DATABASE_URL_FILE
    name: str
    source: Path
    target: str


def secret_name(reference: str) -> str:
    """`secret://northwind-db` -> `northwind-db` (local secret names are restricted)."""
    name = reference.removeprefix("secret://")
    if not NAME.fullmatch(name):
        raise SecretError(
            "SECRET_NAME",
            f"Local secret names must match {NAME.pattern}: {reference!r}",
        )
    return name


def _store(state: Path) -> Path:
    return state / "secrets"


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise SecretError("SECRET_MODE", f"{path} must be a directory owned by you")
    if info.st_mode & 0o077:
        path.chmod(0o700)


def set_secret(state: Path, name: str, data: bytes) -> Path:
    if not NAME.fullmatch(name):
        raise SecretError("SECRET_NAME", f"Secret names must match {NAME.pattern}")
    if not data or len(data) > MAX_BYTES:
        raise SecretError("SECRET_EMPTY", f"Secret value must be 1..{MAX_BYTES} bytes")
    state.parent.mkdir(parents=True, exist_ok=True)
    _private_dir(state)  # same rules as the deployment store (0700, yours)
    _private_dir(_store(state))
    path = _store(state) / name
    temporary = path.with_name(f".{name}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _read(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as error:
        raise SecretError(
            "SECRET_MISSING",
            f"Secret {path.name!r} is not set; run `agentbreeder secret set {path.name}`",
        ) from error
    except OSError as error:  # ELOOP: a symlink
        raise SecretError("SECRET_MODE", f"Secret {path.name!r} must be a regular file") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise SecretError("SECRET_MODE", f"Secret {path.name!r} must be a file you own")
        if info.st_mode & 0o077:
            raise SecretError(
                "SECRET_MODE", f"Secret {path.name!r} must be mode 0600 (chmod 600 {path})"
            )
        data = handle.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise SecretError("SECRET_EMPTY", f"Secret {path.name!r} must be 1..{MAX_BYTES} bytes")
    return data


def preflight(state: Path, references: dict[str, str]) -> None:
    """Check every referenced secret exists with safe type, ownership and mode. Metadata only:
    no value is read until the deploy is allowed and approved (materialise)."""
    for reference in references.values():
        name = secret_name(reference)
        path = _store(state) / name
        try:
            info = path.lstat()
        except FileNotFoundError as error:
            raise SecretError(
                "SECRET_MISSING",
                f"Secret {name!r} is not set; run `agentbreeder secret set {name}`",
            ) from error
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise SecretError("SECRET_MODE", f"Secret {name!r} must be a regular file you own")
        if info.st_mode & 0o077:
            raise SecretError(
                "SECRET_MODE", f"Secret {name!r} must be mode 0600 (chmod 600 {path})"
            )
        if not 0 < info.st_size <= MAX_BYTES:
            raise SecretError("SECRET_EMPTY", f"Secret {name!r} must be 1..{MAX_BYTES} bytes")


def deployment_dir(state: Path, deployment_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", deployment_id):
        raise SecretError("SECRET_NAME", "invalid deployment id")
    return state / "deployments" / deployment_id / "secrets"


def materialise(state: Path, deployment_id: str, references: dict[str, str]) -> list[SecretMount]:
    """Copy referenced values into the deployment's private secret dir; return bind mounts."""
    target_dir = deployment_dir(state, deployment_id)
    _private_dir(target_dir.parent.parent)
    _private_dir(target_dir.parent)
    _private_dir(target_dir)
    by_name: dict[str, list[str]] = {}
    for env, reference in sorted(references.items()):
        by_name.setdefault(secret_name(reference), []).append(env)
    mounts = []
    for name, envs in sorted(by_name.items()):
        data = _read(_store(state) / name)
        path = target_dir / name
        temporary = target_dir / f".{name}.tmp"
        temporary.unlink(missing_ok=True)
        descriptor = os.open(
            temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o444)
        os.replace(temporary, path)
        mounts.append(SecretMount(tuple(envs), name, path, f"{MOUNT_ROOT}/{name}"))
    return mounts


def write_generated(state: Path, deployment_id: str, name: str, env: str, data: bytes):
    """Write an engine-generated secret (e.g. the minted gateway key) for one deployment."""
    if not NAME.fullmatch(name):
        raise SecretError("SECRET_NAME", "invalid generated secret name")
    target_dir = deployment_dir(state, deployment_id)
    _private_dir(target_dir.parent.parent)
    _private_dir(target_dir.parent)
    _private_dir(target_dir)
    path = target_dir / name
    temporary = target_dir / f".{name}.tmp"
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.chmod(temporary, 0o444)
    os.replace(temporary, path)
    return SecretMount((env,), name, path, f"{MOUNT_ROOT}/{name}")


def present(state: Path, deployment_id: str, references: dict[str, str]) -> bool:
    """Whether every copy for this container generation still exists."""
    directory = deployment_dir(state, deployment_id)
    return all((directory / secret_name(ref)).is_file() for ref in references.values())


def cleanup(state: Path, deployment_id: str) -> None:
    """Delete a deployment's copied secrets (remove, failure paths). Safe to repeat."""
    directory = deployment_dir(state, deployment_id)
    if directory.is_symlink():
        directory.unlink()
    elif directory.exists():
        shutil.rmtree(directory)
