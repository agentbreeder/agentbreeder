"""Build a new agent from a form (#833, M17; console spec §4.5 "Build brand-new", §8.5, guided
§14.7).

The browser never builds YAML. It sends the form as `new agent --from-spec`'s JSON spec; the
server writes that spec to a file only its user can read (0600, in a 0700 temporary directory:
the prompt never travels through argv, which every local user can read through `ps`), runs
`new agent NAME --from-spec=FILE --dir=DEST --dry-run --json` for the preview and shows the
files exactly as the CLI would write them. Create runs the dry run again (the code and
verification checks are re-made on a fresh answer, not the browser's), then the write with
`--expect-digest=<plan_sha256>` as previewed, so a spec, a picked version or a date that changed
in between is refused (DIGEST_MISMATCH) rather than written. The temporary directory is removed
either way.

Only combinations run end to end are created (`combination.verified`, the CLI's
`agentspec.VERIFIED`); others are previewed with a note. Knowledge is refused for now (the CLI's
SPEC_UNSUPPORTED, checked here first so no child runs).

The pickers offer `parts`: the `tool/` versions and `mcp/` servers (with the tools each declares)
over the roots a new project resolves, AGENTBREEDER_REGISTRY_PATH and the built-ins. It is an
offer, not a check: the CLI resolves every picked ref again (REF_NOT_FOUND, REF_CONFLICT,
ENTRY_UNSAFE) and refuses a tool a server does not declare (MCP_TOOL_UNDECLARED).

Pure request and answer checks plus the temporary file: no engine module is loaded, so the
server process keeps its allowlist (`tests/test_center_reads.py`). The CLI validates the spec
itself; these checks keep the request to the spec's shape and size.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

from agentbreeder import registry
from agentbreeder.center.files import BadRequest
from agentbreeder.center.onboard import (
    DIGEST,
    MODEL_ID,
    NAME,
    PROVIDERS,
    ROOT_KINDS,
    BadOutput,
)
from agentbreeder.services.schema import MCP_TOOL, RESERVED, SERVICE_NAME
from agentbreeder.strict_yaml import StrictYamlError, load_file

SPEC_VERSION = 1  # `agentspec.SPEC_VERSION`
MAX_SPEC_BYTES = 256 * 1024  # `agentspec.MAX_SPEC_BYTES`
FIELDS = frozenset(
    {"schema_version", "name", "model", "model_id", "max_model_calls", "prompt", "evals"}
    | {"tools", "knowledge", "mcp"}
)
MAX_TOOLS = 50  # `agentspec.Spec.tools`
MAX_SERVERS = 10  # `agentspec.Spec.mcp`
MAX_SERVER_TOOLS = 100  # `agentspec.Mcp.tools`
STACK_SERVICES = frozenset({"postgres", "litellm"})  # `agentspec.STACK_SERVICES`
PART_KINDS = ("tool", "mcp")
MAX_PARTS = 500  # versions offered per kind
MAX_DESCRIPTION = 300
MCP_FILE_LIMIT = 64 * 1024  # `mcp.MAX_BYTES`
EFFECTS = ("read", "write")
MAX_FILES = 40
MAX_CONTENT = 2 * 1024 * 1024  # all files' text together: the prompt is at most 32 KiB


def request(body: object) -> tuple[str, dict]:
    """The checked `(name, spec)` of a preview or create request body `{spec: {...}}`."""
    spec = body.get("spec") if isinstance(body, dict) else None
    if not isinstance(spec, dict):
        raise BadRequest("Send the agent's spec", "SPEC_INVALID")
    unknown = sorted(set(spec) - FIELDS)
    if unknown:
        raise BadRequest(f"Unknown field in the spec: {unknown[0][:40]}", "SPEC_INVALID")
    if spec.get("schema_version") != SPEC_VERSION:
        raise BadRequest(f"The spec's schema_version is {SPEC_VERSION}", "SPEC_INVALID")
    name = spec.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise BadRequest(
            "The agent's name starts with a lowercase letter, then lowercase letters, digits and "
            "dashes, at most 31 characters, not ending in a dash",
            "NAME_INVALID",
        )
    if spec.get("model") not in PROVIDERS:
        raise BadRequest(f"Pick a model provider: {', '.join(PROVIDERS)}", "MODEL_INVALID")
    model_id = spec.get("model_id")
    if model_id is not None and not (isinstance(model_id, str) and MODEL_ID.fullmatch(model_id)):
        raise BadRequest(
            "A model id is letters, digits and . _ : / -, at most 128 characters",
            "MODEL_ID_INVALID",
        )
    if spec.get("knowledge", []) != []:
        raise BadRequest(
            "Knowledge is not part of a new agent yet: the generated stack has no embedding "
            "model or index",
            "SPEC_UNSUPPORTED",
        )
    _picks(spec.get("tools", []), spec.get("mcp", []))
    if len(encode(spec)) > MAX_SPEC_BYTES:
        raise BadRequest(
            f"The spec is larger than {MAX_SPEC_BYTES // 1024} KiB; shorten the prompt or cases",
            "SPEC_TOO_LARGE",
        )
    return name, spec


def _picks(tools: object, servers: object) -> None:
    """The picked refs' shape (the CLI resolves them): `tool/` refs, one version per tool; `mcp/`
    servers with at least one tool each."""
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        raise BadRequest(f"Pick at most {MAX_TOOLS} tools", "SPEC_INVALID")
    names = set()
    for text in tools:
        ref = _ref(text, "tool")
        if ref.name in names:
            raise BadRequest(f"Pick one version of tool {ref.name}", "SPEC_INVALID")
        names.add(ref.name)
    if not isinstance(servers, list) or len(servers) > MAX_SERVERS:
        raise BadRequest(f"Pick at most {MAX_SERVERS} MCP servers", "SPEC_INVALID")
    names = set()
    for entry in servers:
        if not isinstance(entry, dict) or set(entry) != {"ref", "tools"}:
            raise BadRequest("An MCP server is {ref, tools}", "SPEC_INVALID")
        ref = _ref(entry["ref"], "mcp")
        if ref.name in names:
            raise BadRequest(f"Pick one version of MCP server {ref.name}", "SPEC_INVALID")
        names.add(ref.name)
        granted = entry["tools"]
        if not isinstance(granted, list) or not 0 < len(granted) <= MAX_SERVER_TOOLS:
            raise BadRequest(
                f"Grant {ref.name} 1 to {MAX_SERVER_TOOLS} of its tools", "SPEC_INVALID"
            )
        if not all(isinstance(tool, str) and re.fullmatch(MCP_TOOL, tool) for tool in granted):
            raise BadRequest(f"{ref.name}: not a tool name", "SPEC_INVALID")
        if len(set(granted)) != len(granted):
            raise BadRequest(f"{ref.name}: a tool is granted twice", "SPEC_INVALID")


def _ref(text: object, kind: str) -> registry.Ref:
    try:
        ref = registry.Ref.parse(text if isinstance(text, str) else "")
    except registry.RegistryError:
        ref = None
    if ref is None or ref.kind != kind:
        raise BadRequest(f"Not a pinned {kind}/name@X.Y.Z ref", "SPEC_INVALID")
    return ref


def _description(data: dict) -> str:
    text = data.get("description")
    if not isinstance(text, str):
        return ""
    text = " ".join(registry.clean(text)[0].split())
    return text if len(text) <= MAX_DESCRIPTION else text[: MAX_DESCRIPTION - 1] + "…"


def _server_name(name: str) -> str | None:
    """Why `name` cannot name a stack's MCP server (`agentspec._servers`), or None."""
    if not re.fullmatch(SERVICE_NAME, name) or name in RESERVED or name in STACK_SERVICES:
        return f"{name} cannot name a stack's MCP server"
    return None


def _declared(version_dir: Path) -> list[dict] | None:
    """The tools `mcp.yaml` declares, with their effect, read only from a regular file (not a
    link or a FIFO); None when the file or any tool in it cannot be read (the CLI's `McpSpec`
    would refuse it: MCP_INVALID)."""
    path = version_dir / "mcp.yaml"
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        data = load_file(path, MCP_FILE_LIMIT)
    except (OSError, StrictYamlError):
        return None
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, dict) or not 0 < len(tools) <= MAX_SERVER_TOOLS:
        return None
    shown = []
    for name, value in tools.items():
        effect = value.get("effect") if isinstance(value, dict) else None
        if not isinstance(name, str) or not re.fullmatch(MCP_TOOL, name) or effect not in EFFECTS:
            return None
        shown.append({"name": name, "effect": effect})
    return shown


def parts(search: list[Path]) -> dict:
    """What the pickers offer: every `tool/` and `mcp/` version in `search` (as `registry.resolve`
    reads them: the first root that has a version, none under a symlinked directory), newest
    version of a name first, with each server's declared tools. Unreadable versions are left out
    and counted in `skipped`."""

    def place(row: dict) -> tuple:
        ref = registry.Ref.parse(row["ref"])
        return (ref.kind, ref.name, tuple(-int(part) for part in ref.version.split(".")))

    listed = [row for row in registry.listing(search) if row["ref"].split("/")[0] in PART_KINDS]
    # By kind and name, newest version first. The sort is stable, so the rows of one ref keep
    # their root order and the first is the one `resolve` reads; the cap cuts in shown order.
    rows = sorted(listed, key=place)
    # `resolve` refuses every version of a name whose directory is a link in any root.
    unsafe = {row["ref"].split("@")[0] for row in rows if row["symlinked"]}
    seen: set[str] = set()
    shown: dict[str, list[dict]] = {kind: [] for kind in PART_KINDS}
    skipped, truncated = 0, False
    for row in rows:
        ref = registry.Ref.parse(row["ref"])
        if row["ref"] in seen:
            continue
        seen.add(row["ref"])
        if row["ref"].split("@")[0] in unsafe:
            skipped += 1
            continue
        if len(shown[ref.kind]) >= MAX_PARTS:
            truncated = True
            continue
        path = Path(row["path"])
        try:
            manifest = registry.read_manifest(path, ref)
        except registry.RegistryError:
            skipped += 1
            continue
        entry = {
            "ref": str(ref),
            "name": ref.name,
            "version": ref.version,
            "root_kind": registry.root_kind(Path(row["root"])),
            "runs_code": registry.runs_code(ref.kind),
            "description": _description(manifest),
        }
        if ref.kind == "mcp":
            declared = _declared(path)
            entry["tools"] = declared or []
            entry["unusable"] = _server_name(ref.name)
            if declared is None and entry["unusable"] is None:
                entry["unusable"] = (
                    f"{ref}'s mcp.yaml cannot be read; `agentbreeder registry show {ref}` shows it"
                )
        shown[ref.kind].append(entry)

    return {
        "tools": shown["tool"],
        "mcp": shown["mcp"],
        "skipped": skipped,
        "truncated": truncated,
    }


def write_request(body: object) -> tuple[str, bool]:
    """The previewed plan digest and the "this code runs in agents" answer of a create."""
    digest = body.get("digest") if isinstance(body, dict) else None
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise BadRequest("Preview the agent first", "PREVIEW_REQUIRED")
    return digest, body.get("code_confirmed") is True


def encode(spec: dict) -> bytes:
    # Compact, as the browser's JSON.stringify measures it (the size check matches the form's).
    return json.dumps(spec, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


@contextlib.contextmanager
def spec_file(spec: dict) -> Iterator[Path]:
    """The spec as a 0600 file in a fresh 0700 directory, removed on the way out. The CLI
    refuses a spec file that is a link, not ours, or readable by others (SPEC_UNSAFE)."""
    directory = Path(tempfile.mkdtemp(prefix="agentbreeder-builder-"))
    try:
        os.chmod(directory, 0o700)
        path = directory / "spec.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encode(spec))
        yield path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _strings(value: object, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise BadOutput
    if not all(isinstance(item, str) for item in value):
        raise BadOutput
    return list(value)


def _files(value: object) -> list[dict]:
    if not isinstance(value, list) or not 0 < len(value) <= MAX_FILES:
        raise BadOutput
    shown, total = [], 0
    for entry in value:
        if not isinstance(entry, dict):
            raise BadOutput
        path, sha, content = entry.get("path"), entry.get("sha256"), entry.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise BadOutput
        if not isinstance(sha, str) or not DIGEST.fullmatch(sha):
            raise BadOutput
        total += len(content)
        if total > MAX_CONTENT:
            raise BadOutput
        shown.append({"path": path, "sha256": sha, "content": content})
    return shown


def _refs(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > 100:
        raise BadOutput
    shown = []
    for entry in value:
        if not isinstance(entry, dict):
            raise BadOutput
        ref, kind = entry.get("ref"), entry.get("kind")
        root_kind, runs, sha = entry.get("root_kind"), entry.get("runs_code"), entry.get("sha256")
        if not isinstance(ref, str) or not isinstance(kind, str):
            raise BadOutput
        if root_kind not in ROOT_KINDS or not isinstance(runs, bool):
            raise BadOutput
        if not isinstance(sha, str) or not DIGEST.fullmatch(sha):
            raise BadOutput
        shown.append(
            {"ref": ref, "kind": kind, "root_kind": root_kind, "runs_code": runs, "sha256": sha}
        )
    return shown


def _grants(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > 10:
        raise BadOutput
    shown = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("server"), str):
            raise BadOutput
        shown.append({"server": entry["server"], "tools": _strings(entry.get("tools"), 100)})
    return shown


def summary(data: object, name: str, dry_run: bool, model: str, directory: str) -> dict:
    """`new agent --from-spec --json`'s answer, checked against its schema 1 shape and reduced
    to what the preview shows; `model` and `directory` are what was asked for. Raises BadOutput
    for anything else."""
    try:
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise BadOutput
        if data.get("dry_run") is not dry_run or data.get("name") != name:
            raise BadOutput
        if data.get("spec_version") != SPEC_VERSION or data.get("model") != model:
            raise BadOutput
        if data.get("directory") != directory:  # what is shown is where it is registered
            raise BadOutput
        text = {key: data.get(key) for key in ("directory", "model", "model_id", "review_by")}
        if not all(isinstance(value, str) for value in text.values()):
            raise BadOutput
        combination = data.get("combination")
        if not isinstance(combination, dict) or not isinstance(combination.get("verified"), bool):
            raise BadOutput
        parts = _strings(combination.get("parts"), 10)
        refs = _refs(data.get("refs"))
        # Recomputed, so a preview can never hide code its own rows show.
        expected = any(row["runs_code"] and row["root_kind"] != "builtin" for row in refs)
        if data.get("runs_code") is not expected:
            raise BadOutput
        digest = data.get("plan_sha256")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise BadOutput
        return {
            "name": name,
            **text,
            "aliases": _strings(data.get("aliases"), 10),
            "cases": _strings(data.get("cases"), 100),
            "files": _files(data.get("files")),
            "combination": {"parts": parts, "verified": combination["verified"]},
            "refs": refs,
            "runs_code": expected,
            "grants": _grants(data.get("grants")),
            "keys": _strings(data.get("keys"), 20),
            "plan_sha256": digest,
        }
    except BadOutput:
        raise BadOutput("`new agent --from-spec --json` printed an unexpected answer") from None
