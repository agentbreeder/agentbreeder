"""`agent.yaml` (authoring schema v1): what an agent is made of, built by `agentbreeder build`.

This is NOT the deploy manifest (docs/manifest.md), which stays image-in. An agent file is
environment-neutral: gateways, services, secret bindings and identities come from the operator
config for the chosen environment, and nothing here can grant a permission or raise a ceiling.
See docs/superpowers/specs/2026-11-07-agent-yaml-build-design.md §5.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agentbreeder.manifest import _GUARDRAIL as GUARDRAIL_PATTERN
from agentbreeder.registry import Ref, RegistryError
from agentbreeder.strict_yaml import StrictYamlError, load_file

MAX_BYTES = 64 * 1024
NAME_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$"  # ≤ 53: room for "-candidate"
NOTES_TOOL = "tool/customer-notes"
ALIAS_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
# The most any runtime may take; `runtime_profile.ENGINE_LIMITS` holds the same numbers (a test
# keeps them equal). Written here, not imported: the console's server process loads this module
# and its allowlist of AgentBreeder modules stays as it was.
MAX_KNOWLEDGE = 5
MAX_TOOLS = 50
MAX_MCP = 10


class AgentFileError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


class CodedError(ValueError):
    """A validator's refusal that has its own error code (pydantic keeps the exception in the
    error's `ctx`, so `load_agent_file` reports this code instead of AGENT_INVALID)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RefEntry(_Strict):
    ref: str


class PromptSource(_Strict):
    ref: str | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.ref is None) == (self.path is None):
            raise ValueError("prompt needs exactly one of ref or path")
        return self


class Model(_Strict):
    alias: str = Field(pattern=ALIAS_PATTERN)
    reasoning: Literal["none", "low", "medium", "high"] = "low"
    max_model_calls: int = Field(default=4, ge=1, le=20)
    budget_usd: float | None = Field(default=None, gt=0, le=100000)
    rpm: int | None = Field(default=None, ge=1, le=100000)


class Knowledge(_Strict):
    ref: str
    top_k: int = Field(default=5, ge=1, le=20)
    extra_searches: int = Field(default=1, ge=0, le=5)


class McpEntry(_Strict):
    # Only the ref: deploy finds the stack's relay for it, so agent.yaml names no stack.
    ref: str
    tools: list[str] = Field(min_length=1, max_length=100)


class Memory(_Strict):
    sessions: Literal["postgres"] = "postgres"
    notes: bool = False


class Evals(_Strict):
    ref: str
    threshold: float = Field(default=1.0, ge=0, le=1)
    retries: int = Field(default=1, ge=0, le=3)
    # #1058 D6: every case tag must pass at least this rate; off (None) by default.
    tag_threshold: float | None = Field(default=None, ge=0, le=1)


# Sub-agents (#1058, sub-agents spec §5): child agents in the same runtime, called as tools
# named ask_<name>. Kept in their own block (skills and guardrails are neighbours).
SUBAGENT_NAME = r"^[a-z][a-z0-9_]{0,31}$"
MAX_SUBAGENTS = 5
MAX_TURN_MODEL_CALLS = 40
TURN_MODEL_CALLS_MARGIN = 6  # the default turn cap: the parent's calls plus this, at most 40


class SubAgentModel(_Strict):
    """The parent's `Model` without `budget_usd` and `rpm`: those belong to the deployment's
    one gateway key, which covers every agent together (spec §8.1)."""

    alias: str = Field(pattern=ALIAS_PATTERN)
    reasoning: Literal["none", "low", "medium", "high"] = "low"
    max_model_calls: int = Field(default=4, ge=1, le=20)

    @model_validator(mode="before")
    @classmethod
    def _key_fields(cls, data):
        if isinstance(data, dict):
            for key in ("budget_usd", "rpm"):
                if key in data:
                    raise CodedError(
                        "SUBAGENT_FIELD_UNSUPPORTED",
                        f"{key} is set on the agent's model only; it limits the deployment's "
                        "one gateway key, which every sub-agent shares",
                    )
        return data


class SubAgent(_Strict):
    name: str = Field(pattern=SUBAGENT_NAME)
    description: str = Field(min_length=1, max_length=500)
    model: SubAgentModel
    prompt: PromptSource
    tools: list[RefEntry] = Field(default_factory=list, max_length=20)
    mcp: list[McpEntry] = Field(default_factory=list, max_length=10)
    knowledge: bool = False
    max_calls_per_turn: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="before")
    @classmethod
    def _depth(cls, data):
        if isinstance(data, dict) and "subagents" in data:
            raise CodedError("SUBAGENT_DEPTH", "a sub-agent cannot have sub-agents (depth is 1)")
        return data


class Limits(_Strict):
    # Model calls per turn, every agent together (spec §7.4).
    turn_model_calls: int = Field(ge=1, le=MAX_TURN_MODEL_CALLS)


class AgentFile(_Strict):
    kind: Literal["agent"]
    schema_version: Literal[1]
    name: str = Field(pattern=NAME_PATTERN)
    runtime: str
    description: str = Field(default="", max_length=500)
    model: Model
    prompt: PromptSource
    # The most any runtime may take (`runtime_profile.ENGINE_LIMITS`). How many the pinned
    # runtime version takes is its own `limits` (one knowledge set on every `runtime/adk`
    # version so far), which `build` checks (`RUNTIME_LIMIT`, #1083 F1).
    knowledge: list[Knowledge] = Field(default_factory=list, max_length=MAX_KNOWLEDGE)
    tools: list[RefEntry] = Field(default_factory=list, max_length=MAX_TOOLS)
    mcp: list[McpEntry] = Field(default_factory=list, max_length=MAX_MCP)
    memory: Memory = Field(default_factory=Memory)
    agent_events: Literal["postgres", "none"] = "postgres"
    traces: Literal["otlp", "none"] = "otlp"
    evals: Evals | None = None
    # Agent-level guardrails (#1053): optional guardrails the environment's gateway config
    # defines. They add to the environment floor and never remove or weaken it.
    guardrails: list[str] = Field(default_factory=list, max_length=10)
    # Skills (#1059, skills spec §6): optional; an agent file without them is unchanged. Kept
    # in its own block, separate from `guardrails:` (#1053).
    skills: list[RefEntry] = Field(default_factory=list, max_length=10)
    # Sub-agents (#1058): optional; an agent file without them is unchanged.
    subagents: list[SubAgent] = Field(default_factory=list, max_length=MAX_SUBAGENTS)
    limits: Limits | None = None

    @field_validator("guardrails")
    @classmethod
    def _guardrail_names(cls, value: list[str]) -> list[str]:
        if any(
            not isinstance(name, str) or not re.fullmatch(GUARDRAIL_PATTERN, name)
            for name in value
        ):
            raise ValueError("not a gateway guardrail name")
        if len(set(value)) != len(value):
            raise ValueError("duplicate guardrail")
        return value

    @model_validator(mode="after")
    def _refs(self):
        # Messages name the field location only, never the supplied value (it is printed).
        expected = [("runtime", self.runtime, "runtime")]
        if self.prompt.ref:
            expected.append(("prompt.ref", self.prompt.ref, "prompt"))
        expected += [
            (f"knowledge[{i}].ref", k.ref, "knowledge") for i, k in enumerate(self.knowledge)
        ]
        expected += [(f"tools[{i}].ref", t.ref, "tool") for i, t in enumerate(self.tools)]
        expected += [(f"mcp[{i}].ref", m.ref, "mcp") for i, m in enumerate(self.mcp)]
        if self.evals:
            expected.append(("evals.ref", self.evals.ref, "eval"))
        expected += [(f"skills[{i}].ref", s.ref, "skill") for i, s in enumerate(self.skills)]
        owners = [None] * len(expected)  # None: the parent; i: subagents[i]
        for i, sub in enumerate(self.subagents):
            mine = []
            if sub.prompt.ref:
                mine.append((f"subagents[{i}].prompt.ref", sub.prompt.ref, "prompt"))
            mine += [
                (f"subagents[{i}].tools[{j}].ref", t.ref, "tool") for j, t in enumerate(sub.tools)
            ]
            mine += [(f"subagents[{i}].mcp[{j}].ref", m.ref, "mcp") for j, m in enumerate(sub.mcp)]
            expected += mine
            owners += [i] * len(mine)
        seen: dict[str, tuple[str, str, int | None]] = {}
        for (where, text, kind), owner in zip(expected, owners, strict=True):
            try:
                ref = Ref.parse(text)
            except RegistryError as error:
                raise ValueError(
                    f"{where}: not a pinned {kind}/name@X.Y.Z ref ({error.code})"
                ) from None
            if ref.kind != kind:
                raise ValueError(f"{where}: must be a {kind}/name@X.Y.Z ref")
            # One version per kind/name: the build context places each ref at kind/name, and
            # two versions of one tool would also define the same Python modules. The same
            # ref in two agents (the parent and a sub-agent, or two sub-agents) is placed once.
            key = f"{ref.kind}/{ref.name}"
            if key in seen:
                first, version, first_owner = seen[key]
                if version != ref.version or first_owner == owner:
                    raise ValueError(f"{where}: duplicates {first} (one version per name)")
                continue
            seen[key] = (where, ref.version, owner)
        for i, entry in enumerate(self.mcp):
            if len(set(entry.tools)) != len(entry.tools):
                raise ValueError(f"mcp[{i}].tools: duplicate tool")
        if self.prompt.path:
            path = Path(self.prompt.path)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("prompt.path: must be a relative path inside the project")
        names = {f"{Ref.parse(t.ref).kind}/{Ref.parse(t.ref).name}" for t in self.tools}
        if self.memory.notes and NOTES_TOOL not in names:
            raise ValueError(f"memory.notes: needs the {NOTES_TOOL} tool")
        self._check_subagents()
        return self

    def _check_subagents(self) -> None:
        """Sub-agent rules that need the whole file (spec §5.2, §5.3). Messages name the
        location only."""
        if self.limits is not None and not self.subagents:
            raise ValueError("limits: valid only with subagents")
        granted = {m.ref: set(m.tools) for m in self.mcp}
        names: dict[str, int] = {}
        for i, sub in enumerate(self.subagents):
            if sub.name in names:
                raise ValueError(f"subagents[{i}].name: duplicates subagents[{names[sub.name]}]")
            names[sub.name] = i
            for j, tool in enumerate(sub.tools):
                ref = Ref.parse(tool.ref)
                if f"{ref.kind}/{ref.name}" == NOTES_TOOL:
                    raise CodedError(
                        "SUBAGENT_NOTES",
                        f"subagents[{i}].tools[{j}].ref: customer notes are the agent's only, "
                        "never a sub-agent's",
                    )
            for j, entry in enumerate(sub.mcp):
                if len(set(entry.tools)) != len(entry.tools):
                    raise ValueError(f"subagents[{i}].mcp[{j}].tools: duplicate tool")
                # One relay grant per deployment: a sub-agent's MCP tools must be the agent's.
                if entry.ref not in granted or not set(entry.tools) <= granted[entry.ref]:
                    raise CodedError(
                        "SUBAGENT_MCP_UNGRANTED",
                        f"subagents[{i}].mcp[{j}]: every server and tool must also be in the "
                        "agent's own mcp: entry for that server",
                    )
            if sub.knowledge and not self.knowledge:
                raise ValueError(f"subagents[{i}].knowledge: needs a knowledge: entry")
            if sub.prompt.path:
                path = Path(sub.prompt.path)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError(
                        f"subagents[{i}].prompt.path: must be a relative path inside the project"
                    )

    def turn_model_calls(self) -> int | None:
        """The turn-wide model call cap: `limits.turn_model_calls`, else min(40, the parent's
        calls + 6); None without sub-agents."""
        if not self.subagents:
            return None
        if self.limits is not None:
            return self.limits.turn_model_calls
        return min(MAX_TURN_MODEL_CALLS, self.model.max_model_calls + TURN_MODEL_CALLS_MARGIN)

    def aliases(self, extra: tuple[str, ...] | list[str] = ()) -> list[str]:
        """Every gateway alias the agent uses: the parent's, each sub-agent's in file order,
        then `extra` (the knowledge embedding alias, which only build can read), de-duplicated
        in that order. The deployment's one key is minted for these (spec §6)."""
        result: list[str] = []
        for alias in [self.model.alias, *(s.model.alias for s in self.subagents), *extra]:
            if alias not in result:
                result.append(alias)
        return result

    def refs(self) -> list[str]:
        """Every registry ref, in a stable order (runtime, prompt, knowledge, tools, mcp, evals,
        then each sub-agent's prompt, tools and mcp in file order, then skills), each once."""
        result = [self.runtime]
        if self.prompt.ref:
            result.append(self.prompt.ref)
        result += [k.ref for k in self.knowledge] + [t.ref for t in self.tools]
        result += [m.ref for m in self.mcp]
        if self.evals:
            result.append(self.evals.ref)
        for sub in self.subagents:
            if sub.prompt.ref:
                result.append(sub.prompt.ref)
            result += [t.ref for t in sub.tools] + [m.ref for m in sub.mcp]
        result += [s.ref for s in self.skills]
        return list(dict.fromkeys(result))

    def paths(self) -> list[str]:
        result = [self.prompt.path] if self.prompt.path else []
        result += [s.prompt.path for s in self.subagents if s.prompt.path]
        return list(dict.fromkeys(result))


def detect(data: dict) -> str:
    """'agent' | 'manifest' | 'legacy' | 'unknown' for a parsed YAML mapping."""
    if data.get("kind") == "agent":
        return "agent"
    if "kind" not in data and "image" in data and "schema_version" in data:
        return "manifest"
    if "kind" not in data and (
        "spec_version" in data or "framework" in data or ("model" in data and "image" not in data)
    ):
        return "legacy"
    return "unknown"


def read(path: Path) -> tuple[str, dict]:
    try:
        data = load_file(path, limit=MAX_BYTES)
    except StrictYamlError as error:
        raise AgentFileError("AGENT_INVALID", str(error)) from error
    return detect(data), data


def load_agent_file(path: Path) -> AgentFile:
    kind, data = read(path)
    if kind == "manifest":
        raise AgentFileError(
            "WRONG_FILE_KIND",
            f"{path} is a deploy manifest, not an agent file; deploy it with "
            "`agentbreeder deploy`",
        )
    if kind == "legacy":
        raise AgentFileError(
            "WRONG_FILE_KIND",
            f"{path} looks like a 2.x agent.yaml, which is unsupported; see the legacy-2x docs "
            "and add `kind: agent` with the current schema (docs/agent-yaml.md)",
        )
    if kind != "agent":
        raise AgentFileError("WRONG_FILE_KIND", f"{path} needs `kind: agent`")
    try:
        return AgentFile.model_validate(data)
    except ValidationError as error:
        # Name the fields; never echo supplied values (they may hold pasted secrets).
        problems, codes = [], []
        for item in error.errors():
            where = ".".join(str(part) for part in item["loc"]) or "document"
            message = item["msg"].removeprefix("Value error, ")
            problems.append(f"{where}: {message}" if item["type"] == "value_error" else where)
            cause = (item.get("ctx") or {}).get("error")
            if isinstance(cause, CodedError):
                codes.append(cause.code)
        raise AgentFileError(
            sorted(codes)[0] if codes else "AGENT_INVALID",
            "Invalid " + "; ".join(sorted(set(problems))) + " (see docs/agent-yaml.md)",
        ) from error
