"""BuilderSession API (Wave 3, spec §6) — create / get / list (more in C4-C6)."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from api.auth import get_current_user
from api.database import get_db
from api.models.database import User
from api.models.schemas import (
    ApiResponse,
    BuilderEjectRequest,
    BuilderMessageRequest,
    BuilderSessionCreateRequest,
    BuilderSessionResponse,
)
from api.routes.builders import _builder_key_name, _no_key_detail
from api.services.builder_session_service import (
    BuilderSessionService,
    CloudSandboxUnavailable,
    SessionEventBus,
)
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import AuthenticationError, ProviderError
from engine.providers.models import ProviderConfig, ProviderType
from engine.providers.openai_provider import OpenAIProvider
from engine.sandbox.base import select_sandbox_mode
from engine.secrets.factory import get_workspace_backend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/builder/sessions", tags=["builder-sessions"])


def _bus(request: Request) -> SessionEventBus:
    return request.app.state.builder_event_bus


def _provider_and_secret(engine_name: str, user: User):
    """Return (secret_name, provider_factory) for the engine's BYO key."""
    if engine_name == "codex":
        secret = f"AGENTBREEDER_CODEX_BUILDER_KEY__{user.id}"

        def make_codex(key: str) -> OpenAIProvider:
            return OpenAIProvider(
                ProviderConfig(provider_type=ProviderType.openai, api_key=key)
            )

        return secret, make_codex
    # default claude
    secret = _builder_key_name(user)

    def make_claude(key: str) -> AnthropicProvider:
        return AnthropicProvider(
            ProviderConfig(provider_type=ProviderType.anthropic, api_key=key)
        )

    return secret, make_claude


def _to_response(sess: object) -> BuilderSessionResponse:
    st = getattr(sess, "state", None) or {}
    return BuilderSessionResponse(
        id=str(sess.id),
        team=sess.team,
        engine=sess.engine,
        agent_yaml=st.get("agent_yaml"),
        files=st.get("files", {}),
        deploy_job_id=st.get("deploy_job_id"),
        history=st.get("history", []),
    )


@router.post("", response_model=ApiResponse[BuilderSessionResponse])
async def create_session(
    body: BuilderSessionCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BuilderSessionResponse]:
    if body.engine not in ("claude", "codex"):
        raise HTTPException(status_code=400, detail="engine must be 'claude' or 'codex'")
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.create(team=user.team, user_id=user.id, engine=body.engine)
    return ApiResponse(data=_to_response(sess))


@router.get("", response_model=ApiResponse[list[BuilderSessionResponse]])
async def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[BuilderSessionResponse]]:
    svc = BuilderSessionService(db, _bus(request))
    rows = await svc.list_for_team(user.team)
    return ApiResponse(data=[_to_response(s) for s in rows])


@router.get("/{session_id}", response_model=ApiResponse[BuilderSessionResponse])
async def get_session(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BuilderSessionResponse]:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(status_code=404, detail="Builder session not found")
    return ApiResponse(data=_to_response(sess))


@router.post("/{session_id}/messages")
async def post_message(
    session_id: uuid.UUID,
    body: BuilderMessageRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(status_code=404, detail="Builder session not found")

    secret_name = _builder_key_name(user)
    backend, _ws = get_workspace_backend()
    api_key = await backend.get(secret_name)
    if not api_key:
        raise HTTPException(status_code=400, detail=_no_key_detail(secret_name))

    async def generator() -> AsyncGenerator[dict, None]:
        provider = AnthropicProvider(
            ProviderConfig(provider_type=ProviderType.anthropic, api_key=api_key)
        )
        try:
            async for frame in svc.run_interview_turn(sess, provider, body.content):
                yield frame
        except AuthenticationError:
            logger.warning(
                "builder /messages: auth failed for session %s (key not logged)", session_id
            )
            yield {
                "event": "error",
                "data": json.dumps(
                    {"detail": "Claude API authentication failed.", "code": "auth_error"}
                ),
            }
        except ProviderError:
            logger.warning("builder /messages: upstream error for session %s", session_id)
            yield {
                "event": "error",
                "data": json.dumps(
                    {"detail": "Upstream Claude API error.", "code": "upstream_error"}
                ),
            }
        finally:
            await provider.close()

    return EventSourceResponse(generator())


@router.post("/{session_id}/eject")
async def eject_to_code(
    session_id: uuid.UUID,
    body: BuilderEjectRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(status_code=404, detail="Builder session not found")

    engine_name = body.engine or sess.engine
    if engine_name not in ("claude", "codex"):
        raise HTTPException(status_code=400, detail="engine must be 'claude' or 'codex'")

    # Fail closed: code execution is only allowed in local sandbox mode. The
    # 409 fires BEFORE any sandbox/provider/stream is constructed, so the
    # multi-tenant cloud never runs user code in-process (CloudSandbox is W4).
    if select_sandbox_mode() != "local":
        raise HTTPException(
            status_code=409,
            detail="Code generation requires local sandbox mode (cloud sandbox lands in Wave 4).",
        )

    secret_name, make_provider = _provider_and_secret(engine_name, user)
    backend, _ws = get_workspace_backend()
    api_key = await backend.get(secret_name)
    if not api_key:
        raise HTTPException(status_code=400, detail=_no_key_detail(secret_name))

    async def generator() -> AsyncGenerator[dict, None]:
        provider = make_provider(api_key)
        try:
            async for frame in svc.run_eject(sess, provider, body.instruction, engine_name):
                yield frame
        except CloudSandboxUnavailable:
            logger.warning("builder /eject: sandbox unavailable for session %s", session_id)
            yield {
                "event": "error",
                "data": json.dumps(
                    {"detail": "Sandbox unavailable.", "code": "sandbox_unavailable"}
                ),
            }
        except (AuthenticationError, ProviderError):
            logger.warning("builder /eject: provider error for session %s", session_id)
            yield {
                "event": "error",
                "data": json.dumps(
                    {"detail": "Coding agent provider error.", "code": "provider_error"}
                ),
            }
        finally:
            await provider.close()

    return EventSourceResponse(generator())
