# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Onboard an `agent.yaml` alone in the console (#823, M7): the `new agent --stack-for` dry-run
preview, the three-tier confirm's server check (code from outside the built-in registry needs
`code_confirmed`), the write pinned to the previewed digest, and registration's S-01 re-check."""

from __future__ import annotations

import json
import os
import sys
import textwrap
import threading
from datetime import date
from pathlib import Path

import pytest
from test_center import api, cookie  # noqa: F401 (fixtures)

from agentbreeder import newagent
from agentbreeder.center import onboard, server
from agentbreeder.center.server import Center, make_server

DIGEST = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64
FAKE = textwrap.dedent(
    f"""
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    calls = marker + "/calls.json"
    seen = json.load(open(calls)) if os.path.exists(calls) else []
    json.dump(seen + [argv], open(calls, "w"))
    if argv[:2] != ["new", "agent"]:
        print("[]")
        sys.exit(0)
    path = next(a.split("=", 1)[1] for a in argv if a.startswith("--stack-for="))
    expect = [a.split("=", 1)[1] for a in argv if a.startswith("--expect-digest=")]
    dry = "--dry-run" in argv

    def refuse(code, message):
        print(json.dumps({{"schema_version": 1, "error": {{"code": code, "message": message}}}}))
        sys.stderr.write("[" + code + "] " + message + "\\n")
        sys.exit(2)

    if os.path.exists(marker + "/refuse"):
        refuse("NAME_MISMATCH", "\\x1b[31mNAME must be the agent's name in agent.yaml (support)")
    if expect and expect[0] != "{DIGEST}":
        refuse("DIGEST_MISMATCH", path + " is not the file that was previewed")
    if os.path.exists(marker + ("/bad-output" if dry else "/bad-write")):
        print(json.dumps({{"schema_version": 1, "dry_run": dry, "name": argv[2]}}))
        sys.exit(0)
    code = os.path.exists(marker + "/runs-code")
    refs = [
        {{"ref": "runtime/adk@1.2.0", "kind": "runtime", "root_kind": "builtin",
          "runs_code": True}},
        {{"ref": "prompt/p@0.1.0", "kind": "prompt", "root_kind": "project",
          "runs_code": False}},
    ]
    if code:
        refs.append({{"ref": "eval/e@0.1.0", "kind": "eval", "root_kind": "project",
                     "runs_code": True}})
    lie = os.path.exists(marker + "/hide-code")
    summary = {{
        "schema_version": 1, "dry_run": dry, "name": argv[2],
        "directory": os.path.dirname(path), "agent_file": path,
        "model": argv[argv.index("--model") + 1], "model_id": "gemini/gemini-3.8-flash",
        "verified": True, "key": "GEMINI_API_KEY", "aliases": ["chat", "judge"],
        "review_by": "2027-05-12",
        "files": [{{"path": "stack.yaml", "mode": "0644", "sha256": "{DIGEST}"}},
                  {{"path": "stack/litellm/config.yaml", "mode": "0644", "sha256": "{OTHER}"}}],
        "warnings": [], "agent_sha256": "{DIGEST}", "refs": refs,
        "runs_code": False if lie else code,
    }}
    if not dry:
        stack = os.path.join(os.path.dirname(path), "stack.yaml")
        with open(stack, "w") as handle:
            handle.write("kind: stack\\n")
        os.chmod(stack, 0o666 if os.path.exists(marker + "/loose") else 0o644)
    print(json.dumps(summary, indent=2))
    """
)


@pytest.fixture
def center(tmp_path, monkeypatch):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    directory = tmp_path / "center"
    directory.mkdir(mode=0o700)
    instance = Center(directory, prefix=[sys.executable, str(script)])
    http_server = make_server(instance, 0)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    yield instance
    http_server.shutdown()
    http_server.server_close()


@pytest.fixture
def agent_dir(tmp_path) -> Path:
    path = tmp_path / "support"
    path.mkdir(mode=0o755)
    (path / "agent.yaml").write_text("kind: agent\n")
    os.chmod(path / "agent.yaml", 0o644)
    return path


def calls(tmp_path) -> list[list[str]]:
    path = tmp_path / "calls.json"
    return json.loads(path.read_text()) if path.exists() else []


def body(agent_dir, **changes) -> dict:
    return {"path": str(agent_dir / "agent.yaml"), "name": "support", "model": "gemini", **changes}


PREVIEW = "/api/projects/stack-for"


# -- the preview -------------------------------------------------------------------------------


def test_preview_runs_a_dry_run_with_fixed_arguments(center, cookie, agent_dir, tmp_path):
    status, data, _ = api(center, "POST", PREVIEW, body(agent_dir), cookie)
    assert status == 200, data
    assert calls(tmp_path) == [
        [
            "new",
            "agent",
            "support",
            "--model",
            "gemini",
            f"--stack-for={agent_dir / 'agent.yaml'}",
            "--dry-run",
            "--json",
        ]
    ]
    shown = data["data"]
    assert shown["agent_sha256"] == DIGEST and shown["runs_code"] is False
    assert [entry["path"] for entry in shown["files"]] == [
        "stack.yaml",
        "stack/litellm/config.yaml",
    ]
    assert shown["refs"][0] == {
        "ref": "runtime/adk@1.2.0",
        "kind": "runtime",
        "root_kind": "builtin",
        "runs_code": True,
    }
    assert "agent_file" not in shown and "mode" not in shown["files"][0]
    assert not (agent_dir / "stack.yaml").exists()
    assert center.state.projects() == []


def test_preview_takes_the_directory_and_a_model_id(center, cookie, agent_dir, tmp_path):
    request = body(agent_dir, path=f"{agent_dir}/", model="openrouter", model_id="openrouter/a/b")
    status, data, _ = api(center, "POST", PREVIEW, request, cookie)
    assert status == 200, data
    argv = calls(tmp_path)[0]
    assert "--model-id=openrouter/a/b" in argv and argv[4] == "openrouter"
    assert f"--stack-for={agent_dir}/agent.yaml" in argv


def test_preview_shows_code_from_outside_the_built_ins(center, cookie, agent_dir, tmp_path):
    (tmp_path / "runs-code").write_text("")
    _, data, _ = api(center, "POST", PREVIEW, body(agent_dir), cookie)
    assert data["data"]["runs_code"] is True
    assert data["data"]["refs"][-1]["kind"] == "eval"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"name": "Support"}, "NAME_INVALID"),
        ({"name": "a" * 32}, "NAME_INVALID"),
        ({"name": ["support"]}, "NAME_INVALID"),
        ({"model": "mistral"}, "MODEL_INVALID"),
        ({"model": None}, "MODEL_INVALID"),
        ({"path": "relative/agent.yaml"}, "PATH_INVALID"),
        ({"path": "/x/\nagent.yaml"}, "PATH_INVALID"),
        ({"path": "/x/\x85/agent.yaml"}, "PATH_INVALID"),
        ({"path": "/" + "a" * 4096}, "PATH_INVALID"),
        ({"path": 7}, "PATH_INVALID"),
        ({"model_id": "gemini/a b"}, "MODEL_ID_INVALID"),
        ({"model_id": "x" * 129}, "MODEL_ID_INVALID"),
        ({"model_id": 3}, "MODEL_ID_INVALID"),
    ],
)
def test_a_bad_request_runs_nothing(center, cookie, agent_dir, tmp_path, change, code):
    status, data, _ = api(center, "POST", PREVIEW, body(agent_dir, **change), cookie)
    assert status == 400 and data["error"]["code"] == code
    assert calls(tmp_path) == []


def test_a_body_that_is_not_an_object_is_refused():
    with pytest.raises(server.BadRequest):
        onboard.request(["support"])


def test_preview_shows_the_clis_own_coded_refusal(center, cookie, agent_dir, tmp_path):
    (tmp_path / "refuse").write_text("")
    status, data, _ = api(center, "POST", PREVIEW, body(agent_dir), cookie)
    assert status == 400 and data["error"]["code"] == "NAME_MISMATCH"
    assert "\x1b" not in data["error"]["message"]
    assert "agent's name in agent.yaml (support)" in data["error"]["message"]


@pytest.mark.parametrize("marker", ["bad-output", "hide-code"])
def test_preview_refuses_an_answer_off_the_contract(center, cookie, agent_dir, tmp_path, marker):
    (tmp_path / "runs-code").write_text("")
    (tmp_path / marker).write_text("")
    status, data, _ = api(center, "POST", PREVIEW, body(agent_dir), cookie)
    assert status == 400 and data["error"]["code"] == "BAD_OUTPUT"


# -- the write ---------------------------------------------------------------------------------


@pytest.mark.parametrize("digest", [None, "", "sha256:abc", 7])
def test_writing_needs_the_previewed_digest(center, cookie, agent_dir, tmp_path, digest):
    request = {"stack_for": body(agent_dir, digest=digest)}
    status, data, _ = api(center, "POST", "/api/projects", request, cookie)
    assert status == 400 and data["error"]["code"] == "PREVIEW_REQUIRED"
    assert calls(tmp_path) == []


def test_a_stack_for_body_that_is_not_an_object_is_refused(center, cookie, tmp_path):
    status, data, _ = api(center, "POST", "/api/projects", {"stack_for": "x"}, cookie)
    assert status == 400 and data["error"]["code"] == "PREVIEW_REQUIRED"


def test_writing_checks_the_digest_then_writes_then_registers(center, cookie, agent_dir, tmp_path):
    request = {"stack_for": body(agent_dir, digest=DIGEST)}
    status, data, _ = api(center, "POST", "/api/projects", request, cookie)
    assert status == 200, data
    base = ["new", "agent", "support", "--model", "gemini", f"--stack-for={agent_dir}/agent.yaml"]
    assert calls(tmp_path) == [
        [*base, f"--expect-digest={DIGEST}", "--dry-run", "--json"],
        [*base, f"--expect-digest={DIGEST}", "--json"],
    ]
    assert (agent_dir / "stack.yaml").is_file()
    assert data["data"]["path"] == str(agent_dir.resolve())
    assert [project["path"] for project in center.state.projects()] == [str(agent_dir.resolve())]


def test_code_from_outside_the_built_ins_needs_the_confirm(center, cookie, agent_dir, tmp_path):
    (tmp_path / "runs-code").write_text("")
    for confirmed in (None, False, "yes", "true", 1):
        request = {"stack_for": body(agent_dir, digest=DIGEST, code_confirmed=confirmed)}
        status, data, _ = api(center, "POST", "/api/projects", request, cookie)
        assert status == 400 and data["error"]["code"] == "CODE_CONFIRM_REQUIRED"
    assert all("--dry-run" in argv for argv in calls(tmp_path))  # never the write
    assert not (agent_dir / "stack.yaml").exists() and center.state.projects() == []
    request = {"stack_for": body(agent_dir, digest=DIGEST, code_confirmed=True)}
    status, data, _ = api(center, "POST", "/api/projects", request, cookie)
    assert status == 200, data
    assert (agent_dir / "stack.yaml").is_file()


def test_a_changed_agent_file_is_refused_before_writing(center, cookie, agent_dir, tmp_path):
    request = {"stack_for": body(agent_dir, digest=OTHER)}
    status, data, _ = api(center, "POST", "/api/projects", request, cookie)
    assert status == 400 and data["error"]["code"] == "DIGEST_MISMATCH"
    assert len(calls(tmp_path)) == 1 and not (agent_dir / "stack.yaml").exists()


def test_registration_rechecks_ownership_after_the_write(center, cookie, agent_dir, tmp_path):
    """S-01 at registration: a stack.yaml others can write is refused even after `new agent`
    accepted the directory."""
    (tmp_path / "loose").write_text("")
    request = {"stack_for": body(agent_dir, digest=DIGEST)}
    status, data, _ = api(center, "POST", "/api/projects", request, cookie)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert "stack.yaml" in data["error"]["message"] and center.state.projects() == []
    # The files stay, and the message says how to finish (a preview would meet STACK_EXISTS).
    assert (agent_dir / "stack.yaml").is_file()
    assert "was written but the project was not added" in data["error"]["message"]
    assert "An existing project" in data["error"]["message"]


def test_a_write_answer_off_the_contract_is_not_registered(center, cookie, agent_dir, tmp_path):
    (tmp_path / "bad-write").write_text("")
    request = {"stack_for": body(agent_dir, digest=DIGEST)}
    status, data, _ = api(center, "POST", "/api/projects", request, cookie)
    assert status == 400 and data["error"]["code"] == "BAD_OUTPUT"
    assert "was written but the project was not added" in data["error"]["message"]
    assert center.state.projects() == []


def test_the_write_answer_is_checked_too(center, cookie, agent_dir, tmp_path, monkeypatch):
    real = onboard.summary

    def strict(data, name, dry_run, model=None):
        if not dry_run:
            raise onboard.BadOutput("off the contract")
        return real(data, name, dry_run, model)

    monkeypatch.setattr(onboard, "summary", strict)
    request = {"stack_for": body(agent_dir, digest=DIGEST)}
    status, data, _ = api(center, "POST", "/api/projects", request, cookie)
    assert status == 400 and data["error"]["code"] == "BAD_OUTPUT"
    assert center.state.projects() == []


# -- the answer's shape ------------------------------------------------------------------------


def _answer(**changes) -> dict:
    answer = {
        "schema_version": 1,
        "dry_run": True,
        "name": "support",
        "directory": "/p",
        "model": "gemini",
        "model_id": "gemini/x",
        "key": "GEMINI_API_KEY",
        "review_by": "2027-01-01",
        "verified": True,
        "aliases": ["chat"],
        "warnings": [],
        "files": [{"path": "stack.yaml", "sha256": DIGEST}],
        "refs": [],
        "runs_code": False,
        "agent_sha256": DIGEST,
    }
    answer.update(changes)
    return answer


def test_a_good_answer_passes():
    assert onboard.summary(_answer(), "support", dry_run=True)["agent_sha256"] == DIGEST
    assert onboard.summary(_answer(), "support", dry_run=True, model="gemini")


def test_an_answer_for_another_provider_is_refused():
    with pytest.raises(onboard.BadOutput):
        onboard.summary(_answer(), "support", dry_run=True, model="openai")


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"dry_run": False},
        {"name": "other"},
        {"directory": 1},
        {"model": "mistral"},
        {"verified": "yes"},
        {"agent_sha256": "sha256:x"},
        {"files": []},
        {"files": [{"path": "stack.yaml", "sha256": "x"}]},
        {"files": ["stack.yaml"]},
        {"files": "stack.yaml"},
        {"refs": "x"},
        {"refs": ["x"]},
        {"refs": [{"ref": "a", "kind": "b", "root_kind": "elsewhere", "runs_code": False}]},
        {"refs": [{"ref": "a", "kind": "b", "root_kind": "project", "runs_code": "no"}]},
        {"refs": [{"ref": 1, "kind": "b", "root_kind": "project", "runs_code": False}]},
        {"runs_code": True},
        {"aliases": [1]},
        {"aliases": "chat"},
        {"warnings": ["w"] * 21},
    ],
)
def test_an_answer_off_the_contract_is_refused(change):
    with pytest.raises(onboard.BadOutput):
        onboard.summary(_answer(**change), "support", dry_run=True)
    with pytest.raises(onboard.BadOutput):
        onboard.summary(["not", "a", "dict"], "support", dry_run=True)


def test_the_console_rules_are_new_agents():
    assert onboard.PROVIDERS == tuple(sorted(newagent.PROVIDERS))
    assert onboard.NAME.pattern == newagent.NAME_PATTERN == server.STARTER_NAME.pattern
    assert onboard.MODEL_ID.pattern == newagent.MODEL_ID_PATTERN
    from agentbreeder import registry, stackfor

    assert onboard.DIGEST.pattern == registry.DIGEST.pattern
    assert onboard.AGENT_FILE == stackfor.AGENT_FILE


def test_the_browser_rules_are_new_agents():
    source = (server.STATIC / "lib" / "onboard.js").read_text()
    assert f"export const PROVIDERS = {json.dumps(list(onboard.PROVIDERS))};" in source


# -- the real CLI ------------------------------------------------------------------------------


@pytest.fixture
def real_center(tmp_path, monkeypatch):
    from agentbreeder import registry

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    directory = tmp_path / "center"
    directory.mkdir(mode=0o700)
    instance = Center(directory, prefix=[sys.executable, "-m", "agentbreeder"])
    http_server = make_server(instance, 0)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    yield instance
    http_server.shutdown()
    http_server.server_close()


def test_onboarding_a_starter_agent_file_with_the_real_cli(real_center, tmp_path):
    project = tmp_path / "projects" / "hello"
    project.parent.mkdir(mode=0o755)
    newagent.create(newagent.plan("hello", "gemini", project, today=date(2026, 11, 13)))
    (project / "stack.yaml").unlink()
    (project / "stack/litellm/config.yaml").unlink()
    url, _ = real_center.mint_login()
    status, data, _ = api(real_center, "POST", "/api/session", {"token": url.split("#t=")[1]})
    session = data["data"]["session"]
    request = {"path": str(project), "name": "hello", "model": "gemini"}
    status, data, _ = api(real_center, "POST", PREVIEW, request, session)
    assert status == 200, data
    preview = data["data"]
    assert preview["runs_code"] is True  # the project's own eval set
    assert not (project / "stack.yaml").exists()
    request = {**request, "digest": preview["agent_sha256"]}
    status, data, _ = api(real_center, "POST", "/api/projects", {"stack_for": request}, session)
    assert status == 400 and data["error"]["code"] == "CODE_CONFIRM_REQUIRED"
    request["code_confirmed"] = True
    status, data, _ = api(real_center, "POST", "/api/projects", {"stack_for": request}, session)
    assert status == 200, data
    assert data["data"]["path"] == str(project.resolve())
    assert (project / "stack.yaml").is_file() and (project / "stack/litellm/config.yaml").is_file()
    # A second write is refused by `new agent` itself: the stack exists now.
    status, data, _ = api(real_center, "POST", PREVIEW, request, session)
    assert status == 400 and data["error"]["code"] == "STACK_EXISTS"
