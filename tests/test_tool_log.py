"""`agentbreeder tool log` without a database (M12 spec delta §4): arguments, the credential
choice and console checks over scripted catalog rows, hostile row shaping, output and refusals.
The real-Postgres runs are tests/test_tool_log_docker.py and tests/test_tool_log_adk_docker.py.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from agentbreeder import cli, dbread, secret_files, toollog
from agentbreeder.commands import tool as tool_command
from agentbreeder.redaction import Redactor
from agentbreeder.services.state import ROOT_ENV, StackState
from agentbreeder.toollog import Query, Target, ToolLogError

NOW = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)
TURN = "9f1c" * 8
AGENT_PASSWORD = "pw-agent-" + "1" * 12
CONSOLE_PASSWORD = "pw-console-" + "4" * 12


def _dsn(user: str, password: str, database: str = "support") -> str:
    """A fake host-form URL, assembled so no credential-shaped literal sits in the source."""
    return "postgresql://" + user + ":" + password + "@127.0.0.1:1/" + database


# -- arguments ---------------------------------------------------------------------------------


def test_parse_ref():
    assert toollog.parse_ref("tool/orders") == Target("orders", None)
    assert toollog.parse_ref("tool/orders@1.0.0") == Target("orders", "1.0.0")
    for bad in ("mcp/github@1.0.0", "tool/Orders", "tool/orders@1.0", "orders", "", "tool/a_b"):
        with pytest.raises(ToolLogError) as raised:
            toollog.parse_ref(bad)
        assert raised.value.code == "REF_INVALID"


def test_target_pattern_escapes_like():
    assert Target("orders", None).pattern() == "tool/orders@%"
    assert Target("orders", "1.0.0").pattern() == "tool/orders@1.0.0"
    assert Target("a-b", None).ref == "tool/a-b"
    assert toollog._like_escape("a_b%c\\") == "a\\_b\\%c\\\\"


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        (None, timedelta(hours=24)),
        ("90s", timedelta(seconds=90)),
        ("10m", timedelta(minutes=10)),
        ("720h", timedelta(hours=720)),
    ],  # fmt: skip
)
def test_parse_since_relative(text, delta):
    assert toollog.parse_since(text, NOW) == NOW - delta


def test_parse_since_absolute_and_refusals():
    assert toollog.parse_since("2026-11-13T09:00:00Z", NOW) == datetime(
        2026, 11, 13, 9, tzinfo=UTC
    )
    for bad in ("721h", "0s", "10d", "2026-13-40T09:00:00Z", "2026-11-14T09:00:00Z", "x"):
        with pytest.raises(ToolLogError) as raised:
            toollog.parse_since(bad, NOW)
        assert raised.value.code == "SINCE_INVALID"


def test_check_limit():
    assert toollog.check_limit("1") == 1 and toollog.check_limit(1000) == 1000
    for bad in (0, 1001, -5, "abc", "", "1e3", " 5", "00000"):
        with pytest.raises(ToolLogError) as raised:
            toollog.check_limit(bad)
        assert raised.value.code == "LIMIT_INVALID"


def _tool(root: Path, name: str, version: str, functions, raw: str | None = None) -> None:
    version_dir = root / "tool" / name / version
    version_dir.mkdir(parents=True)
    (version_dir / "manifest.yaml").write_text(f"kind: tool\nname: {name}\nversion: {version}\n")
    (version_dir / "tool.yaml").write_text(
        raw if raw is not None else yaml.safe_dump({"functions": functions})
    )


def test_legacy_actions(tmp_path):
    root = tmp_path / "registry"
    _tool(root, "orders", "1.0.0", [{"name": "lookup_orders"}, {"name": "bad name"}, "x"])
    _tool(root, "orders", "1.1.0", [{"name": "get_catalog"}, {"name": 5}])
    _tool(root, "orders", "1.2.0", None, raw="functions: [unclosed")
    _tool(root, "orders", "1.3.0", None, raw="functions: 3\n")
    _tool(root, "other", "1.0.0", [{"name": "other_fn"}])
    every = toollog.legacy_actions(Target("orders", None), [root])
    assert every == ["tool:get_catalog", "tool:lookup_orders"]
    assert toollog.legacy_actions(Target("orders", "1.0.0"), [root]) == ["tool:lookup_orders"]
    assert toollog.legacy_actions(Target("missing", None), [root]) == []


# -- scripted database -------------------------------------------------------------------------


class FakeError(Exception):
    def __init__(self, sqlstate=None, text="failed"):
        super().__init__(text)
        self.sqlstate = sqlstate


class FakeOperational(FakeError):
    """psycopg.OperationalError: a connection-level failure."""


FAKE_PSYCOPG = types.SimpleNamespace(OperationalError=FakeOperational)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeDb:
    """Answers each statement by the first key in `answers` that is a substring of the SQL."""

    def __init__(self, answers: dict, fail: dict | None = None):
        self.answers = answers
        self.fail = fail or {}
        self.statements: list[str] = []
        self.closed = False

    def execute(self, statement, params=None):
        text = statement if isinstance(statement, str) else statement.as_string(None)
        self.statements.append(text)
        for key, error in self.fail.items():
            if key in text:
                raise error
        for key, rows in self.answers.items():
            if key in text:
                return FakeCursor(rows)
        return FakeCursor([])

    def close(self):
        self.closed = True


def _fake_psycopg(monkeypatch, connections: dict, connect_error: dict | None = None):
    """A psycopg stand-in: `connect(url)` returns connections[url] (or raises)."""
    import psycopg

    module = types.SimpleNamespace(
        Error=FakeError, OperationalError=FakeOperational, sql=psycopg.sql, opened=[]
    )

    def connect(url, **options):
        assert options["options"] == dbread.OPTIONS and options["autocommit"] is True
        if connect_error and url in connect_error:
            raise connect_error[url]
        module.opened.append(url)
        return connections[url]

    module.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", module)
    return module


AGENT_OWNS = [
    ("turns", "public", 1, True, False, 1),
    ("audit_events", "public", 2, True, False, 1),
]
OWNER_MADE = [
    ("turns", "public", 1, False, True, 1),
    ("audit_events", "public", 2, False, True, 1),
]
CLEAN = [
    ("audit_events", True, True, True, True, True, None, True, True),
    ("turns", True, True, True, True, True, None, True, True),
]
NAMES = ("at", "action", "turn_id", "trace_id", "db_role", "tool", "ok", "agent", "latency_ms")
NAMES += ("args", "error", "phantoms")


def _row(**overrides):
    """One page row as fetch receives it: the id, then ROW_NAMES."""
    row = [
        NOW, "tool:lookup_orders", TURN, "4bf9" * 8, "support_agent", "tool/orders@1.0.0", True,
        "northwind-support", Decimal(41), None, None, 2,
    ]  # fmt: skip
    for key, value in overrides.items():
        row[NAMES.index(key)] = value
    return (7, *row)


COUNTS_ONLY = (None,) * 12 + (3,)  # no page rows: the count row of the LEFT JOIN


def _query(**overrides):
    values = {
        "target": Target("orders", None),
        "since": NOW - timedelta(hours=24),
        "limit": 2,
        "content": False,
        "agent": None,
        "legacy": ["tool:lookup_orders"],
    }
    values.update(overrides)
    return Query(**values)


def test_fetch_agent_credential(monkeypatch):
    db = FakeDb({"pg_has_role": AGENT_OWNS, "atthasdef": [], "WITH calls": [_row(), _row()]})
    module = _fake_psycopg(monkeypatch, {"agent": db})
    result = toollog.fetch("agent", "console", _query())
    assert module.opened == ["agent", "agent"] and db.closed
    assert result.credential == "agent" and result.schema == "public"
    assert result.db_role_verified is False and result.phantom_rows == 2
    assert len(result.rows) == 2 and result.truncated is False
    query = next(s for s in db.statements if "WITH calls" in s)
    assert "NULL::text" in query and "pg_column_size" not in query  # no db_role, no content
    assert "m.agent = " not in query and "PRECEDING" in query
    assert any("REPEATABLE READ READ ONLY" in s for s in db.statements)
    assert any("statement_timeout = '5s'" in s for s in db.statements)


def test_fetch_console_credential_verified_and_truncated(monkeypatch):
    agent = FakeDb({"pg_has_role": OWNER_MADE})
    console = FakeDb(
        {
            "no_trigger_acl": CLEAN,
            "SELECT EXISTS": [(True,)],
            "atthasdef": [(False,)],
            "WITH calls": [_row(), _row(), _row()],
        }
    )
    module = _fake_psycopg(monkeypatch, {"agent": agent, "console": console})
    result = toollog.fetch("agent", "console", _query(content=True, agent="northwind-support"))
    assert module.opened == ["agent", "console"] and agent.closed and console.closed
    assert result.credential == "console" and result.db_role_verified is True
    assert result.truncated is True and len(result.rows) == 2
    assert result.phantom_rows == 0  # with --agent, phantoms (no agent) are not reported
    query = next(s for s in console.statements if "WITH calls" in s)
    assert "a.db_role" in query and "pg_column_size" in query and "m.agent = " in query


def test_fetch_db_role_default_is_not_verified(monkeypatch):
    agent = FakeDb({"pg_has_role": OWNER_MADE})
    console = FakeDb(
        {"no_trigger_acl": CLEAN, "atthasdef": [(True,)], "WITH calls": [COUNTS_ONLY]}
    )
    _fake_psycopg(monkeypatch, {"agent": agent, "console": console})
    result = toollog.fetch("agent", "console", _query())
    assert result.db_role_verified is False and result.rows == []
    assert result.phantom_rows == 3 and result.truncated is False  # counted with no rows shown


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([], "TOOL_LOG_NO_TABLES"),
        ([("turns", "public", 1, True, False, 1)], "TOOL_LOG_NO_TABLES"),
        (
            [("turns", "agent", 1, True, False, 0), ("audit_events", "public", 2, True, False, 1)],
            "CONSOLE_OBJECT_UNEXPECTED",
        ),
        (
            [("turns", "Bad", 1, True, False, 0), ("audit_events", "Bad", 2, True, False, 0)],
            "CONSOLE_OBJECT_UNEXPECTED",
        ),
        (
            [
                ("turns", "public", 1, True, False, 1),
                ("audit_events", "public", 2, False, True, 1),
            ],
            "CONSOLE_OBJECT_UNEXPECTED",
        ),
    ],
)
def test_locate_refusals(monkeypatch, rows, code):
    _fake_psycopg(monkeypatch, {"agent": FakeDb({"pg_has_role": rows})})
    with pytest.raises(ToolLogError) as raised:
        toollog.fetch("agent", "console", _query())
    assert raised.value.code == code


def test_locate_prefers_the_agents_own_schema():
    rows = [
        ("turns", "support_agent", 1, True, False, 0),
        ("audit_events", "support_agent", 2, True, False, 0),
        ("turns", "public", 3, False, True, 1),
        ("audit_events", "public", 4, False, True, 1),
    ]
    assert toollog.locate(FakeDb({"pg_has_role": rows})) == ("support_agent", "agent")


def test_console_missing(monkeypatch):
    _fake_psycopg(monkeypatch, {"agent": FakeDb({"pg_has_role": OWNER_MADE})})
    with pytest.raises(ToolLogError) as raised:
        toollog.fetch("agent", None, _query())
    assert raised.value.code == "CONSOLE_ROLE_MISSING"


@pytest.mark.parametrize("index", range(1, 9))
def test_console_check_each_failure(index):
    bad = list(CLEAN[0])
    bad[index] = ["planted\x1b[2J"] if index == 6 else False
    db = FakeDb({"no_trigger_acl": [tuple(bad), CLEAN[1]]})
    with pytest.raises(ToolLogError) as raised:
        toollog.console_check(db, "public")
    assert raised.value.code == "CONSOLE_OBJECT_UNEXPECTED"
    assert "\x1b" not in str(raised.value)


def test_console_check_needs_both_relations():
    with pytest.raises(ToolLogError) as raised:
        toollog.console_check(FakeDb({"no_trigger_acl": CLEAN[:1]}), "public")
    assert raised.value.code == "CONSOLE_OBJECT_UNEXPECTED"


@pytest.mark.parametrize(
    ("error", "which", "code"),
    [
        (FakeError("28P01"), "agent", "DB_LOGIN_FAILED"),
        (FakeError("28000"), "console", "DB_LOGIN_FAILED"),
        (
            FakeOperational(None, "password authentication failed for x"),
            "agent",
            "DB_LOGIN_FAILED",
        ),
        (FakeOperational(None, 'role "x" does not exist'), "agent", "DB_LOGIN_FAILED"),
        (FakeOperational(None, "no pg_hba.conf entry for host"), "console", "DB_LOGIN_FAILED"),
        (FakeError("42501"), "console", "CONSOLE_ROLE_NO_ACCESS"),
        (FakeError("42501"), "agent", "TOOL_LOG_NO_ACCESS"),
        (FakeError("42P01"), "console", "TOOL_LOG_NO_TABLES"),
        (FakeError("57014"), "console", "TOOL_LOG_TIMEOUT"),
        (FakeError("08006"), "agent", "SERVICES_DOWN"),
        (FakeOperational(None, "connection refused"), "agent", "SERVICES_DOWN"),
        (FakeOperational(None, 'database "x" does not exist'), "agent", "SERVICES_DOWN"),
        (FakeOperational(None, 'database "my-role" does not exist'), "agent", "SERVICES_DOWN"),
        (FakeError(None, "timestamp too large (after year 10K)"), "agent", "TOOL_LOG_FAILED"),
        (FakeError(None, "authentication failed"), "agent", "TOOL_LOG_FAILED"),  # not at connect
        (FakeError("XX000"), "agent", "TOOL_LOG_FAILED"),
    ],
)
def test_mapped_errors_never_carry_driver_text(error, which, code):
    error.args = (_dsn("u", AGENT_PASSWORD) + " " + str(error),)
    mapped = dbread.mapped(error, which, FAKE_PSYCOPG, toollog.PREFIX, timeout="narrow --since")
    assert mapped.code == code and AGENT_PASSWORD not in str(mapped)
    assert "127.0.0.1" not in str(mapped)


def test_errors_on_connect_and_query(monkeypatch):
    _fake_psycopg(monkeypatch, {}, connect_error={"agent": FakeError("28P01")})
    with pytest.raises(ToolLogError) as raised:
        toollog.fetch("agent", None, _query())
    assert raised.value.code == "DB_LOGIN_FAILED" and "agent credential" in str(raised.value)

    _fake_psycopg(monkeypatch, {"agent": FakeDb({}, fail={"pg_has_role": FakeError("57014")})})
    with pytest.raises(ToolLogError) as raised:
        toollog.fetch("agent", None, _query())
    assert raised.value.code == "TOOL_LOG_TIMEOUT"

    agent = FakeDb({"pg_has_role": OWNER_MADE})
    console = FakeDb({"no_trigger_acl": CLEAN}, fail={"WITH calls": FakeError("42501")})
    _fake_psycopg(monkeypatch, {"agent": agent, "console": console})
    with pytest.raises(ToolLogError) as raised:
        toollog.fetch("agent", "console", _query())
    assert raised.value.code == "CONSOLE_ROLE_NO_ACCESS" and console.closed


def test_read_session_keeps_other_errors_and_closes(monkeypatch):
    db = FakeDb({})
    module = _fake_psycopg(monkeypatch, {"agent": db})
    with pytest.raises(ToolLogError) as raised:
        with dbread.read_session(module, "agent", "agent", "X"):
            raise ToolLogError("CONSOLE_OBJECT_UNEXPECTED", "planted")
    assert raised.value.code == "CONSOLE_OBJECT_UNEXPECTED" and db.closed
    assert "ROLLBACK" not in db.statements and db.statements[0].startswith("BEGIN")
    with pytest.raises(ToolLogError) as raised:
        with dbread.read_session(module, "agent", "agent", "X", timeout="wait"):
            raise FakeError("57014")
    assert raised.value.code == "X_TIMEOUT" and str(raised.value).endswith("wait")


def test_dependency_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ToolLogError) as raised:
        toollog.fetch("agent", None, _query())
    assert raised.value.code == "DEPENDENCY_MISSING" and "agentbreeder[eval]" in str(raised.value)


# -- shaping hostile rows ----------------------------------------------------------------------


def _shaped(content=False, redact=None, **row):
    values = dict(zip(NAMES, _row(**row)[1:], strict=True))
    return toollog.shape(values, content, redact or Redactor([]))


def test_shape_valid_row():
    assert _shaped() == {
        "at": "2026-11-13T17:00:00.000Z", "agent": "northwind-support",
        "tool": "tool/orders@1.0.0", "function": "lookup_orders", "status": "ok",
        "latency_ms": 41, "turn_id": TURN, "trace_id": "4bf9" * 8, "db_role": "support_agent",
    }  # fmt: skip
    assert _shaped(ok=False)["status"] == "error"


def test_shape_hostile_fields_become_null():
    shaped = _shaped(
        at="2026-11-13",
        action="tool:lookup\x1b]52;c;aGk=\x07",
        turn_id="ZZ",
        trace_id="4bf9‮",
        db_role="Support",
        tool="tool/orders@1.0.0\n",
        ok="true",
        agent="-bad-",
        latency_ms=Decimal("1.5"),
    )
    assert shaped == {
        "at": None, "agent": None, "tool": None, "function": None, "status": None,
        "latency_ms": None, "turn_id": None, "trace_id": None, "db_role": None,
    }  # fmt: skip
    for latency in (Decimal(-1), Decimal(86_400_001), True, "41", 1.0):
        assert _shaped(latency_ms=latency)["latency_ms"] is None
    assert _shaped(latency_ms=86_400_000)["latency_ms"] == 86_400_000
    assert _shaped(action=None)["function"] is None


def test_shape_content_is_redacted_cleaned_and_bounded():
    secret = "s3cret-value-" + "9" * 10
    redact = Redactor([secret.encode()])
    args = {
        "q": f"look {secret}\x1b[2J‮",
        secret: [1, 2.5, None, True, {"deep": "x" * 5000}],
        "n": 10**30,
    }
    shaped = _shaped(True, redact, args=json.dumps(args), error=f"boom {secret}\x1b]8;;\x07")
    text = json.dumps(shaped)
    assert secret not in text and "\x1b" not in text and "‮" not in text
    assert shaped["args"]["q"].startswith("look [redacted]")
    assert shaped["args"]["[redacted]"][:4] == [1, 2.5, None, True]
    assert len(shaped["args"]["[redacted]"][4]["deep"]) == toollog.TEXT_LIMIT
    assert shaped["args"]["n"] == 10**30
    assert shaped["error"].startswith("boom [redacted]")


def test_shape_args_shapes():
    nested = json.loads("[" * 40 + "]" * 40)
    kept = _shaped(True, args=json.dumps({"a": nested}))["args"]["a"]
    depth = 1
    while isinstance(kept, list):
        kept, depth = kept[0], depth + 1
    assert kept == "…" and depth == toollog.ARGS_DEPTH
    assert _shaped(True, args="[" * 100000 + "]" * 100000)["args"] is None  # RecursionError
    assert _shaped(True, args="not json")["args"] is None
    assert _shaped(True, args="[1]")["args"] is None  # not an object
    assert _shaped(True, args=None)["args"] is None and _shaped(True, error=5)["error"] is None
    assert toollog._clean(float("inf"), Redactor([])) is None
    assert toollog._clean(object(), Redactor([])) is None


# -- the command -------------------------------------------------------------------------------


def _stack(project: Path, binding="postgres.roles.support_agent", **postgres_with) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    stack = {
        "kind": "stack",
        "schema_version": 1,
        "name": "northwind",
        "services": {
            "postgres": {
                "ref": "service/postgres-pgvector@1.2.0",
                "with": {
                    "databases": ["support"],
                    "roles": ["support_agent"],
                    "roles_database": "support",
                    **postgres_with,
                },
            },  # fmt: skip
            "litellm": {"ref": "service/litellm@1.0.0"},
            "phoenix": {"ref": "service/phoenix@1.0.0"},
        },
        "environment": {
            "gateway": "litellm",
            "models": ["m"],
            "ceilings": {"budget_usd": 5, "rpm": 60},
            "bindings": {"DATABASE_URL": {"ref": binding}} if binding else {},
            "agents": {
                "northwind-support": {"sponsor": "a@example.com", "review_by": "2027-01-01"}
            },
        },
    }
    (project / "stack.yaml").write_text(yaml.safe_dump(stack))
    return project


@pytest.fixture
def stacks(tmp_path, monkeypatch):
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    return tmp_path / "stacks"


def _written(project: Path, environments=("dev",), secrets=None, phoenix_port=6006) -> Path:
    from agentbreeder.services.engine import load

    state = StackState(load(project).name, project.resolve())
    if phoenix_port:
        state.record_port("phoenix", "http", phoenix_port)
    for environment in environments:
        directory = state.env_dir(environment)
        directory.mkdir(parents=True)
        (directory / "operator.yaml").write_text("x: 1\n")
        store = state.deploy_state(environment)
        for name, value in (secrets or {}).items():
            secret_files.set_secret(store, name, value.encode())
    state.close()
    return state.directory


def _run(capsys, *argv):
    code = cli.main(["tool", "log", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _error(capsys, *argv) -> str:
    code, out, err = _run(capsys, *argv, "--json")
    assert code == 2
    body = json.loads(out)
    assert set(body) == {"schema_version", "error"} and err.startswith("[")
    return body["error"]["code"]


SECRETS = {
    "agent-database": _dsn("a", AGENT_PASSWORD),
    "console-database": _dsn("c", CONSOLE_PASSWORD),
}


def test_refusals_before_the_database(tmp_path, stacks, capsys):
    project = _stack(tmp_path / "p")
    where = ("--project", str(project))
    assert _error(capsys, "mcp/x@1.0.0", *where) == "REF_INVALID"
    assert _error(capsys, "tool/orders", "--since", "9999h", *where) == "SINCE_INVALID"
    assert _error(capsys, "tool/orders", "--limit", "0", *where) == "LIMIT_INVALID"
    assert _error(capsys, "tool/orders", "--agent", "nobody", *where) == "AGENT_NOT_FOUND"
    assert _error(capsys, "tool/orders", "--project", str(tmp_path)) == "STACK_MISSING"
    assert _error(capsys, "tool/orders", *where) == "SERVICES_DOWN"
    _written(project, ("dev", "prod"))
    assert _error(capsys, "tool/orders", *where) == "ENVIRONMENT_AMBIGUOUS"
    assert _error(capsys, "tool/orders", "--environment", "qa", *where) == "SERVICES_DOWN"
    assert _error(capsys, "tool/orders", "--environment", "dev", *where) == "SERVICES_STALE"
    code, out, err = _run(capsys, "tool/orders", "--environment", "dev", *where)
    assert code == 2 and out == "" and "[SERVICES_STALE]" in err


def test_stale_secret_file(tmp_path, stacks, capsys):
    project = _stack(tmp_path / "p")
    directory = _written(project)
    path = directory / "envs" / "dev" / "state" / "secrets"
    path.mkdir(parents=True)
    (path / "agent-database").write_bytes(b"x")
    (path / "agent-database").chmod(0o644)
    assert _error(capsys, "tool/orders", "--project", str(project)) == "SERVICES_STALE"
    (path / "agent-database").chmod(0o600)
    (path / "agent-database").write_bytes(b"\xff\xfe")
    assert _error(capsys, "tool/orders", "--project", str(project)) == "SERVICES_STALE"


@pytest.mark.parametrize(
    ("binding", "extra", "database"),
    [
        ("postgres.roles.support_agent", {}, "support"),
        ("postgres.databases.support", {}, "support"),
        ("postgres.roles.support_agent", {"roles_database": ["x"]}, None),
        ("postgres.console", {}, None),
        ("litellm.url", {}, None),
        ("nosuch.databases.x", {}, None),
        (None, {}, None),
    ],
)
def test_database_name(tmp_path, binding, extra, database):
    from agentbreeder.services.engine import load

    stack = load(_stack(tmp_path / "p", binding, **extra))
    if database:
        assert dbread.database_name(stack, toollog.PREFIX) == database
    else:
        with pytest.raises(ToolLogError) as raised:
            dbread.database_name(stack, toollog.PREFIX)
        assert raised.value.code == "TOOL_LOG_UNSUPPORTED"


def test_database_name_rejects_a_malformed_reference(tmp_path):
    from agentbreeder.services.engine import load

    stack = load(_stack(tmp_path / "p"))
    object.__setattr__(stack.environment.bindings["DATABASE_URL"], "ref", "Not A Ref")
    with pytest.raises(ToolLogError):
        dbread.database_name(stack, toollog.PREFIX)


def _fetched(monkeypatch, result: toollog.Result, seen: list):
    def fetch(agent_url, console_url, query):
        seen.append((agent_url, console_url, query))
        return result

    monkeypatch.setattr(toollog, "fetch", fetch)


def _result(rows=None, **extra) -> toollog.Result:
    result = toollog.Result("console", "public", True, **extra)
    rows = rows if rows is not None else [_row()]
    result.rows = [dict(zip(NAMES, row[1:], strict=True)) for row in rows]
    return result


def test_json_output(tmp_path, stacks, capsys, monkeypatch):
    project = _stack(tmp_path / "p")
    _written(project, secrets=SECRETS)
    seen = []
    rows = [_row(), _row(agent=None, latency_ms=None, tool=None, ok=False)]
    _fetched(monkeypatch, _result(rows, phantom_rows=1, truncated=True), seen)
    code, out, err = _run(capsys, "tool/orders", "--project", str(project), "--json")
    assert code == 0 and err == ""
    body = json.loads(out)
    assert body["schema_version"] == 1 and body["ref"] == "tool/orders"
    assert body["environment"] == "dev" and body["database"] == "support"
    assert body["credential"] == "console" and body["db_role_verified"] is True
    assert body["phoenix_url"] == "http://127.0.0.1:6006"
    assert body["truncated"] is True and body["phantom_rows"] == 1 and body["limit"] == 100
    assert body["content"] is False and "args" not in body["calls"][0]
    assert [c["status"] for c in body["calls"]] == ["ok", "error"]
    assert len(body["notes"]) == 3 and "#922" in body["notes"][1]
    agent_url, console_url, query = seen[0]
    assert agent_url == SECRETS["agent-database"] and console_url == SECRETS["console-database"]
    assert query.limit == 100 and query.content is False and query.legacy == []
    since = datetime.strptime(body["window"]["since"], "%Y-%m-%dT%H:%M:%SZ")
    until = datetime.strptime(body["window"]["until"], "%Y-%m-%dT%H:%M:%SZ")
    assert until - since == timedelta(hours=24)


def test_text_output_and_content(tmp_path, stacks, capsys, monkeypatch):
    project = _stack(tmp_path / "p")
    _written(project, secrets={"agent-database": SECRETS["agent-database"]}, phoenix_port=None)
    rows = [
        _row(db_role="agentbreeder_console", args=json.dumps({"q": AGENT_PASSWORD}), error="e"),
        _row(latency_ms=None, args=None),
    ]
    result = _result(rows, truncated=True)
    result.credential, result.db_role_verified = "agent", False
    seen = []
    _fetched(monkeypatch, result, seen)
    code, out, err = _run(
        capsys, "tool/orders@1.0.0", "--project", str(project), "--content", "--agent",
        "northwind-support", "--since", "10m",
    )  # fmt: skip
    assert code == 0 and "Arguments are customer data" in err
    assert seen[0][1] is None and seen[0][2].agent == "northwind-support"
    assert "tool/orders@1.0.0, last 10m, newest first" in out and "agent credential" in out
    assert "agentbreeder_console (console)" in out and "41 ms" in out
    assert AGENT_PASSWORD not in out and '"q": "[redacted]"' in out and 'error: "e"' in out
    assert "… more; narrow --since or raise --limit" in out
    assert "Note: --agent leaves out rows" in out and "Phoenix:" not in out
    assert "Note: db_role is not verified" in out


def test_text_output_without_calls(tmp_path, stacks, capsys, monkeypatch):
    project = _stack(tmp_path / "p")
    _written(project, secrets=SECRETS)
    _fetched(monkeypatch, _result([]), [])
    code, out, _ = _run(capsys, "tool/orders", "--project", str(project))
    assert code == 0 and "No calls in this window." in out
    assert "last 24h" in out and "Phoenix: http://127.0.0.1:6006" in out
    assert "schema public" in out


def test_absolute_since_header_and_agent_schema_note(tmp_path, stacks, capsys, monkeypatch):
    project = _stack(tmp_path / "p")
    _written(project, secrets=SECRETS)
    result = _result([])
    result.schema = "support_agent"
    _fetched(monkeypatch, result, [])
    since = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    code, out, _ = _run(capsys, "tool/orders", "--project", str(project), "--since", since)
    assert code == 0 and f"since {since}" in out
    assert "Note: Read from the agent role's own schema support_agent" in out
    assert _error(capsys, "tool/orders", "--project", str(project), "--limit", "x") == (
        "LIMIT_INVALID"
    )


def test_phoenix_url_without_state():
    assert tool_command._phoenix_url(None, None) is None


def test_redaction_includes_bare_passwords(tmp_path):
    db = dbread.StackDatabase(
        tmp_path, "dev", "support", tmp_path, tmp_path, SECRETS["agent-database"],
        "postgresql://x:[bad",
    )  # fmt: skip
    values = dbread.redaction(db)
    assert AGENT_PASSWORD in values and SECRETS["agent-database"] in values
    assert AGENT_PASSWORD not in repr(db) and "[bad" not in repr(db)
