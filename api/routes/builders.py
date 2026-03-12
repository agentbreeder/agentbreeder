"""YAML Builder API routes — read, write, and import raw YAML for any resource type."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml as pyyaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from api.models.schemas import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/builders", tags=["builders"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESOURCE_TYPES = {"agent", "prompt", "tool", "rag", "memory"}

_SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "engine" / "schema"

_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


def _load_schema(resource_type: str) -> dict[str, Any]:
    """Load and cache a JSON Schema for *resource_type*."""
    if resource_type in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[resource_type]

    schema_file = _SCHEMA_DIR / f"{resource_type}.schema.json"
    if not schema_file.exists():
        raise HTTPException(
            status_code=400,
            detail=f"No schema found for resource type '{resource_type}'",
        )

    schema = json.loads(schema_file.read_text())
    _SCHEMA_CACHE[resource_type] = schema
    return schema


def _validate_resource_type(resource_type: str) -> None:
    if resource_type not in _RESOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid resource_type '{resource_type}'. "
                f"Must be one of: {', '.join(sorted(_RESOURCE_TYPES))}"
            ),
        )


def _validate_yaml_against_schema(yaml_content: str, resource_type: str) -> dict[str, Any]:
    """Parse YAML and validate against the JSON Schema. Returns parsed dict."""
    try:
        data = pyyaml.safe_load(yaml_content)
    except pyyaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=422,
            detail="YAML must be a mapping (object), not a scalar or list",
        )

    schema = _load_schema(resource_type)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            "{path}: {msg}".format(
                path="/" + "/".join(str(p) for p in e.absolute_path) if e.absolute_path else "/",
                msg=e.message,
            )
            for e in errors[:10]
        )
        raise HTTPException(status_code=422, detail=f"Schema validation failed: {detail}")

    return data


# ---------------------------------------------------------------------------
# In-memory store (swap for DB-backed registry in production)
# ---------------------------------------------------------------------------

_STORE: dict[str, dict[str, str]] = {
    "agent": {},
    "prompt": {},
    "tool": {},
    "rag": {},
    "memory": {},
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class YamlImportRequest(BaseModel):
    resource_type: str
    yaml_content: str


class YamlImportResponse(BaseModel):
    name: str
    resource_type: str
    message: str


class YamlSaveResponse(BaseModel):
    name: str
    resource_type: str
    valid: bool
    message: str


# ---------------------------------------------------------------------------
# GET /api/v1/builders/{resource_type}/{name}/yaml
# ---------------------------------------------------------------------------


@router.get(
    "/{resource_type}/{name}/yaml",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_resource_yaml(
    resource_type: str,
    name: str,
) -> PlainTextResponse:
    """Return the raw YAML config for a resource."""
    _validate_resource_type(resource_type)

    stored = _STORE.get(resource_type, {}).get(name)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"{resource_type} '{name}' not found")

    return PlainTextResponse(content=stored, media_type="application/x-yaml")


# ---------------------------------------------------------------------------
# PUT /api/v1/builders/{resource_type}/{name}/yaml
# ---------------------------------------------------------------------------


@router.put("/{resource_type}/{name}/yaml", response_model=ApiResponse[YamlSaveResponse])
async def put_resource_yaml(
    resource_type: str,
    name: str,
    request: Request,
) -> ApiResponse[YamlSaveResponse]:
    """Accept raw YAML, validate against the schema, and save."""
    _validate_resource_type(resource_type)

    body_bytes = await request.body()
    yaml_content = body_bytes.decode("utf-8")

    if not yaml_content.strip():
        raise HTTPException(status_code=422, detail="Empty YAML body")

    _validate_yaml_against_schema(yaml_content, resource_type)

    _STORE[resource_type][name] = yaml_content
    logger.info("Saved %s '%s' YAML (%d bytes)", resource_type, name, len(yaml_content))

    return ApiResponse(
        data=YamlSaveResponse(
            name=name,
            resource_type=resource_type,
            valid=True,
            message=f"{resource_type} '{name}' saved successfully",
        ),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/builders/import
# ---------------------------------------------------------------------------


@router.post("/import", response_model=ApiResponse[YamlImportResponse], status_code=201)
async def import_resource_yaml(
    body: YamlImportRequest,
) -> ApiResponse[YamlImportResponse]:
    """Import raw YAML to create a new resource entry."""
    _validate_resource_type(body.resource_type)

    data = _validate_yaml_against_schema(body.yaml_content, body.resource_type)

    name = data.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="YAML must contain a 'name' field")

    if name in _STORE.get(body.resource_type, {}):
        raise HTTPException(
            status_code=409,
            detail=f"{body.resource_type} '{name}' already exists. Use PUT to update.",
        )

    _STORE[body.resource_type][name] = body.yaml_content
    logger.info("Imported %s '%s' from YAML", body.resource_type, name)

    return ApiResponse(
        data=YamlImportResponse(
            name=name,
            resource_type=body.resource_type,
            message=f"{body.resource_type} '{name}' imported successfully",
        ),
    )
