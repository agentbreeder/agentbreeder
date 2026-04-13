"""CrewAI runtime builder.

Validates CrewAI agent code, generates a Dockerfile, and prepares
the build context for containerized deployment.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig
from engine.runtimes.base import ContainerImage, RuntimeBuilder, RuntimeValidationResult, build_env_block

CREWAI_SERVER_TEMPLATE = Path(__file__).parent / "templates" / "crewai_server.py"

DOCKERFILE_TEMPLATE = """\
FROM python:3.11-slim

WORKDIR /app

# Install CrewAI dependencies first (layer caching)
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



class CrewAIRuntime(RuntimeBuilder):
    """Runtime builder for CrewAI agents."""

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        errors: list[str] = []

        # Check for crew source file — accept crew.py or agent.py
        crew_file = agent_dir / "crew.py"
        agent_file = agent_dir / "agent.py"
        if not crew_file.exists() and not agent_file.exists():
            errors.append(
                f"Missing crew.py (or agent.py) in {agent_dir}. "
                "CrewAI agents must have a crew.py with a 'crew' variable "
                "(a Crew instance), or agent.py as a fallback."
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
        if CREWAI_SERVER_TEMPLATE.exists():
            shutil.copy2(CREWAI_SERVER_TEMPLATE, build_dir / "server.py")

        # Write Dockerfile
        dockerfile = build_dir / "Dockerfile"
        env_block = build_env_block(config, "crewai")

        # CrewAI-specific ENV directives
        crewai_env_lines: list[str] = []
        if config.crewai is not None:
            # process is validated as enum so safe, but quote for consistency
            crewai_env_lines.append(f'ENV AGENT_CREWAI_PROCESS="{config.crewai.process}"')
            if config.crewai.manager_llm:
                safe_llm = config.crewai.manager_llm.replace("\n", " ").replace("\r", " ").replace('"', '\\"')
                crewai_env_lines.append(f'ENV AGENT_CREWAI_MANAGER_LLM="{safe_llm}"')
            if config.crewai.verbose:
                crewai_env_lines.append("ENV AGENT_CREWAI_VERBOSE=true")
            if config.crewai.memory:
                crewai_env_lines.append("ENV AGENT_CREWAI_MEMORY=true")

        crewai_env_block = ("\n" + "\n".join(crewai_env_lines)) if crewai_env_lines else ""
        dockerfile_content = (
            DOCKERFILE_TEMPLATE.rstrip()
            + "\n\n# Agent configuration\n"
            + env_block
            + crewai_env_block
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
        return [
            "crewai>=0.80.0",
            "crewai-tools>=0.4.0",
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "httpx>=0.27.0",
            "pydantic>=2.0.0",
        ]
