"""The built-in `runtime/adk@1.5.0` as the engine reads it (#1058 RT on #1083 F1): the first
`runtime/adk` version that declares `protocol`, so nothing about it is implied.

The unchanged happy path: Northwind and the `new agent` starter, which use no sub-agents and no
skills, build the same `agent.config.json`, manifests and deploy template on 1.5.0 as on 1.4.0,
apart from the runtime's own ref and hash. Stub builder only: no image is built.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from test_runtime_golden import _northwind, _operator, _starter

from agentbreeder import registry, runtime_profile
from agentbreeder.build import BuildError, build

VERSION_DIR = registry.BUILTIN_ROOT / "runtime" / "adk" / "1.5.0"
ADK_SKIPS = {
    "transfer_to_agent",
    "adk_request_credential",
    "adk_request_confirmation",
    "adk_request_input",
    "set_model_response",
}
IMAGE = "sha256:" + "c" * 64


def test_the_manifest_declares_everything_the_engine_reads():
    profile = runtime_profile.load(VERSION_DIR)
    assert not profile.legacy
    assert (profile.protocol, profile.profile, profile.kit) == (1, "v1", None)
    assert (profile.framework, profile.display_name) == ("google-adk", "Google ADK")
    # Nothing is implied once `protocol` is set: `knowledge` and `notes` are listed.
    assert profile.capabilities == {"knowledge", "notes", "mcp", "subagents", "skills"}
    assert profile.limits == {"knowledge": 1, "tools": 50, "mcp_servers": 10}
    assert profile.reserved_tool_names == ADK_SKIPS | {"load_skill", "load_skill_resource"}
    legacy = runtime_profile.load(VERSION_DIR.parent / "1.4.0")
    assert legacy.legacy and legacy.capabilities | {"subagents", "skills"} == profile.capabilities
    assert legacy.limits == profile.limits and legacy.reserved_tool_names == ADK_SKIPS


def test_modules_json_lists_the_runtimes_own_modules():
    profile = runtime_profile.load(VERSION_DIR)
    modules = runtime_profile.load_modules(VERSION_DIR, profile)
    own = {path.stem for path in (VERSION_DIR / "runtime").glob("*.py")}
    assert modules.modules == own
    assert {"skills", "skill_hash", "subagents", "turns", "telemetry"} <= own
    assert {"litellm", "google-adk"} <= set(modules.imports)


class Keeps:
    """Stands in for `docker build`: keeps agent.config.json."""

    def __init__(self):
        self.config = b""

    def __call__(self, context: Path, iidfile: Path) -> str:
        self.config = (context / "agent.config.json").read_bytes()
        return IMAGE


def _build(tmp_path, identity_env, kind: str, version: str) -> dict:
    root = tmp_path / version
    root.mkdir()
    if kind == "northwind":
        project = _northwind(root, version)
        aliases = ["support-model", "support-embedding"]
        name = "northwind-support"
    else:
        project = _starter(root, version)
        aliases, name = ["chat", "judge"], "golden-starter"
    keeps = Keeps()
    result = build(
        project / "agent.yaml", _operator(identity_env, name, aliases), "dev", builder=keeps
    )
    return {
        "config": keeps.config,
        "manifest": result.manifest_path.read_bytes(),
        "candidate": result.candidate_manifest_path.read_bytes(),
        "template": result.deploy.model_dump(mode="json"),
    }


def _without_the_runtime(data: bytes) -> dict:
    """A manifest or config as JSON or YAML, without the fields that name the runtime version
    or are derived from the whole document (which includes it)."""
    document = yaml.safe_load(data)

    def strip(node):
        if isinstance(node, dict):
            return {
                key: strip(value)
                for key, value in node.items()
                if key not in ("runtime", "digest", "revision", "config_sha256", "lock_sha256")
            }
        if isinstance(node, list):
            return [strip(item) for item in node]
        if isinstance(node, str):
            return node.replace("1.5.0", "1.4.0")
        return node

    return strip(document)


@pytest.mark.parametrize("kind", ["northwind", "starter"])
def test_an_agent_without_sub_agents_or_skills_builds_as_on_1_4_0(
    tmp_path, identity_env, monkeypatch, kind
):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    old = _build(tmp_path, identity_env, kind, "1.4.0")
    new = _build(tmp_path, identity_env, kind, "1.5.0")
    config = json.loads(new["config"])
    assert not {"subagents", "limits", "skills"} & set(config)
    # agent.config.json has no runtime field at all: the same bytes on both versions.
    assert new["config"] == old["config"]
    assert new["template"] == old["template"]
    for part in ("manifest", "candidate"):
        assert _without_the_runtime(new[part]) == _without_the_runtime(old[part]), part


def _refused(project: Path, operator) -> BuildError:
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator, "dev", builder=Keeps())
    return caught.value


def _tool(project: Path, function: str, module: str) -> None:
    root = project / "registry" / "tool" / "extra" / "1.0.0"
    (root / "src").mkdir(parents=True)
    (root / "manifest.yaml").write_text("kind: tool\nname: extra\nversion: 1.0.0\n")
    spec = {"functions": [{"name": function, "module": module, "description": "x"}]}
    (root / "tool.yaml").write_text(yaml.safe_dump(spec))
    (root / "src" / f"{module}.py").write_text(f"def {function}(rt):\n    return 1\n")
    agent = yaml.safe_load((project / "agent.yaml").read_text())
    agent.setdefault("tools", []).append({"ref": "tool/extra@1.0.0"})
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))


@pytest.mark.parametrize("name", ["load_skill", "load_skill_resource", "transfer_to_agent"])
def test_a_function_tool_named_like_a_runtime_tool_is_refused_at_build(
    tmp_path, identity_env, monkeypatch, name
):
    """On 1.4.0 such a tool builds; on 1.5.0 the runtime would refuse to start with skills."""
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = _starter(tmp_path, "1.5.0")
    _tool(project, name, "extra")
    error = _refused(project, _operator(identity_env, "golden-starter", ["chat", "judge"]))
    assert error.code == "TOOL_NAME_RESERVED"
    assert f"tool/extra@1.0.0: {name} is reserved by runtime/adk@1.5.0" in str(error)


@pytest.mark.parametrize("module", ["skills", "skill_hash", "subagents", "turns", "litellm"])
def test_a_tool_module_that_would_hide_the_runtime_is_refused_at_build(
    tmp_path, identity_env, monkeypatch, module
):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = _starter(tmp_path, "1.5.0")
    _tool(project, "lookup_extra", module)
    error = _refused(project, _operator(identity_env, "golden-starter", ["chat", "judge"]))
    assert error.code == "TOOL_MODULE_SHADOWS"
    assert f"module '{module}' would hide a module of runtime/adk@1.5.0" in str(error)


def test_a_second_knowledge_set_is_over_the_runtimes_limit(tmp_path, identity_env, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = _northwind(tmp_path, "1.5.0")
    first = project / "registry" / "knowledge" / "northwind-help" / "1.0.0"
    second = project / "registry" / "knowledge" / "more-help" / "1.0.0"
    shutil.copytree(first, second)
    manifest = second / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("name: northwind-help", "name: more-help"))
    path = project / "agent.yaml"
    path.write_text(
        path.read_text().replace(
            "knowledge:\n", "knowledge:\n  - {ref: knowledge/more-help@1.0.0, top_k: 3}\n", 1
        )
    )
    operator = _operator(identity_env, "northwind-support", ["support-model", "support-embedding"])
    error = _refused(project, operator)
    assert error.code == "RUNTIME_LIMIT"
    assert "runtime/adk@1.5.0 takes at most 1 knowledge: entry" in str(error)
