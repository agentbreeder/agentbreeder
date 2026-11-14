"""`agentbreeder knowledge status` (#830, M14): the chunker/hash parity with every shipped
runtime rag.py, freshness against a fake database, hostile rows, and the CLI shape."""

from __future__ import annotations

import contextlib
import datetime
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from agentbreeder import cli, knowledge, registry
from agentbreeder.commands import knowledge as command
from agentbreeder.knowledge import KnowledgeError

RUNTIMES = sorted((registry.BUILTIN_ROOT / "runtime" / "adk").glob("*/runtime/rag.py"))
NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
UTC = datetime.UTC

TRICKY_DOCS = {
    "a.md": "# Billing\n\nFirst paragraph.\n\n## Heading only\n\nSecond\nline.\n\n\n\nThird.",
    "b.txt": "plain title line\nmore\n\nnext para",
    "crlf.md": "# Windows\r\n\r\nCRLF paragraph.\r\n\r\nAnother.\r",
    "c.MD": "upper-case suffix: the runtime's glob does not take it",
    ".md": "a dotfile the runtime's glob takes\n\nsecond",
    "empty.md": "",
    "form.md": "# Form\x0cfeed\n\nbody   sep",
    "uni.md": "# Ünïcode ✓\n\nnaïve café",
    "notes.rst": "not a doc",
}


def _version(root: Path, name="help", version="1.0.0", docs=None, embedding=None) -> Path:
    path = root / "knowledge" / name / version
    (path / "docs").mkdir(parents=True)
    emb = embedding if embedding is not None else {"alias": "kb-embed", "dimensions": 8}
    (path / "manifest.yaml").write_text(
        f"kind: knowledge\nname: {name}\nversion: {version}\ndescription: d\n"
        f"embedding: {json.dumps(emb)}\n",
        encoding="utf-8",
    )
    for file, text in (docs if docs is not None else {"a.md": "# T\n\none\n\ntwo"}).items():
        (path / "docs" / file).write_bytes(text.encode())
    return path


def _rag(path: Path):
    spec = importlib.util.spec_from_file_location(f"rag_{path.parent.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _RuntimeDb:
    """Enough of psycopg.Connection for rag.KnowledgeSet.sync, capturing what it inserts."""

    def __init__(self):
        self.inserted = {}

    @contextlib.contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        if sql.startswith("INSERT INTO"):
            self.inserted[params[0]] = params[3]
        return _Result([])


# ------------------------------------------------------------------ parity with rag.py


@pytest.mark.parametrize("rag_path", RUNTIMES, ids=lambda p: p.parent.parent.name)
def test_chunker_and_hash_parity(rag_path, tmp_path):
    """CHUNKER_PARITY: the CLI's expected index is exactly what each runtime writes."""
    pytest.importorskip("psycopg")
    rag = _rag(rag_path)
    root = tmp_path / "registry"
    path = _version(root, docs=TRICKY_DOCS, embedding={"alias": "kb-embed", "dimensions": 8})
    ref = registry.Ref("knowledge", "help", "1.0.0")
    want = knowledge.expected(registry.Resolved(ref, path, root))

    def embedder_for(alias, dimensions):
        return lambda texts, task, titles=None: [[0.0] * dimensions for _ in texts]

    entry = {"name": "help", "version": "1.0.0"}
    kset = rag.KnowledgeSet(entry, path, embedder_for)
    db = _RuntimeDb()
    kset.sync(db)
    assert kset.table == want.table == rag.table_name("help", "1.0.0")
    assert db.inserted == want.chunks
    assert [c["id"] for c in kset.chunks] == list(want.chunks)
    assert kset.dimensions == want.dimensions and kset.alias == want.alias
    assert "c#0" not in want.chunks and "notes#0" not in want.chunks
    assert any(chunk_id.startswith(".md#") for chunk_id in want.chunks)
    assert want.documents == len({c.split("#")[0] for c in want.chunks})


@pytest.mark.parametrize("rag_path", RUNTIMES, ids=lambda p: p.parent.parent.name)
def test_default_embedding_parity(rag_path, tmp_path):
    pytest.importorskip("psycopg")
    rag = _rag(rag_path)
    root = tmp_path / "registry"
    path = _version(root, embedding={})
    want = knowledge.expected(
        registry.Resolved(registry.Ref("knowledge", "help", "1.0.0"), path, root)
    )
    kset = rag.KnowledgeSet({"name": "help", "version": "1.0.0"}, path, lambda a, d: None)
    assert (want.alias, want.dimensions) == (kset.alias, kset.dimensions) == ("", 768)


def test_runtimes_found():
    assert len(RUNTIMES) >= 5


def test_northwind_expected():
    hits = knowledge.versions("northwind-help", None, registry.roots(NORTHWIND))
    assert [str(h.ref) for h in hits] == ["knowledge/northwind-help@1.0.0"]
    want = knowledge.expected(hits[0])
    assert want.documents == 5
    assert want.alias == "support-embedding" and want.dimensions == 768
    assert want.table == "kb_northwind_help_1_0_0"
    assert len(want.chunks) > 5


# ------------------------------------------------------------------ refs, versions, files


@pytest.mark.parametrize(
    "text, parsed",
    [("knowledge/help", ("help", None)), ("knowledge/help@1.2.3", ("help", "1.2.3"))],
)
def test_parse_ref(text, parsed):
    assert knowledge.parse_ref(text) == parsed


@pytest.mark.parametrize(
    "text",
    ["tool/help", "knowledge/Help", "knowledge/help@1.2", "knowledge/help@01.0.0", "", "help"],
)
def test_parse_ref_invalid(text):
    with pytest.raises(KnowledgeError) as caught:
        knowledge.parse_ref(text)
    assert caught.value.code == "REF_INVALID"


def test_versions_all_sorted_and_missing(tmp_path):
    root = tmp_path / "registry"
    for version in ("1.10.0", "1.2.0", "1.9.1"):
        _version(root, version=version)
    _version(root, name="helper")  # a prefix of another name is not this name
    found = knowledge.versions("help", None, [root])
    assert [h.ref.version for h in found] == ["1.2.0", "1.9.1", "1.10.0"]
    assert [h.ref.version for h in knowledge.versions("help", "1.9.1", [root])] == ["1.9.1"]
    with pytest.raises(KnowledgeError) as caught:
        knowledge.versions("nope", None, [root])
    assert caught.value.code == "REF_NOT_FOUND"
    with pytest.raises(KnowledgeError) as caught:
        knowledge.versions("help", "3.0.0", [root])
    assert caught.value.code == "REF_NOT_FOUND"


def _hit(path: Path, root: Path, name="help", version="1.0.0"):
    return registry.Resolved(registry.Ref("knowledge", name, version), path, root)


def test_doc_link_refused(tmp_path):
    root = tmp_path / "registry"
    path = _version(root)
    (tmp_path / "outside.md").write_text("secret")
    os.symlink(tmp_path / "outside.md", path / "docs" / "link.md")
    with pytest.raises(KnowledgeError) as caught:
        knowledge.expected(_hit(path, root))
    assert caught.value.code == "KNOWLEDGE_INVALID"


def test_doc_directory_refused(tmp_path):
    root = tmp_path / "registry"
    path = _version(root)
    (path / "docs" / "dir.md").mkdir()
    with pytest.raises(KnowledgeError, match="is not a file"):
        knowledge.expected(_hit(path, root))


def test_doc_not_utf8_and_too_large(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    path = _version(root, docs={"a.md": "ok"})
    (path / "docs" / "b.md").write_bytes(b"\xff\xfe bad")
    with pytest.raises(KnowledgeError, match="not UTF-8"):
        knowledge.expected(_hit(path, root))
    (path / "docs" / "b.md").write_bytes(b"x" * 20)
    monkeypatch.setattr(knowledge, "MAX_FILE_BYTES", 10)
    with pytest.raises(KnowledgeError, match="too large"):
        knowledge.expected(_hit(path, root))


def test_doc_unreadable(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    path = _version(root)

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(knowledge.os, "open", refuse)
    with pytest.raises(KnowledgeError, match="cannot read docs/a.md"):
        knowledge.expected(_hit(path, root))


def test_docs_unlistable(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    path = _version(root)

    def refuse(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "iterdir", refuse)
    with pytest.raises(KnowledgeError, match="cannot list docs/"):
        knowledge.expected(_hit(path, root))


def test_no_docs_dir(tmp_path):
    root = tmp_path / "registry"
    path = _version(root, docs={})
    (path / "docs").rmdir()
    want = knowledge.expected(_hit(path, root))
    assert want.chunks == {} and want.documents == 0


@pytest.mark.parametrize(
    "embedding",
    [
        '"x"',
        '{"alias": 3}',
        '{"dimensions": true}',
        '{"dimensions": "many"}',
        '{"dimensions": []}',
    ],
)
def test_bad_embedding(tmp_path, embedding):
    root = tmp_path / "registry"
    path = _version(root)
    manifest = path / "manifest.yaml"
    text = manifest.read_text().splitlines()
    manifest.write_text("\n".join(text[:-1] + [f"embedding: {embedding}"]) + "\n")
    with pytest.raises(KnowledgeError) as caught:
        knowledge.expected(_hit(path, root))
    assert caught.value.code == "KNOWLEDGE_INVALID"


def test_unsafe_table_name():
    with pytest.raises(KnowledgeError):
        knowledge.table_name("x" * 63, "1.0.0")


# ------------------------------------------------------------------ freshness on a fake DB

T1 = datetime.datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
T2 = datetime.datetime(2026, 11, 13, 10, 30, tzinfo=UTC)


def _want(chunks=None, dimensions=8) -> knowledge.Expected:
    chunks = chunks if chunks is not None else {"a#1": "h1", "a#2": "h2", "b#1": "h3"}
    return knowledge.Expected(
        ref="knowledge/help@1.0.0",
        name="help",
        version="1.0.0",
        table="kb_help_1_0_0",
        alias="kb-embed",
        dimensions=dimensions,
        documents=len({c.split("#")[0] for c in chunks}),
        chunks=chunks,
    )


class FakeDb:
    def __init__(self, located, rows=()):
        self.located, self.rows, self.calls = located, list(rows), []

    def __call__(self, sql, params):
        self.calls.append((sql, params))
        if sql == knowledge.LOCATE_SQL:
            return self.located
        return self.rows[: params["limit"]]


def test_not_indexed():
    db = FakeDb([])
    result = knowledge.status(_want(), db)
    assert result.state == "not_indexed"
    view = result.as_json()
    assert view["schema"] is None and view["chunks"]["indexed"] == 0
    assert "first search" in view["notes"][0]
    assert len(db.calls) == 1


def test_fresh():
    rows = [("a#1", "h1", T1), ("a#2", "h2", T2), ("b#1", "h3", T1)]
    db = FakeDb([("support_agent", "r", True, True, 8, True)], rows)
    result = knowledge.status(_want(), db)
    view = result.as_json()
    assert view["state"] == "fresh"
    assert view["chunks"] == {
        "expected": 3,
        "indexed": 3,
        "fresh": 3,
        "stale": 0,
        "missing": 0,
        "extra": 0,
    }
    assert view["documents"] == {"expected": 2, "indexed": 2}
    assert view["updated"] == {"oldest": "2026-11-13T09:00:00Z", "newest": "2026-11-13T10:30:00Z"}
    assert view["embedding"] == {"alias": "kb-embed", "dimensions": 8, "table_dimensions": 8}
    assert view["notes"] == []
    sql, params = db.calls[1]
    assert sql.startswith("SELECT left(id, 300), left(hash, 64), CASE WHEN isfinite(updated)")
    assert 'FROM "support_agent"."kb_help_1_0_0"' in sql
    assert "text" not in sql.split("FROM")[0]  # chunk text is never selected
    assert params == {"limit": knowledge.MAX_ROWS + 1}


def test_stale_missing_extra():
    rows = [("a#1", "old", T1), ("zz#0", "h9", T1)]
    db = FakeDb([("public", "r", True, True, 8, True)], rows)
    view = knowledge.status(_want(), db).as_json()
    assert view["state"] == "stale"
    assert view["chunks"] == {
        "expected": 3,
        "indexed": 2,
        "fresh": 0,
        "stale": 1,
        "missing": 2,
        "extra": 1,
    }
    assert view["documents"] == {"expected": 2, "indexed": 1}
    assert "re-indexes" in view["notes"][-1]


def test_dimension_mismatch_is_stale():
    rows = [("a#1", "h1", T1), ("a#2", "h2", T1), ("b#1", "h3", T1)]
    view = knowledge.status(
        _want(), FakeDb([("public", "r", True, True, 768, True)], rows)
    ).as_json()
    assert view["state"] == "stale"
    assert view["embedding"]["table_dimensions"] == 768
    assert "768 dimensions, the manifest 8" in view["notes"][0]


def test_unknown_vector_type():
    view = knowledge.status(_want({}), FakeDb([("public", "r", True, True, None, True)])).as_json()
    assert view["embedding"]["table_dimensions"] is None
    assert "unknown dimensions" in view["notes"][0]


def test_hostile_rows():
    """Forged rows: wrong types, duplicates, escapes, naive or fake timestamps."""
    rows = [
        (b"a#1", "h1", T1),  # bytes id
        (None, None, None),
        ("a#1", "h1", "2026-11-13"),  # timestamp as text: unknown
        ("a#1", "h1", T2),  # duplicate id (the agent can drop the primary key)
        ("\x1b]52;c;ZXZpbA==\x07#0", "h", datetime.datetime(2026, 1, 1)),  # OSC 52, naive time
        ("a#2", 12345, T1),  # hash of a wrong type
        ("b#1",),  # short row
    ]
    view = knowledge.status(
        _want(), FakeDb([("public", "r", True, True, 8, True)], rows)
    ).as_json()
    assert view["chunks"] == {
        "expected": 3,
        "indexed": 4,
        "fresh": 1,
        "stale": 2,
        "missing": 0,
        "extra": 4,
    }
    assert view["updated"] == {"oldest": "2026-11-13T09:00:00Z", "newest": "2026-11-13T09:00:00Z"}
    text = json.dumps(view)
    assert "\x1b" not in text and "ZXZpbA" not in text


@pytest.mark.parametrize(
    "located",
    [
        [("public", "v", True, True, 8, True)],  # a view
        [("public", "r", False, True, 8, True)],  # someone else's table
        [("public", "r", None, True, 8, True)],
        [('pub"lic', "r", True, True, 8, True)],  # an unquotable schema
        [(None, "r", True, True, 8, True)],
    ],
)
def test_object_unexpected(located):
    db = FakeDb(located)
    with pytest.raises(KnowledgeError) as caught:
        knowledge.status(_want(), db)
    assert caught.value.code == "KNOWLEDGE_OBJECT_UNEXPECTED"
    assert len(db.calls) == 1  # no rows read


def test_too_large(monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_ROWS", 2)
    rows = [("a#1", "h1", T1)] * 5
    with pytest.raises(KnowledgeError) as caught:
        knowledge.status(_want(), FakeDb([("public", "r", True, True, 8, True)], rows))
    assert caught.value.code == "KNOWLEDGE_TOO_LARGE"


# ------------------------------------------------------------------ the CLI


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj" / "registry"
    _version(root, version="1.0.0", docs={"a.md": "# T\n\none\n\ntwo"})
    _version(root, version="1.1.0", docs={"a.md": "# T\n\none\n\ntwo\n\nthree"})
    return tmp_path / "proj"


def _fake_database(located, rows, calls=None):
    @contextlib.contextmanager
    def database(project, environment):
        if calls is not None:
            calls.append((project, environment))
        yield "dev", "support", FakeDb(located, rows)

    return database


def _main(*argv) -> int:
    return cli.main(["knowledge", "status", *argv])


def _indexed(project) -> list[tuple]:
    hit = knowledge.versions("help", "1.0.0", registry.roots(project))[0]
    want = knowledge.expected(hit)
    return [(cid, h, T1) for cid, h in want.chunks.items()]


def test_cli_json(project, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(
        command,
        "_database",
        _fake_database([("support_agent", "r", True, True, 8, True)], _indexed(project), calls),
    )
    assert (
        _main("knowledge/help", "--project", str(project), "--environment", "dev", "--json") == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert calls == [(project, "dev")]
    assert data["schema_version"] == 1
    assert (data["ref"], data["environment"], data["database"], data["credential"]) == (
        "knowledge/help",
        "dev",
        "support",
        "agent",
    )
    assert [(v["ref"], v["state"]) for v in data["versions"]] == [
        ("knowledge/help@1.0.0", "fresh"),
        ("knowledge/help@1.1.0", "stale"),  # the fake serves 1.0.0's rows: "three" missing
    ]


def test_cli_text(project, capsys, monkeypatch):
    monkeypatch.setattr(
        command,
        "_database",
        _fake_database([("support_agent", "r", True, True, 4, True)], _indexed(project)),
    )
    assert _main("knowledge/help@1.0.0", "--project", str(project)) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "knowledge/help@1.0.0 (database support, environment dev)"
    assert "stale" in out and "support_agent.kb_help_1_0_0" in out
    assert "chunks 2 indexed of 2 (fresh 2, stale 0, missing 0, extra 0); documents 1 of 1" in out
    assert "updated 2026-11-13T09:00:00Z .. 2026-11-13T09:00:00Z" in out
    assert "embedding kb-embed, 8 dimensions (table 4)" in out
    assert "note: the table's embedding has 4 dimensions" in out


def test_cli_text_not_indexed(project, capsys, monkeypatch):
    monkeypatch.setattr(command, "_database", _fake_database([], []))
    assert _main("knowledge/help@1.1.0", "--project", str(project)) == 0
    out = capsys.readouterr().out
    assert "not_indexed" in out and "chunks 0 indexed of 3; documents 0 of 1" in out


def test_cli_escapes_alias(tmp_path, capsys, monkeypatch):
    root = tmp_path / "p" / "registry"
    _version(root, embedding={"alias": "evil\u001b[2Jx", "dimensions": 8})
    monkeypatch.setattr(command, "_database", _fake_database([], []))
    assert _main("knowledge/help@1.0.0", "--project", str(tmp_path / "p")) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "evil" in out


def test_cli_errors(project, capsys, monkeypatch):
    assert _main("tool/help", "--project", str(project), "--json") == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[REF_INVALID] ")
    assert json.loads(captured.out)["error"]["code"] == "REF_INVALID"

    assert _main("knowledge/nope", "--project", str(project)) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[REF_NOT_FOUND] ") and captured.out == ""

    @contextlib.contextmanager
    def down(project, environment):
        raise KnowledgeError("SERVICES_DOWN", "start services first")
        yield

    monkeypatch.setattr(command, "_database", down)
    assert _main("knowledge/help", "--project", str(project), "--json") == 2
    assert json.loads(capsys.readouterr().out)["error"] == {
        "code": "SERVICES_DOWN",
        "message": "start services first",
    }


def test_cli_registry_error_mapped(project, capsys):
    # A manifest that declares another version: resolve's MANIFEST_INVALID keeps its code.
    manifest = project / "registry" / "knowledge" / "help" / "1.0.0" / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("version: 1.0.0", "version: 9.0.0"))
    assert _main("knowledge/help@1.0.0", "--project", str(project)) == 2
    assert capsys.readouterr().err.startswith("[MANIFEST_INVALID] ")


def test_help_lists_command(capsys):
    with pytest.raises(SystemExit):
        cli.main(["knowledge", "status", "--help"])
    out = capsys.readouterr().out
    assert "--project DIR" in out and "--environment" in out and "--json" in out


def test_python_m_subprocess(project):
    """`python -m agentbreeder knowledge status` in a subprocess: a refusal before any I/O."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "knowledge", "status", "tool/x", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "REF_INVALID"


def test_add_document_by_registry_edit(project, tmp_path, capsys):
    """M14's add/edit by path is `registry edit` on the knowledge version: the new version's
    expected index has the new document's chunks (the runtime indexes them after release)."""
    new = tmp_path / "faq.md"
    new.write_text("# FAQ\n\nHow do I reset my pen?\n\nHold the button.", encoding="utf-8")
    argv = ["registry", "edit", "knowledge/help@1.1.0", "--set", "docs/faq.md", str(new)]
    assert cli.main([*argv, "--project", str(project), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ref"] == "knowledge/help@1.1.1"
    before = knowledge.expected(knowledge.versions("help", "1.1.0", registry.roots(project))[0])
    after = knowledge.expected(knowledge.versions("help", "1.1.1", registry.roots(project))[0])
    assert (before.documents, after.documents) == (1, 2)
    assert set(after.chunks) - set(before.chunks) == {"faq#1", "faq#2"}
    assert after.table == "kb_help_1_1_1"
    removed = ["registry", "edit", "knowledge/help@1.1.1", "--remove", "docs/a.md"]
    assert cli.main([*removed, "--project", str(project), "--json"]) == 0
    capsys.readouterr()
    last = knowledge.expected(knowledge.versions("help", "1.1.2", registry.roots(project))[0])
    assert set(last.chunks) == {"faq#1", "faq#2"}


# ------------------------------------------------------------------ the seam (real dbread)


class _PgError(Exception):
    sqlstate = None


class _PgOperational(_PgError):
    pass


class _FakeConnection:
    def __init__(self, db: FakeDb, fail: str | None):
        self.db, self.fail, self.closed, self.statements = db, fail, False, []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if sql.startswith(("BEGIN", "SET LOCAL", "ROLLBACK")):
            return _Result([])
        if self.fail:
            error = _PgError("server text with postgresql://u:pw@host/db")
            error.sqlstate = self.fail
            raise error
        return _Result(self.db(sql, params))

    def close(self):
        self.closed = True


def _fake_psycopg(connection):
    class Module:
        Error = _PgError
        OperationalError = _PgOperational

        @staticmethod
        def connect(url, **options):
            connection.options = options
            connection.url = url
            return connection

    return Module


@pytest.fixture
def seam(monkeypatch, tmp_path):
    from agentbreeder import dbread

    target = dbread.StackDatabase(
        tmp_path, "dev", "support", tmp_path, tmp_path, "postgresql://agent:pw@h/support", None
    )
    calls = {}

    def resolve(stack, project, environment, *, prefix):
        calls["resolve"] = (stack, project, environment, prefix)
        return target

    monkeypatch.setattr(dbread, "load_stack", lambda project: "STACK")
    monkeypatch.setattr(dbread, "resolve", resolve)

    def use(connection):
        monkeypatch.setattr(dbread, "psycopg_module", lambda command: _fake_psycopg(connection))

    return dbread, calls, use


def test_seam_reads_in_one_read_only_session(project, seam, capsys):
    dbread, calls, use = seam
    connection = _FakeConnection(
        FakeDb([("support_agent", "r", True, True, 8, True)], _indexed(project)), None
    )
    use(connection)
    assert _main("knowledge/help@1.0.0", "--project", str(project), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["environment"], data["database"]) == ("dev", "support")
    assert data["versions"][0]["state"] == "fresh"
    assert calls["resolve"] == ("STACK", project, None, "KNOWLEDGE_STATUS")
    assert connection.url == "postgresql://agent:pw@h/support"  # the agent's credential
    assert "default_transaction_read_only=on" in connection.options["options"]
    assert connection.statements[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert connection.statements[-1] == "ROLLBACK" and connection.closed


def test_seam_maps_errors_without_the_dsn(project, seam, capsys):
    dbread, calls, use = seam
    connection = _FakeConnection(FakeDb([]), "57014")
    use(connection)
    assert _main("knowledge/help", "--project", str(project), "--json") == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "KNOWLEDGE_STATUS_TIMEOUT"
    assert "pw@" not in captured.out + captured.err and connection.closed


def test_seam_object_refusal_closes(project, seam, capsys):
    dbread, calls, use = seam
    connection = _FakeConnection(FakeDb([("public", "v", True, True, None, True)]), None)
    use(connection)
    assert _main("knowledge/help@1.0.0", "--project", str(project)) == 2
    assert capsys.readouterr().err.startswith("[KNOWLEDGE_OBJECT_UNEXPECTED] ")
    assert connection.closed and "ROLLBACK" not in connection.statements


def test_seam_stack_refusal(project, seam, capsys, monkeypatch):
    dbread, calls, use = seam

    def stale(*args, **kwargs):
        raise dbread.DbReadError("SERVICES_STALE", "run `agentbreeder services up`")

    monkeypatch.setattr(dbread, "resolve", stale)
    assert _main("knowledge/help", "--project", str(project), "--json") == 2
    assert json.loads(capsys.readouterr().out)["error"] == {
        "code": "SERVICES_STALE",
        "message": "run `agentbreeder services up`",
    }


# ------------------------------------------------------------------ review round 1


def test_locate_sql_reads_dimensions_from_the_catalog():
    """format_type would print `public.vector(768)` under search_path pg_catalog,pg_temp; the
    dimensions come from pgvector's typmod, with the type checked by name."""
    assert "format_type" not in knowledge.LOCATE_SQL
    assert "a.atttypmod" in knowledge.LOCATE_SQL and "t.typname = 'vector'" in knowledge.LOCATE_SQL
    assert "a.attname = 'updated'" in knowledge.LOCATE_SQL


def test_table_outside_the_default_path():
    rows = [("a#1", "h1", T1)]
    located = [("other", "r", False, False, 8, True), ("app", "r", True, False, 8, True)]
    view = knowledge.status(_want({"a#1": "h1"}), FakeDb(located, rows)).as_json()
    assert view["schema"] == "app" and view["state"] == "fresh"
    assert "not on the default search path" in view["notes"][0]


def test_tables_in_several_schemas_are_ambiguous():
    located = [("app", "r", True, False, 8, True), ("app2", "r", True, False, 8, True)]
    with pytest.raises(KnowledgeError) as caught:
        knowledge.status(_want(), FakeDb(located))
    assert caught.value.code == "KNOWLEDGE_OBJECT_AMBIGUOUS"


def test_unowned_table_elsewhere_is_not_the_agents():
    located = [("other", "r", False, False, 8, True), ("views", "v", True, False, 8, True)]
    assert knowledge.status(_want(), FakeDb(located)).state == "not_indexed"


def test_default_path_wins_and_quoted_schema():
    rows = [("a#1", "h1", T1)]
    located = [("Support-Agent", "r", True, True, 8, True), ("app", "r", True, False, 8, True)]
    db = FakeDb(located, rows)
    view = knowledge.status(_want({"a#1": "h1"}), db).as_json()
    assert view["schema"] == "Support-Agent" and view["notes"] == []
    assert '"Support-Agent"."kb_help_1_0_0"' in db.calls[1][0]


@pytest.mark.parametrize("dimensions", ["vector(8)", True, 0, 99999, -1, 8.0])
def test_hostile_table_dimensions(dimensions):
    view = knowledge.status(
        _want({}), FakeDb([("public", "r", True, True, dimensions, True)])
    ).as_json()
    assert view["embedding"]["table_dimensions"] is None and view["state"] == "stale"


def test_timestamp_utc_cannot_hold():
    """An aware datetime.max in a negative offset (the role can set its own TimeZone)."""
    minus5 = datetime.timezone(datetime.timedelta(hours=-5))
    rows = [("a#1", "h1", datetime.datetime.max.replace(tzinfo=minus5)), ("a#2", "h2", T2)]
    view = knowledge.status(
        _want({"a#1": "h1", "a#2": "h2"}), FakeDb([("public", "r", True, True, 8, True)], rows)
    ).as_json()
    assert view["updated"] == {"oldest": "2026-11-13T10:30:00Z", "newest": "2026-11-13T10:30:00Z"}


def test_other_offsets_shown_in_utc():
    plus2 = datetime.timezone(datetime.timedelta(hours=2))
    rows = [("a#1", "h1", datetime.datetime(2026, 11, 13, 12, 0, tzinfo=plus2))]
    view = knowledge.status(
        _want({"a#1": "h1"}), FakeDb([("public", "r", True, True, 8, True)], rows)
    ).as_json()
    assert view["updated"]["oldest"] == "2026-11-13T10:00:00Z"


@pytest.mark.parametrize("dimensions", [".inf", "0", "16001", "-3"])
def test_dimensions_out_of_range(tmp_path, dimensions):
    root = tmp_path / "registry"
    path = _version(root)
    manifest = path / "manifest.yaml"
    lines = manifest.read_text().splitlines()
    manifest.write_text(
        "\n".join(lines[:-1] + [f"embedding: {{dimensions: {dimensions}}}"]) + "\n"
    )
    with pytest.raises(KnowledgeError) as caught:
        knowledge.expected(_hit(path, root))
    assert caught.value.code == "KNOWLEDGE_INVALID"


def test_chunk_cap_while_chunking(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_ROWS", 3)
    root = tmp_path / "registry"
    path = _version(root, docs={"a.md": "one\n\ntwo\n\nthree\n\nfour\n\nfive"})
    with pytest.raises(KnowledgeError) as caught:
        knowledge.expected(_hit(path, root))
    assert caught.value.code == "KNOWLEDGE_TOO_LARGE"


def test_file_count_and_total_bytes_caps(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    path = _version(root, docs={"a.md": "x" * 30, "b.md": "y" * 30, "c.txt": "z"})
    monkeypatch.setattr(registry, "MAX_FILES", 2)
    with pytest.raises(KnowledgeError, match="more than 2 documents"):
        knowledge.expected(_hit(path, root))
    monkeypatch.setattr(registry, "MAX_FILES", 2000)
    monkeypatch.setattr(registry, "MAX_BYTES", 50)
    with pytest.raises(KnowledgeError, match="documents are too large"):
        knowledge.expected(_hit(path, root))


def test_docs_symlink_refused(tmp_path):
    root = tmp_path / "registry"
    path = _version(root, docs={})
    (path / "docs").rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.md").write_text("private")
    os.symlink(elsewhere, path / "docs")
    with pytest.raises(KnowledgeError, match="docs/ must not be a symlink"):
        knowledge.expected(_hit(path, root))


def test_fifo_swapped_in_never_blocks(tmp_path):
    fifo = tmp_path / "a.md"
    os.mkfifo(fifo)
    with pytest.raises(KnowledgeError, match="is not a file"):
        knowledge._read_text(fifo)


# ------------------------------------------------------------------ review round 2


def test_sql_guards():
    assert "isfinite(updated)" in knowledge.UPDATED_TIMESTAMPTZ
    assert "updated" not in knowledge.ROWS_SQL.replace("{updated}", "")
    assert "pg_(temp|toast_temp)_" in knowledge.LOCATE_SQL
    order = knowledge.LOCATE_SQL.split("ORDER BY", 1)[1]
    assert order.index("'public'") < order.index("pg_has_role") < order.index("n.nspname\n")


def test_bad_schema_name_off_the_path_is_skipped():
    located = [("odd schema", "r", True, False, 8, True), ("app", "r", True, False, 8, True)]
    view = knowledge.status(_want({}), FakeDb(located)).as_json()
    assert view["schema"] == "app"


def test_total_size_counts_bytes(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    path = _version(root, docs={"a.md": "\U0001f600" * 20})  # 20 characters, 80 bytes
    monkeypatch.setattr(registry, "MAX_BYTES", 50)
    with pytest.raises(KnowledgeError, match="documents are too large"):
        knowledge.expected(_hit(path, root))


# ------------------------------------------------------------------ review round 3


@pytest.mark.parametrize("stamped", [False, None, "t", 1])
def test_updated_not_timestamptz_is_never_selected(stamped):
    """A redefined `updated` (text, jsonb, ...) is not referenced at all: the statement parses
    for any column type, and the times are unknown."""
    db = FakeDb([("public", "r", True, True, 8, stamped)], [("a#1", "h1", None)])
    view = knowledge.status(_want({"a#1": "h1"}), db).as_json()
    sql = db.calls[1][0]
    assert sql == (
        'SELECT left(id, 300), left(hash, 64), NULL::timestamptz FROM "public"."kb_help_1_0_0" '
        "LIMIT %(limit)s"
    )
    assert view["state"] == "fresh" and view["updated"] == {"oldest": None, "newest": None}
