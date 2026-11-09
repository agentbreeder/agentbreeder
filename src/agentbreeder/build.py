"""`agentbreeder build agent.yaml`: resolve refs, assemble a build context, build the image, and
write the deploy manifest for one environment. Build never deploys and never reads secrets.

Build context (what the runtime's fixed Dockerfile COPYs):
    Dockerfile, runtime/ (incl. requirements.lock) from the pinned runtime version
    artifacts/<kind>/<name>/                    resolved prompt, knowledge and tool versions
    artifacts/paths/<…>                         path refs (iteration only)
    agent.config.json                           data only, derived from agent.yaml + the lock
Eval sets are resolved and locked but not copied into the image (release uses them).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from agentbreeder import registry
from agentbreeder.agentfile import AgentFile, load_agent_file
from agentbreeder.lock import Lock, materialise
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import OperatorConfig
from agentbreeder.strict_yaml import StrictYamlError, load_file

IMAGE_KINDS = ("prompt", "knowledge", "tool")
RUNTIME_FILES = ("Dockerfile", "runtime")  # the runtime keeps its requirements.lock in runtime/


class BuildError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass(frozen=True)
class BuildResult:
    name: str
    image: str
    manifest_path: Path
    candidate_manifest_path: Path | None
    build_record_path: Path
    lock_path: Path
    lock_changed: bool
    refs: list[dict]
    aliases: list[str]

    def as_json(self) -> dict:
        return {
            "name": self.name,
            "image": self.image,
            "manifest_path": str(self.manifest_path),
            "candidate_manifest_path": str(self.candidate_manifest_path)
            if self.candidate_manifest_path
            else None,
            "build_record_path": str(self.build_record_path),
            "lock": {"path": str(self.lock_path), "changed": self.lock_changed},
            "refs": self.refs,
            "aliases": self.aliases,
        }


def _knowledge_alias(directory: Path) -> str:
    try:
        manifest = load_file(directory / "manifest.yaml")
    except StrictYamlError as error:
        raise BuildError("ARTIFACT_INVALID", f"knowledge manifest: {error}") from error
    alias = (manifest.get("embedding") or {}).get("alias")
    if not isinstance(alias, str) or not alias:
        raise BuildError("ARTIFACT_INVALID", "knowledge manifest needs embedding.alias")
    return alias


def _config(agent: AgentFile, placed: dict[str, Path], context: Path) -> dict:
    def rel(path: Path) -> str:
        return path.relative_to(context).as_posix()

    prompt = placed[agent.prompt.ref or f"path:{Path(agent.prompt.path).as_posix()}"]
    return {
        "name": agent.name,
        "description": agent.description,
        "model": {
            "alias": agent.model.alias,
            "reasoning": agent.model.reasoning,
            "max_model_calls": agent.model.max_model_calls,
        },
        "prompt": {"dir": rel(prompt)},
        "knowledge": [
            {
                "name": registry.Ref.parse(k.ref).name,
                "version": registry.Ref.parse(k.ref).version,
                "dir": rel(placed[k.ref]),
                "top_k": k.top_k,
                "extra_searches": k.extra_searches,
            }
            for k in agent.knowledge
        ],
        "tools": [
            {
                "name": registry.Ref.parse(t.ref).name,
                "version": registry.Ref.parse(t.ref).version,
                "dir": rel(placed[t.ref]),
            }
            for t in agent.tools
        ],
        "mcp": [
            {
                "name": registry.Ref.parse(m.ref).name,
                "version": registry.Ref.parse(m.ref).version,
                "tools": list(m.tools),
            }
            for m in agent.mcp
        ],
        "memory": agent.memory.model_dump(),
        "agent_events": agent.agent_events,
        "traces": agent.traces,
    }


def _world_readable(context: Path) -> None:
    """Directories 0755, files 0644 (0755 if executable): a restrictive umask must not make
    the image unreadable to the non-root runtime user. Nothing secret is in the context."""
    for path in [context, *context.rglob("*")]:
        if path.is_symlink():
            raise BuildError("ARTIFACT_INVALID", f"unexpected symlink in build context: {path}")
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


def _prompt_as_prompt_md(directory: Path) -> None:
    """The runtime reads `<prompt dir>/prompt.md`; a path prompt keeps its own file name in
    the lock (hash of what you wrote) and is renamed only inside the build context."""
    files = [p for p in directory.iterdir() if p.is_file()]
    if len(files) == 1 and files[0].name != "prompt.md":
        files[0].rename(directory / "prompt.md")
    if not (directory / "prompt.md").is_file():
        raise BuildError("ARTIFACT_INVALID", "a path prompt must be one file or contain prompt.md")


def _check_tool_modules(agent: AgentFile, placed: dict[str, Path]) -> None:
    """Python caches modules by name, so two tools exporting the same module name would run
    one package's code for the other. Refuse that at build time."""
    owners: dict[str, str] = {}
    for tool in agent.tools:
        try:
            spec = load_file(placed[tool.ref] / "tool.yaml")
        except StrictYamlError as error:
            raise BuildError("ARTIFACT_INVALID", f"{tool.ref} tool.yaml: {error}") from error
        for function in spec.get("functions") or []:
            module = str((function or {}).get("module", ""))
            if module in owners and owners[module] != tool.ref:
                raise BuildError(
                    "TOOL_MODULE_CONFLICT",
                    f"{tool.ref} and {owners[module]} both define Python module {module!r}",
                )
            owners[module] = tool.ref


# ADK skips an MCP tool with one of these names, so runtime/adk@1.1.0 refuses them at startup.
ADK_RESERVED_TOOLS = frozenset(
    {
        "transfer_to_agent",
        "adk_request_credential",
        "adk_request_confirmation",
        "adk_request_input",
        "set_model_response",
    }
)
KNOWLEDGE_TOOL = "search_knowledge_base"


def _check_mcp_names(agent: AgentFile, placed: dict[str, Path]) -> None:
    """The model sees MCP tools as `<server>_<tool>` (`-` in the server name becomes `_`). The
    runtime refuses, at startup, a reserved name or one that clashes across servers, with a
    function tool or with knowledge search; deploy's health check can't see that, so every turn
    would fail. Build has every input, so it refuses them here."""
    taken: dict[str, str] = {KNOWLEDGE_TOOL: "knowledge search"} if agent.knowledge else {}
    for tool in agent.tools:
        spec = load_file(placed[tool.ref] / "tool.yaml")  # parsed by _check_tool_modules
        for function in spec.get("functions") or []:
            taken[str((function or {}).get("name", ""))] = tool.ref
    for entry in agent.mcp:
        prefix = registry.Ref.parse(entry.ref).name.replace("-", "_")
        reserved = sorted(set(entry.tools) & ADK_RESERVED_TOOLS)
        if reserved:
            raise BuildError(
                "MCP_TOOL_RESERVED",
                f"{entry.ref}: {', '.join(reserved)} is reserved by the ADK runtime",
            )
        for tool in entry.tools:
            name = f"{prefix}_{tool}"
            if name in taken:
                raise BuildError(
                    "MCP_TOOL_CLASH",
                    f"{entry.ref}: the model would see {name}, which {taken[name]} also defines",
                )
            taken[name] = entry.ref


def _check_mcp_tools(agent: AgentFile, placed: dict[str, Path]) -> None:
    """Each `mcp:` entry asks only for tools its version declares (the relay grants a subset
    of those; the operator environment decides which)."""
    from agentbreeder.mcp import load_mcp
    from agentbreeder.services.schema import ServiceError

    for entry in agent.mcp:
        try:
            declared = set(load_mcp(placed[entry.ref]).tools)
        except ServiceError as error:
            raise BuildError("ARTIFACT_INVALID", f"{entry.ref}: {error}") from error
        unknown = sorted(set(entry.tools) - declared)
        if unknown:
            raise BuildError("MCP_TOOL_UNKNOWN", f"{entry.ref} declares no tools {unknown}")


def _runtime_serving(runtime_dir: Path, ref: str) -> dict:
    """The port and health path the runtime serves on, from its own manifest.yaml."""
    try:
        meta = load_file(runtime_dir / "manifest.yaml")
    except StrictYamlError as error:
        raise BuildError("ARTIFACT_INVALID", f"{ref} manifest.yaml: {error}") from error
    port, health = meta.get("port", 8080), meta.get("health_path", "/health")
    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
        or not isinstance(health, str)
        or not re.fullmatch(r"/[A-Za-z0-9/_-]*", health)
    ):
        raise BuildError("ARTIFACT_INVALID", f"{ref} manifest.yaml: invalid port or health_path")
    return {"port": port, "health_path": health}


def _runtime_capabilities(runtime_dir: Path, ref: str) -> frozenset[str]:
    """What the runtime supports beyond serving an agent, from its manifest.yaml: `mcp` means it
    has an MCP client for the stack relay (runtime/adk@1.1.0 and later)."""
    try:
        meta = load_file(runtime_dir / "manifest.yaml")
    except StrictYamlError as error:
        raise BuildError("ARTIFACT_INVALID", f"{ref} manifest.yaml: {error}") from error
    values = meta.get("capabilities", [])
    if (
        not isinstance(values, list)
        or not all(
            isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", v) for v in values
        )
        or len(set(values)) != len(values)
    ):
        raise BuildError("ARTIFACT_INVALID", f"{ref} manifest.yaml: invalid capabilities")
    return frozenset(values)


def _manifest(agent, operator, environment, image, identity, aliases, name, serving) -> dict:
    env = operator.model.environments[environment]
    data: dict = {
        "schema_version": 2,
        "name": name,
        "image": image,
        "target": "local",
        **serving,
        "identity": identity.model_dump(exclude_none=True),
    }
    if env.bindings:
        data["secrets"] = dict(env.bindings)
    access = {"gateway": env.gateway, "aliases": aliases}
    if agent.model.budget_usd is not None:
        access["budget_usd"] = agent.model.budget_usd
    if agent.model.rpm is not None:
        access["rpm"] = agent.model.rpm
    data["model_access"] = access
    if agent.mcp:
        data["mcp_access"] = [{"ref": m.ref, "tools": list(m.tools)} for m in agent.mcp]
    try:
        Manifest.model_validate(data)  # generated manifests must satisfy the deploy contract
    except ValidationError as error:
        where = ", ".join(".".join(map(str, e["loc"])) for e in error.errors()[:5])
        raise BuildError(
            "MANIFEST_INVALID", f"generated deploy manifest is invalid: {where}"
        ) from None
    return data


def docker_build(context: Path, iidfile: Path) -> str:
    from agentbreeder.runtime import Docker

    docker = Docker()
    docker.authorize()
    docker.command("build", "--quiet", "--iidfile", str(iidfile), str(context), timeout=1800)
    return iidfile.read_text().strip()


def build(
    agent_path: Path,
    operator: OperatorConfig,
    environment: str | None = None,
    *,
    mode: str = "plain",
    update: set[str] | None = None,
    output: Path | None = None,
    builder: Callable[[Path, Path], str] = docker_build,
) -> BuildResult:
    agent = load_agent_file(agent_path)
    project = agent_path.resolve().parent
    name = environment or operator.model.default_environment
    if not name or name not in operator.model.environments:
        raise BuildError("CONTEXT_ENVIRONMENT_UNKNOWN", "Unknown or missing --environment")
    env = operator.model.environments[name]
    if env.gateway is None:
        raise BuildError(
            "MODEL_ACCESS_UNCONFIGURED", f"Environment {name} has no gateway for built agents"
        )
    identities = env.agents.get(agent.name)
    if identities is None:
        raise BuildError(
            "IDENTITY_UNBOUND",
            f"Environment {name} has no identity for agent {agent.name}; add "
            f"environments.{name}.agents.{agent.name}.identity to the operator config",
        )
    lock_path = project / "agent.lock"
    lock = Lock.load(lock_path)
    output = (output or project / "deploy").resolve()
    with tempfile.TemporaryDirectory(prefix="agentbreeder-build-") as temporary:
        context = Path(temporary) / "context"
        artifacts = context / "artifacts"
        entries, changed = materialise(
            refs=agent.refs(),
            paths=agent.paths(),
            project_dir=project,
            search=registry.roots(project),
            lock=lock,
            context=artifacts,
            mode=mode,
            update=update,
        )
        runtime_dir = artifacts / "runtime" / registry.Ref.parse(agent.runtime).name
        serving = _runtime_serving(runtime_dir, agent.runtime)
        capabilities = _runtime_capabilities(runtime_dir, agent.runtime)
        if agent.mcp and "mcp" not in capabilities:
            raise BuildError(
                "MCP_RUNTIME_UNSUPPORTED",
                f"{agent.runtime} has no MCP client, so the agent's mcp: tools would never "
                "appear; use runtime/adk@1.1.0 or later",
            )
        for item in RUNTIME_FILES:
            if not (runtime_dir / item).exists():
                raise BuildError("ARTIFACT_INVALID", f"{agent.runtime} is missing {item}")
            shutil.move(str(runtime_dir / item), str(context / item))
        shutil.rmtree(artifacts / "runtime")
        shutil.rmtree(artifacts / "eval", ignore_errors=True)
        placed = {entry.key: entry.path for entry in entries}
        _check_mcp_tools(agent, placed)
        shutil.rmtree(artifacts / "mcp", ignore_errors=True)  # for the relay, not the image
        if agent.prompt.path:
            _prompt_as_prompt_md(placed[f"path:{Path(agent.prompt.path).as_posix()}"])
        _check_tool_modules(agent, placed)
        _check_mcp_names(agent, placed)
        config = _config(agent, placed, context)
        (context / "agent.config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
        _world_readable(context)  # the image runs as uid 65532; COPY keeps these modes
        aliases = [agent.model.alias]
        for knowledge in agent.knowledge:
            alias = _knowledge_alias(placed[knowledge.ref])
            if alias not in aliases:
                aliases.append(alias)
        image = builder(context, Path(temporary) / "image-id")
    if not image.startswith("sha256:"):
        raise BuildError("BUILD_FAILED", "docker build did not return an image ID")
    output.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(
        agent, operator, name, image, identities.identity, aliases, agent.name, serving
    )
    manifest_path = output / f"{agent.name}.deploy.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    candidate_path = None
    if identities.candidate_identity is not None:
        candidate = _manifest(
            agent,
            operator,
            name,
            image,
            identities.candidate_identity,
            aliases,
            f"{agent.name}-candidate",
            serving,
        )
        candidate_path = output / f"{agent.name}-candidate.deploy.yaml"
        candidate_path.write_text(yaml.safe_dump(candidate, sort_keys=False))
    if changed and mode != "locked":
        lock.save(lock_path)
    refs = [{"ref": e.key, "sha256": e.sha256, "source": e.source} for e in entries]
    record = {
        "name": agent.name,
        "environment": name,
        "image": image,
        "agent_yaml_sha256": "sha256:" + hashlib.sha256(agent_path.read_bytes()).hexdigest(),
        "lock_sha256": "sha256:" + hashlib.sha256(lock_path.read_bytes()).hexdigest()
        if lock_path.exists()
        else None,
        "runtime": agent.runtime,
        "refs": refs,
        "aliases": aliases,
    }
    record_path = output / f"{agent.name}.build.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    return BuildResult(
        agent.name,
        image,
        manifest_path,
        candidate_path,
        record_path,
        lock_path,
        changed and mode != "locked",
        refs,
        aliases,
    )
