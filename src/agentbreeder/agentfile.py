"""`agent.yaml` (authoring schema v1): what an agent is made of, built by `agentbreeder build`.

This is NOT the deploy manifest (docs/manifest.md), which stays image-in. An agent file is
environment-neutral: gateways, services, secret bindings and identities come from the operator
config for the chosen environment, and nothing here can grant a permission or raise a ceiling.
See docs/superpowers/specs/2026-11-07-agent-yaml-build-design.md §5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentbreeder.registry import Ref, RegistryError
from agentbreeder.strict_yaml import StrictYamlError, load_file

MAX_BYTES = 64 * 1024
NAME_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$"  # ≤ 53: room for "-candidate"
NOTES_TOOL = "tool/customer-notes"


class AgentFileError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
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
    alias: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
    reasoning: Literal["none", "low", "medium", "high"] = "low"
    max_model_calls: int = Field(default=4, ge=1, le=20)
    budget_usd: float | None = Field(default=None, gt=0, le=100000)
    rpm: int | None = Field(default=None, ge=1, le=100000)


class Knowledge(_Strict):
    ref: str
    top_k: int = Field(default=5, ge=1, le=20)
    extra_searches: int = Field(default=1, ge=0, le=5)


class Memory(_Strict):
    sessions: Literal["postgres", "in_memory"] = "postgres"
    notes: bool = False


class Evals(_Strict):
    ref: str
    threshold: float = Field(default=1.0, ge=0, le=1)
    retries: int = Field(default=1, ge=0, le=3)


class AgentFile(_Strict):
    kind: Literal["agent"]
    schema_version: Literal[1]
    name: str = Field(pattern=NAME_PATTERN)
    runtime: str
    description: str = Field(default="", max_length=500)
    model: Model
    prompt: PromptSource
    knowledge: list[Knowledge] = Field(default_factory=list, max_length=10)
    tools: list[RefEntry] = Field(default_factory=list, max_length=50)
    memory: Memory = Field(default_factory=Memory)
    agent_events: Literal["postgres", "none"] = "postgres"
    traces: Literal["otlp", "none"] = "otlp"
    evals: Evals | None = None

    @model_validator(mode="after")
    def _refs(self):
        expected = [(self.runtime, "runtime")]
        if self.prompt.ref:
            expected.append((self.prompt.ref, "prompt"))
        expected += [(k.ref, "knowledge") for k in self.knowledge]
        expected += [(t.ref, "tool") for t in self.tools]
        if self.evals:
            expected.append((self.evals.ref, "eval"))
        seen = set()
        for text, kind in expected:
            try:
                ref = Ref.parse(text)
            except RegistryError as error:
                raise ValueError(str(error)) from error
            if ref.kind != kind:
                raise ValueError(f"{text} must be a {kind}/… ref")
            if text in seen:
                raise ValueError(f"{text} is listed twice")
            seen.add(text)
        if self.prompt.path:
            path = Path(self.prompt.path)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("prompt.path must be a relative path inside the project")
        if self.memory.notes and not any(
            Ref.parse(t.ref).kind + "/" + Ref.parse(t.ref).name == NOTES_TOOL for t in self.tools
        ):
            raise ValueError(f"memory.notes needs the {NOTES_TOOL} tool")
        return self

    def refs(self) -> list[str]:
        """Every registry ref, in a stable order (runtime, prompt, knowledge, tools, evals)."""
        result = [self.runtime]
        if self.prompt.ref:
            result.append(self.prompt.ref)
        result += [k.ref for k in self.knowledge] + [t.ref for t in self.tools]
        if self.evals:
            result.append(self.evals.ref)
        return result

    def paths(self) -> list[str]:
        return [self.prompt.path] if self.prompt.path else []


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
        problems = []
        for item in error.errors():
            where = ".".join(str(part) for part in item["loc"]) or "document"
            message = item["msg"].removeprefix("Value error, ")
            problems.append(f"{where}: {message}" if item["type"] == "value_error" else where)
        raise AgentFileError(
            "AGENT_INVALID",
            "Invalid " + "; ".join(sorted(set(problems))) + " (see docs/agent-yaml.md)",
        ) from error
