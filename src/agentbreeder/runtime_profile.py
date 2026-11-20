"""What the engine knows about a `runtime/` version, read from the version itself (#1083 F1,
multi-framework spec §5.3 and §6.2).

`build`, `new agent` and `registry runtimes` take every framework fact from here: the runtime's
`manifest.yaml` keys (`protocol`, `profile`, `capabilities`, `limits`, `reserved_tool_names`, ...)
and its generated `runtime/modules.json` (the runtime's own module names and its lock's import
names). Nothing in the engine branches on a framework name.

Runtime versions are append-only, so the versions published before these keys existed cannot gain
them. A manifest **without a `protocol` key** gets the one legacy profile, `LEGACY_ADK`: the facts
the engine used to hold as constants. It is the only framework-named constant in the engine and it
applies only to those versions.

This module loads only `strict_yaml` at import.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agentbreeder.strict_yaml import StrictYamlError, load_file

CAPABILITY = re.compile(r"[a-z][a-z0-9_-]{0,31}")
FRAMEWORK = re.compile(r"[a-z][a-z0-9-]{0,31}")
TOOL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
PACKAGE = re.compile(r"[a-z0-9]([a-z0-9-]{0,98}[a-z0-9])?")
KIT_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
DOCS_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")

PROTOCOLS = (1,)  # agent protocol versions this engine can build for (spec §10)
PROFILES = ("v1", "kit")
MODEL_APIS = ("openai-chat", "anthropic-messages")
# How the deploy runs a kit runtime's image. `two-uid` (owner decision D15, #1089): a second
# container from the same image, under its own uid with no secret mount, runs the framework's
# subprocess (the Claude Code CLI). Unset: one container, as before.
ISOLATIONS = ("two-uid",)
# What `agent.yaml` itself allows (`agentfile.MAX_KNOWLEDGE`, `MAX_TOOLS`, `MAX_MCP`; a test keeps
# them equal); a runtime's `limits` narrows these.
ENGINE_LIMITS = {"knowledge": 5, "tools": 50, "mcp_servers": 10}
# A runtime that declares no limit for a part gets the limits every runtime had before F1.
DEFAULT_LIMITS = {"knowledge": 1, "tools": 50, "mcp_servers": 10}
MAX_RESERVED = 100
MAX_CAPABILITIES = 32
MAX_DISPLAY_NAME = 100
MAX_NOTES = 300
MAX_DOCS = 200
MODULES_FILE = "runtime/modules.json"
MODULES_LIMIT = 256 * 1024
MODULES_VERSION = 1
# The tool a runtime with `knowledge` gives the model for further searches (spec §5.2): the same
# name on every runtime, so it is part of the protocol, not of one framework.
KNOWLEDGE_TOOL = "search_knowledge_base"


class ProfileError(ValueError):
    """The runtime version cannot be used: its manifest or `modules.json` is unreadable or
    invalid (`ARTIFACT_INVALID`), or it serves a protocol this engine does not know
    (`PROTOCOL_UNSUPPORTED`). The message holds no manifest text beyond a number the strict
    loader already parsed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Profile:
    """The engine's resolved view of one runtime version."""

    framework: str
    display_name: str
    protocol: int
    profile: str
    capabilities: frozenset[str]
    limits: dict[str, int]
    reserved_tool_names: frozenset[str]
    kit: str | None = None
    model_api: str | None = None
    isolation: str | None = None
    notes: str | None = None
    docs: str | None = None
    # True for a manifest without `protocol` (`LEGACY_ADK`). A legacy runtime checks reserved
    # names against an MCP server's own tool names only, as ADK does; a runtime that declares
    # `protocol` also has them checked against every name the model would see.
    legacy: bool = False

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def limit(self, part: str) -> int:
        return self.limits[part]

    def as_json(self) -> dict:
        """The fields `registry runtimes --json` shows (console spec §8); optional ones are
        left out when unset."""
        data: dict = {
            "display_name": self.display_name,
            "framework": self.framework,
            "protocol": self.protocol,
            "profile": self.profile,
        }
        if self.kit is not None:
            data["kit"] = self.kit
        if self.isolation is not None:
            data["isolation"] = self.isolation
        data["capabilities"] = sorted(self.capabilities)
        data["limits"] = dict(sorted(self.limits.items()))
        data["reserved_tool_names"] = sorted(self.reserved_tool_names)
        if self.notes is not None:
            data["notes"] = self.notes
        if self.docs is not None:
            data["docs"] = self.docs
        return data


@dataclass(frozen=True)
class Legacy:
    """`LEGACY_ADK`: what a runtime manifest without a `protocol` key means."""

    framework: str
    display_name: str
    protocol: int
    profile: str
    capabilities: frozenset[str]  # implied; the manifest's own list is added
    limits: dict[str, int]
    reserved_tool_names: frozenset[str]
    modules: frozenset[str]  # the runtime's own module names (`runtime/*.py`)
    imports: dict[str, tuple[str, ...]]  # locked package -> its top-level import names


# Top-level import names of every package in a `runtime/adk` requirements.lock published before
# F1 (read from the installed distributions, `importlib.metadata.packages_distributions()`, in the
# CI image). tests/test_tool_check.py fails when one of those locks has a package missing here.
# A runtime version published after F1 carries its own `runtime/modules.json` instead.
_LOCKED_IMPORTS: dict[str, tuple[str, ...]] = {
    "aiohappyeyeballs": ("aiohappyeyeballs",),
    "aiohttp": ("aiohttp",),
    "aiosignal": ("aiosignal",),
    "aiosqlite": ("aiosqlite",),
    "alembic": ("alembic",),
    "annotated-doc": ("annotated_doc",),
    "annotated-types": ("annotated_types",),
    "anyio": ("anyio",),
    "attrs": (
        "attr",
        "attrs",
    ),
    "authlib": ("authlib",),
    "boto3": ("boto3",),
    "botocore": ("botocore",),
    "certifi": ("certifi",),
    "cffi": (
        "_cffi_backend",
        "cffi",
    ),
    "charset-normalizer": ("charset_normalizer",),
    "click": ("click",),
    "cryptography": ("cryptography",),
    "distro": ("distro",),
    "fastapi": ("fastapi",),
    "fastuuid": ("fastuuid",),
    "filelock": ("filelock",),
    "frozenlist": ("frozenlist",),
    "fsspec": ("fsspec",),
    "google-adk": ("google",),
    "google-api-core": ("google",),
    "google-auth": ("google",),
    "google-cloud-core": ("google",),
    "google-cloud-monitoring": ("google",),
    "google-cloud-spanner": ("google",),
    "google-genai": ("google",),
    "googleapis-common-protos": ("google",),
    "graphviz": ("graphviz",),
    "greenlet": ("greenlet",),
    "grpc-google-iam-v1": ("google",),
    "grpc-interceptor": ("grpc_interceptor",),
    "grpcio": ("grpc",),
    "grpcio-status": ("grpc_status",),
    "h11": ("h11",),
    "h2": ("h2",),
    "hf-xet": ("hf_xet",),
    "hpack": ("hpack",),
    "httpcore": ("httpcore",),
    "httpcore2": ("httpcore2",),
    "httpx": ("httpx",),
    "httpx2": ("httpx2",),
    "huggingface-hub": ("huggingface_hub",),
    "hyperframe": ("hyperframe",),
    "idna": ("idna",),
    "importlib-metadata": ("importlib_metadata",),
    "jinja2": ("jinja2",),
    "jiter": ("jiter",),
    "jmespath": ("jmespath",),
    "joserfc": ("joserfc",),
    "jsonschema": ("jsonschema",),
    "jsonschema-specifications": ("jsonschema_specifications",),
    "litellm": ("litellm",),
    "mako": ("mako",),
    "markupsafe": ("markupsafe",),
    "mcp": ("mcp",),
    "mcp-types": ("mcp_types",),
    "mmh3": ("mmh3",),
    "multidict": ("multidict",),
    "openai": ("openai",),
    "opentelemetry-api": ("opentelemetry",),
    "opentelemetry-exporter-otlp-proto-common": ("opentelemetry",),
    "opentelemetry-exporter-otlp-proto-http": ("opentelemetry",),
    "opentelemetry-proto": ("opentelemetry",),
    "opentelemetry-resourcedetector-gcp": ("opentelemetry",),
    "opentelemetry-sdk": ("opentelemetry",),
    "opentelemetry-semantic-conventions": ("opentelemetry",),
    "packaging": ("packaging",),
    "propcache": ("propcache",),
    "proto-plus": ("proto",),
    "protobuf": ("google",),
    "psycopg": ("psycopg",),
    "psycopg-binary": ("psycopg_binary",),
    "pyasn1": ("pyasn1",),
    "pyasn1-modules": ("pyasn1_modules",),
    "pycparser": ("pycparser",),
    "pydantic": ("pydantic",),
    "pydantic-core": ("pydantic_core",),
    "pydantic-settings": ("pydantic_settings",),
    "pyjwt": ("jwt",),
    "python-dateutil": ("dateutil",),
    "python-dotenv": ("dotenv",),
    "python-multipart": (
        "multipart",
        "python_multipart",
    ),
    "pyyaml": (
        "_yaml",
        "yaml",
    ),
    "referencing": ("referencing",),
    "regex": ("regex",),
    "requests": ("requests",),
    "rpds-py": ("rpds",),
    "s3transfer": ("s3transfer",),
    "six": ("six",),
    "sniffio": ("sniffio",),
    "sqlalchemy": ("sqlalchemy",),
    "sqlalchemy-spanner": ("google",),
    "sqlparse": ("sqlparse",),
    "sse-starlette": ("sse_starlette",),
    "starlette": ("starlette",),
    "tenacity": ("tenacity",),
    "tiktoken": (
        "tiktoken",
        "tiktoken_ext",
    ),
    "tokenizers": ("tokenizers",),
    "tqdm": ("tqdm",),
    "truststore": ("truststore",),
    "typing-extensions": (
        "__pycache__",
        "typing_extensions",
    ),
    "typing-inspection": ("typing_inspection",),
    "urllib3": ("urllib3",),
    "uvicorn": ("uvicorn",),
    "watchdog": ("watchdog",),
    "websockets": ("websockets",),
    "yarl": ("yarl",),
    "zipp": ("zipp",),
}

LEGACY_ADK = Legacy(
    framework="google-adk",
    display_name="Google ADK",
    protocol=1,
    profile="v1",
    capabilities=frozenset({"knowledge", "notes"}),
    limits=dict(DEFAULT_LIMITS),
    # ADK skips an MCP tool with one of these names, so runtime/adk@1.1.0 refuses them at
    # startup.
    reserved_tool_names=frozenset(
        {
            "transfer_to_agent",
            "adk_request_credential",
            "adk_request_confirmation",
            "adk_request_input",
            "set_model_response",
        }
    ),
    # Module names of the runtime itself (`runtime/adk/*/runtime/*.py`); a test keeps this in
    # step with the versions published before F1.
    modules=frozenset(
        {
            "agent",
            "config",
            "mcp_client",
            "memory",
            "rag",
            "redaction",
            "server",
            "telemetry",
            "tools",
        }
    ),
    imports=_LOCKED_IMPORTS,
)


def _invalid(what: str) -> ProfileError:
    return ProfileError("ARTIFACT_INVALID", f"invalid {what}")


# Keys that mean something only beside `protocol`. A manifest that has one of them without
# `protocol` is refused, not read as legacy: a forgotten `protocol` would otherwise turn a kit
# runtime into "Google ADK" and drop its limits and the deploy manifest's `runtime` field.
PROTOCOL_KEYS = (
    "profile",
    "kit",
    "display_name",
    "limits",
    "reserved_tool_names",
    "model_api",
    "isolation",
    "notes",
    "docs",
)


def declared_capabilities(meta: dict) -> frozenset[str]:
    values = meta.get("capabilities", [])
    if (
        not isinstance(values, list)
        or len(values) > MAX_CAPABILITIES
        or not all(isinstance(v, str) and CAPABILITY.fullmatch(v) for v in values)
        or len(set(values)) != len(values)
    ):
        raise _invalid("capabilities")
    return frozenset(values)


def _text(meta: dict, key: str, limit: int, *, required: bool = False) -> str | None:
    """A one-line label: printable, not blank, at most `limit` characters."""
    if key not in meta:
        if required:
            raise _invalid(key)
        return None
    value = meta[key]
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
        or not value.isprintable()
    ):
        raise _invalid(key)
    return value


def _limits(meta: dict) -> dict[str, int]:
    limits = dict(DEFAULT_LIMITS)
    declared = meta.get("limits", {})
    if not isinstance(declared, dict) or set(declared) - set(ENGINE_LIMITS):
        raise _invalid("limits")
    for part, value in declared.items():
        if type(value) is not int or not 0 <= value <= ENGINE_LIMITS[part]:
            raise _invalid(f"limits.{part}")
        limits[part] = value
    return limits


def _reserved(meta: dict) -> frozenset[str]:
    values = meta.get("reserved_tool_names", [])
    if (
        not isinstance(values, list)
        or len(values) > MAX_RESERVED
        or not all(isinstance(v, str) and TOOL_NAME.fullmatch(v) for v in values)
        or len(set(values)) != len(values)
    ):
        raise _invalid("reserved_tool_names")
    return frozenset(values)


def _docs(meta: dict) -> str | None:
    if "docs" not in meta:
        return None
    value = meta["docs"]
    if (
        not isinstance(value, str)
        or len(value) > MAX_DOCS
        or not DOCS_PATH.fullmatch(value)
        or ".." in value.split("/")
    ):
        raise _invalid("docs")
    return value


def from_manifest(meta: object) -> Profile:
    """The profile a parsed runtime `manifest.yaml` gives. Raises ProfileError."""
    if not isinstance(meta, dict):
        raise _invalid("manifest")
    capabilities = declared_capabilities(meta)
    if "protocol" not in meta:
        stray = [key for key in PROTOCOL_KEYS if key in meta]
        if stray:
            raise ProfileError("ARTIFACT_INVALID", f"{stray[0]} needs protocol")
        legacy = LEGACY_ADK
        framework = meta.get("framework")
        return Profile(
            framework=(
                framework
                if isinstance(framework, str) and FRAMEWORK.fullmatch(framework)
                else legacy.framework
            ),
            display_name=legacy.display_name,
            protocol=legacy.protocol,
            profile=legacy.profile,
            capabilities=legacy.capabilities | capabilities,
            limits=dict(legacy.limits),
            reserved_tool_names=legacy.reserved_tool_names,
            legacy=True,
        )
    protocol = meta["protocol"]
    if type(protocol) is not int or not 0 < protocol < 1000:
        raise _invalid("protocol")
    if protocol not in PROTOCOLS:
        raise ProfileError(
            "PROTOCOL_UNSUPPORTED", f"protocol {protocol} is not supported by this AgentBreeder"
        )
    profile = meta.get("profile")
    if profile not in PROFILES:
        raise _invalid("profile")
    kit = meta.get("kit")
    if profile == "kit":
        if not isinstance(kit, str) or not KIT_VERSION.fullmatch(kit):
            raise _invalid("kit")
    elif kit is not None:
        raise _invalid("kit")
    framework = meta.get("framework")
    if not isinstance(framework, str) or not FRAMEWORK.fullmatch(framework):
        raise _invalid("framework")
    model_api = meta.get("model_api")
    if model_api is not None and model_api not in MODEL_APIS:
        raise _invalid("model_api")
    isolation = meta.get("isolation")
    if isolation is not None and (isolation not in ISOLATIONS or profile != "kit"):
        raise _invalid("isolation")
    return Profile(
        framework=framework,
        display_name=_text(meta, "display_name", MAX_DISPLAY_NAME, required=True),
        protocol=protocol,
        profile=profile,
        capabilities=capabilities,
        limits=_limits(meta),
        reserved_tool_names=_reserved(meta),
        kit=kit,
        model_api=model_api,
        isolation=isolation,
        notes=_text(meta, "notes", MAX_NOTES),
        docs=_docs(meta),
    )


def load(runtime_dir: Path) -> Profile:
    """The profile of the runtime version in `runtime_dir`. Raises ProfileError."""
    try:
        meta = load_file(runtime_dir / "manifest.yaml")
    except StrictYamlError as error:
        raise ProfileError("ARTIFACT_INVALID", str(error)) from error
    return from_manifest(meta)


# -- runtime/modules.json ------------------------------------------------------------------------


@dataclass(frozen=True)
class Modules:
    """A runtime version's own top-level module names and its lock's import names."""

    modules: frozenset[str]
    imports: dict[str, tuple[str, ...]]

    def names(self) -> frozenset[str]:
        """Every top-level name a tool's `src/` must not use on this runtime."""
        return self.modules | {name for names in self.imports.values() for name in names}


def modules_document(modules: set[str], imports: dict[str, list[str]]) -> str:
    """The canonical `modules.json` text (`scripts/runtime_modules.py` writes it)."""
    body = {
        "schema_version": MODULES_VERSION,
        "modules": sorted(modules),
        "imports": {package: sorted(names) for package, names in sorted(imports.items())},
    }
    return json.dumps(body, indent=1) + "\n"


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_modules(data: bytes) -> Modules:
    """`modules.json` bytes as Modules. Raises ProfileError; nothing from the file is echoed."""
    invalid = ProfileError("ARTIFACT_INVALID", f"invalid {MODULES_FILE}")
    if len(data) > MODULES_LIMIT:
        raise invalid
    try:
        body = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError):
        raise invalid from None
    if (
        not isinstance(body, dict)
        or set(body) != {"schema_version", "modules", "imports"}
        or type(body["schema_version"]) is not int
        or body["schema_version"] != MODULES_VERSION
    ):
        raise invalid
    modules, imports = body["modules"], body["imports"]

    def names(values: object) -> bool:
        return (
            isinstance(values, list)
            and all(isinstance(v, str) and MODULE.fullmatch(v) for v in values)
            and len(set(values)) == len(values)
        )

    if not names(modules) or not isinstance(imports, dict):
        raise invalid
    for package, values in imports.items():
        if not PACKAGE.fullmatch(package) or not names(values):
            raise invalid
    return Modules(frozenset(modules), {p: tuple(v) for p, v in imports.items()})


def load_modules(runtime_dir: Path, profile: Profile) -> Modules:
    """The module names of the runtime version in `runtime_dir`: `LEGACY_ADK`'s for a legacy
    version (which has no file), else its `runtime/modules.json`, which a runtime that declares
    `protocol` must ship. Raises ProfileError."""
    if profile.legacy:
        return Modules(LEGACY_ADK.modules, LEGACY_ADK.imports)
    path = runtime_dir / MODULES_FILE
    try:
        if path.is_symlink() or not path.is_file():
            raise ProfileError("ARTIFACT_INVALID", f"missing {MODULES_FILE}")
        with path.open("rb") as handle:
            data = handle.read(MODULES_LIMIT + 1)
    except OSError:
        raise ProfileError("ARTIFACT_INVALID", f"unreadable {MODULES_FILE}") from None
    return parse_modules(data)


# -- registry runtimes ---------------------------------------------------------------------------

LISTING_VERSION = 1


def listing(search: list[Path], project_dir: Path | None = None) -> dict:
    """`registry runtimes --json`: the engine's resolved view of every `runtime/` version in
    the search roots (console spec §8), so a reader holds no profile logic of its own.

    A usable version has the profile's fields and `verified` (the version was run end to end,
    `agentspec.runtime_verified`). One the engine cannot use (an invalid manifest, an
    unknown protocol, a missing `modules.json`, a symlinked directory, a conflict between
    roots) is listed with `usable: false`, a `code` and a `reason`, never dropped. Versions run
    end to end come first, then by name, newest version first."""
    from agentbreeder import agentspec, registry

    rows: dict[str, dict] = {}
    for row in registry.listing(search, "runtime/"):
        if row["ref"] in rows:
            continue  # the same ref in a later root; `resolve` below reports a conflict
        name, _, version = row["ref"].removeprefix("runtime/").partition("@")
        entry: dict = {
            "ref": row["ref"],
            "name": name,
            "version": version,
            "root_kind": registry.root_kind(Path(row["root"]), project_dir),
        }
        try:
            # A directory such as runtime/x/1.0 is listed by `registry list` but is not an
            # X.Y.Z version (REF_UNPINNED): unusable, and the other versions are still shown.
            ref = registry.Ref.parse(row["ref"])
            hit = registry.resolve(ref, search)  # refuses a symlinked directory, a conflict
            # As build's copy does: a link, a hard link or a non-regular file anywhere in the
            # version is refused before its manifest or modules.json is read, so nothing is
            # read from outside the version.
            registry.copy_and_hash(hit.path)
            profile = load(hit.path)
            load_modules(hit.path, profile)
        except ProfileError as error:
            entry |= {"verified": False, "usable": False, "code": error.code}
            entry["reason"] = registry.clean(error.message)[0]
        except registry.RegistryError as error:
            entry |= {"verified": False, "usable": False, "code": error.code}
            entry["reason"] = registry.clean(str(error).removeprefix(f"[{error.code}] "))[0]
        except OSError:
            # A file in the version cannot be read: that version is unusable, and the others
            # are still listed. The path is not echoed.
            entry |= {"verified": False, "usable": False, "code": "ENTRY_UNREADABLE"}
            entry["reason"] = "a file in the version cannot be read"
        else:
            entry |= profile.as_json()
            entry |= {"verified": agentspec.runtime_verified(row["ref"]), "usable": True}
        rows[row["ref"]] = entry

    def order(entry: dict) -> tuple:
        # `registry.listing` admits only digits and dots; an empty part sorts as 0.
        version = tuple(-int(part or 0) for part in entry["version"].split("."))
        return (not entry["verified"], entry["name"], version)

    return {"schema_version": LISTING_VERSION, "runtimes": sorted(rows.values(), key=order)}
