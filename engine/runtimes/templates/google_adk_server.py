"""AgentBreeder server wrapper for Google ADK agents.

This file is copied into the agent container at build time.
It wraps any Google ADK agent as a FastAPI server with /invoke and /health endpoints.

Authentication: uses Application Default Credentials (ADC).
Set GOOGLE_APPLICATION_CREDENTIALS to a service account key path, or rely on
Workload Identity when running on GCP.
"""

import importlib
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")

# Module-level singletons — initialized once at startup, reused for all requests
_agent = None
_runner = None
_session_service = None


def _load_agent() -> Any:
    """Dynamically load the ADK agent from agent.py."""
    sys.path.insert(0, "/app")
    try:
        module = importlib.import_module("agent")
    except ImportError as e:
        logger.error("Failed to import agent module: %s", e)
        raise

    for attr_name in ("root_agent", "agent", "app"):
        if hasattr(module, attr_name):
            return getattr(module, attr_name)

    msg = (
        "agent.py must export one of: 'root_agent', 'agent', or 'app'. "
        "This should be a google.adk.agents.Agent instance."
    )
    raise AttributeError(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """FastAPI lifespan context manager for startup and shutdown."""
    global _agent, _runner, _session_service  # noqa: PLW0603
    logger.info("Loading Google ADK agent...")
    _agent = _load_agent()

    # Apply AGENT_MODEL override if the loaded agent is an LlmAgent
    agent_model = os.getenv("AGENT_MODEL")
    agent_temperature_str = os.getenv("AGENT_TEMPERATURE")
    agent_max_tokens_str = os.getenv("AGENT_MAX_TOKENS")

    if agent_model and hasattr(_agent, "model"):
        try:
            _agent.model = agent_model
            logger.info("Applied AGENT_MODEL override: %s", agent_model)
        except Exception:
            logger.warning("Could not set AGENT_MODEL on agent — proceeding with agent default")

    if (agent_temperature_str or agent_max_tokens_str) and hasattr(_agent, "generate_content_config"):
        try:
            from google.genai import types as genai_types

            kwargs: dict[str, Any] = {}
            if agent_temperature_str:
                kwargs["temperature"] = float(agent_temperature_str)
            if agent_max_tokens_str:
                kwargs["max_output_tokens"] = int(agent_max_tokens_str)
            _agent.generate_content_config = genai_types.GenerateContentConfig(**kwargs)
            logger.info("Applied generate_content_config overrides: %s", kwargs)
        except Exception:
            logger.warning("Could not apply generate_content_config — proceeding with agent defaults")

    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    app_name = os.getenv("GOOGLE_CLOUD_PROJECT", "agentbreeder-local")
    _session_service = InMemorySessionService()
    _runner = Runner(
        agent=_agent,
        app_name=app_name,
        session_service=_session_service,
    )
    logger.info("Google ADK agent loaded successfully (app_name=%s)", app_name)

    yield
    # shutdown code (if needed in the future)


app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
    lifespan=lifespan,
)


class InvokeRequest(BaseModel):
    input: str
    session_id: Optional[str] = None  # pass to maintain conversation history
    config: Optional[dict[str, Any]] = None


class InvokeResponse(BaseModel):
    output: Any
    session_id: str  # echo back so caller can continue conversation
    metadata: Optional[dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy" if _agent is not None else "loading",
        agent_name=os.getenv("AGENT_NAME", "unknown"),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
    )


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _agent is None or _runner is None or _session_service is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    # Reuse provided session_id or create a new one for this call
    session_id = request.session_id or str(uuid.uuid4())
    config = request.config or {}
    user_id = config.get("user_id", "agentbreeder-user")

    try:
        result = await _run_agent(request.input, session_id, user_id)
        return InvokeResponse(output=result, session_id=session_id)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _run_agent(input_text: str, session_id: str, user_id: str) -> str:
    """Run the Google ADK agent using the module-level runner and session service."""
    from google.genai import types as genai_types

    app_name = os.getenv("GOOGLE_CLOUD_PROJECT", "agentbreeder-local")

    # Look up existing session or create a new one
    existing = await _session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if existing is None:
        session = await _session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
    else:
        session = existing

    user_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=input_text)],
    )

    final_response = ""
    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=user_message,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_response += part.text

    return final_response
