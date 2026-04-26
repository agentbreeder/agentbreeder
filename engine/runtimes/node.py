"""Node.js runtime family — dispatches to TypeScript framework templates."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig
from engine.runtimes.base import ContainerImage, RuntimeBuilder, RuntimeValidationResult

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates" / "node"

FRAMEWORK_TEMPLATES: dict[str, str] = {
    "vercel-ai": "vercel_ai_server.ts",
    "mastra": "mastra_server.ts",
    "langchain-js": "langchain_js_server.ts",
    "openai-agents-ts": "openai_agents_ts_server.ts",
    "deepagent": "deepagent_server.ts",
    "custom": "custom_node_server.ts",
    "mcp-ts": "mcp_ts_server.ts",
    "mcp-py": "mcp_py_server.ts",
}

FRAMEWORK_DEPS: dict[str, list[str]] = {
    "vercel-ai": ["ai", "@ai-sdk/openai"],
    "mastra": ["@mastra/core"],
    "langchain-js": ["langchain", "@langchain/core"],
    "openai-agents-ts": ["@openai/agents"],
    "deepagent": ["deepagent"],
    "custom": [],
    "mcp-ts": ["@modelcontextprotocol/sdk"],
    "mcp-py": [],
}


def _resolve_framework(config: AgentConfig) -> str:
    """Get the framework string, defaulting to 'custom' if unknown."""
    framework = config.runtime.framework if config.runtime else "custom"
    if framework not in FRAMEWORK_TEMPLATES:
        logger.warning("Unknown Node.js framework '%s' — falling back to 'custom'", framework)
        return "custom"
    return framework


def _substitute_placeholders(template: str, config: AgentConfig, port: str = "3000") -> str:
    """Replace {{PLACEHOLDER}} tokens in a template string."""
    framework = config.runtime.framework if config.runtime else "custom"
    return (
        template.replace("{{AGENT_NAME}}", config.name)
        .replace("{{AGENT_VERSION}}", config.version)
        .replace("{{AGENT_FRAMEWORK}}", framework)
        .replace("{{PORT}}", port)
    )


def _build_package_json(agent_name: str, framework: str, extra_deps: list[str]) -> str:
    import json

    deps: dict[str, str] = {
        "@agentbreeder/aps-client": "^0.1.0",
        "ts-node": "^10.9.2",
        "typescript": "^5.4.0",
    }
    for dep in extra_deps:
        deps[dep] = "latest"

    pkg = {
        "name": agent_name,
        "version": "1.0.0",
        "private": True,
        "type": "module",
        "scripts": {"start": "node --loader ts-node/esm server.ts"},
        "dependencies": deps,
    }
    return json.dumps(pkg, indent=2)


def _build_tsconfig() -> str:
    import json

    return json.dumps(
        {
            "compilerOptions": {
                "target": "ES2022",
                "module": "ESNext",
                "moduleResolution": "bundler",
                "strict": True,
                "esModuleInterop": True,
                "lib": ["ES2022"],
            }
        },
        indent=2,
    )


def _build_dockerfile(node_version: str) -> str:
    return f"""\
FROM node:{node_version}-slim AS deps
WORKDIR /app
COPY package.json tsconfig.json ./
RUN npm install --omit=dev

FROM node:{node_version}-slim AS runner
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NODE_ENV=production
EXPOSE 3000
CMD ["node", "--loader", "ts-node/esm", "server.ts"]
"""


class NodeRuntimeFamily(RuntimeBuilder):
    """Builds Node.js agent containers from TypeScript framework templates."""

    def validate(self, agent_dir: Path, config: AgentConfig) -> RuntimeValidationResult:
        errors: list[str] = []
        entrypoint = (
            config.runtime.entrypoint if config.runtime and config.runtime.entrypoint else None
        ) or "agent.ts"
        if not (agent_dir / entrypoint).exists():
            errors.append(f"Missing entrypoint: {entrypoint}")
        return RuntimeValidationResult(valid=len(errors) == 0, errors=errors)

    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage:
        framework = _resolve_framework(config)
        node_version = (config.runtime.version if config.runtime else None) or "20"
        template_file = TEMPLATES_DIR / FRAMEWORK_TEMPLATES[framework]

        # Build context: copy agent dir + inject platform files
        build_dir = Path(tempfile.mkdtemp(prefix="agentbreeder-node-"))

        # Copy developer files
        shutil.copytree(str(agent_dir), str(build_dir), dirs_exist_ok=True)

        # Write platform-managed server.ts (with substitutions)
        server_ts_content = _substitute_placeholders(template_file.read_text(), config)
        (build_dir / "server.ts").write_text(server_ts_content)

        # Write shared loader (with placeholder substitution)
        shared_content = _substitute_placeholders(
            (TEMPLATES_DIR / "_shared_loader.ts").read_text(), config
        )
        (build_dir / "_shared_loader.ts").write_text(shared_content)

        # Write package.json
        extra_deps = FRAMEWORK_DEPS.get(framework, [])
        pkg = _build_package_json(config.name, framework, extra_deps)
        (build_dir / "package.json").write_text(pkg)

        # Write tsconfig.json
        (build_dir / "tsconfig.json").write_text(_build_tsconfig())

        # Write Dockerfile
        dockerfile_content = _build_dockerfile(node_version)
        (build_dir / "Dockerfile").write_text(dockerfile_content)

        return ContainerImage(
            tag=f"agentbreeder/{config.name}:{config.version}",
            dockerfile_content=dockerfile_content,
            context_dir=build_dir,
        )

    def get_entrypoint(self, config: AgentConfig) -> str:
        return "node --loader ts-node/esm server.ts"

    def get_requirements(self, config: AgentConfig) -> list[str]:
        framework = _resolve_framework(config)
        return FRAMEWORK_DEPS.get(framework, [])
