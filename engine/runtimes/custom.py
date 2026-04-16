"""Custom (Bring Your Own Framework) runtime builder.

Validates and packages agents that use any framework not natively supported.
If the user provides their own Dockerfile, it is used as-is.
Otherwise, a minimal fallback Dockerfile is generated that wraps agent.py or main.py
with a thin FastAPI server (custom_server.py).
"""

from __future__ import annotations

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

CUSTOM_SERVER_TEMPLATE = Path(__file__).parent / "templates" / "custom_server.py"

FALLBACK_DOCKERFILE = """\
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY . .

# Non-root user for security
RUN useradd -m -r agent && chown -R agent:agent /app
USER agent

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
"""


class CustomRuntime(RuntimeBuilder):
    """Runtime builder for agents using a custom (BYO) framework.

    Supports two modes:
    1. BYO Dockerfile — the user's own Dockerfile is used as-is.
    2. Fallback mode — agent.py or main.py is wrapped with custom_server.py.
    """

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        errors: list[str] = []

        has_dockerfile = (agent_dir / "Dockerfile").exists()
        has_agent_py = (agent_dir / "agent.py").exists()
        has_main_py = (agent_dir / "main.py").exists()

        if not has_dockerfile and not has_agent_py and not has_main_py:
            errors.append(
                f"No entry point found in {agent_dir}. "
                "Custom agents must provide either a 'Dockerfile' (BYO Docker) "
                "or 'agent.py' / 'main.py' (for the thin wrapper server)."
            )

        has_requirements = (agent_dir / "requirements.txt").exists()
        has_pyproject = (agent_dir / "pyproject.toml").exists()
        if not has_requirements and not has_pyproject:
            errors.append(
                "Missing requirements.txt or pyproject.toml. "
                "Add one with your agent's dependencies."
            )

        # Soft warning: BYO Dockerfile should expose port 8080
        if has_dockerfile and not errors:
            dockerfile_content = (agent_dir / "Dockerfile").read_text()
            if "8080" not in dockerfile_content:
                errors.append(
                    "Warning: Dockerfile does not appear to expose port 8080. "
                    "AgentBreeder routes traffic to port 8080 by default. "
                    "Add 'EXPOSE 8080' and ensure your server listens on port 8080."
                )

        return RuntimeValidationResult(valid=len(errors) == 0, errors=errors)

    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage:
        """Generate Dockerfile and prepare build context.

        If the user's agent directory contains a Dockerfile, it is copied and
        used verbatim. Otherwise, the fallback Dockerfile is written and
        custom_server.py is injected as server.py.
        """
        build_dir = Path(tempfile.mkdtemp(prefix="agentbreeder-build-"))

        # Copy agent source code (skip hidden dirs and pycache)
        for item in agent_dir.iterdir():
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            dest = build_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            else:
                shutil.copy2(item, dest)

        user_has_dockerfile = (build_dir / "Dockerfile").exists()

        if user_has_dockerfile:
            # BYO Dockerfile mode: use it as-is; read for ContainerImage metadata
            dockerfile_content = (build_dir / "Dockerfile").read_text()
        else:
            # Fallback mode: merge requirements and inject server wrapper

            # Ensure requirements.txt exists with framework deps
            requirements_file = build_dir / "requirements.txt"
            existing_requirements = ""
            if requirements_file.exists():
                existing_requirements = requirements_file.read_text()

            framework_deps = self.get_requirements(config)
            all_deps = set(existing_requirements.strip().splitlines()) | set(framework_deps)
            # Remove empty strings that arise from splitting empty/blank files
            all_deps.discard("")
            requirements_file.write_text("\n".join(sorted(all_deps)) + "\n")

            # Copy custom_server.py as server.py only if the user has not provided their own
            if not (build_dir / "server.py").exists() and CUSTOM_SERVER_TEMPLATE.exists():
                shutil.copy2(CUSTOM_SERVER_TEMPLATE, build_dir / "server.py")

            # Write fallback Dockerfile
            env_block = build_env_block(config, "custom")
            ollama_extra = ""
            if config.model.primary.startswith("ollama/"):
                ollama_extra = '\nENV OLLAMA_BASE_URL="http://agentbreeder-ollama:11434"'
            dockerfile_content = (
                FALLBACK_DOCKERFILE.rstrip()
                + "\n\n# Agent configuration\n"
                + env_block
                + ollama_extra
                + "\n"
            )
            (build_dir / "Dockerfile").write_text(dockerfile_content)

        tag = f"agentbreeder/{config.name}:{config.version}"

        return ContainerImage(
            tag=tag,
            dockerfile_content=dockerfile_content,
            context_dir=build_dir,
        )

    def get_entrypoint(self, config: AgentConfig) -> str:
        # BYO Dockerfile sets its own CMD; this entrypoint applies to the fallback path.
        return "uvicorn server:app --host 0.0.0.0 --port 8080"

    def get_requirements(self, config: AgentConfig) -> list[str]:
        # Minimal set — users add their own framework deps via requirements.txt
        deps = [
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "httpx>=0.27.0",
            "pydantic>=2.0.0",
        ]
        if config.model.primary.startswith("ollama/"):
            deps.append("litellm>=1.40.0")
        return deps
