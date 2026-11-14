"""Opt-in real Docker run of `services up/down` with the built-in echo service (#738).

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_services_docker.py
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_services_engine import CANARY, PY, STACK, write_project

from agentbreeder import registry
from agentbreeder.services.engine import Engine
from agentbreeder.services.refs import Reference
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-500:]}")
    return result.stdout


@pytest.fixture
def projects(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", CANARY)
    first = write_project(tmp_path / "one")
    second = write_project(tmp_path / "two")
    engines = []

    def make(project: Path) -> Engine:
        instance = Engine(project)
        engines.append(instance)
        return instance

    yield first, second, make
    for instance in engines:
        try:
            instance.active_deployments = lambda: []
            instance.down(volumes=True)
        finally:
            instance.close()


def up(instance: Engine) -> dict:
    return instance.up("dev", confirm=lambda grant: True)


def test_echo_stack_lifecycle(projects):
    first, second, make = projects
    one = make(first)
    result = up(one)
    assert result["services"] == {"echo": "created", "gateway": "created"}
    assert up(one)["services"] == {"echo": "unchanged", "gateway": "unchanged"}

    # No secret in container config or labels.
    secrets = one.state.known_secrets()
    assert len(secrets) >= 4
    for name in ("echo", "gateway"):
        inspect = docker("inspect", f"{one.state.project}-{name}-1")
        for value in [*secrets, CANARY.encode()]:
            assert value.decode() not in inspect

    # The agents network reaches agents-exposed services; the default bridge stays for publish.
    agents = f"{one.state.project}-agents"
    probe = (
        "import urllib.request;"
        "print(urllib.request.urlopen('http://echo:8080/index.html', timeout=5).status)"
    )
    # Same order as runtime.create: create on the default bridge, connect, then start.
    agent = f"{one.state.project}-probe-agent"
    docker("create", "--name", agent, "-p", "127.0.0.1::8000", PY, "python3", "-c", probe)
    try:
        docker("network", "connect", agents, agent)
        assert docker("start", "--attach", agent).strip() == "200"
    finally:
        docker("rm", "--force", agent, check=False)

    # Host port on loopback, from the operator config's eval secret.
    status = one.status()
    port = status["services"]["echo"]["host_ports"]["http"]
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=5) as response:
        assert response.status == 200

    # down keeps data and secrets; up again recreates nothing new in the volume.
    minted = one.state.read_secret("echo", "minted")
    one.down()
    assert (
        docker("ps", "--all", "--quiet", "--filter", f"label=io.agentbreeder.stack={one.state.id}")
        == ""
    )
    assert up(one)["services"]["echo"] == "created"
    assert one.state.read_secret("echo", "minted") == minted  # same volume: no new bootstrap

    # A second checkout with the same stack name runs alongside.
    two = make(second)
    other = up(two)
    assert other["stack"] != result["stack"]
    assert two.status()["services"]["echo"]["state"] == "running"

    # down --volumes removes only this stack's volumes.
    removed = one.down(volumes=True)
    assert removed["volumes_removed"] == [f"{one.state.project}-echo-data"]
    volumes = json.loads(json.dumps(docker("volume", "ls", "--quiet").split()))
    assert f"{two.state.project}-echo-data" in volumes


PG = "pgvector/pgvector@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f"


def test_postgres_pgvector_roles_init_sql_and_hardening(tmp_path, monkeypatch):
    """The built-in postgres runs read-only, capability-free as 999; each database has its own
    role that can't reach the other; init_sql runs as the target database's role."""
    import psycopg

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    stack = json.loads(json.dumps(STACK))
    stack["services"] = {
        "postgres": {
            "ref": "service/postgres-pgvector@1.0.0",
            "with": {
                "databases": ["alpha", "beta"],
                "init_sql": "db/seed.sql",
                "init_sql_database": "alpha",
            },
        },
        "gateway": stack["services"]["gateway"],
    }
    stack["environment"]["services"] = {}
    stack["environment"]["bindings"] = {"DATABASE_URL": {"ref": "postgres.databases.alpha"}}
    stack["environment"]["eval_database"] = {"ref": "postgres.databases.alpha"}
    project = write_project(tmp_path / "pg", stack=stack)
    (project / "db").mkdir()
    (project / "db" / "seed.sql").write_text(
        "CREATE TABLE IF NOT EXISTS items (id int PRIMARY KEY, v vector(3));\n"
        "INSERT INTO items VALUES (1, '[1,2,3]') ON CONFLICT DO NOTHING;\n"
    )
    one = Engine(project)
    try:
        assert up(one)["services"]["postgres"] == "created"
        assert up(one)["services"]["postgres"] == "unchanged"  # init is idempotent

        container = f"{one.state.project}-postgres-1"
        config = json.loads(docker("inspect", container))[0]["HostConfig"]
        assert config["ReadonlyRootfs"] and config["CapDrop"] == ["ALL"]
        assert json.loads(docker("inspect", container))[0]["Config"]["User"] == "999:999"
        inspect = docker("inspect", container)
        for value in one.state.known_secrets():
            assert value.decode() not in inspect

        port = one.status()["services"]["postgres"]["host_ports"]["pg"]
        alpha = one.state.read_secret("postgres", "role.alpha").decode()
        dsn = f"postgresql://alpha:{alpha}@127.0.0.1:{port}"
        with psycopg.connect(f"{dsn}/alpha", connect_timeout=10) as conn:
            assert conn.execute("SELECT v::text FROM items WHERE id = 1").fetchone() == (
                "[1,2,3]",
            )
            owner = conn.execute(
                "SELECT tableowner FROM pg_tables WHERE tablename = 'items'"
            ).fetchone()
            assert owner == ("alpha",)  # applied as the database's role, not the superuser
            assert conn.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname = 'alpha'"
            ).fetchone() == (False,)
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(f"{dsn}/beta", connect_timeout=10).close()

        # A repo init_sql can't run psql meta-commands (the init step also holds the superuser):
        # a leading one is refused, any later one is a server syntax error; nothing executes.
        for sql in ("\\! touch /tmp/pwned\n", "SELECT 1;\n\\! touch /tmp/pwned\n"):
            (project / "db" / "seed.sql").write_text(sql)
            (project / "stack.lock").unlink()
            with pytest.raises(ServiceError, match="postgres.init exited"):
                up(one)
        (project / "db" / "seed.sql").write_text("SELECT 1;\n")
        (project / "stack.lock").unlink()
        up(one)

        one.down()
        assert up(one)["services"]["postgres"] == "created"
        with psycopg.connect(f"{dsn}/alpha", connect_timeout=10) as conn:
            assert conn.execute("SELECT count(*) FROM items").fetchone() == (1,)
    finally:
        try:
            one.active_deployments = lambda: []
            one.down(volumes=True)
        finally:
            one.close()


def test_postgres_pgvector_least_privilege_roles(tmp_path, monkeypatch):
    """1.1.0 `roles`: each extra role connects only to roles_database, creates only in its own
    schema, and gets nothing on the owner's tables until init_sql grants it (#750)."""
    import psycopg

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    stack = json.loads(json.dumps(STACK))
    stack["services"] = {
        "postgres": {
            "ref": "service/postgres-pgvector@1.1.0",
            "with": {
                "databases": ["alpha", "beta"],
                "init_sql": "db/seed.sql",
                "init_sql_database": "alpha",
                "roles": ["alpha_agent", "alpha_ops"],
                "roles_database": "alpha",
            },
        },
        "gateway": stack["services"]["gateway"],
    }
    stack["environment"]["services"] = {}
    stack["environment"]["bindings"] = {"DATABASE_URL": {"ref": "postgres.roles.alpha_agent"}}
    stack["environment"]["eval_database"] = {"ref": "postgres.databases.alpha"}
    project = write_project(tmp_path / "pg", stack=stack)
    (project / "db").mkdir()
    (project / "db" / "seed.sql").write_text(
        "CREATE TABLE IF NOT EXISTS requests (id int PRIMARY KEY, "
        "status text NOT NULL DEFAULT 'pending');\n"
        "INSERT INTO requests VALUES (1, 'pending') ON CONFLICT DO NOTHING;\n"
        "GRANT SELECT ON requests TO alpha_agent;\n"
        "GRANT INSERT (id) ON requests TO alpha_agent;\n"
        "GRANT SELECT, UPDATE (status) ON requests TO alpha_ops;\n"
        "CREATE SCHEMA IF NOT EXISTS sessions AUTHORIZATION alpha_agent;\n"
    )
    one = Engine(project)
    try:
        assert up(one)["services"]["postgres"] == "created"
        assert up(one)["services"]["postgres"] == "unchanged"  # init is idempotent
        port = one.status()["services"]["postgres"]["host_ports"]["pg"]
        plan = one.plan()
        one.secrets(plan, None)
        agent = one.state.read_secret("postgres", "login.alpha_agent").decode()
        ops = one.state.read_secret("postgres", "login.alpha_ops").decode()
        bound = plan.output(Reference("postgres", "roles", "alpha_agent"), "internal").decode()
        assert bound == f"postgresql://alpha_agent:{agent}@postgres:5432/alpha"
        agent_url = f"postgresql://alpha_agent:{agent}@127.0.0.1:{port}/alpha"
        ops_url = f"postgresql://alpha_ops:{ops}@127.0.0.1:{port}/alpha"

        with psycopg.connect(agent_url, connect_timeout=10, autocommit=True) as conn:
            assert conn.execute("SELECT status FROM requests").fetchone() == ("pending",)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE requests SET status = 'approved'")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO requests (id, status) VALUES (2, 'approved')")
            conn.execute("INSERT INTO requests (id) VALUES (2)")  # status defaults to pending
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("CREATE TABLE public.x (id int)")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("CREATE SCHEMA alpha_ops2")
            conn.execute("CREATE TABLE IF NOT EXISTS notes (id int)")
            assert conn.execute(
                "SELECT schemaname FROM pg_tables WHERE tablename = 'notes'"
            ).fetchone() == ("alpha_agent",)
            conn.execute("CREATE TABLE IF NOT EXISTS sessions.events (id int)")
            assert conn.execute(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles "
                "WHERE rolname = 'alpha_agent'"
            ).fetchone() == (False,)
        base = agent_url.rsplit("/", 1)[0]
        for other in ("beta", "postgres", "template1"):
            with pytest.raises(psycopg.OperationalError):
                psycopg.connect(f"{base}/{other}", connect_timeout=10).close()
        with psycopg.connect(ops_url, connect_timeout=10, autocommit=True) as conn:
            conn.execute("UPDATE requests SET status = 'approved' WHERE id = 1")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM alpha_agent.notes")

        # A role named like a database is refused.
        stack["services"]["postgres"]["with"]["roles"] = ["alpha_agent", "beta"]
        write_project(project, stack=stack)
        one.close()
        one = Engine(project)  # an engine reads stack.yaml once
        with pytest.raises(ServiceError, match="postgres.init exited"):
            up(one)
    finally:
        try:
            one.active_deployments = lambda: []
            one.down(volumes=True)
        finally:
            one.close()


CONSOLE_SEED = """
CREATE TABLE IF NOT EXISTS turns (id text PRIMARY KEY, tools jsonb NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS audit_events (
  id BIGSERIAL PRIMARY KEY, actor text NOT NULL, action text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS customers (id text PRIMARY KEY);
INSERT INTO turns VALUES ('t1') ON CONFLICT DO NOTHING;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS db_role TEXT;
CREATE OR REPLACE FUNCTION audit_events_db_role() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog AS $$
BEGIN NEW.db_role := session_user; RETURN NEW; END $$;
CREATE OR REPLACE TRIGGER audit_events_set_db_role BEFORE INSERT ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_db_role();
GRANT INSERT ON audit_events TO alpha_agent;
GRANT USAGE ON SEQUENCE audit_events_id_seq TO alpha_agent;
DO $$ BEGIN
  IF to_regrole('agentbreeder_console') IS NOT NULL THEN
    GRANT SELECT, DELETE ON turns TO agentbreeder_console;
    GRANT SELECT ON audit_events TO agentbreeder_console;
    GRANT INSERT (actor, action, detail) ON audit_events TO agentbreeder_console;
    GRANT USAGE ON SEQUENCE audit_events_id_seq TO agentbreeder_console;
  END IF;
END $$;
"""


def test_postgres_pgvector_console_role(tmp_path, monkeypatch):
    """1.2.0 `console_database`: `agentbreeder_console` connects only there, holds nothing but
    what the owner's init_sql grants, `db_role` records the login, the name is reserved, and
    unsetting the param stops the credential working (M12 delta §3.2–3.4)."""
    import psycopg

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    stack = json.loads(json.dumps(STACK))
    with_ = {
        "databases": ["alpha", "beta"],
        "init_sql": "db/seed.sql",
        "init_sql_database": "alpha",
        "roles": ["alpha_agent"],
        "roles_database": "alpha",
        "console_database": "alpha",
    }
    stack["services"] = {
        "postgres": {"ref": "service/postgres-pgvector@1.2.0", "with": with_},
        "gateway": stack["services"]["gateway"],
    }
    stack["environment"]["services"] = {}
    stack["environment"]["bindings"] = {"DATABASE_URL": {"ref": "postgres.roles.alpha_agent"}}
    stack["environment"]["eval_database"] = {"ref": "postgres.databases.alpha"}
    project = write_project(tmp_path / "pg", stack=stack)
    (project / "db").mkdir()
    (project / "db" / "seed.sql").write_text(CONSOLE_SEED)
    one = Engine(project)
    try:
        assert up(one)["services"]["postgres"] == "created"
        assert up(one)["services"]["postgres"] == "unchanged"  # init is idempotent
        port = one.status()["services"]["postgres"]["host_ports"]["pg"]
        plan = one.plan()
        one.secrets(plan, None)
        secret = one.state.read_secret("postgres", "console").decode()
        console_url = plan.output(Reference("postgres", "console"), "host").decode()
        assert console_url == f"postgresql://agentbreeder_console:{secret}@127.0.0.1:{port}/alpha"
        agent = one.state.read_secret("postgres", "login.alpha_agent").decode()
        agent_url = f"postgresql://alpha_agent:{agent}@127.0.0.1:{port}/alpha"

        with psycopg.connect(console_url, connect_timeout=10, autocommit=True) as conn:
            assert conn.execute("SELECT id FROM public.turns").fetchall() == [("t1",)]
            conn.execute(
                "INSERT INTO public.audit_events (actor, action, detail) "
                "VALUES ('console', 'note', '{}')"
            )
            for denied in (
                "SELECT * FROM public.customers",
                "UPDATE public.turns SET tools = '[]'",
                "INSERT INTO public.audit_events (actor, action, db_role) VALUES ('a', 'b', 'x')",
                "CREATE TABLE public.x (id int)",
                "CREATE TEMP TABLE x (id int)",
                "CREATE SCHEMA console_own",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(denied)
            row = conn.execute(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication "
                "OR rolbypassrls, rolconnlimit FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            assert row == (False, 10)
            assert conn.execute(
                "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r "
                "ON r.oid IN (m.member, m.roleid) WHERE r.rolname = 'agentbreeder_console'"
            ).fetchone() == (0,)
        base = console_url.rsplit("/", 1)[0]
        for other in ("beta", "postgres", "template1"):
            with pytest.raises(psycopg.OperationalError):
                psycopg.connect(f"{base}/{other}", connect_timeout=10).close()
        with psycopg.connect(agent_url, connect_timeout=10, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO public.audit_events (actor, action, db_role) "
                "VALUES ('runtime', 'tool:x', 'agentbreeder_console')"  # forged: overridden
            )
        owner = one.state.read_secret("postgres", "role.alpha").decode()
        with psycopg.connect(
            f"postgresql://alpha:{owner}@127.0.0.1:{port}/alpha", connect_timeout=10
        ) as conn:
            assert conn.execute(
                "SELECT actor, db_role FROM audit_events ORDER BY id"
            ).fetchall() == [("console", "agentbreeder_console"), ("runtime", "alpha_agent")]

        # The reserved name can't be a `roles` (or `databases`) item.
        stack["services"]["postgres"]["with"] = {
            **with_,
            "roles": ["alpha_agent", "agentbreeder_console"],
        }
        write_project(project, stack=stack)
        one.close()
        one = Engine(project)  # an engine reads stack.yaml once
        with pytest.raises(ServiceError, match="postgres.init exited"):
            up(one)

        # Unset: the role can't log in any more; init_sql's guarded grants still apply cleanly.
        stack["services"]["postgres"]["with"] = {
            k: v for k, v in with_.items() if k != "console_database"
        }
        write_project(project, stack=stack)
        one.close()
        one = Engine(project)
        up(one)
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(console_url, connect_timeout=10).close()
    finally:
        try:
            one.active_deployments = lambda: []
            one.down(volumes=True)
        finally:
            one.close()


def test_northwind_commerce_sql_on_console_role(tmp_path, monkeypatch):
    """Northwind's real `commerce.sql` applies on 1.2.0 twice, keeps the agent's grants, gives
    the console role exactly its table and stamps `db_role` (M12 delta §3.3–3.4)."""
    import psycopg

    northwind = Path(registry.__file__).parent / "examples" / "northwind"
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    stack = json.loads(json.dumps(STACK))
    stack["services"] = {
        "postgres": {
            "ref": "service/postgres-pgvector@1.2.0",
            "with": {
                "databases": ["support"],
                "init_sql": "db/commerce.sql",
                "init_sql_database": "support",
                "roles": ["support_agent", "support_ops"],
                "roles_database": "support",
                "console_database": "support",
            },
        },
        "gateway": stack["services"]["gateway"],
    }
    stack["environment"]["services"] = {}
    stack["environment"]["bindings"] = {"DATABASE_URL": {"ref": "postgres.roles.support_agent"}}
    stack["environment"]["eval_database"] = {"ref": "postgres.databases.support"}
    project = write_project(tmp_path / "nw", stack=stack)
    (project / "db").mkdir()
    (project / "db" / "commerce.sql").write_bytes(
        (northwind / "stack" / "db" / "commerce.sql").read_bytes()
    )
    one = Engine(project)
    try:
        assert up(one)["services"]["postgres"] == "created"
        assert up(one)["services"]["postgres"] == "unchanged"
        port = one.status()["services"]["postgres"]["host_ports"]["pg"]
        plan = one.plan()
        one.secrets(plan, None)
        console_url = plan.output(Reference("postgres", "console"), "host").decode()
        agent = one.state.read_secret("postgres", "login.support_agent").decode()
        agent_url = f"postgresql://support_agent:{agent}@127.0.0.1:{port}/support"
        with psycopg.connect(agent_url, connect_timeout=10, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO turns (id, session, customer, message, reply, latency_ms, "
                "model_calls, tools, sources, evidence, retrieval, prompt_tokens, output_tokens) "
                "VALUES ('t1', 's', 'cust-001', 'm', 'r', 1, 1, '[]', '[]', '[]', 'none', 1, 1)"
            )
            conn.execute(
                "INSERT INTO audit_events (turn_id, actor, action, detail) "
                "VALUES ('t1', 'agent', 'tool:order_status', '{}')"
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM turns")
        with psycopg.connect(console_url, connect_timeout=10, autocommit=True) as conn:
            assert conn.execute("SELECT action, db_role FROM public.audit_events").fetchall() == [
                ("tool:order_status", "support_agent")
            ]
            assert conn.execute("SELECT id FROM public.turns").fetchall() == [("t1",)]
            conn.execute("INSERT INTO public.notes (customer, note) VALUES ('cust-001', 'n')")
            for denied in (
                "SELECT * FROM public.customers",
                "SELECT * FROM public.orders",
                "UPDATE public.audit_events SET actor = 'x'",
                "SELECT * FROM adk_sessions.anything",
            ):
                with pytest.raises(
                    (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable)
                ):
                    conn.execute(denied)
    finally:
        try:
            one.active_deployments = lambda: []
            one.down(volumes=True)
        finally:
            one.close()


def _without_console(sql: str) -> str:
    """Northwind's commerce.sql as it was before 1.2.0: no db_role column and no console grants."""
    start = sql.index("-- Which database login inserted")
    end = sql.index("FUNCTION public.audit_events_db_role();\n") + len(
        "FUNCTION public.audit_events_db_role();\n"
    )
    console = sql.index("-- AgentBreeder's console role")
    return sql[:start] + sql[end:console]


def test_postgres_pgvector_console_upgrade_and_fail_closed(tmp_path, monkeypatch):
    """1.1.0 → 1.2.0 on a populated audit_events keeps old rows' db_role NULL; unsetting
    console_database ends the role's open sessions; a database named agentbreeder_console, a
    console role with a membership, and an undeclared console_database each stop `init`."""
    import psycopg

    real = (
        Path(registry.__file__).parent / "examples" / "northwind" / "stack" / "db" / "commerce.sql"
    ).read_text()
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    base_with = {
        "databases": ["support"],
        "init_sql": "db/commerce.sql",
        "init_sql_database": "support",
        "roles": ["support_agent", "support_ops"],
        "roles_database": "support",
    }
    stack = json.loads(json.dumps(STACK))
    gateway = stack["services"]["gateway"]
    stack["environment"]["services"] = {}
    stack["environment"]["bindings"] = {"DATABASE_URL": {"ref": "postgres.roles.support_agent"}}
    stack["environment"]["eval_database"] = {"ref": "postgres.databases.support"}
    project = tmp_path / "up"

    def configure(ref: str, sql: str, **extra) -> Engine:
        stack["services"] = {
            "postgres": {"ref": ref, "with": {**base_with, **extra}},
            "gateway": gateway,
        }
        write_project(project, stack=stack)
        (project / "db").mkdir(exist_ok=True)
        (project / "db" / "commerce.sql").write_text(sql)
        return Engine(project)

    def bring_up(instance: Engine) -> dict:
        return instance.up("dev", mode="update", confirm=lambda grant: True)

    one = configure("service/postgres-pgvector@1.1.0", _without_console(real))
    try:
        bring_up(one)
        port = one.status()["services"]["postgres"]["host_ports"]["pg"]
        host = f"127.0.0.1:{port}"
        agent = one.state.read_secret("postgres", "login.support_agent").decode()
        owner = one.state.read_secret("postgres", "role.support").decode()
        superuser = one.state.read_secret("postgres", "superuser").decode()
        agent_url = f"postgresql://support_agent:{agent}@{host}/support"
        owner_url = f"postgresql://support:{owner}@{host}/support"
        super_url = f"postgresql://postgres:{superuser}@{host}/postgres"
        audit = "INSERT INTO audit_events (actor, action) VALUES (%s, 'tool:x')"
        with psycopg.connect(agent_url, connect_timeout=10, autocommit=True) as conn:
            conn.execute(audit, ("before",))

        one.close()
        one = configure("service/postgres-pgvector@1.2.0", real, console_database="support")
        bring_up(one)
        bring_up(one)  # init and the migration run twice cleanly
        with psycopg.connect(agent_url, connect_timeout=10, autocommit=True) as conn:
            conn.execute(audit, ("after",))
        with psycopg.connect(owner_url, connect_timeout=10) as conn:
            assert conn.execute(
                "SELECT actor, db_role FROM audit_events ORDER BY id"
            ).fetchall() == [("before", None), ("after", "support_agent")]

        plan = one.plan()
        one.secrets(plan, None)
        console_url = plan.output(Reference("postgres", "console"), "host").decode()
        held = psycopg.connect(console_url, connect_timeout=10, autocommit=True)
        try:
            assert held.execute("SELECT count(*) FROM public.turns").fetchone() == (0,)
            one.close()
            one = configure("service/postgres-pgvector@1.2.0", real)  # console_database unset
            bring_up(one)
            with pytest.raises(psycopg.OperationalError):
                held.execute("SELECT 1")  # its session was ended
        finally:
            held.close()
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(console_url, connect_timeout=10).close()

        # Fail closed: an undeclared console_database, a database of the reserved name, and a
        # console role with a membership (not made by init) each stop init.
        one.close()
        one = configure("service/postgres-pgvector@1.2.0", real, console_database="other")
        with pytest.raises(ServiceError, match="postgres.init exited"):
            bring_up(one)
        for setup, teardown in (
            ("CREATE DATABASE agentbreeder_console", "DROP DATABASE agentbreeder_console"),
            (
                "GRANT support_agent TO agentbreeder_console",
                "REVOKE support_agent FROM agentbreeder_console",
            ),
        ):
            with psycopg.connect(super_url, connect_timeout=10, autocommit=True) as conn:
                conn.execute(setup)
            one.close()
            one = configure("service/postgres-pgvector@1.2.0", real, console_database="support")
            with pytest.raises(ServiceError, match="postgres.init exited"):
                bring_up(one)
            with psycopg.connect(super_url, connect_timeout=10, autocommit=True) as conn:
                conn.execute(teardown)
        one.close()
        one = configure("service/postgres-pgvector@1.2.0", real, console_database="support")
        bring_up(one)  # back to a clean state: the console credential works again
        psycopg.connect(console_url, connect_timeout=10).close()
    finally:
        try:
            one.active_deployments = lambda: []
            one.down(volumes=True)
        finally:
            one.close()


def test_litellm_gateway_on_postgres(tmp_path, monkeypatch):
    """The built-in litellm runs read-only as 65532 on its own postgres role, answers health, and
    takes only the generated `sk-` master key, which never enters the container config."""
    import urllib.error
    import urllib.request

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    stack = json.loads(json.dumps(STACK))
    stack["services"] = {
        "postgres": {"ref": "service/postgres-pgvector@1.0.0", "with": {"databases": ["litellm"]}},
        "gateway": {
            "ref": "service/litellm@1.0.0",
            "with": {"config": "gw/config.yaml"},
            "wire": {"database-url": "postgres.databases.litellm"},
        },
    }
    stack["environment"]["services"] = {}
    stack["environment"]["bindings"] = {}
    stack["environment"].pop("eval_database")
    project = write_project(tmp_path / "gw", stack=stack)
    one = Engine(project)
    try:
        assert up(one)["services"]["gateway"] == "created"
        container = f"{one.state.project}-gateway-1"
        inspect = json.loads(docker("inspect", container))[0]
        assert (
            inspect["HostConfig"]["ReadonlyRootfs"] and inspect["Config"]["User"] == "65532:65532"
        )
        assert inspect["Config"]["Entrypoint"] == ["/files/start.py"]
        raw = docker("inspect", container)
        for value in one.state.known_secrets():
            assert value.decode() not in raw

        master = (one.state.directory / "service-secrets" / "gateway" / "master-key").read_text()
        assert master.startswith("sk-")
        port = one.status()["services"]["gateway"]["host_ports"]["http"]
        base = f"http://127.0.0.1:{port}"

        def generate(token):
            request = urllib.request.Request(
                f"{base}/key/generate",
                data=json.dumps({"models": ["m1"], "duration": "5m"}).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())

        assert generate(master)["key"].startswith("sk-")
        with pytest.raises(urllib.error.HTTPError) as denied:
            generate("sk-" + "0" * 48)
        assert denied.value.code in (400, 401, 403)
    finally:
        try:
            one.active_deployments = lambda: []
            one.down(volumes=True)
        finally:
            one.close()


def test_phoenix_on_postgres_mints_an_ingest_key(tmp_path, monkeypatch):
    """The built-in phoenix runs read-only as 65532 with auth on and its data in its own postgres
    role; bootstrap mints a system key that Phoenix accepts, and mints again for a new volume."""
    import urllib.error
    import urllib.request

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    stack = json.loads(json.dumps(STACK))
    stack["services"] = {
        "postgres": {"ref": "service/postgres-pgvector@1.0.0", "with": {"databases": ["phoenix"]}},
        "phoenix": {
            "ref": "service/phoenix@1.0.0",
            "wire": {"database-url": "postgres.databases.phoenix"},
        },
        "gateway": STACK["services"]["gateway"],
    }
    stack["environment"]["services"] = {}  # agents reach Phoenix only through a proxy
    stack["environment"]["bindings"] = {}
    stack["environment"].pop("eval_database")
    project = write_project(tmp_path / "px", stack=stack)
    one = Engine(project)

    def projects_status(base, token):
        request = urllib.request.Request(
            f"{base}/v1/projects", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    try:
        assert up(one)["services"]["phoenix"] == "created"
        container = f"{one.state.project}-phoenix-1"
        inspect = json.loads(docker("inspect", container))[0]
        assert (
            inspect["HostConfig"]["ReadonlyRootfs"] and inspect["Config"]["User"] == "65532:65532"
        )
        assert inspect["Config"]["Entrypoint"] == ["/files/start.py"]
        raw = docker("inspect", container)
        for value in one.state.known_secrets():
            assert value.decode() not in raw

        secrets = one.state.directory / "service-secrets" / "phoenix"
        assert (secrets / "secret").read_text().startswith("s1-")
        key = (secrets / "ingest-key").read_text().strip()
        assert key.count(".") == 2
        port = one.status()["services"]["phoenix"]["host_ports"]["http"]
        base = f"http://127.0.0.1:{port}"
        assert projects_status(base, key) == 200
        assert projects_status(base, "x.y.z") == 401

        assert up(one)["services"]["phoenix"] == "unchanged"  # same volume: same key
        assert (secrets / "ingest-key").read_text().strip() == key

        one.active_deployments = lambda: []
        one.down(volumes=True)
        up(one)
        fresh = (secrets / "ingest-key").read_text().strip()
        port = one.status()["services"]["phoenix"]["host_ports"]["http"]
        base = f"http://127.0.0.1:{port}"
        assert fresh != key
        assert projects_status(base, fresh) == 200
        assert projects_status(base, key) == 401  # the old database, and its key, are gone
    finally:
        try:
            one.active_deployments = lambda: []
            one.down(volumes=True)
        finally:
            one.close()
