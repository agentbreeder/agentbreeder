"""AgentBreeder server wrapper for Google ADK agents.

This file is copied into the agent container at build time.
It wraps any Google ADK agent as a FastAPI server with /invoke and /health endpoints.

Authentication: uses Application Default Credentials (ADC).
Set GOOGLE_APPLICATION_CREDENTIALS to a service account key path, or rely on
Workload Identity when running on GCP.

Configuration: the runtime builder injects AGENTBREEDER_ADK_CONFIG as a JSON
string with the GoogleADKConfig fields (session_backend, memory_service, etc.).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from engine.config_parser import ToolRef
from engine.tool_bridge import to_adk_tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")

# Model string prefixes that route through LiteLLM instead of the native Gemini SDK.
_LITELLM_PREFIXES = (
    "ollama/",
    "groq/",
    "bedrock/",
    "openai/",
    "anthropic/",
    "huggingface/",
    "vertex_ai/",
    "azure/",
    "cohere/",
    "mistral/",
    "together_ai/",
    "replicate/",
)


class InvokeRequest(BaseModel):
    input: str
    session_id: str | None = None
    user_id: str | None = None
    config: dict | None = None  # type: ignore[type-arg]


InvokeRequest.model_rebuild()


class ToolCall(BaseModel):
    """Structured record of a single tool invocation (#215).

    Shared shape across all runtime templates so the dashboard playground can
    render a tool-call timeline without regex-scraping message bodies.
    """

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    duration_ms: int = 0
    started_at: str = ""


class InvokeResponse(BaseModel):
    output: str | None = None
    session_id: str
    metadata: dict | None = None  # type: ignore[type-arg]
    # Structured tool-call timeline (#215). Populated from ADK events that
    # carry ``function_call`` / ``function_response`` parts.  Always present,
    # may be empty.
    history: list[ToolCall] = Field(default_factory=list)


InvokeResponse.model_rebuild()


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _verify_auth(authorization: str | None = Header(default=None)) -> None:
    """Bearer token auth for protected endpoints.

    Behavior:
      - If AGENT_AUTH_TOKEN env var is unset/empty -> auth disabled (local dev).
      - If set, every request to a protected endpoint must include
        ``Authorization: Bearer <token>`` and the token must match exactly.

    /health is intentionally NOT protected so Cloud Run / k8s liveness probes
    can hit it without credentials.
    """
    expected = os.getenv("AGENT_AUTH_TOKEN", "").strip()
    if not expected:
        return  # auth disabled
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    presented = authorization.removeprefix("Bearer ").strip()
    if presented != expected:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


def _load_agent() -> Any:
    """Dynamically load the ADK agent from agent.py or root_agent.yaml."""
    sys.path.insert(0, "/app")

    # Try agent.py first
    try:
        module = importlib.import_module("agent")
        for attr_name in ("root_agent", "agent", "app"):
            if hasattr(module, attr_name):
                logger.info("Loaded agent from agent.py (attr=%s)", attr_name)
                return getattr(module, attr_name)
        msg = (
            "agent.py must export one of: 'root_agent', 'agent', or 'app'. "
            "This should be a google.adk.agents.Agent instance."
        )
        raise AttributeError(msg)
    except ImportError:
        pass  # fall through to root_agent.yaml

    # Fall back to root_agent.yaml (ADK config-based agents)
    yaml_path = "/app/root_agent.yaml"
    if os.path.exists(yaml_path):
        try:
            from server_loader import load_agent_from_yaml

            logger.info("Loaded agent from root_agent.yaml")
            return load_agent_from_yaml(yaml_path)
        except Exception as e:
            logger.error("Failed to load agent from root_agent.yaml: %s", e)
            raise

    msg = (
        "No agent found. Provide either agent.py (exporting root_agent/agent/app) "
        "or root_agent.yaml (from `adk create --type=config`)."
    )
    raise FileNotFoundError(msg)


def _build_session_service(cfg: dict) -> Any:  # type: ignore[type-arg]
    """Construct the appropriate SessionService from config."""
    backend = cfg.get("session_backend", "memory")
    if backend == "database":
        from google.adk.sessions import DatabaseSessionService

        db_url = cfg.get("session_db_url") or os.getenv("DATABASE_URL")
        if not db_url:
            raise ValueError(
                "session_db_url or DATABASE_URL env var is required for database session backend"
            )
        logger.info("Using DatabaseSessionService (url=<redacted>)")
        return DatabaseSessionService(db_url=db_url)
    elif backend == "vertex_ai":
        from google.adk.sessions import VertexAiSessionService

        project = os.getenv("GOOGLE_CLOUD_PROJECT", "agentbreeder-local")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        logger.info("Using VertexAiSessionService (project=%s, location=%s)", project, location)
        return VertexAiSessionService(project=project, location=location)
    else:
        from google.adk.sessions import InMemorySessionService

        logger.info("Using InMemorySessionService")
        return InMemorySessionService()


def _build_memory_service(cfg: dict) -> Any:  # type: ignore[type-arg]
    """Construct the appropriate MemoryService from config."""
    svc = cfg.get("memory_service", "memory")
    if svc == "vertex_ai_bank":
        from google.adk.memory import VertexAiMemoryBankService

        project = os.getenv("GOOGLE_CLOUD_PROJECT", "agentbreeder-local")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        logger.info("Using VertexAiMemoryBankService")
        return VertexAiMemoryBankService(project=project, location=location)
    elif svc == "vertex_ai_rag":
        from google.adk.memory import VertexAiRagMemoryService

        rag_corpus = os.getenv("VERTEX_RAG_CORPUS", "")
        logger.info("Using VertexAiRagMemoryService (corpus=%s)", rag_corpus)
        return VertexAiRagMemoryService(rag_corpus=rag_corpus)
    else:
        # Return None — Runner accepts None for optional memory_service
        logger.info("Using default (no) MemoryService")
        return None


def _build_artifact_service(cfg: dict) -> Any:  # type: ignore[type-arg]
    """Construct the appropriate ArtifactService from config."""
    svc = cfg.get("artifact_service", "memory")
    if svc == "gcs":
        from google.adk.artifacts import GcsArtifactService

        bucket = cfg.get("gcs_bucket") or os.getenv("GCS_ARTIFACT_BUCKET", "")
        if not bucket:
            raise ValueError(
                "gcs_bucket or GCS_ARTIFACT_BUCKET env var is required for GCS artifact service"
            )
        logger.info("Using GcsArtifactService (bucket=%s)", bucket)
        return GcsArtifactService(bucket_name=bucket)
    else:
        # Return None — Runner accepts None for optional artifact_service
        logger.info("Using default (no) ArtifactService")
        return None


# Module-level globals — initialized at startup, reused for all requests
_agent = None
_runner = None
_session_service = None
_adk_cfg: dict = {}  # type: ignore[type-arg]
_adk_tools: list[Any] = []


async def startup() -> None:
    """Wire tool bridge into the loaded ADK agent.

    This is called by the lifespan after _load_agent(), and can also be
    called directly in tests (with _agent pre-set) to exercise the wiring.
    """
    global _adk_tools  # noqa: PLW0603
    tools_json = os.getenv("AGENT_TOOLS_JSON", "[]")
    try:
        raw_tools = json.loads(tools_json)
        tool_refs = [ToolRef(**t) for t in raw_tools]
        _adk_tools = to_adk_tools(tool_refs)
        if _adk_tools:
            logger.info("Loaded %d ADK tool(s)", len(_adk_tools))
            if _agent is not None and hasattr(_agent, "tools"):
                try:
                    if isinstance(_agent.tools, list):
                        _agent.tools.extend(_adk_tools)
                    else:
                        _agent.tools = list(_agent.tools) + _adk_tools
                except Exception:
                    logger.warning(
                        "Could not inject tools into ADK agent -- agent.tools is not mutable"
                    )
    except Exception:
        logger.exception("Failed to load ADK tools -- proceeding with no tools")
        _adk_tools = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """FastAPI lifespan context manager for startup and shutdown."""
    global _agent, _runner, _session_service, _adk_cfg  # noqa: PLW0603
    logger.info("Loading Google ADK agent...")
    _agent = _load_agent()

    # Apply AGENT_MODEL override if the loaded agent is an LlmAgent
    agent_model = os.getenv("AGENT_MODEL")
    agent_temperature_str = os.getenv("AGENT_TEMPERATURE")
    agent_max_tokens_str = os.getenv("AGENT_MAX_TOKENS")

    if agent_model and hasattr(_agent, "model"):
        try:
            if agent_model.startswith(_LITELLM_PREFIXES):
                from google.adk.models.lite_llm import LiteLlm

                if agent_model.startswith("ollama/"):
                    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://agentbreeder-ollama:11434")
                    _agent.model = LiteLlm(model=agent_model, api_base=ollama_url)
                    logger.info(
                        "Applied LiteLlm model override (Ollama): model=%s api_base=%s",
                        agent_model,
                        ollama_url,
                    )
                else:
                    _agent.model = LiteLlm(model=agent_model)
                    logger.info("Applied LiteLlm model override: model=%s", agent_model)
            else:
                _agent.model = agent_model
                logger.info("Applied AGENT_MODEL override: %s", agent_model)
        except Exception:
            logger.warning("Could not set AGENT_MODEL on agent — proceeding with agent default")

    if (agent_temperature_str or agent_max_tokens_str) and hasattr(
        _agent, "generate_content_config"
    ):
        try:
            from google.genai import types as genai_types

            kwargs: dict = {}  # type: ignore[type-arg]
            if agent_temperature_str:
                kwargs["temperature"] = float(agent_temperature_str)
            if agent_max_tokens_str:
                kwargs["max_output_tokens"] = int(agent_max_tokens_str)
            _agent.generate_content_config = genai_types.GenerateContentConfig(**kwargs)
            logger.info("Applied generate_content_config overrides: %s", kwargs)
        except Exception:
            logger.warning(
                "Could not apply generate_content_config — proceeding with agent defaults"
            )

    # --- Tool bridge ---
    await startup()

    # --- Load ADK framework config ---
    raw_cfg = os.getenv("AGENTBREEDER_ADK_CONFIG", "{}")
    try:
        _adk_cfg = json.loads(raw_cfg)
    except json.JSONDecodeError:
        logger.warning("AGENTBREEDER_ADK_CONFIG is not valid JSON; using defaults")
        _adk_cfg = {}

    # --- Build configurable services + runner ---
    _session_service = _build_session_service(_adk_cfg)
    memory_service = _build_memory_service(_adk_cfg)
    artifact_service = _build_artifact_service(_adk_cfg)

    from google.adk.runners import Runner

    app_name = os.getenv("GOOGLE_CLOUD_PROJECT", "agentbreeder-local")
    runner_kwargs: dict = {  # type: ignore[type-arg]
        "agent": _agent,
        "app_name": app_name,
        "session_service": _session_service,
    }
    if memory_service is not None:
        runner_kwargs["memory_service"] = memory_service
    if artifact_service is not None:
        runner_kwargs["artifact_service"] = artifact_service
    _runner = Runner(**runner_kwargs)
    logger.info("Google ADK agent loaded successfully (app_name=%s)", app_name)

    yield
    # shutdown (future use)


app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy" if _agent is not None else "loading",
        agent_name=os.getenv("AGENT_NAME", "unknown"),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
    )


@app.post("/invoke", response_model=InvokeResponse, dependencies=[Depends(_verify_auth)])
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _agent is None or _runner is None or _session_service is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    config = request.config or {}
    user_id = request.user_id or config.get("user_id", "agentbreeder-user")

    try:
        output, session_id, history = await _run_agent(
            input_text=request.input,
            user_id=user_id,
            session_id=request.session_id,
        )
        return InvokeResponse(output=output, session_id=session_id, history=history)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/stream", dependencies=[Depends(_verify_auth)])
async def stream(request: InvokeRequest) -> StreamingResponse:
    """Stream Google ADK execution as Server-Sent Events."""
    if _agent is None or _runner is None or _session_service is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")
    config = request.config or {}
    user_id = request.user_id or config.get("user_id", "agentbreeder-user")
    return StreamingResponse(
        _stream_agent_sse(request.input, request.session_id, user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_agent_sse(input_text: str, session_id: str | None, user_id: str) -> Any:
    """Async generator forwarding each ADK Event as an SSE data line."""
    from google.genai import types as genai_types

    app_name = os.getenv("GOOGLE_CLOUD_PROJECT", "agentbreeder-local")

    # Reuse or create session
    existing = None
    if session_id:
        existing = await _session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
    if existing is None:
        session = await _session_service.create_session(app_name=app_name, user_id=user_id)
    else:
        session = existing

    user_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=input_text)],
    )

    try:
        async for event in _runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=user_message,
        ):
            if hasattr(event, "model_dump"):
                payload = event.model_dump()
            else:
                payload = {
                    "is_final": bool(
                        hasattr(event, "is_final_response") and event.is_final_response()
                    )
                }
                if event.content and event.content.parts:
                    payload["text"] = "".join(getattr(p, "text", "") for p in event.content.parts)
            yield f"data: {json.dumps(payload)}\n\n"
    except Exception as exc:  # noqa: BLE001
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    yield "data: [DONE]\n\n"


async def _run_agent(
    input_text: str,
    user_id: str,
    session_id: str | None,
) -> tuple[str, str, list[ToolCall]]:
    """Run the Google ADK agent using the module-level runner.

    Reuses an existing session when session_id is provided; creates a new one otherwise.
    Returns (response_text, session_id, tool_call_history).
    """
    from google.genai import types as genai_types

    app_name = os.getenv("GOOGLE_CLOUD_PROJECT", "agentbreeder-local")

    # Resolve or create session
    if session_id:
        session = await _session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        if session is None:
            # Session expired or not found — create a new one
            session = await _session_service.create_session(app_name=app_name, user_id=user_id)
    else:
        session = await _session_service.create_session(app_name=app_name, user_id=user_id)

    user_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=input_text)],
    )

    final_response = ""
    # Pair function_call parts with their function_response parts by id/name (#215).
    pending: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    history: list[ToolCall] = []

    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=user_message,
    ):
        if getattr(event, "content", None) and getattr(event.content, "parts", None):
            for part in event.content.parts:
                # function_call → name + args
                fc = getattr(part, "function_call", None)
                if fc is not None and getattr(fc, "name", None):
                    fc_id = str(getattr(fc, "id", "") or fc.name)
                    fc_args = getattr(fc, "args", None) or {}
                    if not isinstance(fc_args, dict):
                        try:
                            fc_args = dict(fc_args)
                        except (TypeError, ValueError):
                            fc_args = {"raw": str(fc_args)}
                    pending.setdefault(fc_id, {"name": fc.name, "args": fc_args, "result": ""})
                    pending[fc_id]["name"] = fc.name
                    pending[fc_id]["args"] = fc_args
                    if fc_id not in order:
                        order.append(fc_id)
                # function_response → result
                fr = getattr(part, "function_response", None)
                if fr is not None:
                    fr_name = getattr(fr, "name", "") or ""
                    fr_id = str(getattr(fr, "id", "") or fr_name)
                    response_payload = getattr(fr, "response", None) or {}
                    try:
                        result_str = (
                            json.dumps(response_payload)
                            if not isinstance(response_payload, str)
                            else response_payload
                        )
                    except (TypeError, ValueError):
                        result_str = str(response_payload)
                    entry = pending.setdefault(fr_id, {"name": fr_name, "args": {}, "result": ""})
                    entry["result"] = result_str
                    if not entry.get("name"):
                        entry["name"] = fr_name
                    if fr_id not in order:
                        order.append(fr_id)

        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_response += part.text

    for cid in order:
        entry = pending[cid]
        history.append(
            ToolCall(
                name=str(entry.get("name", "") or ""),
                args=entry.get("args", {}) or {},
                result=str(entry.get("result", "") or ""),
            )
        )

    return final_response, session.id, history
