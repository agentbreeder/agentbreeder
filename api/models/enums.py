"""Database enums for AgentBreeder."""

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


class A2AStatus(enum.StrEnum):
    registered = "registered"
    active = "active"
    inactive = "inactive"
    error = "error"


class TemplateStatus(enum.StrEnum):
    draft = "draft"
    published = "published"
    deprecated = "deprecated"


class ListingStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    unlisted = "unlisted"


class TemplateCategory(enum.StrEnum):
    customer_support = "customer_support"
    data_analysis = "data_analysis"
    code_review = "code_review"
    research = "research"
    automation = "automation"
    content = "content"
    other = "other"


class KeyScopeType(enum.StrEnum):
    """Who a LiteLLM virtual key is issued to."""

    org = "org"
    team = "team"
    user = "user"
    agent = "agent"
    service_principal = "service_principal"


class BudgetDuration(enum.StrEnum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    yearly = "yearly"


class IncidentSeverity(enum.StrEnum):
    """Severity level for an operational incident."""

    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class IncidentStatus(enum.StrEnum):
    """Lifecycle status for an incident.

    Valid forward transitions: open → investigating → mitigated → resolved.
    """

    open = "open"
    investigating = "investigating"
    mitigated = "mitigated"
    resolved = "resolved"
