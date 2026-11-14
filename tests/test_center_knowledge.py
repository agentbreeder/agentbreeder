"""A knowledge version's index status in the console (#830, M14 console half, part 1):
`knowledge status REF --json` as an uncached CLI child, the query checked before it, and only the
allow-listed fields of its answer passed on (docs/commands/knowledge.md, docs/knowledge.md)."""

from __future__ import annotations

import contextlib
import json
import sys
import textwrap

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)
from test_knowledge_status import T1, _fake_database, _indexed, _version

from agentbreeder import cli, knowledge, registry
from agentbreeder.center import knowledgestatus, server
from agentbreeder.commands import knowledge as command

VERSION = {
    "ref": "knowledge/northwind-help@1.0.0",
    "table": "kb_northwind_help_1_0_0",
    "schema": "support_agent",
    "state": "fresh",
    "documents": {"expected": 5, "indexed": 5},
    "chunks": {"expected": 21, "indexed": 21, "fresh": 21, "stale": 0, "missing": 0, "extra": 0},
    "updated": {"oldest": "2026-11-13T09:12:03Z", "newest": "2026-11-13T09:12:04Z"},
    "embedding": {"alias": "support-embedding", "dimensions": 768, "table_dimensions": 768},
    "notes": [],
}
ANSWER = {
    "schema_version": 1,
    "ref": "knowledge/northwind-help@1.0.0",
    "environment": "dev",
    "database": "support",
    "credential": "agent",
    "versions": [VERSION],
}

# `knowledge status` only: the argv is recorded; `status.json` and `status-exit` set the answer.
FAKE = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[:2] == ["knowledge", "status"]:
        open(marker + "/status-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/status-stderr"):
            sys.stderr.write(open(marker + "/status-stderr").read())
            sys.exit(1)
        print(open(marker + "/status.json").read())
        code = marker + "/status-exit"
        sys.exit(int(open(code).read()) if os.path.exists(code) else 0)
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "status.json").write_text(json.dumps(ANSWER))
    return [sys.executable, str(path)]


def read(center, project, cookie, query="ref=knowledge%2Fnorthwind-help%401.0.0"):  # noqa: F811
    path = f"/api/projects/{project['id']}/knowledge/status?{query}"
    return api(center, "GET", path, cookie=cookie)


def answer(tmp_path, value, code=0):
    (tmp_path / "status.json").write_text(json.dumps(value))
    (tmp_path / "status-exit").write_text(str(code))


def calls(tmp_path) -> list[list[str]]:
    path = tmp_path / "status-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# -- the route ---------------------------------------------------------------------------------


def test_a_status_is_read_with_a_fixed_argv(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None
    assert calls(tmp_path) == [
        [
            "knowledge", "status", "knowledge/northwind-help@1.0.0", "--project",
            project["path"], "--environment", "dev", "--json",
        ]
    ]  # fmt: skip
    assert data["data"] == {
        "ref": "knowledge/northwind-help@1.0.0",
        "environment": "dev",
        "database": "support",
        "credential": "agent",
        "versions": [VERSION],
    }


def test_every_version_of_a_name(center, project, cookie, tmp_path):  # noqa: F811
    older = {**VERSION, "ref": "knowledge/northwind-help@0.9.0", "state": "not_indexed"}
    answer(tmp_path, {**ANSWER, "ref": "knowledge/northwind-help", "versions": [older, VERSION]})
    status, data, _ = read(center, project, cookie, "ref=knowledge%2Fnorthwind-help")
    assert status == 200 and data["error"] is None, data
    assert [v["state"] for v in data["data"]["versions"]] == ["not_indexed", "fresh"]
    assert calls(tmp_path)[-1][2] == "knowledge/northwind-help"


def test_the_answer_is_never_cached(center, project, cookie, tmp_path):  # noqa: F811
    _, first, _ = read(center, project, cookie)
    assert first["error"] is None
    refused = {"code": "SERVICES_DOWN", "message": "start it with `agentbreeder services up`"}
    answer(tmp_path, {"schema_version": 1, "error": refused}, 2)
    _, second, _ = read(center, project, cookie)
    assert second["data"] is None and second["stale"] is False
    assert second["error"] == refused
    assert len(calls(tmp_path)) == 2


@pytest.mark.parametrize(
    "query",
    [
        "",
        "ref=",
        "ref=tool%2Forders",
        "ref=knowledge%2FHelp",
        "ref=knowledge%2Fhelp%40v1",
        "ref=knowledge%2Fhelp%401.0",
        "ref=--project",
        "ref=knowledge%2F..%2Fx",
        "ref=knowledge%2Fhelp%401.0.0%0A",
    ],
)
def test_bad_queries_never_reach_the_cli(center, project, cookie, tmp_path, query):  # noqa: F811
    status, data, _ = read(center, project, cookie, query)
    assert status == 400 and data["error"]["code"] == "REF_INVALID"
    assert calls(tmp_path) == []


def test_unknown_keys_are_dropped(center, project, cookie, tmp_path):  # noqa: F811
    extra = {**VERSION, "text": "chunk text", "chunks": {**VERSION["chunks"], "rows": ["x"]}}
    answer(tmp_path, {**ANSWER, "versions": [extra], "dsn": "postgresql://secret"})
    _, data, _ = read(center, project, cookie)
    text = json.dumps(data)
    assert "chunk text" not in text and "secret" not in text and "rows" not in text
    assert set(data["data"]["versions"][0]) == set(VERSION)


@pytest.mark.parametrize(
    "value,code",
    [
        ({"schema_version": 1, "error": {"code": "DB_LOGIN_FAILED", "message": "no"}},
         "DB_LOGIN_FAILED"),
        ({"schema_version": 1, "error": {"code": "bad code", "message": "no"}},
         "KNOWLEDGE_STATUS_FAILED"),
        ({**ANSWER, "schema_version": 2}, "KNOWLEDGE_STATUS_INVALID"),
        ({**ANSWER, "ref": "knowledge/other@1.0.0"}, "KNOWLEDGE_STATUS_INVALID"),
        ({**ANSWER, "credential": "console"}, "KNOWLEDGE_STATUS_INVALID"),
        ({**ANSWER, "versions": "x"}, "KNOWLEDGE_STATUS_INVALID"),
        ({**ANSWER, "versions": []}, "KNOWLEDGE_STATUS_INVALID"),
        ({**ANSWER, "versions": [1]}, "KNOWLEDGE_STATUS_INVALID"),
        ({**ANSWER, "versions": [{**VERSION, "state": "maybe"}]}, "KNOWLEDGE_STATUS_INVALID"),
        ({**ANSWER, "versions": [{**VERSION, "ref": "knowledge/northwind-help@2.0.0"}]},
         "KNOWLEDGE_STATUS_INVALID"),
        ([1, 2], "KNOWLEDGE_STATUS_INVALID"),
    ],
)  # fmt: skip
def test_failures_are_coded(center, project, cookie, tmp_path, value, code):  # noqa: F811
    answer(tmp_path, value, 2 if isinstance(value, dict) and "error" in value else 0)
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"] == code


def test_a_cli_failure_without_json_is_reported(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "status-stderr").write_text(
        "[DEPENDENCY_MISSING] knowledge status needs psycopg\n"
    )
    _, data, _ = read(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "DEPENDENCY_MISSING"


def test_status_reads_are_bounded(center, project, cookie, tmp_path):  # noqa: F811
    for _ in range(server.MAX_KNOWLEDGE_IN_FLIGHT):
        assert center.knowledge_reads.acquire(blocking=False)
    try:
        status, data, _ = read(center, project, cookie)
        assert status == 400 and data["error"]["code"] == "KNOWLEDGE_STATUS_BUSY"
        assert calls(tmp_path) == []
    finally:
        for _ in range(server.MAX_KNOWLEDGE_IN_FLIGHT):
            center.knowledge_reads.release()
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None


# -- the answer check --------------------------------------------------------------------------

REQUEST = {"ref": "knowledge/northwind-help@1.0.0"}


def test_hostile_values_become_null_and_limits_hold():
    hostile = {
        "ref": "knowledge/northwind-help@1.0.0",
        "state": "stale",
        "table": "kb; drop",
        "schema": 'a"b',
        "documents": {"expected": -1, "indexed": True},
        "chunks": {"expected": knowledgestatus.MAX_ROWS + 1, "indexed": "21", "fresh": 1.5},
        "updated": {"oldest": "yesterday", "newest": "2026-11-13T09:12:04.5Z"},
        "embedding": {"alias": "e\x1b[2J‮" + "x" * 300, "dimensions": 0, "table_dimensions": 16001},
        "notes": ["n\x00ote"] * 20 + [7],
    }
    status, problem = knowledgestatus.status_answer(
        {**ANSWER, "versions": [hostile], "environment": 5, "database": "d\x07b"}, REQUEST
    )
    assert problem is None
    (version,) = status["versions"]
    assert version["table"] is None and version["schema"] is None
    assert version["documents"] == {"expected": None, "indexed": None}
    assert set(version["chunks"].values()) == {None}
    assert version["updated"] == {"oldest": None, "newest": None}
    alias = version["embedding"]["alias"]
    assert len(alias) == knowledgestatus.MAX_ALIAS and "\x1b" not in alias and "‮" not in alias
    assert version["embedding"]["dimensions"] is None
    assert version["embedding"]["table_dimensions"] is None
    assert version["notes"] == ["note"] * knowledgestatus.MAX_NOTES
    assert status["environment"] is None and status["database"] == "db"


def test_missing_parts_are_null():
    bare = {"ref": "knowledge/northwind-help@1.0.0", "state": "not_indexed"}
    status, problem = knowledgestatus.status_answer({**ANSWER, "versions": [bare]}, REQUEST)
    assert problem is None
    version = status["versions"][0]
    assert version["embedding"] == {"alias": None, "dimensions": None, "table_dimensions": None}
    assert version["notes"] == [] and version["chunks"]["expected"] is None


def test_a_name_takes_only_its_own_versions():
    request = {"ref": "knowledge/help"}
    good = {**VERSION, "ref": "knowledge/help@1.0.0"}
    for ref in (
        "knowledge/help",
        "knowledge/helpx@1.0.0",
        "knowledge/help@1.0",
        "tool/help@1.0.0",
    ):
        status, problem = knowledgestatus.status_answer(
            {**ANSWER, "ref": "knowledge/help", "versions": [good, {**VERSION, "ref": ref}]},
            request,
        )
        assert status is None and problem["code"] == "KNOWLEDGE_STATUS_INVALID", ref
    twice = {**ANSWER, "ref": "knowledge/help", "versions": [good, good]}
    assert knowledgestatus.status_answer(twice, request) == (None, knowledgestatus.INVALID)
    many = [
        {**good, "ref": f"knowledge/help@1.0.{n}"} for n in range(knowledgestatus.MAX_VERSIONS + 1)
    ]
    status, problem = knowledgestatus.status_answer(
        {**ANSWER, "ref": "knowledge/help", "versions": many}, request
    )
    assert problem == knowledgestatus.INVALID


def test_status_argv_never_takes_a_path_from_the_request():
    where = {"path": "/p", "environment": "dev"}
    request = knowledgestatus.status_request({"ref": ["knowledge/help"], "project": ["/etc"]})
    assert request == {"ref": "knowledge/help"}
    assert knowledgestatus.status_argv(where, request) == [
        "knowledge", "status", "knowledge/help", "--project", "/p", "--environment", "dev",
        "--json",
    ]  # fmt: skip


def test_rules_match_the_cli():
    """Local copies (the server does not load the CLI's knowledge command)."""
    assert knowledgestatus.TABLE.pattern == knowledge.TABLE.pattern
    assert knowledgestatus.SCHEMA.pattern == knowledge.SCHEMA.pattern
    assert knowledgestatus.MAX_DIMENSIONS == knowledge.MAX_DIMENSIONS
    assert knowledgestatus.MAX_ROWS == knowledge.MAX_ROWS
    assert knowledgestatus.MAX_DOCUMENTS == registry.MAX_FILES
    assert knowledgestatus.STATES == ("not_indexed", "fresh", "stale")
    for ref in ("knowledge/help", "knowledge/help@1.0.0", "knowledge/a-b@10.2.33"):
        name, version = knowledge.parse_ref(ref)
        pattern = knowledgestatus.KNOWLEDGE_REF if version else knowledgestatus.KNOWLEDGE_NAME
        assert pattern.fullmatch(ref)
    route = next(r for r in server.ROUTES if r.handler is server._knowledge_status)
    assert route.method == "GET"


# -- the real CLI's answers --------------------------------------------------------------------


@pytest.fixture
def knowledge_project(tmp_path):
    root = tmp_path / "proj" / "registry"
    _version(root, version="1.0.0", docs={"a.md": "# T\n\none\n\ntwo"})
    _version(root, version="1.1.0", docs={"a.md": "# T\n\none\n\ntwo\n\nthree"})
    return tmp_path / "proj"


def _cli_json(capsys, *argv) -> tuple[int, object]:
    code = cli.main(["knowledge", "status", *argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_the_real_cli_answer_passes(knowledge_project, capsys, monkeypatch):
    located = [("support_agent", "r", True, True, 8, True)]
    monkeypatch.setattr(command, "_database", _fake_database(located, _indexed(knowledge_project)))
    for ref in ("knowledge/help", "knowledge/help@1.1.0"):
        code, body = _cli_json(capsys, ref, "--project", str(knowledge_project))
        assert code == 0
        request = knowledgestatus.status_request({"ref": [ref]})
        status, problem = knowledgestatus.status_answer(body, request)
        assert problem is None, body
        assert status["versions"] == body["versions"]  # every field passes unchanged
    assert [v["state"] for v in status["versions"]] == ["stale"]
    assert status["versions"][0]["updated"]["oldest"] == T1.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_real_not_indexed_answer_passes(knowledge_project, capsys, monkeypatch):
    monkeypatch.setattr(command, "_database", _fake_database([], []))
    code, body = _cli_json(capsys, "knowledge/help@1.0.0", "--project", str(knowledge_project))
    assert code == 0
    status, problem = knowledgestatus.status_answer(body, {"ref": "knowledge/help@1.0.0"})
    assert problem is None
    (version,) = status["versions"]
    assert version["state"] == "not_indexed" and version["schema"] is None
    assert version["embedding"]["table_dimensions"] is None and version["notes"]


def test_a_real_cli_refusal_passes(knowledge_project, capsys, monkeypatch):
    @contextlib.contextmanager
    def down(where, environment):
        raise knowledge.KnowledgeError("SERVICES_DOWN", "start them with `services up`")
        yield

    monkeypatch.setattr(command, "_database", down)
    code, body = _cli_json(capsys, "knowledge/help", "--project", str(knowledge_project))
    assert code == 2
    status, problem = knowledgestatus.status_answer(body, {"ref": "knowledge/help"})
    assert status is None and problem["code"] == "SERVICES_DOWN"
    assert "services up" in problem["message"]


def test_a_real_registry_refusal_passes(knowledge_project, capsys):
    code, body = _cli_json(capsys, "knowledge/nope", "--project", str(knowledge_project))
    assert code == 2
    status, problem = knowledgestatus.status_answer(body, {"ref": "knowledge/nope"})
    assert status is None and problem["code"] == "REF_NOT_FOUND"
