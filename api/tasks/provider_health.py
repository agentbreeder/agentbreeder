"""Provider health check background task."""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.enums import ProviderStatus
from registry.providers import ProviderRegistry

logger = logging.getLogger(__name__)


async def check_all_providers(session: AsyncSession) -> list[dict]:
    """Iterate all enabled providers and update their health status.

    Simulates a health check with 95% success rate and random latency
    between 100-500ms. Returns a list of status dicts for each provider.

    This function is designed to be called from an API endpoint. In a
    production system it would be triggered by a task queue (e.g. Celery/Redis).
    """
    providers, _ = await ProviderRegistry.list(session, per_page=1000)
    results: list[dict] = []

    for provider in providers:
        if not provider.is_enabled:
            results.append(
                {
                    "provider_id": str(provider.id),
                    "name": provider.name,
                    "status": provider.status.value,
                    "checked": False,
                    "reason": "disabled",
                }
            )
            continue

        # Simulate health check: 95% success rate
        success = random.random() < 0.95
        latency_ms = random.randint(100, 500)

        if success:
            provider.status = ProviderStatus.active
            provider.avg_latency_ms = latency_ms
            provider.last_verified = datetime.now(UTC)
        else:
            provider.status = ProviderStatus.error
            provider.avg_latency_ms = None

        await session.flush()

        results.append(
            {
                "provider_id": str(provider.id),
                "name": provider.name,
                "status": provider.status.value,
                "checked": True,
                "latency_ms": latency_ms if success else None,
                "success": success,
            }
        )
        logger.info(
            "Health check for '%s': %s (latency=%s ms)",
            provider.name,
            "ok" if success else "error",
            latency_ms if success else "N/A",
        )

    return results
