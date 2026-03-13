"""JWT inter-agent authentication for A2A communication.

Issues and validates service tokens for agent-to-agent calls.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from jose import jwt
from jose.exceptions import JWTError as InvalidTokenError

logger = logging.getLogger(__name__)

_SECRET_KEY = os.getenv("A2A_JWT_SECRET", os.getenv("SECRET_KEY", "dev-secret"))
_ALGORITHM = "HS256"
_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


def create_service_token(
    agent_name: str,
    team: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a JWT service token for inter-agent authentication."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": f"agent:{agent_name}",
        "iat": now,
        "exp": now + _TOKEN_EXPIRY_SECONDS,
        "type": "a2a_service",
    }
    if team:
        payload["team"] = team
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def validate_service_token(token: str) -> dict[str, Any]:
    """Validate a JWT service token and return claims.

    Raises jwt.InvalidTokenError on failure.
    """
    payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
    if payload.get("type") != "a2a_service":
        raise InvalidTokenError("Not an A2A service token")
    return payload


def extract_agent_name(token: str) -> str | None:
    """Extract the agent name from a service token, or None if invalid."""
    try:
        payload = validate_service_token(token)
        sub = payload.get("sub", "")
        return sub.removeprefix("agent:") if sub.startswith("agent:") else None
    except Exception:
        return None
