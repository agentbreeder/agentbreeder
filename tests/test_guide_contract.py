"""The `next --json` contract (#820, M4): models, generated schema and the documented examples."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbreeder import guide

ROOT = Path(__file__).parents[1]
DOC = ROOT / "docs/next.md"
IMAGE = "sha256:" + "4f" * 32
PROD = "3f2a9c1d0b8e4a7f9c6d5e4f3a2b1c0d"


def _examples() -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)\n```", DOC.read_text(encoding="utf-8"), re.S)
    return [json.loads(block) for block in blocks]


def _doc_row(step: str) -> str:
    return next(
        line
        for line in DOC.read_text(encoding="utf-8").splitlines()
        if re.match(rf"\| `{step}` \| (yes|no) \|", line)
    )


def test_schema_doc_in_sync():
    schema = json.loads((ROOT / "docs/next.schema.json").read_text(encoding="utf-8"))
    assert schema == guide.json_schema(), (
        "regenerate docs/next.schema.json from agentbreeder.guide.json_schema()"
    )


def test_published_schema_is_open_but_models_closed():
    text = (ROOT / "docs/next.schema.json").read_text(encoding="utf-8")
    assert "additionalProperties" not in text
    data = _examples()[0]
    data["later_field"] = 1
    with pytest.raises(ValidationError):
        guide.Guide.model_validate(data)


def test_documented_examples_validate():
    examples = _examples()
    assert len(examples) == 3
    for example in examples:
        guide.Guide.model_validate(example)


def test_every_field_is_present_in_the_examples():
    # One shape: no field is omitted, so the examples carry every field of every model they use.
    def walk(model, value):
        if isinstance(value, dict) and hasattr(model, "model_fields"):
            assert set(value) == set(model.model_fields), (model.__name__, set(value))

    for example in _examples():
        walk(guide.Guide, example)
        for step in example["steps"]:
            for name, model in [
                ("reason", guide.Reason),
                ("fix", guide.Fix),
            ]:
                walk(model, step[name])


def test_reason_and_action_tables_match_the_doc():
    assert tuple(guide.REASONS) == guide.STEP_ORDER == tuple(guide.ACTIONS)
    for step, codes in guide.REASONS.items():
        row = _doc_row(step)
        for code in codes:
            bold = f"**`{code}`**" in row
            assert bold or f"`{code}`" in row, (step, code)
            assert bold == (code in guide.BLOCKING), (step, code)
        actions_cell = row.rsplit("|", 2)[-2]
        for action in guide.ACTIONS[step]:
            assert f"`{action}`" in actions_cell, (step, action)
    text = DOC.read_text(encoding="utf-8")
    for code in guide.CONDITION_CODES:
        assert f"| `{code}` |" in text, code
    for url in guide.URLS:
        assert url in text


def test_titles_documented_in_examples():
    seen = {s["id"]: s["title"] for e in _examples() for s in e["steps"]}
    for step, title in seen.items():
        assert guide.TITLES[step] == title


def _base() -> dict:
    return copy.deepcopy(_examples()[0])


def _released() -> dict:
    return copy.deepcopy(_examples()[1])


def _step(data: dict, step_id: str) -> dict:
    return next(step for step in data["steps"] if step["id"] == step_id)


def _invalid(data: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        guide.Guide.model_validate(data)


def _fix(action=None, command="agentbreeder x", note=None, url=None) -> dict:
    return {"action": action, "command": command, "note": note, "url": url}


def _all_done(data: dict) -> dict:
    """The released example with Docker back: everything done."""
    _step(data, "prerequisites").update(state="done", reason=None, fix=None)
    _step(data, "services").update(
        state="done",
        reason=None,
        fix=None,
        data={"services": [], "grant_recorded": True, "environment_written": True},
    )
    data.update(conditions=[], complete=True, next=None)
    return data


def test_all_done_is_complete():
    guide.Guide.model_validate(_all_done(_released()))


def test_steps_must_be_every_id_in_order():
    data = _base()
    data["steps"].reverse()
    _invalid(data, "in order")
    data = _base()
    data["steps"] = [s for s in data["steps"] if s["id"] != "keys"]
    _invalid(data, "in order")


def _kubernetes(state="next", code="NOT_PROMOTED", action="promote") -> dict:
    return {
        "id": "kubernetes",
        "title": "Kubernetes",
        "required": False,
        "state": state,
        "reason": {"code": code, "message": "Production is not on the cluster"},
        "fix": _fix(action),
        "data": {
            "cluster": "agentbreeder",
            "cluster_up": True,
            "cluster_configured": True,
            "deployment": None,
            "promoted_image": None,
            "production_image": IMAGE,
        },
    }


def test_kubernetes_is_optional_and_next_only_when_required_done():
    data = _all_done(_released())
    data["next"] = "kubernetes"
    data["steps"].append(_kubernetes())
    guide.Guide.model_validate(data)
    # A registry image on the cluster is a valid image.
    _step(data, "kubernetes")["data"]["promoted_image"] = "localhost:5001/hello@" + IMAGE
    guide.Guide.model_validate(data)
    # With a required step not done, kubernetes must wait.
    _step(data, "build").update(
        state="next",
        reason={"code": "BUILD_STALE", "message": "agent.yaml changed"},
        fix=_fix("build"),
    )
    _step(data, "release").update(
        state="todo", reason={"code": "PRODUCTION_STALE", "message": "x"}, fix=_fix("release")
    )
    _step(data, "try").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    data["complete"], data["next"] = False, "build"
    _invalid(data, "kubernetes: state next")
    _step(data, "kubernetes")["state"] = "todo"
    guide.Guide.model_validate(data)


def test_kubernetes_approval_pending_is_blocked():
    data = _all_done(_released())
    data["steps"].append(_kubernetes("blocked", "APPROVAL_PENDING", None))
    data["conditions"] = [
        {
            "code": "STEP_BLOCKED",
            "message": "x",
            "step": "kubernetes",
            "deployment": None,
            "service": None,
        }
    ]
    guide.Guide.model_validate(data)
    _step(data, "kubernetes")["fix"]["action"] = "promote"
    _invalid(data, "does not belong here|blocked step has no console action")


def test_reason_given_exactly_when_not_done():
    data = _base()
    _step(data, "keys")["reason"] = {"code": "KEYS_MISSING", "message": "x"}
    _invalid(data, "reason is given exactly")
    data = _base()
    _step(data, "services")["reason"] = None
    _invalid(data, "reason is given exactly")


def test_unknown_reason_code_refused():
    data = _base()
    _step(data, "services")["reason"]["code"] = "NOT_BUILT"
    _invalid(data, "unknown reason")


def test_not_checked_rules():
    data = _base()
    _step(data, "try")["data"] = {"deployment": None, "endpoint": None}
    _invalid(data, "data is null exactly when NOT_CHECKED")
    data = _base()
    _step(data, "build").update(data=None)
    _invalid(data, "data is null only when")
    data = copy.deepcopy(_examples()[2])
    _step(data, "project").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    _invalid(data, "expected next")


def test_complete_and_next_must_match_states():
    data = _base()
    data["complete"] = True
    _invalid(data, "complete must be")
    data = _base()
    data["next"] = "build"
    _invalid(data, "next must name")
    data = _base()
    _step(data, "build")["state"] = "next"
    _invalid(data, "build: state next")
    data = _base()
    _step(data, "services")["state"] = "todo"
    _invalid(data, "services: state todo")


def test_blocked_follows_the_code_not_the_producer():
    # SERVICES_DOWN is not blocking, so the first step not done must be next.
    data = _base()
    _step(data, "services")["state"] = "blocked"
    _step(data, "services")["fix"]["action"] = None
    data["next"] = None
    _invalid(data, "expected next")


def test_lock_stale_is_next_without_a_console_action():
    data = _all_done(_released())
    _step(data, "build").update(
        state="next",
        reason={"code": "LOCK_STALE", "message": "agent.lock does not match agent.yaml"},
        fix=_fix(None, "agentbreeder build agent.yaml --stack . --environment dev"),
    )
    _step(data, "release").update(
        state="todo", reason={"code": "PRODUCTION_STALE", "message": "x"}, fix=_fix("release")
    )
    _step(data, "try").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    data.update(complete=False, next="build")
    guide.Guide.model_validate(data)


def test_gate_failed_and_migration():
    data = _all_done(_released())
    _step(data, "release")["data"]["gate_failed"] = True
    _step(data, "release").update(
        state="next",
        reason={"code": "RELEASE_GATE_FAILED", "message": "1/2 cases passed"},
        fix=_fix("release", "agentbreeder logs --failed hello", "Edit the prompt, publish, build"),
    )
    _step(data, "try").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    data.update(complete=False, next="release")
    guide.Guide.model_validate(data)
    # State that needs `migrate`: release is blocked with no data, try cannot be checked.
    _step(data, "release").update(
        state="blocked",
        reason={"code": "STATE_NEEDS_MIGRATION", "message": "run migrate"},
        fix=_fix(None, "agentbreeder migrate --stack . --environment dev"),
        data=None,
    )
    data["next"] = None
    data["conditions"] = [
        {
            "code": "STEP_BLOCKED",
            "message": "x",
            "step": "release",
            "deployment": None,
            "service": None,
        }
    ]
    guide.Guide.model_validate(data)
    _step(data, "release")["fix"]["action"] = "release"
    _invalid(data, "does not belong here")


def test_production_disabled_follows_the_blocker():
    data = _all_done(_released())
    release = _step(data, "release")
    release["data"]["production"]["state"] = "disabled"
    release["data"]["blocker"] = True
    release.update(
        state="blocked",
        reason={"code": "PRODUCTION_DISABLED", "message": "The kill switch disabled production"},
        fix=_fix(None, "agentbreeder enable hello"),
    )
    _step(data, "try").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    data.update(complete=False, next=None)
    data["conditions"] = [
        {
            "code": "STEP_BLOCKED",
            "message": "x",
            "step": "release",
            "deployment": None,
            "service": None,
        }
    ]
    guide.Guide.model_validate(data)
    # A blocker with any other reported reason is refused, so no Release button hides it.
    release.update(
        state="next", reason={"code": "NOT_RELEASED", "message": "x"}, fix=_fix("release")
    )
    data.update(next="release", conditions=[])
    _invalid(data, "PRODUCTION_DISABLED exactly")
    # After `enable` (blocker gone, row still disabled): not ready, and Release is offered.
    release["data"]["blocker"] = False
    release["reason"]["code"] = "PRODUCTION_NOT_READY"
    guide.Guide.model_validate(data)
    for state in ("removing", "failed", "pending", "starting"):
        release["data"]["production"]["state"] = state
        guide.Guide.model_validate(data)
    # A done release cannot carry a blocker.
    done = _all_done(_released())
    _step(done, "release")["data"]["blocker"] = True
    _invalid(done, "PRODUCTION_DISABLED exactly|no blocker")


def test_stale_production_stays_released_and_not_ungated():
    data = _all_done(_released())
    _step(data, "build")["data"]["image"] = "sha256:" + "ab" * 32
    _step(data, "release").update(
        state="next",
        reason={"code": "PRODUCTION_STALE", "message": "A newer build is not released"},
        fix=_fix("release"),
    )
    _step(data, "try").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    data.update(complete=False, next="release")
    guide.Guide.model_validate(data)  # released stays true, so no PRODUCTION_UNGATED
    for code in ("RELEASE_EVALSET_CHANGED", "RELEASE_THRESHOLD_LOWERED"):
        _step(data, "release")["reason"]["code"] = code
        guide.Guide.model_validate(data)


def test_services_unreadable_is_blocked_with_docker_up():
    data = _base()
    _step(data, "services").update(
        state="blocked",
        reason={"code": "SERVICES_UNREADABLE", "message": "docker compose ps timed out"},
        fix=_fix(None, None, "Check Docker Compose v2, then run next again"),
        data={"services": [], "grant_recorded": True, "environment_written": True},
    )
    data["next"] = None
    data["conditions"] = [
        {
            "code": "STEP_BLOCKED",
            "message": "x",
            "step": "services",
            "deployment": None,
            "service": None,
        }
    ]
    guide.Guide.model_validate(data)


def test_code_action_mapping():
    data = _all_done(_released())
    data["next"] = "kubernetes"
    data["steps"].append(_kubernetes("next", "CLUSTER_DOWN", "promote"))
    _invalid(data, "does not belong here")
    _step(data, "kubernetes")["fix"]["action"] = "cluster-up"
    guide.Guide.model_validate(data)
    _step(data, "kubernetes")["reason"]["code"] = "GRANT_PENDING"
    _invalid(data, "does not belong here")
    _step(data, "kubernetes")["fix"]["action"] = "services-up"
    guide.Guide.model_validate(data)
    data = _base()
    _step(data, "build")["reason"]["code"] = "REF_UNPINNED"
    _invalid(data, "does not belong here")


def test_condition_fields_required_and_ungated_from_data():
    data = _base()
    data["conditions"] = [
        {
            "code": "SERVICES_UNHEALTHY",
            "message": "x",
            "step": None,
            "deployment": None,
            "service": None,
        }
    ]
    _invalid(data, "SERVICES_UNHEALTHY needs service")
    data["conditions"][0]["service"] = "postgres"
    guide.Guide.model_validate(data)
    # A hand-deployed production that failed the gate is still ungated.
    data = _ungated(_released())
    release = _step(data, "release")
    release["data"]["gate_failed"] = True
    release["reason"]["code"] = "RELEASE_GATE_FAILED"
    guide.Guide.model_validate(data)
    data["conditions"][0]["deployment"] = "0" * 32
    _invalid(data, "PRODUCTION_UNGATED names production")


def test_release_done_with_build_not_checked_is_a_validation_error():
    data = _all_done(_released())
    _step(data, "build").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    data.update(complete=False)
    with pytest.raises(ValidationError):
        guide.Guide.model_validate(data)


def test_blocked_needs_step_blocked_condition():
    data = copy.deepcopy(_examples()[1])
    data["conditions"] = []
    _invalid(data, "STEP_BLOCKED")
    data = _base()
    data["conditions"] = [
        {
            "code": "STEP_BLOCKED",
            "message": "x",
            "step": "services",
            "deployment": None,
            "service": None,
        }
    ]
    _invalid(data, "STEP_BLOCKED")
    data = _base()
    data["conditions"] = [
        {"code": "SOMETHING", "message": "x", "step": None, "deployment": None, "service": None}
    ]
    _invalid(data, "unknown condition")


@pytest.mark.parametrize(
    ("step", "change", "match"),
    [
        ("keys", {"required": False}, "required must be True"),
        ("keys", {"title": "Done - nothing to do"}, "title must be"),
        ("services", {"fix": _fix("promote")}, "does not belong"),
        ("keys", {"fix": _fix("set-keys")}, "only try carries a fix"),
        ("services", {"fix": None}, "needs a fix"),
        ("services", {"fix": _fix(None, None, None)}, "needs an action, a command or a note"),
    ],
)
def test_step_rules(step, change, match):
    data = _base()
    _step(data, step).update(change)
    _invalid(data, match)


def test_keys_sets_must_add_up():
    data = _base()
    _step(data, "keys")["data"] = {
        "required": ["GEMINI_API_KEY"],
        "present": [],
        "stored": [],
        "missing": [],
    }
    _invalid(data, "missing must be")
    _step(data, "keys")["data"] = {
        "required": ["GEMINI_API_KEY"],
        "present": ["GEMINI_API_KEY", "AWS_SECRET_ACCESS_KEY"],
        "stored": [],
        "missing": [],
    }
    _invalid(data, "subsets|required key names")


def test_paths_must_be_inside_project():
    data = _base()
    _step(data, "project")["data"]["agent_file"] = "/etc/passwd"
    _invalid(data, "inside project")
    data = _base()
    _step(data, "project")["data"]["agent_file"] = "/home/me/agentbreeder-projects/hello/../x"
    _invalid(data, "inside project")


def test_try_must_match_production():
    data = _released()
    _step(data, "try")["data"]["endpoint"] = "http://127.0.0.1:1234"
    _invalid(data, "production's deployment")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), 2),
        (("schema_version",), True),
        (("complete",), 0),
        (("project",), ""),
        (("project",), "../../etc"),
        (("environment",), "dev --yes; x"),
        (("environment",), ""),
        (("steps", 3, "fix", "action"), "rm -rf"),
        (("steps", 3, "fix", "url"), "https://accounts.google.com@evil.example/x"),
        (("steps", 3, "fix", "command"), "agentbreeder build\nrm -rf ~"),
        (("steps", 3, "fix", "command"), "agentbreeder ‮build"),
        (("steps", 3, "reason", "code"), "lower_case"),
        (("steps", 3, "reason", "message"), "x" * 301),
        (("steps", 3, "reason", "message"), ""),
        (("steps", 3, "reason", "message"), "zero​width"),
        (("steps", 3, "reason", "message"), "osc \x1b]52;c;x\x07"),
        (("steps", 2, "data", "missing"), ["gemini key"]),
        (("steps", 3, "data", "services", 0, "state"), "starting"),
        (("steps", 3, "data", "services", 0, "name"), "Bad Name"),
        (("steps", 4, "data", "image"), "sha256:short"),
        (("steps", 1, "data", "agent"), "Hello"),
        (("steps", 1, "data", "newer", 0, "line"), 0),
        (("steps", 1, "data", "newer", 0, "ref"), "prompt/x"),
        (
            ("steps", 0, "data", "checks"),
            [{"name": "x", "passed": True, "required": True, "message": "x"}] * 51,
        ),
    ],
)
def test_field_constraints(path, value):
    data = _base()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        guide.Guide.model_validate(data)


@pytest.mark.parametrize(
    "state", ["created", "restarting", "paused", "exited", "removing", "dead"]
)
def test_compose_states_accepted(state):
    data = _base()
    _step(data, "services")["data"]["services"][0]["state"] = state
    guide.Guide.model_validate(data)


def _ungated(data: dict) -> dict:
    """Production deployed by hand from a registry image: no release evidence."""
    _all_done(data)
    release = _step(data, "release")
    release["data"]["production"]["image"] = "registry.example/hello:1@" + IMAGE
    release["data"]["released"] = False
    release.update(
        state="next",
        reason={"code": "NO_RELEASE_EVIDENCE", "message": "Production skipped the eval gate"},
        fix=_fix("release"),
    )
    _step(data, "try").update(
        state="todo", reason={"code": "NOT_CHECKED", "message": "x"}, fix=None, data=None
    )
    data.update(complete=False, next="release")
    data["conditions"] = [
        {
            "code": "PRODUCTION_UNGATED",
            "message": "Production runs an image no release promoted",
            "step": "release",
            "deployment": PROD,
            "service": None,
        }
    ]
    return data


def test_production_may_run_a_registry_image_and_is_ungated():
    data = _ungated(_released())
    guide.Guide.model_validate(data)
    data["conditions"] = []
    _invalid(data, "PRODUCTION_UNGATED")


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"released": False}, "release: done needs"),
        ({"gate_failed": True}, "release: done needs"),
        ({"production": None}, "release: done needs"),
    ],
)
def test_release_done_needs_evidence(change, match):
    data = _all_done(_released())
    _step(data, "release")["data"].update(change)
    _invalid(data, match)


def test_release_done_needs_ready_current_production():
    for field, value in (("state", "starting"), ("manifest_current", False)):
        data = _all_done(_released())
        _step(data, "release")["data"]["production"][field] = value
        _invalid(data, "release: done needs")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image", None),
        ("agent_yaml_current", False),
        ("lock_current", False),
        ("environment", "prod"),
    ],
)
def test_build_done_needs_evidence(field, value):
    data = _all_done(_released())
    _step(data, "build")["data"][field] = value
    _invalid(data, "build: done needs")


def test_try_endpoint_without_deployment_refused():
    data = _ungated(_released())
    _step(data, "try").update(reason={"code": "NOT_CHECKED", "message": "x"}, data=None, fix=None)
    guide.Guide.model_validate(data)
    released = _released()
    _step(released, "try")["data"] = {"deployment": None, "endpoint": "http://127.0.0.1:22"}
    _invalid(released, "production's deployment")


def test_not_checked_has_no_fix():
    data = _base()
    _step(data, "try")["fix"] = _fix("invoke")
    _invalid(data, "NOT_CHECKED steps are todo with no fix")


def test_keys_state_tied_to_missing():
    data = _base()
    _step(data, "keys")["data"].update(present=[], missing=["GEMINI_API_KEY"])
    _invalid(data, "KEYS_MISSING exactly")
    data = _base()
    _step(data, "keys")["data"].update(required=["GEMINI_API_KEY", "GEMINI_API_KEY"])
    _invalid(data, "must not repeat")


@pytest.mark.parametrize(
    ("project", "agent_file"),
    [
        ("//", "/etc/passwd"),
        ("/home/me/../../etc", "/home/me/../../etc/agent.yaml"),
        ("/home/me/hello", "/home/me/hello//etc/passwd"),
        ("/home/me/hello/", "/home/me/hello/agent.yaml"),
    ],
)
def test_paths_are_normalised(project, agent_file):
    data = _base()
    data["project"] = project
    _step(data, "project")["data"].update(agent_file=agent_file, stack_file=None, newer=[])
    _invalid(data, "project must be|inside project")


@pytest.mark.parametrize("char", ["\u00a0", "\u00ad", "\u3164", "\ufe0f", "\u2003", "\ue000"])
def test_invisible_and_odd_spaces_refused(char):
    data = _base()
    _step(data, "services")["fix"]["command"] = f"agentbreeder services{char}up"
    with pytest.raises(ValidationError):
        guide.Guide.model_validate(data)


@pytest.mark.parametrize(
    "endpoint", ["http://10.0.0.5:8080", "http://127.0.0.1:0", "http://127.0.0.1:65536"]
)
def test_endpoint_must_be_loopback_with_a_port(endpoint):
    data = _released()
    _step(data, "try")["data"]["endpoint"] = endpoint
    _step(data, "release")["data"]["production"]["endpoint"] = endpoint
    with pytest.raises(ValidationError):
        guide.Guide.model_validate(data)
