"""OpenAI Agents SDK runtime builder.

Validates OpenAI Agents agent code, generates a Dockerfile, and prepares
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
    inject_wheel_copies,
    runtime_support_requirement,
)

OPENAI_AGENTS_SERVER_TEMPLATE = Path(__file__).parent / "templates" / "openai_agents_server.py"

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
    CMD python -c "import os,httpx; httpx.get('http://localhost:'+os.environ.get('PORT','8080')+'/health').raise_for_status()"

# Honor $PORT: when the observability sidecar is injected the deployer sets
# PORT=8081 so the agent listens on an internal port and the sidecar owns 8080.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
"""


class OpenAIAgentsRuntime(RuntimeBuilder):
    """Runtime builder for OpenAI Agents SDK agents."""

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        """Validate an OpenAI Agents SDK agent directory.

        Requires ``agent_dir`` to exist and contain ``agent.py`` or
        ``main.py`` (exporting an ``agents.Agent`` instance named ``agent``)
        plus ``requirements.txt`` or ``pyproject.toml`` declaring
        ``openai-agents`` and ``openai``.
        """
        precondition = self._check_agent_dir(agent_dir)
        if precondition is not None:
            return precondition

        errors: list[str] = []

        # Check for agent source file — accept agent.py or main.py
        has_agent = (agent_dir / "agent.py").exists()
        has_main = (agent_dir / "main.py").exists()
        if not has_agent and not has_main:
            errors.append(
                f"Missing agent.py or main.py in {agent_dir}. "
                "OpenAI Agents agents must have an agent.py or main.py "
                "with an 'agent' variable (an openai.agents.Agent instance)."
            )

        # Check for requirements
        has_requirements = (agent_dir / "requirements.txt").exists()
        has_pyproject = (agent_dir / "pyproject.toml").exists()
        if not has_requirements and not has_pyproject:
            errors.append(
                "Missing requirements.txt or pyproject.toml. "
                "Add one with your agent's dependencies (must include openai-agents)."
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
            all_deps = set(existing_requirements.strip().splitlines()) | set(framework_deps)
            requirements_file.write_text("\n".join(sorted(all_deps)) + "\n")

            # Copy the server wrapper template
            if OPENAI_AGENTS_SERVER_TEMPLATE.exists():
                shutil.copy2(OPENAI_AGENTS_SERVER_TEMPLATE, build_dir / "server.py")

            # Write Dockerfile
            dockerfile = build_dir / "Dockerfile"
            env_block = build_env_block(config, "openai_agents")
            ollama_extra = ""
            if config.model.primary.startswith("ollama/"):
                ollama_extra = '\nENV OLLAMA_BASE_URL="http://agentbreeder-ollama:11434"'
            dockerfile_content = (
                inject_wheel_copies(DOCKERFILE_TEMPLATE, build_dir).rstrip()
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
        except Exception:
            shutil.rmtree(build_dir, ignore_errors=True)
            raise

    def get_entrypoint(self, config: AgentConfig) -> str:
        """Return the OpenAI Agents container startup command.

        Agents are wrapped in a FastAPI ``server.py`` and served by uvicorn on
        port 8080; the wrapper invokes ``Runner.run(agent, ...)`` on each
        ``/invoke`` request.
        """
        return "uvicorn server:app --host 0.0.0.0 --port 8080"

    def get_requirements(self, config: AgentConfig) -> list[str]:
        """Return pip dependencies for OpenAI Agents SDK agents.

        Always includes ``openai-agents``, ``openai`` and the FastAPI server
        deps. ``litellm`` is added when the model uses a LiteLLM-routable
        prefix but is NOT routed through the LiteLLM proxy gateway.
        """
        deps = [
            "openai-agents>=0.1.0",
            "openai>=1.60.0",
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "httpx>=0.27.0",
            "pydantic>=2.0.0",
        ]
        if _should_add_litellm_sdk(config):
            deps.extend(_get_litellm_requirements())
        support = runtime_support_requirement()
        if support:
            deps.append(support)
        return deps
