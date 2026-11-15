"""Opt-in real Docker run of the MCP repin review (#964): `services pins` probes a live relay,
and `up --repin --expect-pins` pins what was reviewed, or nothing when the upstream changed
after the review.

    AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_services_pins_docker.py --no-cov

The upstream is the mock MCP service (tests/fixtures/mcp/mock_server.py) on the relay's private
network; the test changes the `list_issues` description it serves by writing /tmp/tools.json.
"""

from __future__ import annotations

import json

import pytest
from test_mcp_relay_docker import docker
from test_mcp_relay_docker import project as project  # noqa: F401 (fixture)

from agentbreeder.services import mcp_stack
from agentbreeder.services.schema import ServiceError

pytestmark = pytest.mark.skipif(
    __import__("os").getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)


def serve(engine, description: str) -> list[dict]:
    tools = [
        {"name": "list_issues", "description": description, "inputSchema": {"type": "object"}},
        {"name": "delete_repo", "description": "Delete a repo", "inputSchema": {"type": "object"}},
    ]
    script = "import json, sys; open('/tmp/tools.json', 'w').write(sys.argv[1])"
    container = f"{engine.state.project}-browser-1"
    docker("exec", container, "python3", "-c", script, json.dumps(tools))
    return tools


def snapshot(engine) -> dict:
    pins = engine.state.directory / "pins" / "mock" / "pins.json"
    return {"lock": (engine.project / "stack.lock").read_bytes(), "pins": pins.read_bytes()}


def test_review_then_repin_through_a_live_relay(project):  # noqa: F811
    engine = project
    result = engine.up("dev", confirm=lambda grant: True, confirm_pins=lambda report: True)
    assert result["mcp"]["mock"]["state"] == "ok"

    review = engine.pins()
    assert review["servers"]["mock"]["state"] == "ok"
    assert review["review"]["servers"]["mock"]["accept"] == {}

    tools = serve(engine, "List issues (now also closes them)")
    review = engine.pins()
    changed = review["servers"]["mock"]["changed"]["list_issues"]
    assert changed["definition"] == tools[0]
    assert changed["previous"]["description"] == "List issues"
    assert review["digest"] == mcp_stack.pins_review_digest(review["review"])

    # A change between the review and the apply pins nothing.
    serve(engine, "List issues (now also deletes them)")
    before = snapshot(engine)
    with pytest.raises(ServiceError, match="PINS_CHANGED"):
        engine.up("dev", confirm=lambda grant: True, repin=True, expect_pins=review["digest"])
    assert snapshot(engine) == before

    # Review again and apply: exactly the reviewed definition is pinned.
    review = engine.pins()
    applied = engine.up(
        "dev", confirm=lambda grant: True, repin=True, expect_pins=review["digest"]
    )
    assert applied["repinned"] == {"mock": {"accept": ["list_issues"], "drop": []}}
    assert applied["pins_digest"] == review["digest"]
    lock = json.loads((engine.project / "stack.lock").read_text())["mcp"]["mock"]["tools"]
    expected = review["review"]["servers"]["mock"]["accept"]["list_issues"]
    assert lock["list_issues"] == expected
    assert engine.status()["mcp"]["mock"]["state"] == "ok"
