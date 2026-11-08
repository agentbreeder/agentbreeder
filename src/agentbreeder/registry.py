"""File/git-backed registries for agent building blocks, addressed as `kind/name@X.Y.Z`.

A registry root holds immutable versions at `<root>/<kind>/<name>/<version>/` with a
`manifest.yaml` (kind, name, version, description) and the payload. Roots are searched in order:
the project's `./registry`, then each `AGENTBREEDER_REGISTRY_PATH` entry, then the versions
shipped inside AgentBreeder (runtimes). A ref found in several roots with different content is an
error rather than silent shadowing.

Integrity (spec §6): a version is copied into the build context first and hashed from the copy,
so what is hashed is exactly what is built. Only regular files and directories are allowed — no
symlinks, hardlinks, devices, absolute or `..` paths, or non-UTF-8 names. The hash covers
length-prefixed (type, path, executable bit, size, bytes) records in sorted path order. Hashes
detect tampering after a version has been locked; they cannot vouch for the first lock.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentbreeder.strict_yaml import StrictYamlError, load_file

KINDS = ("runtime", "prompt", "tool", "knowledge", "eval")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
REF = re.compile(rf"(?P<kind>{'|'.join(KINDS)})/(?P<name>{NAME.pattern})@(?P<version>[0-9.]+)")
PATH_ENV = "AGENTBREEDER_REGISTRY_PATH"
BUILTIN_ROOT = Path(__file__).parent / "builtin_registry"
MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024


class RegistryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass(frozen=True)
class Ref:
    kind: str
    name: str
    version: str

    @classmethod
    def parse(cls, text: str) -> Ref:
        match = REF.fullmatch(text or "")
        if not match:
            if isinstance(text, str) and "@" not in text and "/" in text:
                raise RegistryError(
                    "REF_UNPINNED", f"{text!r} needs an exact version, e.g. {text}@1.0.0"
                )
            raise RegistryError(
                "REF_INVALID", f"{text!r} is not kind/name@X.Y.Z (kinds: {', '.join(KINDS)})"
            )
        if not VERSION.fullmatch(match["version"]):
            raise RegistryError(
                "REF_UNPINNED", f"{text!r}: Phase A refs need an exact X.Y.Z version"
            )
        return cls(match["kind"], match["name"], match["version"])

    def __str__(self) -> str:
        return f"{self.kind}/{self.name}@{self.version}"

    def relative(self) -> Path:
        return Path(self.kind, self.name, self.version)


@dataclass(frozen=True)
class Resolved:
    ref: Ref
    path: Path
    root: Path


def roots(project_dir: Path | None = None) -> list[Path]:
    result = []
    if project_dir is not None:
        result.append(project_dir / "registry")
    for entry in os.environ.get(PATH_ENV, "").split(os.pathsep):
        if entry.strip():
            result.append(Path(entry).expanduser())
    result.append(BUILTIN_ROOT)
    unique = []
    for root in result:
        if root not in unique:
            unique.append(root)
    return unique


def _safe_name(part: str) -> None:
    try:
        part.encode("utf-8")
    except UnicodeEncodeError as error:
        raise RegistryError("ENTRY_UNSAFE", "registry entry names must be UTF-8") from error
    if part in ("", ".", "..") or "/" in part or "\\" in part or "\x00" in part:
        raise RegistryError("ENTRY_UNSAFE", f"unsafe path component {part!r}")


def _entries(source: Path) -> list[tuple[str, Path, os.stat_result]]:
    """Every entry under `source`, validated, sorted by POSIX relative path."""
    found = []
    total = 0
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        for name in sorted(dirnames + filenames):
            _safe_name(name)
            path = Path(directory, name)
            info = path.lstat()
            relative = path.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode):
                raise RegistryError("ENTRY_UNSAFE", f"symlinks are not allowed: {relative}")
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1:
                    raise RegistryError("ENTRY_UNSAFE", f"hardlinks are not allowed: {relative}")
                total += info.st_size
            elif not stat.S_ISDIR(info.st_mode):
                raise RegistryError("ENTRY_UNSAFE", f"only files and directories: {relative}")
            found.append((relative, path, info))
            if len(found) > MAX_FILES or total > MAX_BYTES:
                raise RegistryError("ENTRY_UNSAFE", "registry version is too large")
    return sorted(found, key=lambda item: item[0])


def _frame(data: bytes) -> bytes:
    return len(data).to_bytes(8, "big") + data


def copy_and_hash(source: Path, destination: Path | None = None) -> str:
    """Hash a version directory, copying it to `destination` first when given (and hashing the
    copy, so the hashed bytes are the built bytes). Returns `sha256:<hex>`."""
    if source.is_symlink() or not source.is_dir():
        raise RegistryError("ENTRY_UNSAFE", f"{source} must be a real directory")
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=False)
        for relative, path, info in _entries(source):
            target = destination / relative
            if stat.S_ISDIR(info.st_mode):
                target.mkdir()
                continue
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as reader:
                if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                    raise RegistryError("ENTRY_UNSAFE", f"changed while copying: {relative}")
                with open(target, "xb") as writer:
                    shutil.copyfileobj(reader, writer)
            os.chmod(target, 0o755 if info.st_mode & 0o111 else 0o644)
        source = destination
    digest = hashlib.sha256(b"agentbreeder-registry-v1\0")
    for relative, path, info in _entries(source):
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"D" + _frame(relative.encode()))
            continue
        content = path.read_bytes()
        executable = b"x" if info.st_mode & 0o111 else b"-"
        digest.update(b"F" + _frame(relative.encode()) + executable + _frame(content))
    return "sha256:" + digest.hexdigest()


def read_manifest(version_dir: Path, ref: Ref | None = None) -> dict:
    try:
        data = load_file(version_dir / "manifest.yaml", limit=64 * 1024)
    except StrictYamlError as error:
        raise RegistryError("MANIFEST_INVALID", f"{version_dir}: {error}") from error
    try:
        declared = Ref.parse(f"{data.get('kind')}/{data.get('name')}@{data.get('version')}")
    except RegistryError as error:
        raise RegistryError(
            "MANIFEST_INVALID", f"{version_dir}/manifest.yaml needs kind, name and X.Y.Z version"
        ) from error
    if ref is not None and declared != ref:
        raise RegistryError(
            "MANIFEST_INVALID", f"{version_dir} declares {declared}, expected {ref}"
        )
    return data


def resolve(ref: Ref, search: list[Path]) -> Resolved:
    hits = []
    for root in search:
        candidate = root / ref.relative()
        if candidate.is_symlink():
            raise RegistryError("ENTRY_UNSAFE", f"{candidate} must not be a symlink")
        if candidate.is_dir():
            hits.append(Resolved(ref, candidate, root))
    if not hits:
        where = ", ".join(str(r) for r in search)
        raise RegistryError("REF_NOT_FOUND", f"{ref} not found in {where}")
    if len(hits) > 1 and len({copy_and_hash(hit.path) for hit in hits}) > 1:
        raise RegistryError(
            "REF_CONFLICT",
            f"{ref} exists with different content in {hits[0].root} and {hits[1].root}",
        )
    read_manifest(hits[0].path, ref)
    return hits[0]


def publish(source: Path, root: Path) -> tuple[Ref, str, Path]:
    """Copy a prepared version directory into a registry root. Versions are immutable."""
    data = read_manifest(source)
    ref = Ref.parse(f"{data['kind']}/{data['name']}@{data['version']}")
    target = root / ref.relative()
    if target.exists() or target.is_symlink():
        raise RegistryError(
            "VERSION_EXISTS", f"{ref} is already published at {target}; bump the version"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".publish-", dir=target.parent))
    try:
        digest = copy_and_hash(source, staging / "v")
        os.rename(staging / "v", target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return ref, digest, target


def listing(search: list[Path], prefix: str = "") -> list[dict]:
    rows = []
    for root in search:
        for kind in KINDS:
            for version_dir in sorted((root / kind).glob("*/*")) if (root / kind).is_dir() else []:
                text = f"{kind}/{version_dir.parent.name}@{version_dir.name}"
                if not text.startswith(prefix) or not REF.fullmatch(text):
                    continue
                rows.append({"ref": text, "root": str(root), "path": str(version_dir)})
    return rows


def verify(search: list[Path]) -> list[dict]:
    """Re-check every version: safe entries, manifest matches its path. Returns problems."""
    problems = []
    for row in listing(search):
        try:
            read_manifest(Path(row["path"]), Ref.parse(row["ref"]))
            row["sha256"] = copy_and_hash(Path(row["path"]))
        except RegistryError as error:
            problems.append({"ref": row["ref"], "path": row["path"], "error": str(error)})
    return problems


SCAFFOLD = {
    "prompt": {"prompt.md": "You are a helpful assistant.\n"},
    "tool": {
        "tool.yaml": "functions: []   # see docs/agent-yaml.md\n",
        "src/.keep": "",
    },
    "knowledge": {"docs/README.md": "# Knowledge source\n\nAdd .md or .txt files here.\n"},
    "eval": {"golden.jsonl": ""},
}


def scaffold(kind: str, name: str, directory: Path, version: str = "0.1.0") -> Path:
    if kind not in SCAFFOLD:
        raise RegistryError("REF_INVALID", f"kind must be one of {', '.join(SCAFFOLD)}")
    ref = Ref.parse(f"{kind}/{name}@{version}")
    target = directory / f"{ref.kind}-{ref.name}"
    if target.exists():
        raise RegistryError("VERSION_EXISTS", f"{target} already exists")
    target.mkdir(parents=True)
    (target / "manifest.yaml").write_text(
        f"kind: {ref.kind}\nname: {ref.name}\nversion: {ref.version}\ndescription: ''\n"
    )
    for relative, content in SCAFFOLD[kind].items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return target
