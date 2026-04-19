"""Unit tests targeting uncovered lines in claude_sdk_server.py.

Targets:
  lines 65-74   _load_agent: AttributeError when no recognised export
  line  129     _call_client: system prompt with prompt caching disabled (short prompt)
  line  147     startup: tools JSON parse exception path
  line  159     startup: _load_agent raises AttributeError path
  lines 168-170 startup: ImportError importing anthropic in startup
  line  175     health: returns "loading" when _agent is None
  lines 184-192 invoke: 503 and 500 error paths
  lines 197-244 _run_agent: all dispatch branches (AsyncAnthropic, Anthropic sync,
                async callable, async run(), unsupported type)
  line  267     _stream_sse: system prompt branch
  line  286     _extract_text: no text block returns ""
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_server():
    """Re-import claude_sdk_server cleanly, injecting a fake anthropic stub."""
    for key in list(sys.modules.keys()):
        if "claude_sdk_server" in key:
            del sys.modules[key]

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = type("AsyncAnthropic", (), {})
    fake_anthropic.Anthropic = type("Anthropic", (), {})
    sys.modules["anthropic"] = fake_anthropic

    sys.path.insert(0, "engine/runtimes/templates")
    import claude_sdk_server as srv  # noqa: PLC0415

    return srv


def _make_response(text: str = "reply") -> MagicMock:
    """Build a fake Anthropic messages response."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


# ---------------------------------------------------------------------------
# _load_agent — AttributeError when no recognised export (lines 65-74)
# ---------------------------------------------------------------------------


class TestLoadAgentAttributeError:
    def test_raises_attribute_error_when_no_recognised_export(self):
        srv = _import_server()

        fake_module = types.ModuleType("agent")
        # No 'agent', 'app', or 'client' attributes.
        fake_module.something_else = "irrelevant"  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"agent": fake_module}):
            with pytest.raises(AttributeError, match="must export one of"):
                srv._load_agent()

    def test_raises_import_error_when_module_missing(self):
        srv = _import_server()

        # Ensure 'agent' cannot be imported.
        with patch.dict(sys.modules, {"agent": None}):  # type: ignore[dict-item]
            with pytest.raises((ImportError, AttributeError)):
                srv._load_agent()

    def test_returns_agent_attr_when_present(self):
        srv = _import_server()

        fake_obj = object()
        fake_module = types.ModuleType("agent")
        fake_module.agent = fake_obj  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"agent": fake_module}):
            result = srv._load_agent()

        assert result is fake_obj

    def test_returns_client_attr_when_agent_missing_but_client_present(self):
        srv = _import_server()

        fake_client = object()
        fake_module = types.ModuleType("agent")
        fake_module.client = fake_client  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"agent": fake_module}):
            result = srv._load_agent()

        assert result is fake_client


# ---------------------------------------------------------------------------
# _call_client — short system prompt skips caching (line 129)
# ---------------------------------------------------------------------------


class TestCallClientSystemPrompt:
    @pytest.mark.asyncio
    async def test_short_system_prompt_not_cached(self, monkeypatch):
        """When prompt caching is enabled but prompt is short, use plain string."""
        srv = _import_server()
        srv._prompt_caching_enabled = True
        srv._thinking_config = None
        srv._tools = []

        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return _make_response("ok")

        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = fake_create

        monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("AGENT_MAX_TOKENS", "512")

        # Short prompt — well below the 8192 threshold for sonnet
        short_prompt = "Be helpful."
        await srv._call_client(fake_client, "claude-sonnet-4-6", short_prompt, [])

        # system must be the plain string, NOT a list with cache_control
        assert captured["system"] == short_prompt

    @pytest.mark.asyncio
    async def test_long_system_prompt_cached_for_sonnet(self, monkeypatch):
        """When caching enabled and prompt >= 8192 chars, wrap with cache_control."""
        srv = _import_server()
        srv._prompt_caching_enabled = True
        srv._thinking_config = None
        srv._tools = []

        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return _make_response("ok")

        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = fake_create

        long_prompt = "x" * 9000  # > 8192
        await srv._call_client(fake_client, "claude-sonnet-4-6", long_prompt, [])

        assert isinstance(captured["system"], list)
        assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_no_system_key_when_prompt_empty(self, monkeypatch):
        """When system_prompt is empty, 'system' must not appear in kwargs."""
        srv = _import_server()
        srv._prompt_caching_enabled = False
        srv._thinking_config = None
        srv._tools = []

        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return _make_response("ok")

        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = fake_create

        await srv._call_client(fake_client, "claude-sonnet-4-6", "", [])

        assert "system" not in captured

    @pytest.mark.asyncio
    async def test_tools_added_to_kwargs(self, monkeypatch):
        """When _tools is populated, they appear in the API call."""
        srv = _import_server()
        srv._prompt_caching_enabled = False
        srv._thinking_config = None
        srv._tools = [{"name": "search"}]

        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return _make_response("ok")

        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = fake_create

        await srv._call_client(fake_client, "claude-sonnet-4-6", "", [])

        assert captured["tools"] == [{"name": "search"}]


# ---------------------------------------------------------------------------
# startup — exception paths (lines 147, 159, 168-170)
# ---------------------------------------------------------------------------


class TestStartupExceptionPaths:
    @pytest.mark.asyncio
    async def test_tools_json_parse_error_falls_back_to_empty(self, monkeypatch):
        """Invalid JSON in AGENT_TOOLS_JSON must set _tools=[] without crashing."""
        monkeypatch.setenv("AGENT_TOOLS_JSON", "NOT_VALID_JSON")
        srv = _import_server()

        fake_agent_module = types.ModuleType("agent")
        fake_agent_module.agent = object()  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"agent": fake_agent_module}):
            await srv.startup()

        assert srv._tools == []
        srv._agent = None

    @pytest.mark.asyncio
    async def test_load_agent_attribute_error_sets_agent_none(self, monkeypatch):
        """When _load_agent raises AttributeError, _agent stays None (line 159)."""
        monkeypatch.delenv("AGENT_TOOLS_JSON", raising=False)
        srv = _import_server()

        # No 'agent'/'app'/'client' on the module triggers AttributeError in _load_agent
        fake_module = types.ModuleType("agent")
        with patch.dict(sys.modules, {"agent": fake_module}):
            await srv.startup()

        # Server must survive and _agent remain None
        assert srv._agent is None

    @pytest.mark.asyncio
    async def test_import_error_for_anthropic_during_startup(self, monkeypatch):
        """ImportError when importing anthropic in startup must not crash (lines 168-170)."""
        monkeypatch.delenv("AGENT_TOOLS_JSON", raising=False)
        srv = _import_server()

        fake_agent = object()
        fake_module = types.ModuleType("agent")
        fake_module.agent = fake_agent  # type: ignore[attr-defined]

        # Make anthropic non-importable during startup
        with patch.dict(sys.modules, {"agent": fake_module, "anthropic": None}):  # type: ignore[dict-item]
            await srv.startup()

        # _agent was set from the module, but _client stays None due to ImportError
        assert srv._client is None


# ---------------------------------------------------------------------------
# health — "loading" when _agent is None (line 175)
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_loading_when_agent_none(self):
        srv = _import_server()
        srv._agent = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "loading"

    @pytest.mark.asyncio
    async def test_health_healthy_when_agent_set(self):
        srv = _import_server()
        srv._agent = object()

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        srv._agent = None


# ---------------------------------------------------------------------------
# /invoke — 503 and 500 paths (lines 184-192)
# ---------------------------------------------------------------------------


class TestInvokeEndpoint:
    @pytest.mark.asyncio
    async def test_invoke_503_when_agent_none(self):
        srv = _import_server()
        srv._agent = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": "hello"})

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_invoke_500_when_run_agent_raises(self):
        srv = _import_server()

        # Agent object with an async run() that raises
        class BrokenAgent:
            async def run(self, _):
                raise ValueError("boom")

        srv._agent = BrokenAgent()

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": "hello"})

        assert response.status_code == 500
        assert "boom" in response.json()["detail"]
        srv._agent = None

    @pytest.mark.asyncio
    async def test_invoke_returns_output_on_success(self):
        srv = _import_server()

        class GoodAgent:
            async def run(self, text: str) -> str:
                return f"echo:{text}"

        srv._agent = GoodAgent()

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": "ping"})

        assert response.status_code == 200
        assert response.json()["output"] == "echo:ping"
        srv._agent = None


# ---------------------------------------------------------------------------
# _run_agent — all dispatch branches (lines 197-244)
# ---------------------------------------------------------------------------


class TestRunAgentDispatch:
    """Test _run_agent() dispatch for every supported agent type."""

    def _setup_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("AGENT_SYSTEM_PROMPT", "")

    @pytest.mark.asyncio
    async def test_async_anthropic_client_dispatch(self, monkeypatch):
        """AsyncAnthropic client branch (lines 204-210)."""
        self._setup_env(monkeypatch)
        srv = _import_server()
        import anthropic

        mock_resp = _make_response("async-result")
        mock_client = MagicMock(spec=anthropic.AsyncAnthropic)
        mock_client.__class__ = anthropic.AsyncAnthropic
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        srv._agent = mock_client
        srv._tools = []

        result = await srv._run_agent("hello")
        assert result == "async-result"
        srv._agent = None

    @pytest.mark.asyncio
    async def test_async_anthropic_with_system_prompt(self, monkeypatch):
        """AsyncAnthropic branch includes system in kwargs when prompt set."""
        monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("AGENT_SYSTEM_PROMPT", "Be concise.")
        srv = _import_server()
        import anthropic

        captured: dict = {}
        mock_resp = _make_response("reply")

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return mock_resp

        mock_client = MagicMock(spec=anthropic.AsyncAnthropic)
        mock_client.__class__ = anthropic.AsyncAnthropic
        mock_client.messages = MagicMock()
        mock_client.messages.create = fake_create

        srv._agent = mock_client
        srv._tools = []

        await srv._run_agent("question")
        assert captured["system"] == "Be concise."
        srv._agent = None

    @pytest.mark.asyncio
    async def test_sync_anthropic_client_dispatch(self, monkeypatch):
        """Sync Anthropic client branch — runs in thread (lines 213-222)."""
        self._setup_env(monkeypatch)
        srv = _import_server()
        import anthropic

        mock_resp = _make_response("sync-result")
        mock_client = MagicMock(spec=anthropic.Anthropic)
        mock_client.__class__ = anthropic.Anthropic
        mock_client.messages = MagicMock()
        mock_client.messages.create = MagicMock(return_value=mock_resp)

        srv._agent = mock_client
        srv._tools = []

        result = await srv._run_agent("hi")
        assert result == "sync-result"
        srv._agent = None

    @pytest.mark.asyncio
    async def test_sync_anthropic_with_tools_and_prompt(self, monkeypatch):
        """Sync Anthropic branch passes tools and system when configured."""
        monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("AGENT_SYSTEM_PROMPT", "Help!")
        srv = _import_server()
        import anthropic

        captured: dict = {}
        mock_resp = _make_response("reply")
        mock_client = MagicMock(spec=anthropic.Anthropic)
        mock_client.__class__ = anthropic.Anthropic
        mock_client.messages = MagicMock()
        mock_client.messages.create = MagicMock(
            side_effect=lambda **kw: captured.update(kw) or mock_resp
        )

        srv._agent = mock_client
        srv._tools = [{"name": "lookup"}]

        await srv._run_agent("query")
        assert captured["system"] == "Help!"
        assert captured["tools"] == [{"name": "lookup"}]
        srv._agent = None
        srv._tools = []

    @pytest.mark.asyncio
    async def test_async_callable_dispatch(self, monkeypatch):
        """Async callable branch (lines 225-226)."""
        self._setup_env(monkeypatch)
        srv = _import_server()

        async def my_callable(text: str) -> str:
            return f"callable:{text}"

        srv._agent = my_callable
        result = await srv._run_agent("ping")
        assert result == "callable:ping"
        srv._agent = None

    @pytest.mark.asyncio
    async def test_async_run_method_dispatch(self, monkeypatch):
        """Object with async run() method (lines 229-230)."""
        self._setup_env(monkeypatch)
        srv = _import_server()

        class MyAgent:
            async def run(self, text: str) -> str:
                return f"run:{text}"

        srv._agent = MyAgent()
        result = await srv._run_agent("input")
        assert result == "run:input"
        srv._agent = None

    @pytest.mark.asyncio
    async def test_unsupported_agent_type_raises_type_error(self, monkeypatch):
        """Unsupported agent object raises TypeError (lines 232-244)."""
        self._setup_env(monkeypatch)
        srv = _import_server()

        # A plain object with no recognised interface
        srv._agent = object()

        with pytest.raises(TypeError, match="not a supported type"):
            await srv._run_agent("hello")

        srv._agent = None

    @pytest.mark.asyncio
    async def test_sync_callable_not_dispatched_via_async_callable_branch(self, monkeypatch):
        """A sync (non-coroutine) callable must NOT match the async callable branch."""
        self._setup_env(monkeypatch)
        srv = _import_server()

        def sync_fn(text: str) -> str:
            return "sync"

        srv._agent = sync_fn

        # sync_fn is callable but iscoroutinefunction is False, and it has no run() method,
        # so _run_agent should fall through to the TypeError branch.
        with pytest.raises(TypeError):
            await srv._run_agent("hi")

        srv._agent = None


# ---------------------------------------------------------------------------
# _stream_sse — system_prompt branch (line 267)
# ---------------------------------------------------------------------------


class TestStreamSSESystemPrompt:
    @pytest.mark.asyncio
    async def test_stream_sse_includes_system_prompt_in_kwargs(self, monkeypatch):
        """When AGENT_SYSTEM_PROMPT is set, it appears in messages.stream() kwargs."""
        monkeypatch.setenv("AGENT_SYSTEM_PROMPT", "You are helpful.")
        monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("AGENT_MAX_TOKENS", "512")
        srv = _import_server()
        import anthropic

        captured: dict = {}

        class FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            @property
            def text_stream(self):
                async def _gen():
                    return
                    yield  # pragma: no cover

                return _gen()

        def fake_stream_fn(**kwargs):
            captured.update(kwargs)
            return FakeStream()

        mock_client = MagicMock()
        mock_client.__class__ = anthropic.AsyncAnthropic
        mock_client.messages = MagicMock()
        mock_client.messages.stream = fake_stream_fn

        srv._agent = mock_client
        srv._client = mock_client
        srv._tools = []

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hello"})

        assert response.status_code == 200
        assert captured.get("system") == "You are helpful."
        srv._agent = None
        srv._client = None

    @pytest.mark.asyncio
    async def test_stream_sse_no_system_key_when_prompt_empty(self, monkeypatch):
        """When AGENT_SYSTEM_PROMPT is empty, 'system' must NOT appear in kwargs."""
        monkeypatch.setenv("AGENT_SYSTEM_PROMPT", "")
        monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-4-6")
        srv = _import_server()
        import anthropic

        captured: dict = {}

        class FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            @property
            def text_stream(self):
                async def _gen():
                    return
                    yield  # pragma: no cover

                return _gen()

        def fake_stream_fn(**kwargs):
            captured.update(kwargs)
            return FakeStream()

        mock_client = MagicMock()
        mock_client.__class__ = anthropic.AsyncAnthropic
        mock_client.messages = MagicMock()
        mock_client.messages.stream = fake_stream_fn

        srv._agent = mock_client
        srv._client = mock_client
        srv._tools = []

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/stream", json={"input": "hello"})

        assert "system" not in captured
        srv._agent = None
        srv._client = None


# ---------------------------------------------------------------------------
# _extract_text — no text block returns "" (line 286)
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_returns_empty_string_when_no_text_block(self):
        srv = _import_server()

        # Block with no 'text' attribute
        block = MagicMock(spec=[])  # spec=[] means no attributes
        resp = MagicMock()
        resp.content = [block]

        result = srv._extract_text(resp)
        assert result == ""

    def test_returns_first_text_block(self):
        srv = _import_server()

        block1 = MagicMock()
        block1.text = "first"
        block2 = MagicMock()
        block2.text = "second"

        resp = MagicMock()
        resp.content = [block1, block2]

        result = srv._extract_text(resp)
        assert result == "first"

    def test_returns_empty_string_for_empty_content(self):
        srv = _import_server()
        resp = MagicMock()
        resp.content = []
        assert srv._extract_text(resp) == ""


# ---------------------------------------------------------------------------
# _get_cache_threshold
# ---------------------------------------------------------------------------


class TestGetCacheThreshold:
    def test_sonnet_threshold_is_8192(self):
        srv = _import_server()
        assert srv._get_cache_threshold("claude-sonnet-4-6") == 8192
        assert srv._get_cache_threshold("claude-SONNET-latest") == 8192

    def test_non_sonnet_threshold_is_16384(self):
        srv = _import_server()
        assert srv._get_cache_threshold("claude-opus-4") == 16384
        assert srv._get_cache_threshold("claude-haiku-3") == 16384

    def test_thinking_config_applied(self):
        """_call_client applies thinking config when _thinking_config is set."""
        srv = _import_server()
        srv._thinking_config = {"type": "adaptive", "_effort": "high"}
        srv._tools = []
        srv._prompt_caching_enabled = False

        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return _make_response("ok")

        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = fake_create

        asyncio.run(srv._call_client(fake_client, "claude-sonnet-4-6", "", []))

        assert "thinking" in captured
        assert captured["thinking"]["type"] == "adaptive"
        assert "betas" in captured
        srv._thinking_config = None
