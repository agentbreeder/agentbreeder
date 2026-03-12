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
    PromptUpdate,
    PromptVersionCreate,
    PromptVersionDiffResponse,
    PromptVersionResponse,
    SearchResult,
    ToolCreate,
    ToolDetailResponse,
    ToolResponse,
    ToolUsageResponse,
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
    prompts, total = await PromptRegistry.list(db, team=team, page=page, per_page=per_page)
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


@router.get("/prompts/{prompt_id}", response_model=ApiResponse[PromptResponse])
async def get_prompt(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PromptResponse]:
    """Get a single prompt by ID."""
    prompt = await PromptRegistry.get_by_id(db, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return ApiResponse(data=PromptResponse.model_validate(prompt))


@router.put("/prompts/{prompt_id}", response_model=ApiResponse[PromptResponse])
async def update_prompt(
    prompt_id: str,
    body: PromptUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PromptResponse]:
    """Update a prompt's content or description."""
    prompt = await PromptRegistry.update(
        db, prompt_id, content=body.content, description=body.description
    )
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return ApiResponse(data=PromptResponse.model_validate(prompt))


@router.delete("/prompts/{prompt_id}", response_model=ApiResponse[dict])
async def delete_prompt(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Delete a prompt."""
    deleted = await PromptRegistry.delete(db, prompt_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return ApiResponse(data={"deleted": True})


@router.get(
    "/prompts/{prompt_id}/versions",
    response_model=ApiResponse[list[PromptResponse]],
)
async def list_prompt_versions(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[PromptResponse]]:
    """List all versions of a prompt by name (looked up via the given id)."""
    versions = await PromptRegistry.get_versions(db, prompt_id)
    if not versions:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return ApiResponse(
        data=[PromptResponse.model_validate(p) for p in versions],
        meta=ApiMeta(page=1, per_page=len(versions), total=len(versions)),
    )


@router.post(
    "/prompts/{prompt_id}/duplicate",
    response_model=ApiResponse[PromptResponse],
    status_code=201,
)
async def duplicate_prompt(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PromptResponse]:
    """Duplicate a prompt as a new version."""
    prompt = await PromptRegistry.duplicate(db, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return ApiResponse(data=PromptResponse.model_validate(prompt))


# --- Prompt Version Snapshots ---


@router.get(
    "/prompts/{prompt_id}/versions/history",
    response_model=ApiResponse[list[PromptVersionResponse]],
)
async def list_prompt_version_history(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[PromptVersionResponse]]:
    """List all content-version snapshots for a prompt."""
    prompt = await PromptRegistry.get_by_id(db, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    versions = await PromptRegistry.list_version_snapshots(db, prompt_id)
    return ApiResponse(
        data=[PromptVersionResponse.model_validate(v) for v in versions],
        meta=ApiMeta(page=1, per_page=len(versions), total=len(versions)),
    )


@router.post(
    "/prompts/{prompt_id}/versions/history",
    response_model=ApiResponse[PromptVersionResponse],
    status_code=201,
)
async def create_prompt_version_snapshot(
    prompt_id: str,
    body: PromptVersionCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PromptVersionResponse]:
    """Create a new version snapshot for a prompt."""
    prompt = await PromptRegistry.get_by_id(db, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    ver = await PromptRegistry.create_version_snapshot(
        db,
        prompt_id=prompt_id,
        version=body.version,
        content=body.content,
        change_summary=body.change_summary,
        author=body.author,
    )
    return ApiResponse(data=PromptVersionResponse.model_validate(ver))


@router.get(
    "/prompts/{prompt_id}/versions/history/{version_id}",
    response_model=ApiResponse[PromptVersionResponse],
)
async def get_prompt_version_snapshot(
    prompt_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PromptVersionResponse]:
    """Get a specific version snapshot."""
    version = await PromptRegistry.get_version_snapshot(db, prompt_id, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return ApiResponse(data=PromptVersionResponse.model_validate(version))


@router.get(
    "/prompts/{prompt_id}/versions/history/{v1}/diff/{v2}",
    response_model=ApiResponse[PromptVersionDiffResponse],
)
async def diff_prompt_version_snapshots(
    prompt_id: str,
    v1: str,
    v2: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PromptVersionDiffResponse]:
    """Compute a unified diff between two prompt version snapshots."""
    ver1, ver2, diff_text = await PromptRegistry.diff_version_snapshots(
        db, prompt_id, v1, v2
    )
    if not ver1 or not ver2:
        raise HTTPException(status_code=404, detail="One or both versions not found")
    return ApiResponse(
        data=PromptVersionDiffResponse(
            version_a=PromptVersionResponse.model_validate(ver1),
            version_b=PromptVersionResponse.model_validate(ver2),
            diff=diff_text.splitlines(),
        )
    )


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
