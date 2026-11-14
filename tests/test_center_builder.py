# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Build a new agent in the console (#833, M17 console half): the spec as a private temporary
file, the `new agent --from-spec --dry-run --json` preview with every file's text, create pinned
to the previewed `plan_sha256`, only verified combinations created, and the "this code runs in
agents" re-check. The tool and MCP pickers' catalog (`GET /api/builder/parts`) and picks. Run
through the real CLI."""

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


def test_a_picked_path_tool_is_previewed_as_code_not_run_end_to_end(
    center, cookie, home, calls, path_registry
):
    status, data, _ = preview(center, cookie, tools=["tool/clock@0.2.0"])
    assert status == 200, data
    shown = data["data"]
    assert shown["combination"] == {
        "parts": ["model", "prompt", "evals", "tools"],
        "verified": False,
    }
    [ref] = shown["refs"]
    assert ref["ref"] == "tool/clock@0.2.0" and ref["root_kind"] == "path"
    assert ref["runs_code"] is True and shown["runs_code"] is True
    agent = next(e for e in shown["files"] if e["path"] == "agent.yaml")["content"]
    assert '- {ref: "tool/clock@0.2.0"}' in agent
    assert calls[0]["spec"]["tools"] == ["tool/clock@0.2.0"]
    # Not run end to end, so Create refuses it even when the code is confirmed.
    status, refused, _ = create(
        center, cookie, shown["plan_sha256"], confirmed=True, tools=["tool/clock@0.2.0"]
    )
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


def test_once_verified_a_path_tool_is_created_after_the_code_confirm(
    center, cookie, home, calls, path_registry, monkeypatch
):
    """When a run adds `("tools",)` to `agentspec.VERIFIED`, Create works with no console
    change. Simulated: every dry-run answer of the real CLI is marked verified."""
    real = center._new_agent

    def verified(argv):
        data = real(argv)
        if "--dry-run" in argv:
            data["combination"]["verified"] = True
        return data

    monkeypatch.setattr(center, "_new_agent", verified)
    tools = ["tool/clock@0.2.0"]
    _, data, _ = preview(center, cookie, tools=tools)
    digest = data["data"]["plan_sha256"]
    assert data["data"]["runs_code"] is True
    status, refused, _ = create(center, cookie, digest, tools=tools)
    assert status == 400 and refused["error"]["code"] == "CODE_CONFIRM_REQUIRED"
    destination = home / "agentbreeder-projects" / "helper"
    assert not destination.exists()
    status, added, _ = create(center, cookie, digest, confirmed=True, tools=tools)
    assert status == 200, added
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
    assert builder.SPEC_VERSION == agentspec.SPEC_VERSION
    assert builder.MAX_SPEC_BYTES == agentspec.MAX_SPEC_BYTES
    script = (Path(builder.__file__).parent / "static" / "lib" / "builder.js").read_text()
    for name in ("MAX_TOOLS", "MAX_SERVERS", "MAX_SERVER_TOOLS"):
        assert f"export const {name} = {getattr(builder, name)};" in script, name


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
