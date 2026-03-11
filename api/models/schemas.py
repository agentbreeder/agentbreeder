"""Pydantic schemas for API request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from api.models.enums import AgentStatus, DeployJobStatus, ProviderStatus, ProviderType, UserRole

T = TypeVar("T")


# --- Standard API Response ---


class ApiMeta(BaseModel):
    page: int = 1
    per_page: int = 20
    total: int = 0


class ApiResponse(BaseModel, Generic[T]):
    """Standard API response wrapper: {data, meta, errors}."""

    data: T
    meta: ApiMeta = Field(default_factory=ApiMeta)
    errors: list[str] = Field(default_factory=list)


# --- Auth Schemas ---


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    team: str = "default"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: UserRole
    team: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Agent Schemas ---


class AgentCreate(BaseModel):
    name: str
    version: str
    description: str = ""
    team: str
    owner: str
    framework: str
    model_primary: str
    model_fallback: str | None = None
    endpoint_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    version: str | None = None
    description: str | None = None
    endpoint_url: str | None = None
    status: AgentStatus | None = None
    tags: list[str] | None = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    description: str
    team: str
    owner: str
    framework: str
    model_primary: str
    model_fallback: str | None
    endpoint_url: str | None
    status: AgentStatus
    tags: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Tool Schemas ---


class ToolCreate(BaseModel):
    name: str
    description: str = ""
    tool_type: str = "mcp_server"
    schema_definition: dict[str, Any] = Field(default_factory=dict)
    endpoint: str | None = None
    source: str = "manual"


class ToolResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    tool_type: str
    endpoint: str | None
    status: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Model Schemas ---


class ModelCreate(BaseModel):
    name: str
    provider: str
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"


class ModelResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    description: str
    status: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Prompt Schemas ---


class PromptCreate(BaseModel):
    name: str
    version: str
    content: str
    description: str = ""
    team: str


class PromptResponse(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    content: str
    description: str
    team: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Deploy Schemas ---


class DeployRequest(BaseModel):
    config_path: str | None = None
    config_yaml: str | None = None
    target: str = "local"


class DeployJobResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str | None = None
    status: DeployJobStatus
    target: str
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


# --- Provider Schemas ---


class ProviderCreate(BaseModel):
    name: str
    provider_type: ProviderType
    base_url: str | None = None
    config: dict[str, Any] | None = None


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    status: ProviderStatus | None = None
    config: dict[str, Any] | None = None


class ProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider_type: ProviderType
    base_url: str | None
    status: ProviderStatus
    last_verified: datetime | None
    latency_ms: int | None
    model_count: int
    config: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProviderTestResult(BaseModel):
    success: bool
    latency_ms: int | None = None
    model_count: int | None = None
    error: str | None = None


class ProviderDiscoverResult(BaseModel):
    models: list[str]
    total: int


# --- Search ---


class SearchResult(BaseModel):
    entity_type: str
    id: uuid.UUID
    name: str
    description: str
    team: str | None = None
    score: float = 1.0
