"""Template CRUD routes — /api/v1/templates."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import User
from api.models.enums import TemplateCategory, TemplateStatus
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    TemplateCreate,
    TemplateInstantiateRequest,
    TemplateInstantiateResponse,
    TemplateResponse,
    TemplateUpdate,
)
from registry.templates import TemplateRegistry

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


@router.get("", response_model=ApiResponse[list[TemplateResponse]])
async def list_templates(
    category: TemplateCategory | None = Query(None),
    framework: str | None = Query(None),
    status: TemplateStatus | None = Query(None),
    team: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[TemplateResponse]]:
    templates, total = await TemplateRegistry.list(
        db,
        category=category,
        framework=framework,
        status=status,
        team=team,
        page=page,
        per_page=per_page,
    )
    return ApiResponse(
        data=[TemplateResponse.model_validate(t) for t in templates],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("", response_model=ApiResponse[TemplateResponse], status_code=201)
async def create_template(
    body: TemplateCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TemplateResponse]:
    template = await TemplateRegistry.create(
        db,
        name=body.name,
        version=body.version,
        description=body.description,
        category=body.category,
        framework=body.framework,
        config_template=body.config_template,
        parameters=[p.model_dump() for p in body.parameters],
        tags=body.tags,
        author=body.author,
        team=body.team,
        readme=body.readme,
    )
    await db.commit()
    return ApiResponse(data=TemplateResponse.model_validate(template))


@router.get("/{template_id}", response_model=ApiResponse[TemplateResponse])
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TemplateResponse]:
    template = await TemplateRegistry.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return ApiResponse(data=TemplateResponse.model_validate(template))


@router.put("/{template_id}", response_model=ApiResponse[TemplateResponse])
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TemplateResponse]:
    update_data = body.model_dump(exclude_none=True)
    if "parameters" in update_data:
        update_data["parameters"] = [
            p.model_dump() if hasattr(p, "model_dump") else p for p in update_data["parameters"]
        ]
    template = await TemplateRegistry.update(db, template_id, **update_data)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.commit()
    return ApiResponse(data=TemplateResponse.model_validate(template))


@router.delete("/{template_id}")
async def delete_template(
    template_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, bool]]:
    deleted = await TemplateRegistry.delete(db, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.commit()
    return ApiResponse(data={"deleted": True})


@router.post(
    "/{template_id}/instantiate",
    response_model=ApiResponse[TemplateInstantiateResponse],
)
async def instantiate_template(
    template_id: uuid.UUID,
    body: TemplateInstantiateRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TemplateInstantiateResponse]:
    """Fill in template parameters to generate agent.yaml content."""
    template = await TemplateRegistry.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    import copy
    import json
    from io import StringIO

    from ruamel.yaml import YAML

    config = copy.deepcopy(template.config_template)

    # Substitute parameters in the config
    config_str = json.dumps(config)
    for param in template.parameters:
        placeholder = "{{" + param.get("name", "") + "}}"
        value = body.values.get(param.get("name", ""), param.get("default", ""))
        if value is None:
            value = ""
        config_str = config_str.replace(placeholder, str(value))

    config = json.loads(config_str)

    # Convert to YAML
    yaml = YAML()
    yaml.default_flow_style = False
    buf = StringIO()
    yaml.dump(config, buf)
    yaml_content = buf.getvalue()

    agent_name = config.get("name", template.name)

    # Increment use count
    await TemplateRegistry.increment_use_count(db, template_id)
    await db.commit()

    return ApiResponse(
        data=TemplateInstantiateResponse(
            yaml_content=yaml_content,
            agent_name=agent_name,
        )
    )
