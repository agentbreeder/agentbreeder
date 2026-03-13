"""API versioning middleware and deprecation policy utilities."""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# ── Deprecation registry ────────────────────────────────────────────────────
# Maps deprecated API path prefixes to deprecation metadata.
# Add entries here when deprecating endpoints.
#
# Format:
#   "/api/v1/some-path": {
#       "sunset": "2027-01-01",          # RFC 8594 Sunset date (YYYY-MM-DD)
#       "successor": "/api/v2/some-path" # Optional replacement path
#   }

DEPRECATED_PATHS: dict[str, dict[str, str]] = {
    # Example (add real deprecations here as v2 endpoints ship):
    # "/api/v1/registry/search": {
    #     "sunset": "2027-06-01",
    #     "successor": "/api/v2/registry/search",
    # },
}

# Current stable API version
CURRENT_API_VERSION = "v1"
LATEST_API_VERSION = "v1"  # bump to "v2" when v2 is promoted to stable


class APIVersionMiddleware(BaseHTTPMiddleware):
    """Middleware that injects API version headers on every response.

    Headers added:
    - ``X-API-Version``: the version segment from the request path (v1, v2, …)
    - ``X-API-Latest``: the current stable API version
    - ``Deprecation``: ISO 8601 date when the endpoint will be removed (deprecated paths only)
    - ``Sunset``: Same as Deprecation — RFC 8594 format
    - ``Link``: Pointer to the successor endpoint (deprecated paths only)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        path = request.url.path

        # Detect version from path (e.g. /api/v1/... → "v1")
        api_version = _extract_version(path)
        if api_version:
            response.headers["X-API-Version"] = api_version
        response.headers["X-API-Latest"] = LATEST_API_VERSION

        # Inject deprecation headers if this path is deprecated
        for deprecated_prefix, meta in DEPRECATED_PATHS.items():
            if path.startswith(deprecated_prefix):
                sunset = meta.get("sunset", "")
                successor = meta.get("successor", "")

                if sunset:
                    response.headers["Deprecation"] = f'date="{sunset}"'
                    response.headers["Sunset"] = sunset

                if successor:
                    response.headers["Link"] = f'<{successor}>; rel="successor-version"'

                logger.warning(
                    "Deprecated endpoint called",
                    extra={
                        "path": path,
                        "sunset": sunset,
                        "successor": successor,
                        "client": request.client.host if request.client else "unknown",
                    },
                )
                break

        return response


def _extract_version(path: str) -> str | None:
    """Extract the API version segment from a path like /api/v1/agents."""
    parts = path.lstrip("/").split("/")
    for part in parts:
        if part.startswith("v") and part[1:].isdigit():
            return part
    return None


def deprecate_path(prefix: str, *, sunset: str, successor: str = "") -> None:
    """Register a path prefix as deprecated at runtime.

    Args:
        prefix:    URL path prefix to deprecate (e.g. "/api/v1/old-resource")
        sunset:    ISO 8601 / RFC 8594 date string when the endpoint will be removed
                   (e.g. "2027-01-01")
        successor: Optional URL of the replacement endpoint
    """
    DEPRECATED_PATHS[prefix] = {"sunset": sunset, "successor": successor}
    logger.info("API path deprecated", extra={"prefix": prefix, "sunset": sunset})
