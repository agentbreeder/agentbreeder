"""Tests for builders and playground API routes."""

from __future__ import annotations

import textwrap

import pytest
from fastapi.testclient import TestClient

import api.routes.builders as builders_module
from api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_store(tmp_path):
    """Redirect the global FileStore to a fresh temp directory for each test."""
    from api.routes.builders import FileStore

    store = FileStore(base_dir=tmp_path)
    builders_module._store = store
    yield store
    # Restore default store after test (use a new instance)
    builders_module._store = FileStore()


# ---------------------------------------------------------------------------
# Valid YAML payloads
# ---------------------------------------------------------------------------

VALID_AGENT_YAML = textwrap.dedent("""\
    name: test-agent
    version: "1.0.0"
    team: engineering
    owner: alice@example.com
    framework: langgraph
    model:
      primary: claude-sonnet-4
    deploy:
      cloud: aws
""")

VALID_PROMPT_YAML = textwrap.dedent("""\
    name: support-system
    version: "1.0.0"
    content: "You are a helpful support agent."
""")

VALID_TOOL_YAML = textwrap.dedent("""\
    name: zendesk-mcp
    version: "1.0.0"
    description: "Zendesk MCP server"
    type: mcp
""")


# ═══════════════════════════════════════════════════════════════════════════
# Builders endpoints
# ═══════════════════════════════════════════════════════════════════════════


class TestGetYamlAgent:
    def test_get_yaml_agent(self, _clear_store) -> None:
        """GET /builders/agent/{name}/yaml returns stored YAML."""
        _clear_store.set("agent", "test-agent", VALID_AGENT_YAML)
        resp = client.get("/api/v1/builders/agent/test-agent/yaml")
        assert resp.status_code == 200
        assert "test-agent" in resp.text
        assert resp.headers["content-type"].startswith("application/x-yaml")

    def test_get_yaml_not_found(self) -> None:
        """GET for nonexistent resource returns 404."""
        resp = client.get("/api/v1/builders/agent/nonexistent/yaml")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestPutYamlAgent:
    def test_put_yaml_agent(self) -> None:
        """PUT /builders/agent/{name}/yaml saves valid YAML."""
        resp = client.put(
            "/api/v1/builders/agent/test-agent/yaml",
            content=VALID_AGENT_YAML,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["valid"] is True
        assert body["data"]["name"] == "test-agent"
        assert body["data"]["resource_type"] == "agent"
        # Verify it was persisted
        assert builders_module._store.get_raw("agent", "test-agent") == VALID_AGENT_YAML

    def test_put_yaml_invalid_schema(self) -> None:
        """PUT with YAML missing required fields returns 422."""
        invalid_yaml = textwrap.dedent("""\
            name: bad-agent
            version: "1.0.0"
        """)
        resp = client.put(
            "/api/v1/builders/agent/bad-agent/yaml",
            content=invalid_yaml,
        )
        assert resp.status_code == 422
        assert "Schema validation failed" in resp.json()["detail"]

    def test_put_yaml_malformed(self) -> None:
        """PUT with non-YAML content returns 422."""
        resp = client.put(
            "/api/v1/builders/agent/bad/yaml",
            content=":::\n  - :\n  invalid: [unterminated",
        )
        assert resp.status_code == 422


class TestImportYaml:
    def test_import_yaml(self) -> None:
        """POST /builders/import creates a new resource."""
        resp = client.post(
            "/api/v1/builders/import",
            json={
                "resource_type": "agent",
                "yaml_content": VALID_AGENT_YAML,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["name"] == "test-agent"
        assert body["data"]["resource_type"] == "agent"
        assert "imported" in body["data"]["message"].lower()
        # Verify persisted
        assert builders_module._store.exists("agent", "test-agent")

    def test_import_yaml_invalid_type(self) -> None:
        """POST with unknown resource_type returns 400."""
        resp = client.post(
            "/api/v1/builders/import",
            json={
                "resource_type": "spaceship",
                "yaml_content": VALID_AGENT_YAML,
            },
        )
        assert resp.status_code == 400
        assert "Invalid resource_type" in resp.json()["detail"]


class TestGetYamlPrompt:
    def test_get_yaml_prompt(self, _clear_store) -> None:
        """GET /builders/prompt/{name}/yaml returns stored prompt YAML."""
        _clear_store.set("prompt", "support-system", VALID_PROMPT_YAML)
        resp = client.get("/api/v1/builders/prompt/support-system/yaml")
        assert resp.status_code == 200
        assert "support-system" in resp.text


class TestGetYamlTool:
    def test_get_yaml_tool(self, _clear_store) -> None:
        """GET /builders/tool/{name}/yaml returns stored tool YAML."""
        _clear_store.set("tool", "zendesk-mcp", VALID_TOOL_YAML)
        resp = client.get("/api/v1/builders/tool/zendesk-mcp/yaml")
        assert resp.status_code == 200
        assert "zendesk-mcp" in resp.text


# ═══════════════════════════════════════════════════════════════════════════
# Playground endpoints
# ═══════════════════════════════════════════════════════════════════════════


class TestPlaygroundChat:
    def test_playground_chat(self) -> None:
        """POST /playground/chat returns a response with expected fields."""
        import unittest.mock as mock

        import api.routes.playground as pg

        with mock.patch.object(pg, "_try_litellm_call", return_value=("Hello!", 10, 5, "claude-sonnet-4")):
            resp = client.post(
                "/api/v1/playground/chat",
                json={
                    "agent_id": "agent-123",
                    "message": "Hello, how can you help?",
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "response" in data
        assert isinstance(data["token_count"], int)
        assert data["token_count"] > 0
        assert isinstance(data["cost_estimate"], float)
        assert isinstance(data["latency_ms"], int)
        assert data["model_used"] == "claude-sonnet-4"
        assert data["conversation_id"]

    def test_playground_chat_with_model_override(self) -> None:
        """Model override is reflected in model_used."""
        resp = client.post(
            "/api/v1/playground/chat",
            json={
                "agent_id": "agent-123",
                "message": "Test",
                "model_override": "gpt-4o",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["model_used"] == "gpt-4o"

    def test_playground_chat_with_prompt_override(self) -> None:
        """System prompt override is accepted without error."""
        resp = client.post(
            "/api/v1/playground/chat",
            json={
                "agent_id": "agent-123",
                "message": "Test",
                "system_prompt_override": "You are a pirate.",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["response"]

    def test_playground_chat_missing_agent_id(self) -> None:
        """Missing agent_id returns 422 validation error."""
        resp = client.post(
            "/api/v1/playground/chat",
            json={
                "message": "Hello",
            },
        )
        assert resp.status_code == 422


class TestPlaygroundSaveEvalCase:
    def test_save_eval_case(self) -> None:
        """POST /playground/eval-case saves and returns eval_case_id."""
        resp = client.post(
            "/api/v1/playground/eval-case",
            json={
                "agent_id": "agent-123",
                "conversation_history": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
                "assistant_message": "Hi there!",
                "model_used": "claude-sonnet-4",
                "tags": ["smoke-test"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["saved"] is True
        assert data["eval_case_id"]
