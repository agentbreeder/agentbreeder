"""Unit tests for ``agentbreeder validate`` runtime-file prereq checks.

Covers issue #560, bug #10 — `agentbreeder validate` used to pass on
example projects that `agentbreeder deploy` then failed for at step 4/6
with "Missing agent.py". The fix invokes the matching runtime's
``validate()`` after schema validation so the missing-files class of
failure surfaces at validate-time.

Test matrix:

* missing ``agent.py`` (LangGraph) — FAIL with helpful message
* missing ``requirements.txt`` (LangGraph) — FAIL with helpful message
* valid bundle (LangGraph with both files) — PASS
* ``claude-managed`` deploy without ``agent.py`` — PASS (no container is
  built, so on-disk prereqs do not apply)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ────────────────────────────── Fixtures ────────────────────────────────────


def _write_langgraph_yaml(agent_dir: Path) -> Path:
    """Write a minimal valid LangGraph agent.yaml into ``agent_dir``."""
    yaml_path = agent_dir / "agent.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "name: test-agent",
                "version: 0.1.0",
                "team: examples",
                "owner: dev@example.com",
                "framework: langgraph",
                "model:",
                "  primary: claude-sonnet-4",
                "deploy:",
                "  cloud: local",
                "",
            ]
        )
    )
    return yaml_path


def _write_agent_py(agent_dir: Path) -> Path:
    """Write a minimal LangGraph agent.py exposing ``graph``."""
    p = agent_dir / "agent.py"
    p.write_text(
        "from langgraph.graph import StateGraph\n"
        "from typing import TypedDict\n"
        "class S(TypedDict):\n"
        "    message: str\n"
        "    response: str\n"
        "def respond(s):\n"
        "    return {'message': s.get('message',''), 'response': 'ok'}\n"
        "b = StateGraph(S)\n"
        "b.add_node('respond', respond)\n"
        "b.set_entry_point('respond')\n"
        "b.set_finish_point('respond')\n"
        "graph = b.compile()\n"
    )
    return p


def _write_requirements(agent_dir: Path) -> Path:
    p = agent_dir / "requirements.txt"
    p.write_text("langgraph>=0.2.0\nlangchain-core>=0.3.0\n")
    return p


# ────────────────────────────── Tests ───────────────────────────────────────


def test_validate_fails_when_agent_py_is_missing(tmp_path: Path) -> None:
    """LangGraph agent.yaml without agent.py — validate should FAIL."""
    yaml_path = _write_langgraph_yaml(tmp_path)
    _write_requirements(tmp_path)  # requirements present, agent.py missing
    # No agent.py created intentionally.

    result = runner.invoke(app, ["validate", str(yaml_path), "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    messages = " ".join(e["message"] for e in payload["errors"])
    assert "agent.py" in messages
    # Suggestion guidance should be present so users know what to do.
    suggestions = " ".join(e["suggestion"] for e in payload["errors"])
    assert suggestions  # non-empty


def test_validate_fails_when_requirements_is_missing(tmp_path: Path) -> None:
    """LangGraph agent.yaml + agent.py but no requirements.txt — FAIL."""
    yaml_path = _write_langgraph_yaml(tmp_path)
    _write_agent_py(tmp_path)
    # No requirements.txt and no pyproject.toml.

    result = runner.invoke(app, ["validate", str(yaml_path), "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    messages = " ".join(e["message"] for e in payload["errors"])
    assert "requirements.txt" in messages or "pyproject.toml" in messages


def test_validate_passes_for_complete_bundle(tmp_path: Path) -> None:
    """LangGraph agent.yaml + agent.py + requirements.txt — PASS."""
    yaml_path = _write_langgraph_yaml(tmp_path)
    _write_agent_py(tmp_path)
    _write_requirements(tmp_path)

    result = runner.invoke(app, ["validate", str(yaml_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["errors"] == []


def test_validate_passes_claude_managed_without_agent_py(tmp_path: Path) -> None:
    """Claude Managed Agents do not build a container — agent.py is NOT
    required for validate to succeed (Anthropic manages the runtime).
    """
    yaml_path = tmp_path / "agent.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "name: managed-agent",
                "version: 0.1.0",
                "team: examples",
                "owner: dev@example.com",
                "framework: claude_sdk",
                "model:",
                "  primary: claude-sonnet-4",
                "deploy:",
                "  cloud: claude-managed",
                "claude_managed:",
                "  environment:",
                "    networking: unrestricted",
                "  tools:",
                "    - type: agent_toolset_20260401",
                "",
            ]
        )
    )
    # Deliberately omit agent.py and requirements.txt.

    result = runner.invoke(app, ["validate", str(yaml_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is True, payload
    assert payload["errors"] == []


@pytest.mark.parametrize(
    "example_name",
    [
        # Previously broken (issue #560 bug #10) — should now PASS after the
        # examples cleanup in this commit.
        "ollama-agent",
        "a2a-subagent",
        "aws-app-runner-agent",
        "claude-managed-agent",
        "crewai-agent",
        "go-agent",
        "graphrag-ollama-agent",
        "openrouter-agent",
        "registry-pattern-ts",
        # Sanity — never broken.
        "langgraph-agent",
    ],
)
def test_validate_passes_for_each_repaired_example(example_name: str) -> None:
    """Each of the nine previously-broken examples now passes validate.

    Treated as a regression net: if anyone re-introduces a stub example
    without the runtime files the deployer needs, this test fails.
    """
    repo_root = Path(__file__).resolve().parents[3]
    yaml_path = repo_root / "examples" / example_name / "agent.yaml"
    if not yaml_path.exists():
        pytest.skip(f"example missing on disk: {yaml_path}")

    result = runner.invoke(app, ["validate", str(yaml_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is True, payload
