"""MCP server packager — generates Dockerfiles and tags images.

Handles containerization of MCP servers for deployment as sidecars.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Dockerfile templates per transport type
_DOCKERFILE_TEMPLATES = {
    "stdio": """\
FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY . .
EXPOSE 3000
CMD ["node", "index.js"]
""",
    "sse": """\
FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY . .
EXPOSE 3000
ENV MCP_TRANSPORT=sse
CMD ["node", "index.js"]
""",
    "streamable_http": """\
FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY . .
EXPOSE 3000
ENV MCP_TRANSPORT=streamable_http
CMD ["node", "index.js"]
""",
}


def generate_dockerfile(
    transport: str = "stdio",
    custom_base: str | None = None,
) -> str:
    """Generate a Dockerfile for an MCP server.

    Args:
        transport: MCP transport type (stdio, sse, streamable_http).
        custom_base: Optional custom base image.
    """
    template = _DOCKERFILE_TEMPLATES.get(transport, _DOCKERFILE_TEMPLATES["stdio"])

    if custom_base:
        template = template.replace("FROM node:20-slim", f"FROM {custom_base}")

    return template


def build_image_tag(
    name: str,
    version: str,
    registry_prefix: str = "agentbreeder",
) -> str:
    """Generate a Docker image tag for an MCP server.

    Returns: e.g. "agentbreeder/mcp-zendesk:1.0.0"
    """
    slug = name.lower().replace(" ", "-")
    return f"{registry_prefix}/mcp-{slug}:{version}"


def generate_sidecar_config(
    name: str,
    image_uri: str,
    transport: str = "stdio",
    port: int = 3000,
) -> dict[str, Any]:
    """Generate sidecar container configuration for docker-compose or k8s.

    Returns a dict suitable for inclusion in deploy configuration.
    """
    return {
        "name": f"mcp-{name}",
        "image": image_uri,
        "transport": transport,
        "port": port,
        "environment": {
            "MCP_TRANSPORT": transport,
        },
        "health_check": {
            "path": "/health",
            "interval": "30s",
        },
    }
