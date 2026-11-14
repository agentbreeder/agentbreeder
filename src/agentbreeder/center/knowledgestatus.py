"""A knowledge version's index status in the console (#830, M14 console half, part 1):
`agentbreeder knowledge status REF --json` (contract: docs/commands/knowledge.md and
docs/knowledge.md), run as an uncached CLI child.

Pure functions: the server checks the query with `status_request`, builds the argv with
`status_argv` and passes the answer through `status_answer` before the browser sees it.

- The database is read only by the CLI child, with the agent's own credential (`agent-database`,
  written by `services up`); the console server never reads a credential.
- The CLI never reads chunk text, and nothing here could carry it: only counts, the table and
  schema names, timestamps, the embedding alias and dimensions, a state and the CLI's notes go on.
- The answer is checked again against local copies of the CLI's rules (a test keeps them equal;
  the server does not load `agentbreeder.knowledge`'s database command), so a changed or broken
  answer is an error, never a quiet "fresh".
- A refusal (exit 2, `{"schema_version": 1, "error": {code, message}}`) comes back as the
  envelope's coded error; the screen shows it as "Unavailable" with the CLI's next step.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from agentbreeder import registry
from agentbreeder.center.files import BadRequest

# `knowledge`'s own rules (a test keeps them equal).
KNOWLEDGE_NAME = re.compile(rf"knowledge/{registry.NAME.pattern}")
KNOWLEDGE_REF = re.compile(rf"knowledge/{registry.NAME.pattern}@{registry.VERSION.pattern}")
TABLE = re.compile(r"[a-z0-9_]{1,63}")
SCHEMA = re.compile(r"[A-Za-z_][A-Za-z0-9_$-]{0,62}")
WHEN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
CODE = re.compile(r"[A-Z][A-Z0-9_]{2,39}")
STATES = ("not_indexed", "fresh", "stale")
MAX_DIMENSIONS = 16000  # `knowledge.MAX_DIMENSIONS`
MAX_ROWS = 50_000  # `knowledge.MAX_ROWS`: chunks expected or indexed
MAX_DOCUMENTS = registry.MAX_FILES  # more documents is KNOWLEDGE_TOO_LARGE
MAX_VERSIONS = 500  # versions of one name passed on; more is an invalid answer
MAX_ALIAS = 200
MAX_NOTE = 400
MAX_NOTES = 10
MAX_MESSAGE = 600  # the CLI cuts its message to 600 characters
# Reading up to 50 MiB of documents and chunking them comes first; each query stops after 5 s.
TIMEOUT = 120
INVALID = {
    "code": "KNOWLEDGE_STATUS_INVALID",
    "message": "The knowledge status answer does not match its contract; run `agentbreeder "
    "knowledge status` in a terminal",
}


def status_request(query: dict[str, list[str]]) -> dict:
    """The checked query: `{ref}`. Raises BadRequest."""
    ref = query.get("ref", [""])[0]
    if not (KNOWLEDGE_NAME.fullmatch(ref) or KNOWLEDGE_REF.fullmatch(ref)):
        raise BadRequest("Ref must be knowledge/NAME or knowledge/NAME@X.Y.Z", "REF_INVALID")
    return {"ref": ref}


def status_argv(project: dict, request: dict) -> list[str]:
    """`knowledge status` with the registered project and environment; only the checked ref from
    the request, never a path."""
    argv = ["knowledge", "status", request["ref"], "--project", project["path"]]
    return [*argv, "--environment", project["environment"], "--json"]


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    # Control characters and invisible format characters (bidi overrides, zero-width) go.
    cleaned = "".join(
        ch
        for ch in value
        if ch >= " " and not "\x7f" <= ch <= "\x9f" and unicodedata.category(ch) != "Cf"
    )
    return cleaned[:limit]


def _match(pattern: re.Pattern, value: Any) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _count(value: Any, top: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
        return None
    return value


def _dimensions(value: Any) -> int | None:
    count = _count(value, MAX_DIMENSIONS)
    return count if count else None


def _part(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _version(item: Any, refs: re.Pattern) -> dict | None:
    """One version's status, or None when its ref or state is not one the CLI gives."""
    if not isinstance(item, dict):
        return None
    ref = _match(refs, item.get("ref"))
    state = item.get("state")
    if ref is None or state not in STATES:
        return None
    documents = _part(item.get("documents"))
    chunks = _part(item.get("chunks"))
    updated = _part(item.get("updated"))
    embedding = _part(item.get("embedding"))
    notes = item.get("notes") if isinstance(item.get("notes"), list) else []
    return {
        "ref": ref,
        "table": _match(TABLE, item.get("table")),
        "schema": _match(SCHEMA, item.get("schema")),
        "state": state,
        "documents": {
            key: _count(documents.get(key), MAX_DOCUMENTS) for key in ("expected", "indexed")
        },
        "chunks": {
            key: _count(chunks.get(key), MAX_ROWS)
            for key in ("expected", "indexed", "fresh", "stale", "missing", "extra")
        },
        "updated": {key: _match(WHEN, updated.get(key)) for key in ("oldest", "newest")},
        "embedding": {
            "alias": _text(embedding.get("alias"), MAX_ALIAS),
            "dimensions": _dimensions(embedding.get("dimensions")),
            "table_dimensions": _dimensions(embedding.get("table_dimensions")),
        },
        "notes": [note for note in (_text(n, MAX_NOTE) for n in notes[:MAX_NOTES]) if note],
    }


def status_answer(data: object, request: dict) -> tuple[dict | None, dict | None]:
    """`(status, None)`, or `(None, problem)` with `problem` `{code, message}`: the CLI's own
    coded refusal, or KNOWLEDGE_STATUS_INVALID when the answer does not match its contract (a
    different ref, a version of another name, an unknown state, no versions). Only known keys go
    on."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None, INVALID
    failure = data.get("error")
    if isinstance(failure, dict):  # the coded refusal (exit 2)
        code = failure.get("code")
        return None, {
            "code": code
            if isinstance(code, str) and CODE.fullmatch(code)
            else "KNOWLEDGE_STATUS_FAILED",
            "message": _text(failure.get("message"), MAX_MESSAGE) or "knowledge status failed",
        }
    ref = request["ref"]
    versions = data.get("versions")
    if (
        data.get("ref") != ref
        or data.get("credential") != "agent"
        or not isinstance(versions, list)
        or not 1 <= len(versions) <= MAX_VERSIONS
    ):
        return None, INVALID
    # One version answers for itself; a name answers with its own versions only.
    refs = (
        re.compile(re.escape(ref))
        if "@" in ref
        else re.compile(rf"{re.escape(ref)}@{registry.VERSION.pattern}")
    )
    shaped = [_version(item, refs) for item in versions]
    if any(version is None for version in shaped):
        return None, INVALID
    if len({version["ref"] for version in shaped}) != len(shaped):  # each version once
        return None, INVALID
    return {
        "ref": ref,
        "environment": _text(data.get("environment"), 64),
        "database": _text(data.get("database"), 64),
        "credential": "agent",
        "versions": shaped,
    }, None
