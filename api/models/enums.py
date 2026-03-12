"""Database enums for Agent Garden."""

from __future__ import annotations

import enum


class AgentStatus(enum.StrEnum):
    deploying = "deploying"
    running = "running"
    stopped = "stopped"
    failed = "failed"


class DeployJobStatus(enum.StrEnum):
    pending = "pending"
    parsing = "parsing"
    building = "building"
    provisioning = "provisioning"
    deploying = "deploying"
    health_checking = "health_checking"
    registering = "registering"
    completed = "completed"
    failed = "failed"


class UserRole(enum.StrEnum):
    admin = "admin"
    deployer = "deployer"
    contributor = "contributor"
    viewer = "viewer"


class RegistryEntityType(enum.StrEnum):
    agent = "agent"
    tool = "tool"
    model = "model"
    prompt = "prompt"
    knowledge_base = "knowledge_base"


class ProviderType(enum.StrEnum):
    openai = "openai"
    anthropic = "anthropic"
    google = "google"
    ollama = "ollama"
    litellm = "litellm"
    openrouter = "openrouter"


class ProviderStatus(enum.StrEnum):
    active = "active"
    disabled = "disabled"
    error = "error"


class EvalRunStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class OrchestrationStatus(enum.StrEnum):
    draft = "draft"
    deployed = "deployed"
    stopped = "stopped"
    failed = "failed"
