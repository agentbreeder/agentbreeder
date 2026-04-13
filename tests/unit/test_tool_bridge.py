"""Unit tests for engine/tool_bridge.py -- all three framework adapters."""

from __future__ import annotations

import json
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal ToolRef stand-in so tests don't depend on the full engine package.
# ---------------------------------------------------------------------------


class _ToolRef:
    """Minimal ToolRef mimic for bridge tests."""

    def __init__(
        self,
        *,
        ref: str | None = None,
        name: str | None = None,
        type: str | None = None,
        description: str | None = None,
        schema_: dict[str, Any] | None = None,
    ) -> None:
        self.ref = ref
        self.name = name
        self.type = type
        self.description = description
        self.schema_ = schema_


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_bridge():
    """Import (or re-import) the tool_bridge module cleanly."""
    for key in list(sys.modules.keys()):
        if "tool_bridge" in key:
            del sys.modules[key]
    import engine.tool_bridge as bridge  # noqa: PLC0415
    return bridge


# ---------------------------------------------------------------------------
# _ref_to_slug
# ---------------------------------------------------------------------------


class TestRefToSlug:
    def test_simple_name(self):
        bridge = _import_bridge()
        assert bridge._ref_to_slug("search") == "SEARCH"

    def test_slash_and_hyphen(self):
        bridge = _import_bridge()
        assert bridge._ref_to_slug("tools/zendesk-mcp") == "TOOLS_ZENDESK_MCP"

    def test_all_special_chars(self):
        bridge = _import_bridge()
        assert bridge._ref_to_slug("my.tool:v2") == "MY_TOOL_V2"


# ---------------------------------------------------------------------------
# to_claude_tools
# ---------------------------------------------------------------------------


class TestToClaudeTools:
    def test_empty_list(self):
        bridge = _import_bridge()
        assert bridge.to_claude_tools([]) == []

    def test_ref_only_tool(self):
        bridge = _import_bridge()
        tool = _ToolRef(ref="tools/zendesk-mcp", description="Zendesk integration")
        result = bridge.to_claude_tools([tool])

        assert len(result) == 1
        tp = result[0]
        assert tp["name"] == "zendesk-mcp"
        assert tp["description"] == "Zendesk integration"
        assert tp["input_schema"]["type"] == "object"
        assert tp["input_schema"]["properties"] == {}

    def test_inline_tool_with_schema(self):
        bridge = _import_bridge()
        schema = {
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        tool = _ToolRef(name="search", description="Search KB", schema_=schema)
        result = bridge.to_claude_tools([tool])

        assert len(result) == 1
        tp = result[0]
        assert tp["name"] == "search"
        assert tp["input_schema"]["properties"] == {"query": {"type": "string"}}
        assert tp["input_schema"]["required"] == ["query"]

    def test_tool_with_no_description_gets_default(self):
        bridge = _import_bridge()
        tool = _ToolRef(ref="tools/order-lookup")
        result = bridge.to_claude_tools([tool])
        assert result[0]["description"] == "Tool: order-lookup"

    def test_tool_name_sanitised(self):
        """Names with chars outside [a-zA-Z0-9_-] are replaced with underscores."""
        bridge = _import_bridge()
        tool = _ToolRef(name="my tool v2!", description="desc")
        result = bridge.to_claude_tools([tool])
        assert result[0]["name"] == "my_tool_v2_"

    def test_tool_name_truncated_to_64_chars(self):
        bridge = _import_bridge()
        tool = _ToolRef(name="a" * 100, description="desc")
        result = bridge.to_claude_tools([tool])
        assert len(result[0]["name"]) == 64

    def test_tool_with_no_name_and_no_ref_is_skipped(self):
        bridge = _import_bridge()
        tool = _ToolRef(description="orphan")
        result = bridge.to_claude_tools([tool])
        assert result == []

    def test_multiple_tools(self):
        bridge = _import_bridge()
        tools = [
            _ToolRef(ref="tools/zendesk-mcp"),
            _ToolRef(name="search"),
            _ToolRef(ref="tools/order-lookup", description="Order lookup"),
        ]
        result = bridge.to_claude_tools(tools)
        assert len(result) == 3
        names = [t["name"] for t in result]
        assert "zendesk-mcp" in names
        assert "search" in names
        assert "order-lookup" in names


# ---------------------------------------------------------------------------
# to_crewai_tools
# ---------------------------------------------------------------------------


class TestToCrewaiTools:
    def _make_mock_crewai(self):
        """Return fake crewai module stubs with a minimal BaseTool."""

        class FakeBaseTool:
            name: str = ""
            description: str = ""
            args_schema: Any = None

            def _run(self, **kwargs: Any) -> str:
                return ""

        fake_crewai_tools = types.ModuleType("crewai.tools")
        fake_crewai_tools.BaseTool = FakeBaseTool  # type: ignore[attr-defined]
        fake_crewai = types.ModuleType("crewai")
        return fake_crewai, fake_crewai_tools

    def test_returns_empty_when_crewai_missing(self):
        with patch.dict(sys.modules, {"crewai": None, "crewai.tools": None}):
            bridge = _import_bridge()
            result = bridge.to_crewai_tools([_ToolRef(ref="tools/search")])
        assert result == []

    def test_skips_tool_with_no_endpoint(self, monkeypatch):
        """Tool without TOOL_ENDPOINT_* env var is skipped, no crash."""
        monkeypatch.delenv("TOOL_ENDPOINT_TOOLS_SEARCH", raising=False)
        fake_crewai, fake_crewai_tools = self._make_mock_crewai()
        with patch.dict(sys.modules, {"crewai": fake_crewai, "crewai.tools": fake_crewai_tools}):
            bridge = _import_bridge()
            result = bridge.to_crewai_tools([_ToolRef(ref="tools/search")])
        assert result == []

    def test_creates_tool_instance_when_endpoint_set(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_TOOLS_ZENDESK_MCP", "http://tool-host/zendesk")
        fake_crewai, fake_crewai_tools = self._make_mock_crewai()
        with patch.dict(sys.modules, {"crewai": fake_crewai, "crewai.tools": fake_crewai_tools}):
            bridge = _import_bridge()
            result = bridge.to_crewai_tools(
                [_ToolRef(ref="tools/zendesk-mcp", description="Zendesk")]
            )
        assert len(result) == 1
        assert result[0].name == "zendesk_mcp"
        assert result[0].description == "Zendesk"

    def test_run_posts_to_endpoint(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_SEARCH", "http://tool-host/search")
        fake_crewai, fake_crewai_tools = self._make_mock_crewai()
        mock_response = MagicMock()
        mock_response.json.return_value = {"output": "found 3 results"}
        mock_response.raise_for_status = MagicMock()

        with patch.dict(sys.modules, {"crewai": fake_crewai, "crewai.tools": fake_crewai_tools}):
            bridge = _import_bridge()
            with patch("httpx.post", return_value=mock_response) as mock_post:
                result = bridge.to_crewai_tools([_ToolRef(name="search", description="Search")])
                assert len(result) == 1
                output = result[0]._run(input="hello world")

        mock_post.assert_called_once_with(
            "http://tool-host/search",
            json={"input": "hello world"},
            timeout=30.0,
        )
        assert output == "found 3 results"

    def test_run_returns_error_string_on_http_failure(self, monkeypatch):
        import httpx as real_httpx
        monkeypatch.setenv("TOOL_ENDPOINT_SEARCH", "http://tool-host/search")
        fake_crewai, fake_crewai_tools = self._make_mock_crewai()

        with patch.dict(sys.modules, {"crewai": fake_crewai, "crewai.tools": fake_crewai_tools}):
            bridge = _import_bridge()
            with patch("httpx.post", side_effect=real_httpx.ConnectError("refused")):
                result = bridge.to_crewai_tools([_ToolRef(name="search")])
                output = result[0]._run(input="test")

        assert "Error calling tool" in output
        assert "search" in output

    def test_schema_props_become_args_schema_fields(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_LOOKUP", "http://tool-host/lookup")
        fake_crewai, fake_crewai_tools = self._make_mock_crewai()
        schema = {
            "properties": {
                "order_id": {"type": "string"},
                "region": {"type": "string"},
            }
        }
        with patch.dict(sys.modules, {"crewai": fake_crewai, "crewai.tools": fake_crewai_tools}):
            bridge = _import_bridge()
            result = bridge.to_crewai_tools([_ToolRef(name="lookup", schema_=schema)])
        assert len(result) == 1
        fields = result[0].args_schema.__annotations__
        assert "order_id" in fields
        assert "region" in fields


# ---------------------------------------------------------------------------
# to_adk_tools
# ---------------------------------------------------------------------------


class TestToAdkTools:
    def test_empty_list(self):
        bridge = _import_bridge()
        assert bridge.to_adk_tools([]) == []

    def test_skips_tool_with_no_endpoint(self, monkeypatch):
        monkeypatch.delenv("TOOL_ENDPOINT_TOOLS_SEARCH", raising=False)
        bridge = _import_bridge()
        result = bridge.to_adk_tools([_ToolRef(ref="tools/search")])
        assert result == []

    def test_returns_callable_when_endpoint_set(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_TOOLS_ZENDESK_MCP", "http://tool-host/zendesk")
        bridge = _import_bridge()
        result = bridge.to_adk_tools(
            [_ToolRef(ref="tools/zendesk-mcp", description="Zendesk")]
        )
        assert len(result) == 1
        assert callable(result[0])

    def test_function_name_is_safe_identifier(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_ORDER_LOOKUP", "http://tool-host/order")
        bridge = _import_bridge()
        result = bridge.to_adk_tools([_ToolRef(ref="tools/order-lookup")])
        assert result[0].__name__ == "order_lookup"

    def test_function_docstring_is_description(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_SEARCH", "http://tool-host/search")
        bridge = _import_bridge()
        result = bridge.to_adk_tools([_ToolRef(name="search", description="Search the KB")])
        assert result[0].__doc__ == "Search the KB"

    def test_function_default_doc_when_no_description(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_SEARCH", "http://tool-host/search")
        bridge = _import_bridge()
        result = bridge.to_adk_tools([_ToolRef(name="search")])
        assert "search" in result[0].__doc__.lower()

    def test_schema_props_become_params(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_LOOKUP", "http://tool-host/lookup")
        bridge = _import_bridge()
        schema = {
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        }
        result = bridge.to_adk_tools([_ToolRef(name="lookup", schema_=schema)])
        import inspect
        sig = inspect.signature(result[0])
        assert "order_id" in sig.parameters

    def test_no_schema_gets_single_input_param(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_SEARCH", "http://tool-host/search")
        bridge = _import_bridge()
        result = bridge.to_adk_tools([_ToolRef(name="search")])
        import inspect
        sig = inspect.signature(result[0])
        assert "input" in sig.parameters

    @pytest.mark.asyncio
    async def test_callable_posts_to_endpoint(self, monkeypatch):
        monkeypatch.setenv("TOOL_ENDPOINT_SEARCH", "http://tool-host/search")
        bridge = _import_bridge()
        result = bridge.to_adk_tools([_ToolRef(name="search")])
        fn = result[0]

        mock_response = MagicMock()
        mock_response.json.return_value = {"output": "3 results"}
        mock_response.raise_for_status = MagicMock()
        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_async_client):
            output = await fn(input="hello")

        mock_async_client.post.assert_called_once_with(
            "http://tool-host/search", json={"input": "hello"}
        )
        assert output == "3 results"


# ---------------------------------------------------------------------------
# Integration: claude_sdk_server startup wires tools
# ---------------------------------------------------------------------------


class TestClaudeSdkServerToolWiring:
    """Verify claude_sdk_server reads AGENT_TOOLS_JSON and populates _tools."""

    def _import_server(self, monkeypatch, tools_json: str = "[]"):
        for key in list(sys.modules.keys()):
            if "claude_sdk_server" in key:
                del sys.modules[key]
        monkeypatch.setenv("AGENT_TOOLS_JSON", tools_json)

        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.AsyncAnthropic = MagicMock  # type: ignore[attr-defined]
        fake_anthropic.Anthropic = MagicMock  # type: ignore[attr-defined]

        # Stub the bridge and config_parser so the server can import them.
        fake_engine_tb = types.ModuleType("engine.tool_bridge")
        fake_engine_tb.to_claude_tools = lambda refs: [  # type: ignore[attr-defined]
            {
                "name": (r.name or (r.ref or "").split("/")[-1]),
                "description": "",
                "input_schema": {"type": "object", "properties": {}},
            }
            for r in refs
        ]
        fake_engine_cp = types.ModuleType("engine.config_parser")
        fake_engine_cp.ToolRef = _ToolRef  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "anthropic": fake_anthropic,
                "engine.tool_bridge": fake_engine_tb,
                "engine.config_parser": fake_engine_cp,
            },
        ):
            sys.path.insert(0, "engine/runtimes/templates")
            import claude_sdk_server as srv  # noqa: PLC0415
        return srv

    def test_empty_tools_json_leaves_tools_empty(self, monkeypatch):
        srv = self._import_server(monkeypatch, "[]")
        assert srv._tools == []

    def test_valid_tools_json_populates_tools(self, monkeypatch):
        import asyncio
        tools_payload = json.dumps([
            {"ref": "tools/zendesk-mcp", "description": "Zendesk"},
            {"name": "search", "description": "Search"},
        ])
        srv = self._import_server(monkeypatch, tools_payload)
        asyncio.get_event_loop().run_until_complete(srv.startup())
        assert len(srv._tools) == 2

    def test_malformed_tools_json_does_not_crash(self, monkeypatch):
        import asyncio
        srv = self._import_server(monkeypatch, "{not valid json}")
        asyncio.get_event_loop().run_until_complete(srv.startup())
        assert srv._tools == []


# ---------------------------------------------------------------------------
# Integration: crewai_server startup wires tools
# ---------------------------------------------------------------------------


class TestCrewAiServerToolWiring:
    def _import_server(self, monkeypatch, tools_json: str = "[]"):
        for key in list(sys.modules.keys()):
            if "crewai_server" in key:
                del sys.modules[key]
        monkeypatch.setenv("AGENT_TOOLS_JSON", tools_json)

        class FakeBaseTool:
            name: str = "fake"
            description: str = ""
            args_schema: Any = None

            def _run(self, **kwargs: Any) -> str:
                return ""

        fake_crewai_tools_mod = types.ModuleType("crewai.tools")
        fake_crewai_tools_mod.BaseTool = FakeBaseTool  # type: ignore[attr-defined]
        fake_crewai_mod = types.ModuleType("crewai")

        fake_engine_tb = types.ModuleType("engine.tool_bridge")
        fake_engine_tb.to_crewai_tools = lambda refs: [FakeBaseTool() for _ in refs]  # type: ignore[attr-defined]
        fake_engine_cp = types.ModuleType("engine.config_parser")
        fake_engine_cp.ToolRef = _ToolRef  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "crewai": fake_crewai_mod,
                "crewai.tools": fake_crewai_tools_mod,
                "engine.tool_bridge": fake_engine_tb,
                "engine.config_parser": fake_engine_cp,
            },
        ):
            sys.path.insert(0, "engine/runtimes/templates")
            import crewai_server as srv  # noqa: PLC0415
        return srv

    def test_tools_injected_into_crew_agents(self, monkeypatch):
        import asyncio
        tools_payload = json.dumps([{"name": "search", "description": "Search"}])
        srv = self._import_server(monkeypatch, tools_payload)

        class FakeAgent:
            role = "researcher"
            tools: list = []

        class FakeCrew:
            agents = [FakeAgent()]

        srv._crew = FakeCrew()
        asyncio.get_event_loop().run_until_complete(srv.startup())

        assert len(srv._crewai_tools) == 1
        assert len(FakeCrew.agents[0].tools) == 1

    def test_empty_tools_json_no_injection(self, monkeypatch):
        import asyncio
        srv = self._import_server(monkeypatch, "[]")

        class FakeAgent:
            role = "researcher"
            tools: list = []

        class FakeCrew:
            agents = [FakeAgent()]

        srv._crew = FakeCrew()
        asyncio.get_event_loop().run_until_complete(srv.startup())
        assert srv._crewai_tools == []
        assert FakeCrew.agents[0].tools == []


# ---------------------------------------------------------------------------
# Integration: google_adk_server startup wires tools
# ---------------------------------------------------------------------------


class TestAdkServerToolWiring:
    def _import_server(self, monkeypatch, tools_json: str = "[]"):
        for key in list(sys.modules.keys()):
            if "google_adk_server" in key:
                del sys.modules[key]
        monkeypatch.setenv("AGENT_TOOLS_JSON", tools_json)

        fake_google = types.ModuleType("google")
        fake_adk = types.ModuleType("google.adk")
        fake_runners = types.ModuleType("google.adk.runners")
        fake_sessions = types.ModuleType("google.adk.sessions")

        class FakeRunner:
            def __init__(self, **kwargs: Any):
                pass

        class FakeSessionService:
            pass

        fake_runners.Runner = FakeRunner  # type: ignore[attr-defined]
        fake_sessions.InMemorySessionService = FakeSessionService  # type: ignore[attr-defined]

        fake_engine_tb = types.ModuleType("engine.tool_bridge")
        fake_engine_tb.to_adk_tools = lambda refs: [lambda input="": "result" for _ in refs]  # type: ignore[attr-defined]
        fake_engine_cp = types.ModuleType("engine.config_parser")
        fake_engine_cp.ToolRef = _ToolRef  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "google": fake_google,
                "google.adk": fake_adk,
                "google.adk.runners": fake_runners,
                "google.adk.sessions": fake_sessions,
                "google.genai": types.ModuleType("google.genai"),
                "google.genai.types": types.ModuleType("google.genai.types"),
                "engine.tool_bridge": fake_engine_tb,
                "engine.config_parser": fake_engine_cp,
            },
        ):
            sys.path.insert(0, "engine/runtimes/templates")
            import google_adk_server as srv  # noqa: PLC0415
        return srv

    def test_tools_injected_into_adk_agent(self, monkeypatch):
        import asyncio
        tools_payload = json.dumps([{"name": "search", "description": "Search"}])
        srv = self._import_server(monkeypatch, tools_payload)

        class FakeADKAgent:
            tools: list = []

        srv._agent = FakeADKAgent()
        asyncio.get_event_loop().run_until_complete(srv.startup())

        assert len(srv._adk_tools) == 1
        assert len(FakeADKAgent.tools) == 1

    def test_empty_tools_no_mutation(self, monkeypatch):
        import asyncio
        srv = self._import_server(monkeypatch, "[]")

        class FakeADKAgent:
            tools: list = []

        srv._agent = FakeADKAgent()
        asyncio.get_event_loop().run_until_complete(srv.startup())
        assert srv._adk_tools == []
        assert FakeADKAgent.tools == []
