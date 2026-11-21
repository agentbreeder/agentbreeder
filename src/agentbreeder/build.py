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
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from agentbreeder import registry, runtime_profile, skill_check
from agentbreeder.agentfile import AgentFile, load_agent_file
from agentbreeder.lock import Lock, materialise
from agentbreeder.manifest import IdentityReference, Manifest
from agentbreeder.operator_config import OperatorConfig
from agentbreeder.package import DeployTemplate
from agentbreeder.runtime_profile import KNOWLEDGE_TOOL, Profile, ProfileError
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
    # The environment-neutral deploy template (spec §5.4); `release` puts it in the package.
    deploy: DeployTemplate | None = None

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


# The hash of a placed skill, as the runtime computes it at start (runtime/adk@1.5.0's
# runtime/skill_hash.py; a test loads that file and checks the two agree). Not the lock's hash:
# that one covers the whole registry version, this one the copy placed without `scripts/`.
SKILL_HASH_HEADER = b"agentbreeder-skill-placed-v1\n"


def skill_placed_hash(directory: Path) -> str:
    """`sha256:<hex>` over a fixed header, then for each file under `directory` in path order
    its POSIX relative path and its bytes, each framed by its length."""
    files: dict[str, bytes] = {}
    for parent, dirnames, filenames in os.walk(directory, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(parent, name)
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise BuildError("ARTIFACT_INVALID", "a placed skill holds only files")
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    digest = hashlib.sha256(SKILL_HASH_HEADER)
    for relative in sorted(files):
        name, data = relative.encode(), files[relative]
        digest.update(b"%d:" % len(name) + name + b"%d:" % len(data) + data)
    return "sha256:" + digest.hexdigest()


def _place_skills(
    agent: AgentFile, placed: dict[str, Path], capabilities: frozenset[str]
) -> dict[str, str]:
    """Each skill's placed copy, checked again, without `scripts/`, and its placed hash by
    ref (skills spec §7). A script skill is refused unless the runtime runs scripts, before
    anything is stripped, so a skill's scripts never reach an image that cannot isolate them."""
    hashes: dict[str, str] = {}
    for entry in agent.skills:
        directory = placed[entry.ref]
        try:
            skill_check.check(directory, registry.Ref.parse(entry.ref))
        except registry.RegistryError as error:
            raise BuildError(error.code, str(error).split("] ", 1)[-1]) from None
        if skill_check.runs_code(directory) and "skill_scripts" not in capabilities:
            raise BuildError(
                "RUNTIME_NO_SKILL_SCRIPTS",
                f"{entry.ref} has scripts and {agent.runtime} does not run skill scripts; use "
                "a skill without scripts until a runtime version supports them",
            )
        shutil.rmtree(directory / "scripts", ignore_errors=True)
        if (directory / "scripts").exists() or (directory / "scripts").is_symlink():
            raise BuildError("ARTIFACT_INVALID", f"could not remove scripts/ from {entry.ref}")
        hashes[entry.ref] = skill_placed_hash(directory)
    return hashes


def _config(
    agent: AgentFile,
    placed: dict[str, Path],
    context: Path,
    skill_hashes: dict[str, str] | None = None,
) -> dict:
    def rel(path: Path) -> str:
        return path.relative_to(context).as_posix()

    prompt = placed[_prompt_key(agent.prompt)]
    config = {
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
    if agent.subagents:  # only when present, so a file without them builds the same bytes
        config["subagents"] = [_subagent_config(sub, placed, rel) for sub in agent.subagents]
        config["limits"] = {"turn_model_calls": agent.turn_model_calls()}
    if agent.skills:  # only when present, so a file without them builds the same bytes
        config["skills"] = [
            {
                "name": registry.Ref.parse(s.ref).name,
                "version": registry.Ref.parse(s.ref).version,
                "dir": rel(placed[s.ref]),
                "sha256": (skill_hashes or {})[s.ref],
            }
            for s in agent.skills
        ]
    return config


def _prompt_key(prompt) -> str:
    return prompt.ref or f"path:{Path(prompt.path).as_posix()}"


def _subagent_config(sub, placed: dict[str, Path], rel: Callable[[Path], str]) -> dict:
    """One `agent.config.json` `subagents[]` entry (sub-agents spec §6): data only."""
    return {
        "name": sub.name,
        "description": sub.description,
        "model": {
            "alias": sub.model.alias,
            "reasoning": sub.model.reasoning,
            "max_model_calls": sub.model.max_model_calls,
        },
        "max_calls_per_turn": sub.max_calls_per_turn,
        "prompt": {"dir": rel(placed[_prompt_key(sub.prompt)])},
        "tools": [
            {
                "name": registry.Ref.parse(t.ref).name,
                "version": registry.Ref.parse(t.ref).version,
                "dir": rel(placed[t.ref]),
            }
            for t in sub.tools
        ],
        "mcp": [
            {
                "name": registry.Ref.parse(m.ref).name,
                "version": registry.Ref.parse(m.ref).version,
                "tools": list(m.tools),
            }
            for m in sub.mcp
        ],
        "knowledge": sub.knowledge,
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
    for tool in _all_tools(agent):  # one Python process runs every agent's tools
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


def _all_tools(agent: AgentFile) -> list:
    """The parent's tool entries, then each sub-agent's, each ref once."""
    seen: dict[str, object] = {}
    for tool in [*agent.tools, *(t for sub in agent.subagents for t in sub.tools)]:
        seen.setdefault(tool.ref, tool)
    return list(seen.values())


def _function_names(tools: list, placed: dict[str, Path]) -> dict[str, str]:
    """name -> tool ref for every function the tools define (tool.yaml parsed already by
    _check_tool_modules)."""
    names: dict[str, str] = {}
    for tool in tools:
        spec = load_file(placed[tool.ref] / "tool.yaml")
        for function in spec.get("functions") or []:
            names[str((function or {}).get("name", ""))] = tool.ref
    return names


def _mcp_names(entries: list) -> dict[str, str]:
    """The `<server>_<tool>` name the model sees -> the mcp ref."""
    return {
        f"{registry.Ref.parse(e.ref).name.replace('-', '_')}_{tool}": e.ref
        for e in entries
        for tool in e.tools
    }


def check_subagent_names(agent: AgentFile, placed: dict[str, Path], profile: Profile) -> None:
    """A sub-agent is the parent's tool `ask_<name>` and an agent of that name in the runtime
    (spec §5.3): refuse a clash with the parent's own name, functions, MCP tools or the
    runtime's reserved tool names, and a clash between tools inside one sub-agent. Messages
    name the location only."""
    builtin = "an ADK built-in tool" if profile.legacy else "a tool name the runtime reserves"
    parent = {re.sub(r"[^a-zA-Z0-9_]", "_", agent.name): "the agent's own name"}
    parent |= {name: builtin for name in profile.reserved_tool_names}
    if agent.knowledge:
        parent[KNOWLEDGE_TOOL] = "knowledge search"
    parent |= _function_names(agent.tools, placed) | _mcp_names(agent.mcp)
    for i, sub in enumerate(agent.subagents):
        tool = f"ask_{sub.name}"
        if tool in parent:
            raise BuildError(
                "SUBAGENT_NAME_CLASH",
                f"subagents[{i}].name: the agent would see {tool}, which {parent[tool]} "
                "also defines",
            )
        own: dict[str, str] = {name: builtin for name in profile.reserved_tool_names}
        if sub.knowledge:
            own[KNOWLEDGE_TOOL] = "knowledge search"
        for name, ref in [
            *_function_names(sub.tools, placed).items(),
            *_mcp_names(sub.mcp).items(),
        ]:
            if name in own and own[name] != ref:
                raise BuildError(
                    "SUBAGENT_NAME_CLASH",
                    f"subagents[{i}]: the sub-agent would see {name} from {ref} and {own[name]}",
                )
            own[name] = ref


def _check_mcp_names(agent: AgentFile, placed: dict[str, Path], profile: Profile) -> None:
    """The model sees MCP tools as `<server>_<tool>` (`-` in the server name becomes `_`). The
    runtime refuses, at startup, a reserved name or one that clashes across servers, with a
    function tool or with knowledge search; deploy's health check can't see that, so every turn
    would fail. Build has every input, so it refuses them here.

    The reserved names are the runtime's (`reserved_tool_names`). An MCP server's own tool name
    is checked against them on every runtime (ADK skips such a tool before it is prefixed). A
    runtime that declares `protocol` also has every name the model would see checked: a
    function tool's name and the prefixed MCP name."""
    reserved_names = profile.reserved_tool_names
    owner = "the ADK runtime" if profile.legacy else agent.runtime
    taken: dict[str, str] = {KNOWLEDGE_TOOL: "knowledge search"} if agent.knowledge else {}
    if not profile.legacy:  # every agent's function tools, a sub-agent's included
        for name, ref in _function_names(_all_tools(agent), placed).items():
            if name in reserved_names:
                raise BuildError("TOOL_NAME_RESERVED", f"{ref}: {name} is reserved by {owner}")
    for tool in agent.tools:
        spec = load_file(placed[tool.ref] / "tool.yaml")  # parsed by _check_tool_modules
        for function in spec.get("functions") or []:
            taken[str((function or {}).get("name", ""))] = tool.ref
    for entry in agent.mcp:
        prefix = registry.Ref.parse(entry.ref).name.replace("-", "_")
        reserved = sorted(set(entry.tools) & reserved_names)
        if not profile.legacy:
            reserved += sorted(
                name for tool in entry.tools if (name := f"{prefix}_{tool}") in reserved_names
            )
        if reserved:
            raise BuildError(
                "MCP_TOOL_RESERVED",
                f"{entry.ref}: {', '.join(reserved)} is reserved by {owner}",
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


def _runtime_profile(runtime_dir: Path, ref: str) -> tuple[Profile, runtime_profile.Modules]:
    """Every framework fact `build` uses, read from the resolved runtime version
    (`runtime_profile`, the parser `agentspec` and `registry runtimes` share): its manifest
    keys and its module names. `build` never branches on a framework name."""
    try:
        profile = runtime_profile.load(runtime_dir)
    except ProfileError as error:
        if error.code == "PROTOCOL_UNSUPPORTED":
            raise BuildError(error.code, f"{ref}: {error}") from error
        raise BuildError("ARTIFACT_INVALID", f"{ref} manifest.yaml: {error}") from error
    try:
        return profile, runtime_profile.load_modules(runtime_dir, profile)
    except ProfileError as error:
        raise BuildError(error.code, f"{ref}: {error}") from error


def _runtime_capabilities(runtime_dir: Path, ref: str) -> frozenset[str]:
    """What the runtime supports beyond serving an agent (its profile's capabilities)."""
    return _runtime_profile(runtime_dir, ref)[0].capabilities


def check_runtime(agent: AgentFile, profile: Profile) -> None:
    """Refuse an agent whose parts its runtime version cannot run: a missing capability or a
    count over the runtime's limit. Messages name the runtime ref and the capability or limit."""
    ref = agent.runtime
    if agent.skills and not profile.has("skills"):
        # Refused before docker build, so a skill never reaches an image that would not load
        # it. A runtime that does (runtime/adk@1.5.0) gets it through _place_skills.
        raise BuildError(
            "RUNTIME_NO_SKILLS",
            f"{ref} does not load skills, so the agent's skills: would never "
            "reach the model; remove skills: until a runtime version supports them",
        )
    if agent.subagents and not profile.has("subagents"):
        raise BuildError(
            "RUNTIME_NO_SUBAGENTS",
            f"{ref} does not run sub-agents, so the agent's subagents: would "
            "never be called; remove subagents: until a runtime version supports them",
        )
    if agent.mcp and not profile.has("mcp"):
        raise BuildError(
            "MCP_RUNTIME_UNSUPPORTED",
            f"{ref} has no `mcp` capability (no MCP client), so the agent's mcp: tools would "
            "never appear; use a runtime version that lists it",
        )
    if any(sub.mcp for sub in agent.subagents) and not profile.has("mcp"):
        raise BuildError(
            "MCP_RUNTIME_UNSUPPORTED",
            f"{ref} has no `mcp` capability (no MCP client), so a sub-agent's mcp: tools "
            "would never appear; use a runtime version that lists it",
        )
    if agent.knowledge and not profile.has("knowledge"):
        raise BuildError(
            "RUNTIME_NO_KNOWLEDGE",
            f"{ref} has no `knowledge` capability, so the agent's knowledge: would never be "
            "searched; use a runtime version that lists it",
        )
    if agent.memory.notes and not profile.has("notes"):
        raise BuildError(
            "RUNTIME_NO_NOTES",
            f"{ref} has no `notes` capability, so memory.notes would never be kept; set "
            "memory.notes to false or use a runtime version that lists it",
        )
    # The agent's own lists, as `agent.yaml` counts them (a sub-agent's are bounded there).
    for part, field, used in (
        ("knowledge", "knowledge", len(agent.knowledge)),
        ("tools", "tools", len(agent.tools)),
        ("mcp_servers", "mcp", len(agent.mcp)),
    ):
        allowed = profile.limit(part)
        if used > allowed:
            raise BuildError(
                "RUNTIME_LIMIT",
                f"{ref} takes at most {allowed} {field}: {'entry' if allowed == 1 else 'entries'}"
                f" (limits.{part}); the agent has {used}",
            )


def _check_shadowing(agent: AgentFile, placed: dict[str, Path], names: frozenset[str]) -> None:
    """A tool module named like one of the runtime's own modules, or like a package its lock
    installs, would hide it. `registry publish` checks this against the built-in runtimes
    (`tool_check`); a runtime that ships `runtime/modules.json` is checked here too, because it
    may come from a project registry `tool_check` never saw."""
    for tool in _all_tools(agent):
        spec = load_file(placed[tool.ref] / "tool.yaml")  # parsed by _check_tool_modules
        for function in spec.get("functions") or []:
            module = str((function or {}).get("module", "")).split(".")[0]
            if module in names:
                raise BuildError(
                    "TOOL_MODULE_SHADOWS",
                    f"{tool.ref}: module {module!r} would hide a module of {agent.runtime}",
                )


def _runtime_info(ref: str, profile: Profile) -> dict:
    """The deploy manifest's `runtime` field for a kit runtime: `model_api` and `isolation`
    only when the runtime declares the non-default ones (#1089), so every other manifest keeps
    its bytes."""
    info: dict = {"ref": ref, "profile": "kit"}
    if profile.model_api == "anthropic-messages":
        info["model_api"] = profile.model_api
    if profile.isolation is not None:
        info["isolation"] = profile.isolation
    return info


def _where(error: ValidationError) -> str:
    return ", ".join(".".join(map(str, e["loc"])) for e in error.errors()[:5])


def template(
    agent: AgentFile, serving: dict, aliases: list[str], profile: Profile | None = None
) -> DeployTemplate:
    """The deploy fields derived from the agent and its runtime only (spec §5.4): port,
    health_path, aliases, budget_usd, rpm, mcp_access, guardrails (#1053) and, for a kit
    runtime only, `runtime` (#1083 F1). It is environment-neutral, so a released package can
    carry it; identity, secrets, the gateway, target and image are `bind`'s, and the template
    schema has no field for them."""
    kit = profile is not None and profile.profile == "kit"
    try:
        return DeployTemplate.model_validate(
            {
                "port": serving["port"],
                "health_path": serving["health_path"],
                "aliases": list(aliases),
                "budget_usd": agent.model.budget_usd,
                "rpm": agent.model.rpm,
                "mcp_access": [{"ref": m.ref, "tools": list(m.tools)} for m in agent.mcp],
                "guardrails": list(agent.guardrails) or None,
                # Written only for a kit runtime, so every manifest, package and revision
                # digest of an agent on an earlier runtime keeps its bytes.
                "runtime": _runtime_info(agent.runtime, profile) if kit else None,
            }
        )
    except ValidationError as error:
        raise BuildError(
            "MANIFEST_INVALID", f"generated deploy template is invalid: {_where(error)}"
        ) from None


def bind(
    deploy: DeployTemplate,
    image: str,
    operator: OperatorConfig,
    environment: str,
    identity: IdentityReference,
    name: str,
) -> dict:
    """The deploy manifest for one environment. `identity` (chosen from that environment's
    `agents` entry), `secrets` and `model_access.gateway` come from the operator config only;
    `target` and `image` from the deploy path only; the rest from the template. Returns the
    document `build` writes (this key order), validated against the deploy contract."""
    env = operator.model.environments[environment]
    fields = deploy.model_dump(mode="json")
    data: dict = {
        "schema_version": 2,
        "name": name,
        "image": image,
        "target": "local",
        "port": fields["port"],
        "health_path": fields["health_path"],
        "identity": identity.model_dump(exclude_none=True),
    }
    if env.bindings:
        data["secrets"] = dict(env.bindings)
    access = {"gateway": env.gateway, "aliases": fields["aliases"]}
    for key in ("budget_usd", "rpm"):  # None in the template means the field is omitted
        if fields[key] is not None:
            access[key] = fields[key]
    data["model_access"] = access
    if fields["mcp_access"]:  # the manifest's mcp_access needs at least one entry
        data["mcp_access"] = fields["mcp_access"]
    if fields.get("guardrails"):  # #1053: left out when the agent names none
        data["guardrails"] = list(fields["guardrails"])
    if fields.get("runtime"):  # #1083 F1: a kit runtime only
        data["runtime"] = dict(fields["runtime"])
    try:
        Manifest.model_validate(data)  # generated manifests must satisfy the deploy contract
    except ValidationError as error:
        raise BuildError(
            "MANIFEST_INVALID", f"generated deploy manifest is invalid: {_where(error)}"
        ) from None
    return data


def docker_build(context: Path, iidfile: Path) -> str:
    from agentbreeder.runtime import Docker

    docker = Docker()
    docker.authorize()
    docker.command("build", "--quiet", "--iidfile", str(iidfile), str(context), timeout=1800)
    return iidfile.read_text().strip()


PLATFORM = re.compile(r"[a-z0-9]{1,32}/[a-z0-9_]{1,32}(/[a-z0-9]{1,16})?")


def docker_platform(image: str) -> str:
    """`os/architecture[/variant]` of a local image, as `package.json` records it."""
    from agentbreeder.runtime import Docker

    docker = Docker()
    docker.authorize()
    value = docker.command(
        "image",
        "inspect",
        "--format",
        "{{.Os}}/{{.Architecture}}{{if .Variant}}/{{.Variant}}{{end}}",
        image,
    ).strip()
    if not PLATFORM.fullmatch(value):
        raise BuildError("BUILD_FAILED", "docker image inspect did not return a platform")
    return value


@contextmanager
def _remotes(table: dict | None, offline: bool) -> Iterator:
    """The declared sources for one build (#829), or None without `registries:`. Imported
    only when a source is declared, so a build without one loads no network code."""
    if table is None:
        yield None
        return
    from agentbreeder.sources.remote import Remotes

    with Remotes(table, offline=offline) as remotes:
        yield remotes


def build(
    agent_path: Path,
    operator: OperatorConfig,
    environment: str | None = None,
    *,
    mode: str = "plain",
    update: set[str] | None = None,
    output: Path | None = None,
    builder: Callable[[Path, Path], str] = docker_build,
    offline: bool = False,
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
    table = agent.sources_table()
    with (
        tempfile.TemporaryDirectory(prefix="agentbreeder-build-") as temporary,
        _remotes(table, offline) as remotes,
    ):
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
            remotes=remotes,
        )
        runtime_dir = artifacts / "runtime" / registry.Ref.parse(agent.runtime).name
        serving = _runtime_serving(runtime_dir, agent.runtime)
        profile, modules = _runtime_profile(runtime_dir, agent.runtime)
        check_runtime(agent, profile)
        for item in RUNTIME_FILES:
            if not (runtime_dir / item).exists():
                raise BuildError("ARTIFACT_INVALID", f"{agent.runtime} is missing {item}")
            shutil.move(str(runtime_dir / item), str(context / item))
        shutil.rmtree(artifacts / "runtime")
        shutil.rmtree(artifacts / "eval", ignore_errors=True)
        placed = {entry.key: entry.path for entry in entries}
        _check_mcp_tools(agent, placed)
        shutil.rmtree(artifacts / "mcp", ignore_errors=True)  # for the relay, not the image
        for path in agent.paths():  # the parent's and each sub-agent's path prompt
            _prompt_as_prompt_md(placed[f"path:{Path(path).as_posix()}"])
        _check_tool_modules(agent, placed)
        if not profile.legacy:
            _check_shadowing(agent, placed, modules.names())
        _check_mcp_names(agent, placed, profile)
        check_subagent_names(agent, placed, profile)
        config = _config(
            agent, placed, context, _place_skills(agent, placed, profile.capabilities)
        )
        (context / "agent.config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
        _world_readable(context)  # the image runs as uid 65532; COPY keeps these modes
        # The parent's alias, each sub-agent's, then the knowledge embedding alias (spec §6).
        aliases = agent.aliases([_knowledge_alias(placed[k.ref]) for k in agent.knowledge])
        # Refuse a bad template before docker build.
        deploy = template(agent, serving, aliases, profile)
        image = builder(context, Path(temporary) / "image-id")
    if not image.startswith("sha256:"):
        raise BuildError("BUILD_FAILED", "docker build did not return an image ID")
    output.mkdir(parents=True, exist_ok=True)
    manifest = bind(deploy, image, operator, name, identities.identity, agent.name)
    manifest_path = output / f"{agent.name}.deploy.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    candidate_path = None
    if identities.candidate_identity is not None:
        candidate = bind(
            deploy, image, operator, name, identities.candidate_identity, f"{agent.name}-candidate"
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
    if table is not None:  # only with registries:, so a file without them writes the same record
        record["sources"] = table
    if agent.subagents:  # only when present, so a file without them writes the same record
        record["subagents"] = [{"name": s.name, "alias": s.model.alias} for s in agent.subagents]
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
        deploy,
    )
