"""Database enums for Agent Garden."""

from __future__ import annotations

import enum


class AgentStatus(str, enum.Enum):
    deploying = "deploying"
    running = "running"
    stopped = "stopped"
    failed = "failed"


class DeployJobStatus(str, enum.Enum):
    pending = "pending"
    parsing = "parsing"
    building = "building"
    provisioning = "provisioning"
    deploying = "deploying"
    health_checking = "health_checking"
    registering = "registering"
    completed = "completed"
    failed = "failed"


class UserRole(str, enum.Enum):
    admin = "admin"
    deployer = "deployer"
    contributor = "contributor"
    viewer = "viewer"


class RegistryEntityType(str, enum.Enum):
    agent = "agent"
    tool = "tool"
    model = "model"
    prompt = "prompt"
    knowledge_base = "knowledge_base"


class ProviderType(str, enum.Enum):
    openai = "openai"
    anthropic = "anthropic"
    google = "google"
    ollama = "ollama"
    litellm = "litellm"
    openrouter = "openrouter"


class ProviderStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    error = "error"
