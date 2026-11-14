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
SPEC_UNSUPPORTED, checked here first so no child runs), and so are tools and MCP servers until
the builder's pickers exist.

Pure request and answer checks plus the temporary file: no engine module is loaded, so the
server process keeps its allowlist (`tests/test_center_reads.py`). The CLI validates the spec
itself; these checks keep the request to the spec's shape and size.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from agentbreeder.center.files import BadRequest
from agentbreeder.center.onboard import (
    DIGEST,
    MODEL_ID,
    NAME,
    PROVIDERS,
    ROOT_KINDS,
    BadOutput,
)

SPEC_VERSION = 1  # `agentspec.SPEC_VERSION`
MAX_SPEC_BYTES = 256 * 1024  # `agentspec.MAX_SPEC_BYTES`
FIELDS = frozenset(
    {"schema_version", "name", "model", "model_id", "max_model_calls", "prompt", "evals"}
    | {"tools", "knowledge", "mcp"}
)
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
    if spec.get("tools", []) != [] or spec.get("mcp", []) != []:
        # The pickers (and their code confirm) are the builder's next part.
        raise BadRequest(
            "Tools and MCP servers are not part of the builder yet", "SPEC_UNSUPPORTED"
        )
    if len(encode(spec)) > MAX_SPEC_BYTES:
        raise BadRequest(
            f"The spec is larger than {MAX_SPEC_BYTES // 1024} KiB; shorten the prompt or cases",
            "SPEC_TOO_LARGE",
        )
    return name, spec


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
