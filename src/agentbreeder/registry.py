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

import bisect
import collections
import contextlib
import difflib
import errno
import functools
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from agentbreeder.strict_yaml import StrictYamlError, load_file, load_text

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
    """`[CODE] message`. Names from registry content are cleaned where they are interpolated
    (`_shown`); the whole message is cleaned again here, since parser errors can quote file
    content, so no message can carry terminal escapes or bidi controls (S-27)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {clean(message)[0]}")
        self.code = code


def _shown(value) -> str:
    """A path or name for an error message: one line, no control or bidi characters."""
    return clean(str(value), name=True)[0]


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
            "CONTENT_MANIFEST_MISSING", f"cannot read the shipped content manifest {_shown(path)}"
        ) from error
    if not valid:
        raise RegistryError(
            "CONTENT_MANIFEST_MISSING", f"malformed content manifest {_shown(path)}"
        )
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
        raise _mismatch(f"not in the shipped manifest: {_shown(relative)}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise _mismatch(f"differs from the shipped manifest: {_shown(relative)}")
    if bool(info.st_mode & 0o111) != entry["exec"]:
        raise _mismatch(f"exec bit differs from the manifest: {_shown(relative)}")


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
            raise _mismatch(f"not in the shipped manifest: {_shown(relative)}/")


def _unreadable(error: OSError) -> RegistryError:
    where = _shown(error.filename) if error.filename else "a registry entry"
    return RegistryError("ENTRY_UNSAFE", f"cannot read {where}: {error.strerror or error}")


def _entries(source: Path) -> list[tuple[str, Path, os.stat_result]]:
    """Every entry under `source`, validated, sorted by POSIX relative path.

    Files in the installed package's shipped trees may be hardlinked (installer caches) and are
    instead compared with `_content.json`; every other root refuses hardlinks."""
    found = []
    total = 0
    shipped = _shipped_prefix(source)
    manifest = _shipped_manifest(_PACKAGED_ROOT.resolve()) if shipped is not None else {}

    def unreadable(error: OSError):
        # os.walk skips a directory it cannot list unless told otherwise: never a silent gap.
        raise _unreadable(error)

    for directory, dirnames, filenames in os.walk(source, onerror=unreadable, followlinks=False):
        # Interpreter caches are never part of a version: skipped for hashing and copying.
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        filenames = [f for f in filenames if not f.endswith((".pyc", ".pyo"))]
        for name in sorted(dirnames + filenames):
            _safe_name(name)
            path = Path(directory, name)
            try:
                info = path.lstat()
            except OSError as error:
                raise _unreadable(error) from error
            relative = path.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode):
                raise RegistryError(
                    "ENTRY_UNSAFE", f"symlinks are not allowed: {_shown(relative)}"
                )
            if stat.S_ISREG(info.st_mode):
                if shipped is not None:
                    _check_shipped(manifest, shipped, relative, path, info)
                elif info.st_nlink > 1:
                    raise RegistryError(
                        "ENTRY_UNSAFE", f"hardlinks are not allowed: {_shown(relative)}"
                    )
                total += info.st_size
            elif not stat.S_ISDIR(info.st_mode):
                raise RegistryError(
                    "ENTRY_UNSAFE", f"only files and directories: {_shown(relative)}"
                )
            found.append((relative, path, info))
            if len(found) > MAX_FILES or total > MAX_BYTES:
                raise RegistryError("ENTRY_UNSAFE", "registry version is too large")
    if shipped is not None:
        _check_shipped_complete(manifest, shipped, found)
    return sorted(found, key=lambda item: item[0])


_HASH_HEADER = b"agentbreeder-registry-v1\0"


def _frame(data: bytes) -> bytes:
    return len(data).to_bytes(8, "big") + data


def copy_and_hash(source: Path, destination: Path | None = None) -> str:
    """Hash a version directory, copying it to `destination` first when given (and hashing the
    copy, so the hashed bytes are the built bytes). Returns `sha256:<hex>`."""
    if source.is_symlink() or not source.is_dir():
        raise RegistryError("ENTRY_UNSAFE", f"{_shown(source)} must be a real directory")
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
                    raise RegistryError(
                        "ENTRY_UNSAFE", f"changed while copying: {_shown(relative)}"
                    )
                with open(target, "xb") as writer:
                    shutil.copyfileobj(reader, writer)
            os.chmod(target, 0o755 if info.st_mode & 0o111 else 0o644)
        source = destination
    digest = hashlib.sha256(_HASH_HEADER)  # _read_version frames records the same way
    for relative, path, info in _entries(source):
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"D" + _frame(relative.encode()))
            continue
        content = path.read_bytes()
        executable = b"x" if info.st_mode & 0o111 else b"-"
        digest.update(b"F" + _frame(relative.encode()) + executable + _frame(content))
    return "sha256:" + digest.hexdigest()


MANIFEST_LIMIT = 64 * 1024


def read_manifest(version_dir: Path, ref: Ref | None = None, text: str | None = None) -> dict:
    """The version's manifest.yaml, checked against `ref`; parsed from `text` when the caller
    already read the file (so what is checked is what is shown)."""
    try:
        if text is None:
            data = load_file(version_dir / "manifest.yaml", limit=MANIFEST_LIMIT)
        elif len(text.encode()) > MANIFEST_LIMIT:
            raise StrictYamlError(f"manifest.yaml exceeds the {MANIFEST_LIMIT // 1024} KiB limit")
        else:
            data = load_text(text)
    except StrictYamlError as error:
        raise RegistryError("MANIFEST_INVALID", f"{_shown(version_dir)}: {error}") from error
    try:
        declared = Ref.parse(f"{data.get('kind')}/{data.get('name')}@{data.get('version')}")
    except RegistryError as error:
        raise RegistryError(
            "MANIFEST_INVALID",
            f"{_shown(version_dir)}/manifest.yaml needs kind, name and X.Y.Z version",
        ) from error
    if ref is not None and declared != ref:
        raise RegistryError(
            "MANIFEST_INVALID", f"{_shown(version_dir)} declares {declared}, expected {ref}"
        )
    return data


def resolve(ref: Ref, search: list[Path]) -> Resolved:
    hits = []
    for root in search:
        candidate = root / ref.relative()
        # The root itself may be a link (e.g. an AGENTBREEDER_REGISTRY_PATH entry); nothing
        # below it may be, or a version would be read from outside the root it is shown under.
        for part in (root / ref.kind, root / ref.kind / ref.name, candidate):
            if part.is_symlink():
                raise RegistryError("ENTRY_UNSAFE", f"{_shown(part)} must not be a symlink")
        if candidate.is_dir():
            hits.append(Resolved(ref, candidate, root))
    if not hits:
        where = ", ".join(_shown(r) for r in search)
        raise RegistryError("REF_NOT_FOUND", f"{ref} not found in {where}")
    if len(hits) > 1 and len({copy_and_hash(hit.path) for hit in hits}) > 1:
        raise RegistryError(
            "REF_CONFLICT",
            f"{ref} exists with different content in {_shown(hits[0].root)} and "
            f"{_shown(hits[1].root)}",
        )
    read_manifest(hits[0].path, ref)
    return hits[0]


# Kinds whose versions are code that runs inside agents (or is the agent server): the console asks
# for the "this code runs in agents" confirm before importing one (S-05). Prompts and knowledge
# are text that steers the agent.
CODE_KINDS = frozenset({"tool", "service", "eval", "mcp", "runtime"})
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def runs_code(kind: str) -> bool:
    return kind in CODE_KINDS


def _staged_manifest(source: Path, staged: Path) -> dict:
    """The manifest parsed from the staged copy's bytes (what is written is what is checked);
    errors name `source`, never the private staging directory."""
    where = f"{_shown(source)}/manifest.yaml"
    path = staged / "manifest.yaml"
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise RegistryError("MANIFEST_INVALID", f"{where} is missing") from None
    if not stat.S_ISREG(info.st_mode):
        raise RegistryError("MANIFEST_INVALID", f"{where} is missing")
    if info.st_size > MANIFEST_LIMIT:
        raise RegistryError("MANIFEST_INVALID", f"{where} exceeds the 64 KiB limit")
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise RegistryError("MANIFEST_INVALID", f"{where} is not UTF-8 text") from None
    return read_manifest(source, text=text)


_TOP_KEY = re.compile(r"^(?P<key>name|version):(?P<value>[^\r\n]*)(?=\r?$)", re.MULTILINE)


def _renamed_manifest(source: Path, staged: Path, data: dict, ref: Ref) -> None:
    """Rewrite only the top-level `name:` and `version:` lines of the staged manifest, so every
    other byte (comments, other values) is kept; fail closed unless re-parsing gives exactly
    the old fields with the new name and version."""
    path = staged / "manifest.yaml"
    with path.open(encoding="utf-8", newline="") as handle:  # line endings kept as written
        text = handle.read()  # (Path.read_text has no newline= before Python 3.13)
    values = {"name": ref.name, "version": ref.version}
    found = [match["key"] for match in _TOP_KEY.finditer(text)]
    if sorted(found) != ["name", "version"]:
        raise RegistryError(
            "MANIFEST_INVALID",
            f"{_shown(source)}/manifest.yaml: to import under another name or version, write "
            "`name:` and `version:` once each as plain top-level lines",
        )
    rewritten = _TOP_KEY.sub(lambda m: f'{m["key"]}: "{values[m["key"]]}"', text)
    path.write_text(rewritten, encoding="utf-8", newline="")
    if _staged_manifest(source, staged) != {**data, **values}:
        raise RegistryError(
            "MANIFEST_INVALID",
            f"{_shown(source)}/manifest.yaml: cannot rename it safely; write `name:` and "
            "`version:` as plain one-line top-level values",
        )


@contextlib.contextmanager
def _as_source(staged: Path, source: Path):
    """An OS error on the staged copy names the matching source path, never the staging dir."""
    try:
        yield
    except OSError as error:
        if error.filename and Path(error.filename).is_relative_to(staged):
            error.filename = str(source / Path(error.filename).relative_to(staged))
        raise


def _check_target(root: Path, ref: Ref) -> Path:
    """`<root>/<kind>/<name>/<version>`, refused when it exists or when a directory on the way
    is a symlink (a write would land outside the root it is shown under)."""
    for part in (root / ref.kind, root / ref.kind / ref.name):
        if part.is_symlink():
            raise RegistryError("ENTRY_UNSAFE", f"{_shown(part)} must not be a symlink")
    target = root / ref.relative()
    if target.exists() or target.is_symlink():
        raise RegistryError(
            "VERSION_EXISTS", f"{ref} is already published at {_shown(target)}; bump the version"
        )
    return target


def place(
    source: Path,
    root: Path,
    *,
    name: str | None = None,
    version: str | None = None,
    expect_digest: str | None = None,
    dry_run: bool = False,
    search: list[Path] | None = None,
    project_dir: Path | None = None,
    source_ref: Ref | None = None,
    edit=None,
    context: int = 3,
) -> dict:
    """Copy a version directory into a registry root as an immutable version: `publish`,
    `import`, `fork` and `edit`. `name`/`version` rename it (the staged manifest is rewritten).

    The source is copied once into a private staging directory and every check runs on that
    copy: the entry checks (`_entries`), the manifest, and the hash. With `dry_run`, nothing is
    written and the result carries the staged files (as `show`); its `sha256` is what a later
    call passes as `expect_digest`, which refuses unless the new staged copy hashes the same
    (S-04: the bytes written are the bytes reviewed). `search` adds a REF_CONFLICT check against
    the other roots, so the new version resolves. `source_ref` refuses a staged copy whose
    manifest declares another ref (`fork`: the source changed after it was resolved).

    `edit(staged)` (`registry edit`) changes the private staged copy before any check; the result
    then carries `diff` (as `registry diff --json`) from the copy as staged to the edited one,
    and an edit that changes nothing is refused (EDIT_UNCHANGED)."""
    if expect_digest is not None and not DIGEST.fullmatch(expect_digest):
        raise RegistryError("DIGEST_INVALID", "--expect-digest must be sha256:<64 hex digits>")
    if trusted_root(root):
        raise RegistryError(
            "REGISTRY_READ_ONLY",
            f"{_shown(root)} is shipped inside AgentBreeder and checked against its content "
            "manifest; add the version directory by hand, git add it, run "
            "scripts/content_manifest.py and record it in tests/fixtures/builtin_hashes.json",
        )
    if source.is_symlink() or not source.is_dir():
        raise RegistryError("ENTRY_UNSAFE", f"{_shown(source)} must be a real directory")
    if root.resolve().is_relative_to(source.resolve()):
        # A write stages inside the root, so the copy would contain itself.
        raise RegistryError(
            "ENTRY_UNSAFE",
            f"the registry root {_shown(root)} is inside the source {_shown(source)}; "
            "publish a version directory, not the project",
        )
    if dry_run:
        staging = Path(tempfile.mkdtemp(prefix="agentbreeder-import-"))
    else:
        root.mkdir(parents=True, exist_ok=True)
        # In the root, so the final rename stays on one filesystem; not listed (not a kind).
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
    staged = staging / "v"
    try:
        with _reads(), _as_source(staged, source):
            copy_and_hash(source, staged)
            if edit is not None:
                pristine, pristine_digest = _read_version(staged)
                edit(staged)
                if copy_and_hash(staged) == pristine_digest:
                    raise RegistryError(
                        "EDIT_UNCHANGED", f"the edit leaves {_shown(source)} unchanged"
                    )
            data = _staged_manifest(source, staged)
            original = Ref.parse(f"{data['kind']}/{data['name']}@{data['version']}")
            if source_ref is not None and original != source_ref:
                raise RegistryError(
                    "MANIFEST_INVALID",
                    f"{_shown(source)} now declares {original}, not {source_ref}; try again",
                )
            ref = Ref.parse(
                f"{original.kind}/{original.name if name is None else name}"
                f"@{original.version if version is None else version}"
            )
            if ref != original:
                _renamed_manifest(source, staged, data, ref)
            items, digest = _read_version(staged)
            if edit is not None:
                _check_kind(ref.kind, staged)
                changed = {
                    "from": {"ref": str(original), "sha256": pristine_digest},
                    "to": {"ref": str(ref), "sha256": digest},
                    "context": context,
                    "identical": False,
                    "files": _changes(
                        {i.relative: i for i in pristine}, {i.relative: i for i in items}, context
                    ),
                }
        if expect_digest is not None and digest != expect_digest:
            raise RegistryError(
                "DIGEST_MISMATCH",
                f"{ref} would be written as {digest}, not the reviewed {expect_digest}: the "
                "source changed after the preview; review it again",
            )
        target = _check_target(root, ref)
        for other in search or []:
            if other == root:
                continue
            # As `resolve`: a symlinked kind or name directory fails every ref under it.
            for part in (other / ref.kind, other / ref.kind / ref.name, other / ref.relative()):
                if part.is_symlink():
                    raise RegistryError(
                        "ENTRY_UNSAFE",
                        f"{_shown(part)} is a symlink, so {ref} would not resolve; fix that root",
                    )
            candidate = other / ref.relative()
            if candidate.is_dir() and copy_and_hash(candidate) != digest:
                raise RegistryError(
                    "REF_CONFLICT",
                    f"{ref} already exists with different content in {_shown(other)}; "
                    "choose another name or version",
                )
        if not dry_run:
            with _reads():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.rename(staged, target)
                except OSError as error:
                    if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                        raise RegistryError(
                            "ENTRY_UNSAFE",
                            f"cannot write {_shown(target)}: {error.strerror or error}",
                        ) from error
                    # Published meanwhile (another tab, another terminal).
                    raise RegistryError(
                        "VERSION_EXISTS", f"{ref} was published at {_shown(target)} meanwhile"
                    ) from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result = {
        "ref": str(ref),
        "kind": ref.kind,
        "name": ref.name,
        "version": ref.version,
        "source_ref": str(original),
        "sha256": digest,
        "source": _absolute(source),
        "root": _absolute(root),  # resolve() also works for a root a dry run did not create
        "root_kind": root_kind(root, project_dir),
        "path": _absolute(target),
        "dry_run": dry_run,
        "runs_code": runs_code(ref.kind),
    }
    if dry_run:
        result["files"] = [_file_view(item) for item in items]
    if edit is not None:
        result["diff"] = changed
    return result


def publish(source: Path, root: Path) -> tuple[Ref, str, Path]:
    """Copy a prepared version directory into a registry root. Versions are immutable."""
    result = place(source, root)
    ref = Ref.parse(result["ref"])
    return ref, result["sha256"], root / ref.relative()


def import_version(
    source: Path,
    project_dir: Path,
    *,
    name: str | None = None,
    version: str | None = None,
    expect_digest: str | None = None,
    dry_run: bool = False,
) -> dict:
    """`registry import`: a version directory from anywhere into the project's `./registry`,
    with `publish`'s checks plus a conflict check against the other search roots."""
    return place(
        source,
        project_dir / "registry",
        name=name,
        version=version,
        expect_digest=expect_digest,
        dry_run=dry_run,
        search=roots(project_dir),
        project_dir=project_dir,
    )


def fork_version(
    ref: Ref,
    project_dir: Path,
    *,
    name: str | None = None,
    version: str | None = None,
    expect_digest: str | None = None,
    dry_run: bool = False,
) -> dict:
    """`registry fork`: copy a resolvable version (built-in, an AGENTBREEDER_REGISTRY_PATH
    entry, or the project itself) into the project's `./registry` under a new name or version,
    which the project then owns: change a copy of it and `publish` a bump (console §8.4).

    An unchanged ref is refused: an identical project copy adds nothing, and once edited in
    place it would conflict with the original (REF_CONFLICT) instead of being a new version."""
    search = roots(project_dir)
    hit = resolve(ref, search)
    if (name is None or name == ref.name) and (version is None or version == ref.version):
        raise RegistryError(
            "FORK_UNCHANGED",
            f"forking {ref} unchanged would only copy it; pass --as NAME or --version X.Y.Z "
            "to fork it as a new version the project owns",
        )
    return place(
        hit.path,
        project_dir / "registry",
        name=name,
        version=version,
        expect_digest=expect_digest,
        dry_run=dry_run,
        search=search,
        project_dir=project_dir,
        source_ref=ref,
    )


BUMPS = ("patch", "minor", "major")


def bumped(version: str, part: str) -> str:
    """`version` with its `part` (patch, minor or major) raised and the lower parts zeroed."""
    if not VERSION.fullmatch(version) or part not in BUMPS:
        raise RegistryError("VERSION_INVALID", f"cannot bump {_shown(version)} by {_shown(part)}")
    major, minor, patch = (int(n) for n in version.split("."))
    raised = {
        "patch": (major, minor, patch + 1),
        "minor": (major, minor + 1, 0),
        "major": (major + 1, 0, 0),
    }[part]
    result = ".".join(str(n) for n in raised)
    if not VERSION.fullmatch(result):
        raise RegistryError("VERSION_INVALID", f"{_shown(version)} cannot be bumped further")
    return result


def edit_path(text: str) -> str:
    """A file path inside a version for `registry edit`: relative, POSIX, every part a safe
    name, never the manifest (the edit rewrites only its `version:`) or an interpreter cache
    (which a version never contains)."""
    parts = text.split("/") if isinstance(text, str) else [""]
    for part in parts:
        _safe_name(part)
    if text.casefold() == "manifest.yaml":  # macOS volumes ignore case
        raise RegistryError(
            "EDIT_INVALID",
            "manifest.yaml is not edited: the edit rewrites only its `version:` line; to change "
            "other fields, copy the version, edit it and `registry publish` it",
        )
    if "__pycache__" in parts or text.endswith((".pyc", ".pyo")):
        raise RegistryError("EDIT_INVALID", f"{_shown(text)} is an interpreter cache file")
    return text


def _apply_edits(staged: Path, sets: list[tuple[str, bytes]], removes: list[str]) -> None:
    """Write `sets` (path, new bytes) and delete `removes` in the private staged copy. A set
    path's parents are created; a set keeps an existing file's executable bit (a new file is
    not executable); a removed path must be a file."""
    paths = [path for path, _ in sets] + list(removes)
    if not paths:
        raise RegistryError("EDIT_INVALID", "nothing to change: pass --set PATH FILE or --remove")
    if len(set(paths)) != len(paths):
        raise RegistryError("EDIT_INVALID", "each path may be set or removed only once")
    for path in paths:
        edit_path(path)
    manifest = staged / "manifest.yaml"
    for path in paths:
        # Another spelling of the manifest on a case-insensitive or normalising file system.
        target = staged / path
        if target.exists() and not target.is_symlink() and target.samefile(manifest):
            raise RegistryError("EDIT_INVALID", f"{_shown(path)} is manifest.yaml; not edited")
    for path in removes:
        target = staged / path
        if target.is_symlink() or not target.is_file():
            raise RegistryError("EDIT_INVALID", f"cannot remove {_shown(path)}: no such file")
        target.unlink()
        for parent in Path(path).parents[:-1]:  # directories the removal left empty
            directory = staged / parent
            if any(directory.iterdir()):
                break
            directory.rmdir()
    for path, content in sets:
        if len(content) > MAX_BYTES:
            raise RegistryError("ENTRY_UNSAFE", "registry version is too large")
        target = staged / path
        for parent in reversed(Path(path).parents[:-1]):
            directory = staged / parent
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise RegistryError(
                    "EDIT_INVALID", f"cannot set {_shown(path)}: {_shown(parent)} is a file"
                )
            directory.mkdir(exist_ok=True)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RegistryError("EDIT_INVALID", f"cannot set {_shown(path)}: it is a directory")
        mode = 0o755 if target.exists() and target.stat().st_mode & 0o111 else 0o644
        target.write_bytes(content)
        os.chmod(target, mode)


def _check_kind(kind: str, staged: Path) -> None:
    """Kind-specific checks an edit must pass before it is written: an eval set must still
    load as `release` loads it."""
    if kind != "eval":
        return
    from agentbreeder.evals import EvalError, load_eval_set  # evals imports this module

    try:
        load_eval_set(staged)
    except OSError as error:  # never str(error): it names the private staging directory
        raise RegistryError(
            "EVAL_INVALID", f"the edited eval set does not load: {error.strerror or 'read error'}"
        ) from None
    except (EvalError, UnicodeDecodeError) as error:
        message = str(error).removeprefix("[EVAL_INVALID] ").replace(str(staged), "<version>")
        raise RegistryError(
            "EVAL_INVALID", f"the edited eval set does not load: {message}"
        ) from None


def edit_version(
    ref: Ref,
    project_dir: Path,
    *,
    sets: list[tuple[str, bytes]] = (),
    removes: list[str] = (),
    bump: str = "patch",
    version: str | None = None,
    expect_digest: str | None = None,
    dry_run: bool = False,
    context: int = 3,
) -> dict:
    """`registry edit` (console §8.4): copy a project version into a private staging directory,
    apply `sets` and `removes`, raise its version (`bump`, or exactly `version`) and publish it
    into the project's `./registry` with `place`'s checks, atomically. The bumped version
    already existing, or appearing meanwhile (another tab), is VERSION_EXISTS.

    Only project versions are edited: a built-in or AGENTBREEDER_REGISTRY_PATH version is forked
    first (`registry fork`). `--dry-run` returns the digest a later call passes as
    `expect_digest` (S-04), and both return `diff` from REF to the new version."""
    search = roots(project_dir)
    hit = resolve(ref, search)
    root = project_dir / "registry"
    if hit.root.resolve() != root.resolve():
        raise RegistryError(
            "EDIT_NOT_PROJECT",
            f"{ref} is in {_shown(hit.root)} ({root_kind(hit.root, project_dir)}), not the "
            "project registry; fork it first: registry fork "
            f"{ref} --version {bumped(ref.version, 'patch')}",
        )
    new_version = version if version is not None else bumped(ref.version, bump)
    if not VERSION.fullmatch(new_version):
        raise RegistryError("VERSION_INVALID", f"{_shown(new_version)} is not X.Y.Z")
    if new_version == ref.version:
        raise RegistryError("VERSION_EXISTS", f"{ref} is published; edit it as a new version")
    if tuple(map(int, new_version.split("."))) < tuple(map(int, ref.version.split("."))):
        raise RegistryError(
            "VERSION_INVALID", f"an edit raises the version: {new_version} is below {ref.version}"
        )
    return place(
        hit.path,
        root,
        version=new_version,
        expect_digest=expect_digest,
        dry_run=dry_run,
        search=search,
        project_dir=project_dir,
        source_ref=ref,
        edit=lambda staged: _apply_edits(staged, list(sets), list(removes)),
        context=context,
    )


def _links(root: Path) -> list[tuple[str, Path]]:
    """Symlinked (or dangling) `<root>/<kind>` and `<root>/<kind>/<name>` directories, which
    `resolve` refuses: (ref pattern, path). The root itself may be a link."""
    found = []
    for kind in KINDS:
        directory = root / kind
        if directory.is_symlink():
            found.append((f"{kind}/*", directory))
            continue
        try:
            children = sorted(directory.iterdir()) if directory.is_dir() else []
        except OSError:
            continue  # unreadable: reading any version under it reports the error
        found += [(f"{kind}/{_shown(c.name)}@*", c) for c in children if c.is_symlink()]
    return found


def listing(search: list[Path], prefix: str = "") -> list[dict]:
    """Every version in the search roots. `symlinked` marks one under a symlinked kind or name
    directory: it is listed, but `resolve` (build, show, services up) refuses it."""
    rows = []
    for root in search:
        linked = {path for _, path in _links(root)}
        for kind in KINDS:
            for version_dir in sorted((root / kind).glob("*/*")) if (root / kind).is_dir() else []:
                text = f"{kind}/{version_dir.parent.name}@{version_dir.name}"
                if not text.startswith(prefix) or not REF.fullmatch(text):
                    continue
                symlinked = bool({root / kind, version_dir.parent} & linked)
                rows.append(
                    {
                        "ref": text,
                        "root": str(root),
                        "path": str(version_dir),
                        "symlinked": symlinked,
                    }
                )
    return rows


def verify(search: list[Path]) -> list[dict]:
    """Re-check every version: safe entries, manifest matches its path. Returns problems.
    A symlinked kind or name directory is a problem too: every ref under it fails to resolve,
    even when a later root has the version (fail closed)."""
    problems = []
    for root in search:
        for pattern, path in _links(root):
            problems.append(
                {
                    "ref": pattern,
                    "path": str(path),
                    "error": str(
                        RegistryError(
                            "ENTRY_UNSAFE",
                            f"{_shown(path)} must not be a symlink; every {pattern} ref fails",
                        )
                    ),
                }
            )
    for row in listing(search):
        if row.pop("symlinked"):
            continue  # reported above
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


def _span(first: int, last: int | None = None) -> str:
    """A regex class item for code points (kept out of the source as literal characters)."""
    return chr(first) if last is None else f"{chr(first)}-{chr(last)}"


# Bidi controls, line/paragraph separators, invisible format characters (zero-width, word
# joiner, BOM, tags) and lone surrogates, which YAML can produce and which cannot be encoded.
_BIDI = _span(0x061C) + _span(0x200E, 0x200F) + _span(0x202A, 0x202E) + _span(0x2066, 0x2069)
_SEPARATORS = _span(0x2028, 0x2029)
_INVISIBLE = _span(0x200B, 0x200D) + _span(0x2060) + _span(0xFEFF) + _span(0xE0000, 0xE007F)
_SURROGATES = _span(0xD800, 0xDFFF)
_STRIP_NAME = re.compile(
    f"[{_span(0x00, 0x1F)}{_span(0x7F, 0x9F)}{_BIDI}{_SEPARATORS}{_INVISIBLE}{_SURROGATES}]"
)
# Content keeps tab and newline, and zero-width joiners (emoji, scripts that need them).
_STRIP_TEXT = re.compile(
    f"[{_span(0x00, 0x08)}{_span(0x0B, 0x1F)}{_span(0x7F, 0x9F)}{_BIDI}{_SEPARATORS}{_SURROGATES}]"
)


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


def _read_one(
    relative: str, path: Path, info: os.stat_result, version, limit: int
) -> tuple[int, bool, str, bytes]:
    """Size, executable bit, sha256 and the first `limit` bytes of one file, opened
    with O_NOFOLLOW and refused if it is no longer the regular file `_entries` checked or
    changes size while read. The same bytes are framed into `version` exactly as
    `copy_and_hash` frames them, so the version hash shown is the hash of what is shown."""
    digest = hashlib.sha256()
    head = bytearray()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as reader:
        now = os.fstat(reader.fileno())
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
            raise RegistryError("ENTRY_UNSAFE", f"changed while reading: {_shown(relative)}")
        if now.st_size > MAX_BYTES:
            raise RegistryError("ENTRY_UNSAFE", "registry version is too large")
        executable = bool(now.st_mode & 0o111)
        version.update(
            b"F"
            + _frame(relative.encode())
            + (b"x" if executable else b"-")
            # _frame(content), streamed: the length first, then the bytes
            + now.st_size.to_bytes(8, "big")
        )
        while chunk := reader.read(1024 * 1024):
            size += len(chunk)
            if size > now.st_size:
                break
            digest.update(chunk)
            version.update(chunk)
            if len(head) < limit:
                head += chunk[: limit - len(head)]
    if size != now.st_size:
        raise RegistryError("ENTRY_UNSAFE", f"changed while reading: {_shown(relative)}")
    return size, executable, "sha256:" + digest.hexdigest(), bytes(head)


def _read_version(path: Path) -> tuple[list[_File], str]:
    """Every entry of a version directory, validated by `_entries`, in path order, and the
    version hash (as `copy_and_hash`) of exactly the bytes read."""
    budget = SHOW_TOTAL_LIMIT
    result = []
    version = hashlib.sha256(_HASH_HEADER)
    for relative, file_path, info in _entries(path):
        if stat.S_ISDIR(info.st_mode):
            version.update(b"D" + _frame(relative.encode()))
            result.append(_File(relative, "dir"))
            continue
        # The manifest is always parsed from these bytes, so it is read and kept in full.
        manifest = relative == "manifest.yaml"
        limit = max(SHOW_FILE_LIMIT, MANIFEST_LIMIT) if manifest else SHOW_FILE_LIMIT
        size, executable, digest, head = _read_one(relative, file_path, info, version, limit)
        keep = manifest or len(head) <= budget
        if keep:
            budget -= min(len(head), budget)
        kept = head if keep else None
        result.append(_File(relative, "file", size, executable, digest, kept, size > len(head)))
    return result, "sha256:" + version.hexdigest()


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


@contextlib.contextmanager
def _reads():
    """OS errors (permissions, a file removed or replaced mid-read, ELOOP) and parser recursion
    limits become registry errors instead of tracebacks."""
    try:
        yield
    except OSError as error:
        raise _unreadable(error) from error
    except RecursionError as error:
        raise RegistryError("ENTRY_UNSAFE", "too deeply nested to read") from error


def _absolute(path: Path) -> str:
    """A stable absolute path for JSON (the console keys on it), cleaned for display."""
    return _shown(path.resolve())


def _read_checked(ref: Ref, hit: Resolved) -> tuple[list[_File], str, dict]:
    """A resolved version's entries, its hash and its manifest, all from one read: the hash
    and the manifest describe exactly the bytes that are shown."""
    items, digest = _read_version(hit.path)
    entry = next((i for i in items if i.relative == "manifest.yaml" and i.type == "file"), None)
    where = f"{_shown(hit.path)}/manifest.yaml"
    if entry is None or entry.head is None:
        raise RegistryError("MANIFEST_INVALID", f"{where} is missing")
    if entry.size > MANIFEST_LIMIT:
        raise RegistryError("MANIFEST_INVALID", f"{where} exceeds the 64 KiB limit")
    text = _decode(entry.head, entry.truncated)
    if text is None:
        raise RegistryError("MANIFEST_INVALID", f"{where} is not UTF-8 text")
    return items, digest, read_manifest(hit.path, ref, text)


def show(ref: Ref, search: list[Path], project_dir: Path | None = None) -> dict:
    """One version, read-only: manifest, hash and files, with the text of text files (capped).

    `registry show --json` prints this dict: ref, kind, name, version, description, root,
    root_kind, path, sha256, manifest, omitted (the version's text budget ran out), and files[]:
    {path, type: "dir", stripped} or {path, type: "file", size, executable, sha256, binary,
    truncated, omitted, text, stripped}. `text` is null for binary or omitted files."""
    with _reads():
        hit = resolve(ref, search)
        items, digest, data = _read_checked(ref, hit)
    files = [_file_view(item) for item in items]
    manifest = _clean_value(data)
    description = manifest.get("description")
    return {
        "ref": str(ref),
        "kind": ref.kind,
        "name": ref.name,
        "version": ref.version,
        "description": description if isinstance(description, str) else "",
        "root": _absolute(hit.root),
        "root_kind": root_kind(hit.root, project_dir),
        "path": _absolute(hit.path),
        "sha256": digest,
        "manifest": manifest,
        "omitted": any(f.get("omitted") for f in files),
        "files": files,
    }


MAX_CONTEXT = 1000


def _range(start: int, stop: int) -> tuple[int, int]:
    """A hunk side as diff(1) prints it: 1-based start, line count (an empty side starts at the
    line before)."""
    length = stop - start
    return (start + 1 if length else start), length


def _line(op: str, text: str) -> dict:
    if text.endswith("\n"):
        return {"op": op, "text": text[:-1]}
    return {"op": op, "text": text, "no_newline": True}


# What one `registry diff` may do, all files together: line comparisons (about a second),
# wall-clock time, and hunk lines it outputs (far more than any prompt or eval change needs, small
# enough to keep the JSON a few MB). Once any is spent, every further changed file is reported as
# too large to show.
DIFF_BUDGET = 3_000_000
DIFF_SECONDS = 2.0
DIFF_LINES = 50_000
PATIENCE_DEPTH = 32  # nested anchor passes before a segment goes to SequenceMatcher


class _TooLarge(Exception):
    pass


class _Budget:
    """The work and output left for one diff, shared by all of its files."""

    def __init__(self) -> None:
        self.left = DIFF_BUDGET
        self.lines_left = DIFF_LINES
        self.deadline = time.monotonic() + DIFF_SECONDS

    @property
    def spent(self) -> bool:
        return self.left < 0

    def emit(self, lines: int) -> bool:
        """Whether `lines` more hunk lines fit; if not, the budget is spent for later files."""
        if self.spent or lines > self.lines_left:
            self.left = -1
            return False
        self.lines_left -= lines
        return True

    def spend(self, work: int) -> None:
        self.left -= work
        if self.left < 0 or time.monotonic() > self.deadline:
            self.left = -1
            raise _TooLarge


class _Bounded(difflib.SequenceMatcher):
    """SequenceMatcher (no autojunk) that charges every `find_longest_match` call to the diff's
    budget. Each call scans, for every line of its `a` range, that line's occurrences in `b`, so
    the cost of a call is known before it runs (prefix sums). This bounds the quadratic cases:
    repeated lines, and recursion over many small changes."""

    def __init__(self, before: list[str], after: list[str], budget: _Budget):
        budget.spend(len(before) + len(after) + 1)  # building the indexes
        super().__init__(None, before, after, autojunk=False)
        occurrences = collections.Counter(after)
        self._prefix = [0]
        for line in before:
            self._prefix.append(self._prefix[-1] + occurrences[line] + 1)
        self._budget = budget

    def find_longest_match(self, alo=0, ahi=None, blo=0, bhi=None):
        ahi = len(self.a) if ahi is None else ahi
        self._budget.spend(self._prefix[ahi] - self._prefix[alo] + 1)
        return super().find_longest_match(alo, ahi, blo, bhi)


def _unique_anchors(a, b, alo, ahi, blo, bhi, budget: _Budget) -> list[tuple[int, int]]:
    """Patience anchors: lines that occur exactly once in a[alo:ahi] and once in b[blo:bhi],
    reduced to the longest run that keeps their order on both sides."""
    budget.spend((ahi - alo) + (bhi - blo) + 1)
    count_a = collections.Counter(a[alo:ahi])
    count_b = collections.Counter(b[blo:bhi])
    where_b = {b[j]: j for j in range(blo, bhi) if count_b[b[j]] == 1}
    pairs = [(i, where_b[a[i]]) for i in range(alo, ahi) if count_a[a[i]] == 1 and a[i] in where_b]
    tails: list[int] = []  # smallest j ending an increasing run of each length
    ends: list[int] = []  # index in `pairs` of that run's last pair
    previous: list[int | None] = []
    for index, (_, j) in enumerate(pairs):
        position = bisect.bisect_left(tails, j)
        if position == len(tails):
            tails.append(j)
            ends.append(index)
        else:
            tails[position] = j
            ends[position] = index
        previous.append(ends[position - 1] if position else None)
    chain = []
    index = ends[-1] if ends else None
    while index is not None:
        chain.append(pairs[index])
        index = previous[index]
    return chain[::-1]


def _match(a, b, alo, ahi, blo, bhi, budget: _Budget, depth: int, out: list) -> None:
    """Opcodes for a[alo:ahi] against b[blo:bhi], appended to `out`: the common start and end,
    then the region between them (`_match_middle`)."""
    start = 0
    while alo + start < ahi and blo + start < bhi and a[alo + start] == b[blo + start]:
        start += 1
    end = 0
    while (
        ahi - end > alo + start
        and bhi - end > blo + start
        and a[ahi - 1 - end] == b[bhi - 1 - end]
    ):
        end += 1
    budget.spend(start + end + 1)
    if start:
        out.append(("equal", alo, alo + start, blo, blo + start))
    _match_middle(a, b, alo + start, ahi - end, blo + start, bhi - end, budget, depth, out)
    if end:
        out.append(("equal", ahi - end, ahi, bhi - end, bhi))


def _match_middle(a, b, alo, ahi, blo, bhi, budget: _Budget, depth: int, out: list) -> None:
    """Patience-style: anchor on lines unique to both sides and match the segments between
    them; a segment with no such line goes to the budgeted SequenceMatcher."""
    if alo == ahi and blo == bhi:
        return
    if alo == ahi or blo == bhi:
        out.append(("insert" if alo == ahi else "delete", alo, ahi, blo, bhi))
        return
    anchors = _unique_anchors(a, b, alo, ahi, blo, bhi, budget) if depth < PATIENCE_DEPTH else []
    if not anchors:
        matcher = _Bounded(a[alo:ahi], b[blo:bhi], budget)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            out.append((tag, i1 + alo, i2 + alo, j1 + blo, j2 + blo))
        return
    i0, j0 = alo, blo
    for i, j in anchors:
        _match(a, b, i0, i, j0, j, budget, depth + 1, out)
        out.append(("equal", i, i + 1, j, j + 1))
        i0, j0 = i + 1, j + 1
    _match(a, b, i0, ahi, j0, bhi, budget, depth + 1, out)


def _merge(codes: list[tuple]) -> list[tuple]:
    """Join neighbouring opcodes: equal with equal, and any changes into one replace (or insert
    or delete when one side is empty), as SequenceMatcher would report them."""
    merged: list[list] = []
    for tag, i1, i2, j1, j2 in codes:
        if i1 == i2 and j1 == j2:
            continue
        changed = tag != "equal"
        if merged and (merged[-1][0] != "equal") == changed:
            merged[-1][2], merged[-1][4] = i2, j2
        else:
            merged.append([tag, i1, i2, j1, j2])
    result = []
    for tag, i1, i2, j1, j2 in merged:
        if tag != "equal":
            tag = "insert" if i1 == i2 else "delete" if j1 == j2 else "replace"
        result.append((tag, i1, i2, j1, j2))
    return result


def _opcodes(before: list[str], after: list[str], budget: _Budget) -> list[tuple] | None:
    """Opcodes for the whole files, or None when the diff's budget is spent. The common start
    and end are trimmed first (linear), lines unique to both sides anchor the rest (patience),
    and only segments without such lines go to SequenceMatcher, whose quadratic work is charged
    to the budget."""
    if budget.spent:
        return None
    out: list[tuple] = []
    try:
        _match(before, after, 0, len(before), 0, len(after), budget, 0, out)
    except _TooLarge:
        return None
    return _merge(out)


def _grouped(codes: list[tuple], context: int):
    """difflib.SequenceMatcher.get_grouped_opcodes over precomputed opcodes."""
    matcher = difflib.SequenceMatcher(None, [], [])
    matcher.opcodes = codes  # get_opcodes() returns these as they are
    return matcher.get_grouped_opcodes(context)


def _hunks(
    before: list[str], after: list[str], context: int, budget: _Budget
) -> list[dict] | None:
    """Unified-diff hunks, or None when the change is over the diff's shared budget: work
    (see `_opcodes`) or hunk lines output (DIFF_LINES)."""
    if before == after:
        return []
    codes = _opcodes(before, after, budget)
    if codes is None:
        return None
    groups = list(_grouped(codes, context))
    # Output is bounded too: count the lines the hunks would hold before building any of them.
    emitted = sum(
        (i2 - i1) + (0 if tag == "equal" else j2 - j1)
        for group in groups
        for tag, i1, i2, j1, j2 in group
    )
    if not budget.emit(emitted):
        return None
    hunks = []
    for group in groups:
        from_start, from_count = _range(group[0][1], group[-1][2])
        to_start, to_count = _range(group[0][3], group[-1][4])
        lines = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += [_line(" ", text) for text in before[i1:i2]]
                continue
            lines += [_line("-", text) for text in before[i1:i2]]
            lines += [_line("+", text) for text in after[j1:j2]]
        hunks.append(
            {
                "from_start": from_start,
                "from_count": from_count,
                "to_start": to_start,
                "to_count": to_count,
                "lines": lines,
            }
        )
    return hunks


def _side(item: _File | None) -> tuple[list[str], bool, bool]:
    """(lines, whether they can be diffed, binary) for one side of a file change."""
    if item is None:
        return [], True, False
    text, binary, _ = _text_of(item)
    if text is None or item.truncated:
        return [], False, binary
    return text.splitlines(keepends=True), True, False  # only \n is left as a line break


def _file_change(
    path: str, before: _File | None, after: _File | None, context: int, budget: _Budget
) -> dict:
    status = "added" if before is None else "removed" if after is None else "modified"
    item = after or before
    name, stripped = clean(path, name=True)
    change = {"path": name, "status": status, "type": item.type}
    if item.type == "dir":
        change["stripped"] = stripped
        return change
    old, old_ok, old_binary = _side(before)
    new, new_ok, new_binary = _side(after)
    hunks = _hunks(old, new, context, budget) if old_ok and new_ok else []
    change.update(
        {
            "from_sha256": before.sha256 if before else None,
            "to_sha256": after.sha256 if after else None,
            "executable": {
                "from": before.executable if before else None,
                "to": after.executable if after else None,
            },
            "binary": old_binary or new_binary,
            "truncated": any(i is not None and i.truncated for i in (before, after)),
            "omitted": any(i is not None and i.head is None for i in (before, after)),
            "too_large": hunks is None,  # the changed region is over the diff limits
            "hunks": hunks or [],
        }
    )
    content_stripped = any(_text_of(i)[2] for i in (before, after) if i is not None)
    change["stripped"] = stripped or content_stripped
    return change


def _only_dirs(items: dict[str, _File], keys: set[str]) -> set[str]:
    """Directories in `keys` with no file of the same version beneath them (empty folders)."""
    files = [k for k, item in items.items() if item.type == "file"]
    return {k for k in keys if not any(f.startswith(k + "/") for f in files)}


def diff(first: Ref, second: Ref, search: list[Path], context: int = 3, project_dir=None) -> dict:
    """What changes from `first` to `second`, file by file, in path order, with `context` lines
    around each change (as `diff -u`). Read like `show`: same checks, caps and stripping.

    `registry diff --json` prints this dict: from and to ({ref, sha256, root, root_kind}),
    context, identical, and files[] of changed entries: {path, status: added|removed|modified,
    type: "dir", stripped} or {path, status, type: "file", from_sha256, to_sha256, executable
    {from, to}, binary, truncated, omitted, too_large, stripped, hunks[]}; a hunk is
    {from_start, from_count, to_start, to_count, lines[] of {op: " "|"-"|"+", text,
    no_newline?}}. A file with no hunks changed in a way that is not shown as text: binary, over
    the cap, over the diff's shared budget of work and output lines (`too_large`), only its
    executable bit, or only in removed control or bidi characters (`stripped`)."""
    if not 0 <= context <= MAX_CONTEXT:
        raise RegistryError("DIFF_INVALID", f"context must be 0 to {MAX_CONTEXT} lines")
    if (first.kind, first.name) != (second.kind, second.name):
        raise RegistryError(
            "REF_INVALID", f"diff compares versions of one kind/name: {first} and {second}"
        )
    sides = []
    for ref in (first, second):
        with _reads():
            hit = resolve(ref, search)
            read, digest, _ = _read_checked(ref, hit)
        items = {item.relative: item for item in read}
        summary = {
            "ref": str(ref),
            "sha256": digest,
            "root": _absolute(hit.root),
            "root_kind": root_kind(hit.root, project_dir),
        }
        sides.append((summary, items))
    (before_summary, before), (after_summary, after) = sides
    return {
        "from": before_summary,
        "to": after_summary,
        "context": context,
        "identical": before_summary["sha256"] == after_summary["sha256"],
        "files": _changes(before, after, context),
    }


def _changes(before: dict[str, _File], after: dict[str, _File], context: int) -> list[dict]:
    """The changed entries from `before` to `after` (relative path -> entry), in path order."""
    budget = _Budget()  # one budget for every file of this diff
    files = []
    removed_dirs = _only_dirs(before, {k for k in before if k not in after})
    added_dirs = _only_dirs(after, {k for k in after if k not in before})
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old is not None and new is not None and old.type != new.type:
            files.append(_file_change(path, old, None, context, budget))
            files.append(_file_change(path, None, new, context, budget))
            continue
        if (old or new).type == "dir":
            if path in removed_dirs or path in added_dirs:
                files.append(_file_change(path, old, new, context, budget))
            continue
        if (
            old is not None
            and new is not None
            and (old.sha256, old.executable)
            == (
                new.sha256,
                new.executable,
            )
        ):
            continue
        files.append(_file_change(path, old, new, context, budget))
    return files


def _unified_range(start: int, count: int) -> str:
    return str(start) if count == 1 else f"{start},{count}"


def unified(result: dict) -> str:
    """A `diff` result as unified diff text (`diff -u`, with ref-prefixed file names)."""
    out = []
    first, second = result["from"]["ref"], result["to"]["ref"]
    for change in result["files"]:
        path, status = change["path"], change["status"]
        if change["type"] == "dir":
            out.append(f"{'Only in ' + (first if status == 'removed' else second)}: {path}/")
            continue
        old = "/dev/null" if status == "added" else f"{first}/{path}"
        new = "/dev/null" if status == "removed" else f"{second}/{path}"
        modes = change["executable"]
        if status == "modified" and modes["from"] != modes["to"]:
            out += [
                f"old mode {'100755' if modes['from'] else '100644'}",
                f"new mode {'100755' if modes['to'] else '100644'}",
            ]
        if not change["hunks"]:
            if change["binary"]:
                out.append(f"Binary files {old} and {new} differ")
            elif change["truncated"] or change["omitted"] or change["too_large"]:
                out.append(f"Files {old} and {new} differ (too large to show)")
            elif status == "modified" and change["from_sha256"] != change["to_sha256"]:
                # Only removed characters changed: say so rather than show nothing.
                out.append(
                    f"Files {old} and {new} differ only in removed control or bidi characters"
                )
            elif status != "modified":
                out += [f"--- {old}", f"+++ {new}"]  # an empty file added or removed
            continue
        out += [f"--- {old}", f"+++ {new}"]
        for hunk in change["hunks"]:
            out.append(
                f"@@ -{_unified_range(hunk['from_start'], hunk['from_count'])} "
                f"+{_unified_range(hunk['to_start'], hunk['to_count'])} @@"
            )
            for line in hunk["lines"]:
                out.append(line["op"] + line["text"])
                if line.get("no_newline"):
                    out.append("\\ No newline at end of file")
    return "\n".join(out) + ("\n" if out else "")


# Project files that reference registry versions, and the lock written beside each.
USAGE_FILES = (("agent", "agent.yaml", "agent.lock"), ("stack", "stack.yaml", "stack.lock"))
# agent.yaml, stack.yaml: what agentfile.read and the stack loader accept (64 KiB), so used-by
# never parses a file that build would refuse.
USAGE_FILE_LIMIT = 64 * 1024
LOCK_FILE_LIMIT = 1024 * 1024  # agent.lock, stack.lock
LOCK_SOURCE_LIMIT = 512
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
AGENT_NAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?")  # as agentfile.NAME_PATTERN
STACK_NAME = re.compile(r"[a-z][a-z0-9-]{0,30}")  # as the stack.yaml schema


def read_project_file(path: Path, limit: int) -> bytes | None:
    """A project file's bytes, or None when it does not exist.

    Only a regular file (not a symlink, FIFO or device) of at most `limit` bytes is read, opened
    with O_NOFOLLOW|O_NONBLOCK and checked to be the file that was examined, so a planted file
    cannot hang the read, exhaust memory or point outside the project."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _unreadable(error) from error
    name = _shown(path.name)
    if not stat.S_ISREG(info.st_mode):
        raise RegistryError("ENTRY_UNSAFE", f"{name} must be a regular file, not a link or device")
    if info.st_size > limit:
        raise RegistryError("ENTRY_UNSAFE", f"{name} exceeds the {limit // 1024} KiB limit")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as reader:
            now = os.fstat(reader.fileno())
            if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (
                info.st_dev,
                info.st_ino,
            ):
                raise RegistryError("ENTRY_UNSAFE", f"changed while reading: {name}")
            data = reader.read(limit + 1)
    except OSError as error:
        raise _unreadable(error) from error
    if len(data) > limit:
        raise RegistryError("ENTRY_UNSAFE", f"{name} exceeds the {limit // 1024} KiB limit")
    return data


def _ref_positions(kind: str, data: dict):
    """(location, value) for the fields `build` and `services up` read refs from: the same
    positions as AgentFile.refs() and the stack schema, never free text such as a
    description."""
    if kind == "agent":
        yield "runtime", data.get("runtime")
        for field in ("prompt", "evals"):
            if isinstance(data.get(field), dict):
                yield f"{field}.ref", data[field].get("ref")
        for field in ("knowledge", "tools", "mcp"):
            if isinstance(data.get(field), list):
                for index, entry in enumerate(data[field]):
                    if isinstance(entry, dict):
                        yield f"{field}[{index}].ref", entry.get("ref")
        return
    for section in ("services", "mcp"):
        if isinstance(data.get(section), dict):
            for name, entry in data[section].items():
                if isinstance(entry, dict):
                    yield f"{section}.{_shown(name)}.ref", entry.get("ref")


def _usage_error(file: str | None, error: Exception) -> dict:
    if isinstance(error, RecursionError):
        error = RegistryError("ENTRY_UNSAFE", "too deeply nested to read")
    return {"file": file, "error": clean(str(error))[0]}


def _declared(row: dict, kind: str, source: str, raw: bytes, key: str) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StrictYamlError(f"{source} is not UTF-8") from error
    data = load_text(text)
    pattern = AGENT_NAME if kind == "agent" else STACK_NAME
    name = data.get("name")
    name = name if isinstance(name, str) and pattern.fullmatch(name) else None
    # An `agent.yaml` may also be a plain deploy manifest; only `kind: agent` is built.
    row[kind] = name if data.get("kind") == kind else None
    row["declared"] += [
        {"file": source, "name": name, "at": at}
        for at, value in _ref_positions(kind, data)
        if value == key
    ]


def _locked(row: dict, lock_name: str, entry, key: str) -> None:
    sha256, source = (
        (entry.get("sha256"), entry.get("source")) if isinstance(entry, dict) else (0, 0)
    )
    if (
        not isinstance(sha256, str)
        or not SHA256.fullmatch(sha256)
        or not isinstance(source, str)
        or len(source) > LOCK_SOURCE_LIMIT
    ):
        error = RegistryError("LOCK_INVALID", f"the {key} entry is malformed")
        row["errors"].append(_usage_error(lock_name, error))
        return
    row["locked"].append({"file": lock_name, "sha256": sha256, "source": _shown(source)})


def usage(ref: Ref, project_dir: Path) -> dict:
    """Where one project declares (`agent.yaml`, `stack.yaml`) and locks (`agent.lock`,
    `stack.lock`) `ref`. A pure read of the project's files: no state, Docker or secrets, so
    the console can call it in-process. Problems are reported per file, not raised.

    Shape: {project, agent, stack, declared[] of {file, name, at}, locked[] of {file, sha256,
    source}, errors[] of {file, error}}; `project` is absolute, `agent` and `stack` are the
    names the files declare (null when absent or invalid), `at` is the field holding the ref."""
    from agentbreeder.lock import Lock  # lock imports this module

    key = str(ref)
    row: dict = {
        "project": _absolute(project_dir),
        "agent": None,
        "stack": None,
        "declared": [],
        "locked": [],
        "errors": [],
    }
    if not project_dir.is_dir():
        missing = RegistryError("PROJECT_NOT_FOUND", f"{_shown(project_dir)} is not a directory")
        row["errors"].append(_usage_error(None, missing))
        return row
    for kind, source, lock_name in USAGE_FILES:
        try:
            raw = read_project_file(project_dir / source, USAGE_FILE_LIMIT)
            if raw is not None:
                _declared(row, kind, source, raw, key)
        except (RegistryError, StrictYamlError, RecursionError) as error:
            row["errors"].append(_usage_error(source, error))
        lock_path = project_dir / lock_name
        try:
            # Checked first (regular file, size) so Lock.load never opens a FIFO or device.
            if read_project_file(lock_path, LOCK_FILE_LIMIT) is None:
                continue
            entries = Lock.load(lock_path).entries
        except (RegistryError, RecursionError) as error:
            row["errors"].append(_usage_error(lock_name, error))
            continue
        if key in entries:
            _locked(row, lock_name, entries[key], key)
    return row
