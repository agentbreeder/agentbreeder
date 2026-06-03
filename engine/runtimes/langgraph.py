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
    runtime_support_requirement,
    stage_local_wheel,
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
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8080')+'/health')"

# Honor $PORT: when the observability sidecar is injected the deployer sets
# PORT=8081 so the sidecar can front public traffic on 8080.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
"""


class LangGraphRuntime(RuntimeBuilder):
    """Runtime builder for LangGraph agents."""

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        """Validate a LangGraph agent directory.

        Requires ``agent_dir`` to exist and contain ``agent.py`` (exporting a
        ``graph`` or ``app`` variable) plus ``requirements.txt`` or
        ``pyproject.toml`` declaring dependencies.
        """
        precondition = self._check_agent_dir(agent_dir)
        if precondition is not None:
            return precondition

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
        """Generate Dockerfile and prepare build context.

        On any failure the temp build context is removed so we never leak
        ``/tmp/agentbreeder-build-*`` directories (audit finding A2).
        """
        build_dir = Path(tempfile.mkdtemp(prefix="agentbreeder-build-"))
        try:
            # Copy agent source code
            for item in agent_dir.iterdir():
                if item.name.startswith(".") or item.name == "__pycache__":
                    continue
                dest = build_dir / item.name
                if item.is_dir():
                    shutil.copytree(
                        item, dest, ignore=shutil.ignore_patterns("__pycache__", ".git")
                    )
                else:
                    shutil.copy2(item, dest)

            # Ensure requirements.txt exists with framework deps
            requirements_file = build_dir / "requirements.txt"
            existing_requirements = ""
            if requirements_file.exists():
                existing_requirements = requirements_file.read_text()

            framework_deps = self.get_requirements(config)
            all_deps = sorted(
                set(existing_requirements.strip().splitlines()) | set(framework_deps)
            )
            # If a local wheel was requested (e.g. a dev build via
            # AGENTBREEDER_RUNTIME_REQUIREMENT), copy it into the context and
            # rewrite the requirement to a bare filename so pip can install it.
            wheel_name = stage_local_wheel(build_dir, all_deps)
            requirements_file.write_text("\n".join(all_deps) + "\n")

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
            if wheel_name:
                # COPY the wheel in before `pip install` runs (the template
                # installs requirements before `COPY . .`).
                dockerfile_content = dockerfile_content.replace(
                    "COPY requirements.txt .\n",
                    f"COPY requirements.txt .\nCOPY {wheel_name} ./\n",
                    1,
                )
            dockerfile.write_text(dockerfile_content)

            tag = f"agentbreeder/{config.name}:{config.version}"

            return ContainerImage(
                tag=tag,
                dockerfile_content=dockerfile_content,
                context_dir=build_dir,
            )
        except Exception:
            shutil.rmtree(build_dir, ignore_errors=True)
            raise

    def get_entrypoint(self, config: AgentConfig) -> str:
        """Return the LangGraph container startup command.

        LangGraph agents are wrapped in a FastAPI ``server.py`` and served by
        uvicorn on port 8080.
        """
        return "uvicorn server:app --host 0.0.0.0 --port 8080"

    def get_requirements(self, config: AgentConfig) -> list[str]:
        """Return pip dependencies for LangGraph agents.

        Always includes ``langgraph``, ``langchain-core``, FastAPI server deps,
        and a model-specific LangChain integration (``langchain-openai``,
        ``langchain-anthropic``, ``langchain-google-genai``, or
        ``langchain-ollama``) picked from ``config.model.primary``.
        ``litellm`` is added when the model uses a LiteLLM-routable prefix.
        """
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
        # MCP servers → the agent loads their tools via langchain-mcp-adapters
        # (agenthub.mcp.load_mcp_tools).
        if config.mcp_servers:
            deps.append("langchain-mcp-adapters>=0.1.0")
        support = runtime_support_requirement()
        if support:
            deps.append(support)
        return deps
