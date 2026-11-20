"""#1083 F1, "engine data-driven": a runtime version's manifest keys and `modules.json` are the
engine's only source of framework facts (multi-framework spec §5.3, §5.4, §6.2).

`tests/test_runtime_golden.py` pins that nothing changed for `runtime/adk`; this file covers
runtimes that declare `protocol`, the `LEGACY_ADK` profile, the manifest `runtime` field,
`registry runtimes`, `new agent --runtime` and the `modules.json` generator.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from conftest import v2_manifest_data
from pydantic import ValidationError
from test_build import FakeBuilder, version
from test_build import operator as operator  # noqa: F401  (fixture)
from test_build import project as project  # noqa: F401  (fixture)

from agentbreeder import agentspec, cli, newagent, registry, runtime_profile, tool_check
from agentbreeder.build import BuildError, build
from agentbreeder.lock import Lock
from agentbreeder.manifest import Manifest, load_manifest
from agentbreeder.package import DeployTemplate
from agentbreeder.runtime_profile import LEGACY_ADK, ProfileError

ROOT = Path(__file__).parents[1]
ADK = Path(registry.__file__).parent / "builtin_registry" / "runtime" / "adk"
TODAY = date(2026, 11, 17)

KIT = """\
framework: example
display_name: Example Framework
protocol: 1
profile: kit
kit: 1.0.0
port: 9000
health_path: /healthz
capabilities: [mcp, knowledge]
limits: {knowledge: 1, tools: 2, mcp_servers: 1}
reserved_tool_names: [search_knowledge_base, handoff]
model_api: openai-chat
notes: Needs nothing special.
docs: docs/runtimes/example.md
"""
MODULES = runtime_profile.modules_document({"server", "kit"}, {"httpx": ["httpx"]})
MCP_YAML = (
    'upstream:\n  remote: {url: "https://mcp.example.com/mcp"}\n'
    "auth:\n  type: bearer\n  secret: token\n"
    "secrets:\n  token: {import: DOCS_MCP_TOKEN}\n"
    "tools:\n  search: {effect: read}\n  over: {effect: read}\n"
)


def kit_runtime(root: Path, manifest: str = KIT, modules: str | None = MODULES) -> Path:
    """Replace the fixture's `runtime/fake@1.0.0` with one that declares `protocol`."""
    path = root / "runtime" / "fake" / "1.0.0"
    shutil.rmtree(path, ignore_errors=True)
    files = {
        "Dockerfile": "FROM scratch\n",
        "runtime/requirements.lock": "httpx==0.28.1\n",
        "runtime/server.py": "x=1\n",
    }
    if modules is not None:
        files["runtime/modules.json"] = modules
    version(root, "runtime", "fake", files, extra=manifest)
    return path


def edit_agent(project: Path, **changes) -> None:  # noqa: F811
    path = project / "agent.yaml"
    data = yaml.safe_load(path.read_text())
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data))


def refused(project: Path, operator) -> BuildError:  # noqa: F811
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    return caught.value


# -- the profile ----------------------------------------


def test_a_manifest_without_protocol_gets_the_legacy_adk_profile():
    profile = runtime_profile.from_manifest({"kind": "runtime", "capabilities": ["mcp"]})
    assert profile.legacy
    assert (profile.display_name, profile.protocol, profile.profile) == ("Google ADK", 1, "v1")
    assert profile.capabilities == {"knowledge", "notes", "mcp"}  # implied, plus the manifest's
    assert profile.limits == {"knowledge": 1, "tools": 50, "mcp_servers": 10}
    assert profile.reserved_tool_names == LEGACY_ADK.reserved_tool_names
    assert "transfer_to_agent" in profile.reserved_tool_names
    assert profile.kit is None and profile.notes is None and profile.docs is None


# Modules runtime/adk@1.5.0 added; it is the first version with `protocol` and its own
# runtime/modules.json (tests/test_runtime_adk_150.py).
ADK_150_MODULES = frozenset({"skill_hash", "skills", "subagents", "turns"})
LANGGRAPH_100_MODULES = frozenset({"kit", "langgraph_adapter", "server"})


@pytest.mark.parametrize("name", sorted(path.name for path in ADK.iterdir()))
def test_every_builtin_adk_version_is_legacy_and_usable(name):
    profile = runtime_profile.load(ADK / name)
    version = tuple(map(int, name.split(".")))
    assert profile.framework == "google-adk"
    assert profile.legacy is (version < (1, 5, 0))  # published before the `protocol` key
    assert profile.has("mcp") is (version >= (1, 1, 0))
    assert profile.has("knowledge") and profile.has("notes")
    own = LEGACY_ADK.modules | (ADK_150_MODULES if version >= (1, 5, 0) else frozenset())
    assert runtime_profile.load_modules(ADK / name, profile).modules == own


def test_a_manifest_with_protocol_is_read_from_its_own_keys():
    profile = runtime_profile.from_manifest(yaml.safe_load(KIT))
    assert not profile.legacy
    assert profile.as_json() == {
        "display_name": "Example Framework",
        "framework": "example",
        "protocol": 1,
        "profile": "kit",
        "kit": "1.0.0",
        "capabilities": ["knowledge", "mcp"],
        "limits": {"knowledge": 1, "mcp_servers": 1, "tools": 2},
        "reserved_tool_names": ["handoff", "search_knowledge_base"],
        "notes": "Needs nothing special.",
        "docs": "docs/runtimes/example.md",
    }
    assert profile.model_api == "openai-chat"
    # Nothing is implied for a runtime that declares its protocol.
    assert not profile.has("notes")


def test_the_engine_limits_are_the_agent_files_own_ceilings():
    from agentbreeder import agentfile

    assert runtime_profile.ENGINE_LIMITS == {
        "knowledge": agentfile.MAX_KNOWLEDGE,
        "tools": agentfile.MAX_TOOLS,
        "mcp_servers": agentfile.MAX_MCP,
    }
    assert all(
        runtime_profile.DEFAULT_LIMITS[part] <= limit
        for part, limit in runtime_profile.ENGINE_LIMITS.items()
    )


def test_undeclared_limits_are_the_limits_every_runtime_had():
    data = yaml.safe_load(KIT)
    del data["limits"], data["reserved_tool_names"]
    profile = runtime_profile.from_manifest(data)
    assert profile.limits == {"knowledge": 1, "tools": 50, "mcp_servers": 10}
    assert profile.reserved_tool_names == frozenset()


@pytest.mark.parametrize(
    "change,what",
    [
        ({"protocol": "1"}, "protocol"),
        ({"protocol": True}, "protocol"),
        ({"protocol": 0}, "protocol"),
        ({"profile": "other"}, "profile"),
        ({"profile": None}, "profile"),
        ({"kit": None}, "kit"),
        ({"kit": "1.0"}, "kit"),
        ({"profile": "v1"}, "kit"),  # `kit` is for the kit profile only
        ({"framework": "Bad Name"}, "framework"),
        ({"framework": None}, "framework"),
        ({"display_name": None}, "display_name"),
        ({"display_name": "x" * 101}, "display_name"),
        ({"display_name": "two\nlines"}, "display_name"),
        ({"display_name": " padded "}, "display_name"),
        ({"notes": "x" * 301}, "notes"),
        ({"notes": "bidi‮"}, "notes"),
        ({"docs": "/etc/passwd"}, "docs"),
        ({"docs": "docs/../../x.md"}, "docs"),
        ({"docs": "https://example.com/x"}, "docs"),
        ({"limits": {"knowledge": 6}}, "limits.knowledge"),
        ({"limits": {"tools": -1}}, "limits.tools"),
        ({"limits": {"tools": True}}, "limits.tools"),
        ({"limits": {"memory": 1}}, "limits"),
        ({"limits": [1]}, "limits"),
        ({"reserved_tool_names": ["a", "a"]}, "reserved_tool_names"),
        ({"reserved_tool_names": ["has space"]}, "reserved_tool_names"),
        ({"reserved_tool_names": [f"n{i}" for i in range(101)]}, "reserved_tool_names"),
        ({"capabilities": "mcp"}, "capabilities"),
        ({"capabilities": [f"c{i}" for i in range(33)]}, "capabilities"),
        ({"model_api": "grpc"}, "model_api"),
    ],
)
def test_an_invalid_key_is_refused_by_name_only(change, what):
    data = yaml.safe_load(KIT)
    for key, value in change.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    with pytest.raises(ProfileError) as caught:
        runtime_profile.from_manifest(data)
    assert caught.value.code == "ARTIFACT_INVALID"
    assert caught.value.message == f"invalid {what}"  # never the supplied value


@pytest.mark.parametrize("key", runtime_profile.PROTOCOL_KEYS)
def test_a_protocol_key_without_protocol_is_refused_not_read_as_legacy(key):
    data = yaml.safe_load(KIT)
    del data["protocol"]
    with pytest.raises(ProfileError) as caught:
        runtime_profile.from_manifest({"kind": "runtime", key: data[key]})
    assert (caught.value.code, caught.value.message) == (
        "ARTIFACT_INVALID",
        f"{key} needs protocol",
    )


def test_an_unknown_protocol_has_its_own_code():
    with pytest.raises(ProfileError) as caught:
        runtime_profile.from_manifest({**yaml.safe_load(KIT), "protocol": 3})
    assert caught.value.code == "PROTOCOL_UNSUPPORTED"
    assert caught.value.message == "protocol 3 is not supported by this AgentBreeder"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"schema_version": 2, "modules": [], "imports": {}}',
        '{"schema_version": true, "modules": [], "imports": {}}',
        '{"schema_version": 1, "modules": [], "imports": {}, "extra": 1}',
        '{"schema_version": 1, "modules": ["a-b"], "imports": {}}',
        '{"schema_version": 1, "modules": ["a", "a"], "imports": {}}',
        '{"schema_version": 1, "modules": [], "imports": {"Bad_Name": []}}',
        '{"schema_version": 1, "modules": [], "imports": {"pkg": "name"}}',
        '{"schema_version": 1, "modules": [], "modules": [], "imports": {}}',
        '{"schema_version": 1, "modules": ["' + "a" * 300_000 + '"], "imports": {}}',
    ],
)
def test_an_invalid_modules_file_is_refused(text):
    with pytest.raises(ProfileError) as caught:
        runtime_profile.parse_modules(text.encode())
    assert caught.value.code == "ARTIFACT_INVALID"
    assert caught.value.message == "invalid runtime/modules.json"


def test_modules_names_are_the_runtime_modules_and_the_lock_imports():
    found = runtime_profile.parse_modules(MODULES.encode())
    assert found.names() == {"server", "kit", "httpx"}


# -- build ----------------------------------------


def test_build_writes_the_runtime_field_only_for_a_kit_runtime(project, operator):  # noqa: F811
    plain = build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    before = plain.manifest_path.read_text()
    assert "runtime" not in yaml.safe_load(before) and plain.deploy.runtime is None

    kit_runtime(project / "registry")
    (project / "agent.lock").unlink()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    expected = {"ref": "runtime/fake@1.0.0", "profile": "kit"}
    written = yaml.safe_load(result.manifest_path.read_text())
    assert written["runtime"] == expected
    assert list(written)[-1] == "runtime"  # appended, so the other keys keep their order
    assert yaml.safe_load(result.candidate_manifest_path.read_text())["runtime"] == expected
    manifest = load_manifest(result.manifest_path)
    assert manifest.runtime.ref == "runtime/fake@1.0.0" and manifest.runtime.profile == "kit"
    # In the deploy template, so the released package and `promote` keep it.
    assert result.deploy.runtime.model_dump() == expected
    assert (manifest.port, manifest.health_path) == (9000, "/healthz")


def test_a_runtime_that_declares_protocol_but_not_the_kit_profile_writes_no_field(
    project,  # noqa: F811
    operator,  # noqa: F811
):
    manifest = KIT.replace("profile: kit\nkit: 1.0.0\n", "profile: v1\n")
    kit_runtime(project / "registry", manifest)
    result = build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert "runtime" not in yaml.safe_load(result.manifest_path.read_text())


@pytest.mark.parametrize(
    "manifest,agent,code,needle",
    [
        (
            KIT.replace("capabilities: [mcp, knowledge]", "capabilities: [mcp]"),
            {},
            "RUNTIME_NO_KNOWLEDGE",
            "runtime/fake@1.0.0 has no `knowledge` capability",
        ),
        (
            KIT,
            {
                "memory": {"notes": True},
                "tools": [{"ref": "tool/orders@1.0.0"}, {"ref": "tool/customer-notes@1.0.0"}],
            },
            "RUNTIME_NO_NOTES",
            "runtime/fake@1.0.0 has no `notes` capability",
        ),
        (
            KIT.replace("capabilities: [mcp, knowledge]", "capabilities: [knowledge]"),
            {"mcp": [{"ref": "mcp/docs@1.0.0", "tools": ["search"]}]},
            "MCP_RUNTIME_UNSUPPORTED",
            "runtime/fake@1.0.0 has no `mcp` capability",
        ),
        (
            KIT.replace("knowledge: 1,", "knowledge: 0,"),
            {},
            "RUNTIME_LIMIT",
            "runtime/fake@1.0.0 takes at most 0 knowledge: entries (limits.knowledge); the agent"
            " has 1",
        ),
        (
            KIT.replace("tools: 2,", "tools: 0,"),
            {},
            "RUNTIME_LIMIT",
            "at most 0 tools: entries (limits.tools); the agent has 1",
        ),
        (
            KIT.replace("mcp_servers: 1", "mcp_servers: 0"),
            {"mcp": [{"ref": "mcp/docs@1.0.0", "tools": ["search"]}]},
            "RUNTIME_LIMIT",
            "at most 0 mcp: entries (limits.mcp_servers); the agent has 1",
        ),
        (KIT.replace("protocol: 1", "protocol: 2"), {}, "PROTOCOL_UNSUPPORTED", "protocol 2"),
        (KIT.replace("profile: kit", "profile: nope"), {}, "ARTIFACT_INVALID", "invalid profile"),
    ],
)
def test_build_refuses_what_the_runtime_cannot_run(
    project,  # noqa: F811
    operator,  # noqa: F811
    manifest,
    agent,
    code,
    needle,
):
    root = project / "registry"
    kit_runtime(root, manifest)
    version(root, "mcp", "docs", {"mcp.yaml": MCP_YAML})
    version(root, "tool", "customer-notes", {"tool.yaml": "functions: []\n", "src/notes.py": ""})
    edit_agent(project, **agent)
    error = refused(project, operator)
    assert error.code == code, error
    assert needle in str(error)


def test_build_needs_a_valid_modules_file_from_a_runtime_that_declares_protocol(
    project,  # noqa: F811
    operator,  # noqa: F811
):
    kit_runtime(project / "registry", modules=None)
    error = refused(project, operator)
    assert error.code == "ARTIFACT_INVALID"
    assert "runtime/fake@1.0.0: missing runtime/modules.json" in str(error)
    kit_runtime(project / "registry", modules="{}")
    assert "invalid runtime/modules.json" in str(refused(project, operator))


def _tool(root: Path, name: str, function: str, module: str) -> None:
    shutil.rmtree(root / "tool" / name, ignore_errors=True)
    spec = {"functions": [{"name": function, "module": module, "description": "x"}]}
    version(root, "tool", name, {"tool.yaml": yaml.safe_dump(spec), f"src/{module}.py": ""})


def test_build_refuses_a_tool_module_that_would_hide_the_runtime(project, operator):  # noqa: F811
    kit_runtime(project / "registry")
    for module in ("kit", "httpx"):  # the runtime's own module, and one its lock installs
        _tool(project / "registry", "orders", "lookup", module)
        error = refused(project, operator)
        assert error.code == "TOOL_MODULE_SHADOWS"
        assert (
            f"tool/orders@1.0.0: module '{module}' would hide a module of runtime/fake@1.0.0"
            in str(error)
        )
    _tool(project / "registry", "orders", "lookup", "orders")
    build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())


def test_reserved_names_cover_every_name_the_model_sees_on_a_protocol_runtime(
    project,  # noqa: F811
    operator,  # noqa: F811
):
    root = project / "registry"
    kit_runtime(root, KIT.replace("handoff]", "handoff, hand_over]"))
    _tool(root, "orders", "handoff", "orders")
    error = refused(project, operator)
    assert error.code == "TOOL_NAME_RESERVED"
    assert "tool/orders@1.0.0: handoff is reserved by runtime/fake@1.0.0" in str(error)

    _tool(root, "orders", "lookup", "orders")
    # An MCP server's own tool name, and the `<server>_<tool>` name the model would see.
    version(
        root,
        "mcp",
        "hand",
        {"mcp.yaml": MCP_YAML.replace("search:", "handoff:")},
    )
    edit_agent(project, mcp=[{"ref": "mcp/hand@1.0.0", "tools": ["handoff"]}])
    error = refused(project, operator)
    assert error.code == "MCP_TOOL_RESERVED", error
    assert "mcp/hand@1.0.0: handoff is reserved by runtime/fake@1.0.0" in str(error)
    edit_agent(project, mcp=[{"ref": "mcp/hand@1.0.0", "tools": ["over"]}])
    error = refused(project, operator)
    assert error.code == "MCP_TOOL_RESERVED" and "hand_over is reserved" in str(error)


def test_a_sub_agents_function_tool_is_checked_against_reserved_names(
    project,  # noqa: F811
    operator,  # noqa: F811
):
    root = project / "registry"
    kit_runtime(root, KIT.replace("[mcp, knowledge]", "[mcp, knowledge, subagents]"))
    _tool(root, "extra", "handoff", "extra")
    sub = {
        "name": "billing",
        "description": "Answers billing questions.",
        "model": {"alias": "support-model"},
        "prompt": {"ref": "prompt/helper@1.0.0"},
        "tools": [{"ref": "tool/extra@1.0.0"}],
    }
    edit_agent(project, subagents=[sub])
    error = refused(project, operator)
    assert error.code == "TOOL_NAME_RESERVED", error
    assert "tool/extra@1.0.0: handoff is reserved by runtime/fake@1.0.0" in str(error)

    # The sub-agent's own name is checked against the runtime's reserved names too.
    _tool(root, "extra", "lookup_more", "extra")
    kit_runtime(
        root,
        KIT.replace("[mcp, knowledge]", "[mcp, knowledge, subagents]").replace(
            "handoff]", "handoff, ask_billing]"
        ),
    )
    error = refused(project, operator)
    assert error.code == "SUBAGENT_NAME_CLASH", error
    assert "a tool name the runtime reserves" in str(error)


def test_a_legacy_runtime_checks_reserved_names_against_mcp_tool_names_only(
    project,  # noqa: F811
    operator,  # noqa: F811
):
    """As before F1: ADK skips an MCP tool by its own name; a function tool of that name was
    never refused by build."""
    _tool(project / "registry", "orders", "transfer_to_agent", "orders")
    build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())


# -- the manifest and the package ----------------------------------------


def _manifest(**extra) -> Manifest:
    access = {"gateway": "litellm-local", "aliases": ["support-model"]}
    return Manifest.model_validate(v2_manifest_data(model_access=access, **extra))


def test_the_runtime_field_is_optional_and_leaves_existing_digests_alone():
    import hashlib

    without = _manifest()
    kit = _manifest(runtime={"ref": "runtime/langgraph@1.0.0", "profile": "kit"})
    assert without.runtime is None and "runtime" not in without.revision_dump()
    # Computed as before the field existed.
    legacy = without.model_dump_json(exclude={"mcp_access", "guardrails", "runtime"})
    assert without.digest() == hashlib.sha256(legacy.encode()).hexdigest()
    assert kit.digest() != without.digest()
    assert kit.revision_dump()["runtime"] == {"ref": "runtime/langgraph@1.0.0", "profile": "kit"}


@pytest.mark.parametrize(
    "runtime",
    [
        {"ref": "runtime/langgraph@1.0.0", "profile": "v1"},
        {"ref": "runtime/langgraph@1.0.0"},
        {"ref": "tool/orders@1.0.0", "profile": "kit"},
        {"ref": "runtime/langgraph", "profile": "kit"},
        {"ref": "runtime/langgraph@1.0.0", "profile": "kit", "extra": 1},
    ],
)
def test_an_invalid_runtime_field_is_refused(runtime):
    with pytest.raises(ValidationError):
        _manifest(runtime=runtime)


def test_the_runtime_field_is_for_built_agents():
    with pytest.raises(ValidationError, match="runtime is for built agents"):
        Manifest.model_validate(
            v2_manifest_data(runtime={"ref": "runtime/langgraph@1.0.0", "profile": "kit"})
        )


def test_the_schema_document_has_the_runtime_field():
    schema = json.loads((ROOT / "docs/manifest.schema.json").read_text())
    assert schema["$defs"]["RuntimeInfo"]["properties"]["profile"]["const"] == "kit"
    assert "runtime" in schema["properties"] and "runtime" not in schema.get("required", [])


def test_a_package_leaves_an_unset_runtime_out_and_keeps_a_set_one():
    fields = {
        "port": 8080,
        "health_path": "/health",
        "aliases": ["support-model"],
        "budget_usd": None,
        "rpm": None,
        "mcp_access": [],
    }
    assert DeployTemplate.model_validate(fields).runtime is None
    kit = DeployTemplate.model_validate(
        {**fields, "runtime": {"ref": "runtime/langgraph@1.0.0", "profile": "kit"}}
    )
    assert kit.model_dump(mode="json")["runtime"]["profile"] == "kit"
    with pytest.raises(ValidationError):
        DeployTemplate.model_validate({**fields, "runtime": {"ref": "x", "profile": "kit"}})


# -- registry runtimes ----------------------------------------


def test_registry_runtimes_lists_the_resolved_view(project, capsys):  # noqa: F811
    builtin = project.parent / "builtin"
    shutil.copytree(ADK / "1.4.0", builtin / "runtime" / "adk" / "1.4.0")
    shutil.copytree(ADK / "1.0.6", builtin / "runtime" / "adk" / "1.0.6")
    root = project / "registry"
    kit_runtime(root)
    version(root, "runtime", "newer", {}, extra=KIT.replace("protocol: 1", "protocol: 3"))
    version(root, "runtime", "broken", {}, extra="protocol: 1\nprofile: kit\n")
    version(root, "runtime", "nomodules", {"runtime/server.py": ""}, extra=KIT)

    assert cli.main(["registry", "runtimes", "--json", "--project", str(project)]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["schema_version"] == 1
    rows = {row["ref"]: row for row in view["runtimes"]}
    # Run end to end first, then by name, newest version first.
    assert [row["ref"] for row in view["runtimes"]] == [
        "runtime/adk@1.4.0",
        "runtime/adk@1.0.6",
        "runtime/broken@1.0.0",
        "runtime/fake@1.0.0",
        "runtime/newer@1.0.0",
        "runtime/nomodules@1.0.0",
    ]
    assert rows["runtime/adk@1.4.0"] == {
        "ref": "runtime/adk@1.4.0",
        "name": "adk",
        "version": "1.4.0",
        "root_kind": "builtin",
        "display_name": "Google ADK",
        "framework": "google-adk",
        "protocol": 1,
        "profile": "v1",
        "capabilities": ["knowledge", "mcp", "notes"],
        "limits": {"knowledge": 1, "mcp_servers": 10, "tools": 50},
        "reserved_tool_names": sorted(LEGACY_ADK.reserved_tool_names),
        "verified": True,
        "usable": True,
    }
    assert rows["runtime/adk@1.0.6"]["capabilities"] == ["knowledge", "notes"]
    assert not rows["runtime/adk@1.0.6"]["verified"]
    fake = rows["runtime/fake@1.0.0"]
    assert fake["root_kind"] == "project" and fake["usable"] and not fake["verified"]
    assert (fake["display_name"], fake["kit"], fake["profile"]) == (
        "Example Framework",
        "1.0.0",
        "kit",
    )
    assert fake["notes"] == "Needs nothing special."
    assert fake["docs"] == "docs/runtimes/example.md"
    assert rows["runtime/newer@1.0.0"] == {
        "ref": "runtime/newer@1.0.0",
        "name": "newer",
        "version": "1.0.0",
        "root_kind": "project",
        "verified": False,
        "usable": False,
        "code": "PROTOCOL_UNSUPPORTED",
        "reason": "protocol 3 is not supported by this AgentBreeder",
    }
    assert (rows["runtime/broken@1.0.0"]["code"], rows["runtime/broken@1.0.0"]["reason"]) == (
        "ARTIFACT_INVALID",
        "invalid kit",
    )
    assert rows["runtime/nomodules@1.0.0"]["reason"] == "missing runtime/modules.json"

    assert cli.main(["registry", "runtimes", "--project", str(project)]) == 0
    text = capsys.readouterr().out
    assert "runtime/adk@1.4.0  Google ADK  protocol 1 (v1)  run end to end\n" in text
    assert (
        "runtime/fake@1.0.0  Example Framework  protocol 1 (kit, kit 1.0.0)  not run end to end"
        "  (project registry)\n" in text
    )
    assert "  capabilities: knowledge, mcp; limits: knowledge 1, mcp_servers 1, tools 2\n" in text
    assert "  reserved tool names: handoff, search_knowledge_base\n" in text
    assert (
        "runtime/newer@1.0.0  cannot be used: protocol 3 is not supported by this AgentBreeder "
        "(PROTOCOL_UNSUPPORTED)  (project registry)" in text
    )


def test_registry_runtimes_on_the_shipped_registry(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    assert cli.main(["registry", "runtimes", "--json", "--project", str(tmp_path)]) == 0
    rows = json.loads(capsys.readouterr().out)["runtimes"]
    assert rows and all(row["usable"] and row["root_kind"] == "builtin" for row in rows)
    # 1.3.0 (Northwind 61/61, #1095), 1.4.0 (the starter and the 3.0.0a3 runs), 1.5.0 (sub-agents
    # and skills) and langgraph 1.0.0 (verify-langgraph-adk150.md) were run end to end; they come
    # first, by name, newest first. No older version was.
    run = [
        "runtime/adk@1.5.0",
        "runtime/adk@1.4.0",
        "runtime/adk@1.3.0",
        "runtime/langgraph@1.0.0",
    ]
    assert [row["ref"] for row in rows if row["verified"]] == run
    assert [row["ref"] for row in rows[:4]] == run
    by_ref = {row["ref"]: row for row in rows}
    for older in ("1.0.4", "1.0.5", "1.0.6", "1.1.0", "1.2.0"):
        assert by_ref[f"runtime/adk@{older}"]["verified"] is False

    assert cli.main(["registry", "runtimes", "--project", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "runtime/adk@1.4.0  Google ADK  protocol 1 (v1)  run end to end\n" in text
    assert "runtime/adk@1.5.0  Google ADK  protocol 1 (v1)  run end to end\n" in text
    assert "runtime/adk@1.3.0  Google ADK  protocol 1 (v1)  run end to end\n" in text
    assert "runtime/adk@1.2.0  Google ADK  protocol 1 (v1)  not run end to end\n" in text


def test_registry_runtimes_marks_a_symlinked_version_unusable(project, capsys):  # noqa: F811
    root = project / "registry"
    real = kit_runtime(project.parent / "elsewhere")
    shutil.rmtree(root / "runtime")
    (root / "runtime").mkdir()
    (root / "runtime" / "fake").symlink_to(real.parent, target_is_directory=True)
    assert cli.main(["registry", "runtimes", "--json", "--project", str(project)]) == 0
    (row,) = json.loads(capsys.readouterr().out)["runtimes"]
    assert (row["usable"], row["code"]) == (False, "ENTRY_UNSAFE")


def test_registry_runtimes_refuses_a_version_with_a_link_inside(project, capsys):  # noqa: F811
    """A linked manifest or `runtime/` would be read from outside the version; build's copy
    refuses it, so the listing must not call it usable."""
    root = project / "registry"
    outside = kit_runtime(project.parent / "elsewhere")
    for linked in ("manifest.yaml", "runtime"):
        path = kit_runtime(root)
        target = path / linked
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        target.symlink_to(outside / linked)
        assert cli.main(["registry", "runtimes", "--json", "--project", str(project)]) == 0
        (row,) = json.loads(capsys.readouterr().out)["runtimes"]
        assert (row["usable"], row["code"]) == (False, "ENTRY_UNSAFE"), linked
        assert "display_name" not in row


@pytest.mark.parametrize("name", ["1.0", "1.0.0.0", "01.0.0", "1..0", "9" * 200])
def test_a_directory_that_is_not_a_version_does_not_stop_the_listing(
    project,  # noqa: F811
    capsys,
    name,
):
    root = project / "registry"
    kit_runtime(root)
    (root / "runtime" / "short" / name).mkdir(parents=True)
    assert cli.main(["registry", "runtimes", "--json", "--project", str(project)]) == 0
    rows = {row["name"]: row for row in json.loads(capsys.readouterr().out)["runtimes"]}
    assert rows["fake"]["usable"]
    assert (rows["short"]["usable"], rows["short"]["code"]) == (False, "REF_UNPINNED")
    assert rows["short"]["version"] == name
    assert cli.main(["registry", "runtimes", "--project", str(project)]) == 0
    assert "cannot be used" in capsys.readouterr().out


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_file_makes_one_version_unusable_not_the_whole_listing(
    project,  # noqa: F811
    capsys,
    monkeypatch,
):
    root = project / "registry"
    secret = kit_runtime(root) / "runtime" / "server.py"
    version(root, "runtime", "other", {"runtime/modules.json": MODULES}, extra=KIT)
    secret.chmod(0)
    try:
        assert cli.main(["registry", "runtimes", "--json", "--project", str(project)]) == 0
        rows = {row["ref"]: row for row in json.loads(capsys.readouterr().out)["runtimes"]}
        monkeypatch.setenv(registry.PATH_ENV, str(root))
        with pytest.raises(newagent.NewAgentError) as caught:
            newagent.plan("helper", "gemini", project.parent / "new", runtime="runtime/fake@1.0.0")
    finally:
        secret.chmod(0o644)
    assert rows["runtime/other@1.0.0"]["usable"]
    assert rows["runtime/fake@1.0.0"] == {
        "ref": "runtime/fake@1.0.0",
        "name": "fake",
        "version": "1.0.0",
        "root_kind": "project",
        "verified": False,
        "usable": False,
        "code": "ENTRY_UNREADABLE",
        "reason": "a file in the version cannot be read",
    }
    assert caught.value.code == "ENTRY_UNREADABLE" and str(project) not in str(caught.value)


def test_new_agent_refuses_a_runtime_that_changes_while_it_is_read(tmp_path, shared, monkeypatch):
    digests = iter(["sha256:" + "a" * 64, "sha256:" + "b" * 64])
    monkeypatch.setattr(registry, "copy_and_hash", lambda path: next(digests))
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.plan("helper", "gemini", tmp_path / "helper", runtime="runtime/fake@1.0.0")
    assert caught.value.code == "DIGEST_MISMATCH"


# -- new agent --runtime ----------------------------------------


def test_the_default_runtime_is_adk_and_the_starter_is_what_it_was(tmp_path):
    plan = newagent.plan("helper", "gemini", tmp_path / "helper", today=TODAY)
    named = newagent.plan(
        "helper", "gemini", tmp_path / "helper", today=TODAY, runtime=newagent.RUNTIME
    )
    assert newagent.RUNTIME.startswith("runtime/adk@")
    assert named.files == plan.files and named.runtime == plan.runtime == newagent.RUNTIME
    assert named.summary(dry_run=True) == plan.summary(dry_run=True)
    assert not named.lock_after_move
    assert "runtime_root_kind" not in plan.summary(dry_run=True)
    assert plan.summary(dry_run=True)["verified"]


def test_new_agent_pins_another_builtin_runtime_version(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    target = tmp_path / "helper"
    code = cli.main(
        ["new", "agent", "helper", "--model", "gemini", "--runtime", "runtime/adk@1.3.0"]
        + ["--dir", str(target), "--json"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["runtime"] == "runtime/adk@1.3.0" and not summary["verified"]
    assert "runtime: runtime/adk@1.3.0\n" in (target / "agent.yaml").read_text()
    assert "runtime/adk@1.3.0" in Lock.load(target / "agent.lock").entries


def test_new_agent_says_when_the_runtime_is_not_the_one_that_was_run(tmp_path, capsys):
    target = tmp_path / "helper"
    args = ["new", "agent", "helper", "--model", "gemini", "--dir", str(target), "--dry-run"]
    assert cli.main([*args, "--runtime", "runtime/adk@1.3.0"]) == 0
    assert (
        "runtime/adk@1.3.0 is not the runtime version this starter has been run end to end on "
        f"({newagent.RUNTIME})." in capsys.readouterr().out
    )
    assert cli.main(args) == 0
    assert "is not the runtime version" not in capsys.readouterr().out


def test_the_starter_on_langgraph_is_verified_with_gemini_only(tmp_path, capsys, monkeypatch):
    """runtime/langgraph@1.0.0 ran the starter with gemini on its default model
    (verify-langgraph-adk150.md, `newagent.STARTER_RUNTIMES`); another provider or model id is
    not verified, and the command says which."""
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    lg = "runtime/langgraph@1.0.0"
    assert newagent.STARTER_RUNTIMES == {lg: {"gemini"}}
    assert newagent.starter_verified(lg, "gemini", newagent.PROVIDERS["gemini"].model)
    assert not newagent.starter_verified(lg, "gemini", "gemini/gemini-2.5-flash")
    assert not newagent.starter_verified(lg, "openai", newagent.PROVIDERS["openai"].model)
    assert not newagent.starter_verified("runtime/adk@1.5.0", "gemini", "gemini/gemini-3.8-flash")
    assert not newagent.starter_verified(newagent.RUNTIME, "anthropic", "anthropic/claude-x")

    def run(model: str) -> tuple[dict, str]:
        args = ["new", "agent", "helper", "--model", model, "--runtime", lg, "--dry-run"]
        assert cli.main([*args, "--dir", str(tmp_path / model), "--json"]) == 0
        summary = json.loads(capsys.readouterr().out)
        assert cli.main([*args, "--dir", str(tmp_path / model)]) == 0
        return summary, capsys.readouterr().out

    summary, text = run("gemini")
    assert summary["runtime"] == lg and summary["verified"] is True
    assert "is not the runtime version" not in text and "only, not with" not in text
    summary, text = run("openai")
    assert summary["verified"] is False
    assert (
        f"This starter has been run end to end on {lg} with gemini only, not with openai." in text
    )
    assert "is not the runtime version" not in text


@pytest.fixture
def shared(tmp_path, monkeypatch) -> Path:
    """A registry on AGENTBREEDER_REGISTRY_PATH with `runtime/fake@1.0.0`, a kit runtime."""
    root = tmp_path / "shared-registry"
    kit_runtime(root)
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    return root


def test_new_agent_on_a_runtime_from_the_registry_path(tmp_path, shared, capsys):
    target = tmp_path / "helper"
    code = cli.main(
        ["new", "agent", "helper", "--model", "gemini", "--runtime", "runtime/fake@1.0.0"]
        + ["--dir", str(target)]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Code that runs in agents, not shipped with AgentBreeder: runtime/fake@1.0.0." in out
    entry = Lock.load(target / "agent.lock").entries["runtime/fake@1.0.0"]
    assert entry["sha256"] == registry.copy_and_hash(shared / "runtime" / "fake" / "1.0.0")
    summary = newagent.plan(
        "other", "gemini", tmp_path / "other", today=TODAY, runtime="runtime/fake@1.0.0"
    ).summary(dry_run=True)
    assert summary["runtime_root_kind"] == "path" and not summary["verified"]


@pytest.mark.parametrize(
    "runtime,code",
    [
        ("adk", "RUNTIME_INVALID"),
        ("tool/orders@1.0.0", "RUNTIME_INVALID"),
        ("runtime/adk@9.9.9", "REF_NOT_FOUND"),
        ("runtime/secret-‮@1.0.0", "RUNTIME_INVALID"),
    ],
)
def test_new_agent_refuses_a_bad_runtime(tmp_path, capsys, monkeypatch, runtime, code):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    target = tmp_path / "helper"
    args = ["new", "agent", "helper", "--model", "gemini", "--dir", str(target), "--json"]
    assert cli.main([*args, "--runtime", runtime]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == code
    assert f"[{code}]" in captured.err and not target.exists()
    if code == "RUNTIME_INVALID":
        assert runtime not in captured.err  # the supplied text is never echoed


def test_new_agent_refuses_a_runtime_it_could_not_build_on(tmp_path, monkeypatch):
    root = tmp_path / "shared-registry"
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    for manifest, modules, code in (
        (KIT.replace("protocol: 1", "protocol: 7"), MODULES, "PROTOCOL_UNSUPPORTED"),
        (KIT.replace("kit: 1.0.0\n", ""), MODULES, "ARTIFACT_INVALID"),
        (KIT, None, "ARTIFACT_INVALID"),
    ):
        kit_runtime(root, manifest, modules)
        with pytest.raises(newagent.NewAgentError) as caught:
            newagent.plan("helper", "gemini", tmp_path / "helper", runtime="runtime/fake@1.0.0")
        assert caught.value.code == code, caught.value
        assert "runtime/fake@1.0.0" in str(caught.value)


@pytest.mark.parametrize("flag", ["--from-spec", "--stack-for"])
def test_runtime_clashes_with_the_modes_that_take_it_from_a_file(tmp_path, capsys, flag):
    path = tmp_path / "file"
    path.write_text("{}")
    args = ["new", "agent", "helper", "--runtime", "runtime/adk@1.3.0", flag, str(path)]
    if flag == "--stack-for":
        args += ["--model", "gemini"]
    assert cli.main(args) == 2
    assert "[USAGE]" in capsys.readouterr().err


# -- new agent --from-spec ----------------------------------------


def _spec(**fields) -> dict:
    return {
        "schema_version": 2,
        "name": "helper",
        "model": "gemini",
        "prompt": {"text": "Be helpful."},
        "evals": {
            "cases": [
                {"id": "c1", "turns": ["hi"], "reference": "A greeting."},
                {"id": "c2", "turns": ["bye"], "reference": "A goodbye."},
            ]
        },
        **fields,
    }


def _planned(tmp_path: Path, data: dict):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)
    return agentspec.plan("helper", path, tmp_path / "helper", today=TODAY)


def test_a_spec_is_checked_against_its_runtime_as_build_checks_it(tmp_path, shared):
    plan, extra = _planned(tmp_path, _spec(runtime="runtime/fake@1.0.0"))
    assert plan.runtime == "runtime/fake@1.0.0" and plan.runtime_root_kind == "path"
    assert not extra["combination"]["verified"]  # no run on this runtime

    kit_runtime(shared, KIT.replace("mcp_servers: 1", "mcp_servers: 0"))
    with pytest.raises(newagent.NewAgentError) as caught:
        _planned(
            tmp_path,
            _spec(
                runtime="runtime/fake@1.0.0",
                mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}],
            ),
        )
    assert caught.value.code == "RUNTIME_LIMIT"
    assert "runtime/fake@1.0.0 takes at most 0 mcp: entries (limits.mcp_servers)" in str(
        caught.value
    )

    kit_runtime(shared, KIT.replace("protocol: 1", "protocol: 9"))
    with pytest.raises(newagent.NewAgentError) as caught:
        _planned(tmp_path, _spec(runtime="runtime/fake@1.0.0"))
    assert caught.value.code == "PROTOCOL_UNSUPPORTED"


def test_verified_is_keyed_by_the_runtime_name():
    """The builder's check stays on each name's version (`BASE_RUNTIMES`): `runtime/adk@1.3.0`
    and 1.5.0 were run end to end (the registry label), but no plain `new agent` combination ran
    on them; runtime/langgraph@1.0.0 ran the starter (verify-langgraph-adk150.md)."""
    spec = agentspec.Spec.model_validate(_spec())
    assert agentspec.verified(spec)
    assert agentspec.runtime_verified("runtime/adk@1.3.0")
    assert agentspec.runtime_verified("runtime/adk@1.5.0")
    assert agentspec.runtime_verified("runtime/langgraph@1.0.0")
    for runtime, expected in (
        (newagent.RUNTIME, True),
        ("runtime/adk@1.3.0", False),
        ("runtime/adk@1.5.0", False),
        ("runtime/langgraph@1.0.0", True),
    ):
        pinned = agentspec.Spec.model_validate(_spec(runtime=runtime))
        assert agentspec.verified(pinned) is expected, runtime


def test_langgraph_is_verified_only_for_what_ran():
    """The starter and one tool on runtime/langgraph@1.0.0, with gemini on its default model:
    another provider, a model id, two tools, MCP, knowledge, sub-agents or skills stay
    unverified."""
    lg = "runtime/langgraph@1.0.0"
    tool = "tool/escalate-to-human@1.0.0"

    def check(**changes):
        return agentspec.verified(agentspec.Spec.model_validate(_spec(runtime=lg, **changes)))

    assert check() is True
    assert check(tools=[tool]) is True
    assert check(model="openai") is False  # ran with gemini only
    assert check(model="openrouter") is False
    assert check(model_id="gemini/gemini-2.5-flash") is False  # the default model only
    assert check(tools=[tool, "tool/clock@1.0.0"]) is False  # VERIFIED_TOOLS
    assert check(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}]) is False
    assert check(knowledge=[{"ref": "knowledge/docs@1.0.0"}]) is False
    assert check(skills=["skill/returns-policy@0.1.0"]) is False  # PART_RUNTIMES: adk@1.5.0
    sub = {
        "name": "billing",
        "description": "Answers billing questions.",
        "model": "gemini",
        "prompt": {"text": "Answer billing questions."},
    }
    assert check(subagents=[sub]) is False  # PART_RUNTIMES: adk@1.5.0
    assert check(subagents=[{**sub, "model": "openai"}]) is False


# -- tool_check and the generator ----------------------------------------


def test_tool_check_reads_modules_files_from_the_builtin_registry(tmp_path, monkeypatch):
    # Every built-in version counts: the legacy table, runtime/adk@1.5.0's own file and
    # runtime/langgraph@1.0.0's (#1083 F3).
    builtin_modules, builtin_imports = tool_check.runtime_names()
    assert builtin_modules == LEGACY_ADK.modules | ADK_150_MODULES | LANGGRAPH_100_MODULES
    assert {"litellm", "langgraph", "langchain_core"} <= builtin_imports
    assert tool_check.shadows("kit") == "shadows a runtime module"
    assert tool_check.shadows("langchain") == "shadows a package the runtime installs"
    legacy_imports = {name for names in LEGACY_ADK.imports.values() for name in names}

    root = tmp_path / "builtin"
    path = root / "runtime" / "langgraph" / "1.0.0" / "runtime"
    path.mkdir(parents=True)
    (path / "modules.json").write_text(
        runtime_profile.modules_document({"kit", "adapter"}, {"langgraph": ["langgraph"]})
    )
    monkeypatch.setattr(registry, "BUILTIN_ROOT", root)
    modules, imports = tool_check.runtime_names()
    assert modules == LEGACY_ADK.modules | {"kit", "adapter"}
    assert imports == legacy_imports | {"langgraph"}
    assert tool_check.shadows("kit") == "shadows a runtime module"
    assert tool_check.shadows("langgraph") == "shadows a package the runtime installs"
    assert tool_check.shadows("agent") == "shadows a runtime module"  # LEGACY_ADK still counts
    assert tool_check.shadows("orders") is None


def test_tool_check_refuses_a_corrupt_builtin_modules_file(tmp_path, monkeypatch):
    root = tmp_path / "builtin"
    path = root / "runtime" / "langgraph" / "1.0.0" / "runtime"
    path.mkdir(parents=True)
    (path / "modules.json").write_text("{}")
    monkeypatch.setattr(registry, "BUILTIN_ROOT", root)
    with pytest.raises(registry.RegistryError) as caught:
        tool_check.shadows("orders")
    assert caught.value.code == "ARTIFACT_INVALID"
    assert "runtime/langgraph/1.0.0/runtime/modules.json" in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def _generator():
    spec = importlib.util.spec_from_file_location(
        "runtime_modules", ROOT / "scripts" / "runtime_modules.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_builtin_runtime_version_is_in_step_with_its_modules_file():
    """The repo test that keeps `runtime/modules.json` in step with each version's files: a
    version that declares `protocol` must ship one listing exactly its own modules and its
    lock's packages; a version published before the key has none (and never gains one)."""
    generator = _generator()
    versions = generator.builtin_versions()
    assert len(versions) >= 7
    for path in versions:
        assert generator.problems(path) == [], path


def test_the_generator_writes_a_file_build_accepts(tmp_path):
    generator = _generator()
    version_dir = tmp_path / "runtime" / "example" / "1.0.0"
    shutil.copytree(ADK / "1.4.0", version_dir, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = version_dir / "manifest.yaml"
    manifest.write_text(
        manifest.read_text().replace("framework: google-adk\n", "")
        + "framework: example\ndisplay_name: Example\nprotocol: 1\nprofile: v1\n"
    )
    (version_dir / "runtime" / "kit").mkdir()
    (version_dir / "runtime" / "kit" / "proxy.py").write_text("")
    (version_dir / "runtime" / "static").mkdir()  # no Python: not importable, not listed

    assert generator.problems(version_dir) == [
        f"{version_dir / 'runtime/modules.json'} is missing: run scripts/runtime_modules.py "
        f"{version_dir}"
    ]
    assert generator.main([str(version_dir)]) == 0
    assert generator.main([str(version_dir), "--check"]) == 0
    profile = runtime_profile.load(version_dir)
    found = runtime_profile.load_modules(version_dir, profile)
    assert found.modules == LEGACY_ADK.modules | {"kit"}
    assert set(found.imports) == generator.locked_packages(version_dir)
    assert found.imports["pyyaml"] == ("_yaml", "yaml")

    # Out of step: a new module, then a new locked package.
    (version_dir / "runtime" / "extra.py").write_text("")
    assert "`modules` does not match runtime/" in generator.problems(version_dir)[0]
    (version_dir / "runtime" / "extra.py").unlink()
    lock = version_dir / "runtime" / "requirements.lock"
    lock.write_text(lock.read_text() + "brand-new==1.0.0 \\\n    --hash=sha256:00\n")
    assert "does not list exactly the lock's packages" in generator.problems(version_dir)[0]
    with pytest.raises(SystemExit, match="no import names for brand-new"):
        generator.expected(version_dir)


def test_the_generator_reads_import_names_from_an_installed_lock(tmp_path):
    generator = _generator()
    site = tmp_path / "site-packages"
    info = site / "Brand_New-1.0.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: Brand_New\nVersion: 1.0.0\n")
    (info / "RECORD").write_text(
        "brand/__init__.py,,\nbrand_fast.cpython-312-x86_64-linux-gnu.so,,\n"
        "Brand_New-1.0.0.dist-info/METADATA,,\n../../bin/brand,,\nREADME.txt,,\n"
    )
    (site / "brand").mkdir()
    for name in ("brand/__init__.py", "brand_fast.cpython-312-x86_64-linux-gnu.so", "README.txt"):
        (site / name).write_text("")  # importlib.metadata lists only files that exist
    assert generator.installed_imports(site) == {"brand-new": {"brand", "brand_fast"}}
    version_dir = tmp_path / "runtime" / "example" / "1.0.0"
    (version_dir / "runtime").mkdir(parents=True)
    (version_dir / "runtime" / "server.py").write_text("")
    (version_dir / "runtime" / "requirements.lock").write_text("Brand.New==1.0.0\n")
    assert json.loads(generator.expected(version_dir, site)) == {
        "schema_version": 1,
        "modules": ["server"],
        "imports": {"brand-new": ["brand", "brand_fast"]},
    }


def test_a_legacy_version_must_not_gain_a_modules_file(tmp_path):
    generator = _generator()
    version_dir = tmp_path / "runtime" / "adk" / "1.4.0"
    shutil.copytree(ADK / "1.4.0", version_dir, ignore=shutil.ignore_patterns("__pycache__"))
    assert generator.problems(version_dir) == []
    (version_dir / "runtime" / "modules.json").write_text(MODULES)
    assert "must not ship one" in generator.problems(version_dir)[0]
