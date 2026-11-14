"""The `agent` registry kind (M13 #763 slice 1b, spec §9): only release writes packages; list,
show, diff, verify and used-by read them."""

from __future__ import annotations

import importlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from agentbreeder import cli, package, registry, registry_deployed
from agentbreeder.agentfile import AgentFileError, load_agent_file
from agentbreeder.registry import Ref, RegistryError
from agentbreeder.services.schema import load_stack

package_tests = importlib.import_module("test_package")
registry_tests = importlib.import_module("test_registry")

NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
REF = Ref.parse("agent/support-bot@1.4.0")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


def staged(tmp_path: Path, version: str = "1.4.0", agent_yaml: bytes | None = None) -> Path:
    """An assembled package directory (as release stages it), outside any registry."""
    agent_yaml = package_tests.AGENT_YAML if agent_yaml is None else agent_yaml
    data = package_tests.record_data(**{"agent.version": version})
    data["inputs"]["agent_yaml_sha256"] = package_tests.sha(agent_yaml)
    record = package.parse(json.dumps(data).encode())
    tmp_path.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=tmp_path)) / version  # a fresh one per call
    package.assemble(staging, record, agent_yaml, package_tests.lock_bytes(), "Support bot")
    return staging


def released(project: Path, tmp_path: Path, version: str = "1.4.0", **kwargs) -> Path:
    ref, _, path = registry.publish(
        staged(tmp_path, version, **kwargs), project / "registry", package=True
    )
    assert str(ref) == f"agent/support-bot@{version}"
    return path


# -- the kind and refs ---------------------------------------------------------------------------


def test_agent_refs_parse_and_existing_refs_are_unchanged():
    assert Ref.parse("agent/support-bot@1.4.0") == Ref("agent", "support-bot", "1.4.0")
    for text in ("runtime/adk@1.1.0", "tool/orders@1.0.0", "mcp/github@1.0.0"):
        assert str(Ref.parse(text)) == text
    with pytest.raises(RegistryError, match=r"kinds: runtime, .*mcp, agent\)") as raised:
        Ref.parse("agents/x@1.0.0")
    assert raised.value.code == "REF_INVALID"
    with pytest.raises(RegistryError, match="REF_UNPINNED"):
        Ref.parse("agent/x@1.2")


def test_no_agent_file_or_stack_field_takes_an_agent_ref(tmp_path):
    data = yaml.safe_load((NORTHWIND / "agent.yaml").read_text())
    data["tools"][0]["ref"] = "agent/support-bot@1.4.0"
    (tmp_path / "agent.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(AgentFileError, match="must be a tool/name@X.Y.Z ref"):
        load_agent_file(tmp_path / "agent.yaml")
    data = yaml.safe_load((NORTHWIND / "agent.yaml").read_text())
    data["runtime"] = "agent/support-bot@1.4.0"
    (tmp_path / "agent.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(AgentFileError, match="must be a runtime/name@X.Y.Z ref"):
        load_agent_file(tmp_path / "agent.yaml")
    stack = yaml.safe_load((NORTHWIND / "stack.yaml").read_text())
    name = next(iter(stack["services"]))
    stack["services"][name]["ref"] = "agent/support-bot@1.4.0"
    (tmp_path / "stack.yaml").write_text(yaml.safe_dump(stack))
    with pytest.raises(Exception, match="must be a service/name@X.Y.Z ref"):
        load_stack(tmp_path / "stack.yaml")


def test_a_package_lock_copy_refuses_agent_keys():
    body = package_tests.lock_bytes(
        {REF.__str__(): {"sha256": "sha256:" + "6" * 64, "source": "registry"}}
    )
    with pytest.raises(package.PackageError, match="not a ref"):
        package.redact_lock(body)


# -- only release writes packages ----------------------------------------------------------------


def test_publish_import_fork_and_edit_refuse_packages(project, tmp_path):
    source = staged(tmp_path)
    for dry_run in (False, True):
        with pytest.raises(RegistryError) as raised:
            registry.place(source, project / "registry", dry_run=dry_run)
        assert raised.value.code == "PACKAGE_RELEASE_ONLY"
        assert "written by release" in str(raised.value)
    with pytest.raises(RegistryError, match="PACKAGE_RELEASE_ONLY"):
        registry.publish(source, project / "registry")
    with pytest.raises(RegistryError, match="PACKAGE_RELEASE_ONLY"):
        registry.import_version(source, project, version="2.0.0")
    assert not (project / "registry" / "agent").exists()
    released(project, tmp_path)
    with pytest.raises(RegistryError, match="PACKAGE_RELEASE_ONLY"):
        registry.fork_version(REF, project, version="1.5.0")
    with pytest.raises(RegistryError, match="PACKAGE_RELEASE_ONLY"):
        registry.fork_version(REF, project)
    with pytest.raises(RegistryError, match="PACKAGE_RELEASE_ONLY"):
        registry.edit_version(REF, project, sets=[("agent.yaml", b"name: x\n")])
    assert [row["ref"] for row in registry.listing(registry.roots(project))] == [str(REF)]


def test_the_cli_refuses_publish_and_scaffold(project, tmp_path, capsys):
    assert cli.main(["registry", "publish", str(staged(tmp_path))]) == 2
    assert (
        "[PACKAGE_RELEASE_ONLY] agent/ versions are written by release" in capsys.readouterr().err
    )
    with pytest.raises(SystemExit) as raised:
        cli.main(["registry", "new", "agent", "support-bot"])
    assert raised.value.code == 2
    assert (
        "[PACKAGE_RELEASE_ONLY] agent/ versions are written by release" in capsys.readouterr().err
    )
    assert not (project / "agent-support-bot").exists()
    with pytest.raises(RegistryError) as refused:
        registry.scaffold("agent", "support-bot", project)
    assert refused.value.code == "PACKAGE_RELEASE_ONLY"


def test_release_publish_checks_the_package(project, tmp_path):
    path = released(project, tmp_path)
    assert sorted(p.name for p in path.iterdir()) == sorted(package.FILES)
    # Not a package: any other kind, a broken package, or a renamed one.
    prompt = registry_tests.make_version(tmp_path / "src", "prompt", "p", "1.0.0", {"p.md": "x"})
    with pytest.raises(RegistryError) as raised:
        registry.publish(prompt, project / "registry", package=True)
    assert raised.value.code == "PACKAGE_INVALID"
    broken = staged(tmp_path, "1.5.0")
    (broken / "agent.yaml").write_bytes(b"name: other\n")
    with pytest.raises(RegistryError, match="agent.yaml differs"):
        registry.publish(broken, project / "registry", package=True)
    with pytest.raises(RegistryError, match="cannot be renamed"):
        registry.place(
            staged(tmp_path, "1.6.0"), project / "registry", version="1.7.0", package=True
        )
    with pytest.raises(RegistryError, match="VERSION_EXISTS"):
        registry.publish(staged(tmp_path / "again"), project / "registry", package=True)
    assert sorted(p.name for p in (project / "registry/agent/support-bot").iterdir()) == ["1.4.0"]


# -- read-only views ------------------------------------------------------------------------------


def test_list_and_verify_include_packages(project, tmp_path, capsys):
    path = released(project, tmp_path)
    search = registry.roots(project)
    assert [row["ref"] for row in registry.listing(search, "agent/")] == [str(REF)]
    assert registry.verify(search) == []
    assert cli.main(["registry", "list"]) == 0
    assert str(REF) in capsys.readouterr().out
    (path / "agent.yaml").write_bytes(b"name: changed\n")
    problems = registry.verify(search)
    assert [p["ref"] for p in problems] == [str(REF)]
    assert problems[0]["error"].startswith("[PACKAGE_INVALID]")
    assert "agent.yaml differs" in problems[0]["error"]
    (path / "agent.yaml").write_bytes(package_tests.AGENT_YAML)
    (path / "notes.txt").write_text("x")
    assert "exactly" in registry.verify(search)[0]["error"]
    assert cli.main(["registry", "verify"]) == 2
    assert f"FAIL {REF}: [PACKAGE_INVALID]" in capsys.readouterr().err


def test_show_renders_the_package_record(project, tmp_path, capsys):
    path = released(project, tmp_path)
    view = registry.show(REF, registry.roots(project), project)
    assert view["package_error"] is None
    assert view["package"] == json.loads((path / "package.json").read_bytes())
    assert [f["path"] for f in view["files"]] == sorted(package.FILES)
    assert view["sha256"] == registry.copy_and_hash(path)
    assert "package" not in registry.show(
        registry.Ref.parse("prompt/p@1.0.0"),
        [registry_tests.make_version(tmp_path / "r", "prompt", "p", "1.0.0", {}).parents[2]],
    )
    assert cli.main(["registry", "show", str(REF)]) == 0
    out = capsys.readouterr().out
    assert "Package:\n  Runtime: runtime/adk@1.1.0" in out
    assert "Evals: eval/support-smoke@1.0.0 passed 12/12" in out
    assert "--- agent.lock ---" in out and "--- agent.yaml ---" in out
    assert cli.main(["registry", "show", str(REF), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["package"]["agent"]["name"] == "support-bot"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: (p / "agent.yaml").write_bytes(b"name: changed\n"), "agent.yaml differs"),
        (lambda p: (p / "package.json").write_text("{}"), "package_version"),
        (lambda p: (p / "notes.txt").write_text("x"), "exactly"),
        (lambda p: (p / "agent.lock").write_bytes(b" " * (package.LOCK_LIMIT + 1)), "too large"),
    ],
)
def test_show_reports_an_invalid_package_without_failing(
    project, tmp_path, capsys, change, message
):
    path = released(project, tmp_path)
    change(path)
    view = registry.show(REF, registry.roots(project), project)
    assert view["package"] is None
    assert view["package_error"].startswith("[PACKAGE_INVALID]")
    assert message in view["package_error"]
    assert cli.main(["registry", "show", str(REF)]) == 0
    assert "Package: not valid: [PACKAGE_INVALID]" in capsys.readouterr().out


def test_show_does_not_echo_forged_package_values(project, tmp_path):
    path = released(project, tmp_path)
    data = json.loads((path / "package.json").read_bytes())
    data["image"]["platform"] = "linux/\x1b[31mred"
    (path / "package.json").write_text(json.dumps(data))
    view = registry.show(REF, registry.roots(project), project)
    assert view["package"] is None
    assert "\x1b" not in view["package_error"] and "red" not in view["package_error"]


def test_diff_between_package_versions(project, tmp_path):
    released(project, tmp_path)
    released(project, tmp_path, "1.5.0", agent_yaml=package_tests.AGENT_YAML + b"# v2\n")
    result = registry.diff(REF, Ref.parse("agent/support-bot@1.5.0"), registry.roots(project))
    changed = {f["path"] for f in result["files"]}
    assert changed == {"agent.yaml", "manifest.yaml", "package.json"}
    text = registry.unified(result)
    assert "+# v2" in text
    assert '-    "version": "1.4.0",' in text and '+    "version": "1.5.0",' in text


def test_used_by_links_no_deployment_to_a_package_yet(tmp_path):
    shutil.copy(NORTHWIND / "agent.yaml", tmp_path / "agent.yaml")
    row = registry.usage(Ref.parse("agent/northwind-support@0.1.0"), tmp_path)
    assert row["declared"] == [] and row["locked"] == []
    result = registry_deployed.deployed(
        Ref.parse("agent/northwind-support@0.1.0"), row, tmp_path, [tmp_path / "state"]
    )
    assert result["deployed"] == result["deployed_undetermined"] == []
    assert result["recorded_not_running"] == [] and result["errors"] == []
