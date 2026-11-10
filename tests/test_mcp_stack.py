"""`services up` with stack.yaml `mcp:` entries (#745): one built-in relay per entry, the grant,
two-phase pinning, `--repin` / `--locked`, status, the narrowing refusal and service upstreams.

Docker is faked (tests/test_services_engine.py); the relay itself is tested against a mock
upstream in tests/test_mcp_relay.py and in Docker in tests/test_mcp_relay_docker.py.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
import yaml
from test_services_engine import PY, STACK, FakeDocker, write_project

from agentbreeder import cli, registry
from agentbreeder.operator_config import CONFIG_ENV, load_operator_config
from agentbreeder.registry import RegistryError
from agentbreeder.services import mcp_stack
from agentbreeder.services import plan as plan_mod
from agentbreeder.services.engine import Engine
from agentbreeder.services.lock import StackLock
from agentbreeder.services.plan import grant_text
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import ROOT_ENV

CANARY = "ghp_canary_mcp_credential_77aa"
AGENT = "demo-agent"
REMOTE = {
    "upstream": {"remote": {"url": "https://api.example.com/mcp"}},
    "auth": {"type": "bearer", "secret": "token"},
    "secrets": {"token": {"import": "GITHUB_MCP_TOKEN"}},
    "tools": {
        "list_issues": {"effect": "read"},
        "create_issue": {"effect": "write"},
        "delete_repo": {"effect": "write"},
    },
}


def tool(name: str, description: str = "") -> dict:
    return {"name": name, "description": description or name, "inputSchema": {"type": "object"}}


class RelayDocker(FakeDocker):
    """FakeDocker plus the relay's probe: answers like relay/relay.py would."""

    def __init__(self):
        super().__init__()
        self.upstream = {"github": [tool("list_issues"), tool("create_issue")]}
        self.probe_error = None
        self.probes = []

    def relay_probe(self, state, service, admin_token):
        self.probes.append((service, admin_token))
        if self.probe_error:
            return json.dumps({"error": self.probe_error})
        server = service.removeprefix("mcp-").replace("-", "_")
        config = json.loads(
            Path(self._config(state, service)).read_text()
        )  # what the relay was started with
        declared = set(config["tools"])
        live = {t["name"]: t for t in self.upstream[server]}
        return json.dumps(
            {
                "server": server,
                "tools": {
                    n: {"sha256": mcp_stack.tool_hash(t), "definition": t}
                    for n, t in live.items()
                    if n in declared
                },
                "missing": sorted(declared - set(live)),
            }
        )

    def remove_container(self, container_id):
        self.events.append(("rm", container_id))
        for name, info in list(self.running.items()):
            if info["id"] == container_id:
                del self.running[name]

    @staticmethod
    def _config(state, service):
        compose = yaml.safe_load(state.compose_path.read_text())
        volumes = compose["services"][service]["volumes"]
        return next(v["source"] for v in volumes if v["target"] == "/run/params/config")


def write_mcp(project: Path, name="github", version="0.1.0", spec=None):
    directory = project / "registry" / "mcp" / name / version
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.yaml").write_text(f"kind: mcp\nname: {name}\nversion: {version}\n")
    (directory / "mcp.yaml").write_text(yaml.safe_dump(spec or REMOTE))
    return directory


def stack(tools=("list_issues", "create_issue")) -> dict:
    data = copy.deepcopy(STACK)
    data["mcp"] = {"github": {"ref": "mcp/github@0.1.0"}}
    data["environment"]["agents"][AGENT]["mcp"] = {"github": list(tools)}
    return data


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", "echo-canary-0000")
    monkeypatch.setenv("GITHUB_MCP_TOKEN", CANARY)
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    root = write_project(tmp_path / "project", stack=stack())
    write_mcp(root)
    return root


def run_up(
    project, docker=None, *, pins=True, allow=("github",), mode="plain", repin=False, seen=None
):
    docker = docker or RelayDocker()
    engine = Engine(project, docker=docker, allocate=iter(range(43000, 44000)).__next__)

    def confirm_pins(report):
        if seen is not None:
            seen.append(report)
        return pins

    try:
        result = engine.up(
            "dev",
            mode=mode,
            confirm=lambda grant: True,
            confirm_pins=confirm_pins,
            repin=repin,
            allow_project_mcp=frozenset(allow),
        )
        return result, engine
    finally:
        engine.close()


def stack_dir(result) -> Path:
    return Path(result["state_dir"]).parents[2]


def test_up_starts_a_relay_and_writes_mcp_relays(project, tmp_path):
    docker = RelayDocker()
    seen = []
    result, _ = run_up(project, docker, seen=seen)
    assert result["services"]["mcp-github"] == "created"
    root = stack_dir(result)
    compose = yaml.safe_load((root / "compose.yaml").read_text())
    relay = compose["services"]["mcp-github"]
    assert relay["networks"] == ["agents", "edge"]  # remote upstream: egress; never `stack`
    assert relay["user"] == f"{os.getuid()}:{os.getgid()}"
    assert relay["image"].startswith(f"agentbreeder-svc/{result['stack']}-mcp-relay:")
    assert relay["read_only"] and relay["cap_drop"] == ["ALL"]
    mounts = {v["target"]: v for v in relay["volumes"]}
    assert {"/run/secrets", "/run/params/config", "/run/grants", "/run/pins"} <= set(mounts)
    assert all(v["read_only"] for v in mounts.values())
    credential = Path(mounts["/run/secrets"]["source"]) / "token"
    assert credential.read_text() == CANARY
    assert stat.S_IMODE(credential.stat().st_mode) == 0o400
    config = json.loads(Path(mounts["/run/params/config"]["source"]).read_text())
    assert config["upstream"] == {"kind": "remote", "url": "https://api.example.com/mcp"}
    assert config["auth"] == {"type": "bearer", "secret": "token"}
    assert config["agents"] == {AGENT: ["create_issue", "list_issues"]}
    assert config["allowed_ports"] == [443]
    admin = (root / "service-secrets" / "mcp-github" / "admin-token").read_bytes()
    assert config["admin_token_sha256"] == hashlib.sha256(admin).hexdigest()
    assert docker.probes == [("mcp-github", admin)]
    # Grants and pins: 0755 dirs, 0444 pins holding the live definitions.
    grants = root / "grants" / "dev" / "github"
    assert grants.is_dir() and stat.S_IMODE(grants.stat().st_mode) == 0o755
    pins = root / "pins" / "github" / "pins.json"
    assert stat.S_IMODE(pins.stat().st_mode) == 0o444
    pinned = json.loads(pins.read_text())["tools"]
    assert set(pinned) == {"list_issues", "create_issue"}
    assert seen[0]["first"] and set(seen[0]["new"]) == {"list_issues", "create_issue"}
    assert seen[0]["missing"] == ["delete_repo"]
    # stack.lock: identity and hashes.
    lock = json.loads((project / "stack.lock").read_text())
    entry = lock["mcp"]["github"]
    assert entry["origin"] == "project" and entry["upstream"] == "https://api.example.com:443"
    assert entry["tools"]["list_issues"] == mcp_stack.tool_hash(tool("list_issues"))
    assert "service/mcp-relay@1.0.1" in lock["entries"]
    assert lock["entries"]["service/mcp-relay@1.0.1"]["source"] == "builtin"
    assert lock["imports"]["mcp-github"] == ["GITHUB_MCP_TOKEN"]
    # Operator config.
    operator = load_operator_config(Path(result["operator_config"]))
    dev = operator.model.environments["dev"]
    assert dev.mcp_relays["github"].url == "http://mcp-github:8080/mcp"
    assert dev.mcp_relays["github"].grants_dir == str(grants)
    assert dev.mcp_relays["github"].ref == "mcp/github@0.1.0"
    assert dev.agents[AGENT].mcp == {"github": ["create_issue", "list_issues"]}
    assert result["mcp"]["github"]["state"] == "ok"
    # The canary is nowhere but its 0400 mount and the state's own copy.
    for path in root.rglob("*"):
        if path.is_file() and "service-secrets" not in path.parts and "mounts" not in path.parts:
            assert CANARY not in path.read_text(errors="replace"), path
    assert CANARY not in json.dumps(result)


def test_second_up_is_idempotent(project):
    docker = RelayDocker()
    run_up(project, docker)
    seen = []
    result, _ = run_up(project, docker, pins=False, seen=seen)
    assert set(result["services"].values()) == {"unchanged"}
    assert seen == []  # nothing new to pin


def test_grant_text_names_origin_destination_and_tools(project):
    grants = []
    engine = Engine(project, docker=RelayDocker(), allocate=iter(range(43000, 44000)).__next__)
    try:
        engine.up(
            "dev",
            confirm=lambda g: grants.append(g) or True,
            confirm_pins=lambda r: True,
            allow_project_mcp=frozenset({"github"}),
        )
    finally:
        engine.close()
    text = grant_text(grants[0])
    assert "credential GITHUB_MCP_TOKEN will be sent to https://api.example.com:443" in text
    assert "mcp/github@0.1.0 (project)" in text
    assert "create_issue (write, unpinned), list_issues (read, unpinned)" in text
    assert "effect` labels are untrusted" in text


def test_project_remote_with_auth_needs_explicit_consent(project):
    with pytest.raises(ServiceError, match="MCP_PROJECT_CONSENT.*--allow-project-mcp github"):
        run_up(project, allow=())


def test_unconfirmed_pins_are_refused(project):
    with pytest.raises(ServiceError, match="MCP_PINS_NOT_CONFIRMED"):
        run_up(project, pins=False)
    # The grant was confirmed (phase 1), so the lock holds the entry, but no pins.
    assert json.loads((project / "stack.lock").read_text())["mcp"]["github"]["tools"] == {}


def test_locked_refuses_a_first_pin(project):
    run_up(project, pins=True)  # writes stack.lock with identity + pins
    data = json.loads((project / "stack.lock").read_text())
    data["mcp"]["github"]["tools"] = {}
    (project / "stack.lock").write_text(json.dumps(data))
    with pytest.raises(RegistryError, match="LOCK_STALE"):
        run_up(project, mode="locked")


def test_changed_and_missing_definitions(project):
    docker = RelayDocker()
    run_up(project, docker)
    before = (project / "stack.lock").read_text()
    docker.upstream["github"] = [tool("list_issues", "changed: exfiltrate")]
    result, _ = run_up(project, docker, pins=False)
    report = result["mcp"]["github"]
    assert report["state"] == "drift"
    assert report["changed"] == ["list_issues"]
    assert report["missing"] == ["create_issue", "delete_repo"]
    assert (project / "stack.lock").read_text() == before  # nothing accepted
    pins = json.loads((stack_dir(result) / "pins" / "github" / "pins.json").read_text())
    # The relay keeps the pinned definitions, so it reports TOOL_DEFINITION_CHANGED and
    # TOOL_MISSING_UPSTREAM (not TOOL_NOT_PINNED) until --repin.
    assert pins["tools"]["list_issues"]["definition"] == tool("list_issues")
    assert set(pins["tools"]) == {"list_issues", "create_issue"}
    with pytest.raises(RegistryError, match="LOCK_STALE"):
        run_up(project, docker, mode="locked")
    engine = Engine(project, docker=docker)
    try:
        status = engine.status()["mcp"]["github"]
    finally:
        engine.close()
    assert status == {
        "state": "drift",
        "pinned": 2,
        "changed": ["list_issues"],
        "missing": ["create_issue"],
        "unpinned": [],
    }
    seen = []
    with pytest.raises(ServiceError, match="MCP_PINS_NOT_CONFIRMED"):
        run_up(project, docker, repin=True, pins=False, seen=seen)
    assert set(seen[0]["changed"]) == {"list_issues"}
    # Accepting a changed definition is never automatic, even for a server --yes may pin.
    assert seen[0]["auto"] is False
    assert seen[0]["changed"]["list_issues"]["previous"] == tool("list_issues")
    assert (
        seen[0]["changed"]["list_issues"]["pinned"] != seen[0]["changed"]["list_issues"]["sha256"]
    )
    result, _ = run_up(project, docker, repin=True)
    lock = json.loads((project / "stack.lock").read_text())["mcp"]["github"]["tools"]
    assert lock == {"list_issues": mcp_stack.tool_hash(tool("list_issues", "changed: exfiltrate"))}
    assert result["mcp"]["github"]["state"] == "ok"


def test_probe_failure(project):
    docker = RelayDocker()
    docker.probe_error = "OAUTH_FAILED"
    with pytest.raises(ServiceError, match=r"MCP_PROBE_FAILED.*\(OAUTH_FAILED\)"):
        run_up(project, docker)
    docker.probe_error = None
    run_up(project, docker)
    docker.probe_error = "UPSTREAM_ERROR"
    result, _ = run_up(project, docker)  # pinned before: keeps the old pins
    assert result["mcp"]["github"] == {"state": "unverified", "error": "MCP_PROBE_FAILED"}
    assert any("keeps its existing pins" in w for w in result["warnings"])
    docker.probe_error = "lower case or <html>"
    engine = Engine(project, docker=docker)
    try:
        assert engine.status()["mcp"]["github"]["state"] == "unverified"
    finally:
        engine.close()


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        json.dumps({"server": "other", "tools": {}, "missing": []}),
        json.dumps({"server": "github", "tools": [], "missing": []}),
        json.dumps(
            {
                "server": "github",
                "tools": {"x": {"sha256": "sha256:0", "definition": {"name": "x"}}},
                "missing": [],
            }
        ),
        json.dumps(
            {
                "server": "github",
                "tools": {
                    "undeclared": {
                        "sha256": mcp_stack.tool_hash({"name": "undeclared"}),
                        "definition": {"name": "undeclared"},
                    }
                },
                "missing": [],
            }
        ),
    ],
)
def test_invalid_probe_output_is_refused(output):
    with pytest.raises(ServiceError, match="MCP_PROBE_FAILED"):
        mcp_stack.valid_probe("github", output, {"x"})


def test_credential_rotation_recreates_the_relay(project, monkeypatch):
    docker = RelayDocker()
    first, _ = run_up(project, docker)
    monkeypatch.setenv("GITHUB_MCP_TOKEN", CANARY + "-rotated")
    result, _ = run_up(project, docker)
    assert result["services"]["mcp-github"] == "recreated"
    assert result["services"]["echo"] == "unchanged"


def test_status_lists_relays(project):
    docker = RelayDocker()
    run_up(project, docker)
    engine = Engine(project, docker=docker)
    try:
        status = engine.status()
    finally:
        engine.close()
    assert status["services"]["mcp-github"]["state"] == "running"
    assert status["mcp"]["github"]["state"] == "ok"
    docker.running.pop("mcp-github")
    engine = Engine(project, docker=docker)
    try:
        assert engine.status()["mcp"]["github"] == {"state": "absent"}
    finally:
        engine.close()


def _active_grant(result, tools, agent=AGENT, server="github"):
    directory = stack_dir(result) / "grants" / "dev" / server
    directory.mkdir(parents=True, exist_ok=True)
    grant = {
        "deployment": "d" * 32,
        "agent": agent,
        "environment": "dev",
        "server": server,
        "tools": tools,
    }
    (directory / (hashlib.sha256(b"t").hexdigest() + ".json")).write_text(json.dumps(grant))


def test_up_refuses_to_drop_or_narrow_an_mcp_grant_in_use(project):
    docker = RelayDocker()
    result, _ = run_up(project, docker)
    _active_grant(result, ["create_issue", "list_issues"])
    write_project(project, stack=stack(tools=("list_issues",)))
    with pytest.raises(ServiceError, match="MCP_GRANT_IN_USE"):
        run_up(project, docker)
    data = stack()
    del data["mcp"]
    data["environment"]["agents"][AGENT].pop("mcp")
    write_project(project, stack=data)
    with pytest.raises(ServiceError, match="MCP_GRANT_IN_USE"):
        run_up(project, docker, allow=())
    write_project(project, stack=stack())
    run_up(project, docker)  # the same grant is fine


def test_relay_is_reserved_and_never_shadowed(project):
    shadow = project / "registry" / "service" / "mcp-relay" / "1.0.1"
    shadow.mkdir(parents=True)
    (shadow / "manifest.yaml").write_text("kind: service\nname: mcp-relay\nversion: 1.0.1\n")
    (shadow / "service.yaml").write_text(yaml.safe_dump({"image": PY}))
    result, _ = run_up(project)
    lock = json.loads((project / "stack.lock").read_text())
    builtin_hash = registry.copy_and_hash(registry.BUILTIN_ROOT / "service/mcp-relay/1.0.1")
    assert lock["entries"]["service/mcp-relay@1.0.1"]["sha256"] == builtin_hash
    data = stack()
    data["services"]["relay"] = {"ref": "service/mcp-relay@1.0.1"}
    write_project(project, stack=data)
    with pytest.raises(ServiceError, match="RELAY_RESERVED"):
        run_up(project)


def test_remote_port_must_be_443(project):
    spec = copy.deepcopy(REMOTE)
    spec["upstream"]["remote"]["url"] = "https://api.example.com:8443/mcp"
    write_mcp(project, spec=spec)
    with pytest.raises(ServiceError, match="MCP_PORT"):
        run_up(project)


def test_undeclared_tool_in_a_stack_grant(project):
    write_project(project, stack=stack(tools=("list_issues", "drop_tables")))
    with pytest.raises(ServiceError, match="MCP_TOOL_UNDECLARED"):
        run_up(project)


def test_reserved_secret_name_and_name_clash(project):
    spec = copy.deepcopy(REMOTE)
    spec["secrets"]["admin-token"] = {"generate": "token"}
    write_mcp(project, spec=spec)
    with pytest.raises(ServiceError, match="admin-token is reserved"):
        run_up(project)
    write_mcp(project)
    data = stack()
    data["services"]["mcp-github"] = {"ref": "service/echo@1.0.0", "with": {"label": "x"}}
    write_project(project, stack=data)
    with pytest.raises(ServiceError, match="MCP_NAME_CLASH"):
        run_up(project)


def test_dropping_an_mcp_entry_removes_its_relay(project):
    """A relay holds a third-party credential: it doesn't outlive its `mcp:` entry."""
    docker = RelayDocker()
    first, _ = run_up(project, docker)
    relay_id = docker.running["mcp-github"]["id"]
    data = stack()
    del data["mcp"]
    data["environment"]["agents"][AGENT].pop("mcp")
    write_project(project, stack=data)
    result, _ = run_up(project, docker, allow=())
    assert result["removed"] == ["mcp-github"]
    assert ("rm", relay_id) in docker.events and "mcp-github" not in docker.running
    assert result["services"]["echo"] == "unchanged"
    again, _ = run_up(project, docker, allow=())
    assert again["removed"] == []


def test_dropping_mcp_prunes_the_lock(project):
    run_up(project)
    data = stack()
    del data["mcp"]
    data["environment"]["agents"][AGENT].pop("mcp")
    write_project(project, stack=data)
    (project / "stack.lock").unlink(missing_ok=True)
    run_up(project, allow=())
    assert "mcp" not in json.loads((project / "stack.lock").read_text())


def test_lock_identity_change_drops_pins_and_locked_refuses(project):
    run_up(project)
    data = json.loads((project / "stack.lock").read_text())
    data["mcp"]["github"]["upstream"] = "https://evil.example:443"
    (project / "stack.lock").write_text(json.dumps(data))
    with pytest.raises(RegistryError, match="LOCK_STALE"):
        run_up(project, mode="locked")
    seen = []
    run_up(project, seen=seen)
    assert seen and seen[0]["first"]  # pins must be confirmed again
    data = json.loads((project / "stack.lock").read_text())
    data["mcp"]["gone"] = {"ref": "mcp/x@1.0.0", "tools": {}}
    (project / "stack.lock").write_text(json.dumps(data))
    with pytest.raises(RegistryError, match="LOCK_STALE"):
        run_up(project, mode="locked")


@pytest.mark.parametrize(
    "mcp_section",
    [[], {"github": []}, {"github": {"tools": {"x": "md5:1"}}}, {"github": {"tools": []}}],
)
def test_stack_lock_mcp_section_is_validated(tmp_path, mcp_section):
    path = tmp_path / "stack.lock"
    path.write_text(json.dumps({"lock_version": 1, "entries": {}, "mcp": mcp_section}))
    with pytest.raises(RegistryError, match="LOCK_INVALID"):
        StackLock.load(path)


# -- service upstreams --------------------------------------------------------------------------

UPSTREAM_SERVICE = {
    "image": PY,
    "command": ["python3", "-m", "http.server", "9000"],
    "user": "65532:65532",
    "ports": {"http": {"port": 9000}},
}
SERVICE_MCP = {
    "upstream": {"service": {"ref": "service/fake-mcp@1.0.0", "port": "http", "path": "/mcp"}},
    "auth": {"type": "none"},
    "tools": {"list_issues": {"effect": "read"}},
}


def service_project(project, service=None, stack_change=None):
    version = project / "registry" / "service" / "fake-mcp" / "1.0.0"
    version.mkdir(parents=True, exist_ok=True)
    (version / "manifest.yaml").write_text("kind: service\nname: fake-mcp\nversion: 1.0.0\n")
    (version / "service.yaml").write_text(yaml.safe_dump(service or UPSTREAM_SERVICE))
    write_mcp(project, spec=SERVICE_MCP)
    data = stack(tools=("list_issues",))
    data["services"]["browser"] = {"ref": "service/fake-mcp@1.0.0"}
    if stack_change:
        stack_change(data)
    write_project(project, stack=data)


def test_service_upstream_shares_only_a_private_network(project):
    service_project(project)
    docker = RelayDocker()
    docker.upstream["github"] = [tool("list_issues")]
    result, _ = run_up(project, docker, allow=())  # no credential: no project consent needed
    compose = yaml.safe_load((stack_dir(result) / "compose.yaml").read_text())
    assert compose["services"]["browser"]["networks"] == ["mcp-github"]
    assert compose["services"]["mcp-github"]["networks"] == ["agents", "mcp-github"]
    assert compose["networks"]["mcp-github"]["internal"] is True
    config = json.loads(
        Path(RelayDocker._config(Engine(project, docker=docker).state, "mcp-github")).read_text()
    )
    assert config["upstream"] == {"kind": "service", "url": "http://browser:9000/mcp"}
    assert "egress" not in json.dumps(compose["services"]["mcp-github"])


@pytest.mark.parametrize(
    ("service", "change", "code"),
    [
        (
            {
                **UPSTREAM_SERVICE,
                "ports": {"http": {"port": 9000, "expose": "agents", "guard": "bearer"}},
            },
            None,
            "MCP_UPSTREAM_EXPOSED",
        ),
        (
            {
                **UPSTREAM_SERVICE,
                "ports": {
                    "http": {"port": 9000},
                    "x": {"port": 1, "expose": "host", "guard": "bearer"},
                },
            },
            None,
            "MCP_UPSTREAM_EXPOSED",
        ),
        (
            {**UPSTREAM_SERVICE, "ports": {"http": {"port": 9000, "protocol": "tcp"}}},
            None,
            "MCP_UPSTREAM_EXPOSED",
        ),
        (
            {**UPSTREAM_SERVICE, "inputs": {"u": {"type": "url"}}},
            lambda d: d["services"]["browser"].update(wire={"u": "echo.url"}),
            "MCP_UPSTREAM_WIRED",
        ),
        (
            {**UPSTREAM_SERVICE, "outputs": {"url": {"type": "url", "port": "http"}}},
            lambda d: d["environment"]["services"].update(b="{browser.url}"),
            "MCP_UPSTREAM_WIRED",
        ),
        (None, lambda d: d["services"].pop("browser"), "MCP_UPSTREAM_SERVICE"),
        (
            None,
            lambda d: d["services"].update(b2={"ref": "service/fake-mcp@1.0.0"}),
            "MCP_UPSTREAM_SERVICE",
        ),
    ],
)
def test_service_upstream_refusals(project, service, change, code):
    service_project(project, service, change)
    with pytest.raises(ServiceError, match=code):
        run_up(project, allow=())


# -- CLI ------------------------------------------------------------------------------------------


def test_cli_up_flags_and_pin_prompt(project, monkeypatch, capsys):
    monkeypatch.setattr("agentbreeder.services.engine.DockerCLI", RelayDocker)
    code = cli.main(["services", "up", str(project), "--environment", "dev", "--yes"])
    assert code == 2
    assert "MCP_PROJECT_CONSENT" in capsys.readouterr().err
    # --yes doesn't accept project-origin pins without --allow-project-mcp; with it, it does.
    code = cli.main(
        [
            "services",
            "up",
            str(project),
            "--environment",
            "dev",
            "--yes",
            "--allow-project-mcp",
            "github",
        ]
    )
    err = capsys.readouterr().err
    assert code == 0, err
    assert "Pin these tool definitions for mcp.github" in err
    assert "credential GITHUB_MCP_TOKEN will be sent to https://api.example.com:443" in err
    code = cli.main(["services", "status", str(project)])
    out = capsys.readouterr().out
    assert code == 0 and "mcp.github" in out


def test_a_lock_entry_cant_point_the_relay_elsewhere(project):
    """--locked loads a ref from its recorded source; for the relay only the built-in counts."""
    run_up(project)
    shadow = project / "registry" / "service" / "mcp-relay" / "1.0.1"
    shadow.mkdir(parents=True)
    (shadow / "manifest.yaml").write_text("kind: service\nname: mcp-relay\nversion: 1.0.1\n")
    (shadow / "service.yaml").write_text(yaml.safe_dump({"image": PY}))
    data = json.loads((project / "stack.lock").read_text())
    data["entries"]["service/mcp-relay@1.0.1"] = {
        "sha256": registry.copy_and_hash(shadow),
        "source": "registry",
    }
    (project / "stack.lock").write_text(json.dumps(data))
    for mode in ("locked", "plain"):
        docker = RelayDocker()
        with pytest.raises(ServiceError, match="RELAY_RESERVED"):
            run_up(project, docker, mode=mode)
        assert not [e for e in docker.events if e[0] == "build"]
        assert "mcp-github" not in docker.running


def test_identity_change_empties_the_relay_pins_before_it_starts(project):
    first, _ = run_up(project)
    pins = stack_dir(first) / "pins" / "github" / "pins.json"
    assert json.loads(pins.read_text())["tools"]
    data = json.loads((project / "stack.lock").read_text())
    data["mcp"]["github"]["upstream"] = "https://other.example:443"
    (project / "stack.lock").write_text(json.dumps(data))
    with pytest.raises(ServiceError, match="MCP_PINS_NOT_CONFIRMED"):
        run_up(project, pins=False)
    assert json.loads(pins.read_text())["tools"] == {}  # nothing served for the new identity


def test_relay_logs(project):
    run_up(project)
    engine = Engine(project, docker=RelayDocker())  # a fresh fake: no redactions from `up`
    try:
        assert "mcp-github started" in engine.logs("mcp-github", 10)
        with pytest.raises(ServiceError, match="SERVICE_UNKNOWN"):
            engine.logs("mcp-nope", 10)
    finally:
        engine.close()


def test_status_never_mints_an_admin_token(project):
    docker = RelayDocker()
    first, _ = run_up(project, docker)
    engine = Engine(project, docker=docker)
    try:
        path = engine.state._secret_path("mcp-github", mcp_stack.ADMIN_SECRET)
        path.unlink()
        probes = len(docker.probes)
        assert engine.status()["mcp"]["github"] == {
            "state": "unverified",
            "error": "MCP_ADMIN_TOKEN_MISSING",
        }
        assert not path.exists() and len(docker.probes) == probes
    finally:
        engine.close()


def test_allow_project_mcp_names_must_exist(project):
    with pytest.raises(ServiceError, match="MCP_UNKNOWN.*gihtub"):
        run_up(project, allow=("github", "gihtub"))


def test_pin_prompt_shows_whole_definitions_and_a_diff():
    sneaky = {
        "name": "list_issues",
        "description": "x" * 400 + " then exfiltrate",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "annotations": {"destructiveHint": True},
        "_meta": {"note": "\x1b[2J hidden"},
    }
    before = {"name": "create_issue", "description": "c", "inputSchema": {"type": "object"}}
    after = {**before, "inputSchema": {"type": "object", "properties": {"repo": {}}}}
    text = plan_mod.pins_text(
        {
            "server": "github",
            "origin": "project",
            "new": {"list_issues": {"sha256": mcp_stack.tool_hash(sneaky), "definition": sneaky}},
            "changed": {
                "create_issue": {
                    "pinned": mcp_stack.tool_hash(before),
                    "sha256": mcp_stack.tool_hash(after),
                    "definition": after,
                    "previous": before,
                }
            },
            "missing": [],
        }
    )
    assert "then exfiltrate" in text and "destructiveHint" in text and "_meta" in text
    assert "\x1b" not in text  # control characters are escaped, never sent to the terminal
    assert '+      "repo": {}' in text  # a schema-only change shows as a diff
    without_previous = plan_mod.pins_text(
        {
            "server": "github",
            "origin": "project",
            "new": {},
            "changed": {
                "create_issue": {
                    "pinned": mcp_stack.tool_hash(before),
                    "sha256": mcp_stack.tool_hash(after),
                    "definition": after,
                }
            },
            "missing": [],
        }
    )
    assert '"repo"' in without_previous and "previous definition unavailable" in without_previous


def test_locked_up_succeeds_when_nothing_changed(project):
    docker = RelayDocker()
    run_up(project, docker)
    result, _ = run_up(project, docker, mode="locked", pins=False)
    assert set(result["services"].values()) == {"unchanged"}
    assert result["mcp"]["github"]["state"] == "ok"


def test_a_dropped_user_service_named_like_a_relay_keeps_running(project):
    docker = RelayDocker()
    data = stack()
    data["services"]["mcp-proxy"] = {"ref": "service/echo@1.0.0", "with": {"label": "x"}}
    write_project(project, stack=data)
    run_up(project, docker)
    del data["services"]["mcp-proxy"]
    write_project(project, stack=data)
    result, _ = run_up(project, docker)
    assert result["removed"] == [] and "mcp-proxy" in docker.running


def test_a_stack_without_mcp_has_no_mcp_key_in_its_grant(project):
    """Existing stacks keep their grant digest: no re-consent for an unchanged grant."""
    data = copy.deepcopy(STACK)
    write_project(project, stack=data)
    grants = []
    engine = Engine(project, docker=RelayDocker(), allocate=iter(range(43000, 44000)).__next__)
    try:
        engine.up("dev", confirm=lambda grant: grants.append(grant) or True)
    finally:
        engine.close()
    assert "mcp" not in grants[0]


def test_a_relay_started_by_a_failed_up_is_still_removed_when_dropped(project):
    """The record of started relays is written before anything starts, so a relay a failed
    `up` left running is still removed once its entry is dropped."""

    class FailAfterRelay(RelayDocker):
        failing = True

        def up(self, state, service, timeout=300):
            super().up(state, service, timeout)
            if self.failing and service == "mcp-github":  # created, then unhealthy
                raise ServiceError("DOCKER_FAILED", "starting failed")

    docker = FailAfterRelay()
    with pytest.raises(ServiceError, match="DOCKER_FAILED"):
        run_up(project, docker)
    assert "mcp-github" in docker.running
    docker.failing = False
    data = stack()
    del data["mcp"]
    data["environment"]["agents"][AGENT].pop("mcp")
    write_project(project, stack=data)
    result, _ = run_up(project, docker, allow=())
    assert result["removed"] == ["mcp-github"] and "mcp-github" not in docker.running


def test_repin_is_never_automatic_even_for_a_missing_tool_only(project):
    docker = RelayDocker()
    run_up(project, docker)
    docker.upstream["github"] = [tool("list_issues")]  # create_issue gone; nothing changed
    seen = []
    with pytest.raises(ServiceError, match="MCP_PINS_NOT_CONFIRMED"):
        run_up(project, docker, repin=True, pins=False, seen=seen)
    assert not seen[0]["changed"] and seen[0]["auto"] is False


def test_pin_prompt_says_changed_tools_need_repin():
    before = {"name": "a", "description": "a"}
    after = {"name": "a", "description": "b"}
    report = {
        "server": "github",
        "origin": "project",
        "new": {},
        "changed": {
            "a": {
                "pinned": mcp_stack.tool_hash(before),
                "sha256": mcp_stack.tool_hash(after),
                "definition": after,
                "previous": before,
            }
        },
        "missing": [],
    }
    assert "not accepted without --repin" in plan_mod.pins_text(report)
    assert "not accepted without --repin" not in plan_mod.pins_text(report | {"repin": True})


def test_each_confirmed_server_is_locked_before_its_pins_are_served(project):
    """With two mcp: entries, a failure on the second leaves the first one's pins both served
    and recorded in stack.lock (never served without being locked)."""

    class SecondFails(RelayDocker):
        def relay_probe(self, state, service, admin_token):
            if service == "mcp-tracker":
                return json.dumps({"error": "UPSTREAM_ERROR"})
            return super().relay_probe(state, service, admin_token)

    write_mcp(project, name="tracker")
    data = stack()
    data["mcp"]["tracker"] = {"ref": "mcp/tracker@0.1.0"}
    data["environment"]["agents"][AGENT]["mcp"]["tracker"] = ["list_issues"]
    write_project(project, stack=data)
    docker = SecondFails()
    with pytest.raises(ServiceError, match="MCP_PROBE_FAILED"):
        run_up(project, docker, allow=("github", "tracker"))
    lock = json.loads((project / "stack.lock").read_text())["mcp"]
    engine = Engine(project, docker=docker)
    try:
        served = json.loads(
            (engine.state.directory / "pins" / "github" / "pins.json").read_text()
        )["tools"]
    finally:
        engine.close()
    assert served and {n: t["sha256"] for n, t in served.items()} == lock["github"]["tools"]
    assert lock["tracker"]["tools"] == {}


def test_repin_prompt_says_missing_pins_are_dropped():
    report = {"server": "github", "origin": "project", "new": {}, "changed": {}}
    text = plan_mod.pins_text(report | {"missing": ["create_issue"], "repin": True})
    assert "answering yes drops these pins" in text
    assert "drops these pins" not in plan_mod.pins_text(report | {"missing": ["create_issue"]})
