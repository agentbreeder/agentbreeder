"""`new agent --from-spec` spec v2 (#1058 SP, console spec §4.5): `runtime`, `subagents`,
`skills`, `limits`, case `tags` and the sub-agent and skill checks; v1 unchanged."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from agentbreeder import agentspec, cli, evals, newagent, registry
from agentbreeder.agentfile import load_agent_file
from agentbreeder.center import builder
from agentbreeder.lock import Lock
from agentbreeder.runtime_caps import CapabilitiesError, runtime_capabilities
from agentbreeder.services.schema import load_stack

TODAY = date(2026, 11, 13)
MARKER = "Private marker kestrel-umbra-71."  # prompt text that must never reach an error
GOLDEN = Path(__file__).parent / "fixtures" / "agentspec_v1_golden.json"
ADK = registry.BUILTIN_ROOT / "runtime" / "adk" / "1.4.0"
CAPABLE = "runtime/adk@1.99.0"  # never a built-in version (1.5.0 is one since RT)


@pytest.fixture(autouse=True)
def clean_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


def case(**changes) -> dict:
    return {
        "id": "hours",
        "turns": ["When is support open?"],
        "expect": {"must_match": ["9"]},
        "reference": "9am to 5pm Pacific.",
    } | changes


def spec(**changes) -> dict:
    return {
        "schema_version": 2,
        "name": "helper",
        "model": "gemini",
        "prompt": {"text": "You are helper. " + MARKER},
        "evals": {"cases": [case()]},
    } | changes


def sub(name="billing", **changes) -> dict:
    return {
        "name": name,
        "description": "Refunds, invoices and payment questions.",
        "model": "gemini",
        "prompt": {"text": f"You are the {name} specialist. " + MARKER},
    } | changes


def write_spec(tmp_path: Path, data, name: str = "spec.json") -> Path:
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def planned(tmp_path: Path, data):
    return agentspec.plan("helper", write_spec(tmp_path, data), tmp_path / "helper", today=TODAY)


def refused(tmp_path: Path, data) -> newagent.NewAgentError:
    with pytest.raises(newagent.NewAgentError) as caught:
        planned(tmp_path, data)
    assert MARKER not in str(caught.value)
    return caught.value


def dry_run_json(tmp_path: Path, data: dict) -> tuple[int, dict]:
    path = write_spec(tmp_path, data)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(
            ["new", "agent", data["name"], "--from-spec", str(path)]
            + ["--dir", str(tmp_path / data["name"]), "--dry-run", "--json"]
        )
    return code, json.loads(buffer.getvalue())


@pytest.fixture
def shared(tmp_path, monkeypatch) -> Path:
    """A registry on AGENTBREEDER_REGISTRY_PATH with `runtime/adk@1.99.0` (1.4.0's files and
    `capabilities: [mcp, subagents, skills]`, a stand-in that cannot clash with a built-in),
    `tool/echo@0.1.0`, `tool/ask@0.1.0` (its function named `ask_billing`),
    `skill/refund-policy@0.1.0` (an instruction pack) and `skill/prorate@0.1.0` (with a
    script)."""
    root = tmp_path / "shared-registry"
    sources = tmp_path / "sources"
    runtime = sources / "runtime-adk"
    shutil.copytree(ADK, runtime)
    manifest = runtime / "manifest.yaml"
    text = manifest.read_text().replace("version: 1.4.0", "version: 1.99.0", 1)
    text = text.replace("capabilities: [mcp]", "capabilities: [mcp, subagents, skills]")
    manifest.write_text(text)
    registry.publish(runtime, root)
    ask = registry.scaffold("tool", "ask", sources)
    text = (ask / "tool.yaml").read_text().replace("- name: echo_ask", "- name: ask_billing")
    (ask / "tool.yaml").write_text(text)
    registry.publish(ask, root)
    for kind, name, scripts in (
        ("tool", "echo", False),
        ("skill", "refund-policy", False),
        ("skill", "prorate", True),
    ):
        registry.publish(registry.scaffold(kind, name, sources, scripts=scripts), root)
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    return root


def full_spec(**changes) -> dict:
    """Two sub-agents (one on the agent's model, one on openai), tools, an MCP grant, a skill,
    the turn cap, and cases with tags and the new checks."""
    return (
        spec(
            runtime=CAPABLE,
            tools=["tool/echo@0.1.0"],
            mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me", "list_issues"]}],
            subagents=[
                sub(tools=["tool/echo@0.1.0"], max_model_calls=3),
                sub(
                    "returns",
                    model="openai",
                    max_calls_per_turn=1,
                    mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}],
                ),
            ],
            skills=["skill/refund-policy@0.1.0"],
            limits={"turn_model_calls": 9},
            evals={
                "cases": [
                    case(
                        tags=["billing", "smoke"],
                        expect={
                            "subagents_include": ["billing"],
                            "subagents_exclude": ["returns"],
                            "subagent_tools_include": {"billing": ["echo_echo"]},
                            "skills_loaded": ["refund-policy"],
                        },
                    ),
                    case(id="plain", expect={"skills_not_loaded": ["refund-policy"]}),
                ]
            },
        )
        | changes
    )


# -- v1 is unchanged -----------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(json.loads(GOLDEN.read_text())))
def test_a_v1_spec_previews_byte_identically_to_before(tmp_path, key):
    """The golden was written by `main`'s agentspec before spec v2 (#1058 SP)."""
    golden = json.loads(GOLDEN.read_text())[key]
    path = write_spec(tmp_path, golden["spec"])
    plan, extra = agentspec.plan("helper", path, Path("/nonexistent-golden/helper"), today=TODAY)
    assert agentspec.summary(plan, extra, dry_run=True) == golden["summary"]
    assert golden["summary"]["spec_version"] == 1


def test_a_v2_spec_without_v2_fields_writes_the_v1_files(tmp_path):
    golden = json.loads(GOLDEN.read_text())["gemini-mcp"]
    path = write_spec(tmp_path, golden["spec"] | {"schema_version": 2})
    plan, extra = agentspec.plan("helper", path, Path("/nonexistent-golden/helper"), today=TODAY)
    shown = agentspec.summary(plan, extra, dry_run=True)
    assert shown["files"] == golden["summary"]["files"]
    assert shown["plan_sha256"] == golden["summary"]["plan_sha256"]
    assert shown["spec_version"] == 2


@pytest.mark.parametrize(
    "changes, where",
    [
        ({"runtime": CAPABLE}, "runtime"),
        ({"subagents": [sub()]}, "subagents"),
        ({"skills": []}, "skills"),
        ({"limits": {"turn_model_calls": 4}}, "limits"),
        ({"evals": {"cases": [case(tags=["smoke"])]}}, "evals.cases[0].tags"),
        (
            {"evals": {"cases": [case(expect={"subagents_include": ["billing"]})]}},
            "evals.cases[0].expect.subagents_include",
        ),
        (
            {"evals": {"cases": [case(expect={"skills_not_loaded": ["x"]})]}},
            "evals.cases[0].expect.skills_not_loaded",
        ),
    ],
)
def test_v2_fields_are_refused_in_a_v1_spec(tmp_path, changes, where):
    error = refused(tmp_path, spec(schema_version=1, **changes))
    assert str(error) == f"[SPEC_INVALID] {where}: needs schema_version 2"


@pytest.mark.parametrize("version", [0, 3, "2", True, None])
def test_other_versions(tmp_path, version):
    error = refused(tmp_path, spec(schema_version=version))
    assert error.code == ("SPEC_INVALID" if version is None else "SPEC_VERSION_UNSUPPORTED")


# -- the caps ------------------------------------------------------------------------------------


def padded(data: dict, size: int) -> str:
    text = json.dumps(data)
    return text[:-1] + " " * (size - len(text)) + "}"


def test_v2_may_be_512_kib_and_v1_256(tmp_path):
    over_v1 = agentspec.MAX_SPEC_BYTES_V1 + 1
    assert refused(tmp_path, padded(spec(schema_version=1), over_v1)).code == "SPEC_TOO_LARGE"
    assert planned(tmp_path, padded(spec(), over_v1))[1]["spec_version"] == 2
    exact = agentspec.MAX_SPEC_BYTES_V2
    assert planned(tmp_path, padded(spec(), exact))[1]["spec_version"] == 2
    assert refused(tmp_path, padded(spec(), exact + 1)).code == "SPEC_TOO_LARGE"


# -- runtime capabilities ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"subagents": [sub()]}, "RUNTIME_NO_SUBAGENTS"),
        ({"skills": ["skill/refund-policy@0.1.0"]}, "RUNTIME_NO_SKILLS"),
        (
            {
                "runtime": "runtime/adk@1.0.6",
                "mcp": [{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}],
            },
            "MCP_RUNTIME_UNSUPPORTED",
        ),
    ],
)
def test_a_runtime_without_the_capability_is_refused_as_build_would(
    tmp_path, shared, changes, code
):
    """No built-in runtime has sub-agents or skills yet, so the default (newagent.RUNTIME)
    refuses them with build's codes, before anything is written."""
    assert refused(tmp_path, spec(**changes)).code == code
    assert not (tmp_path / "helper").exists()


def test_a_script_skill_is_refused_until_a_runtime_runs_scripts(tmp_path, shared):
    error = refused(tmp_path, spec(runtime=CAPABLE, skills=["skill/prorate@0.1.0"]))
    assert error.code == "RUNTIME_NO_SKILL_SCRIPTS" and "skills[0]" in str(error)


def test_a_pinned_runtime_is_written_listed_and_unverified(tmp_path, shared):
    plan, extra = planned(tmp_path, spec(runtime=CAPABLE))
    assert f"runtime: {CAPABLE}\n" in plan.files["agent.yaml"]
    assert extra["refs"][0]["ref"] == CAPABLE and extra["refs"][0]["root_kind"] == "path"
    assert extra["combination"] == {"parts": ["model", "prompt", "evals"], "verified": False}
    assert agentspec.summary(plan, extra, dry_run=True)["runtime"] == CAPABLE
    # the default runtime, named, was run end to end
    assert planned(tmp_path, spec(runtime=newagent.RUNTIME))[1]["combination"]["verified"]


def test_a_runtime_ref_of_another_kind_is_refused(tmp_path):
    error = refused(tmp_path, spec(runtime="tool/echo@0.1.0"))
    assert error.code == "SPEC_INVALID" and "runtime" in str(error)


@pytest.mark.parametrize(
    "text",
    ["capabilities: mcp\n", "capabilities: [mcp, mcp]\n", "capabilities: [MCP]\n", "- a\n"],
)
def test_the_shared_capabilities_parser_refuses(tmp_path, text):
    (tmp_path / "manifest.yaml").write_text(text)
    with pytest.raises(CapabilitiesError):
        runtime_capabilities(tmp_path)


def test_the_shared_capabilities_parser(tmp_path):
    assert runtime_capabilities(ADK) == frozenset({"mcp"})
    (tmp_path / "manifest.yaml").write_text("kind: runtime\n")
    assert runtime_capabilities(tmp_path) == frozenset()
    (tmp_path / "manifest.yaml").write_text("a: [\n")
    with pytest.raises(CapabilitiesError):
        runtime_capabilities(tmp_path)


# -- sub-agents, skills and limits ----------------------------------------------------------------


def test_a_full_v2_project_is_written_and_loads(tmp_path, shared):
    plan, extra = planned(tmp_path, full_spec())
    preview = agentspec.summary(plan, extra, dry_run=True)
    newagent.create(plan)
    project = tmp_path / "helper"
    for entry in preview["files"]:
        assert (project / entry["path"]).read_text() == entry["content"]
    agent = load_agent_file(project / "agent.yaml")
    assert agent.runtime == CAPABLE
    billing, returns = agent.subagents
    # CC2: the agent's model reuses `chat`; another provider gets its own alias
    assert billing.model.alias == "chat" and returns.model.alias == "chat-returns"
    assert billing.model.max_model_calls == 3 and returns.model.max_model_calls == 2
    assert returns.max_calls_per_turn == 1 and billing.max_calls_per_turn == 2
    assert billing.prompt.ref == "prompt/helper-billing-prompt@0.1.0"
    assert billing.description == "Refunds, invoices and payment questions."
    assert [t.ref for t in billing.tools] == ["tool/echo@0.1.0"]
    assert [(m.ref, m.tools) for m in returns.mcp] == [("mcp/github@1.0.0", ["get_me"])]
    assert [s.ref for s in agent.skills] == ["skill/refund-policy@0.1.0"]
    assert agent.limits.turn_model_calls == 9
    assert (project / "src/subagents/billing/prompt.md").read_text().endswith(MARKER + "\n")
    assert agent.aliases() == ["chat", "chat-returns"]
    stack = load_stack(project / "stack.yaml")
    assert list(stack.environment.models) == ["chat", "judge", "chat-returns"]
    config = (project / "stack/litellm/config.yaml").read_text()
    assert (
        '  - model_name: "chat-returns"\n'
        "    litellm_params: {model: openai/gpt-5.6, api_key: os.environ/OPENAI_API_KEY}\n"
    ) in config
    lock = Lock.load(project / "agent.lock")
    assert {
        CAPABLE,
        "prompt/helper-billing-prompt@0.1.0",
        "prompt/helper-returns-prompt@0.1.0",
        "skill/refund-policy@0.1.0",
        "tool/echo@0.1.0",
    } <= set(lock.entries)
    cases = evals.load_eval_set(project / "src/evals").cases
    assert cases[0]["tags"] == ["billing", "smoke"]
    assert cases[0]["expect"]["subagent_tools_include"] == {"billing": ["echo_echo"]}
    assert preview["aliases"] == ["chat", "judge", "chat-returns"]
    assert preview["keys"][:2] == ["GEMINI_API_KEY", "OPENAI_API_KEY"]
    assert [p["ref"] for p in preview["published"]] == [
        "prompt/helper-prompt@0.1.0",
        "eval/helper-evals@0.1.0",
        "prompt/helper-billing-prompt@0.1.0",
        "prompt/helper-returns-prompt@0.1.0",
    ]
    assert extra["combination"] == {
        "parts": ["model", "prompt", "evals", "mcp", "skills", "subagents", "tools"],
        "verified": False,
    }
    assert [(r["ref"], r["kind"]) for r in extra["refs"]] == [
        (CAPABLE, "runtime"),
        ("tool/echo@0.1.0", "tool"),
        ("mcp/github@1.0.0", "mcp"),
        ("skill/refund-policy@0.1.0", "skill"),
    ]
    assert extra["refs"][-1]["runs_code"] is False


def test_the_cli_answers_a_v2_dry_run(tmp_path, shared):
    code, out = dry_run_json(tmp_path, full_spec())
    assert code == 0 and out["spec_version"] == 2 and out["runtime"] == CAPABLE
    assert not (tmp_path / "helper").exists()
    directory = str(tmp_path / "helper")
    shown = builder.summary(out, "helper", True, "gemini", directory, 2)
    assert shown["aliases"] == ["chat", "judge", "chat-returns"]
    with pytest.raises(builder.BadOutput):  # the version sent is the version answered
        builder.summary(out, "helper", True, "gemini", directory, 1)


def test_the_largest_form_fits_the_console_caps(tmp_path, shared):
    """Five sub-agents, each with a 32 KiB prompt and 20 tools, 50 agent tools, ten skills and
    20 long cases: the spec is over the v1 cap and under v2's, and the answer passes
    `center/builder.summary` (MAX_FILES, MAX_REFS)."""
    sources = tmp_path / "more"
    for n in range(100):
        registry.publish(registry.scaffold("tool", f"t{n}", sources), shared)
    for n in range(10):
        registry.publish(registry.scaffold("skill", f"s{n}", sources), shared)
    big = "x" * (agentspec.MAX_PROMPT - 1)
    data = spec(
        runtime=CAPABLE,
        prompt={"text": big},
        tools=[f"tool/t{n}@0.1.0" for n in range(50)],
        subagents=[
            sub(
                f"s{i}",
                prompt={"text": big},
                description="d" * agentspec.MAX_DESCRIPTION,
                tools=[f"tool/t{n}@0.1.0" for n in range(50 + 10 * i, 60 + 10 * i)]
                + [f"tool/t{n}@0.1.0" for n in range(10)],
            )
            for i in range(5)
        ],
        skills=[f"skill/s{n}@0.1.0" for n in range(10)],
        limits={"turn_model_calls": 40},
        evals={"cases": [case(id=f"c{n}", reference="r" * 4000) for n in range(20)]},
    )
    assert builder.MAX_SPEC_BYTES_V1 < len(builder.encode(data)) <= builder.MAX_SPEC_BYTES_V2
    assert builder.request({"spec": data})[0] == "helper"
    code, out = dry_run_json(tmp_path, data)
    assert code == 0, out
    assert len(out["files"]) == 17
    shown = builder.summary(out, "helper", True, "gemini", str(tmp_path / "helper"), 2)
    assert len(shown["refs"]) == 1 + 100 + 10
    assert len(shown["files"]) <= builder.MAX_FILES


@pytest.mark.parametrize(
    "changes, code, where",
    [
        ({"subagents": [sub(), sub()]}, "SPEC_INVALID", "subagents: duplicate name"),
        ({"subagents": [sub("Billing")]}, "SPEC_INVALID", "subagents.0.name"),
        ({"subagents": [sub(subagents=[])]}, "SPEC_INVALID", "subagents.0.subagents"),
        ({"subagents": [sub(model="mistral")]}, "MODEL_UNSUPPORTED", "subagents[0].model"),
        ({"subagents": [sub(model_id="openai/x")]}, "MODEL_ID_INVALID", "subagents[0]"),
        ({"subagents": [sub(model="ollama")]}, "SPEC_UNSUPPORTED", "subagents[0].model"),
        ({"subagents": [sub(max_calls_per_turn=4)]}, "SPEC_INVALID", "max_calls_per_turn"),
        ({"subagents": [sub(description="d" * 501)]}, "SPEC_INVALID", "description"),
        ({"subagents": [sub(description=" ")]}, "SPEC_INVALID", "description is empty"),
        ({"subagents": [sub(description="a‮b")]}, "SPEC_INVALID", "description has"),
        ({"subagents": [sub(prompt={"text": "x" * (32 * 1024 + 1)})]}, "SPEC_INVALID", "prompt"),
        ({"subagents": [sub() | {"x": 1}]}, "SPEC_INVALID", "subagents.0.x"),
        ({"subagents": [sub(f"s{n}") for n in range(6)]}, "SPEC_INVALID", "subagents"),
        ({"limits": {"turn_model_calls": 9}}, "SPEC_INVALID", "limits: valid only with"),
        (
            {"subagents": [sub()], "limits": {"turn_model_calls": 41}},
            "SPEC_INVALID",
            "limits.turn_model_calls",
        ),
        (
            {"subagents": [sub(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])]},
            "SUBAGENT_MCP_UNGRANTED",
            "subagents[0].mcp[0]",
        ),
        (
            {
                "mcp": [{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}],
                "subagents": [sub(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["list_issues"]}])],
            },
            "SUBAGENT_MCP_UNGRANTED",
            "subagents[0].mcp[0]",
        ),
        (
            {"subagents": [sub(tools=["tool/echo@0.1.0", "tool/echo@0.1.0"])]},
            "SPEC_INVALID",
            "subagents[0].tools",
        ),
        (
            {"evals": {"cases": [case(expect={"subagents_include": ["billing"]})]}},
            "SPEC_INVALID",
            "names a sub-agent the spec does not have",
        ),
        (
            {
                "subagents": [sub()],
                "evals": {"cases": [case(expect={"subagent_tools_include": {"other": ["x"]}})]},
            },
            "SPEC_INVALID",
            "names a sub-agent the spec does not have",
        ),
        (
            {"evals": {"cases": [case(expect={"skills_loaded": ["refund-policy"]})]}},
            "SPEC_INVALID",
            "names a skill the spec does not pick",
        ),
        ({"evals": {"cases": [case(tags=["Bad"])]}}, "SPEC_INVALID", "tags"),
        ({"evals": {"cases": [case(tags=["a", "a"])]}}, "SPEC_INVALID", "tags"),
        ({"evals": {"cases": [case(tags=list("abcdef"))]}}, "SPEC_INVALID", "tags"),
        (
            {"evals": {"cases": [case(expect={"subagent_tools_include": {"billing": []}})]}},
            "SPEC_INVALID",
            "subagent_tools_include",
        ),
        (
            {"evals": {"cases": [case(expect={"skills_loaded": ["Bad Name"]})]}},
            "SPEC_INVALID",
            "skills_loaded",
        ),
        (
            {"skills": ["skill/refund-policy@0.1.0", "skill/refund-policy@0.1.0"]},
            "SPEC_INVALID",
            "skills[1]",
        ),
        ({"skills": ["tool/echo@0.1.0"]}, "SPEC_INVALID", "skills[0]"),
    ],
)
def test_bad_v2_specs_are_refused_by_location(tmp_path, shared, changes, code, where):
    error = refused(tmp_path, spec(runtime=CAPABLE, **changes))
    assert error.code == code, error
    assert where in str(error)
    assert not (tmp_path / "helper").exists()


def test_one_tool_version_across_the_agent_and_its_subagents(tmp_path, shared):
    sources = tmp_path / "v2"
    registry.publish(registry.scaffold("tool", "echo", sources, version="0.2.0"), shared)
    data = spec(
        runtime=CAPABLE, tools=["tool/echo@0.1.0"], subagents=[sub(tools=["tool/echo@0.2.0"])]
    )
    assert refused(tmp_path, data).code == "SPEC_INVALID"
    # the same version in the agent and two sub-agents is placed once
    same = spec(
        runtime=CAPABLE,
        tools=["tool/echo@0.1.0"],
        subagents=[sub(tools=["tool/echo@0.1.0"]), sub("other", tools=["tool/echo@0.1.0"])],
    )
    assert [r["ref"] for r in planned(tmp_path, same)[1]["refs"]] == [CAPABLE, "tool/echo@0.1.0"]


def test_a_subagent_named_like_a_tool_function_is_build_s_clash(tmp_path, shared):
    """`tool/ask` defines `ask_billing`, which is also the agent's tool for sub-agent billing."""
    data = spec(runtime=CAPABLE, tools=["tool/ask@0.1.0"], subagents=[sub()])
    error = refused(tmp_path, data)
    assert error.code == "SUBAGENT_NAME_CLASH" and "subagents[0].name" in str(error)


def test_customer_notes_on_a_subagent_keeps_the_agent_file_s_code(tmp_path, shared):
    registry.publish(registry.scaffold("tool", "customer-notes", tmp_path / "notes"), shared)
    data = spec(runtime=CAPABLE, subagents=[sub(tools=["tool/customer-notes@0.1.0"])])
    error = refused(tmp_path, data)
    assert error.code == "SUBAGENT_NOTES" and "subagents[0].tools[0].ref" in str(error)


def test_a_prompt_version_name_over_63_characters_is_refused(tmp_path, shared):
    name = "a" * 31
    path = write_spec(tmp_path, spec(name=name, runtime=CAPABLE, subagents=[sub("b" * 32)]))
    with pytest.raises(newagent.NewAgentError) as caught:
        agentspec.plan(name, path, tmp_path / name, today=TODAY)
    assert caught.value.code == "SPEC_INVALID" and "subagents[0].name" in str(caught.value)


def test_a_subagent_on_the_agent_s_model_adds_no_alias_or_key(tmp_path, shared):
    plan, extra = planned(tmp_path, spec(runtime=CAPABLE, subagents=[sub()]))
    assert extra["keys"] == ["GEMINI_API_KEY"]
    assert agentspec.summary(plan, extra, dry_run=True)["aliases"] == ["chat", "judge"]
    assert 'models: ["chat", "judge"]' in plan.files["stack.yaml"]
    # another model id on the same provider is another alias, with no new key
    other = spec(runtime=CAPABLE, subagents=[sub(model_id="gemini/gemini-2.5-flash")])
    plan, extra = planned(tmp_path, other)
    assert extra["keys"] == ["GEMINI_API_KEY"]
    aliases = agentspec.summary(plan, extra, dry_run=True)["aliases"]
    assert aliases == ["chat", "judge", "chat-billing"]


def test_the_digest_covers_the_subagent_prompts(tmp_path, shared):
    first = planned(tmp_path, spec(runtime=CAPABLE, subagents=[sub()]))[1]["plan_sha256"]
    changed = spec(runtime=CAPABLE, subagents=[sub(prompt={"text": "Another prompt."})])
    assert planned(tmp_path, changed)[1]["plan_sha256"] != first


def test_a_deeply_nested_spec_is_invalid_not_a_crash(tmp_path):
    text = '{"schema_version": 2, "x": ' + "[" * 100000 + "]" * 100000 + "}"
    assert refused(tmp_path, text).code == "SPEC_INVALID"


def test_an_ollama_subagent_on_another_ollama_model(tmp_path, shared):
    data = spec(
        model="ollama",
        runtime=CAPABLE,
        subagents=[sub(model="ollama", model_id="ollama_chat/qwen3:4b")],
    )
    plan, extra = planned(tmp_path, data)
    assert extra["keys"] == []
    assert agentspec.summary(plan, extra, dry_run=True)["aliases"] == [
        "chat",
        "judge",
        "chat-billing",
    ]
    assert "service/litellm@1.2.0" in plan.files["stack.yaml"]
    newagent.create(plan)


def test_two_subagents_on_one_other_model_get_an_alias_each_and_one_key(tmp_path, shared):
    data = spec(runtime=CAPABLE, subagents=[sub("a", model="openai"), sub("b", model="openai")])
    plan, extra = planned(tmp_path, data)
    assert extra["keys"] == ["GEMINI_API_KEY", "OPENAI_API_KEY"]
    assert agentspec.summary(plan, extra, dry_run=True)["aliases"] == [
        "chat",
        "judge",
        "chat-a",
        "chat-b",
    ]
    assert plan.files["stack.yaml"].count("OPENAI_API_KEY") == 1
    newagent.create(plan)


def test_an_embedding_alias_that_is_a_subagent_alias_is_refused(tmp_path, shared, monkeypatch):
    """The knowledge version's own alias is checked against the sub-agents' aliases (the
    knowledge fixtures in test_agent_spec_knowledge.py name `help-embedding`)."""
    embedding = ("chat-billing", "gemini/gemini-embedding-2", 768)
    monkeypatch.setattr(agentspec, "_knowledge", lambda spec, target: ([], [], embedding))
    data = spec(runtime=CAPABLE, subagents=[sub(model="openai")])
    error = refused(tmp_path, data)
    assert error.code == "ALIAS_UNSUPPORTED" and "knowledge[0]" in str(error)
