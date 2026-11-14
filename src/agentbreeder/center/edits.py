"""Prompt editing and "Move agent to this version" (#826, M10 console half): pure request checks
and the CLI argv. The console never writes a registry or `agent.yaml` itself; it runs

- `registry edit REF --set PATH - --version X.Y.Z --project DIR (--dry-run | --expect-digest D)
  --json` with the edited text on stdin (S-12: free text never goes in argv, which every local
  user can read through `ps`), and
- `agent set-ref OLD NEW --project DIR (--dry-run | --expect-base D) --json`.

Each is two requests (S-04): a dry run that returns the diff and the digest of what would be
written, then the write with that digest, which the CLI refuses when the bytes changed since the
review (`DIGEST_MISMATCH`, `BASE_MISMATCH`). The version is exact (`--version`, never `--bump`),
so the button's "Save as X.Y.Z" is what is written; a version that exists already (another tab)
is the CLI's `VERSION_EXISTS`. Imports only `registry` (already on the server's allowlist)."""

from __future__ import annotations

from agentbreeder import registry
from agentbreeder.center.files import BadRequest

# Kinds the console edits as text. Eval sets come with their case editor (M10 PR 2); tools have
# their own flow and confirm (M12).
EDITABLE = frozenset({"prompt"})
MAX_TEXT = 512 * 1024  # characters; under the editor body cap (1 MiB of JSON)
EDIT_KIND = "registry-edit"
SET_REF_KIND = "agent-set-ref"


def _ref(value: object, field: str) -> registry.Ref:
    try:
        return registry.Ref.parse(str(value or ""))
    except registry.RegistryError as problem:
        raise BadRequest(f"{field}: {registry.clean(str(problem))[0]}", "REF_INVALID") from None


def _digest(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not registry.DIGEST.fullmatch(value):
        raise BadRequest(f"{field} must be sha256:<64 hex digits>", "DIGEST_INVALID")
    return value


def edit_request(body: object) -> dict:
    """{ref, path, text, version, digest|None} from the browser's JSON, or BadRequest."""
    if not isinstance(body, dict):
        raise BadRequest("Send a JSON object", "EDIT_INVALID")
    ref = _ref(body.get("ref"), "ref")
    if ref.kind not in EDITABLE:
        raise BadRequest(f"The console edits {', '.join(sorted(EDITABLE))} versions", "EDIT_KIND")
    path = body.get("path")
    if not isinstance(path, str):
        raise BadRequest("Name the file to change", "EDIT_INVALID")
    try:
        path = registry.edit_path(path)
    except registry.RegistryError as problem:
        raise BadRequest(registry.clean(str(problem))[0], problem.code) from None
    text = body.get("text")
    if not isinstance(text, str):
        raise BadRequest("Send the file's text", "EDIT_INVALID")
    if len(text) > MAX_TEXT:
        raise BadRequest(f"The text is over {MAX_TEXT} characters", "EDIT_TOO_LARGE")
    version = body.get("version")
    if not isinstance(version, str) or not registry.VERSION.fullmatch(version):
        raise BadRequest("version must be X.Y.Z", "VERSION_INVALID")
    return {
        "ref": ref,
        "path": path,
        "text": text,
        "version": version,
        "digest": _digest(body.get("digest"), "digest"),
    }


def edit_argv(project: dict, request: dict) -> list[str]:
    """The `registry edit` argv: a dry run without a digest, else the write bound to it."""
    mode = ["--expect-digest", request["digest"]] if request["digest"] else ["--dry-run"]
    return [
        "registry", "edit", str(request["ref"]),
        "--set", request["path"], "-",
        "--version", request["version"],
        "--project", project["path"],
        *mode, "--json",
    ]  # fmt: skip


def set_ref_request(body: object) -> dict:
    """{old, new, base|None} from the browser's JSON, or BadRequest. The CLI makes the real
    checks (declared, once, same kind, resolves); this keeps argv to two canonical refs."""
    if not isinstance(body, dict):
        raise BadRequest("Send a JSON object", "REF_INVALID")
    old, new = _ref(body.get("old"), "old"), _ref(body.get("new"), "new")
    if old == new:
        raise BadRequest("The agent already uses that version", "REF_UNCHANGED")
    return {"old": old, "new": new, "base": _digest(body.get("base"), "base")}


def set_ref_argv(project: dict, request: dict) -> list[str]:
    mode = ["--expect-base", request["base"]] if request["base"] else ["--dry-run"]
    return [
        "agent", "set-ref", str(request["old"]), str(request["new"]),
        "--project", project["path"], *mode, "--json",
    ]  # fmt: skip
