"""Claude SDK runtime builder.

Validates Claude SDK agent code, generates a Dockerfile, and prepares
the build context for containerized deployment.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig
from engine.runtimes.base import ContainerImage, RuntimeBuilder, RuntimeValidationResult, build_env_block

logger = logging.getLogger(__name__)

CLAUDE_SDK_SERVER_TEMPLATE = Path(__file__).parent / "templates" / "claude_sdk_server.py"

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

# Agent environment configuration
{env_block}

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \\
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
"""



class ClaudeSDKRuntime(RuntimeBuilder):
    """Runtime builder for Claude SDK agents."""

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        errors: list[str] = []

        # Check for agent source file
        agent_file = agent_dir / "agent.py"
        if not agent_file.exists():
            errors.append(
                f"Missing agent.py in {agent_dir}. "
                "Claude SDK agents must have an agent.py with an"
                " 'agent', 'app', or 'client' variable."
            )

        # Check for requirements
        has_requirements = (agent_dir / "requirements.txt").exists()
        has_pyproject = (agent_dir / "pyproject.toml").exists()
        if not has_requirements and not has_pyproject:
            errors.append(
                "Missing requirements.txt or pyproject.toml. "
                "Add one with your agent's dependencies."
            )

        # Warn (but don't fail) if ANTHROPIC_API_KEY is not mentioned
        env_example = agent_dir / ".env.example"
        env_file = agent_dir / ".env"
        dotenv_files = [env_example, env_file]
        api_key_mentioned = any(
            f.exists() and "ANTHROPIC_API_KEY" in f.read_text() for f in dotenv_files
        )
        requirements_file = agent_dir / "requirements.txt"
        agent_py = agent_dir / "agent.py"
        for source_file in (requirements_file, agent_py):
            if source_file.exists() and "ANTHROPIC_API_KEY" in source_file.read_text():
                api_key_mentioned = True
                break

        if not api_key_mentioned:
            logger.warning(
                "ANTHROPIC_API_KEY not found in agent directory files. "
                "Ensure it is set as a secret or environment variable at deploy time."
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
        if CLAUDE_SDK_SERVER_TEMPLATE.exists():
            shutil.copy2(CLAUDE_SDK_SERVER_TEMPLATE, build_dir / "server.py")

        # Write Dockerfile
        env_block = self._build_env_block(config)
        dockerfile_content = DOCKERFILE_TEMPLATE.format(env_block=env_block)
        dockerfile = build_dir / "Dockerfile"
        dockerfile.write_text(dockerfile_content)

        tag = f"agentbreeder/{config.name}:{config.version}"

        return ContainerImage(
            tag=tag,
            dockerfile_content=dockerfile_content,
            context_dir=build_dir,
        )

    def get_entrypoint(self, config: AgentConfig) -> str:
        return "uvicorn server:app --host 0.0.0.0 --port 8080"

    def _build_env_block(self, config: AgentConfig) -> str:
        """Build Dockerfile ENV directives from AgentConfig.

        Writes:
        - Core agent identity (AGENT_NAME, AGENT_VERSION, AGENT_MODEL, AGENT_MAX_TOKENS,
                               AGENT_FRAMEWORK, AGENT_SYSTEM_PROMPT)
        - deploy.env_vars (non-secret environment variables)
        - claude_sdk thinking config (AGENT_THINKING_ENABLED, AGENT_THINKING_EFFORT)
        - claude_sdk caching config (AGENT_PROMPT_CACHING)
        - claude_sdk routing config (AGENT_ROUTING_PROVIDER, AGENT_ROUTING_PROJECT_ID,
                                      AGENT_ROUTING_REGION)
        """
        lines: list[str] = []

        # Core agent identity
        safe_name = config.name.replace('"', '\\"')
        lines.append(f'ENV AGENT_NAME="{safe_name}"')
        lines.append(f'ENV AGENT_VERSION="{config.version}"')
        lines.append('ENV AGENT_FRAMEWORK="claude_sdk"')
        safe_model = config.model.primary.replace("\n", " ").replace("\r", " ").replace('"', '\\"')
        lines.append(f'ENV AGENT_MODEL="{safe_model}"')
        if config.model.max_tokens is not None:
            lines.append(f"ENV AGENT_MAX_TOKENS={config.model.max_tokens}")
        if config.model.temperature is not None:
            lines.append(f"ENV AGENT_TEMPERATURE={config.model.temperature}")
        if config.prompts.system:
            safe_sys = config.prompts.system.replace("\n", " ").replace("\r", " ").replace('"', '\\"')
            lines.append(f'ENV AGENT_SYSTEM_PROMPT="{safe_sys}"')

        # deploy.env_vars (non-secret, safe to bake into image layer)
        for key, value in config.deploy.env_vars.items():
            safe_key = key.replace("\n", "").replace("\r", "").replace(" ", "_")
            safe_val = str(value).replace("\n", " ").replace("\r", " ").replace('"', '\\"')
            lines.append(f'ENV {safe_key}="{safe_val}"')

        # Claude SDK — thinking
        sdk = config.claude_sdk
        lines.append(f"ENV AGENT_THINKING_ENABLED={'true' if sdk.thinking.enabled else 'false'}")
        lines.append(f"ENV AGENT_THINKING_EFFORT={sdk.thinking.effort}")

        # Claude SDK — prompt caching
        lines.append(f"ENV AGENT_PROMPT_CACHING={'true' if sdk.prompt_caching else 'false'}")

        # Claude SDK — routing
        lines.append(f"ENV AGENT_ROUTING_PROVIDER={sdk.routing.provider}")
        if sdk.routing.project_id is not None:
            lines.append(f"ENV AGENT_ROUTING_PROJECT_ID={sdk.routing.project_id}")
        if sdk.routing.region is not None:
            lines.append(f"ENV AGENT_ROUTING_REGION={sdk.routing.region}")

        return "\n".join(lines)

    def get_requirements(self, config: AgentConfig) -> list[str]:
        return [
            "anthropic>=0.50.0",
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "httpx>=0.27.0",
            "pydantic>=2.0.0",
        ]
