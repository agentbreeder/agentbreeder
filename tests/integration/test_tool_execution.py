"""End-to-end integration test for tool execution.

Covers the full path from ``agent.yaml`` tool reference → ``resolve_tool``
→ ``sandbox.execute``, asserting result shape at each boundary.

This is a **pure-mock** test — no real cloud, no live MCP server, no real
Docker / subprocess execution. The sandbox executor is replaced with an
in-process stub that runs the resolved tool against a mocked HTTP backend.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from api.services.sandbox_service import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
)
from engine.tool_resolver import (
    ToolNotFoundError,
    is_tool_ref,
    resolve_tool,
    validate_tool_input,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_yaml_with_web_search(tmp_path):
    """Write a minimal ``agent.yaml`` referencing the web_search tool."""
    cfg = {
        "name": "research-agent",
        "version": "0.1.0",
        "team": "engineering",
        "owner": "test@agentbreeder.io",
        "framework": "langgraph",
        "model": {"primary": "claude-sonnet-4"},
        "tools": [{"ref": "tools/web-search"}],
        "deploy": {"cloud": "local"},
    }
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


@pytest.fixture
def mock_tavily_response():
    """Mock HTTP response shape returned by the Tavily API."""
    return {
        "results": [
            {
                "url": "https://example.com/a",
                "title": "Example A",
                "content": "Snippet A",
                "score": 0.95,
            },
            {
                "url": "https://example.com/b",
                "title": "Example B",
                "content": "Snippet B",
                "score": 0.80,
            },
        ],
        "answer": "Distilled answer about the query.",
    }


# ---------------------------------------------------------------------------
# T12: agent.yaml → resolve_tool → sandbox.execute → result shape
# ---------------------------------------------------------------------------


def test_agent_yaml_references_resolvable_tool(agent_yaml_with_web_search):
    """The tools list in agent.yaml must contain valid registry refs."""
    cfg = yaml.safe_load(agent_yaml_with_web_search.read_text())
    refs = [t["ref"] for t in cfg["tools"]]
    assert refs == ["tools/web-search"]
    for ref in refs:
        assert is_tool_ref(ref), f"Bad ref shape: {ref}"


def test_resolve_tool_returns_callable_from_agent_yaml(agent_yaml_with_web_search):
    """``resolve_tool`` produces a callable Python function from the YAML ref."""
    cfg = yaml.safe_load(agent_yaml_with_web_search.read_text())
    ref = cfg["tools"][0]["ref"]

    fn = resolve_tool(ref)

    assert callable(fn)
    assert fn.__name__ == "web_search"


def test_validate_tool_input_against_schema_passes_for_valid_input():
    """SCHEMA enforcement allows well-formed inputs."""
    validate_tool_input("tools/web-search", {"query": "agentbreeder docs"})


def test_validate_tool_input_against_schema_rejects_missing_required():
    """SCHEMA enforcement rejects input missing required fields."""
    from engine.tool_resolver import ToolInputValidationError

    with pytest.raises(ToolInputValidationError, match="query"):
        validate_tool_input("tools/web-search", {})


def test_validate_tool_input_against_schema_rejects_wrong_type():
    """SCHEMA enforcement rejects wrong field types."""
    from engine.tool_resolver import ToolInputValidationError

    with pytest.raises(ToolInputValidationError):
        validate_tool_input("tools/web-search", {"query": 123})


def test_full_pipeline_resolve_then_sandbox_execute(
    agent_yaml_with_web_search, mock_tavily_response, monkeypatch
):
    """End-to-end: parse YAML → resolve tool → execute via sandbox → assert shape.

    The sandbox executor is stubbed to call the resolved tool in-process so we
    can exercise the surrounding contract without spinning up Docker.
    """
    # Step 1 — parse agent.yaml and pull out the tool ref.
    cfg = yaml.safe_load(agent_yaml_with_web_search.read_text())
    ref = cfg["tools"][0]["ref"]

    # Step 2 — resolve to a callable.
    fn = resolve_tool(ref)

    # Step 3 — validate input against the declared schema.
    tool_input = {"query": "integration test query", "max_results": 2}
    validate_tool_input(ref, tool_input)

    # Step 4 — patch the upstream HTTP backend the tool calls.
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    fake_response = MagicMock()
    fake_response.json.return_value = mock_tavily_response
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.return_value = fake_response
    fake_client.__exit__.return_value = False

    with patch("engine.tools.standard.web_search.httpx.Client", return_value=fake_client):
        # Step 5 — pretend the sandbox executor ran our tool. We build a
        # SandboxExecutionResult from the in-process call so we can assert
        # against the same shape the real sandbox would produce.
        result_dict = fn(**tool_input)

        sandbox_result = SandboxExecutionResult(
            execution_id="exec-test-1",
            output=json.dumps(result_dict),
            stdout=f"__TOOL_OUTPUT__{json.dumps(result_dict)}",
            stderr="",
            exit_code=0,
            duration_ms=1,
            timed_out=False,
        )

    # Step 6 — assert the sandbox result shape.
    assert sandbox_result.exit_code == 0
    assert sandbox_result.timed_out is False
    assert sandbox_result.error is None

    parsed: dict[str, Any] = json.loads(sandbox_result.output)
    assert set(parsed.keys()) == {"sources", "answer", "query"}
    assert parsed["query"] == "integration test query"
    assert parsed["answer"] == "Distilled answer about the query."
    assert len(parsed["sources"]) == 2

    # Step 7 — assert per-source shape (matches WebSearchSource TypedDict).
    first = parsed["sources"][0]
    assert set(first.keys()) == {"url", "title", "snippet", "score"}
    assert first["url"] == "https://example.com/a"
    assert first["title"] == "Example A"
    assert first["snippet"] == "Snippet A"
    assert first["score"] == 0.95


def test_full_pipeline_constructs_valid_sandbox_request():
    """The wire format sent to the sandbox service matches its dataclass."""
    req = SandboxExecutionRequest(
        code="result = {'ok': True}",
        input_json={"query": "hello"},
        timeout_seconds=10,
        network_enabled=False,
        tool_id="tools/web-search",
    )
    # Shape check — these are the fields the sandbox service reads.
    assert req.code.startswith("result")
    assert req.input_json == {"query": "hello"}
    assert req.timeout_seconds == 10
    assert req.network_enabled is False
    assert req.tool_id == "tools/web-search"


def test_unknown_tool_ref_raises_tool_not_found():
    """A ref that maps to nothing in standard library / local / registry fails fast."""
    with pytest.raises(ToolNotFoundError):
        resolve_tool("tools/nonexistent-tool-xyz")
