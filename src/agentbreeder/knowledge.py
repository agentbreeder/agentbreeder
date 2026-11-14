"""Knowledge index status (#830, M14): what the registry says a knowledge version should hold,
compared with what the runtime indexed in the stack's database.

The runtime (`runtime/adk` 1.0.4–1.2.0, `runtime/rag.py`, identical in each) keeps one pgvector
table per knowledge version, `kb_<name>_<version>` (dashes and dots as underscores), created
unqualified by the agent's role, so it lands in the first schema of that role's search path
(`"$user"`, then `public`). One row per paragraph chunk: `id` (`<file stem>#<paragraph index>`),
`title`, `text`, `hash`, `embedding`, `updated`. The hash is
`sha256("gateway:<alias>:<dimensions>\\n<title>\\n<text>")`, so the expected index can be
computed here from the version's `docs/` exactly; `CHUNKER_PARITY` in the tests checks this copy
against every shipped `rag.py`.

Indexing happens at the agent's first knowledge search in each runtime process (`release`'s eval
turns do that); a new version is a new table, embedded in full.

Reads use the agent's own credential (M12 delta §3.1): the table belongs to the agent's role, so
nothing is lent. Only the catalog, `(id, hash, updated)` and the embedding column's type are
read; chunk text never is. Every value from the database is treated as hostile: ids and hashes
are only compared and counted, timestamps must be datetimes, the row count is capped.

The connection itself (the stack's environment, the `agent-database` secret, read-only options
and timeouts) is not here: `status()` takes a `query(sql, params) -> list[tuple]` callable.
"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentbreeder import registry

KIND = "knowledge"
SCHEMA_VERSION = 1
# The runtime's table name pattern (rag.table_name) and Postgres' identifier limit.
TABLE = re.compile(r"[a-z0-9_]{1,63}")
# A schema name the CLI quotes itself: no quote, dot, space or control character.
SCHEMA = re.compile(r"[A-Za-z_][A-Za-z0-9_$-]{0,62}")
MAX_DIMENSIONS = 16000  # pgvector's limit for a vector column
MAX_ROWS = 50_000  # more chunks than this (expected or indexed) is KNOWLEDGE_TOO_LARGE
MAX_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_DIMENSIONS = 768  # rag.KnowledgeSet's default

Query = Callable[[str, dict], list[tuple]]


class KnowledgeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


# ------------------------------------------------------------------ refs and versions


def parse_ref(text: str) -> tuple[str, str | None]:
    """`knowledge/NAME` (every version) or `knowledge/NAME@X.Y.Z` → (name, version or None)."""
    match = re.fullmatch(r"([a-z]+)/([^@/]+)(?:@([0-9.]+))?", text or "")
    if not match or match[1] != KIND or not registry.NAME.fullmatch(match[2]):
        raise KnowledgeError("REF_INVALID", "REF must be knowledge/NAME or knowledge/NAME@X.Y.Z")
    if match[3] is not None and not registry.VERSION.fullmatch(match[3]):
        raise KnowledgeError("REF_INVALID", "the version must be X.Y.Z")
    return match[2], match[3]


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def versions(name: str, version: str | None, search: list[Path]) -> list[registry.Resolved]:
    """The versions to report, oldest first: one, or every version of NAME in the roots."""
    if version is not None:
        wanted = [version]
    else:
        found = {
            row["ref"].rsplit("@", 1)[1]
            for row in registry.listing(search, f"{KIND}/{name}@")
            if row["ref"].startswith(f"{KIND}/{name}@")
        }
        wanted = sorted(found, key=_version_key)
        if not wanted:
            raise KnowledgeError("REF_NOT_FOUND", f"no version of {KIND}/{name} in the registry")
    result = []
    for each in wanted:
        try:
            result.append(registry.resolve(registry.Ref(KIND, name, each), search))
        except registry.RegistryError as error:
            raise KnowledgeError(error.code, str(error).removeprefix(f"[{error.code}] ")) from None
    return result


# ------------------------------------------------------------------ the runtime's chunker (copy)


def table_name(name: str, version: str) -> str:
    """rag.table_name."""
    raw = f"kb_{name}_{version}".replace("-", "_").replace(".", "_")
    if not TABLE.fullmatch(raw):
        raise KnowledgeError("KNOWLEDGE_INVALID", f"knowledge table name {raw!r} is unsafe")
    return raw


def load_chunks(texts: list[tuple[str, str]], limit: int | None = None) -> list[dict]:
    """rag.load_chunks over (file stem, text) pairs in the runtime's file order; more than
    `limit` chunks is KNOWLEDGE_TOO_LARGE (checked while chunking)."""
    chunks = []
    for stem, text in texts:
        title = text.splitlines()[0].lstrip("# ").strip() if text else stem
        for index, paragraph in enumerate(p.strip() for p in text.split("\n\n")):
            if paragraph and not paragraph.startswith("#"):
                chunks.append({"id": f"{stem}#{index}", "title": title, "text": paragraph})
                if limit is not None and len(chunks) > limit:
                    raise KnowledgeError(
                        "KNOWLEDGE_TOO_LARGE", f"the documents make more than {limit} chunks"
                    )
    return chunks


def chunk_hash(alias: str, dimensions: int, title: str, text: str) -> str:
    """rag.KnowledgeSet.sync's content hash."""
    source = f"gateway:{alias}:{dimensions}\n{title}\n{text}"
    return hashlib.sha256(source.encode()).hexdigest()


def _doc_files(docs: Path) -> list[Path]:
    """`docs/*.md` then `docs/*.txt`, sorted together (rag.load_chunks's glob), regular files
    only: the runtime would follow a link, which a registry version must not hold."""
    if docs.is_symlink():
        raise KnowledgeError("KNOWLEDGE_INVALID", "docs/ must not be a symlink")
    try:
        entries = sorted(docs.iterdir()) if docs.is_dir() else []
    except OSError as error:
        raise KnowledgeError("KNOWLEDGE_INVALID", f"cannot list docs/: {error.strerror}") from None
    files = [p for p in entries if any(fnmatch.fnmatchcase(p.name, g) for g in ("*.md", "*.txt"))]
    if len(files) > registry.MAX_FILES:
        raise KnowledgeError("KNOWLEDGE_TOO_LARGE", f"more than {registry.MAX_FILES} documents")
    for path in files:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise KnowledgeError(
                "KNOWLEDGE_INVALID",
                f"docs/{registry.clean(path.name, name=True)[0]} is not a file",
            )
    return sorted(files)


def _read_text(path: Path) -> tuple[str, int]:
    """(text as the runtime reads it, size in bytes)."""
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as reader:  # a FIFO swapped in never blocks the read
            if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                raise KnowledgeError(
                    "KNOWLEDGE_INVALID",
                    f"docs/{registry.clean(path.name, name=True)[0]} is not a file",
                )
            data = reader.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise KnowledgeError(
            "KNOWLEDGE_INVALID",
            f"cannot read docs/{registry.clean(path.name, name=True)[0]}: {error.strerror}",
        ) from None
    if len(data) > MAX_FILE_BYTES:
        raise KnowledgeError(
            "KNOWLEDGE_INVALID", f"docs/{registry.clean(path.name, name=True)[0]} is too large"
        )
    try:
        # read_text(encoding="utf-8") in the runtime: universal newlines
        return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"), len(data)
    except UnicodeDecodeError:
        raise KnowledgeError(
            "KNOWLEDGE_INVALID",
            f"docs/{registry.clean(path.name, name=True)[0]} is not UTF-8 (the runtime "
            "could not read it)",
        ) from None


@dataclass
class Expected:
    """What the runtime will index for one version."""

    ref: str
    name: str
    version: str
    table: str
    alias: str
    dimensions: int
    documents: int
    chunks: dict[str, str]  # chunk id -> hash

    @property
    def stems(self) -> set[str]:
        return {chunk_id.split("#", 1)[0] for chunk_id in self.chunks}


def expected(hit: registry.Resolved) -> Expected:
    manifest = registry.read_manifest(hit.path, hit.ref)
    embedding = manifest.get("embedding") or {}
    if not isinstance(embedding, dict):
        raise KnowledgeError("KNOWLEDGE_INVALID", f"{hit.ref}: embedding must be a mapping")
    alias = embedding.get("alias", "")
    raw_dimensions = embedding.get("dimensions", DEFAULT_DIMENSIONS)
    if not isinstance(alias, str) or isinstance(raw_dimensions, bool):
        raise KnowledgeError("KNOWLEDGE_INVALID", f"{hit.ref}: embedding alias or dimensions")
    try:
        dimensions = int(raw_dimensions)
    except (TypeError, ValueError, OverflowError):
        dimensions = 0
    if not 1 <= dimensions <= MAX_DIMENSIONS:
        raise KnowledgeError(
            "KNOWLEDGE_INVALID",
            f"{hit.ref}: embedding.dimensions must be a number from 1 to {MAX_DIMENSIONS}",
        )
    files = _doc_files(hit.path / "docs")
    texts, total = [], 0
    for path in files:
        text, size = _read_text(path)
        total += size
        if total > registry.MAX_BYTES:
            raise KnowledgeError("KNOWLEDGE_TOO_LARGE", "the documents are too large")
        texts.append((path.stem, text))
    chunks = load_chunks(texts, MAX_ROWS)
    return Expected(
        ref=str(hit.ref),
        name=hit.ref.name,
        version=hit.ref.version,
        table=table_name(hit.ref.name, hit.ref.version),
        alias=alias,
        dimensions=dimensions,
        documents=len({c["id"].split("#", 1)[0] for c in chunks}),
        chunks={c["id"]: chunk_hash(alias, dimensions, c["title"], c["text"]) for c in chunks},
    )


# ------------------------------------------------------------------ the database side

# Every table with the version's name, the ones on the default search path first ("$user", then
# public: where the runtime's unqualified CREATE TABLE lands). Run with search_path
# pg_catalog,pg_temp, so the embedding column's dimensions are read from the catalog (pgvector's
# typmod) rather than from format_type, which schema-qualifies a type that is not visible.
LOCATE_SQL = """
SELECT n.nspname, c.relkind,
       pg_has_role(current_user, c.relowner, 'USAGE') AS owned,
       n.nspname IN (current_user, 'public') AS default_path,
       (SELECT CASE WHEN t.typname = 'vector' THEN a.atttypmod END
          FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
         WHERE a.attrelid = c.oid AND a.attname = 'embedding' AND NOT a.attisdropped)
         AS dimensions,
       (SELECT a.atttypid = 'timestamp with time zone'::regtype FROM pg_attribute a
         WHERE a.attrelid = c.oid AND a.attname = 'updated' AND NOT a.attisdropped)
         AS updated_timestamptz
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = %(table)s
  AND n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$' AND n.nspname <> 'pg_toast'
ORDER BY n.nspname = current_user DESC, n.nspname = 'public' DESC,
         pg_has_role(current_user, c.relowner, 'USAGE') DESC, n.nspname
LIMIT 20
"""

# Only ids, hashes and timestamps: chunk text never leaves the database. `updated` is selected
# only when the catalog says its column is timestamptz (the agent's role could redefine it; every
# branch of a CASE is type-checked, so the column's type picks the statement), and only values
# Python's datetime can hold (psycopg refuses infinity and years outside 1..9999). One row over
# the cap tells KNOWLEDGE_TOO_LARGE.
UPDATED_TIMESTAMPTZ = (
    "CASE WHEN isfinite(updated) AND updated >= '0002-01-01 00:00:00+00'::timestamptz "
    "AND updated < '9999-01-01 00:00:00+00'::timestamptz THEN updated END"
)
UPDATED_UNKNOWN = "NULL::timestamptz"
ROWS_SQL = (
    'SELECT left(id, 300), left(hash, 64), {updated} FROM "{schema}"."{table}" LIMIT %(limit)s'
)


@dataclass
class Status:
    expected: Expected
    schema: str | None = None
    table_dimensions: int | None = None
    indexed: int = 0
    indexed_documents: int = 0
    fresh: int = 0
    stale: int = 0
    missing: int = 0
    extra: int = 0
    oldest: datetime.datetime | None = None
    newest: datetime.datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.schema is None:
            return "not_indexed"
        if (
            self.stale
            or self.missing
            or self.extra
            or self.table_dimensions != self.expected.dimensions
        ):
            return "stale"
        return "fresh"

    def as_json(self) -> dict:
        e = self.expected
        return {
            "ref": e.ref,
            "table": e.table,
            "schema": self.schema,
            "state": self.state,
            "documents": {"expected": e.documents, "indexed": self.indexed_documents},
            "chunks": {
                "expected": len(e.chunks),
                "indexed": self.indexed,
                "fresh": self.fresh,
                "stale": self.stale,
                "missing": self.missing,
                "extra": self.extra,
            },
            "updated": {"oldest": _iso(self.oldest), "newest": _iso(self.newest)},
            "embedding": {
                "alias": e.alias,
                "dimensions": e.dimensions,
                "table_dimensions": self.table_dimensions,
            },
            "notes": list(self.notes),
        }


def _iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _when(value) -> datetime.datetime | None:
    """A row's `updated` in UTC, if it is a timezone-aware datetime (timestamptz) that UTC can
    hold; anything else is unknown."""
    if isinstance(value, datetime.datetime) and value.tzinfo is not None:
        try:
            return value.astimezone(datetime.UTC)
        except (OverflowError, ValueError):
            return None
    return None


def _locate(want: Expected, rows: list[tuple]) -> tuple[str, object, bool, bool] | None:
    """(schema, embedding dimensions, on the default path, `updated` is timestamptz) of the
    table the runtime uses.

    The first table on the default search path ("$user", then public) is the one the runtime's
    unqualified name resolves to, whoever owns it: it must be an ordinary table the agent's role
    owns (or a role it belongs to), else KNOWLEDGE_OBJECT_UNEXPECTED. With none there (a role
    whose search_path was changed), exactly one such table in another schema is used, and more
    than one is KNOWLEDGE_OBJECT_AMBIGUOUS; tables the role does not own elsewhere are not its."""
    candidates = []
    for row in rows:
        schema, relkind, owned, default_path, dimensions, stamped = (tuple(row) + (None,) * 6)[:6]
        if not isinstance(schema, str) or not SCHEMA.fullmatch(schema):
            if default_path is True:
                raise KnowledgeError(
                    "KNOWLEDGE_OBJECT_UNEXPECTED",
                    f"{want.table}: a schema with an unexpected name",
                )
            continue  # not where the runtime looks by default, and not quotable here
        candidates.append(
            (schema, relkind, owned is True, default_path is True, dimensions, stamped is True)
        )
    on_path = [c for c in candidates if c[3]]
    if on_path:
        schema, relkind, owned, _, dimensions, stamped = on_path[0]
        if relkind != "r" or not owned:
            raise KnowledgeError(
                "KNOWLEDGE_OBJECT_UNEXPECTED",
                f"{schema}.{want.table} is not a table the agent's role owns",
            )
        return schema, dimensions, True, stamped
    elsewhere = [c for c in candidates if c[1] == "r" and c[2]]
    if len(elsewhere) > 1:
        names = ", ".join(c[0] for c in elsewhere[:5])
        raise KnowledgeError(
            "KNOWLEDGE_OBJECT_AMBIGUOUS",
            f"{want.table} is in several of the agent role's schemas ({names})",
        )
    if elsewhere:
        return elsewhere[0][0], elsewhere[0][4], False, elsewhere[0][5]
    return None


def status(want: Expected, query: Query) -> Status:
    """One version's index status, through `query` (the agent's read-only connection)."""
    result = Status(want)
    found = _locate(want, query(LOCATE_SQL, {"table": want.table}))
    if found is None:
        result.notes.append("not indexed yet: the runtime indexes at the agent's first search")
        return result
    schema, dimensions, default_path, stamped = found
    if not default_path:
        result.notes.append(
            f"found in schema {schema}, not on the default search path (the role's own schema, "
            f"then public); the runtime uses it only if the role's search_path includes {schema}"
        )
    result.schema = schema
    valid = isinstance(dimensions, int) and not isinstance(dimensions, bool)
    result.table_dimensions = dimensions if valid and 1 <= dimensions <= MAX_DIMENSIONS else None
    if result.table_dimensions != want.dimensions:
        result.notes.append(
            f"the table's embedding has {result.table_dimensions or 'unknown'} dimensions, "
            f"the manifest {want.dimensions}"
        )
    updated = UPDATED_TIMESTAMPTZ if stamped else UPDATED_UNKNOWN
    sql = ROWS_SQL.format(updated=updated, schema=schema, table=want.table)
    rows = query(sql, {"limit": MAX_ROWS + 1})
    if len(rows) > MAX_ROWS:
        raise KnowledgeError(
            "KNOWLEDGE_TOO_LARGE", f"{schema}.{want.table} has more than {MAX_ROWS} chunks"
        )
    seen: set[str] = set()
    stems: set[str] = set()
    for row in rows:
        chunk_id, chunk_hash_, updated = (tuple(row) + (None, None, None))[:3]
        if not isinstance(chunk_id, str) or chunk_id in seen:
            result.extra += 1
            continue
        seen.add(chunk_id)
        result.indexed += 1
        stems.add(chunk_id.split("#", 1)[0])
        when = _when(updated)
        if when is not None:
            result.oldest = when if result.oldest is None else min(result.oldest, when)
            result.newest = when if result.newest is None else max(result.newest, when)
        hoped = want.chunks.get(chunk_id)
        if hoped is None:
            result.extra += 1
        elif chunk_hash_ == hoped:
            result.fresh += 1
        else:
            result.stale += 1
    result.missing = sum(1 for chunk_id in want.chunks if chunk_id not in seen)
    result.indexed_documents = len(stems & want.stems)
    if result.state == "stale":
        result.notes.append(
            "out of date: the runtime re-indexes at its next process start and first search "
            "(release's eval does that)"
        )
    return result
