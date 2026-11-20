# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Build a new agent in the console (#833, M17 console half): the spec as a private temporary
file, the `new agent --from-spec --dry-run --json` preview with every file's text, create pinned
to the previewed `plan_sha256`, only verified combinations created, and the "this code runs in
agents" re-check. The tool and MCP pickers' catalog (`GET /api/builder/parts`) and picks. Run
through the real CLI."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from datetime import date
from pathlib import Path

import pytest
from test_center import api, center, cookie  # noqa: F401 (fixtures)

from agentbreeder import newagent, registry
from agentbreeder.center import builder, server
from agentbreeder.center.files import BadRequest

PREVIEW = "/api/projects/builder"
PARTS = "/api/builder/parts"
CREATE = "/api/projects/builder/create"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def no_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


@pytest.fixture
def fake_cli():
    """The real CLI (the server's own default prefix)."""
    return [sys.executable, "-I", "-m", "agentbreeder"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir(mode=0o755)
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: path))
    return path


@pytest.fixture
def calls(center, monkeypatch):
    """Every CLI child's argv, with the spec file's mode and text as the child saw it."""
    seen: list[dict] = []
    real = center.runner.read

    def read(argv, *args, **kwargs):
        entry = {"argv": list(argv), "input": kwargs.get("input")}
        spec = next((a.split("=", 1)[1] for a in argv if a.startswith("--from-spec=")), None)
        if spec:
            path = Path(spec)
            entry["file_mode"] = stat.S_IMODE(path.lstat().st_mode)
            entry["dir_mode"] = stat.S_IMODE(path.parent.lstat().st_mode)
            entry["spec"] = json.loads(path.read_text())
            entry["path"] = path
        seen.append(entry)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(center.runner, "read", read)
    return seen


def spec(**changes) -> dict:
    return {
        "schema_version": 1,
        "name": "helper",
        "model": "gemini",
        "model_id": None,
        "max_model_calls": 2,
        "prompt": {"text": "You are helper. A private instruction.\n"},
        "evals": {
            "cases": [
                {
                    "id": "hours",
                    "turns": ["When is support open?"],
                    "expect": {"must_match": ["9"]},
                    "reference": "9am to 5pm Pacific.",
                }
            ],
            "threshold": 1.0,
            "retries": 1,
        },
        "tools": [],
        "knowledge": [],
        "mcp": [],
        **changes,
    }


def preview(center, cookie, **changes):
    return api(center, "POST", PREVIEW, {"spec": spec(**changes)}, cookie)


def create(center, cookie, digest, confirmed=False, **changes):
    body = {"spec": spec(**changes), "digest": digest, "code_confirmed": confirmed}
    return api(center, "POST", CREATE, body, cookie)


def raced_midnight(result) -> bool:
    status, data, _ = result
    return status == 400 and (data.get("error") or {}).get("code") == "DIGEST_MISMATCH"


def preview_then_create(center, cookie, *confirms, calls=None, between=None, **changes):
    """Preview, then one create per `confirms` value (the code confirm), each pinned to the
    preview's digest. Returns (the preview's data, [each create's (status, data, headers)]).

    The files carry the local date (the agent.yaml header and stack.yaml `review_by`), so a
    preview before local midnight and a create after it differ by design: DIGEST_MISMATCH,
    "preview it again" (#1050). The CLI is a child process, so the date cannot be patched.
    Only then is the whole sequence run once more (`calls` cleared, `between` run again);
    a DIGEST_MISMATCH on the same day, or any other refusal, is returned as it is."""
    for attempt in (1, 2):
        day = date.today()
        status, data, _ = preview(center, cookie, **changes)
        assert status == 200, data
        if between:
            between()
        digest = data["data"]["plan_sha256"]
        results = [create(center, cookie, digest, confirmed, **changes) for confirmed in confirms]
        if attempt == 2 or date.today() == day or not any(map(raced_midnight, results)):
            return data, results
        if calls is not None:
            calls.clear()


# -- the preview -------------------------------------------------------------------------------


def test_preview_shows_every_file_the_cli_would_write(center, cookie, home, calls):
    status, data, _ = preview(center, cookie)
    assert status == 200, data
    shown = data["data"]
    destination = home / "agentbreeder-projects" / "helper"
    assert shown["directory"] == str(destination)
    paths = [entry["path"] for entry in shown["files"]]
    assert paths[0] == "agent.yaml" and "stack.yaml" in paths
    assert "src/evals/golden.jsonl" in paths
    prompt = next(e for e in shown["files"] if e["path"] == "src/prompt/prompt.md")
    assert prompt["content"] == "You are helper. A private instruction.\n"
    assert shown["combination"] == {"parts": ["model", "prompt", "evals"], "verified": True}
    assert shown["keys"] == ["GEMINI_API_KEY"] and shown["cases"] == ["hours"]
    assert shown["runs_code"] is False and shown["refs"] == [] and shown["grants"] == []
    assert shown["plan_sha256"].startswith("sha256:")
    assert not destination.exists()
    assert center.state.projects() == []


def test_the_spec_travels_as_a_private_file_never_in_argv(center, cookie, home, calls):
    status, data, _ = preview(center, cookie)
    assert status == 200, data
    [call] = calls
    path = call["path"]
    assert call["argv"] == [
        "new",
        "agent",
        "helper",
        f"--from-spec={path}",
        f"--dir={home / 'agentbreeder-projects' / 'helper'}",
        "--dry-run",
        "--json",
    ]
    assert call["file_mode"] == 0o600 and call["dir_mode"] == 0o700
    assert call["spec"] == spec() and call["input"] is None
    assert not any("private instruction" in arg for arg in call["argv"])
    assert not path.parent.exists()  # removed after the child


def test_a_combination_not_run_end_to_end_is_previewed_as_unverified(center, cookie, home):
    status, data, _ = preview(center, cookie, model="anthropic")
    assert status == 200, data
    assert data["data"]["combination"]["verified"] is False
    assert data["data"]["keys"] == ["ANTHROPIC_API_KEY"]


def test_the_clis_own_refusal_is_shown(center, cookie, home, calls):
    bad = spec()["evals"] | {"cases": [{"id": "x y", "turns": ["hi"], "reference": "r"}]}
    status, data, _ = preview(center, cookie, evals=bad)
    assert status == 400 and data["error"]["code"] == "SPEC_INVALID"
    assert not calls[0]["path"].parent.exists()


@pytest.mark.parametrize(
    "body,code",
    [
        ({}, "SPEC_INVALID"),
        ({"spec": ["helper"]}, "SPEC_INVALID"),
        ({"spec": spec(extra=1)}, "SPEC_INVALID"),
        ({"spec": spec(schema_version=3)}, "SPEC_INVALID"),
        ({"spec": spec(schema_version=True)}, "SPEC_INVALID"),
        ({"spec": spec(name="Helper")}, "NAME_INVALID"),
        ({"spec": spec(name="../x")}, "NAME_INVALID"),
        ({"spec": spec(name=7)}, "NAME_INVALID"),
        ({"spec": spec(model="mistral")}, "MODEL_INVALID"),
        ({"spec": spec(model_id="gemini/a b")}, "MODEL_ID_INVALID"),
        ({"spec": spec(model_id=3)}, "MODEL_ID_INVALID"),
        ({"spec": spec(knowledge=["knowledge/docs@0.1.0"])}, "SPEC_INVALID"),
        ({"spec": spec(knowledge={"ref": "knowledge/docs@0.1.0"})}, "SPEC_INVALID"),
        ({"spec": spec(knowledge=[{"ref": "tool/docs@0.1.0"}])}, "SPEC_INVALID"),
        ({"spec": spec(knowledge=[{"ref": "knowledge/docs"}])}, "SPEC_INVALID"),
        ({"spec": spec(knowledge=[{"ref": "knowledge/docs@0.1.0", "x": 1}])}, "SPEC_INVALID"),
        ({"spec": spec(knowledge=[{"ref": "knowledge/docs@0.1.0", "top_k": 0}])}, "SPEC_INVALID"),
        (
            {"spec": spec(knowledge=[{"ref": "knowledge/docs@0.1.0", "top_k": True}])},
            "SPEC_INVALID",
        ),
        (
            {"spec": spec(knowledge=[{"ref": "knowledge/docs@0.1.0", "extra_searches": 6}])},
            "SPEC_INVALID",
        ),
        (
            {"spec": spec(knowledge=[{"ref": "knowledge/a@0.1.0"}, {"ref": "knowledge/b@0.1.0"}])},
            "SPEC_INVALID",
        ),
        (
            {"spec": spec(model="anthropic", knowledge=[{"ref": "knowledge/docs@0.1.0"}])},
            "SPEC_UNSUPPORTED",
        ),
        ({"spec": spec(tools="tool/clock@0.1.0")}, "SPEC_INVALID"),
        ({"spec": spec(tools=["tool/clock"])}, "SPEC_INVALID"),
        ({"spec": spec(tools=["mcp/github@1.0.0"])}, "SPEC_INVALID"),
        ({"spec": spec(tools=[7])}, "SPEC_INVALID"),
        ({"spec": spec(tools=["tool/clock@0.1.0", "tool/clock@0.2.0"])}, "SPEC_INVALID"),
        ({"spec": spec(tools=[f"tool/t{n}@1.0.0" for n in range(51)])}, "SPEC_INVALID"),
        ({"spec": spec(mcp={"ref": "mcp/github@1.0.0"})}, "SPEC_INVALID"),
        ({"spec": spec(mcp=[{"ref": "mcp/github@1.0.0"}])}, "SPEC_INVALID"),
        ({"spec": spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": []}])}, "SPEC_INVALID"),
        ({"spec": spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["a b"]}])}, "SPEC_INVALID"),
        ({"spec": spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["a", "a"]}])}, "SPEC_INVALID"),
        ({"spec": spec(mcp=[{"ref": "tool/github@1.0.0", "tools": ["a"]}])}, "SPEC_INVALID"),
        (
            {"spec": spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["a"], "x": 1}])},
            "SPEC_INVALID",
        ),
        (
            {"spec": spec(mcp=[{"ref": f"mcp/github@1.0.{n}", "tools": ["a"]} for n in range(2)])},
            "SPEC_INVALID",
        ),
        (
            {"spec": spec(mcp=[{"ref": f"mcp/s{n}@1.0.0", "tools": ["a"]} for n in range(11)])},
            "SPEC_INVALID",
        ),
        ({"spec": spec(prompt={"text": "x" * (256 * 1024)})}, "SPEC_TOO_LARGE"),
    ],
)
def test_a_bad_request_runs_nothing(center, cookie, home, calls, body, code):
    status, data, _ = api(center, "POST", PREVIEW, body, cookie)
    assert status == 400 and data["error"]["code"] == code, data
    assert calls == []


def test_a_spec_over_64_kib_reaches_the_cli(center, cookie, home, calls):
    status, data, _ = preview(center, cookie, prompt={"text": "word " * 6000})
    assert status == 200, data
    assert len(calls[0]["spec"]["prompt"]["text"]) == 30000


# -- spec v2 (#1058 SP, console spec §4.5) ------------------------------------------------------


def v2(**changes) -> dict:
    return spec(schema_version=2, **changes)


def sub(name="billing", **changes) -> dict:
    return {
        "name": name,
        "description": "Refund questions.",
        "model": "gemini",
        "prompt": {"text": "You are billing.\n"},
    } | changes


TAGGED = {
    "cases": [
        {"id": "a", "turns": ["Hi"], "reference": "Hello.", "tags": ["smoke"]},
    ]
}


@pytest.mark.parametrize(
    "body",
    [
        spec(runtime="runtime/adk@1.4.0"),
        spec(subagents=[sub()]),
        spec(skills=[]),
        spec(limits={"turn_model_calls": 4}),
        spec(evals=TAGGED),
        spec(
            evals={
                "cases": [
                    {"id": "a", "turns": ["Hi"], "reference": "x", "expect": {"skills_loaded": []}}
                ]
            }
        ),
    ],
)
def test_a_v1_body_with_a_v2_field_runs_nothing(center, cookie, home, calls, body):
    status, data, _ = api(center, "POST", PREVIEW, {"spec": body}, cookie)
    assert status == 400 and data["error"]["code"] == "SPEC_INVALID", data
    assert "schema_version 2" in data["error"]["message"]
    assert calls == []


@pytest.mark.parametrize(
    "body",
    [
        v2(runtime="tool/x@1.0.0"),
        v2(runtime=7),
        v2(skills="skill/a@1.0.0"),
        v2(skills=["skill/a@1.0.0", "skill/a@1.0.1"]),
        v2(skills=[f"skill/s{n}@1.0.0" for n in range(11)]),
        v2(skills=["tool/a@1.0.0"]),
        v2(subagents=sub()),
        v2(subagents=[sub(f"s{n}") for n in range(6)]),
        v2(subagents=[sub() | {"x": 1}]),
        v2(subagents=["billing"]),
        v2(subagents=[sub("Billing")]),
        v2(subagents=[sub("b" * 33)]),
        v2(subagents=[sub(), sub()]),
        v2(subagents=[sub(tools=["tool/a@1.0.0", "tool/a@1.0.1"])]),
        v2(subagents=[sub(tools=[f"tool/t{n}@1.0.0" for n in range(21)])]),
        v2(subagents=[sub(mcp=[{"ref": "mcp/github@1.0.0"}])]),
        v2(subagents=[sub()], limits={"turn_model_calls": 41}),
        v2(subagents=[sub()], limits={"turn_model_calls": True}),
        v2(subagents=[sub()], limits={"turn_model_calls": 4, "x": 1}),
        v2(subagents=[sub()], limits=4),
        v2(limits={"turn_model_calls": 4}),
        v2(extra=1),
    ],
)
def test_a_bad_v2_body_runs_nothing(center, cookie, home, calls, body):
    status, data, _ = api(center, "POST", PREVIEW, {"spec": body}, cookie)
    assert status == 400 and data["error"]["code"] == "SPEC_INVALID", data
    assert calls == []


@pytest.mark.parametrize(
    "body, code",
    [
        (v2(subagents=[sub(model="mistral")]), "MODEL_INVALID"),
        (v2(subagents=[sub(model_id="a b")]), "MODEL_ID_INVALID"),
    ],
)
def test_a_subagent_model_is_checked_like_the_agents(center, cookie, home, calls, body, code):
    status, data, _ = api(center, "POST", PREVIEW, {"spec": body}, cookie)
    assert status == 400 and data["error"]["code"] == code, data
    assert calls == []


def test_a_lone_surrogate_is_a_bad_request():
    with pytest.raises(BadRequest) as caught:
        builder.request({"spec": v2(prompt={"text": "\ud800"})})
    assert caught.value.code == "SPEC_INVALID"


def test_the_v2_cap_is_512_kib_and_v1_keeps_256(center, cookie, home, calls):
    case = {"id": "a", "turns": ["Hi"], "reference": "r" * 4000}
    many = {"cases": [dict(case, id=f"c{n}") for n in range(70)]}  # about 280 KiB
    assert builder.MAX_SPEC_BYTES_V1 < len(builder.encode(v2(evals=many)))
    status, data, _ = api(center, "POST", PREVIEW, {"spec": spec(evals=many)}, cookie)
    assert status == 400 and data["error"]["code"] == "SPEC_TOO_LARGE" and calls == []
    status, data, _ = api(center, "POST", PREVIEW, {"spec": v2(evals=many)}, cookie)
    assert len(calls) == 1 and calls[0]["spec"]["schema_version"] == 2
    huge = {"cases": [dict(case, id=f"c{n}") for n in range(140)]}
    status, data, _ = api(center, "POST", PREVIEW, {"spec": v2(evals=huge)}, cookie)
    assert status == 400 and data["error"]["code"] == "SPEC_TOO_LARGE" and len(calls) == 1


def test_a_v2_preview_echoes_its_version(center, cookie, home, calls):
    """The server passes the version it sent to `summary`: a v2 answer to a v2 spec."""
    status, data, _ = api(center, "POST", PREVIEW, {"spec": v2(evals=TAGGED)}, cookie)
    assert status == 200, data
    assert calls[0]["spec"]["schema_version"] == 2
    golden = data["data"]["files"]
    assert any('"tags": ["smoke"]' in f["content"] for f in golden)


def test_an_answer_of_another_version_is_refused(center, cookie, home, calls, monkeypatch):
    real = builder.summary

    def answered_one(data, *args):
        return real(dict(data, spec_version=1), *args)

    monkeypatch.setattr(builder, "summary", answered_one)
    status, data, _ = api(center, "POST", PREVIEW, {"spec": v2(evals=TAGGED)}, cookie)
    assert status == 400 and data["error"]["code"] == "BAD_OUTPUT", data


def test_a_v2_preview_shows_the_clis_capability_refusal(center, cookie, home, calls):
    status, data, _ = api(center, "POST", PREVIEW, {"spec": v2(subagents=[sub()])}, cookie)
    assert status == 400 and data["error"]["code"] == "RUNTIME_NO_SUBAGENTS", data
    assert len(calls) == 1


def test_a_v1_body_sends_and_gets_version_1(center, cookie, home, calls):
    status, data, _ = preview(center, cookie)
    assert status == 200, data
    assert calls[0]["spec"]["schema_version"] == 1


# -- create ------------------------------------------------------------------------------------


def test_create_writes_the_previewed_plan_and_registers_it(center, cookie, home, calls):
    data, [(status, added, _)] = preview_then_create(center, cookie, False, calls=calls)
    digest = data["data"]["plan_sha256"]
    assert status == 200, added
    destination = home / "agentbreeder-projects" / "helper"
    assert added["data"]["path"] == str(destination.resolve())
    assert (destination / "agent.yaml").exists() and (destination / "agent.lock").exists()
    assert (destination / "src/prompt/prompt.md").read_text() == spec()["prompt"]["text"]
    dry, write = calls[1]["argv"], calls[2]["argv"]
    assert f"--expect-digest={digest}" in dry and "--dry-run" in dry
    assert write[-2:] == [f"--expect-digest={digest}", "--json"] and "--dry-run" not in write
    assert all(not call["path"].parent.exists() for call in calls)


def test_create_refuses_a_plan_that_changed_since_the_preview(center, cookie, home, calls):
    _, data, _ = preview(center, cookie)
    status, refused, _ = create(
        center, cookie, data["data"]["plan_sha256"], prompt={"text": "Something else.\n"}
    )
    assert status == 400 and refused["error"]["code"] == "DIGEST_MISMATCH"
    # The UI test's midnight retry (#1050) recognises the refusal by these words.
    assert "preview it again" in refused["error"]["message"]
    assert not (home / "agentbreeder-projects" / "helper").exists()
    assert center.state.projects() == []


@pytest.mark.parametrize("digest", [None, "", "sha256:xyz", 7])
def test_create_needs_a_preview_first(center, cookie, home, calls, digest):
    status, data, _ = create(center, cookie, digest)
    assert status == 400 and data["error"]["code"] == "PREVIEW_REQUIRED"
    assert calls == []


def test_create_refuses_a_combination_not_run_end_to_end(center, cookie, home, calls):
    _, [(status, refused, _)] = preview_then_create(
        center, cookie, False, calls=calls, model="anthropic"
    )
    assert status == 400 and refused["error"]["code"] == "COMBINATION_UNVERIFIED"
    assert len(calls) == 2  # the preview and the re-check; no write
    assert not (home / "agentbreeder-projects" / "helper").exists()


def test_create_needs_the_code_confirm_when_code_runs_in_agents(center, cookie, home, monkeypatch):
    shown = {"combination": {"verified": True}, "runs_code": True}
    destination = home / "agentbreeder-projects" / "helper"
    monkeypatch.setattr(
        center, "_from_spec", lambda body, digest=None: ("helper", destination, shown)
    )
    status, data, _ = create(center, cookie, DIGEST)
    assert status == 400 and data["error"]["code"] == "CODE_CONFIRM_REQUIRED"
    assert not destination.exists()


def test_create_into_a_folder_that_is_not_empty_shows_the_clis_error(center, cookie, home):
    def fill_the_folder():
        (home / "agentbreeder-projects" / "helper").mkdir(exist_ok=True)
        (home / "agentbreeder-projects" / "helper" / "notes.txt").write_text("mine")

    _, [(status, refused, _)] = preview_then_create(center, cookie, False, between=fill_the_folder)
    assert status == 400 and refused["error"]["code"] not in ("BAD_OUTPUT", "")
    assert center.state.projects() == []


# -- the midnight retry (#1050, test-only) ------------------------------------------------------

MISMATCH = (400, {"data": None, "error": {"code": "DIGEST_MISMATCH", "message": "m"}}, {})
WRITTEN = (200, {"data": {"path": "/x"}, "error": None}, {})


@pytest.fixture
def scripted(monkeypatch):
    """`preview_then_create` with scripted local dates and create answers, and no console."""
    state = {"days": [], "answers": [], "previews": 0, "creates": 0}

    class Clock:
        @staticmethod
        def today():
            return state["days"].pop(0)

    def fake_preview(center, cookie, **changes):
        state["previews"] += 1
        return 200, {"data": {"plan_sha256": f"sha256:{state['previews']}"}}, {}

    def fake_create(center, cookie, digest, confirmed=False, **changes):
        state["creates"] += 1
        return state["answers"].pop(0)

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "date", Clock)
    monkeypatch.setattr(module, "preview", fake_preview)
    monkeypatch.setattr(module, "create", fake_create)
    return state


D1, D2, D3 = date(2026, 11, 15), date(2026, 11, 16), date(2026, 11, 17)


def test_a_mismatch_across_local_midnight_is_previewed_and_created_once_more(scripted):
    scripted.update(days=[D1, D2, D2, D2], answers=[MISMATCH, WRITTEN])
    seen, between = ["stale call"], []
    data, results = preview_then_create(
        None, None, False, calls=seen, between=lambda: between.append(1)
    )
    assert results == [WRITTEN] and data["data"]["plan_sha256"] == "sha256:2"
    assert scripted["previews"] == 2 and between == [1, 1] and seen == []


def test_a_mismatch_on_the_same_day_is_not_retried(scripted):
    scripted.update(days=[D1, D1], answers=[MISMATCH])
    _, results = preview_then_create(None, None, False)
    assert results == [MISMATCH] and scripted["previews"] == 1


def test_the_midnight_retry_happens_at_most_once(scripted):
    scripted.update(days=[D1, D2, D2, D3], answers=[MISMATCH, MISMATCH])
    _, results = preview_then_create(None, None, False)
    assert results == [MISMATCH] and scripted["previews"] == 2


def test_a_new_day_without_a_mismatch_is_not_retried(scripted):
    scripted.update(days=[D1, D2], answers=[WRITTEN, WRITTEN])
    _, results = preview_then_create(None, None, False, True)
    assert results == [WRITTEN, WRITTEN] and scripted["previews"] == 1


# -- the pure checks ---------------------------------------------------------------------------


def test_the_spec_file_is_private_and_removed_even_on_error():
    with pytest.raises(RuntimeError), builder.spec_file(spec()) as path:
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.lstat(path.parent).st_mode) == 0o700
        assert json.loads(path.read_text()) == spec()
        raise RuntimeError
    assert not path.parent.exists()


def answer(**changes) -> dict:
    data = {
        "schema_version": 1,
        "dry_run": True,
        "name": "helper",
        "directory": "/x/helper",
        "model": "gemini",
        "model_id": "gemini/gemini-3.8-flash",
        "review_by": "2027-05-13",
        "aliases": ["chat", "judge"],
        "cases": ["hours"],
        "files": [{"path": "agent.yaml", "mode": "0644", "sha256": DIGEST, "content": "a\n"}],
        "spec_version": 1,
        "runtime": "runtime/adk@1.4.0",
        "combination": {"parts": ["model", "prompt", "evals"], "verified": True},
        "refs": [],
        "runs_code": False,
        "grants": [],
        "keys": ["GEMINI_API_KEY"],
        "plan_sha256": DIGEST,
    }
    return data | changes


ASKED = ("helper", True, "gemini", "/x/helper")
CODE_REF = {
    "ref": "tool/x@0.1.0",
    "kind": "tool",
    "root_kind": "path",
    "runs_code": True,
    "sha256": DIGEST,
}


def test_summary_takes_the_largest_v2_answer():
    """A runtime, a knowledge version, 50 tools, 10 servers, five sub-agents' 20 tools each
    and 10 skills is 172 refs; seven files plus two per sub-agent is 17 (#1058 SP)."""
    files = [{"path": f"f{n}", "sha256": DIGEST, "content": "x"} for n in range(17)]
    data = answer(refs=[CODE_REF] * 172, runs_code=True, files=files, spec_version=2)
    shown = builder.summary(data, *ASKED, 2)
    assert len(shown["refs"]) == 172 and len(shown["files"]) == 17


def test_summary_keeps_what_the_preview_shows():
    shown = builder.summary(answer(refs=[CODE_REF], runs_code=True), *ASKED)
    assert shown["refs"] == [CODE_REF] and shown["runs_code"] is True
    assert shown["files"] == [{"path": "agent.yaml", "sha256": DIGEST, "content": "a\n"}]


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"dry_run": False},
        {"name": "other"},
        {"model": "openai"},
        {"spec_version": 2},
        {"refs": [CODE_REF] * (builder.MAX_REFS + 1), "runs_code": True},
        {"plan_sha256": "sha256:x"},
        {"files": []},
        {"files": [{"path": "a", "sha256": DIGEST}]},
        {"files": [{"path": "a", "sha256": DIGEST, "content": "x" * (2 * 1024 * 1024 + 1)}]},
        {"combination": {"parts": [], "verified": "yes"}},
        {"refs": [CODE_REF]},  # runs_code hidden
        {"refs": [CODE_REF | {"root_kind": "web"}], "runs_code": True},
        {"grants": [{"server": "github", "tools": "get_me"}]},
        {"keys": "GEMINI_API_KEY"},
        {"directory": None},
        {"directory": "/elsewhere/helper"},
        {"runtime": None},
        {"runtime": "runtime/adk"},  # not pinned
        {"runtime": "tool/adk@1.4.0"},
    ],
)
def test_summary_refuses_an_unexpected_answer(changes):
    with pytest.raises(builder.BadOutput):
        builder.summary(answer(**changes), *ASKED)


def test_summary_shows_the_runtime_and_refuses_another_than_the_one_asked_for():
    """#1085 S1: the preview names the runtime the CLI pinned; a spec that named one gets that
    one, and a spec that named none gets the CLI's default."""
    assert builder.summary(answer(), *ASKED)["runtime"] == "runtime/adk@1.4.0"
    asked = builder.summary(answer(spec_version=2), *ASKED, 2, "runtime/adk@1.4.0")
    assert asked["runtime"] == "runtime/adk@1.4.0"
    with pytest.raises(builder.BadOutput):
        builder.summary(answer(spec_version=2), *ASKED, 2, "runtime/adk@1.3.0")


# -- the tool and MCP pickers ------------------------------------------------------------------


GITHUB_TOOLS = ["get_me", "list_issues", "issue_read", "search_repositories", "get_file_contents"]


def _mcp(root: Path, name: str, version: str, mcp_yaml: str | None) -> Path:
    path = root / "mcp" / name / version
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(
        f"kind: mcp\nname: {name}\nversion: {version}\ndescription: The {name} server.\n"
    )
    if mcp_yaml is not None:
        (path / "mcp.yaml").write_text(mcp_yaml)
    return path


@pytest.fixture
def path_registry(tmp_path, monkeypatch):
    """An AGENTBREEDER_REGISTRY_PATH root with `tool/clock@0.1.0` and `0.2.0` (scaffolded and
    published, so the CLI resolves them), an MCP server named like a stack service, one whose
    mcp.yaml is missing, and one with a broken manifest."""
    root = tmp_path / "path-registry"
    for version in ("0.1.0", "0.2.0"):
        source = registry.scaffold("tool", "clock", tmp_path / f"src-{version}", version)
        registry.publish(source, root)
    _mcp(root, "postgres", "1.0.0", "tools:\n  query: {effect: read}\n")
    _mcp(root, "blank", "1.0.0", None)
    broken = _mcp(root, "broken", "1.0.0", "tools:\n  a: {effect: read}\n")
    (broken / "manifest.yaml").write_text("kind: mcp\nname: other\nversion: 1.0.0\n")
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    return root


def test_parts_lists_what_a_new_project_resolves(center, cookie, path_registry):
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    assert status == 200 and data["error"] is None, data
    parts = data["data"]
    assert [(e["ref"], e["root_kind"], e["runs_code"]) for e in parts["tools"]] == [
        ("tool/clock@0.2.0", "path", True),
        ("tool/clock@0.1.0", "path", True),
    ]
    servers = {entry["ref"]: entry for entry in parts["mcp"]}
    assert set(servers) == {"mcp/blank@1.0.0", "mcp/github@1.0.0", "mcp/playwright@1.0.0"} | {
        "mcp/postgres@1.0.0"
    }
    github = servers["mcp/github@1.0.0"]
    assert github["root_kind"] == "builtin" and github["unusable"] is None
    assert [tool["name"] for tool in github["tools"]] == GITHUB_TOOLS
    assert {tool["effect"] for tool in github["tools"]} == {"read"}
    assert len(github["description"]) <= builder.MAX_DESCRIPTION
    assert "cannot name a stack's MCP server" in servers["mcp/postgres@1.0.0"]["unusable"]
    assert "mcp.yaml cannot be read" in servers["mcp/blank@1.0.0"]["unusable"]
    assert parts["skipped"] == 1 and parts["truncated"] is False  # the broken manifest


def test_parts_without_a_registry_path_offers_the_built_in_servers(center, cookie):
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    assert status == 200, data
    assert data["data"]["tools"] == []
    assert [entry["name"] for entry in data["data"]["mcp"]] == ["github", "playwright"]


def test_parts_skips_a_version_under_a_symlinked_directory(tmp_path, monkeypatch):
    real = tmp_path / "real"
    source = registry.scaffold("tool", "clock", tmp_path / "src")
    registry.publish(source, real)
    linked = tmp_path / "linked"
    (linked / "tool").mkdir(parents=True)
    (linked / "tool" / "clock").symlink_to(real / "tool" / "clock")
    parts = builder.parts([linked, real])
    assert parts["tools"] == [] and parts["skipped"] == 1


def test_parts_says_so_when_the_registries_cannot_be_read(center, cookie, monkeypatch):
    def broken(search):
        raise OSError("/private/path")

    monkeypatch.setattr(builder, "parts", broken)
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    assert data["error"]["code"] == "REGISTRY_UNREADABLE"
    assert "/private" not in data["error"]["message"]


def test_a_picked_path_tool_with_an_mcp_server_is_not_run_end_to_end(
    center, cookie, home, calls, path_registry
):
    tools = ["tool/clock@0.2.0"]
    mcp = [{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}]
    data, [(status, refused, _)] = preview_then_create(
        center, cookie, True, calls=calls, tools=tools, mcp=mcp
    )
    shown = data["data"]
    assert shown["combination"] == {
        "parts": ["model", "prompt", "evals", "mcp", "tools"],
        "verified": False,
    }
    tool = next(ref for ref in shown["refs"] if ref["kind"] == "tool")
    assert tool["ref"] == "tool/clock@0.2.0" and tool["root_kind"] == "path"
    assert tool["runs_code"] is True and shown["runs_code"] is True
    agent = next(e for e in shown["files"] if e["path"] == "agent.yaml")["content"]
    assert '- {ref: "tool/clock@0.2.0"}' in agent
    assert calls[0]["spec"]["tools"] == tools
    # Not run end to end, so Create refuses it even when the code is confirmed.
    assert status == 400 and refused["error"]["code"] == "COMBINATION_UNVERIFIED"
    assert not (home / "agentbreeder-projects" / "helper").exists()


def test_a_picked_mcp_server_grants_only_the_picked_tools(center, cookie, home, calls):
    picked = [{"ref": "mcp/github@1.0.0", "tools": ["get_me", "issue_read"]}]
    status, data, _ = preview(center, cookie, mcp=picked)
    assert status == 200, data
    shown = data["data"]
    assert shown["grants"] == [{"server": "github", "tools": ["get_me", "issue_read"]}]
    assert shown["keys"] == ["GEMINI_API_KEY", "GITHUB_MCP_TOKEN"]
    assert shown["combination"]["parts"][-1] == "mcp" and not shown["combination"]["verified"]
    assert shown["runs_code"] is False  # a built-in server is shipped code
    stack = next(e for e in shown["files"] if e["path"] == "stack.yaml")["content"]
    assert "mcp/github@1.0.0" in stack and "list_issues" not in stack


def test_a_tool_the_server_does_not_declare_is_the_clis_refusal(center, cookie, home, calls):
    picked = [{"ref": "mcp/github@1.0.0", "tools": ["delete_repo"]}]
    status, data, _ = preview(center, cookie, mcp=picked)
    assert status == 400 and data["error"]["code"] == "MCP_TOOL_UNDECLARED", data
    assert len(calls) == 1


def test_a_picked_path_tool_is_created_only_after_the_code_confirm(
    center, cookie, home, calls, path_registry
):
    """gemini with one path tool was run end to end (#833, `agentspec.VERIFIED`), so Create
    works for it with no console change, once the code is confirmed."""
    tools = ["tool/clock@0.2.0"]
    destination = home / "agentbreeder-projects" / "helper"
    data, [(status, refused, _), (written, added, _)] = preview_then_create(
        center, cookie, False, True, calls=calls, tools=tools
    )
    assert data["data"]["runs_code"] is True
    assert data["data"]["combination"]["verified"] is True  # the real CLI's answer
    assert status == 400 and refused["error"]["code"] == "CODE_CONFIRM_REQUIRED"
    # The refused create re-checked the plan and wrote nothing; only the confirmed one wrote.
    assert ["--dry-run" in call["argv"] for call in calls] == [True, True, True, False]
    assert written == 200, added
    assert '- {ref: "tool/clock@0.2.0"}' in (destination / "agent.yaml").read_text()
    assert "tool/clock@0.2.0" in (destination / "agent.lock").read_text()
    write = calls[-1]
    assert "--dry-run" not in write["argv"] and write["spec"]["tools"] == tools


@pytest.mark.parametrize(
    "text",
    [
        "tools: {}\n",
        "tools:\n  ok: {effect: read}\n  'bad name': {effect: read}\n",
        "tools:\n  ok: {effect: delete}\n",
        "tools: [a, b]\n",
        "not: [valid\n",
    ],
)
def test_a_server_whose_tools_cannot_be_read_cannot_be_picked(tmp_path, text):
    root = tmp_path / "root"
    _mcp(root, "notes", "1.0.0", text)
    [entry] = builder.parts([root])["mcp"]
    assert entry["tools"] == [] and "mcp.yaml cannot be read" in entry["unusable"]


def test_a_linked_mcp_yaml_is_not_read(tmp_path):
    root = tmp_path / "root"
    version = _mcp(root, "notes", "1.0.0", None)
    (tmp_path / "elsewhere.yaml").write_text("tools:\n  secret_name: {effect: read}\n")
    (version / "mcp.yaml").symlink_to(tmp_path / "elsewhere.yaml")
    [entry] = builder.parts([root])["mcp"]
    assert entry["tools"] == [] and entry["unusable"]


def test_the_newest_versions_come_first_and_the_cap_cuts_in_that_order(tmp_path, monkeypatch):
    root = tmp_path / "root"
    for version in ("1.0.0", "1.10.0", "1.9.0"):
        _mcp(root, "notes", version, "tools:\n  add: {effect: write}\n")
    _mcp(root, "alpha", "2.0.0", "tools:\n  add: {effect: write}\n")
    refs = [entry["ref"] for entry in builder.parts([root])["mcp"]]
    assert refs == ["mcp/alpha@2.0.0", "mcp/notes@1.10.0", "mcp/notes@1.9.0", "mcp/notes@1.0.0"]
    monkeypatch.setattr(builder, "MAX_PARTS", 2)
    parts = builder.parts([root])
    assert [e["ref"] for e in parts["mcp"]] == refs[:2] and parts["truncated"] is True


def test_the_mirrored_limits_match_the_clis():
    """builder.py may not import the CLI's modules (the server's allowlist); this test can."""
    from agentbreeder import agentspec, mcp

    fields = agentspec.Spec.model_fields
    assert builder.MAX_TOOLS == fields["tools"].metadata[0].max_length
    assert builder.MAX_SERVERS == fields["mcp"].metadata[0].max_length
    assert builder.MAX_SERVER_TOOLS == agentspec.Mcp.model_fields["tools"].metadata[-1].max_length
    assert builder.STACK_SERVICES == agentspec.STACK_SERVICES
    assert builder.MCP_FILE_LIMIT == mcp.MAX_BYTES
    assert builder.SPEC_VERSIONS == agentspec.SPEC_VERSIONS
    assert builder.MAX_SPEC_BYTES_V1 == agentspec.MAX_SPEC_BYTES_V1
    assert builder.MAX_SPEC_BYTES_V2 == agentspec.MAX_SPEC_BYTES_V2
    script = (Path(builder.__file__).parent / "static" / "lib" / "builder.js").read_text()
    for name in ("MAX_TOOLS", "MAX_SERVERS", "MAX_SERVER_TOOLS"):
        assert f"export const {name} = {getattr(builder, name)};" in script, name


def test_the_consoles_starter_runtime_is_new_agents_default():
    """The form starts on `newagent.RUNTIME`: a spec without sub-agents or skills is verified
    only there (`agentspec.verified`), so another start would be a form Create refuses."""
    script = (Path(builder.__file__).parent / "static" / "lib" / "builder.js").read_text()
    assert f'export const STARTER_RUNTIME = "{newagent.RUNTIME}";' in script


def test_a_version_in_two_roots_is_offered_once_from_the_first(tmp_path):
    """`resolve` reads the first root that has a version; so does the offer."""
    first, second = tmp_path / "first", tmp_path / "second"
    _mcp(first, "notes", "1.0.0", "tools:\n  add: {effect: write}\n")
    _mcp(second, "notes", "1.0.0", "tools:\n  remove: {effect: write}\n")
    [entry] = builder.parts([first, second])["mcp"]
    assert [tool["name"] for tool in entry["tools"]] == ["add"]


def test_a_name_linked_in_one_root_is_not_offered_from_another(tmp_path):
    real, other = tmp_path / "real", tmp_path / "other"
    registry.publish(registry.scaffold("tool", "clock", tmp_path / "a", "0.1.0"), real)
    registry.publish(registry.scaffold("tool", "clock", tmp_path / "b", "0.2.0"), other)
    linked = tmp_path / "linked"
    (linked / "tool").mkdir(parents=True)
    (linked / "tool" / "clock").symlink_to(real / "tool" / "clock")
    parts = builder.parts([linked, other])
    assert parts["tools"] == [] and parts["skipped"] == 2  # 0.1.0 (linked) and 0.2.0 (other)


# -- the framework (#1085 S1): `parts.runtimes` and the spec's `runtime` --------------------------

RUNTIMES_ARGV = ["registry", "runtimes", "--json"]
ADK = "runtime/adk@1.4.0"


def runtime_row(ref=ADK, **changes) -> dict:
    """One usable row of `registry runtimes --json`."""
    name, _, version = ref.removeprefix("runtime/").partition("@")
    row = {
        "ref": ref,
        "name": name,
        "version": version,
        "root_kind": "builtin",
        "display_name": "Google ADK",
        "framework": "google-adk",
        "protocol": 1,
        "profile": "v1",
        "capabilities": ["knowledge", "mcp", "notes"],
        "limits": {"knowledge": 1, "mcp_servers": 10, "tools": 50},
        "reserved_tool_names": ["transfer_to_agent"],
        "verified": True,
        "usable": True,
    }
    return row | changes


UNUSABLE = {
    "ref": "runtime/example@0.1.0",
    "name": "example",
    "version": "0.1.0",
    "root_kind": "path",
    "verified": False,
    "usable": False,
    "code": "PROTOCOL_UNSUPPORTED",
    "reason": "protocol 3 is not supported by this AgentBreeder",
}


def listing(*rows) -> dict:
    return {"schema_version": 1, "runtimes": list(rows)}


@pytest.fixture
def scripted_runtimes(center, monkeypatch):
    """`registry runtimes --json` answered from `answers["result"]` (a `Runner.read` envelope);
    every other child runs. The reads made are in `answers["calls"]`."""
    answers = {"result": None, "calls": []}
    real = center.runner.read

    def read(argv, *args, **kwargs):
        if list(argv) != RUNTIMES_ARGV:
            return real(argv, *args, **kwargs)
        answers["calls"].append(kwargs)
        return answers["result"]

    monkeypatch.setattr(center.runner, "read", read)
    return answers


def test_parts_lists_every_runtime_version_through_the_cli(center, cookie, calls):
    """The engine's resolved view, read by a CLI child and never from the last good answer.
    Every `runtime/adk` version has no `protocol` key, so the legacy profile names it "Google
    ADK" and 1.2.0 and 1.3.0 are usable (console spec §9)."""
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    assert status == 200 and data["error"] is None, data
    assert [call["argv"] for call in calls] == [RUNTIMES_ARGV]
    listed = {entry["ref"]: entry for entry in data["data"]["runtimes"]}
    # run end to end first, by name, newest first
    assert list(listed)[:4] == [
        "runtime/adk@1.5.0",
        "runtime/adk@1.4.0",
        "runtime/adk@1.3.0",
        "runtime/langgraph@1.0.0",
    ]
    for ref in ("runtime/adk@1.2.0", "runtime/adk@1.3.0", "runtime/adk@1.4.0"):
        entry = listed[ref]
        assert entry["usable"] is True and entry["display_name"] == "Google ADK", entry
        assert entry["name"] == "adk" and entry["root_kind"] == "builtin"
        assert entry["protocol"] == 1 and entry["profile"] == "v1"
        assert "mcp" in entry["capabilities"] and entry["limits"]["tools"] == 50
        assert "transfer_to_agent" in entry["reserved_tool_names"]
    assert [listed[f"runtime/adk@1.{n}.0"]["verified"] for n in (2, 3, 4)] == [False, True, True]
    assert "mcp" not in listed["runtime/adk@1.0.4"]["capabilities"]
    # runtime/langgraph@1.0.0 (#1083 F3): a kit runtime, run end to end (verify-langgraph-adk150).
    # runtime/openai-agents@1.0.0 (#1083 F4) and runtime/deepagents@1.0.0 (#1083 F5): kit
    # runtimes, listed, not verified.
    assert {entry["name"] for entry in listed.values()} == {
        "adk",
        "langgraph",
        "openai-agents",
        "deepagents",
    }
    langgraph = listed["runtime/langgraph@1.0.0"]
    assert langgraph["usable"] is True and langgraph["verified"] is True
    assert (langgraph["display_name"], langgraph["profile"], langgraph["kit"]) == (
        "LangGraph",
        "kit",
        "1.0.0",
    )
    assert "mcp" not in langgraph["capabilities"]
    for ref, display_name in (
        ("runtime/openai-agents@1.0.0", "OpenAI Agents SDK"),
        ("runtime/deepagents@1.0.0", "Deep Agents"),
    ):
        entry = listed[ref]
        assert entry["usable"] is True and entry["verified"] is False
        assert (entry["display_name"], entry["profile"]) == (display_name, "kit")
        assert "mcp" not in entry["capabilities"]
    assert listed["runtime/deepagents@1.0.0"]["kit"] == "1.0.0"
    assert {"task", "read_file", "delete"} <= set(
        listed["runtime/deepagents@1.0.0"]["reserved_tool_names"]
    )


def test_the_runtimes_read_is_uncached(center, cookie, scripted_runtimes):
    scripted_runtimes["result"] = {"data": listing(runtime_row()), "error": None, "stale": False}
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    assert status == 200 and [e["ref"] for e in data["data"]["runtimes"]] == [ADK], data
    [call] = scripted_runtimes["calls"]
    assert call["cache"] is False and call["timeout"] == builder.RUNTIMES_TIMEOUT
    assert call["track"] is True and call["max_bytes"] == builder.MAX_RUNTIMES_BYTES


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (
            {"data": None, "error": {"code": "REF_CONFLICT", "message": "two roots"}},
            "REF_CONFLICT",
        ),
        ({"data": {"schema_version": 2, "runtimes": []}, "error": None}, "BAD_OUTPUT"),
        ({"data": [], "error": None}, "BAD_OUTPUT"),
    ],
)
def test_parts_fails_with_the_runtimes_reads_own_code(
    center, cookie, scripted_runtimes, result, code
):
    """No runtime, no preview: the whole answer is the read's error, and the form says so."""
    scripted_runtimes["result"] = result | {"stale": False}
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    assert status == 200 and data["data"] is None, data
    assert data["error"]["code"] == code


RUNTIMES_ROUTE = "/api/runtimes"


def test_the_runtimes_route_is_the_same_view_on_its_own(center, cookie, scripted_runtimes):
    """`GET /api/runtimes` (#1085 S2): the allow-listed view for the framework badge. It only
    names things, so the last good answer is kept (no `cache=False`)."""
    full = runtime_row(secret="dropped")
    scripted_runtimes["result"] = {"data": listing(full, UNUSABLE), "error": None, "stale": False}
    status, data, _ = api(center, "GET", RUNTIMES_ROUTE, None, cookie)
    assert status == 200 and data["error"] is None, data
    assert data["data"] == builder.runtimes(listing(full, UNUSABLE))
    assert "secret" not in data["data"][0]
    [call] = scripted_runtimes["calls"]
    assert "cache" not in call and call["timeout"] == builder.RUNTIMES_TIMEOUT
    assert call["track"] is True and call["max_bytes"] == builder.MAX_RUNTIMES_BYTES


def test_the_runtimes_route_keeps_a_stale_answer(center, cookie, scripted_runtimes):
    problem = {"code": "REF_CONFLICT", "message": "two roots"}
    scripted_runtimes["result"] = {"data": listing(runtime_row()), "error": problem, "stale": True}
    status, data, _ = api(center, "GET", RUNTIMES_ROUTE, None, cookie)
    assert status == 200 and data["stale"] is True and data["error"] == problem, data
    assert [entry["ref"] for entry in data["data"]] == [ADK]


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (
            {"data": None, "error": {"code": "REF_CONFLICT", "message": "two roots"}},
            "REF_CONFLICT",
        ),
        ({"data": {"schema_version": 2, "runtimes": []}, "error": None}, "BAD_OUTPUT"),
        ({"data": [], "error": None}, "BAD_OUTPUT"),
    ],
)
def test_the_runtimes_route_fails_with_the_reads_code(
    center, cookie, scripted_runtimes, result, code
):
    scripted_runtimes["result"] = result | {"stale": False}
    status, data, _ = api(center, "GET", RUNTIMES_ROUTE, None, cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"] == code, data


def test_the_runtimes_route_reads_the_real_command(center, cookie, calls):
    status, data, _ = api(center, "GET", RUNTIMES_ROUTE, None, cookie)
    assert status == 200 and data["error"] is None, data
    assert [call["argv"] for call in calls] == [RUNTIMES_ARGV]
    names = {entry["ref"]: entry["display_name"] for entry in data["data"] if entry["usable"]}
    # Every built-in runtime, named from its manifest: ADK and, since #1163, LangGraph; Deep
    # Agents since #1083 F5.
    assert names[ADK] == "Google ADK" and names["runtime/langgraph@1.0.0"] == "LangGraph"
    assert names["runtime/deepagents@1.0.0"] == "Deep Agents"


def test_parts_shows_a_tools_function_names(center, cookie, path_registry):
    """What a runtime's reserved names are compared with (console spec §4.2)."""
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    assert status == 200, data
    assert [entry["functions"] for entry in data["data"]["tools"]] == [["echo_clock"]] * 2
    assert all("functions" not in entry for entry in data["data"]["mcp"])


@pytest.mark.parametrize(
    "text",
    [None, "functions: nope\n", "functions:\n  - 7\n  - {name: 'a b'}\n", ": : :\n"],
)
def test_a_tool_yaml_that_cannot_be_read_shows_no_function_names(tmp_path, text):
    if text is not None:
        (tmp_path / "tool.yaml").write_text(text)
    assert builder._functions(tmp_path) == []


def test_a_linked_tool_yaml_is_not_read(tmp_path):
    (tmp_path / "real.yaml").write_text("functions:\n  - {name: echo}\n")
    (tmp_path / "tool.yaml").symlink_to(tmp_path / "real.yaml")
    assert builder._functions(tmp_path) == []
    (tmp_path / "tool.yaml").unlink()
    (tmp_path / "real.yaml").rename(tmp_path / "tool.yaml")
    assert builder._functions(tmp_path) == ["echo"]


@pytest.mark.parametrize(
    ("conformance", "result"),
    [
        ({"passed": False, "result": "incomplete"}, "incomplete"),
        ({"passed": False, "result": "fail"}, "fail"),
        ({"passed": True, "result": "pass"}, "pass"),
        ({"passed": False}, "fail"),
    ],
)
def test_a_reports_result_is_kept(conformance, result):
    """An incomplete report is not a failure (#1085 S3): the status names it as incomplete."""
    found = conformance | {"digest": DIGEST, "date": "2026-11-18T10:05:00Z"}
    [shown] = builder.runtimes(listing(runtime_row(conformance=found)))
    assert shown["conformance"]["result"] == result


@pytest.mark.parametrize(
    "conformance",
    [
        {"passed": True, "result": "fail"},
        {"passed": False, "result": "pass"},
        {"passed": False, "result": "maybe"},
    ],
)
def test_a_result_that_contradicts_passed_is_refused(conformance):
    found = conformance | {"digest": DIGEST, "date": "2026-11-18T10:05:00Z"}
    with pytest.raises(builder.BadOutput):
        builder.runtimes(listing(runtime_row(conformance=found)))


def test_runtimes_keeps_what_the_framework_group_shows():
    full = runtime_row(
        "runtime/langgraph@1.0.0",
        display_name="Lang\u202eGraph\n" + "x" * 200,
        framework="langgraph",
        profile="kit",
        kit="1.0.0",
        notes="Needs an Anthropic-shaped\tgateway route. " + "y" * 400,
        docs="docs/runtimes/langgraph.md",
        verified=False,
        conformance={"passed": True, "digest": DIGEST, "date": "2026-11-18", "extra": "dropped"},
        secret="dropped",
    )
    [shown, unusable] = builder.runtimes(listing(full, UNUSABLE | {"path": "/private/x"}))
    assert shown["ref"] == "runtime/langgraph@1.0.0" and shown["name"] == "langgraph"
    assert shown["version"] == "1.0.0" and shown["kit"] == "1.0.0" and shown["profile"] == "kit"
    assert shown["display_name"].startswith("LangGraph x")  # the bidi control and newline go
    assert len(shown["display_name"]) == builder.MAX_DISPLAY_NAME
    assert shown["notes"].startswith("Needs an Anthropic-shaped gateway route. y")
    assert len(shown["notes"]) == builder.MAX_NOTES and shown["notes"].endswith("…")
    assert shown["docs"] == "docs/runtimes/langgraph.md"
    # Without `result` it follows `passed` (#1085 S3); the CLI always sends it.
    assert shown["conformance"] == {
        "passed": True,
        "result": "pass",
        "digest": DIGEST,
        "date": "2026-11-18",
    }
    assert "secret" not in shown
    assert unusable == {
        "ref": "runtime/example@0.1.0",
        "name": "example",
        "version": "0.1.0",
        "root_kind": "path",
        "usable": False,
        "verified": False,
        "code": "PROTOCOL_UNSUPPORTED",
        "reason": "protocol 3 is not supported by this AgentBreeder",
    }
    plain = builder.runtimes(listing(runtime_row()))[0]
    assert not {"kit", "notes", "docs", "conformance"} & set(plain)
    # A version the engine cannot use is never shown as run end to end, whatever the row says.
    [claimed] = builder.runtimes(listing(UNUSABLE | {"verified": True}))
    assert claimed["verified"] is False and claimed["usable"] is False


@pytest.mark.parametrize(
    "docs", ["https://example.com/x", "/etc/passwd", "docs/../../secret", "x" * 201, "a b"]
)
def test_a_docs_value_that_is_not_a_relative_path_is_not_passed_on(docs):
    assert "docs" not in builder.runtimes(listing(runtime_row(docs=docs)))[0]


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"schema_version": 1},
        {"schema_version": 1, "runtimes": [runtime_row()] * (builder.MAX_RUNTIMES + 1)},
        listing(runtime_row(), runtime_row()),  # one ref twice
        listing("runtime/adk@1.4.0"),
        listing(runtime_row() | {"ref": "runtime/adk"}),
        listing(runtime_row() | {"ref": 7}),
        listing(runtime_row() | {"ref": "tool/adk@1.4.0"}),
        listing(runtime_row(root_kind="web")),
        listing(runtime_row(usable="yes")),
        listing(runtime_row(verified=None)),
        listing(runtime_row(display_name="")),
        listing(runtime_row(display_name=7)),
        listing(runtime_row(framework="Google ADK")),
        listing(runtime_row(protocol=True)),
        listing(runtime_row(protocol="1")),
        listing(runtime_row(profile=None)),
        listing(runtime_row(kit="latest")),
        listing(runtime_row(capabilities="mcp")),
        listing(runtime_row(capabilities=["MCP"])),
        listing(runtime_row(capabilities=["a"] * (builder.MAX_CAPABILITIES + 1))),
        listing(runtime_row(limits=[])),
        listing(runtime_row(limits={"tools": -1})),
        listing(runtime_row(limits={"tools": True})),
        listing(runtime_row(limits={"Tools": 1})),
        listing(runtime_row(reserved_tool_names=["a b"])),
        listing(runtime_row(notes=7)),
        listing(runtime_row(docs=7)),
        listing(runtime_row(conformance={"passed": "yes", "digest": DIGEST, "date": "2026"})),
        listing(runtime_row(conformance={"passed": True, "digest": "x", "date": "2026"})),
        listing(runtime_row(conformance={"passed": True, "digest": DIGEST, "date": "a b"})),
        listing(runtime_row(conformance=[])),
        listing(UNUSABLE | {"code": "not a code"}),
        listing(UNUSABLE | {"reason": 7}),
        listing(UNUSABLE | {"ref": "runtime/Example@0.1.0"}),
        listing(UNUSABLE | {"ref": "runtime/example@" + "1" * 41}),
        listing(runtime_row() | {"ref": "runtime/adk@1.4"}),  # usable, but not pinned
    ],
)
def test_runtimes_refuses_an_unexpected_answer(data):
    with pytest.raises(builder.BadOutput, match="registry runtimes"):
        builder.runtimes(data)


def test_a_directory_that_is_not_a_version_is_listed_as_unusable(tmp_path, monkeypatch):
    """`runtime/x/1.0` is not an X.Y.Z version: the real CLI lists it as unusable
    (REF_UNPINNED), and the allow-list keeps that row and every other one, so one stray
    directory on AGENTBREEDER_REGISTRY_PATH cannot take the Build form down."""
    from agentbreeder import runtime_profile

    stray = tmp_path / "root" / "runtime" / "x" / "1.0"
    stray.mkdir(parents=True)
    (stray / "manifest.yaml").write_text("kind: runtime\nname: x\nversion: 1.0\n")
    view = runtime_profile.listing([tmp_path / "root", registry.BUILTIN_ROOT])
    shown = {entry["ref"]: entry for entry in builder.runtimes(view)}
    assert shown["runtime/x@1.0"]["usable"] is False
    assert shown["runtime/x@1.0"]["code"] == "REF_UNPINNED" and shown["runtime/x@1.0"]["reason"]
    assert shown["runtime/adk@1.4.0"]["usable"] is True
    with pytest.raises(BadRequest) as refused:
        builder.check_runtime({"runtime": "runtime/x@1.0"}, list(shown.values()))
    assert refused.value.code == "SPEC_UNSUPPORTED"


def test_an_unusable_row_without_a_reason_is_still_listed():
    [shown] = builder.runtimes(listing(UNUSABLE | {"reason": " \u202e "}))
    assert shown["reason"] == "no reason given" and shown["code"] == "PROTOCOL_UNSUPPORTED"
    [shown] = builder.runtimes(listing({k: v for k, v in UNUSABLE.items() if k != "reason"}))
    assert shown["reason"] == "no reason given"


def test_the_runtime_caps_match_the_engines():
    """builder.py may not import the engine's modules (the server's allowlist); this test can."""
    from agentbreeder import runtime_profile

    assert builder.RUNTIMES_VERSION == runtime_profile.LISTING_VERSION
    assert builder.MAX_DISPLAY_NAME == runtime_profile.MAX_DISPLAY_NAME
    assert builder.MAX_NOTES == runtime_profile.MAX_NOTES
    assert builder.MAX_DOCS == runtime_profile.MAX_DOCS
    assert builder.MAX_CAPABILITIES == runtime_profile.MAX_CAPABILITIES
    assert builder.MAX_RESERVED == runtime_profile.MAX_RESERVED
    assert builder.DOCS.pattern == runtime_profile.DOCS_PATH.pattern
    assert max(runtime_profile.ENGINE_LIMITS.values()) <= builder.MAX_LIMIT
    # Every key the engine's view prints for a usable version is one the allow-list reads.
    printed = set(runtime_profile.listing([registry.BUILTIN_ROOT])["runtimes"][0])
    assert printed <= set(builder.runtimes(listing(runtime_row()))[0])


def test_a_spec_on_the_default_runtime_is_previewed_as_run_end_to_end(center, cookie, home, calls):
    """The form sends a version 2 spec with the runtime's ref (console spec §4.3). The files
    are the ones a version 1 spec writes, and the preview names the runtime."""
    status, plain, _ = preview(center, cookie)
    status, data, _ = api(center, "POST", PREVIEW, {"spec": v2(runtime=ADK)}, cookie)
    assert status == 200, data
    shown = data["data"]
    assert shown["runtime"] == ADK and plain["data"]["runtime"] == ADK
    assert shown["combination"] == {"parts": ["model", "prompt", "evals"], "verified": True}
    assert [ref["ref"] for ref in shown["refs"]] == [ADK]
    assert shown["runs_code"] is False  # a built-in runtime ships with AgentBreeder
    assert shown["files"] == plain["data"]["files"]
    agent = next(e for e in shown["files"] if e["path"] == "agent.yaml")
    assert f"runtime: {ADK}" in agent["content"]
    # The runtimes read comes first, then the dry run.
    assert [call["argv"][:2] for call in calls[1:]] == [["registry", "runtimes"], ["new", "agent"]]
    assert calls[2]["spec"]["runtime"] == ADK


def test_a_run_runtime_version_is_not_a_run_combination(center, cookie, home):
    """`runtime/adk@1.3.0` is "run end to end" in the list (Northwind), but every `new agent`
    combination ran on the default version, so Create stays off for it (console spec §4.3)."""
    spec_ = v2(runtime="runtime/adk@1.3.0")
    status, data, _ = api(center, "POST", PREVIEW, {"spec": spec_}, cookie)
    assert status == 200, data
    assert data["data"]["runtime"] == "runtime/adk@1.3.0"
    assert data["data"]["combination"]["verified"] is False
    body = {"spec": spec_, "digest": data["data"]["plan_sha256"], "code_confirmed": True}
    status, refused, _ = api(center, "POST", CREATE, body, cookie)
    assert status == 400 and refused["error"]["code"] == "COMBINATION_UNVERIFIED", refused
    assert not (home / "agentbreeder-projects" / "helper").exists()


def test_a_runtime_without_mcp_shows_the_clis_refusal(center, cookie, home):
    spec_ = v2(runtime="runtime/adk@1.0.4", mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])
    status, data, _ = api(center, "POST", PREVIEW, {"spec": spec_}, cookie)
    assert status == 400 and data["error"]["code"] == "MCP_RUNTIME_UNSUPPORTED", data


@pytest.mark.parametrize(
    ("rows", "ref", "message"),
    [
        ([runtime_row()], "runtime/langgraph@1.0.0", "is not a runtime version in the registries"),
        (
            [runtime_row(), UNUSABLE],
            "runtime/example@0.1.0",
            "runtime/example@0.1.0 cannot be used: protocol 3 is not supported by this "
            "AgentBreeder (PROTOCOL_UNSUPPORTED)",
        ),
    ],
)
def test_a_runtime_that_is_not_listed_and_usable_runs_no_new_agent(
    center, cookie, home, calls, scripted_runtimes, rows, ref, message
):
    """`SPEC_UNSUPPORTED`, on preview and on create, before any `new agent` child."""
    scripted_runtimes["result"] = {"data": listing(*rows), "error": None, "stale": False}
    for path, body in (
        (PREVIEW, {"spec": v2(runtime=ref)}),
        (CREATE, {"spec": v2(runtime=ref), "digest": DIGEST, "code_confirmed": True}),
    ):
        status, data, _ = api(center, "POST", path, body, cookie)
        assert status == 400 and data["error"]["code"] == "SPEC_UNSUPPORTED", data
        assert message in data["error"]["message"]
    assert [call["argv"][:2] for call in calls] == []  # `calls` wraps the scripted read
    assert not (home / "agentbreeder-projects" / "helper").exists()


def test_a_failed_runtimes_read_refuses_the_spec_with_its_code(
    center, cookie, home, scripted_runtimes
):
    scripted_runtimes["result"] = {
        "data": None,
        "error": {"code": "TIMEOUT", "message": "The command timed out"},
        "stale": False,
    }
    status, data, _ = api(center, "POST", PREVIEW, {"spec": v2(runtime=ADK)}, cookie)
    assert status == 400 and data["error"]["code"] == "TIMEOUT", data


def test_a_spec_without_a_runtime_reads_no_runtimes(center, cookie, home, scripted_runtimes):
    """A version 1 spec (and a version 2 one that names no runtime) is the CLI's default."""
    status, data, _ = preview(center, cookie)
    assert status == 200 and data["data"]["runtime"] == ADK, data
    assert scripted_runtimes["calls"] == []


def test_create_on_the_default_runtime_writes_and_registers_it(center, cookie, home, calls):
    data, [(status, added, _)] = preview_then_create(
        center, cookie, False, calls=calls, schema_version=2, runtime=ADK
    )
    assert status == 200, added
    destination = home / "agentbreeder-projects" / "helper"
    assert f"runtime: {ADK}" in (destination / "agent.yaml").read_text()
    assert (destination / "agent.lock").is_file()
    assert [p["path"] for p in center.state.projects()] == [str(destination)]


@pytest.fixture
def path_runtime(tmp_path, monkeypatch):
    """`runtime/adk@9.9.0` on AGENTBREEDER_REGISTRY_PATH: the built-in 1.4.0's files under
    another version, so it is code from outside the built-in registry."""
    source = tmp_path / "adk-src"
    shutil.copytree(registry.BUILTIN_ROOT / "runtime" / "adk" / "1.4.0", source)
    manifest = source / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("version: 1.4.0", "version: 9.9.0", 1))
    root = tmp_path / "runtime-registry"
    registry.publish(source, root)
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    return "runtime/adk@9.9.0"


def test_a_path_runtime_is_listed_from_its_root_and_previewed_as_code(
    center, cookie, home, path_runtime
):
    """The code confirm lists a runtime from outside the built-in registry, as for a tool
    (console spec §4.3)."""
    status, data, _ = api(center, "GET", PARTS, None, cookie)
    listed = {entry["ref"]: entry for entry in data["data"]["runtimes"]}
    assert listed[path_runtime]["root_kind"] == "path" and listed[path_runtime]["usable"] is True
    assert listed[path_runtime]["verified"] is False
    spec_ = v2(runtime=path_runtime)
    status, data, _ = api(center, "POST", PREVIEW, {"spec": spec_}, cookie)
    assert status == 200, data
    shown = data["data"]
    assert shown["runs_code"] is True and shown["combination"]["verified"] is False
    [ref] = shown["refs"]
    assert (ref["ref"], ref["root_kind"], ref["runs_code"]) == (path_runtime, "path", True)


# -- knowledge (#1069) ---------------------------------------------------------------------------


def test_the_embedding_providers_match_the_cli():
    from agentbreeder import agentspec, stackfor

    assert builder.EMBEDDING_PROVIDERS == tuple(sorted(agentspec.EMBEDDINGS))
    assert builder.ALIAS.pattern == stackfor.ALIAS.pattern
    from agentbreeder import knowledge

    assert builder.DEFAULT_DIMENSIONS == knowledge.DEFAULT_DIMENSIONS
    assert builder.MAX_DIMENSIONS == knowledge.MAX_DIMENSIONS
    # The browser's copy of the providers (lib/builder.js) is the same list.
    source = (Path(builder.__file__).parent / "static" / "lib" / "builder.js").read_text()
    assert 'export const EMBEDDING_PROVIDERS = ["gemini", "openai"];' in source
    fields = agentspec.Knowledge.model_fields
    assert builder.TOP_K == (fields["top_k"].metadata[0].ge, fields["top_k"].metadata[1].le)
    assert builder.EXTRA_SEARCHES == (
        fields["extra_searches"].metadata[0].ge,
        fields["extra_searches"].metadata[1].le,
    )


@pytest.mark.parametrize("model", ["gemini", "openai"])
def test_one_knowledge_version_is_accepted_for_a_provider_with_embeddings(model):
    entry = {"ref": "knowledge/docs@0.1.0", "top_k": 5, "extra_searches": 1}
    name, checked = builder.request({"spec": spec(model=model, knowledge=[entry])})
    assert name == "helper" and checked["knowledge"] == [entry]


KNOWLEDGE_ROW = {
    "ref": "knowledge/docs@0.1.0",
    "alias": "docs-embedding",
    "dimensions": 768,
    "embedding_model": "gemini/gemini-embedding-2",
    "top_k": 5,
    "extra_searches": 1,
    "documents": 3,
    "chunks": 12,
}


def test_summary_shows_the_knowledge_row():
    shown = builder.summary(answer(knowledge=[KNOWLEDGE_ROW]), *ASKED)
    assert shown["knowledge"] == [KNOWLEDGE_ROW]
    assert builder.summary(answer(), *ASKED)["knowledge"] == []


@pytest.mark.parametrize(
    "row",
    [
        KNOWLEDGE_ROW | {"ref": "tool/docs@0.1.0"},
        KNOWLEDGE_ROW | {"alias": "bad alias"},
        KNOWLEDGE_ROW | {"embedding_model": "x y"},
        KNOWLEDGE_ROW | {"dimensions": True},
        KNOWLEDGE_ROW | {"chunks": -1},
        "knowledge/docs@0.1.0",
    ],
)
def test_summary_refuses_a_knowledge_row_off_contract(row):
    with pytest.raises(builder.BadOutput):
        builder.summary(answer(knowledge=[row]), *ASKED)
    with pytest.raises(builder.BadOutput):
        builder.summary(answer(knowledge=[KNOWLEDGE_ROW, KNOWLEDGE_ROW]), *ASKED)


def _knowledge(root: Path, name: str, version: str, embedding: str) -> None:
    source = root.parent / f"kb-{name}-{version}"
    source.mkdir(parents=True)
    (source / "manifest.yaml").write_text(
        f"kind: knowledge\nname: {name}\nversion: {version}\ndescription: Help articles.\n"
        f"chunking: paragraph\n{embedding}store: pgvector\n"
    )
    (source / "docs").mkdir()
    (source / "docs" / "a.md").write_text("# A\n\nOne paragraph.\n")
    registry.publish(source, root)


def test_parts_offers_knowledge_versions_with_their_embedding(tmp_path, monkeypatch):
    root = tmp_path / "path-registry"
    _knowledge(root, "help", "1.0.0", "embedding: {alias: help-embedding, dimensions: 768}\n")
    _knowledge(root, "bare", "1.0.0", "")
    shown = {e["ref"]: e for e in builder.parts([root])["knowledge"]}
    assert shown["knowledge/help@1.0.0"]["embedding"] == {
        "alias": "help-embedding",
        "dimensions": 768,
    }
    assert "unusable" not in shown["knowledge/help@1.0.0"]
    assert shown["knowledge/help@1.0.0"]["description"] == "Help articles."
    assert shown["knowledge/bare@1.0.0"]["embedding"] is None
    assert "no valid embedding" in shown["knowledge/bare@1.0.0"]["unusable"]


@pytest.mark.parametrize(
    ("embedding", "expected"),
    [
        ({"alias": "a"}, {"alias": "a", "dimensions": 768}),
        ({"alias": "a", "dimensions": "1536"}, {"alias": "a", "dimensions": 1536}),
        ({"alias": "a", "dimensions": 16000}, {"alias": "a", "dimensions": 16000}),
        ({"alias": "a", "dimensions": 16001}, None),
        ({"alias": "a", "dimensions": 0}, None),
        ({"alias": "a", "dimensions": True}, None),
        ({"alias": "a", "dimensions": "x"}, None),
        ({"alias": "bad alias"}, None),
        ({}, None),
        ("text", None),
    ],
)
def test_the_embedding_is_read_as_the_cli_reads_it(embedding, expected):
    assert builder._embedding({"embedding": embedding}) == expected
    assert builder._embedding({}) is None
