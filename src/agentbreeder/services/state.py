"""A stack's private state, outside the repo: `~/.agentbreeder/stacks/<stack-id>/`.

    stack.db            owned secrets, host ports, digests, bootstrap markers (SQLite)
    compose.yaml        generated, 0600, file paths only
    files/<sha>/        content-addressed copies of file params and service `files/`
    service-secrets/    <service>/<name>[.<item>] values (0600), never mounted into agents
    mounts/             per-generation 0444 copies that containers bind-mount read-only
    envs/<env>/operator.yaml, principals.yaml, state/   (the deploy state dir)

`<stack-id>` is `<name>-<first 8 hex of sha256(absolute project path)>`, so two checkouts that
both use `name: northwind` never share secrets, ports, volumes or config. Every directory is
0700 and yours, and files are written with O_NOFOLLOW and an atomic replace.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from agentbreeder.services.schema import ServiceError

ROOT_ENV = "AGENTBREEDER_STACKS_DIR"
_ENV = re.compile(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?")
_SHARED_PART = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
_SECRET_FILE = re.compile(r"[a-z][a-z0-9_-]{0,30}(\.[a-z][a-z0-9_]{0,62})?")
ORIGINS = ("generate", "import", "bootstrap")
# As in registry.py: interpreter caches are never hashed into a version, so never copied.
SKIPPED_DIRS = ("__pycache__",)
SKIPPED_SUFFIXES = (".pyc", ".pyo")


def stacks_root() -> Path:
    value = os.environ.get(ROOT_ENV)
    return Path(value).expanduser().resolve() if value else Path.home() / ".agentbreeder/stacks"


def stack_id(name: str, project_dir: Path) -> str:
    digest = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()
    return f"{name}-{digest[:8]}"


def private_dir(path: Path) -> Path:
    """Create (0700) or check a directory: a real directory you own, never a symlink."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError as error:
        raise ServiceError("STATE_UNSAFE", f"cannot create {path}") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ServiceError("STATE_UNSAFE", f"{path} must be a directory you own (not a symlink)")
    if info.st_mode & 0o077:
        path.chmod(0o700)
    return path


def write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Atomically replace `path` with `data`; neither the file nor its temp is followed."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        if mode != 0o600:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_private(path: Path, limit: int = 64 * 1024) -> bytes | None:
    """A file you own with no group/world bits; None when it doesn't exist."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ServiceError("STATE_UNSAFE", f"{path.name} must be a regular file") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ServiceError("STATE_UNSAFE", f"{path.name} must be a 0600 file you own")
        return handle.read(limit + 1)[: limit + 1]


def _existing_private_dir(path: Path) -> bool:
    """Whether `path` is a directory you own (not a symlink); never creates it."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ServiceError("STATE_UNSAFE", f"cannot read {path}") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ServiceError("STATE_UNSAFE", f"{path} must be a directory you own (not a symlink)")
    return True


class StackState:
    @classmethod
    def locate(cls, stack_name: str, project_dir: Path, root: Path | None = None) -> Path | None:
        """The stack's state directory if `services up` made it; None otherwise. Creates nothing
        (`--stack` resolution and read-only commands must not leave phantom stacks)."""
        base = root or stacks_root()
        directory = base / stack_id(stack_name, project_dir)
        if not _existing_private_dir(base) or not _existing_private_dir(directory):
            return None
        return directory

    @classmethod
    def open_readonly(
        cls, stack_name: str, project_dir: Path, root: Path | None = None
    ) -> StackState | None:
        """`stack.db` opened with `mode=ro` (no mkdir, no CREATE, no write lock); None when the
        stack was never brought up."""
        directory = cls.locate(stack_name, project_dir, root)
        return None if directory is None else cls.open_directory(directory)

    @classmethod
    def open_directory(cls, directory: Path) -> StackState | None:
        """A stack state directory opened read-only (as `open_readonly`); None when it holds
        no stack.db."""
        if not _existing_private_dir(directory):
            return None
        path = directory / "stack.db"
        if read_private(path, limit=0) is None:
            return None
        self = cls.__new__(cls)
        self.id = directory.name
        self.project = f"agentbreeder-{self.id}"
        self.root = directory.parent
        self.directory = directory
        self.db = sqlite3.connect(
            f"file:{quote(str(path))}?mode=ro", uri=True, timeout=5, isolation_level=None
        )
        return self

    def __init__(self, stack_name: str, project_dir: Path, root: Path | None = None):
        self.id = stack_id(stack_name, project_dir)
        self.project = f"agentbreeder-{self.id}"  # Compose project and resource prefix
        base = private_dir(root or stacks_root())
        self.root = base
        self.directory = private_dir(base / self.id)
        self.db = sqlite3.connect(self.directory / "stack.db", isolation_level=None)
        os.chmod(self.directory / "stack.db", 0o600)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                service TEXT NOT NULL, name TEXT NOT NULL, origin TEXT NOT NULL,
                PRIMARY KEY (service, name));
            CREATE TABLE IF NOT EXISTS ports (
                service TEXT NOT NULL, port TEXT NOT NULL, host_port INTEGER NOT NULL,
                PRIMARY KEY (service, port));
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )

    def close(self) -> None:
        self.db.close()

    def purge(self) -> None:
        """Delete this stack's state dir (secrets, config, history). Only ever this stack's own
        directory under the stacks root, never a symlink."""
        self.db.close()
        if self.directory.is_symlink() or self.directory.resolve().parent != self.root.resolve():
            raise ServiceError("STATE_UNSAFE", "the stack state dir is not where it should be")
        shutil.rmtree(self.directory)

    # -- layout ---------------------------------------------------------------------------------

    @property
    def compose_path(self) -> Path:
        return self.directory / "compose.yaml"

    def env_dir(self, environment: str) -> Path:
        if not _ENV.fullmatch(environment):
            raise ServiceError("STACK_ENVIRONMENT", "invalid environment name")
        # Under envs/, so an environment can't be named like the stack's own dirs (mounts/...).
        return self.directory / "envs" / environment

    def environments(self) -> list[str]:
        """Environments `up` has written an operator config for."""
        return written_environments(self.directory)

    def deploy_state(self, environment: str) -> Path:
        return self.env_dir(environment) / "state"

    def _secret_path(self, service: str, name: str) -> Path:
        if not _SECRET_FILE.fullmatch(name):
            raise ServiceError("SECRET_NAME", "invalid service secret name")
        return self.directory / "service-secrets" / service / name

    # -- meta -----------------------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def salt(self) -> bytes:
        """A per-stack random key: generation ids are HMACs, so labels and paths that change
        with a secret's value reveal nothing about it."""
        value = self.get("salt")
        if value is None:
            value = secrets.token_hex(32)
            self.put("salt", value)
        return bytes.fromhex(value)

    def generation(self, values: dict[str, bytes]) -> str:
        mac = hmac.new(self.salt(), digestmod=hashlib.sha256)
        for name in sorted(values):
            mac.update(name.encode() + b"\0" + hashlib.sha256(values[name]).digest())
        return mac.hexdigest()[:16]

    # -- secrets --------------------------------------------------------------------------------

    def owner(self, service: str, name: str) -> str | None:
        row = self.db.execute(
            "SELECT origin FROM secrets WHERE service=? AND name=?", (service, name)
        ).fetchone()
        return row[0] if row else None

    def read_secret(self, service: str, name: str) -> bytes | None:
        """A secret `up` wrote. A file AgentBreeder didn't record is refused (pre-seeded)."""
        path = self._secret_path(service, name)
        if not (path.exists() or path.is_symlink()):
            return None
        if self.owner(service, name) is None:
            raise ServiceError(
                "SECRET_NOT_OWNED",
                f"service {service}: secret {name} exists but services up did not create it; "
                "remove it to let up generate or import it",
            )
        data = read_private(path)
        if not data:
            raise ServiceError("SECRET_EMPTY", f"service {service}: secret {name} is empty")
        return data

    def write_secret(self, service: str, name: str, data: bytes, origin: str) -> None:
        if origin not in ORIGINS:
            raise ValueError(origin)
        path = self._secret_path(service, name)
        private_dir(path.parent.parent)
        private_dir(path.parent)
        write_private(path, data)
        self.db.execute(
            "INSERT OR REPLACE INTO secrets (service, name, origin) VALUES (?, ?, ?)",
            (service, name, origin),
        )

    def known_secrets(self) -> list[bytes]:
        """Every stored value, for redacting logs and error text."""
        values = []
        for service, name in self.db.execute("SELECT service, name FROM secrets"):
            try:
                data = self.read_secret(service, name)
            except ServiceError:
                continue
            if data:
                values.append(data)
        return values

    # -- ports ----------------------------------------------------------------------------------

    def host_port(self, service: str, port: str, allocate, reserved=frozenset()) -> int:
        """The recorded host port, or a newly allocated one (not recorded: see record_port)."""
        row = self.db.execute(
            "SELECT host_port FROM ports WHERE service=? AND port=?", (service, port)
        ).fetchone()
        if row:
            return int(row[0])
        taken = {r[0] for r in self.db.execute("SELECT host_port FROM ports")} | set(reserved)
        for _ in range(20):
            chosen = int(allocate())
            if chosen not in taken:
                return chosen
        raise ServiceError("PORT_ALLOCATION", "could not allocate a free loopback port")

    def record_port(self, service: str, port: str, host_port: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO ports (service, port, host_port) VALUES (?, ?, ?)",
            (service, port, host_port),
        )

    def ports(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for service, port, host_port in self.db.execute(
            "SELECT service, port, host_port FROM ports ORDER BY service, port"
        ):
            result.setdefault(service, {})[port] = int(host_port)
        return result

    # -- mounts ---------------------------------------------------------------------------------

    def mount_dir(self, purpose: str, values: dict[str, bytes], mode: int = 0o444) -> Path:
        """A 0700 directory of 0444 copies for one container generation, bind-mounted read-only
        at /run/secrets. Per-item names (`role.support`) become `role/support`. The directory
        name is an HMAC of the contents, so the same values give the same path (no recreate).
        `mode` 0o400 is for containers that run as the operator's uid (MCP relays)."""
        root = private_dir(self.directory / "mounts")
        target = root / f"{purpose}-{self.generation(values)}"
        if target.is_dir() and not target.is_symlink():
            return target
        staging = root / f".{target.name}.tmp"
        if staging.exists():
            shutil.rmtree(staging)
        private_dir(staging)
        for name, data in sorted(values.items()):
            base, _, item = name.partition(".")
            path = staging / base / item if item else staging / base
            if item:
                path.parent.mkdir(mode=0o755, exist_ok=True)
            write_private(path, data, mode=mode)
        # Readable by the container's uid; the 0700 parent keeps other host users out.
        staging.chmod(0o755)
        os.replace(staging, target)
        return target

    def prune_mounts(self, keep: set[Path]) -> None:
        root = self.directory / "mounts"
        if not root.is_dir():
            return
        for child in root.iterdir():
            if child not in keep and child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)

    def shared_dir(self, *parts: str) -> Path:
        """A 0755 directory under the stack dir (still behind its 0700 parent) whose 0444 files
        a container bind-mounts read-only: MCP grants and pins (spec §7.7.4)."""
        path = self.directory
        for part in parts:
            if not _SHARED_PART.fullmatch(part):
                raise ServiceError("STATE_UNSAFE", "invalid state path")
            path = path / part
            if path.is_symlink():
                raise ServiceError("STATE_UNSAFE", f"{path} must not be a symlink")
            path.mkdir(mode=0o755, exist_ok=True)
            path.chmod(0o755)
        return path

    def copy_file(self, source: Path) -> Path:
        """Content-addressed copy (file or directory) under files/; same content, same path."""
        digest = _tree_digest(source)
        root = private_dir(self.directory / "files")
        target = root / digest[:16] / source.name
        if target.exists():
            return target
        staging = root / f".{digest[:16]}.tmp"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o755)
        if source.is_dir():
            shutil.copytree(
                source,
                staging / source.name,
                symlinks=False,
                ignore=shutil.ignore_patterns(*SKIPPED_DIRS, "*.pyc", "*.pyo"),
            )
        else:
            shutil.copyfile(source, staging / source.name)
        # Read-only for everyone; a source file's executable bit is kept (it is hashed, as in the
        # registry), so a locked `files/init.sh` can be run as a program rather than via a shell.
        copied = staging / source.name
        for path in [staging, *staging.rglob("*")]:
            if path.is_dir():
                path.chmod(0o755)
            else:
                original = source if source.is_file() else source / path.relative_to(copied)
                path.chmod(0o555 if _executable(original) else 0o444)
        os.replace(staging, root / digest[:16])
        return target


def written_environments(directory: Path) -> list[str]:
    root = directory / "envs"
    if not root.is_dir():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if _ENV.fullmatch(child.name) and (child / "operator.yaml").is_file()
    )


def _tree_digest(source: Path) -> str:
    digest = hashlib.sha256()
    paths = [source] if source.is_file() else sorted(_tree_files(source))
    for path in paths:
        if path.is_symlink():
            raise ServiceError("STATE_UNSAFE", "service files must not contain symlinks")
        digest.update(path.relative_to(source.parent).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"x" if _executable(path) else b"-")
    return digest.hexdigest()


def _tree_files(source: Path) -> list[Path]:
    # Same skip rule as the registry digest: bytecode caches are never locked, so never mounted.
    return [
        p
        for p in source.rglob("*")
        if p.is_file()
        and p.suffix not in SKIPPED_SUFFIXES
        and not set(SKIPPED_DIRS).intersection(p.relative_to(source).parts)
    ]


def _executable(path: Path) -> bool:
    return bool(path.stat().st_mode & stat.S_IXUSR)
