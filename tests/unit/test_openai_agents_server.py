"""Tests for engine/runtimes/templates/openai_agents_server.py.

Tests cover:
- API key init on startup (set_default_openai_key called / not called)
- /invoke returns enhanced InvokeResponse (agent + handoffs fields)
- HandoffOutputItem extraction populates handoffs list
- /stream returns StreamingResponse with text/event-stream content type
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers to build minimal stub modules for the 'agents' package so that
# importing the server template never requires the real openai-agents package.
# ---------------------------------------------------------------------------


def _make_agents_stub(**extra_attrs: Any) -> types.ModuleType:
    """Return a minimal 'agents' stub module."""
    stub = types.ModuleType("agents")

    class HandoffOutputItem:  # noqa: D401
        """Stub HandoffOutputItem."""

        def __init__(self, target_agent=None, agent=None):
            self.target_agent = target_agent
            self.agent = agent

    class MessageOutputItem:
        def __init__(self, agent=None):
            self.agent = agent

    class Runner:
        pass

    stub.HandoffOutputItem = HandoffOutputItem
    stub.MessageOutputItem = MessageOutputItem
    stub.Runner = Runner
    stub.set_default_openai_key = MagicMock()

    for k, v in extra_attrs.items():
        setattr(stub, k, v)
    return stub


def _import_server(agents_stub: types.ModuleType):
    """Import (or re-import) the server module with the given agents stub injected."""
    # Always inject stub before import
    sys.modules["agents"] = agents_stub

    # Remove cached module so we get a fresh import each time
    for key in list(sys.modules):
        if key in ("openai_agents_server",):
            del sys.modules[key]

    # The template lives at engine/runtimes/templates/openai_agents_server.py
    # We load it as a standalone module (it is not part of a package when copied
    # into containers, so we use importlib.util).
    import importlib.util
    from pathlib import Path

    template_path = (
        Path(__file__).parents[2] / "engine" / "runtimes" / "templates" / "openai_agents_server.py"
    )
    spec = importlib.util.spec_from_file_location("openai_agents_server", template_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["openai_agents_server"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agents_stub():
    """Provide a fresh agents stub and clean up after each test."""
    stub = _make_agents_stub()
    yield stub
    # Cleanup
    sys.modules.pop("agents", None)
    sys.modules.pop("openai_agents_server", None)


# ---------------------------------------------------------------------------
# Startup / API key tests
# ---------------------------------------------------------------------------


class TestStartupApiKeyInit:
    @pytest.mark.asyncio
    async def test_set_default_openai_key_called_when_env_set(self, agents_stub):
        """set_default_openai_key should be called during startup when OPENAI_API_KEY is set."""
        server = _import_server(agents_stub)

        # Patch _load_agent so startup doesn't try to import real agent files
        dummy_agent = MagicMock()
        with (
            patch.object(server, "_load_agent", return_value=dummy_agent),
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key-123"}),
        ):
            await server.startup()

        agents_stub.set_default_openai_key.assert_called_once_with("sk-test-key-123")

    @pytest.mark.asyncio
    async def test_set_default_openai_key_not_called_when_env_absent(self, agents_stub):
        """set_default_openai_key should NOT be called when OPENAI_API_KEY is absent."""
        server = _import_server(agents_stub)

        dummy_agent = MagicMock()
        env_without_key = {
            k: v for k, v in __import__("os").environ.items() if k != "OPENAI_API_KEY"
        }
        with (
            patch.object(server, "_load_agent", return_value=dummy_agent),
            patch.dict("os.environ", env_without_key, clear=True),
        ):
            await server.startup()

        agents_stub.set_default_openai_key.assert_not_called()


# ---------------------------------------------------------------------------
# /invoke endpoint tests
# ---------------------------------------------------------------------------


class TestInvokeEndpoint:
    def _get_client(self, agents_stub, agent_obj, run_result):
        """Build a TestClient with _agent and Runner pre-configured."""
        server = _import_server(agents_stub)

        # Inject agent directly (skip startup)
        server._agent = agent_obj

        # Configure Runner.run as AsyncMock
        agents_stub.Runner.run = AsyncMock(return_value=run_result)

        return TestClient(server.app), server

    def _make_run_result(self, final_output: str, new_items=None):
        result = MagicMock()
        result.final_output = final_output
        result.new_items = new_items or []
        return result

    def test_invoke_returns_output_field(self, agents_stub):
        run_result = self._make_run_result("Hello, world!")
        client, _ = self._get_client(agents_stub, MagicMock(), run_result)

        response = client.post("/invoke", json={"input": "Hi"})

        assert response.status_code == 200
        data = response.json()
        assert data["output"] == "Hello, world!"

    def test_invoke_returns_agent_field(self, agents_stub):
        """Last agent name should be captured in the agent field."""
        # Create an item that has an .agent attribute
        item = MagicMock()
        item_agent = MagicMock()
        item_agent.name = "billing-agent"
        item.agent = item_agent
        # Make sure it's not a HandoffOutputItem
        item.__class__ = MagicMock  # not HandoffOutputItem

        run_result = self._make_run_result("Done", new_items=[item])
        client, _ = self._get_client(agents_stub, MagicMock(), run_result)

        response = client.post("/invoke", json={"input": "What's my bill?"})

        assert response.status_code == 200
        data = response.json()
        assert data["agent"] == "billing-agent"

    def test_invoke_returns_empty_handoffs_when_none(self, agents_stub):
        run_result = self._make_run_result("Hi", new_items=[])
        client, _ = self._get_client(agents_stub, MagicMock(), run_result)

        response = client.post("/invoke", json={"input": "Hello"})

        assert response.status_code == 200
        assert response.json()["handoffs"] == []

    def test_invoke_returns_handoffs_list(self, agents_stub):
        """HandoffOutputItem entries should populate the handoffs list."""
        HandoffOutputItem = agents_stub.HandoffOutputItem

        target1 = MagicMock()
        target1.name = "support-agent"
        target2 = MagicMock()
        target2.name = "billing-agent"

        item1 = HandoffOutputItem(target_agent=target1)
        item2 = HandoffOutputItem(target_agent=target2)

        run_result = self._make_run_result("Resolved", new_items=[item1, item2])
        client, _ = self._get_client(agents_stub, MagicMock(), run_result)

        response = client.post("/invoke", json={"input": "Need help"})

        assert response.status_code == 200
        data = response.json()
        assert data["handoffs"] == ["support-agent", "billing-agent"]

    def test_invoke_handoff_without_target_agent_attr(self, agents_stub):
        """HandoffOutputItem without target_agent falls back to str(item)."""
        HandoffOutputItem = agents_stub.HandoffOutputItem

        item = HandoffOutputItem()  # no target_agent
        item.target_agent = None
        # str representation
        item.__str__ = lambda self: "fallback-agent-str"

        run_result = self._make_run_result("Done", new_items=[item])
        client, _ = self._get_client(agents_stub, MagicMock(), run_result)

        response = client.post("/invoke", json={"input": "go"})

        assert response.status_code == 200
        # The handoff was recorded (even if as str form)
        assert len(response.json()["handoffs"]) == 1

    def test_invoke_503_when_agent_not_loaded(self, agents_stub):
        server = _import_server(agents_stub)
        server._agent = None  # simulate not loaded

        client = TestClient(server.app)
        response = client.post("/invoke", json={"input": "hi"})

        assert response.status_code == 503

    def test_invoke_500_on_runner_exception(self, agents_stub):
        server = _import_server(agents_stub)
        server._agent = MagicMock()
        agents_stub.Runner.run = AsyncMock(side_effect=RuntimeError("LLM error"))

        client = TestClient(server.app)
        response = client.post("/invoke", json={"input": "hi"})

        assert response.status_code == 500
        assert "LLM error" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /stream endpoint tests
# ---------------------------------------------------------------------------


class TestStreamEndpoint:
    def _get_stream_client(self, agents_stub, agent_obj, events):
        server = _import_server(agents_stub)
        server._agent = agent_obj

        # Build an async generator that yields the given events
        async def _fake_stream_events():
            for ev in events:
                yield ev

        stream_result = MagicMock()
        stream_result.stream_events = _fake_stream_events

        agents_stub.Runner.run_streamed = MagicMock(return_value=stream_result)

        return TestClient(server.app)

    def test_stream_returns_200(self, agents_stub):
        client = self._get_stream_client(agents_stub, MagicMock(), [])
        response = client.post("/stream", json={"input": "Hi"})
        assert response.status_code == 200

    def test_stream_content_type_is_event_stream(self, agents_stub):
        client = self._get_stream_client(agents_stub, MagicMock(), [])
        response = client.post("/stream", json={"input": "Hi"})
        assert "text/event-stream" in response.headers["content-type"]

    def test_stream_emits_done_sentinel(self, agents_stub):
        client = self._get_stream_client(agents_stub, MagicMock(), [])
        response = client.post("/stream", json={"input": "Hi"})
        assert "data: [DONE]" in response.text

    def test_stream_emits_event_type(self, agents_stub):

        # Create a fake event with a recognisable class name
        class RawResponsesEvent:
            pass

        event = RawResponsesEvent()

        client = self._get_stream_client(agents_stub, MagicMock(), [event])
        response = client.post("/stream", json={"input": "Hello"})

        assert "RawResponsesEvent" in response.text

    def test_stream_emits_agent_name_when_present(self, agents_stub):
        class AgentUpdatedEvent:
            pass

        event = AgentUpdatedEvent()
        agent_obj = MagicMock()
        agent_obj.name = "triage-agent"
        event.agent = agent_obj

        client = self._get_stream_client(agents_stub, MagicMock(), [event])
        response = client.post("/stream", json={"input": "Route me"})

        import json as _json

        lines = [
            line[len("data: ") :]
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert len(lines) >= 1
        parsed = _json.loads(lines[0])
        assert parsed.get("agent") == "triage-agent"

    def test_stream_emits_delta_when_present(self, agents_stub):
        class TextDeltaEvent:
            delta = "Hello "

        client = self._get_stream_client(agents_stub, MagicMock(), [TextDeltaEvent()])
        response = client.post("/stream", json={"input": "hi"})

        import json as _json

        lines = [
            line[len("data: ") :]
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        parsed = _json.loads(lines[0])
        assert parsed.get("delta") == "Hello "

    def test_stream_multiple_events_in_order(self, agents_stub):
        class Ev:
            def __init__(self, name):
                self.__class__.__name__ = name  # type: ignore[misc]

        # Use distinct classes for distinct type names
        class EventA:
            pass

        class EventB:
            pass

        client = self._get_stream_client(agents_stub, MagicMock(), [EventA(), EventB()])
        response = client.post("/stream", json={"input": "go"})

        text = response.text
        pos_a = text.find("EventA")
        pos_b = text.find("EventB")
        assert pos_a != -1
        assert pos_b != -1
        assert pos_a < pos_b  # EventA comes before EventB


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_healthy_when_agent_loaded(self, agents_stub):
        server = _import_server(agents_stub)
        server._agent = MagicMock()
        client = TestClient(server.app)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_health_loading_when_agent_none(self, agents_stub):
        server = _import_server(agents_stub)
        server._agent = None
        client = TestClient(server.app)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "loading"


# ---------------------------------------------------------------------------
# tool_bridge A2A integration tests (feature #110)
# ---------------------------------------------------------------------------


class TestToolBridgeA2AIntegration:
    """Tests for the tool_bridge A2A sub-agent registration added in startup()."""

    @pytest.mark.asyncio
    async def test_a2a_tool_registered_on_agent_tools_list(self, agents_stub):
        """When AGENT_TOOLS_JSON is set, FunctionTool should be appended to agent.tools."""
        server = _import_server(agents_stub)

        # Build a fake agent with a mutable tools list.
        fake_agent = MagicMock()
        fake_agent.tools = []

        # Build a fake tool_bridge.
        fake_tb = MagicMock()
        fake_tb.execute.return_value = "pong"

        # Build a fake FunctionTool constructor so we can check it's called.
        created_tools = []

        class FakeFunctionTool:
            def __init__(self, fn):
                self._fn = fn
                created_tools.append(self)

        agents_stub.FunctionTool = FakeFunctionTool

        import json as _json
        import sys as _sys

        _sys.modules["engine.tool_bridge"] = fake_tb

        tools_json = _json.dumps([{"name": "sub_agent", "description": "A2A sub-agent"}])

        with (
            patch.object(server, "_load_agent", return_value=fake_agent),
            patch.dict(
                "os.environ",
                {
                    "AGENT_TOOLS_JSON": tools_json,
                    "OPENAI_API_KEY": "sk-dummy",
                },
            ),
        ):
            await server.startup()

        # FunctionTool was instantiated for the sub_agent tool.
        assert len(created_tools) == 1
        # The tool was appended to agent.tools.
        assert len(fake_agent.tools) == 1
        assert fake_agent.tools[0] is created_tools[0]
        # _a2a_tools_registered flag should be True.
        assert server._a2a_tools_registered is True

        _sys.modules.pop("engine.tool_bridge", None)

    @pytest.mark.asyncio
    async def test_a2a_tool_fn_calls_tool_bridge_execute(self, agents_stub):
        """The FunctionTool callable should forward calls to tool_bridge.execute."""
        server = _import_server(agents_stub)

        fake_agent = MagicMock()
        fake_agent.tools = []

        fake_tb = MagicMock()
        fake_tb.execute.return_value = "result data"

        captured_fns = []

        class FakeFunctionTool:
            def __init__(self, fn):
                self._fn = fn
                captured_fns.append(fn)

        agents_stub.FunctionTool = FakeFunctionTool

        import json as _json
        import sys as _sys

        _sys.modules["engine.tool_bridge"] = fake_tb

        tools_json = _json.dumps([{"name": "my_tool", "description": "Does a thing"}])

        with (
            patch.object(server, "_load_agent", return_value=fake_agent),
            patch.dict(
                "os.environ",
                {
                    "AGENT_TOOLS_JSON": tools_json,
                    "OPENAI_API_KEY": "sk-dummy",
                },
            ),
        ):
            await server.startup()

        assert len(captured_fns) == 1
        fn = captured_fns[0]
        # Call the wrapped function — it should delegate to tool_bridge.execute.
        output = fn(input="test input")
        fake_tb.execute.assert_called_once_with("my_tool", {"input": "test input"})
        assert output == "result data"

        _sys.modules.pop("engine.tool_bridge", None)

    @pytest.mark.asyncio
    async def test_a2a_tool_fn_returns_error_string_on_execute_failure(self, agents_stub):
        """If tool_bridge.execute raises, the fn returns an error string (does not crash)."""
        server = _import_server(agents_stub)

        fake_agent = MagicMock()
        fake_agent.tools = []

        fake_tb = MagicMock()
        fake_tb.execute.side_effect = KeyError("no endpoint")

        captured_fns = []

        class FakeFunctionTool:
            def __init__(self, fn):
                captured_fns.append(fn)

        agents_stub.FunctionTool = FakeFunctionTool

        import json as _json
        import sys as _sys

        _sys.modules["engine.tool_bridge"] = fake_tb

        tools_json = _json.dumps([{"name": "broken_tool"}])

        with (
            patch.object(server, "_load_agent", return_value=fake_agent),
            patch.dict(
                "os.environ",
                {
                    "AGENT_TOOLS_JSON": tools_json,
                    "OPENAI_API_KEY": "sk-dummy",
                },
            ),
        ):
            await server.startup()

        assert len(captured_fns) == 1
        result = captured_fns[0](input="anything")
        assert "Error calling" in result

        _sys.modules.pop("engine.tool_bridge", None)

    @pytest.mark.asyncio
    async def test_a2a_tools_not_registered_when_agent_has_no_tools_list(self, agents_stub):
        """If the agent has no mutable .tools list, registration is skipped gracefully."""
        server = _import_server(agents_stub)

        # Agent without a list tools attribute.
        fake_agent = MagicMock(spec=["name"])  # no 'tools' attribute

        fake_tb = MagicMock()
        fake_tb.execute.return_value = "x"

        class FakeFunctionTool:
            def __init__(self, fn):
                pass

        agents_stub.FunctionTool = FakeFunctionTool

        import json as _json
        import sys as _sys

        _sys.modules["engine.tool_bridge"] = fake_tb

        tools_json = _json.dumps([{"name": "tool_a"}])

        with (
            patch.object(server, "_load_agent", return_value=fake_agent),
            patch.dict(
                "os.environ",
                {
                    "AGENT_TOOLS_JSON": tools_json,
                    "OPENAI_API_KEY": "sk-dummy",
                },
            ),
        ):
            # Must not raise.
            await server.startup()

        _sys.modules.pop("engine.tool_bridge", None)

    @pytest.mark.asyncio
    async def test_empty_tools_json_skips_a2a_registration(self, agents_stub):
        """When AGENT_TOOLS_JSON is empty ([]), no FunctionTool is created."""
        server = _import_server(agents_stub)

        fake_agent = MagicMock()
        fake_agent.tools = []

        tool_created = []

        class FakeFunctionTool:
            def __init__(self, fn):
                tool_created.append(fn)

        agents_stub.FunctionTool = FakeFunctionTool

        import sys as _sys

        fake_tb = MagicMock()
        _sys.modules["engine.tool_bridge"] = fake_tb

        with (
            patch.object(server, "_load_agent", return_value=fake_agent),
            patch.dict(
                "os.environ",
                {
                    "AGENT_TOOLS_JSON": "[]",
                    "OPENAI_API_KEY": "sk-dummy",
                },
            ),
        ):
            await server.startup()

        assert tool_created == []

        _sys.modules.pop("engine.tool_bridge", None)


# ---------------------------------------------------------------------------
# Structured tool-call history (#215)
# ---------------------------------------------------------------------------


class TestInvokeHistory:
    """The /invoke response should include a top-level `history` field with one
    ToolCall entry per ToolCallItem/ToolCallOutputItem pair in result.new_items.
    """

    def _make_run_result(self, final_output: str, new_items=None):
        result = MagicMock()
        result.final_output = final_output
        result.new_items = new_items or []
        return result

    def test_history_field_present_and_empty_when_no_tool_calls(self, agents_stub):
        run_result = self._make_run_result("hi", new_items=[])
        server = _import_server(agents_stub)
        server._agent = MagicMock()
        agents_stub.Runner.run = AsyncMock(return_value=run_result)

        client = TestClient(server.app)
        response = client.post("/invoke", json={"input": "hi"})

        assert response.status_code == 200
        data = response.json()
        assert "history" in data
        assert data["history"] == []

    def test_history_pairs_tool_call_with_output(self, agents_stub):
        """ToolCallItem + ToolCallOutputItem with matching call_id pair into one ToolCall."""
        # Build a fake ToolCallItem (raw_item.type == "function_call") and
        # a matching ToolCallOutputItem (raw_item.type == "function_call_output").
        tool_call = MagicMock(spec=["raw_item"])
        tool_call.__class__.__name__ = "ToolCallItem"
        tool_call.raw_item = MagicMock(spec=["type", "name", "arguments", "call_id"])
        tool_call.raw_item.type = "function_call"
        tool_call.raw_item.name = "search"
        tool_call.raw_item.arguments = '{"query": "AI"}'
        tool_call.raw_item.call_id = "call_1"

        tool_output = MagicMock(spec=["raw_item"])
        tool_output.__class__.__name__ = "ToolCallOutputItem"
        tool_output.raw_item = MagicMock(spec=["type", "output", "call_id"])
        tool_output.raw_item.type = "function_call_output"
        tool_output.raw_item.output = "result data"
        tool_output.raw_item.call_id = "call_1"

        run_result = self._make_run_result("Done.", new_items=[tool_call, tool_output])
        server = _import_server(agents_stub)
        server._agent = MagicMock()
        agents_stub.Runner.run = AsyncMock(return_value=run_result)

        client = TestClient(server.app)
        response = client.post("/invoke", json={"input": "search for AI"})

        assert response.status_code == 200
        history = response.json()["history"]
        assert len(history) == 1
        assert history[0]["name"] == "search"
        assert history[0]["args"] == {"query": "AI"}
        assert history[0]["result"] == "result data"
