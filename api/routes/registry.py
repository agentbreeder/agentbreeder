"""Registry API routes — tools, models, prompts, knowledge bases."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    ModelCreate,
    ModelResponse,
    ModelUsageResponse,
    PromptCreate,
    PromptResponse,
    ToolCreate,
    ToolDetailResponse,
    ToolResponse,
    ToolUsageResponse,
    SearchResult,
)
from registry.agents import AgentRegistry
from registry.models import ModelRegistry
from registry.prompts import PromptRegistry
from registry.tools import ToolRegistry

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


@router.get("/tools", response_model=ApiResponse[list[ToolResponse]])
async def list_tools(
    tool_type: str | None = Query(None),
    source: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ToolResponse]]:
    """List tools and MCP servers from the registry."""
    tools, total = await ToolRegistry.list(
        db, tool_type=tool_type, source=source, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[ToolResponse.model_validate(t) for t in tools],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/tools", response_model=ApiResponse[ToolResponse], status_code=201)
async def register_tool(
    body: ToolCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ToolResponse]:
    """Register a tool or MCP server."""
    tool = await ToolRegistry.register(
        db,
        name=body.name,
        description=body.description,
        tool_type=body.tool_type,
        schema_definition=body.schema_definition,
        endpoint=body.endpoint,
        source=body.source,
    )
    return ApiResponse(data=ToolResponse.model_validate(tool))


@router.get("/tools/{tool_id}", response_model=ApiResponse[ToolDetailResponse])
async def get_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ToolDetailResponse]:
    """Get a single tool with full schema."""
    tool = await ToolRegistry.get_by_id(db, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    return ApiResponse(data=ToolDetailResponse.model_validate(tool))


@router.get(
    "/tools/{tool_id}/usage",
    response_model=ApiResponse[list[ToolUsageResponse]],
)
async def get_tool_usage(
    tool_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ToolUsageResponse]]:
    """Get agents that reference this tool."""
    tool = await ToolRegistry.get_by_id(db, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    agents = await ToolRegistry.get_usage(db, tool_id)
    data = [
        ToolUsageResponse(
            agent_id=a.id,
            agent_name=a.name,
            agent_status=a.status.value if hasattr(a.status, "value") else str(a.status),
        )
        for a in agents
    ]
    return ApiResponse(
        data=data,
        meta=ApiMeta(page=1, per_page=len(data), total=len(data)),
    )


@router.get("/models/compare", response_model=ApiResponse[list[ModelResponse]])
async def compare_models(
    ids: str = Query(..., description="Comma-separated model IDs (max 3)"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ModelResponse]]:
    """Compare up to 3 models side by side."""
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if len(id_list) < 2 or len(id_list) > 3:
        raise HTTPException(
            status_code=400,
            detail="Provide 2 or 3 model IDs separated by commas",
        )
    models = await ModelRegistry.get_by_ids(db, id_list)
    if len(models) != len(id_list):
        raise HTTPException(status_code=404, detail="One or more models not found")
    return ApiResponse(
        data=[ModelResponse.model_validate(m) for m in models],
        meta=ApiMeta(page=1, per_page=len(models), total=len(models)),
    )


@router.get("/models", response_model=ApiResponse[list[ModelResponse]])
async def list_models(
    provider: str | None = Query(None),
    source: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ModelResponse]]:
    """List LLM models from the registry."""
    models, total = await ModelRegistry.list(
        db, provider=provider, source=source, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[ModelResponse.model_validate(m) for m in models],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/models", response_model=ApiResponse[ModelResponse], status_code=201)
async def register_model(
    body: ModelCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ModelResponse]:
    """Register an LLM model."""
    model = await ModelRegistry.register(
        db,
        name=body.name,
        provider=body.provider,
        description=body.description,
        config=body.config,
        source=body.source,
        context_window=body.context_window,
        max_output_tokens=body.max_output_tokens,
        input_price_per_million=body.input_price_per_million,
        output_price_per_million=body.output_price_per_million,
        capabilities=body.capabilities,
    )
    return ApiResponse(data=ModelResponse.model_validate(model))


@router.get("/models/{model_id}", response_model=ApiResponse[ModelResponse])
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ModelResponse]:
    """Get a single model with pricing and capabilities."""
    model = await ModelRegistry.get_by_id(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return ApiResponse(data=ModelResponse.model_validate(model))


@router.get(
    "/models/{model_id}/usage",
    response_model=ApiResponse[list[ModelUsageResponse]],
)
async def get_model_usage(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ModelUsageResponse]]:
    """Get agents that use this model as primary or fallback."""
    model = await ModelRegistry.get_by_id(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    usage_list = await ModelRegistry.get_usage(db, model_id)
    data = [
        ModelUsageResponse(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_status=agent.status.value
            if hasattr(agent.status, "value")
            else str(agent.status),
            usage_type=usage_type,
        )
        for agent, usage_type in usage_list
    ]
    return ApiResponse(
        data=data,
        meta=ApiMeta(page=1, per_page=len(data), total=len(data)),
    )


@router.get("/prompts", response_model=ApiResponse[list[PromptResponse]])
async def list_prompts(
    team: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[PromptResponse]]:
    """List prompt templates from the registry."""
    prompts, total = await PromptRegistry.list(
        db, team=team, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[PromptResponse.model_validate(p) for p in prompts],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/prompts", response_model=ApiResponse[PromptResponse], status_code=201)
async def register_prompt(
    body: PromptCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PromptResponse]:
    """Register a prompt template."""
    prompt = await PromptRegistry.register(
        db,
        name=body.name,
        version=body.version,
        content=body.content,
        description=body.description,
        team=body.team,
    )
    return ApiResponse(data=PromptResponse.model_validate(prompt))


@router.get("/search", response_model=ApiResponse[list[SearchResult]])
async def search_registry(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[SearchResult]]:
    """Search across all registry entities (agents, tools)."""
    results: list[SearchResult] = []

    # Search agents
    agents, _ = await AgentRegistry.search(db, query=q, page=1, per_page=per_page)
    for agent in agents:
        results.append(
            SearchResult(
                entity_type="agent",
                id=agent.id,
                name=agent.name,
                description=agent.description,
                team=agent.team,
            )
        )

    # Search tools
    tools, _ = await ToolRegistry.search(db, query=q, page=1, per_page=per_page)
    for tool in tools:
        results.append(
            SearchResult(
                entity_type="tool",
                id=tool.id,
                name=tool.name,
                description=tool.description,
            )
        )

    # Search models
    models, _ = await ModelRegistry.search(db, query=q, page=1, per_page=per_page)
    for model in models:
        results.append(
            SearchResult(
                entity_type="model",
                id=model.id,
                name=model.name,
                description=model.description,
            )
        )

    # Search prompts
    prompts, _ = await PromptRegistry.search(db, query=q, page=1, per_page=per_page)
    for prompt in prompts:
        results.append(
            SearchResult(
                entity_type="prompt",
                id=prompt.id,
                name=prompt.name,
                description=prompt.description,
                team=prompt.team,
            )
        )

    return ApiResponse(
        data=results[:per_page],
        meta=ApiMeta(page=page, per_page=per_page, total=len(results)),
    )
