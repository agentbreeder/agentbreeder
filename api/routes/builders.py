"""YAML Builder API routes — read, write, and import raw YAML for any resource type."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import yaml as pyyaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiResponse
from engine.agent_chat_builder import (
    MAX_HISTORY_MESSAGES,
    ChatTurnResult,
    run_chat_turn,
    run_chat_turn_stream,
)
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import AuthenticationError, ProviderError
from engine.providers.models import ProviderConfig, ProviderType
from engine.recommend import Recommendation, RecommendInput
from engine.recommend import recommend as _recommend
from engine.secrets.factory import get_workspace_backend

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


def _no_key_detail(secret_name: str) -> str:
    return (
        "No Claude key connected. Add your Claude API key in "
        f"Settings → Secrets as '{secret_name}'."
    )


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
# File-backed store
# ---------------------------------------------------------------------------


class FileStore:
    """File-backed key-value store for builder configs.

    Layout: {base_dir}/{resource_type}/{name}.yaml

    ``set`` accepts either a raw YAML string or a dict (serialised to YAML).
    ``get`` returns a dict (deserialised from YAML), or None if missing.
    ``get_raw`` returns the raw YAML string, or None if missing.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        env_dir = os.getenv("BUILDERS_DATA_DIR")
        if base_dir is not None:
            self._base = base_dir
        elif env_dir:
            self._base = Path(env_dir)
        else:
            self._base = Path.home() / ".agentbreeder" / "builders"
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, resource_type: str, name: str) -> Path:
        return self._base / resource_type / f"{name}.yaml"

    def get(self, resource_type: str, name: str) -> dict[str, Any] | None:
        """Return the stored resource as a dict, or None if not found."""
        p = self._path(resource_type, name)
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8")
        return pyyaml.safe_load(raw)

    def get_raw(self, resource_type: str, name: str) -> str | None:
        """Return the stored resource as raw YAML text, or None if not found."""
        p = self._path(resource_type, name)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    def set(self, resource_type: str, name: str, data: dict[str, Any] | str) -> None:
        """Store *data* (dict or raw YAML string) under resource_type/name."""
        p = self._path(resource_type, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            text = pyyaml.dump(data, default_flow_style=False)
        else:
            text = data
        p.write_text(text, encoding="utf-8")

    def exists(self, resource_type: str, name: str) -> bool:
        return self._path(resource_type, name).exists()


_store = FileStore()


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
    _user: User = Depends(get_current_user),
) -> PlainTextResponse:
    """Return the raw YAML config for a resource."""
    _validate_resource_type(resource_type)

    stored = await run_in_threadpool(_store.get_raw, resource_type, name)
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
    _user: User = Depends(require_role("deployer")),
) -> ApiResponse[YamlSaveResponse]:
    """Accept raw YAML, validate against the schema, and save."""
    _validate_resource_type(resource_type)

    body_bytes = await request.body()
    yaml_content = body_bytes.decode("utf-8")

    if not yaml_content.strip():
        raise HTTPException(status_code=422, detail="Empty YAML body")

    _validate_yaml_against_schema(yaml_content, resource_type)

    await run_in_threadpool(_store.set, resource_type, name, yaml_content)
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
    _user: User = Depends(require_role("deployer")),
) -> ApiResponse[YamlImportResponse]:
    """Import raw YAML to create a new resource entry."""
    _validate_resource_type(body.resource_type)

    data = _validate_yaml_against_schema(body.yaml_content, body.resource_type)

    name = data.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="YAML must contain a 'name' field")

    if await run_in_threadpool(_store.exists, body.resource_type, name):
        raise HTTPException(
            status_code=409,
            detail=f"{body.resource_type} '{name}' already exists. Use PUT to update.",
        )

    await run_in_threadpool(_store.set, body.resource_type, name, body.yaml_content)
    logger.info("Imported %s '%s' from YAML", body.resource_type, name)

    return ApiResponse(
        data=YamlImportResponse(
            name=name,
            resource_type=body.resource_type,
            message=f"{body.resource_type} '{name}' imported successfully",
        ),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/builders/recommend
# ---------------------------------------------------------------------------


@router.post("/recommend", response_model=ApiResponse[Recommendation])
async def recommend_stack(
    body: RecommendInput,
    _user: User = Depends(get_current_user),
) -> ApiResponse[Recommendation]:
    """Return a deterministic agent-stack recommendation from the advisory heuristics.

    This is a pure, stateless endpoint — it calls the engine's ``recommend()``
    function and wraps the result in the standard ApiResponse envelope.  No DB
    reads or writes, no LLM calls.
    """
    result = _recommend(body)
    logger.info(
        "Stack recommendation: framework=%s code_tier=%s deploy=%s",
        result.framework,
        result.code_tier,
        result.deploy_target,
    )
    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# Chat-to-Build endpoint — BYO Claude key, security-sensitive
# ---------------------------------------------------------------------------

# Prefix for the per-user workspace secret that holds the user's Claude API key.
# The full secret name is built by appending the user's stable id:
#   f"{_BUILDER_KEY_PREFIX}__{user.id}"
# Must match the frontend helper in ChatBuildPanel.tsx (builderKeySecretName).
_BUILDER_KEY_PREFIX = "AGENTBREEDER_CLAUDE_BUILDER_KEY"


def _builder_key_name(user: User) -> str:
    """Return the per-user secret name for the BYO Claude API key.

    Format: ``AGENTBREEDER_CLAUDE_BUILDER_KEY__{user.id}``

    The user ``id`` is a stable UUID that cannot change, so this name is safe
    to use as a permanent secrets-backend key.  The same format MUST be used
    in the frontend (see ``builderKeySecretName`` in ChatBuildPanel.tsx).
    """
    return f"{_BUILDER_KEY_PREFIX}__{user.id}"


# Hard limits to prevent prompt-injection / resource exhaustion.
# _MAX_MESSAGES is imported from engine.agent_chat_builder (MAX_HISTORY_MESSAGES)
# so both layers stay in sync automatically.
_MAX_MESSAGES = MAX_HISTORY_MESSAGES
_MAX_TOTAL_CHARS = 100_000


class _ChatMessage(BaseModel):
    """A single chat message (OpenAI-format)."""

    role: Literal["user", "assistant"]
    content: str


class ChatBuildRequest(BaseModel):
    """Request body for POST /builders/chat."""

    messages: list[_ChatMessage] = Field(..., min_length=1)

    @field_validator("messages")
    @classmethod
    def _validate_size(cls, msgs: list[_ChatMessage]) -> list[_ChatMessage]:
        if len(msgs) > _MAX_MESSAGES:
            msg = (
                f"Too many messages ({len(msgs)}). "
                f"The chat-to-build conversation is limited to {_MAX_MESSAGES} turns."
            )
            raise ValueError(msg)
        total_chars = sum(len(m.content) for m in msgs)
        if total_chars > _MAX_TOTAL_CHARS:
            msg = (
                f"Message content too large ({total_chars} chars). "
                f"Maximum total is {_MAX_TOTAL_CHARS} characters."
            )
            raise ValueError(msg)
        return msgs


@router.post("/chat", response_model=ApiResponse[ChatTurnResult])
async def chat_build(
    body: ChatBuildRequest,
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ChatTurnResult]:
    """Drive one turn of the conversational agent builder powered by the user's Claude key.

    Security contract:
    - The API key is read server-side from the workspace secrets backend using a
      per-user secret name (AGENTBREEDER_CLAUDE_BUILDER_KEY__{user.id}) so that
      each user's key is completely isolated — no cross-user key sharing.
    - The key is NEVER stored in the DB, NEVER returned in any response body,
      and NEVER included in any log record.
    - If the key is absent → HTTP 400 with a clear "add your key" message;
      the AnthropicProvider is never constructed.
    - Upstream Anthropic errors are sanitised before returning — the raw
      error (which could contain auth details) is never forwarded to the client.
    - The conversation is bounded: max 40 messages and 100,000 total characters
      (enforced by Pydantic validator above).
    - Message roles are constrained to "user" / "assistant" (Pydantic Literal) —
      system-prompt injection via the messages list is structurally impossible.
    """
    secret_name = _builder_key_name(current_user)

    # ── 1. Read the BYO Claude key from the workspace secrets backend ────
    backend, _ws = get_workspace_backend()
    api_key: str | None = await backend.get(secret_name)

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=_no_key_detail(secret_name),
        )

    # ── 2. Construct a fresh provider with the BYO key ───────────────────
    # A new provider is created per request so keys are never shared across
    # requests or reused in a stale httpx client.
    provider = AnthropicProvider(
        ProviderConfig(
            provider_type=ProviderType.anthropic,
            api_key=api_key,
        )
    )

    # ── 3. Drive one conversation turn ───────────────────────────────────
    # Roles are already constrained to "user"/"assistant" by _ChatMessage.
    history = [{"role": m.role, "content": m.content} for m in body.messages]

    try:
        result = await run_chat_turn(provider, history)
    except AuthenticationError:
        # 401/403 from Anthropic — key is likely invalid.
        # Log a warning WITHOUT the key, return a clean 400.
        logger.warning(
            "chat_build: Anthropic authentication failed for user secret '%s' — "
            "key may be invalid or revoked. (key not logged)",
            secret_name,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "Claude API authentication failed. "
                "Check that your key in "
                f"'{secret_name}' is valid."
            ),
        ) from None
    except ProviderError:
        # Network / upstream error — sanitise message (may contain auth details).
        logger.warning(
            "chat_build: upstream Anthropic error for user secret '%s'. "
            "Details suppressed to avoid key leakage.",
            secret_name,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Upstream error communicating with the Claude API. Please try again in a moment."
            ),
        ) from None
    except Exception:
        logger.error(
            "chat_build: unexpected error for user secret '%s'.",
            secret_name,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred.",
        ) from None
    finally:
        # Always close the httpx client to avoid connection leaks.
        await provider.close()

    return ApiResponse(data=result)


@router.post("/chat/stream")
async def chat_build_stream(
    body: ChatBuildRequest,
    current_user: User = Depends(get_current_user),
) -> EventSourceResponse:
    """Streaming variant of POST /builders/chat.

    Identical BYO-key security contract: the key is read server-side from the
    workspace secrets backend (AGENTBREEDER_CLAUDE_BUILDER_KEY__{user.id}),
    never stored, never returned, never logged. Emits SSE events:
      - "token": {"text": "..."}   incremental assistant text
      - "done":  the final ChatTurnResult as JSON
      - "error": {"detail": "..."} on upstream/auth failure
    """
    secret_name = _builder_key_name(current_user)
    backend, _ws = get_workspace_backend()
    api_key: str | None = await backend.get(secret_name)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=_no_key_detail(secret_name),
        )

    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def generator() -> AsyncGenerator[dict[str, str], None]:
        provider = None
        try:
            provider = AnthropicProvider(
                ProviderConfig(provider_type=ProviderType.anthropic, api_key=api_key)
            )
            async for evt in run_chat_turn_stream(provider, history):
                if evt.type == "token":
                    yield {"event": "token", "data": json.dumps({"text": evt.text})}
                elif evt.type == "setup_request" and evt.setup is not None:
                    yield {"event": "setup_request", "data": json.dumps(asdict(evt.setup))}
                elif evt.type == "done" and evt.result is not None:
                    yield {"event": "done", "data": json.dumps(asdict(evt.result))}
        except AuthenticationError:
            logger.warning(
                "chat_build_stream: auth failed for '%s' (key not logged)", secret_name
            )
            yield {
                "event": "error",
                "data": json.dumps({"detail": "Claude API authentication failed.", "code": "auth_error"}),
            }
        except ProviderError:
            logger.warning(
                "chat_build_stream: upstream error for '%s'.", secret_name
            )
            yield {
                "event": "error",
                "data": json.dumps({"detail": "Upstream Claude API error.", "code": "upstream_error"}),
            }
        finally:
            if provider is not None:
                await provider.close()

    return EventSourceResponse(generator())
