"""BuilderSession API (Wave 3, spec §6) — create / get / list (more in C4-C6)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from api.auth import get_current_user
from api.database import get_db
from api.middleware.rbac import enforce_team_role
from api.models.database import User
from api.models.schemas import (
    ApiResponse,
    BuilderEjectRequest,
    BuilderMessageRequest,
    BuilderSessionCreateRequest,
    BuilderSessionResponse,
    DeployRequest,
)
from api.routes.builders import _builder_key_name, _no_key_detail
from api.routes.deploys import _resolve_deploy_team
from api.services.audit_service import AuditService
from api.services.builder_session_service import (
    BuilderSessionService,
    CloudSandboxUnavailable,
    SessionEventBus,
)
from api.services.deploy_service import DeployService
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
            return OpenAIProvider(ProviderConfig(provider_type=ProviderType.openai, api_key=key))

        return secret, make_codex
    # default claude
    secret = _builder_key_name(user)

    def make_claude(key: str) -> AnthropicProvider:
        return AnthropicProvider(ProviderConfig(provider_type=ProviderType.anthropic, api_key=key))

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


@router.post("/{session_id}/deploy", response_model=ApiResponse[BuilderSessionResponse])
async def deploy_from_session(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BuilderSessionResponse]:
    """Deploy the session's validated agent.yaml via the governed deploy path.

    This does NOT introduce a parallel deploy pipeline (CLAUDE.md §2 —
    governance is non-negotiable). It reuses the exact primitives the
    ``POST /api/v1/deploys`` route uses: ``_resolve_deploy_team`` →
    ``enforce_team_role`` (the substantive team-scoped RBAC check) →
    ``DeployService.create_agent_and_deploy`` (the 8-step pipeline that also
    auto-registers the agent) → ``AuditService.log_event``. The resulting
    ``deploy_job_id`` is persisted onto the session state so the Studio chat
    can poll / stream deploy status.
    """
    svc = BuilderSessionService(db, _bus(request))
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(status_code=404, detail="Builder session not found")
    agent_yaml = (sess.state or {}).get("agent_yaml")
    if not agent_yaml:
        raise HTTPException(
            status_code=400, detail="Session has no validated agent.yaml to deploy yet."
        )

    # Reuse the EXACT governed deploy path (RBAC + pipeline + audit). No bypass.
    body = DeployRequest(config_yaml=agent_yaml, target="local")
    team_id, _agent = await _resolve_deploy_team(body, db)
    await enforce_team_role(user, team_id, "deployer")
    try:
        _new_agent, job = await DeployService.create_agent_and_deploy(
            db, yaml_content=agent_yaml, target="local"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await AuditService.log_event(
        actor=user.email,
        action="deploy.create",
        resource_type="deploy_job",
        resource_name=str(job.id),
        resource_id=str(job.id),
        team=team_id,
        details={"agent_id": str(job.agent_id), "target": "local"},
        ip_address=request.client.host if request.client else None,
    )

    state = dict(sess.state or {})
    state["deploy_job_id"] = str(job.id)
    await svc.save_state(sess, state)
    await db.commit()
    return ApiResponse(data=_to_response(sess))


@router.get("/{session_id}/stream")
async def stream_session(
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """Aggregate SSE feed for a session over the in-memory event bus.

    Subscribes to the session's bus topic and relays every published frame
    (interview tokens, eject file-changes, deploy progress) to the client.
    Emits a ``ping`` every 15s of idle to keep the connection alive and to
    notice client disconnects.
    """
    bus = _bus(request)
    svc = BuilderSessionService(db, bus)
    sess = await svc.get(session_id, team=user.team)
    if sess is None:
        raise HTTPException(status_code=404, detail="Builder session not found")

    async def generator() -> AsyncGenerator[dict, None]:
        async with bus.subscribe(str(session_id)) as queue:
            # Flush an immediate frame so response headers are sent right away
            # (sse-starlette holds headers until the first chunk) and the
            # client knows the stream is live before any work is published.
            yield {"event": "ready", "data": json.dumps({"session_id": str(session_id)})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield evt

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
