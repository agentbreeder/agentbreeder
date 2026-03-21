"""Application settings using pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):  # type: ignore[misc,unused-ignore]
    """AgentBreeder configuration loaded from environment variables."""

    # Database
    database_url: str = "postgresql+asyncpg://agentbreeder:agentbreeder@localhost:5432/agentbreeder"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Application
    secret_key: str = "change-me-to-a-random-256-bit-key"
    agentbreeder_env: str = "development"

    # Auth
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # Integrations
    litellm_base_url: str = "http://localhost:4000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
