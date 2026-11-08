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
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

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
        "memory": agent.memory.model_dump(),
        "agent_events": agent.agent_events,
        "traces": agent.traces,
    }


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


def _manifest(agent, operator, environment, image, identity, aliases, name) -> dict:
    env = operator.model.environments[environment]
    data: dict = {
        "schema_version": 2,
        "name": name,
        "image": image,
        "target": "local",
        "port": 8080,
        "health_path": "/health",
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
    Manifest.model_validate(data)  # generated manifests must satisfy the deploy contract
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
        for item in RUNTIME_FILES:
            if not (runtime_dir / item).exists():
                raise BuildError("ARTIFACT_INVALID", f"{agent.runtime} is missing {item}")
            shutil.move(str(runtime_dir / item), str(context / item))
        shutil.rmtree(artifacts / "runtime")
        shutil.rmtree(artifacts / "eval", ignore_errors=True)
        placed = {entry.key: entry.path for entry in entries}
        if agent.prompt.path:
            _prompt_as_prompt_md(placed[f"path:{Path(agent.prompt.path).as_posix()}"])
        _check_tool_modules(agent, placed)
        config = _config(agent, placed, context)
        (context / "agent.config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
        aliases = [agent.model.alias]
        for knowledge in agent.knowledge:
            alias = _knowledge_alias(placed[knowledge.ref])
            if alias not in aliases:
                aliases.append(alias)
        image = builder(context, Path(temporary) / "image-id")
    if not image.startswith("sha256:"):
        raise BuildError("BUILD_FAILED", "docker build did not return an image ID")
    output.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(agent, operator, name, image, identities.identity, aliases, agent.name)
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
