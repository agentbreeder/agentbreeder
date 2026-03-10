"""Base connector interface for registry integrations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseConnector(ABC):
    """Abstract base class for all connectors.

    Connectors are integration plugins that discover external resources
    (tools, models, etc.) and register them in the Agent Garden registry.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable connector name."""

    @abstractmethod
    async def scan(self) -> list[dict]:
        """Scan for resources and return a list of discovered items.

        Each item is a dict with at least:
            - name: str
            - description: str
            - source: str (this connector's name)
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the external service is reachable."""
