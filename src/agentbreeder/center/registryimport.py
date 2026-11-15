"""Import a registry version by path (#825, M9a console half; console spec §4.3, §8.4, §8.5).

The console runs `registry import --project DIR [--as NAME] [--version X.Y.Z] (--dry-run |
--expect-digest D) --json -- PATH` (contract: docs/registry.md "Importing, and writing exactly
what was reviewed"). Two requests (S-04): a dry run that lists the staged copy's files (the
`registry show` shape) and its digest, then the write bound to that digest, which the CLI refuses
when the source changed since the review (`DIGEST_MISMATCH`).

PATH is a host path typed in the browser, so it is the riskiest input here:

- it must be absolute, at most `MAX_PATH` characters, with no control, bidi or invisible
  character, and it reaches argv only after `--`, one word that is never read as a flag;
- the server reads nothing at PATH itself. The CLI child copies it once into a private staging
  directory and refuses anything that is not a registry version directory (no valid
  `manifest.yaml`: MANIFEST_INVALID; a symlink, hardlink or special file: ENTRY_UNSAFE; over
  `registry.MAX_FILES` files or `registry.MAX_BYTES`: ENTRY_UNSAFE) before it lists any file,
  so a session cannot use import to read an arbitrary file or directory;
- the file text it lists is capped and cleaned by the CLI (`registry.SHOW_*_LIMIT`, control,
  bidi and invisible characters removed); `import_answer` checks that again and refuses an answer
  that is not, so the browser never gets raw control or bidi characters from here.

Code-bearing kinds (`registry.CODE_KINDS`: tool, service, eval, mcp, runtime) take the three-tier
confirm (`onboard.py`): the preview, the "this code runs in agents" checkbox, the button. The
server learns the kind only from the CLI, so before a write it runs its own fresh dry run and
refuses one of those kinds without `code_confirmed: true` (`Center.registry_import`). The confirm
is a UX control, not a boundary (spec §10.1 I1): an imported version takes effect only when an
`agent.yaml` references it and a release passes.

`registry fork --json` prints the same object, so `Center.registry_fork` checks its answers with
`import_answer` too (#825, part 2).

Pure request and answer checks; imports only `registry` and `center.files` (on the server's
allowlist, `tests/test_center_reads.py`).
"""

from __future__ import annotations

import re
from typing import Any

from agentbreeder import registry
from agentbreeder.center.files import BadRequest

MAX_PATH = 4096
# C0 and C1 controls, plus everything `registry.clean(name=True)` removes (bidi, separators,
# invisible format characters): checked with `registry.clean` below, this is the fast part.
CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
TIMEOUT = 60  # one staged copy of at most registry.MAX_BYTES, hashed; the console's default
# The CLI's caps on what a dry run lists: the manifest is kept in full, other text within
# SHOW_TOTAL_LIMIT (characters are never more than bytes).
MAX_TEXT = registry.SHOW_TOTAL_LIMIT + max(registry.SHOW_FILE_LIMIT, registry.MANIFEST_LIMIT)
KEYS = (
    "ref", "kind", "name", "version", "source_ref", "sha256", "source", "root", "root_kind",
    "path", "dry_run", "runs_code",
)  # fmt: skip
ROOT_KINDS = ("project", "path", "builtin")
# A file entry as `registry show` lists it; any other key is dropped.
FILE_KEYS = (
    "path", "type", "size", "executable", "sha256", "binary", "truncated", "omitted", "text",
    "stripped",
)  # fmt: skip


class BadOutput(ValueError):
    """The CLI's answer does not match the contract."""


def import_request(body: object) -> dict:
    """{path, name|None, version|None, digest|None, code_confirmed} from the browser's JSON, or
    BadRequest. Nothing here touches the filesystem."""
    if not isinstance(body, dict):
        raise BadRequest("Send a JSON object", "IMPORT_INVALID")
    path = body.get("path")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > MAX_PATH:
        raise BadRequest("Type the absolute path of a version directory", "PATH_INVALID")
    if CONTROLS.search(path) or registry.clean(path, name=True)[1]:
        raise BadRequest("That path has control or invisible characters", "PATH_INVALID")
    path = path.rstrip("/") or "/"
    name = body.get("name")
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
    digest = body.get("digest")
    if digest in (None, ""):
        digest = None
    elif not isinstance(digest, str) or not registry.DIGEST.fullmatch(digest):
        raise BadRequest("digest must be sha256:<64 hex digits>", "DIGEST_INVALID")
    confirmed = body.get("code_confirmed", False)
    if not isinstance(confirmed, bool):
        raise BadRequest("code_confirmed must be true or false", "IMPORT_INVALID")
    return {
        "path": path,
        "name": name,
        "version": version,
        "digest": digest,
        "code_confirmed": confirmed,
    }


def import_argv(project: dict, request: dict, *, dry_run: bool) -> list[str]:
    """The `registry import` argv: a dry run, or the write bound to the reviewed digest. PATH
    comes last, after `--`."""
    argv = ["registry", "import", "--project", project["path"]]
    if request["name"] is not None:
        argv += ["--as", request["name"]]
    if request["version"] is not None:
        argv += ["--version", request["version"]]
    argv += ["--dry-run"] if dry_run else ["--expect-digest", request["digest"]]
    return [*argv, "--json", "--", request["path"]]


def _text(value: object, field: str, limit: int = MAX_PATH) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise BadOutput(f"{field} is not text")
    if registry.clean(value)[1]:
        raise BadOutput(f"{field} has control, bidi or invisible characters")
    return value


def _file(item: object) -> dict:
    if not isinstance(item, dict):
        raise BadOutput("a file entry is not an object")
    path = _text(item.get("path"), "a file path")
    if not path or registry.clean(path, name=True)[1]:
        raise BadOutput("a file path is not a plain name")
    if item.get("type") not in ("file", "dir"):
        raise BadOutput("a file entry has an unknown type")
    text = item.get("text")
    if text is not None:
        _text(text, "a file's text", MAX_TEXT)
    for flag in ("executable", "binary", "truncated", "omitted", "stripped"):
        if flag in item and not isinstance(item[flag], bool):
            raise BadOutput(f"a file's {flag} is not true or false")
    size = item.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise BadOutput("a file's size is not a count")
    digest = item.get("sha256")
    if digest is not None and not (isinstance(digest, str) and registry.DIGEST.fullmatch(digest)):
        raise BadOutput("a file's sha256 is not a digest")
    return {key: item[key] for key in FILE_KEYS if key in item}


def import_answer(data: object, *, dry_run: bool) -> dict:
    """The CLI's `registry import --json` object, checked against the contract (docs/registry.md),
    or BadOutput. A dry run must list the staged files; a write must not."""
    if not isinstance(data, dict) or any(key not in data for key in KEYS):
        raise BadOutput("the answer is not the import object")
    try:
        ref = registry.Ref.parse(_text(data["ref"], "ref"))
        registry.Ref.parse(_text(data["source_ref"], "source_ref"))
    except registry.RegistryError as problem:
        raise BadOutput("a ref in the answer does not parse") from problem
    if (data["kind"], data["name"], data["version"]) != (ref.kind, ref.name, ref.version):
        raise BadOutput("kind, name and version do not match the ref")
    if not isinstance(data["sha256"], str) or not registry.DIGEST.fullmatch(data["sha256"]):
        raise BadOutput("sha256 is not a digest")
    for key in ("source", "root", "path"):
        if not _text(data[key], key).startswith("/"):
            raise BadOutput(f"{key} is not an absolute path")
    if data["root_kind"] not in ROOT_KINDS:
        raise BadOutput("root_kind is unknown")
    if data["dry_run"] is not dry_run:
        raise BadOutput("dry_run is not what was asked")
    if data["runs_code"] is not registry.runs_code(ref.kind):
        raise BadOutput("runs_code does not match the kind")
    files: Any = data.get("files")
    if dry_run:
        if not isinstance(files, list) or len(files) > registry.MAX_FILES:
            raise BadOutput("a dry run lists the staged files")
        files = [_file(item) for item in files]
        if sum(len(item.get("text") or "") for item in files) > MAX_TEXT:
            raise BadOutput("the files' text is over the CLI's cap")
    elif files is not None:
        raise BadOutput("a write does not list files")
    return {**{key: data[key] for key in KEYS}, **({"files": files} if dry_run else {})}
