"""Agent packages (M13 #763, spec `docs/superpowers/specs/2026-11-13-agent-package-design.md`).

A package is a registry version `agent/<name>/<X.Y.Z>/` with exactly four files:

- `manifest.yaml`: kind `agent`, name, version, description (the registry manifest);
- `package.json`: the strict record below (`package_version` 1, at most 64 KiB);
- `agent.yaml`: a byte copy of the released agent.yaml;
- `agent.lock`: the released lock with source labels redacted (§12.3). It is a record only and is
  never read as a lock input.

A package is a record, not a credential: deploying one depends on this state directory's release
row (spec §7.2), not on anything in the package. The deploy template (§5.4) has no field for an
identity, secrets, policy, capabilities, requirements, a target, an image or a gateway, so a
package cannot grant itself any of them.

This module assembles a package into a private staging directory and loads one back. Writing it
into a registry root (`registry.publish`) and the `agent` registry kind come with later slices.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agentbreeder.agentfile import MAX_BYTES as AGENT_YAML_LIMIT
from agentbreeder.agentfile import NAME_PATTERN
from agentbreeder.manifest import _ALIAS, _ENVIRONMENT, McpAccess
from agentbreeder.registry import KINDS, copy_and_hash
from agentbreeder.strict_yaml import StrictYamlError, load_text

PACKAGE_VERSION = 1
PACKAGE_LIMIT = 64 * 1024
LOCK_LIMIT = 1024 * 1024
FILES = ("agent.lock", "agent.yaml", "manifest.yaml", "package.json")
EXTERNAL = "external:"

_SHA256 = r"^sha256:[a-f0-9]{64}$"
_VERSION = r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})"
_PART = r"[a-z0-9][a-z0-9-]{0,62}"


class PackageError(ValueError):
    """A package problem; `code` is the spec §15 code. Messages never echo file contents."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _exact_int(value: object) -> object:
    # Strict JSON mode still lets `Literal[1]` match `true` and `1.0`; a format version is an int.
    if type(value) is not int:
        raise ValueError("must be an integer")
    return value


class AgentInfo(_Strict):
    name: str = Field(pattern=NAME_PATTERN)
    version: str = Field(pattern=f"^{_VERSION}$")
    runtime: str = Field(pattern=f"^runtime/{_PART}@{_VERSION}$")


class ImageInfo(_Strict):
    id: str = Field(pattern=_SHA256)
    platform: str = Field(pattern=r"^[a-z0-9]{1,32}/[a-z0-9_]{1,32}(/[a-z0-9]{1,16})?$")


class Inputs(_Strict):
    agent_yaml_sha256: str = Field(pattern=_SHA256)
    lock_sha256: str = Field(pattern=_SHA256)
    lock_version: Literal[1]
    # The M9b build.json `sources` table, once lock v2 exists; always empty for lock v1.
    sources: dict[str, dict] = Field(max_length=0)

    _lock_version = field_validator("lock_version", mode="before")(_exact_int)


class Evals(_Strict):
    ref: str = Field(pattern=f"^eval/{_PART}@{_VERSION}$")
    set_sha256: str = Field(pattern=_SHA256)
    threshold: float = Field(ge=0, le=1)
    retries: int = Field(ge=0, le=3)
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
    passed: int = Field(ge=0)
    cases: int = Field(ge=1, le=100000)
    environment: str = Field(pattern=_ENVIRONMENT)

    @model_validator(mode="after")
    def _counts(self) -> Evals:
        if self.passed > self.cases:
            raise ValueError("passed exceeds cases")
        # Release only packages a passing run (evals.Report.pass_rate). This is a consistency
        # check, not the gate: deploy trusts the release row, never these numbers (spec §12.1).
        if self.passed / self.cases < self.threshold:
            raise ValueError("the recorded run did not pass its threshold")
        return self


class DeployTemplate(_Strict):
    """The deploy fields derived from the agent and its runtime only (spec §5.4)."""

    port: int = Field(ge=1, le=65535)
    health_path: str = Field(pattern=r"^/[A-Za-z0-9/_-]*$")
    # Tuples, so the frozen record cannot change between hashing and writing.
    aliases: tuple[str, ...] = Field(min_length=1, max_length=20)
    budget_usd: float | None = Field(gt=0, le=100000)
    rpm: int | None = Field(ge=1, le=100000)
    mcp_access: tuple[McpAccess, ...] = Field(max_length=10)

    @field_validator("aliases", "mcp_access", mode="before")
    @classmethod
    def _listed(cls, value: object) -> object:
        # Built from Python lists (build, release) as well as JSON arrays.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _deployable(self) -> DeployTemplate:
        # The same alias rules the deploy contract applies (manifest.ModelAccess).
        if any(not re.fullmatch(_ALIAS, alias) for alias in self.aliases) or len(
            set(self.aliases)
        ) != len(self.aliases):
            raise ValueError("aliases must be unique gateway model names")
        servers = [entry.ref.split("@")[0] for entry in self.mcp_access]
        if len(set(servers)) != len(servers):
            raise ValueError("mcp_access names each MCP server once")
        return self


class Released(_Strict):
    at: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
    agentbreeder: str = Field(pattern=r"^[0-9][0-9A-Za-z.+-]{0,63}$")


class Package(_Strict):
    package_version: Literal[1]
    agent: AgentInfo
    image: ImageInfo
    inputs: Inputs
    evals: Evals
    deploy: DeployTemplate
    released: Released

    _package_version = field_validator("package_version", mode="before")(_exact_int)

    @property
    def ref(self) -> str:
        return f"agent/{self.agent.name}@{self.agent.version}"

    def to_bytes(self) -> bytes:
        """The canonical `package.json` bytes: field order as declared, two-space indent."""
        return (json.dumps(self.model_dump(mode="json"), indent=2) + "\n").encode()


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _json(data: bytes, what: str, code: str):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise PackageError(
            code, f"{what} is not valid JSON, or has a duplicate key or an oversized number"
        ) from None


def _fields(error: ValidationError) -> str:
    """The failing field paths; a key that is not a plain name is shown as `?`, so a forged
    document cannot put text of its choosing into the message."""

    def part(value: object) -> str:
        text = str(value)
        return text if re.fullmatch(r"[A-Za-z0-9_]{1,32}", text) else "?"

    return ", ".join(".".join(map(part, e["loc"])) or "(top level)" for e in error.errors()[:5])


def parse(data: bytes) -> Package:
    """`package.json` bytes, checked against the strict schema (spec §5.3, §12.4)."""
    if len(data) > PACKAGE_LIMIT:
        raise PackageError("PACKAGE_INVALID", "package.json exceeds the 64 KiB limit")
    value = _json(data, "package.json", "PACKAGE_INVALID")
    if not isinstance(value, dict):
        raise PackageError("PACKAGE_INVALID", "package.json must be a JSON object")
    version = value.get("package_version")
    if type(version) is not int or version != PACKAGE_VERSION:
        raise PackageError(
            "PACKAGE_INVALID",
            "package.json has an unsupported package_version (written by a newer AgentBreeder? "
            "upgrade)",
        )
    try:
        return Package.model_validate_json(data)
    except ValidationError as error:
        raise PackageError(
            "PACKAGE_INVALID",
            f"package.json fields are invalid or unknown: {_fields(error)} (written by a newer "
            "AgentBreeder? upgrade)",
        ) from None


_REF_KEY = re.compile(rf"(?:{'|'.join(KINDS)})/{_PART}@{_VERSION}")


def _path_key(path: str) -> bool:
    """A `path:` lock key as `lock.materialise` writes it: a relative POSIX path inside the
    project. Any file name is allowed (spaces, a leading dot, non-ASCII), as the lock allows."""
    parts = PurePosixPath(path).parts
    return (
        bool(path)
        and not path.startswith(("/", "~"))
        and "\\" not in path
        and ".." not in parts
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in path)
    )


_LABEL_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*|\.")
_REMOTE = re.compile(rf"remote:{_PART}")


def safe_label(label: object) -> bool:
    """Whether a lock `source` label can be kept in a package: `builtin`, `registry`,
    `remote:<name>`, or a project-relative path without `..` (spec §12.3)."""
    if not isinstance(label, str):
        return False
    if label in ("builtin", "registry") or _REMOTE.fullmatch(label):
        return True
    return bool(_LABEL_PATH.fullmatch(label)) and ".." not in PurePosixPath(label).parts


def redact_lock(data: bytes) -> bytes:
    """The package's copy of a v1 `agent.lock`: every unsafe `source` label becomes
    `external:`, hashes are kept, and the result is written canonically. The copy is never a
    lock input (`lock.Lock.load` is not called on it)."""
    if len(data) > LOCK_LIMIT:
        raise PackageError("PACKAGE_INVALID", "agent.lock exceeds the 1 MiB limit")
    value = _json(data, "agent.lock", "PACKAGE_INVALID")
    if not isinstance(value, dict) or set(value) != {"lock_version", "entries"}:
        raise PackageError("PACKAGE_INVALID", "agent.lock must hold lock_version and entries")
    if type(value["lock_version"]) is not int or value["lock_version"] != 1:
        raise PackageError("PACKAGE_INVALID", "only lock_version 1 can be packaged")
    entries = value["entries"]
    if not isinstance(entries, dict):
        raise PackageError("PACKAGE_INVALID", "agent.lock entries must be an object")
    redacted = {}
    for key in sorted(entries):
        entry = entries[key]
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "source"}
            or not isinstance(entry["sha256"], str)
            or not re.fullmatch(_SHA256, entry["sha256"])
        ):
            raise PackageError("PACKAGE_INVALID", "agent.lock entries must be {sha256, source}")
        path_entry = key.startswith("path:")
        if not (_path_key(key[5:]) if path_entry else _REF_KEY.fullmatch(key)):
            raise PackageError("PACKAGE_INVALID", "agent.lock has an entry key that is not a ref")
        # A path entry's source is its own project-relative path (lock.materialise).
        kept = safe_label(entry["source"]) or (path_entry and entry["source"] == key[5:])
        source = entry["source"] if kept else EXTERNAL
        redacted[key] = {"sha256": entry["sha256"], "source": source}
    return (json.dumps({"lock_version": 1, "entries": redacted}, indent=2) + "\n").encode()


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def manifest_bytes(name: str, version: str, description: str) -> bytes:
    data = {"kind": "agent", "name": name, "version": version, "description": description}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()


def assemble(
    staging: Path, record: Package, agent_yaml: bytes, lock: bytes, description: str = ""
) -> str:
    """Write the package into `staging` (created here, mode 0700, must not exist) and return its
    tree hash. `agent_yaml` and `lock` must be the bytes `record.inputs` names, so the package
    holds exactly what was built and evaluated. `description` must come from `agent.yaml`, never
    from eval output (spec §12.2). On an error the caller removes `staging`."""
    if _sha256(agent_yaml) != record.inputs.agent_yaml_sha256:
        raise PackageError("PACKAGE_INPUTS_CHANGED", "agent.yaml differs from the built one")
    if _sha256(lock) != record.inputs.lock_sha256:
        raise PackageError("PACKAGE_INPUTS_CHANGED", "agent.lock differs from the built one")
    if not isinstance(description, str) or len(description) > 500:
        raise PackageError("PACKAGE_INVALID", "description must be at most 500 characters")
    files = {
        "manifest.yaml": manifest_bytes(record.agent.name, record.agent.version, description),
        "package.json": record.to_bytes(),
        "agent.yaml": agent_yaml,
        "agent.lock": redact_lock(lock),
    }
    if len(files["package.json"]) > PACKAGE_LIMIT:
        raise PackageError("PACKAGE_INVALID", "package.json exceeds the 64 KiB limit")
    staging.mkdir(mode=0o700)
    os.chmod(staging, 0o700)  # mkdir's mode is masked by the umask
    for name in FILES:
        path = staging / name
        with open(path, "xb") as writer:
            writer.write(files[name])
        os.chmod(path, 0o644)
    return copy_and_hash(staging)


def _read(path: Path, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise PackageError("PACKAGE_INVALID", f"{path.name} is missing or unreadable") from None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)  # checked before fdopen, which refuses a directory with OSError
        raise PackageError("PACKAGE_INVALID", f"{path.name} must be a regular file")
    with os.fdopen(descriptor, "rb") as reader:
        data = reader.read(limit + 1)
    if len(data) > limit:
        raise PackageError("PACKAGE_INVALID", f"{path.name} is too large")
    return data


def load(version_dir: Path, name: str, version: str) -> Package:
    """The package at `version_dir`, which must be `agent/<name>@<version>`: exactly the four
    files, a strict `package.json` and a `manifest.yaml` naming the same ref (spec §5.3).

    It also checks `agent.yaml` against `inputs.agent_yaml_sha256` (the lock copy is redacted, so
    its hash cannot be checked). It does not compare the tree with a release row. The manifest
    is checked here until slice 1b adds `agent` to `registry.KINDS`; then it moves to
    `registry.read_manifest`. Deploy (spec §7.2 step 3) checks
    the tree hash against this state directory's audit log before it trusts a package."""
    if not (re.fullmatch(NAME_PATTERN, name) and re.fullmatch(_VERSION, version)):
        raise PackageError("PACKAGE_INVALID", "agent packages are agent/<name>@X.Y.Z")
    if version_dir.is_symlink() or not version_dir.is_dir():
        raise PackageError("PACKAGE_INVALID", "the package must be a real directory")
    try:
        present = sorted(entry.name for entry in os.scandir(version_dir))
    except OSError:
        raise PackageError("PACKAGE_INVALID", "the package directory is unreadable") from None
    if present != sorted(FILES):
        raise PackageError(
            "PACKAGE_INVALID", f"a package holds exactly {', '.join(FILES)}; found other files"
        )
    record = parse(_read(version_dir / "package.json", PACKAGE_LIMIT))
    try:
        manifest = load_text(_read(version_dir / "manifest.yaml", PACKAGE_LIMIT).decode("utf-8"))
    except (UnicodeDecodeError, StrictYamlError):
        raise PackageError("PACKAGE_INVALID", "manifest.yaml is not valid") from None
    declared = (manifest.get("kind"), manifest.get("name"), manifest.get("version"))
    if declared != ("agent", name, version):
        raise PackageError("PACKAGE_INVALID", "manifest.yaml does not name this agent version")
    if (record.agent.name, record.agent.version) != (name, version):
        raise PackageError("PACKAGE_INVALID", "package.json does not name this agent version")
    if (
        _sha256(_read(version_dir / "agent.yaml", AGENT_YAML_LIMIT))
        != record.inputs.agent_yaml_sha256
    ):
        raise PackageError("PACKAGE_INVALID", "agent.yaml differs from the hash in package.json")
    _read(version_dir / "agent.lock", LOCK_LIMIT)  # a regular file within the cap
    return record
