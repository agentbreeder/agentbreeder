# ruff: noqa: F811  (the imported test_build fixtures are parameters here)
"""`agentbreeder build` with a git registry source (#829 slice 1b acceptance, spec §11.1):
a tool from a separate git repository builds into an agent; a force-push after locking does
not change a `--locked` build; a stopped server is SOURCE_UNREACHABLE; an untrusted host is
SOURCE_HOST_UNTRUSTED. The image build is faked (no Docker); the git server is git's own
`http-backend` on loopback HTTPS."""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from agentbreeder import build as build_module
from agentbreeder import cli
from agentbreeder.build import build
from agentbreeder.registry import RegistryError
from agentbreeder.sources import git as git_source
from tests.git_source_support import BareRepo, GitServer, needs_git, trust_file
from tests.test_build import IMAGE, FakeBuilder, operator, project  # noqa: F401 (imported fixtures)

pytestmark = needs_git


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTBREEDER_CACHE_DIR", str(home / ".agentbreeder" / "cache"))
    monkeypatch.delenv("AGENTBREEDER_REGISTRY_TRUST", raising=False)
    return home


@pytest.fixture
def org(tmp_path, project, home, monkeypatch):
    """Move tool/orders out of the project into a separate git repository, and declare it."""
    repo = BareRepo(tmp_path / "srv" / "acme" / "agent-registry.git")
    repo.version("tool", "orders", "1.0.0", {"tool.yaml": "functions: []\n", "src/orders.py": ""})
    repo.pinned = repo.commit("publish orders")
    shutil.rmtree(project / "registry" / "tool")
    server = GitServer(tmp_path / "srv", tmp_path / "certs")
    monkeypatch.setenv("GIT_SSL_CAINFO", server.ca)
    trust_file(home, {"url": f"https://127.0.0.1:{server.port}/acme", "private": True})
    source = {
        "name": "acme",
        "git": server.url("acme/agent-registry.git"),
        "commit": repo.pinned,
        "path": "registry",
    }
    with open(project / "agent.yaml", "a") as handle:
        handle.write(yaml.safe_dump({"registries": [source]}))
    repo.server = server
    yield repo
    server.stop()


def test_a_tool_from_a_separate_git_repository_builds(project, operator, org):
    builder = FakeBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=builder)
    listing, _ = builder.contexts[0]
    assert "artifacts/tool/orders/src/orders.py" in listing
    lock = json.loads((project / "agent.lock").read_text())
    url = org.server.url("acme/agent-registry.git")
    assert lock["lock_version"] == 2
    assert lock["sources"] == {
        "acme": {"type": "git", "url": url, "pin": {"commit": org.pinned}, "path": "registry"}
    }
    assert lock["entries"]["tool/orders@1.0.0"]["source"] == "remote:acme"
    assert lock["entries"]["prompt/helper@1.0.0"]["source"] == "registry"
    record = json.loads(result.build_record_path.read_text())
    assert record["sources"] == lock["sources"]

    # Force-push a different tool/orders, then build --locked with the server stopped.
    org.version("tool", "orders", "1.0.0", {"tool.yaml": "functions: []\n", "src/orders.py": "x"})
    org.force(org.commit("rewrite", parent=None))
    org.server.stop()
    before = (project / "agent.lock").read_bytes()
    builder = FakeBuilder()
    build(
        project / "agent.yaml",
        operator.load(),
        "dev",
        mode="locked",
        offline=True,
        builder=builder,
    )
    assert (project / "agent.lock").read_bytes() == before


def test_a_stopped_server_and_an_untrusted_host(project, operator, org, home):
    build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    shutil.rmtree(home / ".agentbreeder" / "cache")  # a cold cache: CI, or another machine
    trusted = (home / ".agentbreeder" / "sources.yaml").read_text()
    trust_file(home)
    count = len(org.server.requests)
    for mode in ("plain", "locked"):
        with pytest.raises(RegistryError) as raised:
            build(project / "agent.yaml", operator.load(), "dev", mode=mode, builder=FakeBuilder())
        assert raised.value.code == "SOURCE_HOST_UNTRUSTED", mode
    assert len(org.server.requests) == count  # the untrusted builds sent nothing
    (home / ".agentbreeder" / "sources.yaml").write_text(trusted)
    org.server.stop()
    with pytest.raises(RegistryError) as raised:
        build(project / "agent.yaml", operator.load(), "dev", mode="locked", builder=FakeBuilder())
    assert raised.value.code == "SOURCE_UNREACHABLE" and "'acme'" in str(raised.value)


def test_cli_build_prints_provenance_and_offline(
    project,
    operator,
    org,
    monkeypatch,
    capsys,
):
    original = build_module.build

    def fake_build(*args, **kwargs):
        kwargs["builder"] = lambda c, i: IMAGE
        return original(*args, **kwargs)

    monkeypatch.setattr(build_module, "build", fake_build)
    operator.load()
    agent = str(project / "agent.yaml")
    assert cli.main(["build", agent, "--environment", "dev"]) == 0
    assert f"tool/orders@1.0.0 <- acme @{org.pinned[:7]}" in capsys.readouterr().out
    org.server.stop()
    assert cli.main(["build", agent, "--environment", "dev", "--locked", "--offline"]) == 0
    capsys.readouterr()
    (project / "agent.lock").unlink()
    assert cli.main(["build", agent, "--environment", "dev", "--offline"]) == 2
    assert "SOURCE_OFFLINE" in capsys.readouterr().err


def test_authentication_failures_are_source_auth_failed(tmp_path, home, monkeypatch):
    srv = GitServer(tmp_path / "srv", tmp_path / "certs", status=401)
    monkeypatch.setenv("GIT_SSL_CAINFO", srv.ca)
    trust_file(home, {"url": f"https://127.0.0.1:{srv.port}/acme", "private": True})
    try:
        with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
            repo.fetch(srv.url("acme/private.git"), "a" * 40)
        assert raised.value.code == "SOURCE_AUTH_FAILED"
        assert "git ls-remote" in str(raised.value)
    finally:
        srv.stop()
