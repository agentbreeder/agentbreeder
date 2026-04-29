"""Go runtime builder — packages a Go agent for deployment.

Mirrors :mod:`engine.runtimes.python` and :mod:`engine.runtimes.node` for the
Go ecosystem. Tier-2 polyglot: we don't ship per-framework templates yet
(eino, genkit, dapr_agents — all in follow-up issues). The default and only
framework today is ``custom`` — the user writes their own ``main.go`` that
imports ``github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder`` and
calls :pyfunc:`agentbreeder.NewServer`.

The build context produced here:

- preserves the user's source (``main.go``, ``go.mod``, ``go.sum``);
- injects a multi-stage Dockerfile (golang:1.22-alpine builder ->
  gcr.io/distroless/static final);
- writes ENV lines from agent.yaml so the SDK's :pyfunc:`envOr` defaults
  pick up ``AGENT_NAME``, ``AGENT_VERSION``, ``AGENT_FRAMEWORK``.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig
from engine.runtimes.base import (
    ContainerImage,
    RuntimeBuilder,
    RuntimeValidationResult,
    build_env_block,
)

logger = logging.getLogger(__name__)

GO_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "go"

SUPPORTED_FRAMEWORKS: dict[str, str] = {
    # framework key -> template main.go file
    "custom": "custom_main.go",
}


def _resolve_framework(config: AgentConfig) -> str:
    framework = config.runtime.framework if config.runtime else "custom"
    if framework not in SUPPORTED_FRAMEWORKS:
        logger.warning(
            "Unknown Go framework '%s' — falling back to 'custom'. "
            "Frameworks eino/genkit/dapr_agents/langchaingo are tracked in "
            "follow-up issues.",
            framework,
        )
        return "custom"
    return framework


def _build_dockerfile(go_version: str, env_block: str) -> str:
    """Multi-stage Dockerfile.

    The ``builder`` stage compiles a static binary; the final stage is
    distroless/static for a tiny attack surface and < 20 MB images.
    """
    return f"""\
# syntax=docker/dockerfile:1.7
FROM golang:{go_version}-alpine AS builder
WORKDIR /src
RUN apk add --no-cache git ca-certificates
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64
RUN go build -trimpath -ldflags='-s -w' -o /out/agent ./...

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app
COPY --from=builder /out/agent /app/agent
EXPOSE 8080
USER nonroot:nonroot

# Agent configuration
{env_block}

ENTRYPOINT ["/app/agent"]
"""


class GoRuntimeFamily(RuntimeBuilder):
    """Runtime builder for Go agents using the AgentBreeder Go SDK."""

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        errors: list[str] = []

        has_main = (agent_dir / "main.go").exists()
        has_dockerfile = (agent_dir / "Dockerfile").exists()
        has_gomod = (agent_dir / "go.mod").exists()

        if not has_main and not has_dockerfile:
            errors.append(
                f"No entry point found in {agent_dir}. "
                "Go agents must provide either 'main.go' (recommended) or "
                "their own 'Dockerfile' (BYO Docker)."
            )

        if not has_gomod and not has_dockerfile:
            errors.append(
                "Missing go.mod. Initialize with `go mod init <module>` and "
                "add `github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder`."
            )

        return RuntimeValidationResult(valid=len(errors) == 0, errors=errors)

    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage:
        framework = _resolve_framework(config)
        go_version = (config.runtime.version if config.runtime else None) or "1.22"

        build_dir = Path(tempfile.mkdtemp(prefix="agentbreeder-go-"))

        # Copy developer files (skip hidden + caches).
        for item in agent_dir.iterdir():
            if item.name.startswith(".") or item.name in {"vendor", "bin"}:
                continue
            dest = build_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns(".git", "node_modules"))
            else:
                shutil.copy2(item, dest)

        user_has_dockerfile = (build_dir / "Dockerfile").exists()

        if user_has_dockerfile:
            dockerfile_content = (build_dir / "Dockerfile").read_text()
        else:
            # Inject template main.go only when the user has not provided
            # their own.
            template_path = GO_TEMPLATES_DIR / SUPPORTED_FRAMEWORKS[framework]
            if not (build_dir / "main.go").exists() and template_path.exists():
                shutil.copy2(template_path, build_dir / "main.go")

            env_block = build_env_block(config, f"go-{framework}")
            dockerfile_content = _build_dockerfile(go_version, env_block)
            (build_dir / "Dockerfile").write_text(dockerfile_content)

        return ContainerImage(
            tag=f"agentbreeder/{config.name}:{config.version}",
            dockerfile_content=dockerfile_content,
            context_dir=build_dir,
        )

    def get_entrypoint(self, config: AgentConfig) -> str:
        return "/app/agent"

    def get_requirements(self, config: AgentConfig) -> list[str]:
        # Go modules manage their own deps; nothing to surface to a
        # Python-style requirements list.
        return []
