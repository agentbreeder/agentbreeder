"""MCP repin without a TTY (#964): `services pins` (the review and its digest) and
`services up --repin --expect-pins SHA256` (spec 2026-11-14-mcp-repin-expect-pins.md).

Docker is faked (RelayDocker answers the relay's probe from `docker.upstream`); the live relay
probe runs in tests/test_services_pins_docker.py.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_mcp_stack import AGENT, RelayDocker, run_up, stack, stack_dir, tool, write_mcp
from test_mcp_stack import project as project  # noqa: F401 (fixture)
from test_services_engine import write_project

from agentbreeder import cli
from agentbreeder.services import mcp_stack
from agentbreeder.services import plan as plan_mod
from agentbreeder.services.engine import Engine
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import services_lock, stacks_root

# -- helpers --------------------------------------------------------------------------------------


def read_review(project, docker) -> dict:
    engine = Engine(project, docker=docker)
    try:
        return engine.pins()
    finally:
        engine.close()


def repin(project, docker, digest, *, mode="plain", seen=None):
    return run_up(project, docker, repin=True, pins=False, seen=seen, mode=mode, expect=digest)


def snapshot(project, result_or_root) -> dict:
    """stack.lock, every pins file, every secret file (incl. the admin token)."""
    root = result_or_root if isinstance(result_or_root, Path) else stack_dir(result_or_root)
    files = {"stack.lock": (project / "stack.lock").read_bytes()}
    for sub in ("pins", "service-secrets"):
        for path in sorted((root / sub).rglob("*")):
            if path.is_file():
                files[str(path.relative_to(root))] = path.read_bytes()
    return files


@pytest.fixture
def pinned(project):  # noqa: F811
    """A stack whose github relay is up with list_issues and create_issue pinned."""
    docker = RelayDocker()
    result, _ = run_up(project, docker)
    return docker, result


# -- the digest -----------------------------------------------------------------------------------


def _document():
    return {
        "v": 1,
        "stack": "s-1",
        "servers": {
            "github": {
                "identity": {"ref": "mcp/github@0.1.0", "origin": "built-in", "upstream": "u",
                             "token_url": None},
                "pinned": {"a": "sha256:aa"},
                "accept": {"b": "sha256:bb"},
                "drop": ["c"],
            }
        },
    }  # fmt: skip


def test_digest_is_stable_across_key_order_and_binds_every_field():
    document = _document()
    digest = mcp_stack.pins_review_digest(document)
    assert len(digest) == 64 and digest == digest.lower()
    reordered = json.loads(json.dumps(document, sort_keys=False))
    reordered["servers"]["github"] = dict(reversed(list(reordered["servers"]["github"].items())))
    assert mcp_stack.pins_review_digest(reordered) == digest
    changes = [
        lambda d: d.update(v=2),
        lambda d: d.update(stack="s-2"),
        lambda d: d["servers"]["github"]["identity"].update(upstream="v"),
        lambda d: d["servers"]["github"]["identity"].update(token_url="t"),
        lambda d: d["servers"]["github"]["pinned"].update(a="sha256:ab"),
        lambda d: d["servers"]["github"]["accept"].update(b="sha256:bc"),
        lambda d: d["servers"]["github"]["drop"].append("d"),
        lambda d: d["servers"].update(other={}),
    ]
    for change in changes:
        changed = copy.deepcopy(document)
        change(changed)
        assert mcp_stack.pins_review_digest(changed) != digest


def test_tool_hash_tells_1_from_1_0_and_big_integers_apart():
    assert mcp_stack.tool_hash({"name": "a", "x": 1}) != mcp_stack.tool_hash(
        {"name": "a", "x": 1.0}
    )
    big = 2**53 + 1
    assert mcp_stack.tool_hash({"name": "a", "x": big}) != mcp_stack.tool_hash(
        {"name": "a", "x": big - 1}
    )


# -- services pins (the read) ---------------------------------------------------------------------


def test_pins_review_shows_diff_drop_and_previous(project, pinned):  # noqa: F811
    docker, result = pinned
    docker.upstream["github"] = [tool("list_issues", "changed: exfiltrate")]  # create_issue gone
    before = snapshot(project, result)
    probes = len(docker.probes)
    review = read_review(project, docker)
    assert snapshot(project, result) == before  # nothing written, no token minted
    assert len(docker.probes) == probes + 1
    server = review["servers"]["github"]
    assert server["state"] == "drift" and server["origin"] == "project"
    changed = server["changed"]["list_issues"]
    assert changed["definition"] == tool("list_issues", "changed: exfiltrate")
    assert changed["previous"] == tool("list_issues")
    assert changed["pinned"] == mcp_stack.tool_hash(tool("list_issues"))
    assert server["drop"] == ["create_issue"]
    assert server["missing"] == ["create_issue", "delete_repo"]
    document = review["review"]["servers"]["github"]
    assert document["accept"] == {"list_issues": changed["sha256"]}
    assert document["drop"] == ["create_issue"]
    assert set(document["pinned"]) == {"list_issues", "create_issue"}
    assert review["digest"] == mcp_stack.pins_review_digest(review["review"])


def test_previous_is_null_when_the_pins_file_does_not_hash(project, pinned):  # noqa: F811
    docker, result = pinned
    path = stack_dir(result) / "pins" / "github" / "pins.json"
    data = json.loads(path.read_text())
    data["tools"]["list_issues"]["definition"]["description"] = "tampered"
    path.chmod(0o644)
    path.write_text(json.dumps(data))
    docker.upstream["github"] = [tool("list_issues", "new"), tool("create_issue")]
    review = read_review(project, docker)
    assert review["servers"]["github"]["changed"]["list_issues"]["previous"] is None
    text = plan_mod.pins_review_text(review)
    assert "previous definition unavailable" in text


def test_all_served_unchanged_is_ok_and_unpinned_missing_is_not_bound(project, pinned):  # noqa: F811
    docker, _ = pinned
    review = read_review(project, docker)
    server = review["servers"]["github"]
    assert (
        server["state"] == "ok" and server["drop"] == [] and server["missing"] == ["delete_repo"]
    )
    assert review["review"]["servers"]["github"]["accept"] == {}
    # delete_repo was never pinned or served: whether or not the probe reports it missing, the
    # digest is the same (nothing changes for it).
    engine = Engine(project, docker=docker)
    try:
        plan = engine.plan("plain")
        review_now, probes = engine._review(plan, create=False)
        lock = engine._lock_state[0]
    finally:
        engine.close()
    assert mcp_stack.pins_review_digest(review_now.document) == review["digest"]
    probes["github"] = {**probes["github"], "missing": []}
    other = mcp_stack.pins_review(review["stack"], plan.locked.mcp, lock, probes)
    assert other.details["github"]["missing"] == []
    assert mcp_stack.pins_review_digest(other.document) == review["digest"]
    text = plan_mod.pins_review_text(review)
    assert "delete_repo  (not pinned)" in text and "nothing to repin" in text


def test_a_stack_without_mcp_has_no_servers(tmp_path, monkeypatch, capsys):
    from test_services_engine import STACK

    from agentbreeder import registry
    from agentbreeder.operator_config import CONFIG_ENV
    from agentbreeder.services.state import ROOT_ENV

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", "echo-canary-0000")
    root = write_project(tmp_path / "project", stack=copy.deepcopy(STACK))
    docker = RelayDocker()
    monkeypatch.setattr("agentbreeder.services.engine.DockerCLI", lambda: docker)
    assert cli.main(["services", "pins", str(root), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["servers"] == {} and out["review"]["servers"] == {}
    assert out["digest"] == mcp_stack.pins_review_digest(out["review"])
    assert out["schema_version"] == 1


def test_a_relay_that_is_not_running_refuses_the_read(project, pinned):  # noqa: F811
    docker, _ = pinned
    docker.running["mcp-github"]["state"] = "exited"
    with pytest.raises(ServiceError, match="MCP_RELAY_NOT_RUNNING"):
        read_review(project, docker)
    del docker.running["mcp-github"]
    with pytest.raises(ServiceError, match="MCP_RELAY_NOT_RUNNING"):
        read_review(project, docker)


def test_first_pins_need_up(project):  # noqa: F811
    docker = RelayDocker()
    # The relay runs but nothing was pinned (the pin prompt was declined).
    with pytest.raises(ServiceError, match="MCP_PINS_NOT_CONFIRMED"):
        run_up(project, docker, pins=False)
    with pytest.raises(ServiceError, match="MCP_PINS_NEED_UP"):
        read_review(project, docker)


def test_a_pending_identity_change_needs_up(project, pinned):  # noqa: F811
    docker, _ = pinned
    data = json.loads((project / "stack.lock").read_text())
    data["mcp"]["github"]["upstream"] = "https://other.example:443"
    (project / "stack.lock").write_text(json.dumps(data))
    before = (project / "stack.lock").read_bytes()
    with pytest.raises(ServiceError, match="MCP_PINS_NEED_UP"):
        read_review(project, docker)
    assert (project / "stack.lock").read_bytes() == before  # the pending change isn't saved
    docker.probe_error = "UPSTREAM_ERROR"  # a probe failure on an unpinned server: same code
    with pytest.raises(ServiceError, match="MCP_PINS_NEED_UP"):
        read_review(project, docker)


def two_servers(project):
    write_mcp(project, name="tracker")
    data = stack()
    data["mcp"]["tracker"] = {"ref": "mcp/tracker@0.1.0"}
    data["environment"]["agents"][AGENT]["mcp"]["tracker"] = ["list_issues"]
    write_project(project, stack=data)


def test_a_never_pinned_server_with_nothing_served_is_not_need_up(project):  # noqa: F811
    two_servers(project)
    docker = RelayDocker()
    docker.upstream["tracker"] = []
    result, _ = run_up(project, docker, allow=("github", "tracker"))
    docker.upstream["github"] = [tool("list_issues", "v2"), tool("create_issue")]
    review = read_review(project, docker)
    assert review["review"]["servers"]["tracker"] == {
        "identity": review["review"]["servers"]["tracker"]["identity"],
        "pinned": {},
        "accept": {},
        "drop": [],
    }
    result, _ = run_up(
        project, docker, repin=True, pins=False, allow=("github", "tracker"),
        expect=review["digest"],
    )  # fmt: skip
    assert result["repinned"] == {"github": {"accept": ["list_issues"], "drop": []}}


def test_a_probe_failure_on_one_of_two_servers_fails_the_read(project):  # noqa: F811
    two_servers(project)
    docker = RelayDocker()
    docker.upstream["tracker"] = [tool("list_issues")]
    run_up(project, docker, allow=("github", "tracker"))

    class SecondFails(RelayDocker):
        def relay_probe(self, state, service, admin_token):
            if service == "mcp-tracker":
                return json.dumps({"error": "UPSTREAM_ERROR"})
            return super().relay_probe(state, service, admin_token)

    failing = SecondFails()
    failing.running = docker.running
    failing.upstream = docker.upstream
    with pytest.raises(ServiceError, match="MCP_PROBE_FAILED"):
        read_review(project, failing)


def test_the_read_never_mints_an_admin_token(project, pinned):  # noqa: F811
    docker, result = pinned
    token = stack_dir(result) / "service-secrets" / "mcp-github" / "admin-token"
    token.unlink()
    with pytest.raises(ServiceError, match="MCP_ADMIN_TOKEN_MISSING"):
        read_review(project, docker)
    assert not token.exists()


# -- CLI ------------------------------------------------------------------------------------------


@pytest.fixture
def cli_docker(monkeypatch, pinned):
    docker, _ = pinned
    monkeypatch.setattr("agentbreeder.services.engine.DockerCLI", lambda: docker)
    return docker


def test_cli_pins_json_and_text(project, cli_docker, capsys):  # noqa: F811
    cli_docker.upstream["github"] = [tool("list_issues", "v2")]
    assert cli.main(["services", "pins", str(project), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema_version"] == 1 and len(out["digest"]) == 64
    assert set(out["servers"]["github"]) == {
        "origin", "state", "new", "changed", "missing", "drop", "same",
    }  # fmt: skip
    assert cli.main(["services", "pins", str(project)]) == 0
    text = capsys.readouterr().out
    assert "?" not in text.splitlines()[0]  # read mode asks nothing
    assert "create_issue  (this pin is removed)" in text
    assert "delete_repo  (not pinned)" in text
    assert text.count("this pin is removed") == 1
    assert f"Review digest: {out['digest']}" in text
    assert f"--repin --expect-pins {out['digest']}" in text


def test_cli_pins_busy(project, cli_docker, capsys):  # noqa: F811
    engine = Engine(project, docker=cli_docker)
    identifier = engine.state.id
    engine.close()
    with services_lock(stacks_root(), identifier):
        assert cli.main(["services", "pins", str(project), "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "SERVICES_BUSY"


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        (["--repin", "--expect-pins", "abc"], "PINS_EXPECT_INVALID"),
        (["--repin", "--expect-pins", "A" * 64], "PINS_EXPECT_INVALID"),
        (["--repin", "--expect-pins", "a" * 63], "PINS_EXPECT_INVALID"),
        (["--repin", "--expect-pins", "a" * 65], "PINS_EXPECT_INVALID"),
        (["--expect-pins", "a" * 64], "PINS_EXPECT_NEEDS_REPIN"),
        (["--locked", "--repin", "--expect-pins", "a" * 64], "PINS_EXPECT_NEEDS_REPIN"),
    ],
)
def test_cli_expect_pins_flag_refusals(project, cli_docker, capsys, flags, code):  # noqa: F811
    argv = ["services", "up", str(project), "--environment", "dev", "--json", *flags]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == code
    assert captured.err.startswith(f"[{code}]")


def test_cli_review_then_apply_without_a_tty(project, cli_docker, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        plan_mod, "pins_text", lambda report: pytest.fail("confirm_pins was called")
    )
    cli_docker.upstream["github"] = [tool("list_issues", "v2"), tool("create_issue")]
    assert cli.main(["services", "pins", str(project), "--json"]) == 0
    digest = json.loads(capsys.readouterr().out)["digest"]
    argv = ["services", "up", str(project), "--environment", "dev", "--yes"]
    argv += ["--allow-project-mcp", "github", "--repin", "--expect-pins", digest]
    assert cli.main([*argv, "--json"]) == 0, capsys.readouterr().err
    out = json.loads(capsys.readouterr().out)
    assert out["repinned"] == {"github": {"accept": ["list_issues"], "drop": []}}
    assert out["pins_digest"] == digest
    assert out["mcp"]["github"]["state"] == "ok"
    # The same digest again: the base pins changed, so it no longer applies.
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "[PINS_CHANGED]" in err and "The rest of `up` ran" in err


def test_cli_human_output_names_the_repin(project, cli_docker, capsys):  # noqa: F811
    cli_docker.upstream["github"] = [tool("list_issues")]
    assert cli.main(["services", "pins", str(project), "--json"]) == 0
    digest = json.loads(capsys.readouterr().out)["digest"]
    argv = ["services", "up", str(project), "--environment", "dev", "--yes"]
    argv += ["--allow-project-mcp", "github", "--repin", "--expect-pins", digest]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "mcp.github       repinned: dropped create_issue" in out
    assert f"Pins digest: {digest}" in out


def test_cli_yes_without_expect_pins_still_refuses_a_repin(
    project, cli_docker, capsys, monkeypatch
):  # noqa: E501,F811
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    cli_docker.upstream["github"] = [tool("list_issues", "v2"), tool("create_issue")]
    argv = ["services", "up", str(project), "--environment", "dev", "--yes", "--json"]
    argv += ["--allow-project-mcp", "github", "--repin"]
    assert cli.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "MCP_PINS_NOT_CONFIRMED"


# -- up --repin --expect-pins (the apply) ---------------------------------------------------------


def test_apply_pins_exactly_the_reviewed_definitions(project, pinned):  # noqa: F811
    docker, result = pinned
    docker.upstream["github"] = [tool("list_issues", "v2")]
    review = read_review(project, docker)
    seen = []
    applied, _ = repin(project, docker, review["digest"], seen=seen)
    assert seen == []  # confirm_pins is never called
    assert applied["repinned"] == {"github": {"accept": ["list_issues"], "drop": ["create_issue"]}}
    assert applied["pins_digest"] == review["digest"]
    lock = json.loads((project / "stack.lock").read_text())["mcp"]["github"]["tools"]
    assert lock == {"list_issues": mcp_stack.tool_hash(tool("list_issues", "v2"))}
    pins = json.loads((stack_dir(result) / "pins" / "github" / "pins.json").read_text())
    assert pins["tools"]["list_issues"]["definition"] == tool("list_issues", "v2")
    assert set(pins["tools"]) == {"list_issues"}
    assert applied["mcp"]["github"] == {
        "state": "ok",
        "pinned": ["list_issues"],
        "changed": [],
        "missing": ["create_issue", "delete_repo"],
    }


def _changed_after_review(project, docker, result, change):
    docker.upstream["github"] = [tool("list_issues", "v2"), tool("create_issue")]
    review = read_review(project, docker)
    change(docker)
    before = snapshot(project, result)
    with pytest.raises(ServiceError, match="PINS_CHANGED"):
        repin(project, docker, review["digest"])
    return before, snapshot(project, result)


def test_a_field_changing_after_the_review_pins_nothing(project, pinned):  # noqa: F811
    docker, result = pinned

    def change(docker):
        changed = tool("list_issues", "v2")
        changed["inputSchema"]["required"] = ["repo"]
        docker.upstream["github"][0] = changed

    before, after = _changed_after_review(project, docker, result, change)
    assert after == before  # stack.lock and every pins file byte-identical


def test_a_tool_going_missing_after_the_review_pins_nothing(project, pinned):  # noqa: F811
    docker, result = pinned
    before, after = _changed_after_review(
        project, docker, result, lambda d: d.upstream.update(github=[tool("list_issues", "v2")])
    )
    assert after == before


def test_the_base_pins_changing_after_the_review_pins_nothing(project, pinned):  # noqa: F811
    docker, result = pinned

    def change(docker):
        data = json.loads((project / "stack.lock").read_text())
        data["mcp"]["github"]["tools"]["create_issue"] = "sha256:" + "0" * 64
        (project / "stack.lock").write_text(json.dumps(data))

    before, after = _changed_after_review(project, docker, result, change)
    # Phase 1 rewrites the pins file from the edited lock (as any `up` does); the repin
    # changes nothing more: stack.lock is byte-identical.
    assert after["stack.lock"] == before["stack.lock"]


def test_a_second_server_drifting_pins_neither(project):  # noqa: F811
    two_servers(project)
    docker = RelayDocker()
    docker.upstream["tracker"] = [tool("list_issues")]
    result, _ = run_up(project, docker, allow=("github", "tracker"))
    docker.upstream["github"] = [tool("list_issues", "v2"), tool("create_issue")]
    review = read_review(project, docker)
    docker.upstream["tracker"] = [tool("list_issues", "tracker v2")]
    before = snapshot(project, result)
    with pytest.raises(ServiceError, match="PINS_CHANGED"):
        run_up(
            project, docker, repin=True, pins=False, allow=("github", "tracker"),
            expect=review["digest"],
        )  # fmt: skip
    assert snapshot(project, result) == before  # github isn't pinned either


def test_an_empty_pinned_with_a_hand_made_digest_needs_up(project):  # noqa: F811
    docker = RelayDocker()
    with pytest.raises(ServiceError, match="MCP_PINS_NOT_CONFIRMED"):
        run_up(project, docker, pins=False)
    engine = Engine(project, docker=docker)
    try:
        plan = engine.plan("plain")
        review, _ = engine._review(plan, create=False)
    finally:
        engine.close()
    assert review.need_up == ["github"]
    with pytest.raises(ServiceError, match="MCP_PINS_NEED_UP"):
        repin(project, docker, mcp_stack.pins_review_digest(review.document))
    assert json.loads((project / "stack.lock").read_text())["mcp"]["github"]["tools"] == {}


def test_nothing_to_change_writes_no_lock(project, pinned):  # noqa: F811
    docker, _ = pinned
    review = read_review(project, docker)
    lock = project / "stack.lock"
    before = (lock.read_bytes(), lock.stat().st_mtime_ns)
    applied, _ = repin(project, docker, review["digest"])
    assert applied["repinned"] == {} and applied["pins_digest"] == review["digest"]
    assert (lock.read_bytes(), lock.stat().st_mtime_ns) == before


def test_update_with_an_identity_change_is_refused(project, pinned):  # noqa: F811
    docker, _ = pinned
    review = read_review(project, docker)
    data = json.loads((project / "stack.lock").read_text())
    data["mcp"]["github"]["upstream"] = "https://other.example:443"
    (project / "stack.lock").write_text(json.dumps(data))
    with pytest.raises(ServiceError, match="MCP_PINS_NEED_UP"):
        repin(project, docker, review["digest"], mode="update")


# -- display --------------------------------------------------------------------------------------


def test_a_one_character_change_in_a_long_description_is_a_small_hunk():
    long = "x" * 50_000
    before = {"name": "a", "description": long}
    after = {"name": "a", "description": long[:25_000] + "y" + long[25_001:]}
    lines = plan_mod.review_diff(before, after)
    changed = [line for line in lines if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert len(changed) == 2
    assert all(len(line) <= plan_mod.WRAP + 2 for line in plan_mod.review_lines(after))
    assert any(line.endswith(plan_mod.WRAP_MARK) for line in plan_mod.review_lines(after))


def test_wrapping_never_cuts_an_escape_and_a_real_marker_shows_escaped():
    definition = {"name": "a", "description": ("é" * 40) + "↩" + ('"' * 40)}
    lines = plan_mod.review_lines(definition)
    joined = "".join(line.removesuffix(plan_mod.WRAP_MARK) for line in lines)
    assert joined == "".join(plan_mod._definition(definition))
    assert "\\u21a9" in joined and "↩" not in joined
    for line in lines:
        body = line.removesuffix(plan_mod.WRAP_MARK)
        assert not body.endswith("\\") or body.endswith("\\\\")


def test_a_huge_definition_shows_diff_too_large():
    before = {"name": "a", "enum": list(range(2100))}
    after = {"name": "a", "enum": list(range(2101))}
    lines = plan_mod.review_diff(before, after)
    assert lines[0].startswith("(diff too large")
    assert len(lines) == len(plan_mod.review_lines(after)) + 1


def test_the_engine_refuses_expect_pins_without_repin_or_with_locked(project, pinned):  # noqa: F811
    docker, _ = pinned
    before = (project / "stack.lock").read_bytes()
    with pytest.raises(ServiceError, match="PINS_EXPECT_NEEDS_REPIN"):
        run_up(project, docker, expect="a" * 64)
    with pytest.raises(ServiceError, match="PINS_EXPECT_NEEDS_REPIN"):
        run_up(project, docker, repin=True, mode="locked", expect="a" * 64)
    assert (project / "stack.lock").read_bytes() == before


def test_a_probe_failure_on_apply_pins_nothing(project, pinned):  # noqa: F811
    docker, result = pinned
    docker.upstream["github"] = [tool("list_issues", "v2"), tool("create_issue")]
    review = read_review(project, docker)
    docker.probe_error = "UPSTREAM_ERROR"
    before = snapshot(project, result)
    with pytest.raises(ServiceError, match="MCP_PROBE_FAILED"):
        repin(project, docker, review["digest"])
    assert snapshot(project, result) == before


def test_a_tool_both_served_and_reported_missing_is_accepted_not_dropped():
    entry = mcp_stack.McpEntry("github", "mcp/github@0.1.0", None, "built-in", "mcp-github")
    entry.identity = lambda: {"ref": "r", "origin": "built-in", "upstream": "u", "token_url": None}

    class Lock:
        mcp = {"github": {"tools": {"a": "sha256:" + "0" * 64}}}

    definition = {"name": "a", "description": "new"}
    probe = {
        "tools": {"a": {"sha256": mcp_stack.tool_hash(definition), "definition": definition}},
        "missing": ["a"],
    }
    review = mcp_stack.pins_review("s", {"github": entry}, Lock(), {"github": probe})
    assert review.document["servers"]["github"]["drop"] == []
    assert list(review.document["servers"]["github"]["accept"]) == ["a"]


def test_tokens_keep_escapes_whole_and_survive_a_trailing_backslash():
    assert list(plan_mod._tokens('a\\u00e9\\"b\\')) == ["a", "\\u00e9", '\\"', "b", "\\"]
    assert list(plan_mod._tokens("\\u12")) == ["\\u", "1", "2"]


def test_cli_pins_text_on_an_ascii_terminal(project, cli_docker, capsys, monkeypatch):  # noqa: F811
    cli_docker.upstream["github"] = [tool("list_issues", "x" * 300), tool("create_issue")]
    printed = []

    def ascii_print(*values, **kwargs):
        text = " ".join(map(str, values))
        text.encode("ascii")  # raises UnicodeEncodeError like an ASCII stdout would
        printed.append(text)

    monkeypatch.setattr("builtins.print", ascii_print)
    assert cli.main(["services", "pins", str(project)]) == 0
    assert "\\u21a9" in printed[-1] and "Review digest:" in printed[-1]


def test_review_text_new_tools_empty_stack_and_nothing_repinned():
    definition = {"name": "b", "description": "new tool"}
    digest = "d" * 64
    result = {
        "stack": "s",
        "digest": digest,
        "review": {"servers": {"github": {"accept": {"b": mcp_stack.tool_hash(definition)}}}},
        "servers": {
            "github": {
                "origin": "built-in",
                "state": "ok",
                "new": {
                    "b": {"sha256": mcp_stack.tool_hash(definition), "definition": definition}
                },
                "changed": {},
                "missing": [],
                "drop": [],
                "same": [],
            }
        },
    }
    text = plan_mod.pins_review_text(result)
    assert "  new      b  " in text and '"description": "new tool"' in text
    empty = plan_mod.pins_review_text(result | {"servers": {}, "review": {"servers": {}}})
    assert "The stack has no mcp: entries." in empty
    lines = plan_mod.repinned_lines({"repinned": {}, "pins_digest": digest})
    assert lines == ["MCP pins: nothing to repin", f"Pins digest: {digest}"]
    assert plan_mod.repinned_lines({}) == []
