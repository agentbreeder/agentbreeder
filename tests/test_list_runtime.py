"""`list --json` and `list --all --json` name each deployment's runtime version (multi-framework
spec §11, #1083 F2a): the manifest's `runtime`, else the project's build record or the linked
agent package for the same image, else null."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_list_all import make_stack

from agentbreeder import allstacks, cli
from agentbreeder.store import Store

IMAGE = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
ADK = "runtime/adk@1.4.0"
KIT = "runtime/langgraph@1.0.0"


def deploy(state: Path, name: str, image: str = IMAGE, package=None, **manifest) -> None:
    store = Store(state)
    try:
        store.create(
            f"{name}-id",
            {"name": name, "image": image, **manifest},
            "digest",
            "context",
            package=package,
        )
        store.transition(f"{name}-id", "running", endpoint="http://127.0.0.1:9000")
    finally:
        store.close()


def record(project: Path, agent: str, runtime=ADK, image: str = IMAGE, **extra) -> Path:
    (project / "deploy").mkdir(parents=True, exist_ok=True)
    path = project / "deploy" / f"{agent}.build.json"
    path.write_text(json.dumps({"name": agent, "image": image, "runtime": runtime, **extra}))
    return path


def package(project: Path, name: str, version: str, runtime=ADK, image: str = IMAGE) -> dict:
    directory = project / "registry" / "agent" / name / version
    directory.mkdir(parents=True)
    data = {
        "agent": {"name": name, "version": version, "runtime": runtime},
        "image": {"id": image},
    }
    (directory / "package.json").write_text(json.dumps(data))
    return {"ref": f"agent/{name}@{version}", "sha256": "sha256:" + "0" * 64}


def listed(capsys, *arguments) -> list[dict]:
    assert cli.main(["list", "--json", *arguments]) == 0
    result = json.loads(capsys.readouterr().out)
    return result["deployments"] if isinstance(result, dict) else result


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(tmp_path / "stacks"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return tmp_path / "local", project


def test_the_manifest_runtime_comes_first(state, capsys):
    local, project = state
    record(project, "kit-agent", runtime=ADK)
    deploy(local, "kit-agent", runtime={"ref": KIT, "profile": "kit"})
    (row,) = listed(capsys, "--state-dir", str(local))
    assert row["runtime"] == KIT and row["image"] == IMAGE


def test_the_build_record_for_the_same_image_names_it(state, capsys):
    local, project = state
    record(project, "support")
    deploy(local, "support")
    deploy(local, "support-candidate")  # release's candidate runs the same build
    deploy(local, "rebuilt", image=OTHER)
    record(project, "rebuilt")  # for another image
    deploy(local, "stranger")
    rows = {row["name"]: row["runtime"] for row in listed(capsys, "--state-dir", str(local))}
    assert rows == {"support": ADK, "support-candidate": ADK, "rebuilt": None, "stranger": None}


def test_a_linked_package_for_the_same_image_names_it(state, capsys):
    local, project = state
    deploy(local, "released", package=package(project, "released", "1.2.0", runtime=KIT))
    deploy(local, "moved", image=OTHER, package=package(project, "moved", "1.0.0"))
    deploy(local, "gone", package={"ref": "agent/gone@1.0.0", "sha256": "sha256:" + "0" * 64})
    rows = {row["name"]: row["runtime"] for row in listed(capsys, "--state-dir", str(local))}
    assert rows == {"released": KIT, "moved": None, "gone": None}


@pytest.mark.parametrize(
    "runtime", ["adk", "runtime/adk", "runtime/adk@latest", "tool/x@1.0.0", 7, None, ADK + "\n"]
)
def test_only_a_well_formed_runtime_ref_is_shown(state, capsys, runtime):
    local, project = state
    record(project, "support", runtime=runtime)
    deploy(local, "support")
    assert listed(capsys, "--state-dir", str(local))[0]["runtime"] is None


def test_an_unsafe_or_damaged_record_is_not_read(state, capsys, tmp_path):
    local, project = state
    deploy(local, "linked")
    deploy(local, "damaged")
    deploy(local, "listy")
    deploy(local, "other")
    real = record(tmp_path / "elsewhere", "linked")
    (project / "deploy").mkdir()
    (project / "deploy" / "linked.build.json").symlink_to(real)
    (project / "deploy" / "damaged.build.json").write_text("{not json")
    (project / "deploy" / "listy.build.json").write_text("[]")
    (project / "deploy" / "other.build.json").write_text(
        json.dumps({"name": "someone-else", "image": IMAGE, "runtime": ADK})
    )
    assert {row["runtime"] for row in listed(capsys, "--state-dir", str(local))} == {None}


def test_manifest_values_that_are_not_a_runtime_block_are_ignored():
    assert allstacks.runtime_ref({"runtime": "x", "image": IMAGE}, "a", None, None) is None
    assert allstacks.runtime_ref({"runtime": {"ref": 5}}, "a", None, None) is None
    assert allstacks.runtime_ref({"image": 5}, "a", None, Path(".")) is None
    assert allstacks.runtime_ref({"image": IMAGE}, "Not A Name", "text", Path(".")) is None
    assert allstacks._package("{not json") is None and allstacks._package("[1]") is None


def test_list_all_reads_each_stacks_project(state, capsys, tmp_path):
    local, project = state
    record(project, "hand")
    deploy(local, "hand")
    nw = tmp_path / "nw"
    one = make_stack(tmp_path / "stacks", "northwind", nw)
    record(nw, "northwind-support", runtime=KIT)
    deploy(one / "envs/dev/state", "northwind-support")
    two = make_stack(tmp_path / "stacks", "starter", tmp_path / "st", record=False)
    record(tmp_path / "st", "starter")
    deploy(two / "envs/dev/state", "starter")  # no recorded project: nowhere to look
    rows = {
        row["name"]: row["runtime"] for row in listed(capsys, "--all", "--state-dir", str(local))
    }
    assert rows == {"hand": ADK, "northwind-support": KIT, "starter": None}


def test_text_output_is_unchanged(state, capsys):
    local, project = state
    record(project, "support")
    deploy(local, "support")
    assert cli.main(["list", "--state-dir", str(local)]) == 0
    assert capsys.readouterr().out == "support-id  support  local  running\n"


class EmptyStore:
    def active_all(self):
        return []


def test_a_changed_build_record_is_read_again_on_the_next_call(tmp_path):
    record = tmp_path / "deploy" / "shop.build.json"
    record.parent.mkdir()
    image = "sha256:" + "a" * 64
    manifest = {"image": image}
    for runtime in ("runtime/adk@1.3.0", "runtime/adk@1.4.0"):
        record.write_text(json.dumps({"name": "shop", "image": image, "runtime": runtime}))
        allstacks.rows(EmptyStore(), tmp_path)  # clears the cache, as every `list` does
        assert allstacks.runtime_ref(manifest, "shop", None, tmp_path) == runtime
