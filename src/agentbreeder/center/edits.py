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
is the CLI's `VERSION_EXISTS`. Imports only `registry` (already on the server's allowlist).

Each kind the console edits is an `EditKind` in `KINDS`: which files of a version it may change,
and whether a save needs the code confirm (the three-tier confirm of `onboard.py`: the preview,
the "this code runs in agents" checkbox, the button). Prompts edit any text file; eval sets only
their cases files (JSON lines, checked by the CLI as `release` loads them: EVAL_INVALID); tools
(M12, #828) only their Python source under `src/`, with the code confirm, and the CLI's tool check
(TOOL_INVALID: allowed files, no shadowing module, every function a top-level `def` that parses)
before it writes.

Knowledge (M14, #830) edits only its documents, `docs/NAME.md` or `docs/NAME.txt` (the files the
runtime indexes), and may add a new one (`--set` creates it) or remove one: the remove mode is
`registry edit REF --remove PATH --version X.Y.Z --project DIR (--dry-run | --expect-digest D)
--json`, with the same dry run, digest and per-kind path check, and nothing on stdin. Only kinds
with `remove` take it. Documents land in a git-tracked registry; the screen says so before Save.

"Fork to edit" (#828): a version outside the project registry (built in, or on
AGENTBREEDER_REGISTRY_PATH) is not edited in place. `registry fork REF --version X.Y.Z --project
DIR (--dry-run | --expect-digest D) --json` copies it into the project registry as a new version,
reviewed first the same way (S-04); the copy is then edited like any project version. M9a
(#825) widens it to every kind but an agent package, adds `--as NAME`, and makes a code-bearing
fork take the three-tier confirm (the server checks it on its own dry run, as an import)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentbreeder import registry
from agentbreeder.center.files import BadRequest


@dataclass(frozen=True)
class EditKind:
    """A kind the console edits: `paths` must match the whole edit path; `code` makes a save
    (not a review) need `code_confirmed: true`, because the files run in agents."""

    paths: re.Pattern
    what: str  # the files, as words for the refusal ("eval case files (*.jsonl)")
    code: bool = False
    remove: bool = False  # whether a file of the kind may be removed (`registry edit --remove`)


KINDS = {
    "prompt": EditKind(re.compile(r".+"), "files"),
    # The console opens only the cases file the manifest names; the CLI checks that one.
    "eval": EditKind(re.compile(r"[^/]+\.jsonl"), "eval case files (NAME.jsonl at the top)"),
    # A tool's code: only Python under src/ (tool.yaml, which the model sees, is not edited here).
    "tool": EditKind(re.compile(r"src/.+\.py"), "Python source files (src/**/*.py)", code=True),
    # A knowledge version's documents: the runtime indexes docs/*.md and docs/*.txt (case as
    # written, not nested). Adding one is a --set of a new path. Names are plain (letters,
    # digits, `.`, `_`, `-`; no space, control character or leading dot), stricter than the
    # CLI's, so another name is edited in a terminal.
    "knowledge": EditKind(
        re.compile(r"docs/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.(md|txt)"),
        "documents (docs/NAME.md or docs/NAME.txt)",
        remove=True,
    ),
}
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
    """{ref, path, text|None, remove, version, digest|None} from the browser's JSON, or
    BadRequest. `remove: true` (kinds with `remove` only) removes `path` and takes no text."""
    if not isinstance(body, dict):
        raise BadRequest("Send a JSON object", "EDIT_INVALID")
    ref = _ref(body.get("ref"), "ref")
    if ref.kind not in KINDS:
        raise BadRequest(f"The console edits {', '.join(sorted(KINDS))} versions", "EDIT_KIND")
    path = body.get("path")
    if not isinstance(path, str):
        raise BadRequest("Name the file to change", "EDIT_INVALID")
    try:
        path = registry.edit_path(path)
    except registry.RegistryError as problem:
        raise BadRequest(registry.clean(str(problem))[0], problem.code) from None
    kind = KINDS[ref.kind]
    if not kind.paths.fullmatch(path):
        raise BadRequest(f"The console edits only {kind.what} of a {ref.kind}", "EDIT_PATH")
    remove = body.get("remove", False)
    if not isinstance(remove, bool):
        raise BadRequest("remove is true or left out", "EDIT_INVALID")
    text = body.get("text")
    if remove:
        if not kind.remove:
            raise BadRequest(f"The console does not remove files of a {ref.kind}", "EDIT_REMOVE")
        if text is not None:
            raise BadRequest("A removal takes no text", "EDIT_INVALID")
    elif not isinstance(text, str):
        raise BadRequest("Send the file's text", "EDIT_INVALID")
    elif len(text) > MAX_TEXT:
        raise BadRequest(f"The text is over {MAX_TEXT} characters", "EDIT_TOO_LARGE")
    version = body.get("version")
    if not isinstance(version, str) or not registry.VERSION.fullmatch(version):
        raise BadRequest("version must be X.Y.Z", "VERSION_INVALID")
    digest = _digest(body.get("digest"), "digest")
    if kind.code and digest is not None and body.get("code_confirmed") is not True:
        raise BadRequest(
            "Confirm that you reviewed this code: it runs in agents", "CODE_NOT_CONFIRMED"
        )
    return {
        "ref": ref,
        "path": path,
        "text": None if remove else text,
        "remove": remove,
        "version": version,
        "digest": digest,
    }


def edit_argv(project: dict, request: dict) -> list[str]:
    """The `registry edit` argv: a dry run without a digest, else the write bound to it; the
    text on stdin (`--set PATH -`), or `--remove PATH` with nothing on stdin."""
    mode = ["--expect-digest", request["digest"]] if request["digest"] else ["--dry-run"]
    change = (
        ["--remove", request["path"]] if request["remove"] else ["--set", request["path"], "-"]
    )
    return [
        "registry", "edit", str(request["ref"]),
        *change,
        "--version", request["version"],
        "--project", project["path"],
        *mode, "--json",
    ]  # fmt: skip


def fork_request(body: object) -> dict:
    """{ref, name|None, version|None, digest|None, code_confirmed} from the browser's JSON, or
    BadRequest. Any kind but an agent package is forked (#825, M9a: a built-in service version
    too), under another name (`as`), another exact version, or both; at least one must change.
    `code_confirmed` is checked by the server against its own dry run (code-bearing kinds)."""
    if not isinstance(body, dict):
        raise BadRequest("Send a JSON object", "FORK_INVALID")
    ref = _ref(body.get("ref"), "ref")
    if ref.kind == registry.PACKAGE_KIND:
        raise BadRequest("Agent packages are not forked here", "FORK_KIND")
    name = body.get("as")
    if name in (None, ""):
        name = None
    elif not isinstance(name, str) or not registry.NAME.fullmatch(name):
        raise BadRequest(
            "A name is lowercase letters, digits and dashes, starting with a letter or digit, "
            "at most 63 characters",
            "NAME_INVALID",
        )
    version = body.get("version")
    if version in (None, ""):
        version = None
    elif not isinstance(version, str) or not registry.VERSION.fullmatch(version):
        raise BadRequest("version must be X.Y.Z", "VERSION_INVALID")
    if name in (None, ref.name) and version in (None, ref.version):
        raise BadRequest("Fork it as a new version or under a new name", "FORK_UNCHANGED")
    confirmed = body.get("code_confirmed", False)
    if not isinstance(confirmed, bool):
        raise BadRequest("code_confirmed must be true or false", "FORK_INVALID")
    return {
        "ref": ref,
        "name": name,
        "version": version,
        "digest": _digest(body.get("digest"), "digest"),
        "code_confirmed": confirmed,
    }


def fork_argv(project: dict, request: dict, *, dry_run: bool | None = None) -> list[str]:
    """The `registry fork` argv: a dry run (without a digest, or when asked), else the copy
    bound to the reviewed digest."""
    if dry_run is None:
        dry_run = request["digest"] is None
    mode = ["--dry-run"] if dry_run else ["--expect-digest", request["digest"]]
    rename = ["--as", request["name"]] if request["name"] else []
    version = ["--version", request["version"]] if request["version"] else []
    return [
        "registry", "fork", str(request["ref"]), *rename, *version,
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
