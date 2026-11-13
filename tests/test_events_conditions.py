"""M11 acceptance, condition half (#827): a crashed service raises a condition through the real
checks behind `next` (`guide_checks.compute`), and it clears once the service runs again. The
conditions `events --project` shows are those same ones, never stored."""

# Fixtures imported from test_next and test_release are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

from test_next import Calls, bring_up, probe, running, stacked, stacks  # noqa: F401
from test_release import project  # noqa: F401

from agentbreeder import events, guide_checks


def _unhealthy(result):
    return [(c.code, c.service) for c in result.conditions if c.code == "SERVICES_UNHEALTHY"]


def test_a_crashed_service_raises_a_condition_that_clears_after_restart(
    stacked, stacks, monkeypatch, tmp_path
):
    bring_up(stacked, stacks)
    crashed = running("postgres") | {"litellm": {"id": "l", "state": "exited", "health": ""}}
    result = guide_checks.compute(stacked, probe=probe(stacks, Calls(crashed)))
    assert _unhealthy(result) == [("SERVICES_UNHEALTHY", "litellm")]
    restarted = guide_checks.compute(
        stacked, probe=probe(stacks, Calls(running("postgres", "litellm")))
    )
    assert _unhealthy(restarted) == []
    # Nothing about the condition was stored.
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    assert events.read_recent() == []


def test_events_conditions_is_next_s_function(stacked, stacks, monkeypatch):
    bring_up(stacked, stacks)
    crashed = running("postgres") | {"litellm": {"id": "l", "state": "exited", "health": ""}}
    original = guide_checks.compute
    monkeypatch.setattr(
        guide_checks,
        "compute",
        lambda project, environment="dev": original(
            project, environment, probe=probe(stacks, Calls(crashed))
        ),
    )
    codes = [c["code"] for c in events.conditions(stacked)]
    assert "SERVICES_UNHEALTHY" in codes
