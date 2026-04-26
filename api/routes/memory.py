"""Memory backend API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    ConversationSummaryResponse,
    CreateMemoryConfigRequest,
    DeleteConversationsRequest,
    MemoryConfigResponse,
    MemoryMessageCreate,
    MemoryMessageResponse,
    MemorySearchResultResponse,
    MemoryStatsResponse,
)
from api.services.memory_service import MemoryService

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


# -- Config CRUD -----------------------------------------------------------


@router.post("/configs", response_model=ApiResponse[MemoryConfigResponse], status_code=201)
async def create_memory_config(
    body: CreateMemoryConfigRequest,
    _user: User = Depends(require_role("deployer")),
) -> ApiResponse[MemoryConfigResponse]:
    """Create a new memory configuration."""
    config = await MemoryService.create_config(
        name=body.name,
        team=body.team,
        owner=body.owner,
        backend_type=body.backend_type,
        memory_type=body.memory_type,
        max_messages=body.max_messages,
        namespace_pattern=body.namespace_pattern,
        scope=body.scope,
        linked_agents=body.linked_agents,
        description=body.description,
        tags=body.tags,
    )
    return ApiResponse(data=MemoryConfigResponse.model_validate(config.model_dump()))


@router.get("/configs", response_model=ApiResponse[list[MemoryConfigResponse]])
async def list_memory_configs(
    _user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> ApiResponse[list[MemoryConfigResponse]]:
    """List all memory configurations."""
    configs, total = await MemoryService.list_configs(page=page, per_page=per_page)
    return ApiResponse(
        data=[MemoryConfigResponse.model_validate(c.model_dump()) for c in configs],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/configs/{config_id}", response_model=ApiResponse[MemoryConfigResponse])
async def get_memory_config(
    config_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[MemoryConfigResponse]:
    """Get a memory configuration with stats."""
    config = await MemoryService.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Memory config not found")
    return ApiResponse(data=MemoryConfigResponse.model_validate(config.model_dump()))


@router.delete("/configs/{config_id}", response_model=ApiResponse[dict])
async def delete_memory_config(
    config_id: str, _user: User = Depends(require_role("admin"))
) -> ApiResponse[dict]:
    """Delete a memory configuration and all its data."""
    deleted = await MemoryService.delete_config(config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory config not found")
    return ApiResponse(data={"deleted": True})


# -- Stats -----------------------------------------------------------------


@router.get("/configs/{config_id}/stats", response_model=ApiResponse[MemoryStatsResponse])
async def get_memory_stats(
    config_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[MemoryStatsResponse]:
    """Get usage statistics for a memory configuration."""
    stats = await MemoryService.get_stats(config_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Memory config not found")
    return ApiResponse(data=MemoryStatsResponse.model_validate(stats.model_dump()))


# -- Messages --------------------------------------------------------------


@router.post(
    "/configs/{config_id}/messages",
    response_model=ApiResponse[MemoryMessageResponse],
    status_code=201,
)
async def store_message(
    config_id: str,
    body: MemoryMessageCreate,
    _user: User = Depends(get_current_user),
) -> ApiResponse[MemoryMessageResponse]:
    """Store a message in a memory backend."""
    msg = await MemoryService.store_message(
        config_id,
        session_id=body.session_id,
        role=body.role,
        content=body.content,
        agent_id=body.agent_id,
        metadata=body.metadata,
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Memory config not found")
    return ApiResponse(data=MemoryMessageResponse.model_validate(msg.model_dump()))


# -- Conversations ---------------------------------------------------------


@router.get(
    "/configs/{config_id}/conversations",
    response_model=ApiResponse[list[ConversationSummaryResponse]],
)
async def list_conversations(
    config_id: str,
    _user: User = Depends(get_current_user),
    agent_id: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> ApiResponse[list[ConversationSummaryResponse]]:
    """List conversations (sessions) in a memory config."""
    config = await MemoryService.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Memory config not found")
    convos, total = await MemoryService.list_conversations(
        config_id, agent_id=agent_id, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[ConversationSummaryResponse.model_validate(c.model_dump()) for c in convos],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get(
    "/configs/{config_id}/conversations/{session_id}",
    response_model=ApiResponse[list[MemoryMessageResponse]],
)
async def get_conversation(
    config_id: str,
    session_id: str,
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[MemoryMessageResponse]]:
    """Get all messages in a conversation."""
    config = await MemoryService.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Memory config not found")
    msgs = await MemoryService.get_conversation(config_id, session_id)
    return ApiResponse(data=[MemoryMessageResponse.model_validate(m.model_dump()) for m in msgs])


@router.delete(
    "/configs/{config_id}/conversations",
    response_model=ApiResponse[dict],
)
async def delete_conversations(
    config_id: str,
    body: DeleteConversationsRequest,
    _user: User = Depends(require_role("deployer")),
) -> ApiResponse[dict]:
    """Bulk delete conversations by session, agent, or date range."""
    config = await MemoryService.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Memory config not found")

    before_dt: datetime | None = None
    if body.before:
        before_dt = datetime.fromisoformat(body.before).replace(tzinfo=UTC)

    deleted = await MemoryService.delete_conversations(
        config_id,
        session_id=body.session_id,
        agent_id=body.agent_id,
        before=before_dt,
    )
    return ApiResponse(data={"deleted_count": deleted})


# -- Search ----------------------------------------------------------------


@router.get(
    "/configs/{config_id}/search",
    response_model=ApiResponse[list[MemorySearchResultResponse]],
)
async def search_messages(
    config_id: str,
    _user: User = Depends(get_current_user),
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
) -> ApiResponse[list[MemorySearchResultResponse]]:
    """Full-text search across stored messages."""
    config = await MemoryService.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Memory config not found")

    results = await MemoryService.search_messages(config_id, query=q, limit=limit)
    return ApiResponse(
        data=[
            MemorySearchResultResponse(
                message=MemoryMessageResponse.model_validate(r.message.model_dump()),
                score=r.score,
                highlight=r.highlight,
            )
            for r in results
        ]
    )


# -- Thread convenience (used by @agentbreeder/aps-client) -------------------


@router.get(
    "/thread/{thread_id}",
    response_model=ApiResponse[list[MemoryMessageResponse]],
)
async def get_thread_messages(
    thread_id: str,
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[MemoryMessageResponse]]:
    """Get all messages for a thread ID across all memory configs.

    Convenience endpoint for the aps-client. Returns messages from the first
    config that has this session_id. Returns empty list if not found.
    """
    configs, _ = await MemoryService.list_configs(page=1, per_page=100)
    for config in configs:
        msgs = await MemoryService.get_conversation(config.id, thread_id)
        if msgs:
            return ApiResponse(
                data=[MemoryMessageResponse.model_validate(m.model_dump()) for m in msgs]
            )
    return ApiResponse(data=[])


@router.post(
    "/thread",
    response_model=ApiResponse[dict],
    status_code=201,
)
async def save_thread_messages(
    body: dict[str, Any],
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Save messages for a thread ID.

    Convenience endpoint for the aps-client. Stores messages into the first
    available memory config, or returns 404 if no configs exist.

    Request body:
    - thread_id: str (required)
    - messages: list of {role, content} objects (required)
    """
    thread_id: str = body.get("thread_id", "")
    messages_raw: list[dict] = body.get("messages", [])

    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id is required")
    if not isinstance(messages_raw, list):
        raise HTTPException(status_code=400, detail="messages must be a list")

    configs, _ = await MemoryService.list_configs(page=1, per_page=1)
    if not configs:
        raise HTTPException(status_code=404, detail="No memory config found")

    config = configs[0]
    saved = 0
    for msg in messages_raw:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if content:
            await MemoryService.store_message(
                config.id,
                session_id=thread_id,
                role=role,
                content=content,
            )
            saved += 1

    return ApiResponse(data={"saved": saved, "thread_id": thread_id})
