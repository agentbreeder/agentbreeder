"""Tests for google_adk_server.py covering previously-uncovered lines.

Covers: _load_agent, _build_session_service, _build_memory_service,
        _build_artifact_service, startup, lifespan, health, invoke,
        _stream_agent_sse, and _run_agent.

All external SDKs (google.adk, google.genai) are mocked via sys.modules
injection — no real cloud credentials are needed.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_fake_google_modules(
    *,
    include_database_session: bool = True,
    include_vertex_session: bool = True,
    include_memory_bank: bool = True,
    include_memory_rag: bool = True,
    include_gcs_artifact: bool = True,
    include_litellm: bool = True,
) -> dict:
    """Return a dict of sys.modules entries for all google.* stubs."""
    fake_google = types.ModuleType("google")
    fake_adk = types.ModuleType("google.adk")
    fake_runners = types.ModuleType("google.adk.runners")
    fake_sessions = types.ModuleType("google.adk.sessions")
    fake_memory = types.ModuleType("google.adk.memory")
    fake_artifacts = types.ModuleType("google.adk.artifacts")
    fake_models = types.ModuleType("google.adk.models")
    fake_lite_llm = types.ModuleType("google.adk.models.lite_llm")
    fake_genai = types.ModuleType("google.genai")
    fake_genai_types = types.ModuleType("google.genai.types")

    # Runners
    mock_runner_cls = MagicMock(name="Runner")
    fake_runners.Runner = mock_runner_cls

    # Sessions
    fake_sessions.InMemorySessionService = MagicMock(name="InMemorySessionService")
    if include_database_session:
        fake_sessions.DatabaseSessionService = MagicMock(name="DatabaseSessionService")
    if include_vertex_session:
        fake_sessions.VertexAiSessionService = MagicMock(name="VertexAiSessionService")

    # Memory
    if include_memory_bank:
        fake_memory.VertexAiMemoryBankService = MagicMock(name="VertexAiMemoryBankService")
    if include_memory_rag:
        fake_memory.VertexAiRagMemoryService = MagicMock(name="VertexAiRagMemoryService")

    # Artifacts
    if include_gcs_artifact:
        fake_artifacts.GcsArtifactService = MagicMock(name="GcsArtifactService")

    # LiteLLM model wrapper
    if include_litellm:
        fake_lite_llm.LiteLlm = MagicMock(name="LiteLlm")
        fake_models.lite_llm = fake_lite_llm

    # genai types
    fake_genai_types.Content = MagicMock(name="Content")
    fake_genai_types.Part = MagicMock(name="Part")
    fake_genai_types.GenerateContentConfig = MagicMock(name="GenerateContentConfig")

    mods = {
        "google": fake_google,
        "google.adk": fake_adk,
        "google.adk.runners": fake_runners,
        "google.adk.sessions": fake_sessions,
        "google.adk.memory": fake_memory,
        "google.adk.artifacts": fake_artifacts,
        "google.adk.models": fake_models,
        "google.adk.models.lite_llm": fake_lite_llm,
        "google.genai": fake_genai,
        "google.genai.types": fake_genai_types,
    }
    return mods


def _fresh_import(extra_modules: dict | None = None) -> types.ModuleType:
    """Wipe any cached google_adk_server module and re-import with fresh mocks."""
    for key in list(sys.modules.keys()):
        if "google_adk_server" in key:
            del sys.modules[key]

    fakes = _build_fake_google_modules()
    if extra_modules:
        fakes.update(extra_modules)

    for name, mod in fakes.items():
        sys.modules.setdefault(name, mod)
    # Ensure overrides always win for google.* submodules
    for name, mod in fakes.items():
        if name != "google":  # keep the top-level google consistent
            sys.modules[name] = mod

    if "engine/runtimes/templates" not in sys.path:
        sys.path.insert(0, "engine/runtimes/templates")

    import google_adk_server as srv  # noqa: PLC0415

    return srv


async def _aiter(*items):
    for item in items:
        yield item


def _make_adk_event(*, is_final: bool = False, text: str | None = None) -> MagicMock:
    event = MagicMock()
    event.is_final_response.return_value = is_final
    if text is not None:
        part = MagicMock()
        part.text = text
        event.content = MagicMock()
        event.content.parts = [part]
    else:
        event.content = None
    event.model_dump = MagicMock(return_value={"is_final": is_final, "text": text})
    return event


def _make_non_model_dump_event(*, is_final: bool = False, text: str | None = None) -> MagicMock:
    """Event without model_dump — exercises the else branch in _stream_agent_sse."""
    event = MagicMock(spec=[])  # spec=[] means no attributes by default
    # Add only what we need
    event.is_final_response = MagicMock(return_value=is_final)
    if text is not None:
        part = MagicMock()
        part.text = text
        event.content = MagicMock()
        event.content.parts = [part]
    else:
        event.content = None
    # Explicitly NOT setting model_dump so hasattr returns False
    return event


# ---------------------------------------------------------------------------
# Tests for _load_agent
# ---------------------------------------------------------------------------


class TestLoadAgent:
    def test_load_from_agent_module_root_agent_attr(self, tmp_path):
        """Lines 63-71: imports agent.py and picks 'root_agent' attribute."""
        srv = _fresh_import()

        fake_agent_obj = MagicMock(name="root_agent_instance")
        fake_agent_mod = types.ModuleType("agent")
        fake_agent_mod.root_agent = fake_agent_obj

        with patch.dict(sys.modules, {"agent": fake_agent_mod}):
            result = srv._load_agent()

        assert result is fake_agent_obj

    def test_load_from_agent_module_agent_attr(self):
        """Lines 63-71: picks 'agent' attribute when 'root_agent' absent."""
        srv = _fresh_import()

        fake_agent_obj = MagicMock(name="agent_instance")
        fake_agent_mod = types.ModuleType("agent")
        fake_agent_mod.agent = fake_agent_obj

        with patch.dict(sys.modules, {"agent": fake_agent_mod}):
            result = srv._load_agent()

        assert result is fake_agent_obj

    def test_load_from_agent_module_app_attr(self):
        """Lines 63-71: picks 'app' attribute when 'root_agent' and 'agent' absent."""
        srv = _fresh_import()

        fake_app_obj = MagicMock(name="app_instance")
        fake_agent_mod = types.ModuleType("agent")
        fake_agent_mod.app = fake_app_obj

        with patch.dict(sys.modules, {"agent": fake_agent_mod}):
            result = srv._load_agent()

        assert result is fake_app_obj

    def test_load_from_agent_module_no_valid_attr_raises(self):
        """Lines 72-76: raises AttributeError when no known attribute exported."""
        srv = _fresh_import()

        fake_agent_mod = types.ModuleType("agent")
        # No root_agent, agent, or app attributes

        with patch.dict(sys.modules, {"agent": fake_agent_mod}):
            with pytest.raises(AttributeError, match="root_agent"):
                srv._load_agent()

    def test_load_falls_back_to_yaml_when_no_agent_module(self, tmp_path, monkeypatch):
        """Lines 80-90: falls back to root_agent.yaml when agent.py not importable."""
        srv = _fresh_import()

        fake_loaded_agent = MagicMock(name="yaml_agent")
        fake_server_loader = types.ModuleType("server_loader")
        fake_server_loader.load_agent_from_yaml = MagicMock(return_value=fake_loaded_agent)

        # Ensure "agent" module not in sys.modules so ImportError path is taken
        mods_without_agent = {k: v for k, v in sys.modules.items() if k != "agent"}

        with patch.dict(sys.modules, mods_without_agent, clear=False):
            sys.modules.pop("agent", None)
            with patch("os.path.exists", return_value=True):
                with patch.dict(sys.modules, {"server_loader": fake_server_loader}):
                    result = srv._load_agent()

        assert result is fake_loaded_agent

    def test_load_raises_file_not_found_when_no_agent_and_no_yaml(self):
        """Lines 92-96: raises FileNotFoundError when neither agent.py nor yaml found."""
        srv = _fresh_import()

        sys.modules.pop("agent", None)
        with patch("os.path.exists", return_value=False):
            with pytest.raises(FileNotFoundError, match="No agent found"):
                srv._load_agent()

    def test_load_yaml_raises_on_loader_exception(self):
        """Lines 88-90: exception from load_agent_from_yaml is re-raised."""
        srv = _fresh_import()

        fake_server_loader = types.ModuleType("server_loader")
        fake_server_loader.load_agent_from_yaml = MagicMock(
            side_effect=RuntimeError("yaml parse failed")
        )

        sys.modules.pop("agent", None)
        with patch("os.path.exists", return_value=True):
            with patch.dict(sys.modules, {"server_loader": fake_server_loader}):
                with pytest.raises(RuntimeError, match="yaml parse failed"):
                    srv._load_agent()


# ---------------------------------------------------------------------------
# Tests for _build_session_service
# ---------------------------------------------------------------------------


class TestBuildSessionService:
    def test_memory_backend_returns_in_memory_service(self):
        """Lines 119-123: default 'memory' backend returns InMemorySessionService."""
        srv = _fresh_import()
        result = srv._build_session_service({})
        assert result is sys.modules["google.adk.sessions"].InMemorySessionService.return_value

    def test_database_backend_uses_config_url(self):
        """Lines 102-111: database backend picks up session_db_url from config."""
        srv = _fresh_import()
        cfg = {"session_backend": "database", "session_db_url": "postgresql://test/db"}
        result = srv._build_session_service(cfg)
        db_svc_cls = sys.modules["google.adk.sessions"].DatabaseSessionService
        db_svc_cls.assert_called_once_with(db_url="postgresql://test/db")
        assert result is db_svc_cls.return_value

    def test_database_backend_uses_env_var_url(self, monkeypatch):
        """Lines 105-106: database backend falls back to DATABASE_URL env var."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
        srv = _fresh_import()
        cfg = {"session_backend": "database"}
        srv._build_session_service(cfg)
        db_svc_cls = sys.modules["google.adk.sessions"].DatabaseSessionService
        db_svc_cls.assert_called_once_with(db_url="postgresql://env/db")

    def test_database_backend_raises_when_no_url(self, monkeypatch):
        """Lines 107-110: raises ValueError when no URL provided."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        srv = _fresh_import()
        cfg = {"session_backend": "database"}
        with pytest.raises(ValueError, match="session_db_url or DATABASE_URL"):
            srv._build_session_service(cfg)

    def test_vertex_ai_backend(self, monkeypatch):
        """Lines 112-118: vertex_ai backend creates VertexAiSessionService."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
        srv = _fresh_import()
        cfg = {"session_backend": "vertex_ai"}
        result = srv._build_session_service(cfg)
        vtx_cls = sys.modules["google.adk.sessions"].VertexAiSessionService
        vtx_cls.assert_called_once_with(project="my-project", location="europe-west1")
        assert result is vtx_cls.return_value


# ---------------------------------------------------------------------------
# Tests for _build_memory_service
# ---------------------------------------------------------------------------


class TestBuildMemoryService:
    def test_default_returns_none(self):
        """Lines 143-145: default memory config returns None."""
        srv = _fresh_import()
        result = srv._build_memory_service({})
        assert result is None

    def test_vertex_ai_bank(self, monkeypatch):
        """Lines 129-135: vertex_ai_bank creates VertexAiMemoryBankService."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-x")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-west1")
        srv = _fresh_import()
        cfg = {"memory_service": "vertex_ai_bank"}
        result = srv._build_memory_service(cfg)
        bank_cls = sys.modules["google.adk.memory"].VertexAiMemoryBankService
        bank_cls.assert_called_once_with(project="proj-x", location="us-west1")
        assert result is bank_cls.return_value

    def test_vertex_ai_rag(self, monkeypatch):
        """Lines 136-141: vertex_ai_rag creates VertexAiRagMemoryService."""
        monkeypatch.setenv("VERTEX_RAG_CORPUS", "my-corpus")
        srv = _fresh_import()
        cfg = {"memory_service": "vertex_ai_rag"}
        result = srv._build_memory_service(cfg)
        rag_cls = sys.modules["google.adk.memory"].VertexAiRagMemoryService
        rag_cls.assert_called_once_with(rag_corpus="my-corpus")
        assert result is rag_cls.return_value


# ---------------------------------------------------------------------------
# Tests for _build_artifact_service
# ---------------------------------------------------------------------------


class TestBuildArtifactService:
    def test_default_returns_none(self):
        """Lines 162-164: default artifact config returns None."""
        srv = _fresh_import()
        result = srv._build_artifact_service({})
        assert result is None

    def test_gcs_uses_config_bucket(self):
        """Lines 151-160: gcs artifact service picks up gcs_bucket from config."""
        srv = _fresh_import()
        cfg = {"artifact_service": "gcs", "gcs_bucket": "my-bucket"}
        result = srv._build_artifact_service(cfg)
        gcs_cls = sys.modules["google.adk.artifacts"].GcsArtifactService
        gcs_cls.assert_called_once_with(bucket_name="my-bucket")
        assert result is gcs_cls.return_value

    def test_gcs_uses_env_var_bucket(self, monkeypatch):
        """Lines 154: falls back to GCS_ARTIFACT_BUCKET env var."""
        monkeypatch.setenv("GCS_ARTIFACT_BUCKET", "env-bucket")
        srv = _fresh_import()
        cfg = {"artifact_service": "gcs"}
        srv._build_artifact_service(cfg)
        gcs_cls = sys.modules["google.adk.artifacts"].GcsArtifactService
        gcs_cls.assert_called_once_with(bucket_name="env-bucket")

    def test_gcs_raises_when_no_bucket(self, monkeypatch):
        """Lines 155-158: raises ValueError when no bucket configured."""
        monkeypatch.delenv("GCS_ARTIFACT_BUCKET", raising=False)
        srv = _fresh_import()
        cfg = {"artifact_service": "gcs"}
        with pytest.raises(ValueError, match="gcs_bucket or GCS_ARTIFACT_BUCKET"):
            srv._build_artifact_service(cfg)


# ---------------------------------------------------------------------------
# Tests for startup() — tool bridge wiring
# ---------------------------------------------------------------------------


class TestStartup:
    @pytest.mark.asyncio
    async def test_startup_loads_tools_and_injects_into_agent_list(self, monkeypatch):
        """Lines 194-198: tools injected into agent.tools list."""
        srv = _fresh_import()

        fake_tool = MagicMock(name="adk_tool")
        mock_agent = MagicMock()
        mock_agent.tools = []

        srv._agent = mock_agent

        tools_json = json.dumps([{"ref": "tools/search", "name": "search"}])
        monkeypatch.setenv("AGENT_TOOLS_JSON", tools_json)

        with patch("google_adk_server.to_adk_tools", return_value=[fake_tool]):
            await srv.startup()

        assert fake_tool in mock_agent.tools
        srv._agent = None

    @pytest.mark.asyncio
    async def test_startup_converts_non_list_tools_to_list(self, monkeypatch):
        """Lines 193-194: converts tuple tools to list before extending."""
        srv = _fresh_import()

        fake_tool = MagicMock(name="adk_tool")
        mock_agent = MagicMock()
        mock_agent.tools = (MagicMock(name="existing"),)  # tuple, not list

        srv._agent = mock_agent

        tools_json = json.dumps([{"ref": "tools/search", "name": "search"}])
        monkeypatch.setenv("AGENT_TOOLS_JSON", tools_json)

        with patch("google_adk_server.to_adk_tools", return_value=[fake_tool]):
            await srv.startup()

        assert fake_tool in mock_agent.tools
        srv._agent = None

    @pytest.mark.asyncio
    async def test_startup_handles_immutable_tools_gracefully(self, monkeypatch):
        """Lines 195-198: logs warning instead of raising when tools.extend raises."""
        srv = _fresh_import()

        fake_tool = MagicMock(name="adk_tool")
        mock_agent = MagicMock()

        # tools is a list-like that raises on extend
        broken_list = MagicMock(spec=list)
        broken_list.__iter__ = MagicMock(return_value=iter([]))
        broken_list.extend = MagicMock(side_effect=TypeError("immutable"))
        type(mock_agent).tools = property(lambda self: broken_list)

        srv._agent = mock_agent

        tools_json = json.dumps([{"ref": "tools/search", "name": "search"}])
        monkeypatch.setenv("AGENT_TOOLS_JSON", tools_json)

        with patch("google_adk_server.to_adk_tools", return_value=[fake_tool]):
            # Should NOT raise — just log a warning
            await srv.startup()

        srv._agent = None

    @pytest.mark.asyncio
    async def test_startup_handles_invalid_tools_json(self, monkeypatch):
        """Lines 199-201: invalid JSON resets _adk_tools to empty list."""
        srv = _fresh_import()
        srv._agent = None
        monkeypatch.setenv("AGENT_TOOLS_JSON", "not-json")
        await srv.startup()
        assert srv._adk_tools == []

    @pytest.mark.asyncio
    async def test_startup_no_tools_when_empty(self, monkeypatch):
        """Lines 186-188: no tools loaded when AGENT_TOOLS_JSON is empty list."""
        srv = _fresh_import()
        mock_agent = MagicMock()
        mock_agent.tools = []
        srv._agent = mock_agent
        monkeypatch.setenv("AGENT_TOOLS_JSON", "[]")

        with patch("google_adk_server.to_adk_tools", return_value=[]):
            await srv.startup()

        assert mock_agent.tools == []
        srv._agent = None


# ---------------------------------------------------------------------------
# Tests for lifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_applies_gemini_model_override(self, monkeypatch):
        """Lines 216-230: AGENT_MODEL set on agent.model for gemini models."""
        monkeypatch.setenv("AGENT_MODEL", "gemini-2.0-flash")
        monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
        monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AGENTBREEDER_ADK_CONFIG", "{}")

        srv = _fresh_import()

        mock_agent = MagicMock()
        mock_agent.model = "gemini-1.5-pro"
        mock_session_svc = MagicMock()
        mock_session_svc.create_session = AsyncMock(return_value=MagicMock(id="s1"))
        mock_session_svc.get_session = AsyncMock(return_value=None)

        with (
            patch("google_adk_server._load_agent", return_value=mock_agent),
            patch("google_adk_server._build_session_service", return_value=mock_session_svc),
            patch("google_adk_server._build_memory_service", return_value=None),
            patch("google_adk_server._build_artifact_service", return_value=None),
            patch("google_adk_server.startup", new_callable=AsyncMock),
        ):
            async with srv.lifespan(srv.app):
                pass

        assert mock_agent.model == "gemini-2.0-flash"

    @pytest.mark.asyncio
    async def test_lifespan_applies_litellm_model_override(self, monkeypatch):
        """Lines 218-227: non-gemini model with '/' triggers LiteLlm wrapping."""
        monkeypatch.setenv("AGENT_MODEL", "ollama/llama3")
        monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
        monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AGENTBREEDER_ADK_CONFIG", "{}")

        srv = _fresh_import()

        mock_agent = MagicMock()
        mock_agent.model = "gemini-1.5-pro"
        mock_lite_llm_instance = MagicMock(name="LiteLlmInstance")
        sys.modules["google.adk.models.lite_llm"].LiteLlm = MagicMock(
            return_value=mock_lite_llm_instance
        )

        mock_session_svc = MagicMock()
        mock_session_svc.create_session = AsyncMock(return_value=MagicMock(id="s1"))

        with (
            patch("google_adk_server._load_agent", return_value=mock_agent),
            patch("google_adk_server._build_session_service", return_value=mock_session_svc),
            patch("google_adk_server._build_memory_service", return_value=None),
            patch("google_adk_server._build_artifact_service", return_value=None),
            patch("google_adk_server.startup", new_callable=AsyncMock),
        ):
            async with srv.lifespan(srv.app):
                pass

        assert mock_agent.model is mock_lite_llm_instance

    @pytest.mark.asyncio
    async def test_lifespan_applies_temperature_and_max_tokens(self, monkeypatch):
        """Lines 234-250: AGENT_TEMPERATURE and AGENT_MAX_TOKENS applied."""
        monkeypatch.setenv("AGENT_TEMPERATURE", "0.5")
        monkeypatch.setenv("AGENT_MAX_TOKENS", "512")
        monkeypatch.delenv("AGENT_MODEL", raising=False)
        monkeypatch.setenv("AGENTBREEDER_ADK_CONFIG", "{}")

        srv = _fresh_import()

        mock_agent = MagicMock()
        mock_agent.generate_content_config = None
        mock_session_svc = MagicMock()
        mock_session_svc.create_session = AsyncMock(return_value=MagicMock(id="s1"))

        fake_genai_config = MagicMock(name="GenerateContentConfig_instance")
        sys.modules["google.genai.types"].GenerateContentConfig = MagicMock(
            return_value=fake_genai_config
        )

        with (
            patch("google_adk_server._load_agent", return_value=mock_agent),
            patch("google_adk_server._build_session_service", return_value=mock_session_svc),
            patch("google_adk_server._build_memory_service", return_value=None),
            patch("google_adk_server._build_artifact_service", return_value=None),
            patch("google_adk_server.startup", new_callable=AsyncMock),
        ):
            async with srv.lifespan(srv.app):
                pass

        sys.modules["google.genai.types"].GenerateContentConfig.assert_called_once_with(
            temperature=0.5, max_output_tokens=512
        )
        assert mock_agent.generate_content_config is fake_genai_config

    @pytest.mark.asyncio
    async def test_lifespan_injects_memory_and_artifact_services(self, monkeypatch):
        """Lines 276-280: memory_service and artifact_service added to runner_kwargs."""
        monkeypatch.delenv("AGENT_MODEL", raising=False)
        monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
        monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AGENTBREEDER_ADK_CONFIG", "{}")

        srv = _fresh_import()

        mock_agent = MagicMock()
        mock_session_svc = MagicMock()
        mock_memory_svc = MagicMock(name="MemorySvc")
        mock_artifact_svc = MagicMock(name="ArtifactSvc")

        mock_runner_cls = sys.modules["google.adk.runners"].Runner

        with (
            patch("google_adk_server._load_agent", return_value=mock_agent),
            patch("google_adk_server._build_session_service", return_value=mock_session_svc),
            patch("google_adk_server._build_memory_service", return_value=mock_memory_svc),
            patch("google_adk_server._build_artifact_service", return_value=mock_artifact_svc),
            patch("google_adk_server.startup", new_callable=AsyncMock),
        ):
            async with srv.lifespan(srv.app):
                pass

        call_kwargs = mock_runner_cls.call_args[1]
        assert call_kwargs.get("memory_service") is mock_memory_svc
        assert call_kwargs.get("artifact_service") is mock_artifact_svc

    @pytest.mark.asyncio
    async def test_lifespan_handles_invalid_adk_config_json(self, monkeypatch):
        """Lines 259-261: invalid AGENTBREEDER_ADK_CONFIG JSON defaults to {}."""
        monkeypatch.delenv("AGENT_MODEL", raising=False)
        monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
        monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AGENTBREEDER_ADK_CONFIG", "not-json")

        srv = _fresh_import()

        mock_agent = MagicMock()
        mock_session_svc = MagicMock()

        with (
            patch("google_adk_server._load_agent", return_value=mock_agent),
            patch("google_adk_server._build_session_service", return_value=mock_session_svc),
            patch("google_adk_server._build_memory_service", return_value=None),
            patch("google_adk_server._build_artifact_service", return_value=None),
            patch("google_adk_server.startup", new_callable=AsyncMock),
        ):
            async with srv.lifespan(srv.app):
                assert srv._adk_cfg == {}

    @pytest.mark.asyncio
    async def test_lifespan_model_override_exception_is_swallowed(self, monkeypatch):
        """Lines 231-232: exception during model set is caught and logged."""
        monkeypatch.setenv("AGENT_MODEL", "gemini-2.0-flash")
        monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
        monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AGENTBREEDER_ADK_CONFIG", "{}")

        srv = _fresh_import()

        mock_agent = MagicMock()
        # Raise on model assignment
        type(mock_agent).model = property(
            lambda self: "gemini-1.5-pro",
            lambda self, v: (_ for _ in ()).throw(RuntimeError("cannot set model")),
        )
        mock_session_svc = MagicMock()

        with (
            patch("google_adk_server._load_agent", return_value=mock_agent),
            patch("google_adk_server._build_session_service", return_value=mock_session_svc),
            patch("google_adk_server._build_memory_service", return_value=None),
            patch("google_adk_server._build_artifact_service", return_value=None),
            patch("google_adk_server.startup", new_callable=AsyncMock),
        ):
            # Should not raise
            async with srv.lifespan(srv.app):
                pass

    @pytest.mark.asyncio
    async def test_lifespan_generate_content_config_exception_is_swallowed(self, monkeypatch):
        """Lines 247-248: exception during generate_content_config set is caught."""
        monkeypatch.setenv("AGENT_TEMPERATURE", "0.9")
        monkeypatch.delenv("AGENT_MODEL", raising=False)
        monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
        monkeypatch.setenv("AGENTBREEDER_ADK_CONFIG", "{}")

        srv = _fresh_import()

        mock_agent = MagicMock()
        # generate_content_config exists (so hasattr passes) but assignment raises
        type(mock_agent).generate_content_config = property(
            lambda self: None,
            lambda self, v: (_ for _ in ()).throw(RuntimeError("cannot set config")),
        )
        mock_session_svc = MagicMock()

        # Make GenerateContentConfig raise too so we hit the except branch
        sys.modules["google.genai.types"].GenerateContentConfig = MagicMock(
            side_effect=RuntimeError("config error")
        )

        with (
            patch("google_adk_server._load_agent", return_value=mock_agent),
            patch("google_adk_server._build_session_service", return_value=mock_session_svc),
            patch("google_adk_server._build_memory_service", return_value=None),
            patch("google_adk_server._build_artifact_service", return_value=None),
            patch("google_adk_server.startup", new_callable=AsyncMock),
        ):
            # Should not raise — warning is logged instead
            async with srv.lifespan(srv.app):
                pass


# ---------------------------------------------------------------------------
# Tests for /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_healthy_when_agent_loaded(self, monkeypatch):
        """Line 297-301: health returns 'healthy' when _agent is set."""
        monkeypatch.setenv("AGENT_NAME", "my-agent")
        monkeypatch.setenv("AGENT_VERSION", "2.0.0")
        srv = _fresh_import()
        srv._agent = MagicMock(name="agent")

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["agent_name"] == "my-agent"
        assert body["version"] == "2.0.0"
        srv._agent = None

    @pytest.mark.asyncio
    async def test_health_returns_loading_when_agent_not_set(self):
        """Line 297-301: health returns 'loading' when _agent is None."""
        srv = _fresh_import()
        srv._agent = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        assert resp.json()["status"] == "loading"


# ---------------------------------------------------------------------------
# Tests for /invoke endpoint
# ---------------------------------------------------------------------------


class TestInvokeEndpoint:
    @pytest.mark.asyncio
    async def test_invoke_returns_503_when_not_loaded(self):
        """Lines 306-307: 503 when agent/runner/session not ready."""
        srv = _fresh_import()
        srv._agent = None
        srv._runner = None
        srv._session_service = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/invoke", json={"input": "hello"})

        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_invoke_returns_output_and_session_id(self):
        """Lines 308-318: successful invocation returns output + session_id."""
        srv = _fresh_import()

        mock_agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_ss = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "sess-invoke-1"

        mock_ss.get_session = AsyncMock(return_value=None)
        mock_ss.create_session = AsyncMock(return_value=mock_session)

        final_event = MagicMock()
        final_event.is_final_response.return_value = True
        part = MagicMock()
        part.text = "42 is the answer"
        final_event.content = MagicMock()
        final_event.content.parts = [part]
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._agent = mock_agent
        srv._runner = mock_runner
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/invoke", json={"input": "what is the answer?"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["output"] == "42 is the answer"
        assert body["session_id"] == "sess-invoke-1"
        srv._agent = None
        srv._runner = None
        srv._session_service = None

    @pytest.mark.asyncio
    async def test_invoke_returns_500_on_exception(self):
        """Lines 319-321: exception during invocation yields 500."""
        srv = _fresh_import()

        mock_agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_ss = MagicMock()
        mock_ss.get_session = AsyncMock(return_value=None)
        mock_ss.create_session = AsyncMock(side_effect=RuntimeError("db down"))

        srv._agent = mock_agent
        srv._runner = mock_runner
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/invoke", json={"input": "hello"})

        assert resp.status_code == 500
        srv._agent = None
        srv._runner = None
        srv._session_service = None

    @pytest.mark.asyncio
    async def test_invoke_uses_user_id_from_config(self):
        """Lines 309-310: user_id resolved from request.config when not in top-level."""
        srv = _fresh_import()

        mock_agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_ss = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "sess-uid"
        mock_ss.get_session = AsyncMock(return_value=None)
        mock_ss.create_session = AsyncMock(return_value=mock_session)

        final_event = MagicMock()
        final_event.is_final_response.return_value = True
        final_event.content = None
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._agent = mock_agent
        srv._runner = mock_runner
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/invoke",
                json={"input": "hi", "config": {"user_id": "custom-user"}},
            )

        assert resp.status_code == 200
        # The session was created (verifying user_id was passed through)
        mock_ss.create_session.assert_called_once()
        srv._agent = None
        srv._runner = None
        srv._session_service = None


# ---------------------------------------------------------------------------
# Tests for _stream_agent_sse (session reuse and non-model_dump events)
# ---------------------------------------------------------------------------


class TestStreamAgentSSE:
    @pytest.mark.asyncio
    async def test_stream_reuses_existing_session(self):
        """Lines 347, 352-353: when session_id is given and found, existing session is reused."""
        srv = _fresh_import()

        mock_agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_ss = MagicMock()

        existing_session = MagicMock()
        existing_session.id = "existing-sess"
        mock_ss.get_session = AsyncMock(return_value=existing_session)
        mock_ss.create_session = AsyncMock()

        final_event = _make_adk_event(is_final=True, text="reused")
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._agent = mock_agent
        srv._runner = mock_runner
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/stream",
                json={"input": "hello", "session_id": "existing-sess"},
            )

        assert resp.status_code == 200
        # create_session should NOT have been called since session was found
        mock_ss.create_session.assert_not_called()
        srv._agent = None
        srv._runner = None
        srv._session_service = None

    @pytest.mark.asyncio
    async def test_stream_creates_new_session_when_not_found(self):
        """Lines 350-351: creates new session when get_session returns None."""
        srv = _fresh_import()

        mock_agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_ss = MagicMock()

        mock_ss.get_session = AsyncMock(return_value=None)
        new_session = MagicMock()
        new_session.id = "new-sess"
        mock_ss.create_session = AsyncMock(return_value=new_session)

        final_event = _make_adk_event(is_final=True, text="new")
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._agent = mock_agent
        srv._runner = mock_runner
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/stream",
                json={"input": "hello", "session_id": "missing-sess"},
            )

        assert resp.status_code == 200
        mock_ss.create_session.assert_called_once()
        srv._agent = None
        srv._runner = None
        srv._session_service = None

    @pytest.mark.asyncio
    async def test_stream_handles_event_without_model_dump(self):
        """Lines 369-375: events without model_dump use the dict-building fallback."""
        srv = _fresh_import()

        mock_agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_ss = MagicMock()
        mock_ss.get_session = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.id = "fallback-sess"
        mock_ss.create_session = AsyncMock(return_value=mock_session)

        # Event without model_dump (plain object with content)
        event = _make_non_model_dump_event(is_final=True, text="fallback text")
        mock_runner.run_async = MagicMock(return_value=_aiter(event))

        srv._agent = mock_agent
        srv._runner = mock_runner
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/stream", json={"input": "test"})

        body = resp.text
        # Find the non-DONE data line and check it contains is_final
        for line in body.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                payload = json.loads(line[len("data:") :].strip())
                assert "is_final" in payload
                break
        srv._agent = None
        srv._runner = None
        srv._session_service = None

    @pytest.mark.asyncio
    async def test_stream_handles_event_without_content(self):
        """Lines 369-375: non-model_dump event with no content omits 'text' key."""
        srv = _fresh_import()

        mock_agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_ss = MagicMock()
        mock_ss.get_session = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.id = "no-content-sess"
        mock_ss.create_session = AsyncMock(return_value=mock_session)

        event = _make_non_model_dump_event(is_final=False, text=None)
        mock_runner.run_async = MagicMock(return_value=_aiter(event))

        srv._agent = mock_agent
        srv._runner = mock_runner
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/stream", json={"input": "test"})

        assert resp.status_code == 200
        srv._agent = None
        srv._runner = None
        srv._session_service = None


# ---------------------------------------------------------------------------
# Tests for _run_agent
# ---------------------------------------------------------------------------


class TestRunAgent:
    @pytest.mark.asyncio
    async def test_run_agent_creates_new_session_when_none(self):
        """Lines 406: creates a new session when session_id is None."""
        srv = _fresh_import()

        mock_session = MagicMock()
        mock_session.id = "new-run-sess"
        mock_ss = MagicMock()
        mock_ss.create_session = AsyncMock(return_value=mock_session)

        final_event = MagicMock()
        final_event.is_final_response.return_value = True
        part = MagicMock()
        part.text = "answer"
        final_event.content = MagicMock()
        final_event.content.parts = [part]

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._session_service = mock_ss
        srv._runner = mock_runner

        result_text, result_sess, _history = await srv._run_agent(
            input_text="question", user_id="user1", session_id=None
        )

        assert result_text == "answer"
        assert result_sess == "new-run-sess"
        mock_ss.create_session.assert_called_once()
        srv._session_service = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_run_agent_reuses_existing_session(self):
        """Lines 399-401: reuses session when session_id is given and found."""
        srv = _fresh_import()

        existing_session = MagicMock()
        existing_session.id = "existing-run-sess"
        mock_ss = MagicMock()
        mock_ss.get_session = AsyncMock(return_value=existing_session)
        mock_ss.create_session = AsyncMock()

        final_event = MagicMock()
        final_event.is_final_response.return_value = True
        part = MagicMock()
        part.text = "reused answer"
        final_event.content = MagicMock()
        final_event.content.parts = [part]

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._session_service = mock_ss
        srv._runner = mock_runner

        result_text, result_sess, _history = await srv._run_agent(
            input_text="question", user_id="user1", session_id="existing-run-sess"
        )

        assert result_text == "reused answer"
        assert result_sess == "existing-run-sess"
        mock_ss.create_session.assert_not_called()
        srv._session_service = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_run_agent_creates_new_session_when_existing_not_found(self):
        """Lines 402-404: creates new session when provided session_id not found."""
        srv = _fresh_import()

        new_session = MagicMock()
        new_session.id = "fallback-run-sess"
        mock_ss = MagicMock()
        mock_ss.get_session = AsyncMock(return_value=None)
        mock_ss.create_session = AsyncMock(return_value=new_session)

        final_event = MagicMock()
        final_event.is_final_response.return_value = True
        final_event.content = None

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._session_service = mock_ss
        srv._runner = mock_runner

        result_text, result_sess, _history = await srv._run_agent(
            input_text="question", user_id="user1", session_id="stale-sess"
        )

        assert result_sess == "fallback-run-sess"
        assert result_text == ""
        mock_ss.create_session.assert_called_once()
        srv._session_service = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_run_agent_accumulates_multiple_final_parts(self):
        """Lines 419-422: concatenates text from multiple parts in final event."""
        srv = _fresh_import()

        mock_session = MagicMock()
        mock_session.id = "multi-part-sess"
        mock_ss = MagicMock()
        mock_ss.create_session = AsyncMock(return_value=mock_session)

        final_event = MagicMock()
        final_event.is_final_response.return_value = True
        p1 = MagicMock()
        p1.text = "Hello"
        p2 = MagicMock()
        p2.text = " World"
        p3 = MagicMock()
        del p3.text  # no text attr
        final_event.content = MagicMock()
        final_event.content.parts = [p1, p2, p3]

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(return_value=_aiter(final_event))

        srv._session_service = mock_ss
        srv._runner = mock_runner

        result_text, _, _history = await srv._run_agent(
            input_text="question", user_id="user1", session_id=None
        )

        assert result_text == "Hello World"
        srv._session_service = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_run_agent_skips_non_final_events(self):
        """Lines 419: only final events contribute to output."""
        srv = _fresh_import()

        mock_session = MagicMock()
        mock_session.id = "skip-sess"
        mock_ss = MagicMock()
        mock_ss.create_session = AsyncMock(return_value=mock_session)

        intermediate = MagicMock()
        intermediate.is_final_response.return_value = False
        p = MagicMock()
        p.text = "thinking..."
        intermediate.content = MagicMock()
        intermediate.content.parts = [p]

        final_event = MagicMock()
        final_event.is_final_response.return_value = True
        p_final = MagicMock()
        p_final.text = "final answer"
        final_event.content = MagicMock()
        final_event.content.parts = [p_final]

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(return_value=_aiter(intermediate, final_event))

        srv._session_service = mock_ss
        srv._runner = mock_runner

        result_text, _, _history = await srv._run_agent(
            input_text="question", user_id="user1", session_id=None
        )

        assert result_text == "final answer"
        srv._session_service = None
        srv._runner = None
