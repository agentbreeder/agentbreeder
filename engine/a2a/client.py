"""Async HTTP client for invoking deployed agents.

Replaces simulated agent calls with real HTTP POST to /invoke endpoints.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AgentInvocationResult(BaseModel):
    """Result of invoking a deployed agent."""

    output: str
    tokens: int = 0
    latency_ms: int = 0
    status: str = "success"
    error: str | None = None


class AgentInvocationClient:
    """Async client for calling deployed agent /invoke endpoints."""

    def __init__(self, timeout: float = 30.0, auth_token: str | None = None) -> None:
        self._timeout = timeout
        self._auth_token = auth_token
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._auth_token:
                headers["Authorization"] = f"Bearer {self._auth_token}"
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=headers,
            )
        return self._client

    async def invoke(
        self,
        endpoint_url: str,
        input_message: str,
        context: dict[str, Any] | None = None,
    ) -> AgentInvocationResult:
        """POST to an agent's /invoke endpoint and return the result."""
        client = await self._get_client()
        url = endpoint_url.rstrip("/") + "/invoke"
        payload = {"input_message": input_message, "context": context or {}}

        start = time.monotonic()
        try:
            resp = await client.post(url, json=payload)
            latency_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code != 200:
                return AgentInvocationResult(
                    output="",
                    latency_ms=latency_ms,
                    status="error",
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )

            data = resp.json()
            return AgentInvocationResult(
                output=data.get("output", ""),
                tokens=data.get("tokens", 0),
                latency_ms=latency_ms,
                status="success",
            )

        except httpx.TimeoutException:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Agent invocation timed out: %s", endpoint_url)
            return AgentInvocationResult(
                output="",
                latency_ms=latency_ms,
                status="error",
                error="Request timed out",
            )
        except httpx.ConnectError as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Agent connection failed: %s — %s", endpoint_url, e)
            return AgentInvocationResult(
                output="",
                latency_ms=latency_ms,
                status="error",
                error=f"Connection failed: {e}",
            )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
