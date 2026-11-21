"""runtime/adk@1.5.1: skills, kind (a) instruction packs (#1059 RT-3), with a stub model.

Two placed skills, as `agentbreeder build` leaves them: `refund-policy` (a body and two
reference files) and `complaint-tone` (a body only). Their text carries markers that must never
reach an event, a span or the reply's metadata.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as agent_module  # noqa: E402
import config as cfg  # noqa: E402
import skill_hash  # noqa: E402
import skills  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from test_runtime import FakeMemory, ScriptedModel, app, call, text  # noqa: E402, F401

BODY = "BODY-MARKER-7731: refunds are allowed within 30 days of purchase."
EU = "EU-MARKER-4402: in the EU the cooling-off period is 14 days."
TONE = "TONE-MARKER-9917: apologise once, then state the next step."
MARKERS = ("BODY-MARKER-7731", "EU-MARKER-4402", "TONE-MARKER-9917")
REFUND_DESCRIPTION = "How to decide and explain refunds <b>&</b> EU rules."


def manifest(name, description):
    return f"kind: skill\nname: {name}\nversion: 1.2.0\ndescription: {json.dumps(description)}\n"


def skill_md(name, description, body, extra=""):
    return f"---\nname: {name}\ndescription: {json.dumps(description)}\n{extra}---\n{body}\n"


def place(app, name, files):  # noqa: F811
    """Write a placed skill and return its agent.config.json entry (with the placed hash)."""
    directory = app / "artifacts/skill" / name
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return {
        "name": name,
        "version": "1.2.0",
        "dir": f"artifacts/skill/{name}",
        "sha256": skill_hash.placed_hash(skill_hash.placed_files(directory)),
    }


def refund_files(**changes):
    files = {
        "manifest.yaml": manifest("refund-policy", REFUND_DESCRIPTION),
        "SKILL.md": skill_md("refund-policy", REFUND_DESCRIPTION, BODY),
        "references/eu-rules.md": EU + "\n",
        "references/us-rules.txt": "US rules.\n",
    }
    files.update(changes)
    return {key: value for key, value in files.items() if value is not None}


def with_skills(app, entries):  # noqa: F811
    config = json.loads((app / "agent.config.json").read_text())
    config["model"]["max_model_calls"] = 8
    config["skills"] = entries
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()


def build(monkeypatch):
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    FakeMemory.events = []
    ScriptedModel.requests = []
    return agent_module.Runtime(
        cfg.config(), "postgresql://u:p@db:5432/agent", "gateway-key", model=ScriptedModel()
    )


@pytest.fixture
def packs(app, monkeypatch):  # noqa: F811
    tone = {
        "manifest.yaml": manifest("complaint-tone", "How to answer a complaint."),
        "SKILL.md": skill_md("complaint-tone", "How to answer a complaint.", TONE),
    }
    with_skills(
        app, [place(app, "refund-policy", refund_files()), place(app, "complaint-tone", tone)]
    )
    return build(monkeypatch)


def chat(runtime, message="hi", session="s1"):
    return asyncio.run(runtime.chat_async(session, "cust-001", message))


def events(action=None):
    return [event for event in FakeMemory.events if action is None or event[4] == action]


def answers(request):
    """Every function response in a model request, in order."""
    return [
        (p.function_response.name, p.function_response.response)
        for c in request.contents
        for p in (c.parts or [])
        if p.function_response
    ]


def load(name):
    return call("load_skill", skill_name=name)


def resource(name, path):
    return call("load_skill_resource", skill_name=name, file_path=path)


# -- what the model is offered ---------------------------------------------------------------


def test_the_model_is_offered_exactly_the_two_load_tools(packs):
    ScriptedModel.script = [text("Hi.")]
    chat(packs)
    request = ScriptedModel.requests[0]
    declared = [d.name for tool in request.config.tools for d in tool.function_declarations]
    assert declared == [
        "search_knowledge_base",
        "whoami",
        "boom",
        "load_skill",
        "load_skill_resource",
    ]
    assert not any("script" in name or "unload" in name or "list" in name for name in declared)
    # By the filter, not only because this ADK build happens to offer nothing else.
    assert packs.skill_toolset.tool_filter == ["load_skill", "load_skill_resource"]


def test_the_catalog_is_in_the_system_instruction_escaped_and_after_the_prompt(packs):
    ScriptedModel.script = [text("Hi.")]
    chat(packs)
    system = ScriptedModel.requests[0].config.system_instruction
    assert system.startswith("You are a test agent.")
    assert "<available_skills>" in system and "refund-policy" in system
    assert "complaint-tone" in system
    # A description is text, never markup in the instruction.
    assert "&lt;b&gt;&amp;&lt;/b&gt;" in system and "<b>" not in system
    # L1 only: no body, no reference, and no word of running scripts as a capability.
    assert not any(marker in system for marker in MARKERS)
    assert "run_skill_script" not in system or "cannot" in system.lower()
    assert system.index("customer_memory") < system.index("<available_skills>")


def test_an_agent_without_skills_is_as_before(app, monkeypatch):  # noqa: F811
    runtime = build(monkeypatch)
    ScriptedModel.script = [text("Hi.")]
    result = chat(runtime)
    assert "skills" not in result and runtime.skills == []
    system = ScriptedModel.requests[0].config.system_instruction
    assert "available_skills" not in system and "load_skill" not in system


# -- loading ---------------------------------------------------------------------------------


def test_loading_a_skill_returns_its_body_and_the_reply_names_it(packs):
    ScriptedModel.script = [
        load("refund-policy"),
        resource("refund-policy", "references/eu-rules.md"),
        text("Refunds within 30 days; 14 in the EU."),
    ]
    result = chat(packs)
    assert result["skills"] == [
        {"name": "refund-policy", "version": "1.2.0", "resources": ["eu-rules.md"]}
    ]
    assert result["tools"] == ["load_skill", "load_skill_resource"]
    (_, body), (_, reference) = answers(ScriptedModel.requests[2])
    assert BODY in body["instructions"] and EU in reference["content"]
    # The other skill was not loaded.
    assert TONE not in json.dumps([body, reference], default=str)
    # Only name and description reach ADK's front matter, whatever the file held.
    assert body["frontmatter"]["metadata"] == {} and not body["frontmatter"].get("allowed-tools")


def test_events_carry_the_skill_and_never_its_content(packs):
    ScriptedModel.script = [
        load("refund-policy"),
        resource("refund-policy", "references/eu-rules.md"),
        text("Done."),
    ]
    result = chat(packs)
    sha = packs.skills[0].sha256
    (loaded,) = [event[5] for event in events("tool:load_skill")]
    assert (loaded["skill"], loaded["version"], loaded["sha256"]) == (
        "refund-policy",
        "1.2.0",
        sha,
    )
    assert loaded["ok"] is True and "resource" not in loaded
    (read,) = [event[5] for event in events("tool:load_skill_resource")]
    assert (read["skill"], read["resource"], read["sha256"]) == (
        "refund-policy",
        "eu-rules.md",
        sha,
    )
    assert [event[5] for event in events("skill_loaded")] == [
        {"skill": "refund-policy", "version": "1.2.0", "sha256": sha, "agent": "test-agent"},
        {
            "skill": "refund-policy",
            "version": "1.2.0",
            "sha256": sha,
            "resource": "eu-rules.md",
            "agent": "test-agent",
        },
    ]
    stored = json.dumps([FakeMemory.events, FakeMemory.last_turn], default=str)
    assert not any(marker in stored for marker in MARKERS)
    assert {event[0] for event in events()} == {result["turn_id"]}
    # The reply's fields other than the model's own text hold no skill content either.
    shown = json.dumps({key: value for key, value in result.items() if key != "reply"})
    assert not any(marker in shown for marker in MARKERS)


def test_a_fourth_load_in_a_turn_is_refused(packs):
    ScriptedModel.script = [
        load("refund-policy"),
        load("complaint-tone"),
        load("no-such-skill"),
        load("complaint-tone"),
        text("Done."),
    ]
    result = chat(packs)
    replies = [response for _, response in answers(ScriptedModel.requests[4])]
    assert replies[3]["error_code"] == "SKILL_LOAD_LIMIT"
    assert replies[2]["error_code"] == "SKILL_NOT_FOUND"
    assert [e["name"] for e in result["skills"]] == ["refund-policy", "complaint-tone"]
    rows = [event[5] for event in events("tool:load_skill")]
    assert [row["ok"] for row in rows] == [True, True, False, False]
    assert rows[3]["error"] == "SKILL_LOAD_LIMIT" and "skill" not in rows[2]
    assert len(events("skill_loaded")) == 2


def test_a_fourth_resource_load_in_a_turn_is_refused(packs):
    ScriptedModel.script = [
        resource("refund-policy", "references/eu-rules.md"),
        resource("refund-policy", "references/us-rules.txt"),
        resource("refund-policy", "references/eu-rules.md"),
        resource("refund-policy", "references/us-rules.txt"),
        text("Done."),
    ]
    result = chat(packs)
    replies = [response for _, response in answers(ScriptedModel.requests[4])]
    assert [reply.get("error_code") for reply in replies] == [None, None, None, "SKILL_LOAD_LIMIT"]
    # Each file once, in first-read order, though one was read twice.
    assert result["skills"][0]["resources"] == ["eu-rules.md", "us-rules.txt"]


@pytest.mark.parametrize(
    "path",
    [
        "scripts/x",
        "scripts/prorate.py",
        "assets/x",
        "references/../SKILL.md",
        "references/nested/eu-rules.md",
        "references/.hidden.md",
        "references/",
        "SKILL.md",
        "/etc/passwd",
        "references/eu-rules.md/../../SKILL.md",
        7,
        ["references/eu-rules.md"],
        None,
        "",
    ],
)
def test_only_a_flat_reference_path_is_read(packs, path):
    ScriptedModel.script = [resource("refund-policy", path), text("Done.")]
    result = chat(packs)
    ((_, response),) = answers(ScriptedModel.requests[1])
    assert response["error_code"] in ("INVALID_RESOURCE_PATH", "INVALID_ARGUMENTS")
    assert "content" not in response and result["skills"] == []
    (row,) = events("tool:load_skill_resource")
    assert row[5]["ok"] is False and "resource" not in row[5]
    assert events("skill_loaded") == []


def test_a_reference_the_skill_does_not_have_is_not_found(packs):
    ScriptedModel.script = [resource("complaint-tone", "references/eu-rules.md"), text("Done.")]
    result = chat(packs)
    ((_, response),) = answers(ScriptedModel.requests[1])
    assert response["error_code"] == "RESOURCE_NOT_FOUND" and result["skills"] == []
    assert EU not in json.dumps(response)


def test_reloading_an_active_skill_says_already_loaded(packs):
    ScriptedModel.script = [load("refund-policy"), load("refund-policy"), text("Done.")]
    first = chat(packs)
    (_, body), (_, again) = answers(ScriptedModel.requests[2])
    assert BODY in body["instructions"]
    assert again == {"skill_name": "refund-policy", "status": skills.ALREADY_LOADED}
    assert [e["name"] for e in first["skills"]] == ["refund-policy"]
    assert len(events("skill_loaded")) == 1
    # A later turn of the same session knows it is loaded; another session does not.
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    later = chat(packs)
    assert answers(ScriptedModel.requests[4])[-1][1]["status"] == skills.ALREADY_LOADED
    assert later["skills"] == []
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    other = chat(packs, session="s2")
    assert [e["name"] for e in other["skills"]] == ["refund-policy"]


def test_a_load_counts_inside_the_agents_model_calls(app, monkeypatch):  # noqa: F811
    entry = place(app, "refund-policy", refund_files())
    with_skills(app, [entry])
    config = json.loads((app / "agent.config.json").read_text())
    config["model"]["max_model_calls"] = 2
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    runtime = build(monkeypatch)
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    assert chat(runtime)["model_calls"] == 2
    config = ScriptedModel.requests[1].config.tool_config
    assert config.function_calling_config.mode == "NONE"


def test_already_loaded_is_only_said_for_the_same_version_of_the_skill(app, monkeypatch):  # noqa: F811
    # Review r1 (M1): the session loaded one text; the agent is redeployed with another.
    sessions = InMemorySessionService()  # the conversation store outlives a redeploy

    def deploy(body):
        files = refund_files(**{"SKILL.md": skill_md("refund-policy", REFUND_DESCRIPTION, body)})
        with_skills(app, [place(app, "refund-policy", files)])
        runtime = build(monkeypatch)
        monkeypatch.setattr(runtime.runner, "session_service", sessions)
        runtime.sessions = sessions
        return runtime

    first = deploy("OLD-TEXT-5512: refunds within 30 days.")
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    assert [e["name"] for e in chat(first)["skills"]] == ["refund-policy"]
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    assert chat(first)["skills"] == []  # the same text: already loaded

    second = deploy("NEW-TEXT-8830: refunds within 14 days.")
    assert second.skills[0].sha256 != first.skills[0].sha256
    ScriptedModel.requests = []
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    result = chat(second)
    (_, response) = answers(ScriptedModel.requests[1])[-1]
    assert "NEW-TEXT-8830" in response["instructions"]  # the new body, not "already loaded"
    assert [e["name"] for e in result["skills"]] == ["refund-policy"]
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    assert chat(second)["skills"] == []  # and now this version is the loaded one


def test_a_reference_read_without_loading_the_body_is_listed_too(packs):
    # `skills[]` names a skill whose body was loaded or a file of which was read this turn.
    ScriptedModel.script = [resource("refund-policy", "references/eu-rules.md"), text("Done.")]
    result = chat(packs)
    assert result["skills"] == [
        {"name": "refund-policy", "version": "1.2.0", "resources": ["eu-rules.md"]}
    ]
    # Reading a file is not loading the skill: a later load still returns the body.
    ScriptedModel.script = [load("refund-policy"), text("Done.")]
    chat(packs)
    assert BODY in answers(ScriptedModel.requests[3])[-1][1]["instructions"]


def test_skill_text_the_model_copies_into_an_argument_is_not_stored(packs):
    # Review r1 (L3): a steered model passes the loaded text back as an argument.
    ScriptedModel.script = [
        load("refund-policy"),
        call("load_skill_resource", skill_name=BODY, file_path="references/" + "x" * 500),
        text("Done."),
    ]
    chat(packs)
    (row,) = [event[5] for event in events("tool:load_skill_resource")]
    assert "BODY-MARKER" not in json.dumps(row) and "withheld]" in row["args"]["skill_name"]
    assert len(row["args"]["file_path"]) == 100 and row["ok"] is False
    stored = json.dumps([FakeMemory.events, FakeMemory.last_turn], default=str)
    assert not any(marker in stored for marker in MARKERS)


# -- spans -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def exporter():
    import telemetry
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    # As in production: every span leaves through the runtime's redacting exporter.
    provider.add_span_processor(SimpleSpanProcessor(telemetry.RedactingExporter(memory)))
    return memory


def test_no_span_holds_skill_content(packs, exporter):
    exporter.clear()
    ScriptedModel.script = [
        load("refund-policy"),
        resource("refund-policy", "references/eu-rules.md"),
        text("Done."),
    ]
    chat(packs)
    spans = exporter.get_finished_spans()
    everything = json.dumps(
        [
            [span.name, dict(span.attributes), [dict(e.attributes) for e in span.events]]
            for span in spans
        ],
        default=str,
    )
    assert not any(marker in everything for marker in MARKERS)  # ADK's own spans included
    # ADK recorded the tool responses and the next model requests; the text left as a label.
    assert "[skill refund-policy@1.2.0 SKILL.md withheld]" in everything
    assert "[skill refund-policy@1.2.0 references/eu-rules.md withheld]" in everything
    assert any("gcp.vertex.agent.tool_response" in span.attributes for span in spans)
    sha = packs.skills[0].sha256
    tools = {
        span.attributes["tool.name"]: span.attributes
        for span in spans
        if span.attributes.get("tool.name")
    }
    body, reference = tools["load_skill"], tools["load_skill_resource"]
    assert body["openinference.span.kind"] == "TOOL"
    assert body["agentbreeder.skill.name"] == "refund-policy"
    assert (
        body["agentbreeder.skill.version"] == "1.2.0" and body["agentbreeder.skill.sha256"] == sha
    )
    assert "agentbreeder.skill.resource" not in body
    assert reference["agentbreeder.skill.resource"] == "eu-rules.md"
    assert json.loads(body["output.value"]) == {"chars": len(BODY) + 1}
    assert json.loads(reference["output.value"]) == {"chars": len(EU) + 1}


def test_withheld_text_is_replaced_in_every_form_it_is_exported_in():
    import telemetry

    body = 'Say "sorry" once.\nThen: caf\u00e9 \\ next step, in full.'
    telemetry.withhold(body + "\n", "skill x@1.0.0 SKILL.md")
    telemetry.withhold("short", "skill x@1.0.0 references/tiny.md")  # under the minimum
    label = "[skill x@1.0.0 SKILL.md withheld]"
    nested = json.dumps({"request": json.dumps({"instructions": body})})
    cleaned = telemetry._clean(
        {
            "plain": f"before {body} after",
            "json": json.dumps({"instructions": body + "\n"}),
            "unicode": json.dumps({"instructions": body}, ensure_ascii=False),
            "nested": nested,
            "list": (body, 7),
            "number": 3,
            "short": "a short word stays",
        }
    )
    assert cleaned["plain"] == f"before {label} after"
    assert json.loads(cleaned["json"]) == {"instructions": label + "\n"}
    assert json.loads(cleaned["unicode"]) == {"instructions": label}
    assert json.loads(json.loads(cleaned["nested"])["request"]) == {"instructions": label}
    assert cleaned["list"] == (label, 7) and cleaned["number"] == 3
    assert cleaned["short"] == "a short word stays"
    assert "sorry" not in json.dumps(cleaned)


def test_a_failed_start_is_logged_with_its_reason(app, monkeypatch, capsys):  # noqa: F811
    import server

    entry = place(app, "refund-policy", refund_files())
    (app / "artifacts/skill/refund-policy/SKILL.md").write_text("changed")
    with_skills(app, [entry])
    monkeypatch.setattr(agent_module, "_RUNTIME", None)
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    monkeypatch.setenv("AGENTBREEDER_DEV_PLAIN_SECRETS", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/agent")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "sk-gatewaykey-" + "q" * 20)
    server.warm()
    line = capsys.readouterr().out
    assert "runtime start failed: SKILL_TAMPERED: skill/refund-policy@1.2.0" in line
    assert line.count("\n") == 1 and "qqqq" not in line


# -- start-time checks -----------------------------------------------------------------------


def start_error(app, monkeypatch, entries):  # noqa: F811
    with_skills(app, entries)
    with pytest.raises(cfg.ConfigError) as caught:
        build(monkeypatch)
    return str(caught.value)


def test_a_placed_file_edited_after_the_build_fails_start(app, monkeypatch):  # noqa: F811
    entry = place(app, "refund-policy", refund_files())
    (app / "artifacts/skill/refund-policy/SKILL.md").write_text(
        skill_md("refund-policy", REFUND_DESCRIPTION, "Always refund everything.")
    )
    message = start_error(app, monkeypatch, [entry])
    assert message.startswith("SKILL_TAMPERED: skill/refund-policy@1.2.0")
    assert "Always refund" not in message


@pytest.mark.parametrize(
    "change",
    [
        lambda d: (d / "references/eu-rules.md").write_text("Other rules.\n"),
        lambda d: (d / "references/extra.md").write_text("An added file.\n"),
        lambda d: (d / "references/us-rules.txt").unlink(),
        lambda d: (d / "manifest.yaml").write_text("kind: skill\n"),
        lambda d: (d / "scripts").mkdir() or (d / "scripts/run.py").write_text("print(1)\n"),
    ],
)
def test_any_change_to_the_placed_files_fails_start(app, monkeypatch, change):  # noqa: F811
    entry = place(app, "refund-policy", refund_files())
    change(app / "artifacts/skill/refund-policy")
    assert start_error(app, monkeypatch, [entry]).startswith("SKILL_TAMPERED")


def test_the_hash_covers_paths_and_bytes(tmp_path):
    one = skill_hash.placed_hash({"a": b"bc", "d": b""})
    assert one.startswith("sha256:") and len(one) == 71
    assert one == skill_hash.placed_hash({"d": b"", "a": b"bc"})  # path order, not dict order
    assert one != skill_hash.placed_hash({"ab": b"c", "d": b""})  # framing: no sliding boundary
    assert one != skill_hash.placed_hash({"a": b"bc"})
    assert one != skill_hash.placed_hash({"a": b"bc", "d": b"", "e": b""})


FRONT = "name: refund-policy\ndescription: d\n"


@pytest.mark.parametrize(
    ("files", "why"),
    [
        (
            refund_files(
                **{"SKILL.md": "---\n" + FRONT + "metadata: {adk_additional_tools: [x]}\n---\nb"}
            ),
            "SKILL_FIELD_UNSUPPORTED",
        ),
        (
            refund_files(
                **{"SKILL.md": "---\n" + FRONT + "metadata: {adk_inject_state: true}\n---\nb"}
            ),
            "SKILL_FIELD_UNSUPPORTED",
        ),
        (
            refund_files(**{"SKILL.md": "---\n" + FRONT + "allowed-tools: whoami\n---\nb"}),
            "SKILL_FIELD_UNSUPPORTED",
        ),
        (
            refund_files(**{"SKILL.md": "---\n" + FRONT + "allowed_tools: whoami\n---\nb"}),
            "SKILL_FIELD_UNSUPPORTED",
        ),
        (
            refund_files(**{"SKILL.md": "---\n" + FRONT + "description: e\n---\nb"}),
            "SKILL_INVALID",
        ),
        (refund_files(**{"SKILL.md": "---\n" + FRONT + "x: &a 1\n---\nb"}), "SKILL_INVALID"),
        (
            refund_files(**{"SKILL.md": "---\nname: other\ndescription: d\n---\nb"}),
            "not the skill",
        ),
        (refund_files(**{"SKILL.md": "---\nname: refund-policy\n---\nb"}), "description"),
        (
            refund_files(
                **{"SKILL.md": "---\nname: refund-policy\ndescription: " + "d" * 1025 + "\n---\nb"}
            ),
            "description",
        ),
        (refund_files(**{"SKILL.md": "no front matter"}), "front matter"),
        (refund_files(**{"SKILL.md": "---\nname: refund-policy\n"}), "not closed"),
        (refund_files(**{"SKILL.md": "---\n- a\n---\nb"}), "mapping"),
        (refund_files(**{"SKILL.md": b"---\n" + b"\xff" * 4}), "UTF-8"),
        (refund_files(**{"SKILL.md": "---\n" + FRONT + "---\n" + "b" * 33000}), "32 KiB"),
        (refund_files(**{"SKILL.md": None}), "required"),
        (refund_files(**{"SKILL.md": "---\n" + FRONT + "x: ! {a: b}\n---\nb"}), "tags"),
        (refund_files(**{"SKILL.md": "---\n" + FRONT + "x: !!map {}\n---\nb"}), "tags"),
        (refund_files(**{"SKILL.md": "---\n" + FRONT + "x: !!int 7\n---\nb"}), "tags"),
        (refund_files(**{"SKILL.md": "---\n" + FRONT + "<<: {a: b}\n---\nb"}), "SKILL_INVALID"),
        (refund_files(**{"SKILL.md": "---\n" + FRONT + "7: x\n---\nb"}), "mapping"),
        (
            refund_files(**{"manifest.yaml": manifest("refund-policy", "d") + "scripts: []\n"}),
            "keys that are not allowed",
        ),
        (refund_files(**{"manifest.yaml": manifest("other", "d")}), "not this skill version"),
        (
            refund_files(
                **{"manifest.yaml": manifest("refund-policy", "d").replace("1.2.0", "9.9.9")}
            ),
            "not this skill version",
        ),
        (
            refund_files(**{"manifest.yaml": "kind: [unclosed\n"}),
            "manifest.yaml is not valid YAML",
        ),
        (refund_files(**{"manifest.yaml": "- a\n"}), "manifest.yaml must be a mapping"),
        (refund_files(**{"scripts/prorate.py": "print(1)\n"}), "only manifest.yaml"),
        (refund_files(**{"assets/logo.txt": "x"}), "only manifest.yaml"),
        (refund_files(**{"references/deep/x.md": "x"}), "only manifest.yaml"),
        (refund_files(**{"references/Bad Name.md": "x"}), "only manifest.yaml"),
        (refund_files(**{"references/x.py": "x"}), "only manifest.yaml"),
        (refund_files(**{"notes.md": "x"}), "only manifest.yaml"),
        (refund_files(**{"references/big.md": "x" * (64 * 1024 + 1)}), "64 KiB"),
        (refund_files(**{"references/bad.md": b"\xff\xfe"}), "UTF-8"),
        (refund_files(**{f"references/r{i}.md": "x" for i in range(20)}), "more than 20"),
    ],
)
def test_a_placed_skill_that_breaks_a_publish_rule_fails_start(
    app,  # noqa: F811
    monkeypatch,
    files,
    why,
):
    # Placed by hand with a matching hash: the runtime still refuses what publish refuses.
    message = start_error(app, monkeypatch, [place(app, "refund-policy", files)])
    assert message.startswith("SKILL_INVALID: skill/refund-policy@1.2.0") and why in message
    assert "adk_additional_tools" not in message and "whoami" not in message


def with_description(description):
    return refund_files(
        **{
            "manifest.yaml": manifest("refund-policy", description),
            "SKILL.md": skill_md("refund-policy", description, BODY),
        }
    )


@pytest.mark.parametrize(
    "description",
    ["two\nlines", "bidi \u202e override", "zero\u200bwidth", "bell\x07", "tag \U000e0101"],
)
def test_a_description_must_be_one_line_of_plain_text(app, monkeypatch, description):  # noqa: F811
    # Review r1 (L1): the description is in every turn's system instruction.
    entry = place(app, "refund-policy", with_description(description))
    message = start_error(app, monkeypatch, [entry])
    assert message.startswith("SKILL_INVALID") and "one line of plain text" in message


def test_a_description_the_host_accepts_starts(app, monkeypatch):  # noqa: F811
    # Round 2: `!!str` is accepted by the host's loader, and whether a character is assigned
    # depends on the Python that asks (U+0378 is unassigned today): neither may make an agent
    # that built fail at start.
    files = with_description("Refunds \u0378 and caf\u00e9.")
    files["SKILL.md"] = files["SKILL.md"].replace("description: ", "description: !!str ")
    with_skills(app, [place(app, "refund-policy", files)])
    assert build(monkeypatch).skills[0].skill.description == "Refunds \u0378 and caf\u00e9."


def test_a_failed_skill_tool_keeps_no_raw_arguments(packs):
    plugin = packs.runner.plugin_manager.plugins[0]
    tool = next(t for t in asyncio.run(packs.skill_toolset.get_tools()) if t.name == "load_skill")
    entry = {
        "id": "t1", "memory": FakeMemory(""), "session": "s1", "customer": "cust-001",
        "started": {}, "recorded": {}, "added": {}, "failed": set(), "trace": {},
    }  # fmt: skip
    packs.turns["t1"] = entry
    from types import SimpleNamespace

    context = SimpleNamespace(
        invocation_id="t1", function_call_id="c1", agent_name=packs.agent.name, state={}
    )
    FakeMemory.events = []
    args = {"skill_name": BODY, "other": "x" * 500}
    asyncio.run(
        plugin.on_tool_error_callback(
            tool=tool, tool_args=args, tool_context=context, error=ValueError("boom")
        )
    )
    (row,) = [event[5] for event in events("tool:load_skill")]
    assert set(row["args"]) == {"skill_name"} and "BODY-MARKER" not in json.dumps(row)
    del packs.turns["t1"]


def test_the_description_must_be_the_manifests(app, monkeypatch):  # noqa: F811
    files = refund_files(**{"SKILL.md": skill_md("refund-policy", "Another description.", BODY)})
    message = start_error(app, monkeypatch, [place(app, "refund-policy", files)])
    assert "description is not the manifest's" in message


def test_a_yaml_error_never_quotes_the_file(app, monkeypatch):  # noqa: F811
    # Review r1 (M2): the parser's message holds the offending line; the reason goes to a
    # 503 body and the log.
    broken = "---\nname: refund-policy\ndescription: SECRETTEXT-9911: : [\n---\nb"
    entry = place(app, "refund-policy", refund_files(**{"SKILL.md": broken}))
    assert start_error(app, monkeypatch, [entry]) == (
        "SKILL_INVALID: skill/refund-policy@1.2.0: SKILL.md's front matter is not valid YAML"
    )


def test_a_span_status_holds_no_skill_text():
    import telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Status, StatusCode

    telemetry.withhold("STATUS-MARKER-3141: a whole skill body.", "skill s@1.0.0 SKILL.md")
    inner = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(telemetry.RedactingExporter(inner)))
    with provider.get_tracer("adk-like").start_as_current_span("execute_tool") as current:
        current.set_status(
            Status(StatusCode.ERROR, "failed on STATUS-MARKER-3141: a whole skill body.")
        )
    (span,) = inner.get_finished_spans()
    assert span.status.description == "failed on [skill s@1.0.0 SKILL.md withheld]"


def test_a_link_in_a_placed_skill_fails_start(app, monkeypatch):  # noqa: F811
    entry = place(app, "refund-policy", refund_files())
    secret = app / "secret.txt"
    secret.write_text("the gateway key")
    (app / "artifacts/skill/refund-policy/references/key.md").symlink_to(secret)
    message = start_error(app, monkeypatch, [entry])
    assert message.startswith("SKILL_INVALID") and "gateway key" not in message


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda e: e.update(name="Refund_Policy"), "kebab-case"),
        (lambda e: e.update(name=7), "kebab-case"),
        (lambda e: e.update(version="latest"), "invalid version"),
        (lambda e: e.update(sha256="sha256:abc"), "no sha256"),
        (lambda e: e.pop("sha256"), "no sha256"),
        (lambda e: e.update(dir="../outside"), "escapes the app dir"),
        (lambda e: e.update(dir="artifacts/skill/missing"), "SKILL_TAMPERED"),
        (lambda e: e.update(scripts=[{"name": "prorate"}]), "script skills"),
    ],
)
def test_a_bad_skill_entry_fails_start(app, monkeypatch, change, why):  # noqa: F811
    entry = place(app, "refund-policy", refund_files())
    change(entry)
    assert why in start_error(app, monkeypatch, [entry])


def test_too_many_or_repeated_skills_fail_start(app, monkeypatch):  # noqa: F811
    entry = place(app, "refund-policy", refund_files())
    assert "listed twice" in start_error(app, monkeypatch, [entry, dict(entry)])
    assert "at most 10" in start_error(app, monkeypatch, [entry] * 11)


def test_a_tool_named_like_a_skill_tool_fails_start(app, monkeypatch):  # noqa: F811
    tool_yaml = app / "artifacts/tool/probe/tool.yaml"
    tool_yaml.write_text(
        tool_yaml.read_text().replace("name: whoami", "name: load_skill\n    function: whoami")
    )
    message = start_error(app, monkeypatch, [place(app, "refund-policy", refund_files())])
    assert "load_skill" in message


def test_only_the_parent_has_skills(app, monkeypatch):  # noqa: F811
    from test_subagents import Billing, Parent, Returns, subagent_config

    subagent_config(app)
    with_skills(app, [place(app, "refund-policy", refund_files())])
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    FakeMemory.events = []
    team = agent_module.Runtime(
        cfg.config(),
        "postgresql://u:p@db/agent",
        "k",
        model=Parent(),
        child_models={"billing": Billing(), "returns": Returns()},
    )
    for model in (Parent, Billing, Returns):
        model.script, model.requests = [], []
    Parent.script = [call("ask_billing", request="refund?"), load("refund-policy"), text("Done.")]
    Billing.script = [text("ask the policy")]
    result = chat(team)
    # A two-pack fixture agent with sub-agents returns the documented reply shape.
    assert [e["name"] for e in result["subagents"]] == ["billing"]
    assert [e["name"] for e in result["skills"]] == ["refund-policy"]
    child_system = Billing.requests[0].config.system_instruction
    assert "available_skills" not in child_system
    declared = [
        d.name for tool in Billing.requests[0].config.tools for d in tool.function_declarations
    ]
    assert declared == ["file_refund"]
