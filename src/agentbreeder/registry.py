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

The installed package's own built-in registry and examples are the one exception to the hardlink
rule (installers such as uv hardlink from their cache): their files are compared with the shipped
`_content.json` instead (bytes, exec bit, no extra or missing files). That is a corruption check,
not provenance: whoever can write site-packages can rewrite the manifest too.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentbreeder.strict_yaml import StrictYamlError, load_file

KINDS = ("runtime", "prompt", "tool", "knowledge", "eval", "service", "mcp")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
REF = re.compile(rf"(?P<kind>{'|'.join(KINDS)})/(?P<name>{NAME.pattern})@(?P<version>[0-9.]+)")
PATH_ENV = "AGENTBREEDER_REGISTRY_PATH"
BUILTIN_ROOT = Path(__file__).parent / "builtin_registry"
# The installed package, fixed at import. Unlike BUILTIN_ROOT (tests point it at a temp dir), this
# names the shipped trees whose files are checked against `_content.json` instead of being refused
# for hardlinks: package installers such as uv hardlink installed files from their cache.
_PACKAGED_ROOT = Path(__file__).resolve().parent
_SHIPPED_TREES = ("builtin_registry", "examples")
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
            raise RegistryError("REF_UNPINNED", f"{text!r}: refs need an exact X.Y.Z version")
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


_CHECKOUT_HINT = "in a checkout: git add the change, then run scripts/content_manifest.py"


@functools.lru_cache(maxsize=4)
def _shipped_manifest(package: Path) -> dict[str, dict]:
    path = package / "_content.json"
    try:
        files = json.loads(path.read_text())["files"]
        valid = isinstance(files, dict) and all(
            isinstance(entry, dict)
            and isinstance(entry.get("sha256"), str)
            and isinstance(entry.get("exec"), bool)
            for entry in files.values()
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RegistryError(
            "CONTENT_MANIFEST_MISSING", f"cannot read the shipped content manifest {path}"
        ) from error
    if not valid:
        raise RegistryError("CONTENT_MANIFEST_MISSING", f"malformed content manifest {path}")
    return files


def _shipped_prefix(source: Path) -> str | None:
    """The package-relative path of `source` when it lies in a shipped tree, else None."""
    package = _PACKAGED_ROOT.resolve()
    resolved = source.resolve()
    for tree in _SHIPPED_TREES:
        if resolved.is_relative_to(package / tree):
            return resolved.relative_to(package).as_posix()
    return None


def trusted_root(path: Path) -> bool:
    """True for the installed built-in registry and shipped examples (checked by manifest)."""
    return _shipped_prefix(path) is not None


def _mismatch(message: str) -> RegistryError:
    return RegistryError("CONTENT_MISMATCH", f"{message} ({_CHECKOUT_HINT})")


def _check_shipped(
    manifest: dict, prefix: str, relative: str, path: Path, info: os.stat_result
) -> None:
    """Corruption check for one shipped file: same bytes and exec bit as `_content.json`."""
    entry = manifest.get(f"{prefix}/{relative}")
    if entry is None:
        raise _mismatch(f"not in the shipped manifest: {relative}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise _mismatch(f"differs from the shipped manifest: {relative}")
    if bool(info.st_mode & 0o111) != entry["exec"]:
        raise _mismatch(f"exec bit differs from the manifest: {relative}")


def _check_shipped_complete(
    manifest: dict, prefix: str, found: list[tuple[str, Path, os.stat_result]]
) -> None:
    expected = {key.removeprefix(prefix + "/") for key in manifest if key.startswith(prefix + "/")}
    files = {relative for relative, _, info in found if stat.S_ISREG(info.st_mode)}
    if missing := sorted(expected - files):
        raise _mismatch(f"missing shipped files: {', '.join(missing)}")
    parents = {parent.as_posix() for name in expected for parent in Path(name).parents}
    for relative, _, info in found:
        if stat.S_ISDIR(info.st_mode) and relative not in parents:
            raise _mismatch(f"not in the shipped manifest: {relative}/")


def _entries(source: Path) -> list[tuple[str, Path, os.stat_result]]:
    """Every entry under `source`, validated, sorted by POSIX relative path.

    Files in the installed package's shipped trees may be hardlinked (installer caches) and are
    instead compared with `_content.json`; every other root refuses hardlinks."""
    found = []
    total = 0
    shipped = _shipped_prefix(source)
    manifest = _shipped_manifest(_PACKAGED_ROOT.resolve()) if shipped is not None else {}
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        # Interpreter caches are never part of a version: skipped for hashing and copying.
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        filenames = [f for f in filenames if not f.endswith((".pyc", ".pyo"))]
        for name in sorted(dirnames + filenames):
            _safe_name(name)
            path = Path(directory, name)
            info = path.lstat()
            relative = path.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode):
                raise RegistryError("ENTRY_UNSAFE", f"symlinks are not allowed: {relative}")
            if stat.S_ISREG(info.st_mode):
                if shipped is not None:
                    _check_shipped(manifest, shipped, relative, path, info)
                elif info.st_nlink > 1:
                    raise RegistryError("ENTRY_UNSAFE", f"hardlinks are not allowed: {relative}")
                total += info.st_size
            elif not stat.S_ISDIR(info.st_mode):
                raise RegistryError("ENTRY_UNSAFE", f"only files and directories: {relative}")
            found.append((relative, path, info))
            if len(found) > MAX_FILES or total > MAX_BYTES:
                raise RegistryError("ENTRY_UNSAFE", "registry version is too large")
    if shipped is not None:
        _check_shipped_complete(manifest, shipped, found)
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
    if trusted_root(root):
        raise RegistryError(
            "REGISTRY_READ_ONLY",
            f"{root} is shipped inside AgentBreeder and checked against its content manifest; "
            "add the version directory by hand, git add it, run scripts/content_manifest.py "
            "and record it in tests/fixtures/builtin_hashes.json",
        )
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
    "mcp": {
        "mcp.yaml": (
            "# See docs/services.md (MCP servers). Remote upstreams are https only.\n"
            'upstream:\n  remote: {url: "https://mcp.example.com/mcp"}\n'
            "auth: {type: bearer, secret: token}\n"
            "secrets:\n  token: {import: EXAMPLE_MCP_TOKEN}\n"
            "tools:\n  example_tool: {effect: read}\n"
        ),
    },
    "prompt": {"prompt.md": "You are a helpful assistant.\n"},
    "tool": {
        "tool.yaml": "functions: []   # see docs/agent-yaml.md\n",
        "src/.keep": "",
    },
    "knowledge": {"docs/README.md": "# Knowledge source\n\nAdd .md or .txt files here.\n"},
    "eval": {"golden.jsonl": ""},
    "service": {
        "service.yaml": (
            "# See docs/services.md. Images must be digest-pinned.\n"
            "image: registry.example.com/name@sha256:"
            + "0"
            * 64
            + "\nports:\n  http: {port: 8080, expose: internal}\n"
            "health: {http: /health}\n"
            "outputs:\n  url: {type: url, port: http}\n"
        ),
    },
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


# -- Read-only views: `registry show`, `diff` and `used-by` ---------------------------------------
# Pure reads over registry roots and project files (no Docker, state, secrets or operator config):
# the local console calls these in-process. Every version goes through `resolve` and `_entries`
# (symlink, hardlink, size and shipped-content checks), files are opened with O_NOFOLLOW and
# checked against the inode `_entries` saw, text is capped, and control and bidi characters are
# stripped from file names, manifest values and content before anything is shown (S-27).

SHOW_FILE_LIMIT = 256 * 1024  # text kept per file
SHOW_TOTAL_LIMIT = 2 * 1024 * 1024  # text kept per version; later files are listed, not shown
_BIDI = "؜‎‏  ‪-‮⁦-⁩"
_STRIP_NAME = re.compile(f"[\x00-\x1f\x7f-\x9f{_BIDI}]")
_STRIP_TEXT = re.compile(f"[\x00-\x08\x0b-\x1f\x7f-\x9f{_BIDI}]")  # keeps tab and newline


def clean(text: str, *, name: bool = False) -> tuple[str, bool]:
    """`text` without control and bidi characters (a name also loses tab and newline), and
    whether anything was removed."""
    cleaned = (_STRIP_NAME if name else _STRIP_TEXT).sub("", text)
    return cleaned, cleaned != text


def _clean_value(value):
    if isinstance(value, str):
        return clean(value)[0]
    if isinstance(value, dict):
        return {clean(str(k), name=True)[0]: _clean_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_value(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return clean(str(value))[0]  # e.g. YAML dates


def root_kind(root: Path, project_dir: Path | None = None) -> str:
    """Where a search root comes from: `project` (./registry), `builtin` (shipped with
    AgentBreeder) or `path` (an AGENTBREEDER_REGISTRY_PATH entry)."""
    if root.resolve() == BUILTIN_ROOT.resolve():
        return "builtin"
    if project_dir is not None and root == project_dir / "registry":
        return "project"
    return "path"


@dataclass
class _File:
    relative: str
    type: str  # "file" or "dir"
    size: int = 0
    executable: bool = False
    sha256: str = ""
    head: bytes | None = None  # first SHOW_FILE_LIMIT bytes; None once the version budget is spent
    truncated: bool = False


def _read_one(relative: str, path: Path, info: os.stat_result) -> tuple[int, str, bytes]:
    """Size, sha256 and the first SHOW_FILE_LIMIT bytes of one file, opened with O_NOFOLLOW
    and refused if it is no longer the regular file `_entries` checked."""
    digest = hashlib.sha256()
    head = bytearray()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as reader:
        now = os.fstat(reader.fileno())
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
            raise RegistryError("ENTRY_UNSAFE", f"changed while reading: {relative}")
        while chunk := reader.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise RegistryError("ENTRY_UNSAFE", "registry version is too large")
            digest.update(chunk)
            if len(head) < SHOW_FILE_LIMIT:
                head += chunk[: SHOW_FILE_LIMIT - len(head)]
    return size, "sha256:" + digest.hexdigest(), bytes(head)


def _read_version(path: Path) -> list[_File]:
    """Every entry of a version directory, validated by `_entries`, in path order."""
    budget = SHOW_TOTAL_LIMIT
    result = []
    for relative, file_path, info in _entries(path):
        if stat.S_ISDIR(info.st_mode):
            result.append(_File(relative, "dir"))
            continue
        size, digest, head = _read_one(relative, file_path, info)
        keep = len(head) <= budget
        if keep:
            budget -= len(head)
        executable = bool(info.st_mode & 0o111)
        kept = head if keep else None
        result.append(_File(relative, "file", size, executable, digest, kept, size > len(head)))
    return result


def _decode(head: bytes, truncated: bool) -> str | None:
    """UTF-8 text, or None for binary content (NUL bytes or invalid UTF-8)."""
    if b"\x00" in head:
        return None
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as error:
        # The cap can cut a multi-byte character in half: drop only that tail.
        if truncated and error.start >= len(head) - 3:
            try:
                return head[: error.start].decode("utf-8")
            except UnicodeDecodeError:
                return None
        return None


def _text_of(item: _File) -> tuple[str | None, bool, bool]:
    """(cleaned text or None, binary, whether characters were stripped from the content)."""
    if item.head is None:
        return None, False, False
    text = _decode(item.head, item.truncated)
    if text is None:
        return None, True, False
    cleaned, stripped = clean(text)
    return cleaned, False, stripped


def _file_view(item: _File) -> dict:
    name, stripped = clean(item.relative, name=True)
    if item.type == "dir":
        return {"path": name, "type": "dir", "stripped": stripped}
    text, binary, content_stripped = _text_of(item)
    return {
        "path": name,
        "type": "file",
        "size": item.size,
        "executable": item.executable,
        "sha256": item.sha256,
        "binary": binary,
        "truncated": item.truncated,
        "omitted": item.head is None,
        "text": text,
        "stripped": stripped or content_stripped,
    }


def show(ref: Ref, search: list[Path], project_dir: Path | None = None) -> dict:
    """One version, read-only: manifest, hash and files, with the text of text files (capped).

    `registry show --json` prints this dict: ref, kind, name, version, description, root,
    root_kind, path, sha256, manifest, omitted (the version's text budget ran out), and files[]:
    {path, type: "dir", stripped} or {path, type: "file", size, executable, sha256, binary,
    truncated, omitted, text, stripped}. `text` is null for binary or omitted files."""
    hit = resolve(ref, search)
    files = [_file_view(item) for item in _read_version(hit.path)]
    manifest = _clean_value(read_manifest(hit.path, ref))
    description = manifest.get("description")
    return {
        "ref": str(ref),
        "kind": ref.kind,
        "name": ref.name,
        "version": ref.version,
        "description": description if isinstance(description, str) else "",
        "root": clean(str(hit.root), name=True)[0],
        "root_kind": root_kind(hit.root, project_dir),
        "path": clean(str(hit.path), name=True)[0],
        "sha256": copy_and_hash(hit.path),
        "manifest": manifest,
        "omitted": any(f.get("omitted") for f in files),
        "files": files,
    }
