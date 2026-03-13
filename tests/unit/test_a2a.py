"""Tests for engine/a2a — client, protocol, agent_card, auth, tool_generator, server."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------


class TestJsonRpcProtocol:
    """Test A2A JSON-RPC message types."""

    def test_jsonrpc_request_defaults(self):
        from engine.a2a.protocol import JsonRpcRequest

        req = JsonRpcRequest(method="tasks/send")
        assert req.jsonrpc == "2.0"
        assert req.method == "tasks/send"
        assert req.params == {}
        assert req.id  # auto-generated UUID

    def test_jsonrpc_request_custom_id(self):
        from engine.a2a.protocol import JsonRpcRequest

        req = JsonRpcRequest(id="custom-1", method="tasks/get", params={"task_id": "abc"})
        assert req.id == "custom-1"
        assert req.params == {"task_id": "abc"}

    def test_jsonrpc_error(self):
        from engine.a2a.protocol import JsonRpcError

        err = JsonRpcError(
            code=-32600, message="Invalid request", data={"detail": "missing method"}
        )
        assert err.code == -32600
        assert err.message == "Invalid request"
        assert err.data == {"detail": "missing method"}

    def test_jsonrpc_response_success(self):
        from engine.a2a.protocol import JsonRpcResponse

        resp = JsonRpcResponse(id="1", result={"status": "ok"})
        assert resp.result == {"status": "ok"}
        assert resp.error is None

    def test_jsonrpc_response_error(self):
        from engine.a2a.protocol import JsonRpcError, JsonRpcResponse

        resp = JsonRpcResponse(
            id="1",
            error=JsonRpcError(code=-32601, message="Method not found"),
        )
        assert resp.result is None
        assert resp.error.code == -32601

    def test_task_send_params(self):
        from engine.a2a.protocol import TaskSendParams

        params = TaskSendParams(message="hello", context={"key": "val"})
        assert params.message == "hello"
        assert params.task_id is None

    def test_task_send_params_with_task_id(self):
        from engine.a2a.protocol import TaskSendParams

        params = TaskSendParams(message="hello", task_id="t-123")
        assert params.task_id == "t-123"

    def test_task_result(self):
        from engine.a2a.protocol import TaskResult

        result = TaskResult(task_id="t-1", status="completed", output="done", tokens=100)
        assert result.task_id == "t-1"
        assert result.output == "done"
        assert result.artifacts == []

    def test_agent_card_info_defaults(self):
        from engine.a2a.protocol import AgentCardInfo

        card = AgentCardInfo(name="test-agent", url="http://localhost:8000")
        assert card.name == "test-agent"
        assert card.version == "1.0.0"
        assert card.default_input_modes == ["text"]
        assert card.authentication == {"schemes": ["none"]}

    def test_method_constants(self):
        from engine.a2a.protocol import (
            A2A_METHOD_CANCEL,
            A2A_METHOD_GET,
            A2A_METHOD_SEND,
            A2A_METHOD_SUBSCRIBE,
        )

        assert A2A_METHOD_SEND == "tasks/send"
        assert A2A_METHOD_GET == "tasks/get"
        assert A2A_METHOD_CANCEL == "tasks/cancel"
        assert A2A_METHOD_SUBSCRIBE == "tasks/sendSubscribe"


# ---------------------------------------------------------------------------
# Agent Card tests
# ---------------------------------------------------------------------------


class TestAgentCard:
    """Test agent card generation."""

    def _make_config(self, **overrides):
        from engine.config_parser import AgentConfig, DeployConfig, ModelConfig

        defaults = {
            "name": "test-agent",
            "version": "1.0.0",
            "description": "A test agent",
            "team": "eng",
            "owner": "test@test.com",
            "framework": "custom",
            "model": ModelConfig(primary="claude-sonnet-4"),
            "deploy": DeployConfig(cloud="local"),
            "tools": [],
        }
        defaults.update(overrides)
        return AgentConfig(**defaults)

    def test_generate_card_with_tools(self):
        from engine.a2a.agent_card import generate_agent_card
        from engine.config_parser import ToolRef

        config = self._make_config(
            name="test-agent",
            version="2.0.0",
            tools=[
                ToolRef(ref="tools/search", description="Search tool"),
                ToolRef(name="calculator", description="Math tool"),
            ],
        )

        card = generate_agent_card(config, "http://localhost:9000")
        assert card["name"] == "test-agent"
        assert card["version"] == "2.0.0"
        assert card["url"] == "http://localhost:9000"
        assert len(card["skills"]) == 2
        assert card["skills"][0]["id"] == "search"
        assert card["skills"][1]["id"] == "calculator"
        assert card["authentication"] == {"schemes": ["bearer"]}

    def test_generate_card_no_tools(self):
        from engine.a2a.agent_card import generate_agent_card

        config = self._make_config(name="simple-agent", description="A simple agent", tools=[])

        card = generate_agent_card(config, "http://localhost:9000")
        assert len(card["skills"]) == 1
        assert card["skills"][0]["id"] == "chat"
        assert "simple agent" in card["skills"][0]["description"].lower()

    def test_generate_card_empty_description(self):
        from engine.a2a.agent_card import generate_agent_card

        config = self._make_config(name="no-desc", description="")

        card = generate_agent_card(config, "http://example.com")
        assert card["description"] == ""
        assert "no-desc" in card["skills"][0]["description"]


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestA2AAuth:
    """Test JWT inter-agent authentication."""

    def test_create_service_token(self):
        from engine.a2a.auth import create_service_token

        token = create_service_token("my-agent", team="eng")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_validate_service_token(self):
        from engine.a2a.auth import create_service_token, validate_service_token

        token = create_service_token("my-agent", team="eng")
        claims = validate_service_token(token)
        assert claims["sub"] == "agent:my-agent"
        assert claims["team"] == "eng"
        assert claims["type"] == "a2a_service"

    def test_validate_token_with_extra_claims(self):
        from engine.a2a.auth import create_service_token, validate_service_token

        token = create_service_token("my-agent", extra_claims={"scope": "read"})
        claims = validate_service_token(token)
        assert claims["scope"] == "read"

    def test_validate_invalid_token(self):
        from engine.a2a.auth import validate_service_token

        with pytest.raises(JWTError):
            validate_service_token("invalid.token.here")

    def test_validate_expired_token(self):
        from engine.a2a.auth import _ALGORITHM, _SECRET_KEY

        payload = {
            "sub": "agent:expired",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
            "type": "a2a_service",
        }
        token = jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)

        from engine.a2a.auth import validate_service_token

        with pytest.raises(ExpiredSignatureError):
            validate_service_token(token)

    def test_validate_wrong_type_token(self):
        from engine.a2a.auth import _ALGORITHM, _SECRET_KEY

        payload = {
            "sub": "user:bob",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "type": "user_session",
        }
        token = jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)

        from engine.a2a.auth import validate_service_token

        with pytest.raises(JWTError, match="Not an A2A service token"):
            validate_service_token(token)

    def test_extract_agent_name(self):
        from engine.a2a.auth import create_service_token, extract_agent_name

        token = create_service_token("my-agent")
        name = extract_agent_name(token)
        assert name == "my-agent"

    def test_extract_agent_name_invalid(self):
        from engine.a2a.auth import extract_agent_name

        assert extract_agent_name("bad-token") is None

    def test_create_token_no_team(self):
        from engine.a2a.auth import create_service_token, validate_service_token

        token = create_service_token("solo-agent")
        claims = validate_service_token(token)
        assert "team" not in claims


# ---------------------------------------------------------------------------
# Tool Generator tests
# ---------------------------------------------------------------------------


class TestToolGenerator:
    """Test subagent tool generation."""

    def test_generate_single_tool(self):
        from engine.a2a.tool_generator import generate_subagent_tools
        from engine.config_parser import SubagentRef

        subs = [SubagentRef(ref="agents/billing", name="billing", description="Handles billing")]
        tools = generate_subagent_tools(subs)
        assert len(tools) == 1
        assert tools[0]["name"] == "call_billing"
        assert tools[0]["type"] == "function"
        assert tools[0]["description"] == "Handles billing"
        assert tools[0]["_subagent_ref"] == "agents/billing"
        assert "message" in tools[0]["schema"]["properties"]

    def test_generate_multiple_tools(self):
        from engine.a2a.tool_generator import generate_subagent_tools
        from engine.config_parser import SubagentRef

        subs = [
            SubagentRef(ref="agents/billing"),
            SubagentRef(ref="agents/tech-support"),
        ]
        tools = generate_subagent_tools(subs)
        assert len(tools) == 2
        assert tools[0]["name"] == "call_billing"
        assert tools[1]["name"] == "call_tech_support"

    def test_generate_empty_list(self):
        from engine.a2a.tool_generator import generate_subagent_tools

        tools = generate_subagent_tools([])
        assert tools == []

    def test_default_description(self):
        from engine.a2a.tool_generator import generate_subagent_tools
        from engine.config_parser import SubagentRef

        subs = [SubagentRef(ref="agents/helper")]
        tools = generate_subagent_tools(subs)
        assert "helper" in tools[0]["description"]


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------


class TestAgentInvocationClient:
    """Test the async HTTP invocation client."""

    @pytest.mark.asyncio
    async def test_invoke_success(self):
        from engine.a2a.client import AgentInvocationClient

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"output": "hello", "tokens": 50}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test message")
        assert result.status == "success"
        assert result.output == "hello"
        assert result.tokens == 50
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_invoke_http_error(self):
        from engine.a2a.client import AgentInvocationClient

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test")
        assert result.status == "error"
        assert "HTTP 500" in result.error

    @pytest.mark.asyncio
    async def test_invoke_timeout(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.is_closed = False

        client = AgentInvocationClient(timeout=1.0)
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test")
        assert result.status == "error"
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_invoke_connection_error(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test")
        assert result.status == "error"
        assert "Connection failed" in result.error

    @pytest.mark.asyncio
    async def test_close(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        await client.close()
        mock_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_already_closed(self):
        from engine.a2a.client import AgentInvocationClient

        client = AgentInvocationClient()
        client._client = None
        await client.close()  # should not raise

    @pytest.mark.asyncio
    async def test_get_client_creates_new(self):
        from engine.a2a.client import AgentInvocationClient

        client = AgentInvocationClient(auth_token="test-token")
        http_client = await client._get_client()
        assert http_client is not None
        assert client._client is http_client
        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_reuses_existing(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client._get_client()
        assert result is mock_client

    @pytest.mark.asyncio
    async def test_invoke_url_construction(self):
        from engine.a2a.client import AgentInvocationClient

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"output": "ok"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        await client.invoke("http://agent:8000/", "test")
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://agent:8000/invoke"

    def test_invocation_result_defaults(self):
        from engine.a2a.client import AgentInvocationResult

        result = AgentInvocationResult(output="hello")
        assert result.tokens == 0
        assert result.latency_ms == 0
        assert result.status == "success"
        assert result.error is None


# ---------------------------------------------------------------------------
# Server tests
# ---------------------------------------------------------------------------


class TestA2AServer:
    """Test A2A server FastAPI sub-app."""

    def _make_app(self, invoke_handler=None):
        from engine.a2a.protocol import AgentCardInfo
        from engine.a2a.server import create_a2a_app

        card = AgentCardInfo(name="test-agent", url="http://localhost:9000")
        return create_a2a_app(card, invoke_handler=invoke_handler)

    @pytest.mark.asyncio
    async def test_get_agent_card(self):
        from httpx import ASGITransport, AsyncClient

        app = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/.well-known/agent.json")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_task_send_stub(self):
        from httpx import ASGITransport, AsyncClient

        app = self._make_app()
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/send",
            "params": {"message": "hello"},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/a2a", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["result"]["status"] == "completed"
            assert "hello" in data["result"]["output"]

    @pytest.mark.asyncio
    async def test_task_send_with_handler(self):
        from httpx import ASGITransport, AsyncClient

        async def handler(message, context):
            return {"output": f"Processed: {message}", "tokens": 42}

        app = self._make_app(invoke_handler=handler)
        payload = {
            "jsonrpc": "2.0",
            "id": "2",
            "method": "tasks/send",
            "params": {"message": "hello world"},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/a2a", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["result"]["output"] == "Processed: hello world"
            assert data["result"]["tokens"] == 42

    @pytest.mark.asyncio
    async def test_task_send_handler_error(self):
        from httpx import ASGITransport, AsyncClient

        async def bad_handler(message, context):
            raise RuntimeError("handler exploded")

        app = self._make_app(invoke_handler=bad_handler)
        payload = {
            "jsonrpc": "2.0",
            "id": "3",
            "method": "tasks/send",
            "params": {"message": "boom"},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/a2a", json=payload)
            assert resp.status_code == 500
            data = resp.json()
            assert data["error"]["code"] == -32000

    @pytest.mark.asyncio
    async def test_task_get_not_found(self):
        from httpx import ASGITransport, AsyncClient

        app = self._make_app()
        payload = {
            "jsonrpc": "2.0",
            "id": "4",
            "method": "tasks/get",
            "params": {"task_id": "nonexistent"},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/a2a", json=payload)
            assert resp.status_code == 404
            data = resp.json()
            assert data["error"]["code"] == -32001

    @pytest.mark.asyncio
    async def test_task_get_after_send(self):
        from httpx import ASGITransport, AsyncClient

        app = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Send a task
            send_payload = {
                "jsonrpc": "2.0",
                "id": "5",
                "method": "tasks/send",
                "params": {"message": "store this", "task_id": "t-100"},
            }
            resp = await ac.post("/a2a", json=send_payload)
            assert resp.status_code == 200

            # Get the task
            get_payload = {
                "jsonrpc": "2.0",
                "id": "6",
                "method": "tasks/get",
                "params": {"task_id": "t-100"},
            }
            resp = await ac.post("/a2a", json=get_payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["result"]["task_id"] == "t-100"

    @pytest.mark.asyncio
    async def test_unknown_method(self):
        from httpx import ASGITransport, AsyncClient

        app = self._make_app()
        payload = {
            "jsonrpc": "2.0",
            "id": "7",
            "method": "unknown/method",
            "params": {},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/a2a", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_parse_error(self):
        from httpx import ASGITransport, AsyncClient

        app = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/a2a", content=b"not json", headers={"Content-Type": "application/json"}
            )
            assert resp.status_code == 400
