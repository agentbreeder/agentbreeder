"""`agentbreeder conformance` (#759): finding what was built, the Docker environment (against a
fake Docker runner that starts the fakes and a stub agent in process), the report and the
command. No test here runs Docker."""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import stat
import tarfile
from pathlib import Path

import pytest
import yaml
from conformance_support import StubAgent

from agentbreeder import cli
from agentbreeder.conformance import checks, environment, fakes, report
from agentbreeder.egress import boundary
from agentbreeder.registry import BUILTIN_ROOT
from agentbreeder.store import DeploymentError

IMAGE = "sha256:" + "c" * 64
RUNTIME = "runtime/adk@1.3.0"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Reports and run directories go under a private home, never the developer's."""
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    return directory


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """Short waits: the stub posts its spans at once and answers in milliseconds."""
    original = checks.Context.__init__

    def init(self, *args, **kwargs):
        for name in ("span_wait", "span_settle", "busy_delay"):
            kwargs.setdefault(name, 0.6 if name != "span_settle" else 0.1)
        original(self, *args, **kwargs)

    monkeypatch.setattr(checks.Context, "__init__", init)


def project(tmp_path: Path, runtime=RUNTIME, image=IMAGE, manifest=None, name="stub") -> Path:
    directory = tmp_path / "project"
    (directory / "deploy").mkdir(parents=True, exist_ok=True)
    record = {"name": name, "environment": "dev", "image": image, "runtime": runtime}
    (directory / "deploy" / f"{name}.build.json").write_text(json.dumps(record))
    data = manifest or {"schema_version": 1, "name": name, "image": image}
    (directory / "deploy" / f"{name}.deploy.yaml").write_text(yaml.safe_dump(data))
    return directory


# -- finding what was built ------------------------------------------------------------------


def test_the_subject_comes_from_the_build_record(tmp_path):
    found = environment.subject(IMAGE, project(tmp_path))
    assert (found.agent, found.runtime, found.profile) == ("stub", RUNTIME, "v1")
    assert found.framework == "google-adk"  # LEGACY_ADK: a manifest without `protocol`
    assert found.manifest.port == 8080 and found.manifest.health_path == "/health"


def test_traces_off_comes_from_the_agent_yaml_the_build_hashed(tmp_path):
    """`traces: none` in the project's agent.yaml, while it is the file the build hashed, makes
    the span checks `not_run`; a changed, missing or unparsable file, or a record without the
    hash, leaves every check on."""
    directory = project(tmp_path)
    record_path = directory / "deploy" / "stub.build.json"
    agent = directory / "agent.yaml"

    def built(text: bytes, record_hash: bool = True) -> bool:
        agent.write_bytes(text)
        record = json.loads(record_path.read_text())
        record.pop("agent_yaml_sha256", None)
        if record_hash:
            record["agent_yaml_sha256"] = "sha256:" + hashlib.sha256(text).hexdigest()
        record_path.write_text(json.dumps(record))
        return environment.subject(IMAGE, directory).traces_off

    assert built(b"kind: agent\nname: stub\ntraces: none\n") is True
    assert built(b"kind: agent\nname: stub\ntraces: otlp\n") is False
    assert built(b"kind: agent\nname: stub\n") is False  # the default is otlp
    assert built(b"kind: agent\ntraces: none\n", record_hash=False) is False
    assert built(b"traces: [none\n") is False  # not YAML
    assert built(b"- traces\n") is False  # not a mapping
    built(b"kind: agent\ntraces: none\n")
    agent.write_bytes(b"kind: agent\ntraces: none\n# edited after the build\n")
    assert environment.subject(IMAGE, directory).traces_off is False
    agent.unlink()
    assert environment.subject(IMAGE, directory).traces_off is False
    agent.symlink_to(record_path)  # a link is not read
    assert environment.subject(IMAGE, directory).traces_off is False


def refusal(image, directory) -> environment.ConformanceError:
    with pytest.raises(environment.ConformanceError) as raised:
        environment.subject(image, directory)
    return raised.value


def test_an_image_that_is_not_an_id_is_refused(tmp_path):
    for image in ("northwind:latest", "sha256:abc", "sha256:" + "C" * 64, ""):
        assert refusal(image, tmp_path).code == "CONFORMANCE_IMAGE"


def test_an_image_no_build_record_names_is_refused(tmp_path):
    directory = project(tmp_path)
    other = "sha256:" + "d" * 64
    error = refusal(other, directory)
    assert error.code == "CONFORMANCE_NO_BUILD_RECORD" and "agentbreeder build" in str(error)
    assert refusal(IMAGE, tmp_path / "empty").code == "CONFORMANCE_NO_BUILD_RECORD"
    # A record under another agent's file name, a link and a damaged file are not used.
    deploy = directory / "deploy"
    (deploy / "other.build.json").write_text(json.dumps({"name": "stub", "image": other}))
    (deploy / "bad.build.json").write_text("{not json")
    (deploy / "list.build.json").write_text("[]")
    (deploy / "link.build.json").symlink_to(deploy / "stub.build.json")
    assert refusal(other, directory).code == "CONFORMANCE_NO_BUILD_RECORD"
    assert environment.subject(IMAGE, directory).agent == "stub"


@pytest.mark.parametrize(
    ("runtime", "code"),
    [
        ("runtime/adk@9.9.9", "REF_NOT_FOUND"),
        ("tool/adk@1.0.0", "REF_INVALID"),
        ("runtime/adk", "REF_UNPINNED"),
        (None, "REF_INVALID"),
    ],
)
def test_a_build_record_without_a_usable_runtime_is_refused(tmp_path, runtime, code):
    assert refusal(IMAGE, project(tmp_path, runtime=runtime)).code == code


def test_a_manifest_for_another_image_is_refused(tmp_path):
    other = {"schema_version": 1, "name": "stub", "image": "sha256:" + "d" * 64}
    assert refusal(IMAGE, project(tmp_path, manifest=other)).code == "CONFORMANCE_MANIFEST"
    directory = project(tmp_path)
    (directory / "deploy" / "stub.deploy.yaml").unlink()
    assert refusal(IMAGE, directory).code == "CONFORMANCE_MANIFEST"


def test_a_runtime_on_another_protocol_is_refused(tmp_path, monkeypatch):
    from agentbreeder import runtime_profile

    legacy = runtime_profile.load(BUILTIN_ROOT / "runtime" / "adk" / "1.3.0")
    future = type(legacy)(**{**legacy.__dict__, "protocol": 2})
    monkeypatch.setattr(runtime_profile, "load", lambda path: future)
    error = refusal(IMAGE, project(tmp_path))
    assert error.code == "PROTOCOL_UNSUPPORTED" and "protocol 2" in str(error)


# -- the fakes image -------------------------------------------------------------------------


def test_the_fakes_image_is_the_standard_library_fakes_on_the_pinned_base():
    context, digest = environment.fakes_context()
    assert environment.fakes_context() == (context, digest)
    with tarfile.open(fileobj=io.BytesIO(context)) as archive:
        names = sorted(archive.getnames())
        dockerfile = archive.extractfile("Dockerfile").read().decode()
        shipped = archive.extractfile("agentbreeder/conformance/fakes.py").read()
        package = archive.extractfile("agentbreeder/conformance/__init__.py").read().decode()
    assert names == [
        "Dockerfile",
        "agentbreeder/__init__.py",
        "agentbreeder/conformance/__init__.py",
        "agentbreeder/conformance/fakes.py",
    ]
    assert dockerfile.startswith(f"FROM {boundary.SIDECAR_BASE}\n")
    assert "USER 65532:65532" in dockerfile and digest in dockerfile
    assert shipped == Path(fakes.__file__).read_bytes()
    # The container has only these three files: they may import nothing else from the package.
    assert "import" not in package.split('"""')[2]
    assert "agentbreeder" not in shipped.decode().split('"""', 2)[2].replace(
        "agentbreeder.conformance.fakes", ""
    ).replace("AGENTBREEDER_", "")


def test_the_test_database_is_the_built_in_one():
    service = BUILTIN_ROOT / "service" / "postgres-pgvector" / "1.2.0" / "service.yaml"
    assert yaml.safe_load(service.read_text())["image"] == environment.POSTGRES_IMAGE


# -- the environment, on a fake Docker ---------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeDocker:
    """Records every call. Starting the fakes container starts the real `Harness` in process;
    creating the agent starts a stub agent with the key from its mounted secret file."""

    def __init__(self, flaws=(), fail=None, agent=True, engine="Docker Desktop", version="29.1.0"):
        self.engine, self.version = engine, version
        self.inspections = 0
        self.calls: list[tuple] = []
        self.envs: list[dict] = []
        self.flaws, self.fail, self.agent = frozenset(flaws), fail, agent
        self.harness = self.stub = None
        self.agent_port = free_port()
        self.created = None
        self.database_tries = 0
        self.removed: list[str] = []
        self.secret_modes: dict[str, int] = {}

    def authorize(self, context=None):
        self.calls.append(("authorize",))

    def command(self, *arguments, timeout=30, stdin=None, env=None):
        self.calls.append(arguments)
        self.envs.append(env or {})
        if self.fail and arguments[: len(self.fail)] == self.fail:
            raise DeploymentError("Docker rejected the operation")
        if arguments[0] == "info":
            return self.engine
        if arguments[0] == "version":
            return self.version
        if arguments[:2] == ("image", "inspect"):
            raise DeploymentError("no such image")
        if arguments[:2] == ("container", "exec"):
            self.database_tries += 1
            if self.database_tries < 2:
                raise DeploymentError("not ready")
        if arguments[:2] == ("container", "create") and arguments[3].endswith("-fakes"):
            self.token = env[fakes.TOKEN_ENV]
        if arguments[:2] == ("container", "start") and arguments[2].endswith("-fakes"):
            self.harness = fakes.Harness(self.token, agent=("127.0.0.1", self.agent_port))
        if arguments[:2] == ("container", "inspect"):
            self.inspections += 1
            if "slow_ports" in self.flaws and self.inspections == 1:
                return json.dumps([{"NetworkSettings": {"Ports": {"9000/tcp": None}}}])
            ports = {
                "9000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.harness.control_port)}],
                "9001/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.harness.ingress_port)}],
            }
            if "bad_ports" in self.flaws:
                ports["9001/tcp"][0]["HostIp"] = "0.0.0.0"
            return json.dumps([{"NetworkSettings": {"Ports": ports}}])
        if arguments[:2] in (("container", "rm"), ("network", "rm")):
            self.removed.append(arguments[-1])
        return ""

    def create(self, record, manifest, *, network=None, environment=None, mounts=(), labels=None):
        self.calls.append(("create", record["container"]))
        self.created = {
            "labels": labels,
            "record": record,
            "manifest": manifest,
            "network": network,
            "environment": environment,
            "mounts": list(mounts),
        }
        for mount in mounts:
            self.secret_modes[mount.name] = stat.S_IMODE(os.stat(mount.source).st_mode)
        self.directory_mode = stat.S_IMODE(os.stat(mounts[0].source.parent).st_mode)
        key = next(m for m in mounts if m.name == "llm-gateway-key").source.read_text()
        if self.agent:
            self.stub = StubAgent(
                f"http://127.0.0.1:{self.harness.gateway_port}",
                key,
                flaws=self.flaws,
                deployment=environment["AGENTBREEDER_DEPLOYMENT_ID"],
                runtime=RUNTIME,
                framework="google-adk",
                port=self.agent_port,
            )

    def inspect(self, record):
        self.calls.append(("inspect", record["container"]))
        return None if "no_container" in self.flaws else {"Id": "a" * 64}

    def start(self, info):
        self.calls.append(("start", info["Id"]))

    def logs(self, info, tail=500, since=None):
        self.calls.append(("logs", tail))
        if "no_logs" in self.flaws:
            raise DeploymentError("Docker could not read the container's logs")
        return self.stub.logs(), "long_logs" in self.flaws

    def close(self):
        for part in (self.stub, self.harness):
            if part is not None:
                part.close()


@pytest.fixture
def docker():
    made = []

    def make(**options):
        made.append(FakeDocker(**options))
        return made[-1]

    yield make
    for one in made:
        one.close()


def test_a_run_builds_the_environment_runs_the_checks_and_removes_everything(
    tmp_path, docker, home
):
    fake = docker()
    result = environment.run(IMAGE, project(tmp_path), fake)
    failed = [row for row in result["checks"] if row["status"] == checks.FAIL]
    assert failed == []
    assert result["counts"]["pass"] == sum(1 for c in checks.CHECKS if c.run)
    assert result["result"] == "incomplete" and result["profile"] == "v1"
    assert (result["runtime"], result["agent"], result["image"]) == (RUNTIME, "stub", IMAGE)

    name = fake.created["record"]["container"]
    assert name.startswith("agentbreeder-conformance-")
    commands = [
        call for call in fake.calls if call and call[0] in ("network", "container", "build")
    ]
    # Two networks, the agent's one internal; the database and fakes on it; teardown in reverse.
    assert (
        commands[0][:3] == ("network", "create", "--internal") and commands[0][-1] == name + "-int"
    )
    assert commands[1][:2] == ("network", "create") and "--internal" not in commands[1]
    database = next(
        c for c in commands if c[:2] == ("container", "create") and c[3] == name + "-db"
    )
    assert database[-1] == environment.POSTGRES_IMAGE
    assert ("--network", name + "-int") == database[database.index("--network") :][:2]
    assert "--publish" not in database
    harness = next(
        c for c in commands if c[:2] == ("container", "create") and c[3] == name + "-fakes"
    )
    published = [harness[i + 1] for i, part in enumerate(harness) if part == "--publish"]
    assert published == ["127.0.0.1::9000", "127.0.0.1::9001"]
    for flag in ("--read-only", "no-new-privileges", "65532:65532"):
        assert flag in harness
    assert ("network", "connect", "--alias", "gateway", name + "-int", name + "-fakes") in commands
    assert any(c[:2] == ("build", "--quiet") for c in commands)
    assert fake.removed == [name, name + "-fakes", name + "-db", name + "-pub", name + "-int"]

    # The agent is created by the deploy function, on the internal network only.
    created = fake.created
    assert created["network"] == name + "-int" and created["manifest"].image == IMAGE
    assert created["environment"] == {
        "AGENTBREEDER_GATEWAY_URL": "http://gateway:4000",
        "AGENTBREEDER_SERVICE_TRACES_URL": "http://gateway:4000/v1/traces",
        "AGENTBREEDER_DEPLOYMENT_ID": created["record"]["id"],
    }
    mounts = {mount.name: mount for mount in created["mounts"]}
    assert set(mounts) == {"database-url", "llm-gateway-key"}
    assert mounts["database-url"].envs == ("DATABASE_URL",)
    assert mounts["llm-gateway-key"].target == "/run/secrets/llm-gateway-key"
    assert fake.secret_modes == {"database-url": 0o444, "llm-gateway-key": 0o444}
    assert fake.directory_mode == 0o700

    # No secret is on a command line; the run directory is gone.
    flat = json.dumps(fake.calls)
    key = fake.stub.key
    assert key.startswith("sk-conf-") and key not in flat and fake.token not in flat
    assert any(fakes.TOKEN_ENV in env for env in fake.envs)
    assert any("POSTGRES_PASSWORD" in env for env in fake.envs)
    assert not any((home / ".agentbreeder" / "conformance" / ".runs").iterdir())
    assert ("logs", 10000) in fake.calls


def test_a_flawed_agent_fails_its_check_through_the_environment(tmp_path, docker):
    result = environment.run(IMAGE, project(tmp_path), docker(flaws=["leak_key", "log_key"]))
    failed = {row["id"] for row in result["checks"] if row["status"] == checks.FAIL}
    # (`reply.fields` fails too: the leaked key makes the reply differ from the model's text.)
    assert failed == {"hygiene.replies", "hygiene.logs", "reply.fields"}
    assert result["result"] == "fail"
    assert "the gateway key" in next(
        row["detail"] for row in result["checks"] if row["id"] == "hygiene.logs"
    )


def test_an_agent_with_mcp_servers_gets_a_relay_address_and_token(tmp_path, docker):
    from conftest import v2_manifest_data

    manifest = v2_manifest_data(
        name="stub",
        image=IMAGE,
        model_access={"gateway": "local", "aliases": ["support-model"]},
        mcp_access=[{"ref": "mcp/git-hub@1.0.0", "tools": ["list_issues"]}],
    )
    fake = docker()
    target = environment.subject(IMAGE, project(tmp_path, manifest=manifest))
    with environment.Environment(target, fake) as context:
        created = fake.created
        assert created["environment"]["AGENTBREEDER_MCP_GIT_HUB_URL"] == (
            "http://gateway:4000/mcp/git-hub"
        )
        mount = next(m for m in created["mounts"] if m.name == "mcp-git-hub-token")
        assert mount.envs == ("AGENTBREEDER_MCP_GIT_HUB_TOKEN",)
        token = mount.source.read_text()
        assert len(token) == 64 and context.secrets["the relay token for git-hub"] == token


@pytest.mark.parametrize(
    "fail",
    [("network", "create"), ("build",), ("container", "create"), ("container", "start")],
)
def test_a_docker_failure_is_a_coded_refusal_and_leaves_nothing(tmp_path, docker, fail, home):
    fake = docker(fail=fail)
    with pytest.raises(environment.ConformanceError) as raised:
        environment.run(IMAGE, project(tmp_path), fake)
    assert raised.value.code == "CONFORMANCE_ENVIRONMENT"
    created = [c[-1] for c in fake.calls if c[:2] == ("network", "create")]
    assert set(created) <= set(fake.removed) | {c[-1] for c in fake.calls if c[:2] == fail}
    runs = home / ".agentbreeder" / "conformance" / ".runs"
    assert not runs.exists() or not any(runs.iterdir())


def test_a_database_that_never_starts_is_a_coded_refusal(tmp_path, docker, monkeypatch):
    monkeypatch.setattr(environment, "DATABASE_SECONDS", -1)
    fake = docker(fail=("container", "exec"))
    with pytest.raises(environment.ConformanceError, match="database"):
        environment.run(IMAGE, project(tmp_path), fake)
    assert len(fake.removed) == 3  # the database container and both networks


def test_sigterm_removes_the_environment(monkeypatch, capsys):
    import os
    import signal

    seen = {}

    def interrupted(image, directory):
        seen["handler"] = signal.getsignal(signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("the signal did not unwind the run")

    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(environment, "run", interrupted)
    with pytest.raises(SystemExit) as raised:
        cli.main(["conformance", IMAGE])
    assert raised.value.code == 128 + signal.SIGTERM
    assert seen["handler"] is not before and signal.getsignal(signal.SIGTERM) is before


@pytest.mark.parametrize("flaw", ["bad_ports", "no_container"])
def test_an_environment_that_cannot_be_verified_is_refused(tmp_path, docker, flaw):
    fake = docker(flaws=[flaw], agent=False)
    with pytest.raises(environment.ConformanceError) as raised:
        environment.run(IMAGE, project(tmp_path), fake)
    assert raised.value.code == "CONFORMANCE_ENVIRONMENT"
    assert len(fake.removed) >= 4


def test_fakes_that_never_answer_are_a_coded_refusal(tmp_path, docker, monkeypatch):
    monkeypatch.setattr(environment, "FAKES_SECONDS", -1)
    monkeypatch.setattr(checks.Control, "healthy", lambda self: False)
    with pytest.raises(environment.ConformanceError, match="fakes"):
        environment.run(IMAGE, project(tmp_path), docker(agent=False))


def test_an_agent_that_never_gets_healthy_still_gets_a_report(tmp_path, docker, monkeypatch):
    monkeypatch.setattr(environment, "HEALTH_SECONDS", 0.2)
    result = environment.run(IMAGE, project(tmp_path), docker(agent=False))
    health = next(row for row in result["checks"] if row["id"] == "serving.health")
    assert health["status"] == checks.FAIL and result["result"] == "fail"


def test_ports_that_are_bound_a_moment_after_start_are_waited_for(tmp_path, docker):
    fake = docker(flaws=["slow_ports"])
    target = environment.subject(IMAGE, project(tmp_path))
    with environment.Environment(target, fake, sleep=lambda seconds: None) as context:
        assert fake.inspections == 2 and context.control.healthy()


def test_a_linux_engine_gets_an_isolated_gateway_or_is_refused(tmp_path, docker):
    fake = docker(engine="Ubuntu 24.04")
    target = environment.subject(IMAGE, project(tmp_path))
    with environment.Environment(target, fake):
        internal, public = [c for c in fake.calls if c[:2] == ("network", "create")]
    for option in boundary.ISOLATED_GATEWAY:
        assert f"--opt={option}" in internal and f"--opt={option}" not in public
    for version in ("27.5.1", "unknown"):
        old = docker(engine="Ubuntu 24.04", version=version)
        with pytest.raises(environment.ConformanceError, match="Engine 28"):
            environment.run(IMAGE, project(tmp_path), old)
        assert not any(c[:2] == ("container", "create") for c in old.calls)


def test_the_agent_gets_one_turn_to_build_itself_before_the_checks(tmp_path, docker):
    fake = docker()
    target = environment.subject(IMAGE, project(tmp_path))
    with environment.Environment(target, fake) as context:
        (warm,) = context.control.model_calls()
        assert warm["body"]["messages"][-1]["content"] == "Hello."
        assert len(context.agent.seen) == 1


@pytest.mark.parametrize("flaw", ["no_logs", "long_logs"])
def test_a_log_that_cannot_be_read_whole_is_not_run_not_passed(tmp_path, docker, flaw):
    result = environment.run(IMAGE, project(tmp_path), docker(flaws=[flaw, "log_key"]))
    rows = {row["id"]: row for row in result["checks"]}
    assert rows["hygiene.logs"]["status"] == checks.NOT_RUN
    assert rows["model_errors.text_logs"]["status"] == checks.NOT_RUN


def test_the_fakes_image_is_reused_when_its_code_matches(tmp_path, docker):
    fake = docker()
    _, digest = environment.fakes_context()
    original = fake.command

    def command(*arguments, **options):
        if arguments[:2] == ("image", "inspect"):
            fake.calls.append(arguments)
            label = f"{environment.LABEL}-code"
            return json.dumps([{"Config": {"Labels": {label: digest}}}])
        return original(*arguments, **options)

    fake.command = command
    target = environment.subject(IMAGE, project(tmp_path))
    tag = environment.Environment(target, fake)._fakes_image()
    assert tag == "agentbreeder-conformance-fakes:" + digest[7:23]
    assert not any(call[0] == "build" for call in fake.calls)


def test_logs_before_the_agent_exists_are_not_run(tmp_path, docker):
    target = environment.subject(IMAGE, project(tmp_path))
    with pytest.raises(checks.NotRun):
        environment.Environment(target, docker()).logs()


# -- the report --------------------------------------------------------------------------------


def rows(*statuses) -> list[dict]:
    return [
        {"id": f"g.c{i}", "group": "G", "title": "t", "status": status, "detail": "why\x1b[2J"}
        for i, status in enumerate(statuses)
    ]


def make_report(statuses=("pass",), image=IMAGE, runtime=RUNTIME) -> dict:
    return report.build(
        image=image,
        runtime=runtime,
        profile="v1",
        agent="stub",
        rows=rows(*statuses),
        started="2026-11-18T10:00:00Z",
    )


def test_a_report_is_written_privately_under_the_default_state(home):
    made = make_report(("pass", "not_run", "not_applicable"))
    assert made["result"] == "incomplete" and made["schema_version"] == 1
    assert made["counts"] == {"pass": 1, "fail": 0, "not_run": 1, "not_applicable": 1}
    assert (made["protocol"], made["mode"]) == (1, "declared")
    path = report.write(made)
    assert path == home / ".agentbreeder/conformance/adk/1.3.0" / ("c" * 64 + ".json")
    assert json.loads(path.read_text()) == made
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temporary file left


def test_the_newest_report_is_the_summary_registry_runtimes_shows(home):
    assert report.newest(RUNTIME) is None
    old = {**make_report(("fail",)), "finished": "2026-11-18T10:00:00Z"}
    new = {
        **make_report(("pass",), image="sha256:" + "d" * 64),
        "finished": "2026-11-18T11:00:00Z",
    }
    directory = report.write(old).parent
    report.write(new)
    assert report.newest(RUNTIME) == {
        "passed": True,
        "result": "pass",
        "digest": "sha256:" + "d" * 64,
        "date": "2026-11-18T11:00:00Z",
    }
    assert report.newest("runtime/adk@1.4.0") is None and report.newest("tool/x@1.0.0") is None
    # Files that are not this runtime's private reports are ignored.
    (directory / ("e" * 64 + ".json")).write_text("{not json")
    (directory / ("f" * 64 + ".json")).write_text(
        json.dumps({**new, "runtime": "runtime/x@1.0.0"})
    )
    renamed = {**new, "finished": "2026-11-18T12:00:00Z"}
    (directory / ("a" * 64 + ".json")).write_text(json.dumps(renamed))  # not its own digest
    loose = directory / ("b" * 64 + ".json")
    loose.write_text(json.dumps({**renamed, "image": "sha256:" + "b" * 64}))
    loose.chmod(0o666)
    (directory / ("9" * 64 + ".json")).symlink_to(directory / ("d" * 64 + ".json"))
    assert report.newest(RUNTIME)["date"] == "2026-11-18T11:00:00Z"
    directory.chmod(0o777)
    assert report.newest(RUNTIME) is None


def test_a_report_is_not_written_through_a_link_or_without_an_image(home, tmp_path):
    with pytest.raises(ValueError):
        report.write({**make_report(), "image": "northwind:latest"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    base = home / ".agentbreeder" / "conformance"
    base.mkdir(parents=True)
    (base / "adk").symlink_to(elsewhere)
    with pytest.raises(OSError):
        report.write(make_report())
    assert list(elsewhere.iterdir()) == []


def test_the_text_report_is_one_safe_line_per_check():
    text = report.text(make_report(("pass", "fail", "not_run")))
    assert "\x1b" not in text
    assert "Result: fail (1 passed, 1 failed, 1 not run, 0 not applicable)" in text
    assert "  fail           g.c1: why" in text
    incomplete = report.text(make_report(("pass", "not_run")))
    assert "Not a pass" in incomplete and "Not a pass" not in text


# -- the command -------------------------------------------------------------------------------


def test_the_command_writes_the_report_and_exits_by_result(tmp_path, monkeypatch, capsys, home):
    results = {"value": make_report(("pass", "not_run"))}
    seen = {}

    def fake_run(image, directory, docker=None):
        seen.update(image=image, project=directory)
        return results["value"]

    monkeypatch.setattr(environment, "run", fake_run)
    assert cli.main(["conformance", IMAGE, "--project", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    path = home / ".agentbreeder/conformance/adk/1.3.0" / ("c" * 64 + ".json")
    assert f"Report: {path}" in out and "Result: incomplete" in out and path.exists()
    assert seen == {"image": IMAGE, "project": tmp_path}

    results["value"] = make_report(("pass", "not_applicable"))
    assert cli.main(["conformance", IMAGE, "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["result"] == "pass" and printed["report_path"] == str(path)
    assert json.loads(path.read_text())["result"] == "pass"

    results["value"] = make_report(("fail",))
    assert cli.main(["conformance", IMAGE]) == 1


def test_the_command_reports_a_refusal_with_its_code(tmp_path, capsys):
    assert cli.main(["conformance", "northwind:latest", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[CONFORMANCE_IMAGE] ")
    assert json.loads(captured.out)["error"]["code"] == "CONFORMANCE_IMAGE"
    assert cli.main(["conformance", IMAGE, "--project", str(tmp_path)]) == 2
    assert capsys.readouterr().err.startswith("[CONFORMANCE_NO_BUILD_RECORD] ")


def test_a_report_that_cannot_be_written_is_a_refusal(monkeypatch, capsys, home):
    monkeypatch.setattr(environment, "run", lambda image, directory: make_report())
    (home / ".agentbreeder").write_text("a file where the directory should be")
    assert cli.main(["conformance", IMAGE]) == 2
    assert capsys.readouterr().err.startswith("[CONFORMANCE_REPORT] ")


def test_registry_runtimes_shows_the_newest_report(capsys, home, tmp_path):
    assert cli.main(["registry", "runtimes", "--json", "--project", str(tmp_path)]) == 0
    before = json.loads(capsys.readouterr().out)["runtimes"]
    assert all("conformance" not in entry for entry in before)
    report.write({**make_report(("pass", "not_run")), "finished": "2026-11-18T11:00:00Z"})
    assert cli.main(["registry", "runtimes", "--json", "--project", str(tmp_path)]) == 0
    after = {e["ref"]: e for e in json.loads(capsys.readouterr().out)["runtimes"]}
    assert after[RUNTIME]["conformance"] == {
        "passed": False,
        "result": "incomplete",
        "digest": IMAGE,
        "date": "2026-11-18T11:00:00Z",
    }
    assert "conformance" not in after["runtime/adk@1.4.0"]
    assert cli.main(["registry", "runtimes", "--project", str(tmp_path)]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith(RUNTIME))
    assert "conformance incomplete; " in line


def test_the_agent_container_carries_the_run_label(tmp_path, docker):
    # A run killed outright is found by this label, the agent's container too.
    fake = docker()
    environment.run(IMAGE, project(tmp_path), fake)
    labels = fake.created["labels"]
    assert list(labels) == [environment.LABEL] and labels[environment.LABEL]


def test_the_run_directory_is_not_made_through_a_link(tmp_path, docker, home):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    base = home / ".agentbreeder" / "conformance"
    base.mkdir(parents=True)
    (base / ".runs").symlink_to(elsewhere)
    with pytest.raises(OSError):
        environment.run(IMAGE, project(tmp_path), docker())
    assert not any(elsewhere.iterdir())


def test_files_that_cannot_be_prepared_are_a_coded_refusal(tmp_path, monkeypatch, capsys):
    def broken(image, directory):
        raise PermissionError("no")

    monkeypatch.setattr(environment, "run", broken)
    assert cli.main(["conformance", IMAGE, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONFORMANCE_ENVIRONMENT"


def test_cleanup_holds_sigterm_until_it_is_done(tmp_path, docker, monkeypatch):
    import signal

    seen = []
    real = signal.pthread_sigmask

    def mask(how, signals):
        seen.append((how, set(signals)))
        return real(how, signals)

    monkeypatch.setattr(signal, "pthread_sigmask", mask)
    environment.run(IMAGE, project(tmp_path), docker())
    assert seen[0] == (signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGHUP})
    assert seen[-1][0] == signal.SIG_SETMASK


def test_registry_conformance_prints_the_newest_whole_report(capsys, home):
    """`registry conformance REF` (#1085 S3): the console's runtime detail reads it."""
    assert cli.main(["registry", "conformance", RUNTIME, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "ref": RUNTIME,
        "report": None,
    }
    assert cli.main(["registry", "conformance", RUNTIME]) == 0
    assert capsys.readouterr().out == f"No conformance report for {RUNTIME}.\n"
    old = {**make_report(("fail",)), "finished": "2026-11-18T10:00:00Z"}
    new = {
        **make_report(("pass", "not_run"), image="sha256:" + "d" * 64),
        "finished": "2026-11-18T11:00:00Z",
    }
    report.write(old)
    report.write(new)
    assert cli.main(["registry", "conformance", RUNTIME, "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["report"] == new and printed["ref"] == RUNTIME
    assert report.newest_report(RUNTIME) == new
    assert report.newest(RUNTIME)["digest"] == "sha256:" + "d" * 64
    assert cli.main(["registry", "conformance", RUNTIME]) == 0
    text = capsys.readouterr().out
    assert "Result: incomplete" in text and "Not a pass" in text


@pytest.mark.parametrize("ref", ["tool/x@1.0.0", "runtime/adk", "nonsense"])
def test_registry_conformance_refuses_what_is_not_a_runtime_version(capsys, ref):
    assert cli.main(["registry", "conformance", ref, "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "[REF_" in captured.err
