"""`services up/status/logs/env/down`, the generated operator config and `--stack` (#738).

Docker is faked here; tests/test_services_docker.py runs the same flow against real Docker.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
from pathlib import Path

import pytest
import yaml

from agentbreeder import cli, registry
from agentbreeder.operator_config import CONFIG_ENV, load_operator_config
from agentbreeder.services import engine as engine_module
from agentbreeder.services.compose import DockerCLI
from agentbreeder.services.engine import Engine, context
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import ROOT_ENV

PY = "python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54"
CANARY = "canary-imported-5d1e9a77"
MINTED = b"minted-bootstrap-0c4f"
FAKEGW = {
    "image": PY,
    "provides": "gateway/litellm",
    "command": ["python3", "-m", "http.server", "4000", "--directory", "/files"],
    "user": "65532:65532",
    "ports": {"http": {"port": 4000, "expose": ["agents", "host"], "guard": "litellm-auth"}},
    "params": {"config": {"type": "file", "required": True}},
    "secrets": {"master-key": {"generate": "token"}},
    "health": {"http": "/index.html"},
    "outputs": {
        "url": {"type": "url", "port": "http"},
        "master_key": {"type": "secret", "secret": "master-key", "operator_only": True},
    },
}
STACK = {
    "kind": "stack",
    "schema_version": 1,
    "name": "demo",
    "services": {
        "echo": {"ref": "service/echo@1.0.0", "with": {"label": "hello"}},
        "gateway": {"ref": "service/fakegw@1.0.0", "with": {"config": "gw/config.yaml"}},
    },
    "environment": {
        "gateway": "gateway",
        "models": ["m1"],
        "ceilings": {"budget_usd": 5, "rpm": 60},
        "services": {"echo_url": "{echo.url}/index.html"},
        "bindings": {"ECHO_TOKEN": {"ref": "echo.token"}, "ECHO_LINK": {"ref": "echo.link"}},
        "eval_database": {"ref": "echo.link"},
        "agents": {
            "demo-agent": {
                "sponsor": "lead@example.com",
                "review_by": "2027-06-30",
                "candidate": True,
            }
        },
    },
}


def write_project(project: Path, stack=None, gateway=None) -> Path:
    version = project / "registry" / "service" / "fakegw" / "1.0.0"
    (version / "files").mkdir(parents=True, exist_ok=True)
    (version / "manifest.yaml").write_text("kind: service\nname: fakegw\nversion: 1.0.0\n")
    (version / "service.yaml").write_text(yaml.safe_dump(gateway or FAKEGW))
    (version / "files" / "index.html").write_text("gw\n")
    (project / "gw").mkdir(exist_ok=True)
    (project / "gw" / "config.yaml").write_text(
        yaml.safe_dump({"model_list": [{"model_name": "m1", "litellm_params": {"model": "x"}}]})
    )
    (project / "stack.yaml").write_text(yaml.safe_dump(stack or STACK))
    # Registry versions are immutable once locked; these tests edit them in place.
    (project / "stack.lock").unlink(missing_ok=True)
    return project


class FakeDocker(DockerCLI):
    """Records what the engine asks Docker for; no containers run."""

    def __init__(self):
        super().__init__(run=self._refuse)
        self.events: list[tuple] = []
        self.volumes: dict[str, str] = {}
        self.running: dict[str, dict] = {}
        self.images: set[str] = set()
        self.generation = 0
        self.volume_count = 0
        self.healthy = True
        self.bootstrap_output = MINTED

    @staticmethod
    def _refuse(*args, **kwargs):  # pragma: no cover - a test bug if reached
        raise AssertionError("FakeDocker must not shell out")

    def authorize(self):
        self.context = "fake"

    def up(self, state, service, timeout=300):
        document = yaml.safe_load(state.compose_path.read_text())
        config = document["services"][service]["labels"]["io.agentbreeder.config"]
        current = self.running.get(service)
        if current is None or current["config"] != config:
            self.generation += 1
            self.running[service] = {
                "id": f"c{self.generation}",
                "state": "running",
                "health": "healthy",
                "config": config,
            }
        for key in document.get("volumes", {}).values():
            if key["name"] not in self.volumes:
                self.volume_count += 1
                self.volumes[key["name"]] = f"created-{self.volume_count}"
        self.events.append(("up", service))

    def containers(self, state):
        return copy.deepcopy(self.running)

    def down(self, state):
        self.events.append(("down",))
        self.running.clear()

    def logs(self, state, service, tail):
        return f"{service} started with token {self.secret_seen}\n"

    secret_seen = ""

    def volume_identity(self, name):
        created = self.volumes.get(name)
        return f"{name}@{created}" if created else None

    def stack_volumes(self, state):
        return sorted(n for n in self.volumes if n.startswith(state.project + "-"))

    def remove_volume(self, name):
        self.events.append(("rm-volume", name))
        del self.volumes[name]

    def build(self, context, tag):
        self.events.append(("build", tag))
        self.images.add(tag)

    def image_exists(self, tag):
        return tag in self.images

    def stack_images(self, state):
        return sorted(t for t in self.images if t.startswith(f"agentbreeder-svc/{state.id}-"))

    def remove_image(self, tag):
        self.events.append(("rm-image", tag))
        self.images.discard(tag)

    def pull(self, image):
        self.events.append(("pull", image))

    def probe(self, state, network, url, **kwargs):
        self.events.append(("probe", url))
        return self.healthy

    def oneshot(self, state, *, label, capture=False, **kwargs):
        self.events.append(("oneshot", label, kwargs["mounts"], kwargs["env"]))
        return self.bootstrap_output if capture else b""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", CANARY)
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    project = write_project(tmp_path / "project")
    return project


def engine(project, docker=None, ports=None):
    counter = iter(ports or range(41000, 42000))
    return Engine(project, docker=docker or FakeDocker(), allocate=lambda: next(counter))


def up(project, docker=None, confirm=True, **options):
    instance = engine(project, docker)
    try:
        return instance.up("dev", confirm=lambda grant: confirm, **options), instance
    finally:
        instance.close()


def all_files(root: Path) -> str:
    text = []
    for path in root.rglob("*"):
        if path.is_file() and "service-secrets" not in path.parts and "mounts" not in path.parts:
            if path.name in ("stack.db",) or "secrets" in path.parts:
                continue
            text.append(path.read_text(errors="replace"))
    return "\n".join(text)


def test_up_writes_compose_operator_config_and_bindings(env, tmp_path):
    docker = FakeDocker()
    result, instance = up(env, docker)
    assert result["services"] == {"echo": "created", "gateway": "created"}
    state_dir = Path(result["state_dir"])
    stack_dir = state_dir.parents[2]
    assert stack_dir.parent == tmp_path / "stacks"
    assert (env / "stack.lock").exists()

    compose = yaml.safe_load((stack_dir / "compose.yaml").read_text())
    echo = compose["services"]["echo"]
    assert echo["read_only"] and echo["cap_drop"] == ["ALL"]
    assert echo["entrypoint"] == ["python3"]  # command is the whole argv; image ENTRYPOINT unused
    assert echo["command"][:2] == ["-m", "http.server"]
    assert echo["networks"] == ["stack", "agents", "edge"]
    assert echo["ports"][0]["host_ip"] == "127.0.0.1"
    assert echo["environment"] == {
        "ECHO_LABEL": "hello",
        "PYTHONDONTWRITEBYTECODE": "1",
        "AGENTBREEDER_SERVICE": "echo",
    }
    assert echo["labels"]["io.agentbreeder.stack"] == result["stack"]
    assert compose["networks"]["stack"]["internal"] is True
    assert compose["networks"]["agents"]["internal"] is True
    assert "internal" not in compose["networks"]["edge"]
    assert compose["volumes"]["echo-data"]["labels"] == {"io.agentbreeder.stack": result["stack"]}
    mounts = {v["target"]: v for v in echo["volumes"]}
    secrets_dir = Path(mounts["/run/secrets"]["source"])
    assert (secrets_dir / "canary").read_text() == CANARY
    assert (secrets_dir / "minted").read_bytes() == MINTED  # recreated after bootstrap
    assert mounts["/files"]["read_only"]

    operator = load_operator_config(Path(result["operator_config"]))
    dev = operator.model.environments["dev"]
    assert dev.network == f"agentbreeder-{result['stack']}-agents"
    assert dev.services == {"echo_url": "http://echo:8080/index.html"}
    assert dev.bindings == {
        "ECHO_LINK": "secret://binding-echo-link",
        "ECHO_TOKEN": "secret://binding-echo-token",
    }
    assert dev.agents["demo-agent"].candidate_identity.ref == "demo-agent-candidate"
    gateway = operator.model.gateways["gateway"]
    assert gateway.agent_url == "http://gateway:4000"
    assert gateway.url.startswith("http://127.0.0.1:41")
    assert operator.model.pdp is None and operator.model.enforcement == "enforce"
    assert set(operator.model.providers) == {"demo"}
    token = (stack_dir / "service-secrets" / "echo" / "token").read_bytes()
    link = (state_dir / "secrets" / "binding-echo-link").read_text()
    host_link = (state_dir / "secrets" / "eval-database").read_text()
    assert link == f"http://echo:{token.decode()}@echo:8080/index.html"
    assert host_link.startswith(f"http://echo:{token.decode()}@127.0.0.1:")
    principals = yaml.safe_load((stack_dir / "envs" / "dev" / "principals.yaml").read_text())
    assert principals["principals"]["demo-agent"]["sponsors"][0]["active"] is True
    # Read the way the identity gate reads them: anchors and aliases are refused there.
    from agentbreeder.operator_config import parse_yaml

    for name in ("operator.yaml", "principals.yaml"):
        assert parse_yaml((stack_dir / "envs" / "dev" / name).read_bytes(), name)
    for name in ("operator.yaml", "principals.yaml"):
        assert stat.S_IMODE((stack_dir / "envs" / "dev" / name).stat().st_mode) == 0o600

    # Canaries: never in generated files, never in the result.
    generated = all_files(stack_dir)
    for value in (CANARY, token.decode(), MINTED.decode()):
        assert value not in generated
        assert value not in json.dumps(result)

    labels = [e for e in docker.events if e[0] == "oneshot"]
    assert [e[1] for e in labels] == ["echo.init", "echo.bootstrap"]
    init_mount = Path(labels[0][2][0][0])
    assert sorted(p.name for p in init_mount.iterdir()) == ["token"]  # only declared secrets
    # A step reaches its own service by name; AgentBreeder sets this, the service can't.
    assert labels[0][3]["AGENTBREEDER_SERVICE"] == "echo"
    assert labels[1][3]["AGENTBREEDER_SERVICE"] == "echo"
    assert ("probe", "http://echo:8080/index.html") in docker.events
    # The running service gets it too (a Host allowlist can name the service's own address).
    compose = yaml.safe_load((stack_dir / "compose.yaml").read_text())
    assert compose["services"]["echo"]["environment"]["AGENTBREEDER_SERVICE"] == "echo"


def test_second_up_recreates_nothing_and_needs_no_confirmation(env):
    docker = FakeDocker()
    up(env, docker)
    result, _ = up(env, docker, confirm=False)
    assert result["services"] == {"echo": "unchanged", "gateway": "unchanged"}
    assert [e[1] for e in docker.events if e[0] == "oneshot"][-1] == "echo.init"  # no bootstrap


def test_grant_must_be_confirmed_and_reconfirmed_when_it_changes(env):
    with pytest.raises(ServiceError, match="GRANT_NOT_CONFIRMED"):
        up(env, confirm=False)
    docker = FakeDocker()
    up(env, docker)
    changed = copy.deepcopy(STACK)
    changed["environment"]["ceilings"]["budget_usd"] = 6
    (env / "stack.yaml").write_text(yaml.safe_dump(changed))
    with pytest.raises(ServiceError, match="GRANT_NOT_CONFIRMED"):
        up(env, docker, confirm=False)


def test_gateway_config_is_part_of_the_grant(env):
    docker = FakeDocker()
    result, _ = up(env, docker)
    grant = result["grant"] if "grant" in result else None
    config = env / "gw" / "config.yaml"
    data = yaml.safe_load(config.read_text())
    data["model_list"].append(
        {
            "model_name": "m2",
            "litellm_params": {"model": "gemini/y", "api_key": "os.environ/GEMINI_API_KEY"},
        }
    )
    config.write_text(yaml.safe_dump(data))
    (env / "stack.lock").unlink()
    seen = []
    instance = engine(env, docker)
    try:
        instance.up("dev", confirm=lambda g: seen.append(g) or True)
    finally:
        instance.close()
    gateway = seen[0]["gateway_config"]
    assert len(gateway["sha256"]) == 64
    assert gateway["providers"] == {"m1": "unknown (no key)", "m2": "gemini (GEMINI_API_KEY)"}
    assert grant is None or grant != seen[0]
    with pytest.raises(ServiceError, match="GRANT_NOT_CONFIRMED"):
        config.write_text(config.read_text() + "\n# edited\n")
        (env / "stack.lock").unlink()
        up(env, docker, confirm=False)


def test_changed_import_recreates_the_service(env, monkeypatch):
    docker = FakeDocker()
    up(env, docker)
    monkeypatch.setenv("ECHO_CANARY", CANARY + "-rotated")
    result, _ = up(env, docker)
    assert result["services"] == {"echo": "recreated", "gateway": "unchanged"}
    monkeypatch.delenv("ECHO_CANARY")
    result, _ = up(env, docker)  # reuses the earlier import
    assert result["services"]["echo"] == "unchanged"


def test_bootstrap_runs_again_for_a_new_volume(env):
    docker = FakeDocker()
    up(env, docker)
    instance = engine(env, docker)
    instance.down(volumes=True)
    instance.close()
    assert not docker.volumes
    docker.bootstrap_output = b"second-minted"
    up(env, docker)
    assert [e[1] for e in docker.events if e[0] == "oneshot"].count("echo.bootstrap") == 2


def test_bootstrap_runs_again_when_a_wired_service_gets_a_new_volume(env):
    # Phoenix keeps its data (and the ingest key it mints) in postgres: a new postgres volume
    # invalidates the minted key even though Phoenix itself has no volume.
    gateway = {
        **FAKEGW,
        "inputs": {"upstream": {"type": "secret"}},
        "bootstrap": {"command": ["python3", "-c", "print(1)"], "writes_secret": "minted"},
    }
    stack = copy.deepcopy(STACK)
    stack["services"]["gateway"]["wire"] = {"upstream": "echo.token"}
    write_project(env, stack=stack, gateway=gateway)
    docker = FakeDocker()
    up(env, docker)
    up(env, docker)
    boots = [e[1] for e in docker.events if e[0] == "oneshot"]
    assert boots.count("gateway.bootstrap") == 1  # same volumes: not minted again
    instance = engine(env, docker)
    instance.down(volumes=True)
    instance.close()
    up(env, docker)
    boots = [e[1] for e in docker.events if e[0] == "oneshot"]
    assert boots.count("gateway.bootstrap") == 2


def test_operator_config_edits_are_refused(env):
    result, _ = up(env)
    path = Path(result["operator_config"])
    path.write_text(path.read_text() + "\n# edited\n")
    with pytest.raises(ServiceError, match="OPERATOR_CONFIG_MODIFIED"):
        up(env)


def test_preseeded_operator_config_is_refused(env):
    instance = engine(env)
    target = instance.state.env_dir("dev")
    target.mkdir(mode=0o700, parents=True)
    (target / "operator.yaml").write_text("enforcement: audit\n")
    instance.close()
    with pytest.raises(ServiceError, match="OPERATOR_CONFIG_MODIFIED"):
        up(env)


def test_preseeded_secret_is_refused(env):
    instance = engine(env)
    secrets = instance.state.directory / "service-secrets" / "echo"
    secrets.mkdir(parents=True, mode=0o700)
    (secrets / "token").write_bytes(b"chosen-by-attacker")
    (secrets / "token").chmod(0o600)
    instance.close()
    with pytest.raises(ServiceError, match="SECRET_NOT_OWNED"):
        up(env)


def test_lost_password_with_existing_data_fails(env):
    docker = FakeDocker()
    result, _ = up(env, docker)
    stack_dir = Path(result["state_dir"]).parents[2]
    (stack_dir / "service-secrets" / "echo" / "token").unlink()
    with pytest.raises(ServiceError, match="SECRET_LOST"):
        up(env, docker)


def test_denylisted_and_missing_imports(env, monkeypatch):
    spec = copy.deepcopy(FAKEGW)
    spec["secrets"]["cloud"] = {"import": "AWS_SECRET_ACCESS_KEY"}
    write_project(env, gateway=spec)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    with pytest.raises(ServiceError, match="IMPORT_DENIED"):
        up(env)
    spec["secrets"]["cloud"] = {"import": "GW_PROVIDER_KEY"}
    write_project(env, gateway=spec)
    monkeypatch.delenv("GW_PROVIDER_KEY", raising=False)
    with pytest.raises(ServiceError, match="SECRET_SOURCE_MISSING") as error:
        up(env)
    assert "GW_PROVIDER_KEY" in str(error.value)
    env_file = env.parent / "keys.env"
    env_file.write_text("GW_PROVIDER_KEY=from-file\n")
    env_file.chmod(0o600)
    result, _ = up(env, env_file=env_file)
    assert result["services"]["gateway"] == "created"


def test_imported_secret_overwritten_by_other_origin_is_refused(env):
    result, instance = up(env)
    again = engine(env)
    again.state.db.execute("UPDATE secrets SET origin='generate' WHERE name='canary'")
    again.close()
    with pytest.raises(ServiceError, match="SECRET_NOT_OWNED"):
        up(env)
    again = engine(env)
    again.state.db.execute("UPDATE secrets SET origin='import' WHERE name='canary'")
    again.state.db.execute("UPDATE secrets SET origin='import' WHERE name='token'")
    again.close()
    with pytest.raises(ServiceError, match="SECRET_NOT_OWNED"):
        up(env)


def test_unhealthy_service_stops_up(env):
    docker = FakeDocker()
    docker.healthy = False
    with pytest.raises(ServiceError, match="SERVICE_UNHEALTHY"):
        up(env, docker)


def test_address_scope_is_enforced(env):
    stack = copy.deepcopy(STACK)
    gateway = copy.deepcopy(FAKEGW)
    gateway["ports"]["http"]["expose"] = "agents"
    write_project(env, stack=stack, gateway=gateway)
    with pytest.raises(ServiceError, match="ADDRESS_SCOPE"):
        up(env)


def test_locked_mode_refuses_a_stale_lock(env):
    up(env)
    (env / "gw" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model_list": [
                    {"model_name": "m1", "litellm_params": {"model": "y"}},
                ]
            }
        )
    )
    with pytest.raises(registry.RegistryError, match="HASH_MISMATCH"):
        up(env, mode="locked")


def test_status_logs_env_and_context(env):
    docker = FakeDocker()
    result, _ = up(env, docker)
    instance = engine(env, docker)
    try:
        status = instance.status()
        assert status["services"]["echo"]["state"] == "running"
        assert status["environments"] == ["dev"]
        assert CANARY not in json.dumps(status)
        docker.secret_seen = CANARY
        assert CANARY not in instance.logs("echo", 10) and "[redacted]" in instance.logs("echo", 1)
        with pytest.raises(ServiceError, match="SERVICE_UNKNOWN"):
            instance.logs("nope", 1)
        lines = instance.env_lines(None)
        assert lines[0] == f"export {CONFIG_ENV}={result['operator_config']}"
    finally:
        instance.close()
    resolved = context(env, None)
    assert resolved.environment == "dev" and resolved.state_dir == Path(result["state_dir"])
    with pytest.raises(ServiceError, match="STACK_NOT_UP"):
        context(env, "prod")


def test_context_requires_up_and_an_environment(env):
    with pytest.raises(ServiceError, match="STACK_ENVIRONMENT"):
        context(env, None)
    with pytest.raises(ServiceError, match="STACK_MISSING"):
        context(env / "gw", None)


def test_context_with_two_environments(env):
    docker = FakeDocker()
    up(env, docker)
    instance = engine(env, docker)
    instance.up("staging", confirm=lambda grant: True)
    instance.close()
    with pytest.raises(ServiceError, match="one of dev, staging"):
        context(env, None)
    assert context(env, "staging").environment == "staging"


def test_down_refuses_with_active_deployments_and_scopes_volumes(env):
    docker = FakeDocker()
    result, _ = up(env, docker)
    docker.volumes["agentbreeder-someone-else-data"] = "x"
    instance = engine(env, docker)
    try:
        instance.active_deployments = lambda: ["dev/demo-agent"]
        with pytest.raises(ServiceError, match="STACK_IN_USE"):
            instance.down()
        del instance.active_deployments
        removed = instance.down(volumes=True)
        assert removed["volumes_removed"] == [f"agentbreeder-{result['stack']}-echo-data"]
        assert "agentbreeder-someone-else-data" in docker.volumes
    finally:
        instance.close()


def test_down_purge_removes_everything_the_stack_made(env):
    # A built service (fakegw) so there is a stack image to remove; another stack's image stays.
    gateway = {k: v for k, v in FAKEGW.items() if k != "image"} | {"build": "Dockerfile"}
    write_project(env, gateway=gateway)
    version = env / "registry" / "service" / "fakegw" / "1.0.0"
    (version / "Dockerfile").write_text(f"FROM {PY}\nCOPY files /files\n")
    docker = FakeDocker()
    result, _ = up(env, docker)
    docker.images.add("agentbreeder-svc/other-12345678-gateway:0123456789abcdef")
    state_dir = Path(result["state_dir"]).parents[2]
    assert state_dir.is_dir()
    instance = engine(env, docker)
    try:
        instance.active_deployments = lambda: ["dev/demo-agent"]
        with pytest.raises(ServiceError, match="STACK_IN_USE"):
            instance.down(purge=True)
        assert state_dir.is_dir()  # refused before touching anything
        del instance.active_deployments
        removed = instance.down(purge=True)
    finally:
        instance.close()
    assert removed["volumes_removed"] == [f"agentbreeder-{result['stack']}-echo-data"]
    assert [t.split(":")[0] for t in removed["images_removed"]] == [
        f"agentbreeder-svc/{result['stack']}-gateway"
    ]
    assert removed["state_removed"] == str(state_dir)
    assert not state_dir.exists()  # generated secrets, operator config and history are gone
    assert docker.images == {"agentbreeder-svc/other-12345678-gateway:0123456789abcdef"}
    up(env, docker)  # and the next `up` starts from scratch
    assert state_dir.is_dir()


def test_active_deployments_reads_each_environment_store(env, monkeypatch):
    from agentbreeder.store import Store

    result, _ = up(env)
    Store(Path(result["state_dir"])).close()
    monkeypatch.setattr(Store, "active_all", lambda self: [{"name": "demo-agent"}])
    instance = engine(env)
    try:
        assert instance.active_deployments() == ["dev/demo-agent"]
    finally:
        instance.close()


def test_built_services_check_their_dockerfile(env):
    spec = copy.deepcopy(FAKEGW)
    del spec["image"]
    spec["build"] = "Dockerfile"
    write_project(env, gateway=spec)
    dockerfile = env / "registry/service/fakegw/1.0.0/Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n")
    with pytest.raises(ServiceError, match="digest-pinned"):
        up(env)
    for text in (f"FROM {PY}\nARG TOKEN\n", f"FROM {PY}\nRUN --mount=type=secret,id=x true\n"):
        dockerfile.write_text(text)
        (env / "stack.lock").unlink(missing_ok=True)
        with pytest.raises(ServiceError, match="ARG is refused|secrets and SSH"):
            up(env)
    dockerfile.write_text(f"FROM {PY} AS base\n\nFROM base\nCOPY files /files\n")
    (env / "stack.lock").unlink(missing_ok=True)
    docker = FakeDocker()
    result, _ = up(env, docker)
    built = [e[1] for e in docker.events if e[0] == "build"]
    assert len(built) == 1 and built[0].startswith(f"agentbreeder-svc/{result['stack']}-gateway:")
    # A valid Docker reference: one colon, then a tag of [A-Za-z0-9_.-] (the version's hash hex).
    assert re.fullmatch(r"agentbreeder-svc/[a-z0-9-]+:[0-9a-f]{16}", built[0])
    up(env, docker)
    assert len([e for e in docker.events if e[0] == "build"]) == 1  # image reused


def test_dockerfile_must_be_utf8(tmp_path):
    (tmp_path / "Dockerfile").write_bytes(b"FROM \xff\n")
    with pytest.raises(ServiceError, match="plain ASCII"):
        engine_module._check_dockerfile(tmp_path / "Dockerfile")


def test_dockerfile_must_be_a_regular_file(tmp_path):
    with pytest.raises(ServiceError, match="regular Dockerfile"):
        engine_module._check_dockerfile(tmp_path / "Dockerfile")


def test_generated_config_stays_inside_the_demo_subset(env):
    result, _ = up(env)
    document = yaml.safe_load(Path(result["operator_config"]).read_text())
    assert set(document) == {
        "enforcement",
        "default_environment",
        "require_sponsor",
        "providers",
        "gateways",
        "environments",
    }
    assert document["enforcement"] == "enforce" and document["require_sponsor"] is True
    assert [p["type"] for p in document["providers"].values()] == ["static"]
    assert document["environments"]["dev"]["targets"] == ["local"]


def test_operator_config_rejects_invalid_values(env, monkeypatch):
    from agentbreeder.services import plan as plan_module

    monkeypatch.setattr(plan_module.Plan, "url", lambda self, *a: "not a url")
    with pytest.raises(ServiceError, match="OPERATOR_CONFIG_INVALID"):
        up(env)


def test_allocate_port_returns_a_loopback_port():
    port = engine_module.allocate_port()
    assert 1024 < port < 65536


def test_bindings_secret_errors_are_service_errors(env, monkeypatch):
    from agentbreeder import secret_files

    def refuse(*args):
        raise secret_files.SecretError("SECRET_MODE", "bad mode")

    monkeypatch.setattr(secret_files, "set_secret", refuse)
    with pytest.raises(ServiceError, match="SECRET_MODE"):
        up(env)


def test_bootstrap_secret_unavailable_reports_clearly(env):
    from agentbreeder.services.refs import Reference

    instance = engine(env)
    try:
        plan = instance.plan()
        plan.secrets = {"echo": {}}
        with pytest.raises(ServiceError, match="SECRET_UNAVAILABLE"):
            plan.output(Reference("echo", "minted"), "agents")
    finally:
        instance.close()


# -- CLI ----------------------------------------------------------------------------------------


@pytest.fixture
def fake_docker(monkeypatch):
    docker = FakeDocker()
    monkeypatch.setattr(engine_module, "DockerCLI", lambda: docker)
    return docker


def test_cli_up_status_env_logs_down(env, fake_docker, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["services", "up", str(env), "--environment", "dev"]) == 2
    assert "GRANT_NOT_CONFIRMED" in capsys.readouterr().err
    (env / "agent.yaml").write_text("kind: agent\n")  # only its presence matters to the hint
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 0
    out, err = capsys.readouterr()
    project = env.resolve()
    assert "echo             created" in out
    # No agent.lock yet: build first; absolute paths so the line works from anywhere.
    assert (
        f"Next: agentbreeder build {project / 'agent.yaml'} --stack {project} --environment dev"
        in out
    )
    assert "Demo identities" in err and "imports: ECHO_CANARY" in err
    assert CANARY not in out + err
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["services"]["echo"] == "unchanged"
    assert cli.main(["services", "status", str(env)]) == 0
    assert "echo             running (healthy)" in capsys.readouterr().out
    assert cli.main(["services", "status", str(env), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["environments"] == ["dev"]
    assert cli.main(["services", "env", str(env)]) == 0
    assert capsys.readouterr().out.startswith(f"export {CONFIG_ENV}=")
    fake_docker.secret_seen = CANARY
    assert cli.main(["services", "logs", str(env), "echo"]) == 0
    assert CANARY not in capsys.readouterr().out
    monkeypatch.chdir(env)
    assert cli.main(["services", "logs", "echo", "--tail", "3"]) == 0
    capsys.readouterr()
    assert cli.main(["services", "down", str(env), "--volumes"]) == 2
    assert "CONFIRM_REQUIRED" in capsys.readouterr().err
    assert cli.main(["services", "down", str(env), "--volumes", "--yes"]) == 0
    assert "Removed volume" in capsys.readouterr().out
    assert cli.main(["services", "down", str(env), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["volumes_removed"] == []
    assert cli.main(["services", "down", str(env), "--purge"]) == 2
    assert "CONFIRM_REQUIRED" in capsys.readouterr().err
    assert cli.main(["services", "down", str(env), "--purge", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Removed state " in out and "data and secrets kept" not in out


def test_cli_logs_argument_count(env, capsys):
    with pytest.raises(SystemExit):
        cli.main(["services", "logs", "a", "b", "c"])


def test_cli_up_update_accepts_an_edited_file_param(env, fake_docker, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 0
    config = env / "gw" / "config.yaml"
    config.write_text(config.read_text().replace("model: x", "model: y"))
    capsys.readouterr()
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 2
    assert "services up --update" in capsys.readouterr().err
    args = ["services", "up", str(env), "--environment", "dev", "--yes"]
    assert cli.main([*args, "--update"]) == 0
    assert cli.main([*args, "--locked"]) == 0
    with pytest.raises(SystemExit):
        cli.main([*args, "--locked", "--update"])


def test_cli_up_interactive_confirmation(env, fake_docker, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert cli.main(["services", "up", str(env), "--environment", "dev"]) == 0


def test_cli_services_errors(env, fake_docker, capsys, monkeypatch):
    assert cli.main(["services", "status", str(env / "gw")]) == 2
    assert "STACK_MISSING" in capsys.readouterr().err

    def denied(*args, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr(engine_module.Engine, "status", denied)
    assert cli.main(["services", "status", str(env)]) == 2
    assert "Cannot access the stack state" in capsys.readouterr().err


def test_cli_stack_flag_points_commands_at_the_stack(env, fake_docker, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 0
    capsys.readouterr()
    seen = {}

    def stateful(args):
        seen["state_dir"] = args.state_dir
        seen["config"] = os.environ.get(CONFIG_ENV)
        seen["environment"] = getattr(args, "environment", None)
        return 0

    monkeypatch.setattr("agentbreeder.commands.status.run", stateful)
    assert cli.main(["status", "abc", "--stack", str(env)]) == 0
    resolved = context(env, "dev")
    assert seen == {
        "state_dir": resolved.state_dir,
        "config": str(resolved.operator_config),
        "environment": "dev",
    }
    assert "demo identities (generated by services up)" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["status", "abc", "--stack", str(env), "--state-dir", "/tmp/x"])
    with pytest.raises(SystemExit):
        cli.main(["status", "abc", "--environment", "dev"])
    assert cli.main(["status", "abc", "--stack", str(env), "--environment", "prod"]) == 2
    assert "STACK_NOT_UP" in capsys.readouterr().err
    monkeypatch.setattr(
        "agentbreeder.commands.build.run", lambda args: seen.update(build=args.environment) or 0
    )
    assert cli.main(["build", "agent.yaml", "--stack", str(env)]) == 0
    assert seen["build"] == "dev"


# -- planning details ---------------------------------------------------------------------------

DB = {
    "image": PY,
    "user": "image",
    "command": ["python3", "-m", "http.server", "5432"],
    "ports": {"pg": {"port": 5432, "protocol": "tcp", "expose": "agents"}},
    "params": {
        "databases": {"type": "list", "item": "db_name", "default": []},
        "home": {"type": "db_name"},
        "unset": {"type": "string"},
    },
    "secrets": {"role": {"generate": "token", "per": "databases"}},
    "health": {"command": ["python3", "-c", "pass"]},
    "outputs": {
        "databases": {
            "type": "secret",
            "per": "databases",
            "port": "pg",
            "template": "postgresql://{item}:{secret.role}@{address}/{item}",
        },
        "home": {
            "type": "secret",
            "per": "databases",
            "port": "pg",
            "template": "postgresql://{item}:{secret.role}@{address}/{params.home}",
        },
        "bad": {"type": "secret", "port": "pg", "template": "{params.unset}"},
        "addr": {"type": "url", "port": "pg"},
    },
}
APP = {
    "image": PY,
    "user": "host",
    "command": ["python3", "-m", "http.server"],
    "env": {"DB_ADDR": "{inputs.addr}", "OPTIONAL": "{inputs.extra}"},
    "inputs": {
        "database-url": {"type": "secret"},
        "addr": {"type": "url"},
        "extra": {"type": "url", "optional": True},
    },
}


def test_plan_per_item_tcp_urls_and_inputs(env):
    for name, spec in (("db", DB), ("app", APP)):
        version = env / "registry" / "service" / name / "1.0.0"
        version.mkdir(parents=True)
        (version / "manifest.yaml").write_text(f"kind: service\nname: {name}\nversion: 1.0.0\n")
        (version / "service.yaml").write_text(yaml.safe_dump(spec))
    stack = copy.deepcopy(STACK)
    stack["services"]["db"] = {
        "ref": "service/db@1.0.0",
        "with": {"databases": ["support"], "home": "shared"},
    }
    stack["services"]["app"] = {
        "ref": "service/app@1.0.0",
        "wire": {"database-url": "db.databases.support", "addr": "db.addr"},
    }
    write_project(env, stack=stack)
    docker = FakeDocker()
    result, _ = up(env, docker)
    stack_dir = Path(result["state_dir"]).parents[2]
    compose = yaml.safe_load((stack_dir / "compose.yaml").read_text())
    app = compose["services"]["app"]
    assert app["user"] == f"{os.getuid()}:{os.getgid()}"
    # an optional unwired input is left out
    assert app["environment"] == {"DB_ADDR": "db:5432", "AGENTBREEDER_SERVICE": "app"}
    assert "user" not in compose["services"]["db"]
    assert compose["services"]["db"]["healthcheck"]["test"] == ["CMD", "python3", "-c", "pass"]
    secrets_dir = Path(next(v for v in app["volumes"] if v["target"] == "/run/secrets")["source"])
    role = (stack_dir / "service-secrets" / "db" / "role.support").read_text()
    assert (secrets_dir / "database-url").read_text() == (
        f"postgresql://support:{role}@db:5432/support"
    )
    instance = engine(env, docker)
    try:
        plan = instance.plan()
        instance.secrets(plan, None)
        from agentbreeder.services.refs import Reference

        assert plan.output(Reference("db", "home", "support"), "internal") == (
            f"postgresql://support:{role}@db:5432/shared".encode()
        )
        with pytest.raises(ServiceError, match="TEMPLATE_INVALID.*has no value"):
            plan.output(Reference("db", "bad"), "internal")
    finally:
        instance.close()


def test_probe_image_is_pulled_once(env):
    from agentbreeder.services.compose import PROBE_IMAGE

    docker = FakeDocker()
    up(env, docker)
    assert docker.events.count(("pull", PROBE_IMAGE)) == 1


def test_new_generated_secret_with_existing_volume_is_created(env):
    docker = FakeDocker()
    up(env, docker)
    instance = engine(env, docker)
    instance.state.db.execute("DELETE FROM secrets WHERE name='token'")
    (instance.state.directory / "service-secrets" / "echo" / "token").unlink()
    instance.close()
    result, _ = up(env, docker)  # never recorded: a new secret, not a lost one
    assert result["services"]["echo"] == "recreated"


def test_declined_grant_leaves_no_lock_and_no_ports(env):
    with pytest.raises(ServiceError, match="GRANT_NOT_CONFIRMED"):
        up(env, confirm=False)
    assert not (env / "stack.lock").exists()
    instance = engine(env)
    try:
        assert instance.state.ports() == {}
    finally:
        instance.close()


def test_changing_a_binding_needs_reconfirmation(env):
    docker = FakeDocker()
    up(env, docker)
    changed = copy.deepcopy(STACK)
    changed["environment"]["bindings"]["ECHO_TOKEN"] = {"ref": "echo.minted"}
    (env / "stack.yaml").write_text(yaml.safe_dump(changed))
    with pytest.raises(ServiceError, match="GRANT_NOT_CONFIRMED"):
        up(env, docker, confirm=False)
    instance = engine(env, docker)
    try:
        from agentbreeder.services.plan import grant_text

        grant = instance.plan().grant("dev")
        assert grant["bindings"]["ECHO_TOKEN"] == "echo.minted"
        text = grant_text(grant)
        assert "secret ECHO_TOKEN <- echo.minted" in text and "Eval database" in text
        assert "keys go only to their provider" in text and "m1: unknown (no key)" in text
        origins = {svc["name"]: svc["origin"] for svc in grant["services"]}
        assert origins == {"echo": "built-in", "gateway": "project"}
        # A project gateway is project code: the config review can't bind what it does.
        assert "gateway is project code" in text.replace("\n", " ")
        assert "keys go only" not in grant_text({**grant, "gateway_config": None})
    finally:
        instance.close()


def test_environment_names_cannot_collide_with_stack_dirs(env):
    docker = FakeDocker()
    instance = engine(env, docker)
    try:
        instance.up("mounts", confirm=lambda grant: True)
        assert (instance.state.directory / "envs" / "mounts" / "operator.yaml").exists()
        instance.up("mounts", confirm=lambda grant: True)  # prune_mounts keeps it
        assert (instance.state.deploy_state("mounts") / "secrets").is_dir()
    finally:
        instance.close()


@pytest.mark.parametrize(
    "text,why",
    [
        ("# syntax=evil/frontend\nFROM {py}\n", "parser directives"),
        ("#escape=`\nFROM {py}\n", "parser directives"),
        ("#!/bin/true\n# syntax=evil/frontend\nFROM {py}\n", "parser directives"),
        ("# note\n\n#  check = skip=all\nFROM {py}\n", "parser directives"),
        ("FROM {py}\n# syntax=evil/frontend\n", "parser directives"),
        ("\ufeffFROM {py}\n", "plain ASCII"),
        ("FROM {py}\r\nRUN true\r\n", "printable ASCII"),
        ("FROM {py}\nRUN true\u00a0\n", "plain ASCII"),
        ("FROM \\\n  python:3.12-slim\n", "continuations"),
        ("FROM {py}\nRUN true \\ \t\n", "continuations"),
        ("FROM {py}\nRUN <<EOT\ntrue\nEOT\n", "heredocs"),
        ("FROM python:3.12-slim\n", "digest-pinned"),
        ("FROM ${{IMG}}\n", "digest-pinned"),
        ("FROM python@sha256:{short}\n", "digest-pinned"),
        ("FROM {py} AS base\nFROM 0\n", "digest-pinned"),
        ("FROM --platform=linux/amd64 {py}\n", "unsupported FROM option"),
        ("FROM {py} AS a extra\n", "optional `AS name`"),
        ("FROM {py}\nCOPY --from=evil:latest /x /x\n", "--from"),
        ("FROM {py}\nCOPY --FROM=evil:1 /x /x\n", "unsupported COPY option"),
        ("FROM {py}\nCOPY --from=$IMG /x /x\n", "unsupported COPY option"),
        ("FROM {py}\nCOPY --fr\\om=evil /x /x\n", "unsupported COPY option"),
        ("FROM {py}\nCOPY --from=1 /x /x\n", "--from"),
        ("FROM {py}\nRUN --network=host true\n", "unsupported RUN option"),
        ("FROM {py}\nRUN --mount=type=secret,id=x true\n", "secrets and SSH"),
        ('FROM {py}\nRUN --mount="type=secret,id=x" true\n', "unsupported RUN option"),
        ("FROM {py}\nRUN --mount=type=bind,from=evil:1 true\n", "mount's from"),
        ("FROM {py}\nRUN --mount=type=cache,target=/c,unknown=1 true\n", "unsupported --mount"),
        ("FROM {py}\nRUN --mount=type=cache,type=ssh true\n", "unsupported --mount"),
        ("FROM {py}\nARG X\n", "ARG is refused"),
        ("FROM {py}\nADD https://example.com/x /x\n", "ADD is refused"),
        ("FROM {py}\nONBUILD RUN true\n", "ONBUILD is refused"),
        ('FROM {py}\nSHELL ["/bin/sh", "-c"]\n', "SHELL is refused"),
    ],
)
def test_dockerfile_subset_refuses_ambiguous_syntax(tmp_path, text, why):
    path = tmp_path / "Dockerfile"
    path.write_bytes(text.format(py=PY, short="a" * 63).encode("utf-8"))
    with pytest.raises(ServiceError, match=re.escape(why)):
        engine_module._check_dockerfile(path)


def test_dockerfile_subset_allows_pinned_multistage_builds(tmp_path):
    path = tmp_path / "Dockerfile"
    path.write_text(
        f"# a comment\nFROM {PY} AS build\nRUN pip install x && echo $HOME\n"
        f"FROM {PY}\nCOPY --from=build --chown=1:1 /x /x\nCOPY --from=0 /y /y\n"
        "RUN --mount=type=cache,target=/c --mount=type=bind,from=build,target=/b true\n"
        "ENV A=1\nUSER 65532:65532\nEXPOSE 8080\n"
        'HEALTHCHECK --interval=5s CMD ["python3", "-c", "pass"]\n'
        'CMD ["python3", "-m", "app"]\n\nFROM scratch\nCOPY --from=1 /z /z\n'
    )
    engine_module._check_dockerfile(path)


def test_generated_secret_gets_its_prefix(env):
    gateway = json.loads(json.dumps(FAKEGW))
    gateway["secrets"]["master-key"]["prefix"] = "sk-"
    write_project(env, gateway=gateway)
    _, instance = up(env, FakeDocker())
    value = (instance.state.directory / "service-secrets" / "gateway" / "master-key").read_bytes()
    assert value.startswith(b"sk-") and len(value) == 3 + 48
    assert all(c in b"0123456789abcdef" for c in value[3:])


# -- grant preview and the Kubernetes block (#783) ------------------------------------


CLUSTER = {
    "kubeconfig": "/k/kubeconfig",
    "context": "kind-agentbreeder",
    "cluster_uid": "11111111-2222-3333-4444-555555555555",
    "namespace": "agentbreeder",
    "registry": "localhost:5001/agentbreeder",
    "blocked_cidrs": ["10.244.0.0/16"],
}


def test_preview_digest_matches_up_and_starts_nothing(env):
    docker = FakeDocker()
    instance = engine(env, docker)
    try:
        preview = instance.preview("dev")
    finally:
        instance.close()
    assert preview["confirmed"] is False and preview["digest"] and "echo" in preview["text"]
    assert not any(event[0] == "start" for event in docker.events)
    result, _ = up(env, docker, expect_grant=preview["digest"])
    assert result["services"]["echo"] == "created"


def test_expect_grant_refuses_a_changed_grant(env):
    with pytest.raises(ServiceError, match="GRANT_CHANGED"):
        up(env, FakeDocker(), expect_grant="0" * 64)


def test_preview_reports_imported_keys_by_name_only(env):
    instance = engine(env)
    try:
        preview = instance.preview("dev")
    finally:
        instance.close()
    assert CANARY not in json.dumps(preview)
    assert all(set(key) == {"name", "optional", "stored"} for key in preview["keys"])


def test_cluster_block_reaches_the_grant_and_operator_config(env, monkeypatch):
    from agentbreeder import cluster as local_cluster

    connected = []
    monkeypatch.setattr(local_cluster, "connect", lambda *pair: connected.append(pair))
    monkeypatch.setattr(local_cluster, "blocked_cidrs", lambda network: ["10.0.0.0/8"])
    docker = FakeDocker()
    first, instance = up(env, docker)
    stored = engine(env, docker)
    try:
        before = stored.preview("dev")["digest"]
        stored.state.put("kubernetes", json.dumps(CLUSTER))
        preview = stored.preview("dev")
    finally:
        stored.close()
    assert (
        preview["digest"] != before
        and preview["grant"]["kubernetes"]["context"] == CLUSTER["context"]
    )
    assert "Kubernetes target" in preview["text"]
    result, _ = up(env, docker, expect_grant=preview["digest"])
    config = yaml.safe_load(Path(result["operator_config"]).read_text())["environments"]["dev"]
    assert config["targets"] == ["local", "kubernetes"]
    assert config["kubernetes"]["blocked_cidrs"] == ["10.0.0.0/8"]
    assert connected and connected[-1][0] == local_cluster.NODE


def test_cluster_grant_needs_confirmation(env, monkeypatch):
    from agentbreeder import cluster as local_cluster

    monkeypatch.setattr(local_cluster, "connect", lambda *pair: None)
    monkeypatch.setattr(local_cluster, "blocked_cidrs", lambda network: ["10.0.0.0/8"])
    docker = FakeDocker()
    up(env, docker)
    instance = engine(env, docker)
    instance.state.put("kubernetes", json.dumps(CLUSTER))
    instance.close()
    with pytest.raises(ServiceError, match="GRANT_NOT_CONFIRMED"):
        up(env, docker, confirm=False)


def test_down_detaches_the_kind_node(env):
    docker = FakeDocker()
    up(env, docker)
    instance = engine(env, docker)
    instance.state.put("kubernetes", json.dumps(CLUSTER))
    instance.close()
    calls = []
    docker.run = lambda *arguments, **kwargs: calls.append(arguments) or ""
    instance = engine(env, docker)
    try:
        instance.down()
    finally:
        instance.close()
    assert any(call[:2] == ("network", "disconnect") for call in calls)


def test_cli_services_plan(env, monkeypatch, capsys):
    monkeypatch.setattr(engine_module, "DockerCLI", FakeDocker)
    assert cli.main(["services", "plan", str(env), "--environment", "dev"]) == 0
    assert "Services:" in capsys.readouterr().out
