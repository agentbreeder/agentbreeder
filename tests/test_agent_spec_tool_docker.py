"""Opt-in real Docker run (#833): a project written by `new agent --from-spec` with one path tool,
alone and with one knowledge version, comes up, releases through its eval gate (a case that
needs the tool) and calls the tool from `invoke`.

    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_TEST_ENV_FILE=/path/to/keys.env \\
        pytest tests/test_agent_spec_tool_docker.py

The sandbox, the key handling and the leak check are `test_agent_spec_knowledge_docker`'s: a
fresh HOME with its own Docker context, a minimal environment, the key never printed, and every
output and sandbox file checked for it before the test ends. Teardown removes the deployments
and runs `services down --purge`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_agent_spec_knowledge_docker import CODE, QUESTION, Sandbox, _publish

from agentbreeder import registry

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1" or not os.getenv("AGENTBREEDER_TEST_ENV_FILE"),
    reason="Opt-in real Docker test with a Gemini key file",
)

QUOTE = "SQ-4417"  # only the tool knows it, so a reply carrying it called the tool
TOOL_MANIFEST = (
    "kind: tool\nname: shipping-quote\nversion: 1.0.0\n"
    "description: Shipping days and cost per region, from a fixed table.\n"
)
TOOL_YAML = """\
functions:
  - name: shipping_quote
    module: shipping_quote
    function: shipping_quote
    description: Shipping days, cost and quote code for a region.
    parameters:
      type: object
      properties:
        region: {type: string, enum: [north, south, east, west]}
      required: [region]
"""
TOOL_SOURCE = f"""\
TABLE = {{"north": (2, "4.50"), "south": (3, "5.25"), "east": (4, "6.00"), "west": (5, "7.75")}}


def shipping_quote(rt, region: str) -> str:
    days, cost = TABLE[region]
    code = f"{QUOTE}-{{region[0].upper()}}"
    return f"{{region}}: {{days}} business days, {{cost}} USD, quote code {{code}}"
"""
SHIP = (
    "I'm in the west region. How long does shipping take, what does it cost, and what is the "
    "quote code?"
)
PROMPT = (
    "You are shiphelper, the Acme help desk. For shipping days, cost or a quote code, always call "
    "the shipping_quote tool and give its quote code. Answer in one or two sentences."
)


def _publish_tool(sandbox: Sandbox, tmp_path: Path) -> None:
    source = tmp_path / "tool-source"
    (source / "src").mkdir(parents=True)
    (source / "manifest.yaml").write_text(TOOL_MANIFEST, encoding="utf-8")
    (source / "tool.yaml").write_text(TOOL_YAML, encoding="utf-8")
    (source / "src" / "shipping_quote.py").write_text(TOOL_SOURCE, encoding="utf-8")
    registry.publish(source, sandbox.registry)


def _spec(sandbox: Sandbox, knowledge: bool) -> Path:
    cases = [
        {
            "id": "ship-west",
            "turns": [SHIP],
            "expect": {"must_match": [QUOTE], "tools_include": ["shipping_quote"]},
            "reference": f"5 business days, 7.75 USD, quote code {QUOTE}-W.",
        }
    ]
    spec: dict = {
        "schema_version": 1,
        "name": "shiphelper",
        "model": "gemini",
        "max_model_calls": 4,
        "prompt": {"text": PROMPT},
        "evals": {"cases": cases, "threshold": 1.0, "retries": 1},
        "tools": ["tool/shipping-quote@1.0.0"],
    }
    if knowledge:
        spec["prompt"]["text"] += " For anything else, search the knowledge base first."
        cases.append(
            {
                "id": "engraving-code",
                "turns": [QUESTION],
                "expect": {"must_match": ["7Q3"]},
                "reference": f"The code is {CODE}, from the engraving article.",
            }
        )
        spec["knowledge"] = [{"ref": "knowledge/acme-help@1.0.0", "top_k": 5, "extra_searches": 1}]
    path = sandbox.home / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def sandbox(tmp_path):
    try:
        box = Sandbox(tmp_path, Path(os.environ["AGENTBREEDER_TEST_ENV_FILE"]))
        box.project = box.home / "shiphelper"
        yield box
    finally:
        (tmp_path / "home" / "keys.env").unlink(missing_ok=True)


@pytest.mark.parametrize("knowledge", [False, True], ids=["tool", "tool+knowledge"])
def test_a_generated_tool_agent_releases_and_calls_its_tool(tmp_path, sandbox, knowledge):
    _publish_tool(sandbox, tmp_path)
    if knowledge:
        _publish(sandbox, tmp_path)
    stack = ("--stack", str(sandbox.project), "--environment", "dev")
    agent = str(sandbox.project / "agent.yaml")
    up = False
    try:
        written = sandbox.json(
            "new", "agent", "shiphelper", "--from-spec", str(_spec(sandbox, knowledge))
        )
        parts = ["model", "prompt", "evals", *(["knowledge"] if knowledge else []), "tools"]
        assert written["combination"] == {"parts": parts, "verified": True}
        assert written["runs_code"] is True

        up = True
        sandbox.run(
            "services", "up", str(sandbox.project), "--environment", "dev",
            "--env-file", str(sandbox.keys), "--yes",
        )  # fmt: skip
        sandbox.run("build", agent, *stack, timeout=1200)
        sandbox.run("release", agent, *stack, timeout=1800)

        reply = sandbox.json("invoke", "shiphelper", SHIP, *stack, timeout=300)
        sandbox.show(reply)
        text = sandbox.mask(json.dumps(reply))
        assert f"{QUOTE}-W" in text and "shipping_quote" in text

        if knowledge:
            reply = sandbox.json("invoke", "shiphelper", QUESTION, *stack, timeout=300)
            sandbox.show(reply)
            assert "7Q3" in sandbox.mask(json.dumps(reply))
            status = sandbox.json(
                "knowledge", "status", "knowledge/acme-help@1.0.0",
                "--project", str(sandbox.project), "--environment", "dev",
            )  # fmt: skip
            sandbox.show(status)
            after = sandbox.mask(json.dumps(status))
            assert '"fresh"' in after and '"table_dimensions": 768' in after
    finally:
        try:
            if up:
                try:  # a failed listing must not skip the purge below
                    listed = json.loads(sandbox.run("list", *stack, "--json").stdout)
                    for row in listed:
                        if row.get("state") != "removed" and row.get("id"):
                            sandbox.run("remove", str(row["id"]), "--local", *stack, ok=(0, 1, 2))
                finally:
                    sandbox.run(
                        "services", "down", str(sandbox.project), "--purge", "--yes",
                        ok=(0, 1, 2),
                    )  # fmt: skip
            leaks = sandbox.leaks()
        finally:
            sandbox.keys.unlink(missing_ok=True)
        assert leaks == [], f"the key leaked into {len(leaks)} place(s): {leaks}"
