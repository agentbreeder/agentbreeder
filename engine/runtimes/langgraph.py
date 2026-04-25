"""LangGraph runtime builder.

Validates LangGraph agent code, generates a Dockerfile, and prepares
the build context for containerized deployment.
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
    _get_litellm_requirements,
    _should_add_litellm_sdk,
    build_env_block,
)

LANGGRAPH_SERVER_TEMPLATE = Path(__file__).parent / "templates" / "langgraph_server.py"

DOCKERFILE_TEMPLATE = """\
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


class LangGraphRuntime(RuntimeBuilder):
    """Runtime builder for LangGraph agents."""

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        errors: list[str] = []

        # Check for agent source file
        agent_file = agent_dir / "agent.py"
        if not agent_file.exists():
            errors.append(
                f"Missing agent.py in {agent_dir}. "
                "LangGraph agents must have an agent.py with a 'graph' or 'app' variable."
            )

        # Check for requirements
        has_requirements = (agent_dir / "requirements.txt").exists()
        has_pyproject = (agent_dir / "pyproject.toml").exists()
        if not has_requirements and not has_pyproject:
            errors.append(
                "Missing requirements.txt or pyproject.toml. "
                "Add one with your agent's dependencies."
            )

        return RuntimeValidationResult(valid=len(errors) == 0, errors=errors)

    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage:
        """Generate Dockerfile and prepare build context."""
        # Create a temp build context
        build_dir = Path(tempfile.mkdtemp(prefix="agentbreeder-build-"))

        # Copy agent source code
        for item in agent_dir.iterdir():
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            dest = build_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            else:
                shutil.copy2(item, dest)

        # Ensure requirements.txt exists with framework deps
        requirements_file = build_dir / "requirements.txt"
        existing_requirements = ""
        if requirements_file.exists():
            existing_requirements = requirements_file.read_text()

        framework_deps = self.get_requirements(config)
        all_deps = set(existing_requirements.strip().splitlines()) | set(framework_deps)
        requirements_file.write_text("\n".join(sorted(all_deps)) + "\n")

        # Copy the server wrapper template
        if LANGGRAPH_SERVER_TEMPLATE.exists():
            shutil.copy2(LANGGRAPH_SERVER_TEMPLATE, build_dir / "server.py")

        # Write Dockerfile
        dockerfile = build_dir / "Dockerfile"
        env_block = build_env_block(config, "langgraph")
        ollama_extra = ""
        if config.model.primary.startswith("ollama/"):
            ollama_extra = '\nENV OLLAMA_BASE_URL="http://agentbreeder-ollama:11434"'
        dockerfile_content = (
            DOCKERFILE_TEMPLATE.rstrip()
            + "\n\n# Agent configuration\n"
            + env_block
            + ollama_extra
            + "\n"
        )
        dockerfile.write_text(dockerfile_content)

        tag = f"agentbreeder/{config.name}:{config.version}"

        return ContainerImage(
            tag=tag,
            dockerfile_content=dockerfile_content,
            context_dir=build_dir,
        )

    def get_entrypoint(self, config: AgentConfig) -> str:
        return "uvicorn server:app --host 0.0.0.0 --port 8080"

    def get_requirements(self, config: AgentConfig) -> list[str]:
        deps = [
            "langgraph>=0.2.0",
            "langchain-core>=0.3.0",
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "httpx>=0.27.0",
            "pydantic>=2.0.0",
        ]
        model = config.model.primary
        if model.startswith("ollama/"):
            deps.append("langchain-ollama>=0.2.0")
        elif model.startswith("claude"):
            deps.append("langchain-anthropic>=0.3.0")
        elif model.startswith("gemini"):
            deps.append("langchain-google-genai>=2.0.0")
        else:
            deps.append("langchain-openai>=0.2.0")
        if _should_add_litellm_sdk(config):
            deps.extend(_get_litellm_requirements())
        return deps
