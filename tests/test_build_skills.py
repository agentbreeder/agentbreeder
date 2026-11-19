"""`agentbreeder build` with `skills:` on a runtime that loads them (#1059, skills spec §7): the
placed copy is checked again, has no `scripts/`, and its hash in agent.config.json is the one the
runtime computes at start. Stub builder only."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from test_build import FakeBuilder
from test_build import operator as operator  # noqa: F401  (fixture)
from test_build import project as project  # noqa: F401  (fixture)
from test_build_subagents import GOLDEN, ConfigBuilder, _hashes
from test_registry_skill import make_skill, script_skill

from agentbreeder import registry
from agentbreeder.build import BuildError, build, skill_placed_hash
from agentbreeder.lock import Lock

RUNTIME_SKILL_HASH = (
    registry.BUILTIN_ROOT / "runtime" / "adk" / "1.5.0" / "runtime" / "skill_hash.py"
)
FILES = {"references/eu-rules.md": "EU: 14 days.\n", "references/us.txt": "US rules.\n"}


def _runtime_hash():
    """The runtime's own hash functions, loaded from the built-in file (standard library only)."""
    spec = importlib.util.spec_from_file_location("runtime_skill_hash", RUNTIME_SKILL_HASH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capable(project, capabilities="[mcp, skills]"):  # noqa: F811
    manifest = project / "registry" / "runtime" / "fake" / "1.0.0" / "manifest.yaml"
    text = manifest.read_text().replace("capabilities: [mcp]", f"capabilities: {capabilities}")
    manifest.write_text(text)


def _with_skills(project, *refs):  # noqa: F811
    agent = yaml.safe_load((project / "agent.yaml").read_text())
    agent["skills"] = [{"ref": ref} for ref in refs]
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))


class TreeBuilder(FakeBuilder):
    """Also keeps every placed skill file, by path relative to the context."""

    def __init__(self):
        super().__init__()
        self.skill_files: dict[str, bytes] = {}

    def __call__(self, context, iidfile):
        root = context / "artifacts" / "skill"
        for path in sorted(root.rglob("*")) if root.exists() else []:
            if path.is_file():
                self.skill_files[path.relative_to(context).as_posix()] = path.read_bytes()
        return super().__call__(context, iidfile)


def test_build_places_a_skill_and_records_the_hash_the_runtime_computes(project, operator):  # noqa: F811
    _capable(project)
    make_skill(project / "registry/skill/refund-policy/1.0.0", files=FILES)
    make_skill(project / "registry/skill/complaint-tone/1.0.0", name="complaint-tone")
    _with_skills(project, "skill/refund-policy@1.0.0", "skill/complaint-tone@1.0.0")
    fake = TreeBuilder()
    build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    _, config = fake.contexts[0]
    first, second = config["skills"]
    assert {key: first[key] for key in ("name", "version", "dir")} == {
        "name": "refund-policy",
        "version": "1.0.0",
        "dir": "artifacts/skill/refund-policy",
    }
    assert second["name"] == "complaint-tone" and set(first) == {
        "name",
        "version",
        "dir",
        "sha256",
    }
    placed = {
        path.removeprefix("artifacts/skill/refund-policy/"): data
        for path, data in fake.skill_files.items()
        if path.startswith("artifacts/skill/refund-policy/")
    }
    assert sorted(placed) == [
        "SKILL.md",
        "manifest.yaml",
        "references/eu-rules.md",
        "references/us.txt",
    ]
    # The runtime's function over the placed bytes gives the recorded hash.
    assert _runtime_hash().placed_hash(placed) == first["sha256"]
    assert first["sha256"] != second["sha256"] and first["sha256"].startswith("sha256:")


def test_the_host_and_runtime_hash_functions_agree(tmp_path):
    runtime = _runtime_hash()
    cases = [
        {"SKILL.md": b"body\n", "manifest.yaml": b"kind: skill\n"},
        {"SKILL.md": b"", "references/a.md": b"\xff\x00binary", "references/b.txt": b"x" * 70000},
        {"a": b"bc", "ab": b"c", "z/y/x.md": "café".encode()},
        {},
    ]
    seen = set()
    for index, files in enumerate(cases):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "empty-dir").mkdir()  # not part of the hash on either side
        for relative, data in files.items():
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        host = skill_placed_hash(directory)
        assert host == runtime.placed_hash(runtime.placed_files(directory))
        assert host == runtime.placed_hash(files)
        seen.add(host)
    assert len(seen) == len(cases)
    assert runtime.HEADER == b"agentbreeder-skill-placed-v1\n"
    # A link is refused on both sides, never followed.
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "SKILL.md").symlink_to(tmp_path / "0" / "SKILL.md")
    with pytest.raises(BuildError):
        skill_placed_hash(linked)
    with pytest.raises(ValueError):
        runtime.placed_files(linked)


def test_the_lock_keeps_the_registry_hash_not_the_placed_one(project, operator):  # noqa: F811
    _capable(project)
    make_skill(project / "registry/skill/refund-policy/1.0.0", files=FILES)
    _with_skills(project, "skill/refund-policy@1.0.0")
    fake = FakeBuilder()
    build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    locked = Lock.load(project / "agent.lock").entries["skill/refund-policy@1.0.0"]["sha256"]
    assert locked == registry.copy_and_hash(project / "registry/skill/refund-policy/1.0.0")
    assert locked != fake.contexts[0][1]["skills"][0]["sha256"]  # two hashes, on purpose


def test_a_script_skill_is_refused_before_docker(project, operator):  # noqa: F811
    _capable(project)
    script_skill(project / "registry/skill/refund-policy/1.0.0")
    _with_skills(project, "skill/refund-policy@1.0.0")
    fake = FakeBuilder()
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    assert caught.value.code == "RUNTIME_NO_SKILL_SCRIPTS" and fake.contexts == []
    assert "skill/refund-policy@1.0.0" in str(caught.value)


def test_a_runtime_that_runs_scripts_still_gets_no_scripts_directory(project, operator):  # noqa: F811
    # No such runtime exists yet (K6). The image never holds a skill's scripts either way.
    _capable(project, "[mcp, skills, skill_scripts]")
    script_skill(project / "registry/skill/refund-policy/1.0.0")
    _with_skills(project, "skill/refund-policy@1.0.0")
    fake = TreeBuilder()
    build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    assert not any("/scripts/" in path for path in fake.skill_files)
    assert sorted(fake.skill_files) == [
        "artifacts/skill/refund-policy/SKILL.md",
        "artifacts/skill/refund-policy/manifest.yaml",
    ]


def test_a_runtime_without_skills_is_still_refused(project, operator):  # noqa: F811
    make_skill(project / "registry/skill/refund-policy/1.0.0")
    _with_skills(project, "skill/refund-policy@1.0.0")
    fake = FakeBuilder()
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    assert caught.value.code == "RUNTIME_NO_SKILLS" and fake.contexts == []


def test_a_skill_that_breaks_a_rule_is_refused_with_its_code(project, operator):  # noqa: F811
    _capable(project)
    bad = (
        "---\nname: refund-policy\ndescription: d\nmetadata: {adk_additional_tools: [x]}\n---\nb\n"
    )
    make_skill(project / "registry/skill/refund-policy/1.0.0", description="d", skill=bad)
    _with_skills(project, "skill/refund-policy@1.0.0")
    fake = FakeBuilder()
    with pytest.raises(ValueError) as caught:
        build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    assert caught.value.code == "SKILL_FIELD_UNSUPPORTED" and fake.contexts == []


def test_no_skills_means_the_same_bytes(project, operator):  # noqa: F811
    _capable(project)  # a runtime that loads skills changes nothing for a file without them
    builder = ConfigBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=builder)
    assert "skills" not in json.loads(builder.config)
    hashes = _hashes(result, builder)
    assert hashes["config"] == GOLDEN["helper"]["config"]
    assert hashes["template"] == GOLDEN["helper"]["template"]


def test_the_builtin_runtime_lists_the_capabilities_build_checks():
    from agentbreeder.runtime_caps import runtime_capabilities

    adk = Path(RUNTIME_SKILL_HASH).parents[1]
    # 1.5.0 declares `protocol`, so `knowledge` and `notes` are listed, not implied.
    assert runtime_capabilities(adk) == frozenset(
        {"knowledge", "notes", "mcp", "subagents", "skills"}
    )
