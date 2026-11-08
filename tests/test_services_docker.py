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
