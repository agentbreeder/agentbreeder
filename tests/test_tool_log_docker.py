"""Opt-in real Docker runs of `agentbreeder tool log` against service/postgres-pgvector@1.2.0
(M12 spec delta §3.1, §3.4, §4; #922 phantom rows).

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_tool_log_docker.py
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from test_services_engine import STACK, write_project

from agentbreeder import cli, registry, secret_files
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
TURN = "9f1c" * 8
TRACE = "4bf9" * 8
OLD_TURN = "ab12" * 8
FORGED_TURN = "cd34" * 8


def _project(tmp_path: Path, with_: dict, binding: str, init_sql: bool) -> Path:
    stack = json.loads(json.dumps(STACK))
    stack["services"] = {
        "postgres": {"ref": "service/postgres-pgvector@1.2.0", "with": with_},
        "gateway": stack["services"]["gateway"],
    }
    stack["environment"]["services"] = {}
    stack["environment"]["bindings"] = {"DATABASE_URL": {"ref": binding}}
    stack["environment"]["eval_database"] = {"ref": "postgres.databases." + with_["databases"][0]}
    stack["environment"]["agents"] = {
        "northwind-support": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}
    }
    project = write_project(tmp_path / "nw", stack=stack)
    if init_sql:
        (project / "db").mkdir()
        (project / "db" / "commerce.sql").write_bytes(
            (NORTHWIND / "stack" / "db" / "commerce.sql").read_bytes()
        )
    tool = project / "registry" / "tool" / "orders"
    shutil.copytree(NORTHWIND / "registry" / "tool" / "orders", tool)
    return project


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    made = []

    def make(project: Path) -> Engine:
        instance = Engine(project)
        made.append(instance)
        assert instance.up("dev", confirm=lambda grant: True)["services"]["postgres"] == "created"
        return instance

    yield make
    for instance in made:
        try:
            instance.active_deployments = lambda: []
            instance.down(volumes=True)
        finally:
            instance.close()


def _secret(one: Engine, name: str) -> str:
    return secret_files._read(one.state.deploy_state("dev") / "secrets" / name).decode()


def _log(capsys, project: Path, *extra: str) -> tuple[int, dict, str]:
    code = cli.main(["tool", "log", "tool/orders", "--project", str(project), "--json", *extra])
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def _rows(conn, rows: list[tuple[str, str, dict]]) -> None:
    for turn, action, detail in rows:
        conn.execute(
            "INSERT INTO audit_events (turn_id, actor, action, detail) VALUES (%s, 'agent', %s, "
            "%s::jsonb)",
            (turn, action, json.dumps(detail)),
        )


def _turn(conn, turn: str, trace: str | None) -> None:
    conn.execute(
        "INSERT INTO turns (id, session, customer, message, reply, trace_id, latency_ms, "
        "model_calls, tools, sources, evidence, retrieval, prompt_tokens, output_tokens) "
        "VALUES (%s, 's', 'cust-001', 'm', 'r', %s, 1, 1, '[]', '[]', '[]', 'none', 1, 1)",
        (turn, trace),
    )


def test_tool_log_owner_managed_console(tmp_path, engine, capsys):
    import psycopg

    project = _project(
        tmp_path,
        {
            "databases": ["support"],
            "init_sql": "db/commerce.sql",
            "init_sql_database": "support",
            "roles": ["support_agent", "support_ops"],
            "roles_database": "support",
            "console_database": "support",
        },
        "postgres.roles.support_agent",
        init_sql=True,
    )
    one = engine(project)
    port = one.status()["services"]["postgres"]["host_ports"]["pg"]
    owner_url = _secret(one, "eval-database")  # the database owner, host form
    agent_secret = one.state.read_secret("postgres", "login.support_agent").decode()
    agent_url = f"postgresql://support_agent:{agent_secret}@127.0.0.1:{port}/support"
    tool = "tool/orders@1.0.0"
    with psycopg.connect(agent_url, autocommit=True) as conn:
        _turn(conn, TURN, TRACE)
        _rows(
            conn,
            [
                # runtime/adk 1.2.0: one row per call, failures included
                (
                    TURN,
                    "tool:lookup_orders",
                    {
                        "args": {"order_id": "NW-10021"},
                        "ok": True,
                        "error": None,
                        "tool": tool,
                        "agent": "northwind-support",
                        "latency_ms": 41,
                    },
                ),
                (
                    TURN,
                    "tool:lookup_orders",
                    {
                        "args": {"order_id": agent_secret},
                        "ok": False,
                        "error": "boom \x1b]52;c;aGk=\x07",
                        "tool": tool,
                        "agent": "northwind-support",
                        "latency_ms": 7,
                    },
                ),
                # runtime/adk 1.1.0: a failure (no tool), then the phantom ok row (#922)
                (OLD_TURN, "tool:lookup_orders", {"args": {}, "ok": False, "error": "old"}),
                (
                    OLD_TURN,
                    "tool:lookup_orders",
                    {"args": {}, "ok": True, "error": None, "tool": tool},
                ),
                # 1.1.0, a real success of another function in the same turn
                (
                    OLD_TURN,
                    "tool:get_product_catalog",
                    {"args": {}, "ok": True, "error": None, "tool": tool},
                ),
                # not this tool: an MCP call with the same function name, another tool
                (TURN, "tool:lookup_orders", {"args": {}, "ok": False, "mcp": "mcp/github@1.0.0"}),
                (TURN, "tool:lookup_orders", {"args": {}, "ok": True, "tool": "tool/other@1.0.0"}),
                # forged by the agent role: hostile shapes
                (
                    FORGED_TURN,
                    "tool:lookup_orders\x1b[2J",
                    {
                        "ok": "true",
                        "tool": tool + "‮",
                        "agent": "evil\x1b]8;;x\x07",
                        "latency_ms": "5" * 20,
                        "args": "x" * 5,
                    },
                ),
                (
                    FORGED_TURN,
                    "tool:lookup_orders",
                    {
                        "ok": True,
                        "tool": tool,
                        "agent": "northwind-support",
                        "args": {"blob": "y" * (1024 * 1024)},
                    },
                ),
            ],  # fmt: skip
        )
        conn.execute(
            "INSERT INTO audit_events (turn_id, actor, action, detail, db_role) "
            "VALUES (%s, 'agent', 'tool:lookup_orders', %s::jsonb, 'agentbreeder_console')",
            (FORGED_TURN, json.dumps({"ok": True, "tool": tool, "agent": "northwind-support"})),
        )

    code, body, err = _log(capsys, project)
    assert code == 0, err
    assert body["credential"] == "console" and body["database"] == "support"
    assert body["db_role_verified"] is True
    assert body["phantom_rows"] == 1 and body["truncated"] is False
    assert body["content"] is False and "args" not in body["calls"][0]
    calls = body["calls"]
    assert len(calls) == 7  # 2 new, the old failure and catalog call, 3 forged (phantom out)
    newest = calls[0]
    assert newest["db_role"] == "support_agent"  # the trigger overrode the forged value
    forged = [c for c in calls if c["turn_id"] == FORGED_TURN]
    hostile = next(c for c in forged if c["tool"] is None)
    assert hostile == {
        "at": hostile["at"], "agent": None, "tool": None, "function": None, "status": None,
        "latency_ms": None, "turn_id": FORGED_TURN, "trace_id": None, "db_role": "support_agent",
    }  # fmt: skip
    first = next(c for c in calls if c["latency_ms"] == 41)
    assert first["trace_id"] == TRACE and first["status"] == "ok"
    assert first["agent"] == "northwind-support" and first["function"] == "lookup_orders"
    old = [c for c in calls if c["turn_id"] == OLD_TURN]
    assert sorted((c["function"], c["status"], c["tool"]) for c in old) == [
        ("get_product_catalog", "ok", tool),
        ("lookup_orders", "error", None),
    ]
    assert any("#922" in note for note in body["notes"])

    # --content: arguments redacted and cleaned, the megabyte args left out, errors cleaned
    code, body, err = _log(capsys, project, "--content")
    assert code == 0 and "Arguments are customer data" in err
    failed = next(c for c in body["calls"] if c["latency_ms"] == 7)
    assert agent_secret not in json.dumps(body)
    assert failed["args"] == {"order_id": "[redacted]"}
    assert "\x1b" not in failed["error"] and failed["error"].startswith("boom")
    huge = next(c for c in body["calls"] if c["turn_id"] == FORGED_TURN and c["tool"])
    assert huge["args"] is None

    # text output stays clean
    assert cli.main(["tool", "log", "tool/orders", "--project", str(project)]) == 0
    text = capsys.readouterr().out
    assert "\x1b" not in text and "‮" not in text and "newest first" in text

    # --limit, --agent, a version with no calls
    code, body, _ = _log(capsys, project, "--limit", "2")
    assert body["truncated"] is True and len(body["calls"]) == 2
    code, body, _ = _log(capsys, project, "--agent", "northwind-support")
    assert {c["agent"] for c in body["calls"]} == {"northwind-support"}
    code = cli.main(["tool", "log", "tool/orders@9.9.9", "--project", str(project), "--json"])
    assert code == 0 and json.loads(capsys.readouterr().out)["calls"] == []

    deploy_state = one.state.deploy_state("dev")

    def refused(code_name: str) -> None:
        code, body, err = _log(capsys, project)
        assert code == 2 and body["error"]["code"] == code_name, body
        assert agent_secret not in err and "127.0.0.1" not in err

    with psycopg.connect(owner_url, autocommit=True) as conn:
        # the db_role trigger disabled: no longer "in force now"
        conn.execute("ALTER TABLE public.audit_events DISABLE TRIGGER audit_events_set_db_role")
        assert _log(capsys, project)[1]["db_role_verified"] is False
        conn.execute("ALTER TABLE public.audit_events ENABLE TRIGGER audit_events_set_db_role")
        # TRIGGER granted to the agent role, a planted trigger, row security: refused
        conn.execute("GRANT TRIGGER ON public.turns TO support_agent")
        refused("CONSOLE_OBJECT_UNEXPECTED")
        conn.execute("REVOKE TRIGGER ON public.turns FROM support_agent")
        conn.execute(
            "CREATE FUNCTION public.planted() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RETURN NEW; END $$"
        )
        conn.execute(
            "CREATE TRIGGER planted BEFORE INSERT ON public.turns FOR EACH ROW "
            "EXECUTE FUNCTION public.planted()"
        )
        refused("CONSOLE_OBJECT_UNEXPECTED")
        conn.execute("DROP TRIGGER planted ON public.turns")
        conn.execute("ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY")
        refused("CONSOLE_OBJECT_UNEXPECTED")
        conn.execute("ALTER TABLE public.audit_events DISABLE ROW LEVEL SECURITY")
        # no grant: the console role cannot read
        conn.execute("REVOKE SELECT ON public.turns FROM agentbreeder_console")
        refused("CONSOLE_ROLE_NO_ACCESS")
        conn.execute("GRANT SELECT ON public.turns TO agentbreeder_console")
        assert _log(capsys, project)[0] == 0

    # credentials: a wrong console password, no console secret, no host-form secrets
    real = (deploy_state / "secrets" / "console-database").read_bytes()
    secret_files.set_secret(
        deploy_state, "console-database", real.replace(real.split(b":")[2].split(b"@")[0], b"x")
    )
    refused("DB_LOGIN_FAILED")
    secret_files.delete_secret(deploy_state, "console-database")
    refused("CONSOLE_ROLE_MISSING")
    secret_files.delete_secret(deploy_state, "agent-database")
    refused("SERVICES_STALE")


def test_tool_log_runtime_managed_agent_credential(tmp_path, engine, capsys):
    """The starter layout: the agent's credential is the database owner's, so it reads its own
    tables; no tables yet is TOOL_LOG_NO_TABLES; db_role is not verified."""
    import psycopg

    project = _project(tmp_path, {"databases": ["app"]}, "postgres.databases.app", False)
    one = engine(project)
    code, body, _ = _log(capsys, project)
    assert code == 2 and body["error"]["code"] == "TOOL_LOG_NO_TABLES"
    agent_url = _secret(one, "agent-database")
    runtime = registry.BUILTIN_ROOT / "runtime" / "adk" / "1.2.0" / "runtime" / "memory.py"
    source = runtime.read_text()
    ddl = source.split('SCHEMA = """', 1)[1].split('"""', 1)[0]  # the runtime's own DDL
    with psycopg.connect(agent_url, autocommit=True) as conn:
        conn.execute(ddl)
        _turn(conn, TURN, TRACE)
        _rows(
            conn,
            [
                (
                    TURN,
                    "tool:lookup_orders",
                    {
                        "args": {},
                        "ok": True,
                        "tool": "tool/orders@1.0.0",
                        "agent": "northwind-support",
                        "latency_ms": 3,
                    },
                )
            ],  # fmt: skip
        )
    code, body, err = _log(capsys, project)
    assert code == 0, err
    assert body["credential"] == "agent" and body["db_role_verified"] is False
    assert [c["latency_ms"] for c in body["calls"]] == [3]
    assert body["calls"][0]["db_role"] is None


def test_tool_log_phantom_pairing_and_hostile_times(tmp_path, engine, capsys):
    """#922 pairing on real Postgres: interleaved old-runtime failures, a real success between a
    failure and its phantom, the --since boundary, a result of phantoms only, 1.2.0 rows never
    paired, and an `at` of infinity (psycopg cannot load it) outside every window."""
    import psycopg

    project = _project(tmp_path, {"databases": ["app"]}, "postgres.databases.app", False)
    one = engine(project)
    runtime = registry.BUILTIN_ROOT / "runtime" / "adk" / "1.2.0" / "runtime" / "memory.py"
    ddl = runtime.read_text().split('SCHEMA = """', 1)[1].split('"""', 1)[0]
    tool = "tool/orders@1.0.0"
    fail = {"args": {}, "ok": False, "error": "old"}
    phantom = {"args": {}, "ok": True, "error": None, "tool": tool}
    new_fail = {"ok": False, "tool": tool, "agent": "northwind-support", "latency_ms": 1}
    new_ok = {"ok": True, "tool": tool, "agent": "northwind-support", "latency_ms": 2}
    interleaved, between, boundary, fresh = "aa" * 16, "bb" * 16, "cc" * 16, "dd" * 16
    with psycopg.connect(_secret(one, "agent-database"), autocommit=True) as conn:
        conn.execute(ddl)

        def row(turn, detail, at="now()", action="tool:lookup_orders"):
            conn.execute(
                "INSERT INTO audit_events (turn_id, actor, action, detail, at) "
                f"VALUES (%s, 'agent', %s, %s::jsonb, {at})",
                (turn, action, json.dumps(detail)),
            )

        for detail in (fail, fail, phantom, phantom):  # two parallel failed calls on 1.1.0
            row(interleaved, detail)
        for detail in (fail, phantom, phantom):  # a failure, its phantom, then a real success
            row(between, detail)
        row(boundary, fail, at="now() - interval '2 hours 30 seconds'")
        row(boundary, phantom, at="now() - interval '1 hour 59 minutes 30 seconds'")
        row(fresh, new_fail)  # 1.2.0: a failure then a success, both real
        row(fresh, new_ok)
        row(fresh, {**new_ok, "latency_ms": 3}, at="'infinity'")
        row(fresh, {**new_ok, "latency_ms": 4}, at="now() + interval '1 day'")
        row(fresh, fail, action="tool:gone_fn")  # a tool no root has: only its phantom matches
        row(fresh, {**phantom, "tool": "tool/gone@1.0.0"}, action="tool:gone_fn")

    code, body, err = _log(capsys, project, "--since", "2h")
    assert code == 0, err
    calls = body["calls"]
    statuses = {turn: [c["status"] for c in calls if c["turn_id"] == turn] for turn in
                (interleaved, between, boundary, fresh)}  # fmt: skip
    assert statuses == {
        interleaved: ["error", "error"],
        between: ["ok", "error"],
        boundary: [],
        fresh: ["ok", "error"],
    }
    assert body["phantom_rows"] == 4  # 2 interleaved, 1 between, 1 at the boundary
    assert sorted(c["latency_ms"] for c in calls if c["turn_id"] == fresh) == [1, 2]

    code = cli.main(["tool", "log", "tool/gone", "--project", str(project), "--json"])
    gone = json.loads(capsys.readouterr().out)
    assert code == 0 and gone["calls"] == [] and gone["phantom_rows"] == 1

    code, body, _ = _log(capsys, project, "--agent", "northwind-support")
    assert [c["latency_ms"] for c in body["calls"]] == [2, 1] and body["phantom_rows"] == 0
