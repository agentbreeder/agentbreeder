# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Build a new agent in the console (#833, M17 console half): the spec as a private temporary
file, the `new agent --from-spec --dry-run --json` preview with every file's text, create pinned
to the previewed `plan_sha256`, only verified combinations created, and the "this code runs in
agents" re-check. Run through the real CLI."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from test_center import api, center, cookie  # noqa: F401 (fixtures)

from agentbreeder import registry
from agentbreeder.center import builder, server

PREVIEW = "/api/projects/builder"
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
    status, data, _ = preview(center, cookie, model="openai")
    assert status == 200, data
    assert data["data"]["combination"]["verified"] is False
    assert data["data"]["keys"] == ["OPENAI_API_KEY"]


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
        ({"spec": spec(schema_version=2)}, "SPEC_INVALID"),
        ({"spec": spec(name="Helper")}, "NAME_INVALID"),
        ({"spec": spec(name="../x")}, "NAME_INVALID"),
        ({"spec": spec(name=7)}, "NAME_INVALID"),
        ({"spec": spec(model="mistral")}, "MODEL_INVALID"),
        ({"spec": spec(model_id="gemini/a b")}, "MODEL_ID_INVALID"),
        ({"spec": spec(model_id=3)}, "MODEL_ID_INVALID"),
        ({"spec": spec(knowledge=["knowledge/docs@0.1.0"])}, "SPEC_UNSUPPORTED"),
        ({"spec": spec(tools=["tool/echo@0.1.0"])}, "SPEC_UNSUPPORTED"),
        (
            {"spec": spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])},
            "SPEC_UNSUPPORTED",
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


# -- create ------------------------------------------------------------------------------------


def test_create_writes_the_previewed_plan_and_registers_it(center, cookie, home, calls):
    _, data, _ = preview(center, cookie)
    digest = data["data"]["plan_sha256"]
    status, added, _ = create(center, cookie, digest)
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
    assert not (home / "agentbreeder-projects" / "helper").exists()
    assert center.state.projects() == []


@pytest.mark.parametrize("digest", [None, "", "sha256:xyz", 7])
def test_create_needs_a_preview_first(center, cookie, home, calls, digest):
    status, data, _ = create(center, cookie, digest)
    assert status == 400 and data["error"]["code"] == "PREVIEW_REQUIRED"
    assert calls == []


def test_create_refuses_a_combination_not_run_end_to_end(center, cookie, home, calls):
    _, data, _ = preview(center, cookie, model="openai")
    status, refused, _ = create(center, cookie, data["data"]["plan_sha256"], model="openai")
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
    _, data, _ = preview(center, cookie)
    (home / "agentbreeder-projects" / "helper").mkdir()
    (home / "agentbreeder-projects" / "helper" / "notes.txt").write_text("mine")
    status, refused, _ = create(center, cookie, data["data"]["plan_sha256"])
    assert status == 400 and refused["error"]["code"] not in ("BAD_OUTPUT", "")
    assert center.state.projects() == []


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
    ],
)
def test_summary_refuses_an_unexpected_answer(changes):
    with pytest.raises(builder.BadOutput):
        builder.summary(answer(**changes), *ASKED)
