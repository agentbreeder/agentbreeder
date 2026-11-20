"""`agentbreeder new agent NAME --from-spec FILE`: write an agent project from a JSON spec (M17
#833, the CLI half of the console's agent builder; console spec §4.5, guided §14.7).

The console's builder form never renders YAML: it writes the form as JSON to a private temp file
and runs `new agent NAME --from-spec FILE --dry-run --json` for the preview (the full text of
every file is in `files[].content`), then the same without `--dry-run` to write it.

The spec (`SPEC_VERSION` 1, docs/commands/new.md) names the agent, the model provider and an
optional model id (#905), the prompt text, the eval cases, picked `tool/` versions and `mcp/`
servers with the tools to grant, and at most one pinned `knowledge/` version (#989).

Knowledge: the runtime embeds with the alias the knowledge VERSION names (`embedding.alias`,
`embedding.dimensions` in its manifest), so the generated stack serves that alias too, with the
provider's embedding model (`EMBEDDINGS`; a provider without one is `SPEC_UNSUPPORTED`) and the
version's dimensions, and lists it in `environment.models`. Postgres is
`service/postgres-pgvector@1.2.0` for such a spec (the version Northwind's knowledge ran on;
other specs keep the starter's 1.1.0). Its `init.sh` creates the `vector` extension in every
database, and the `DATABASE_URL` binding (the `app` database's owner) creates the runtime's
`kb_<name>_<version>` table in `public`, where `knowledge status` reads it.

The project is the starter's (`newagent`), so the same checks, staging and rollback apply: the
prompt text and the cases become `src/prompt/` and `src/evals/`, published as
`prompt/NAME-prompt@0.1.0` and `eval/NAME-evals@0.1.0` into the project's `registry/`; the stack
is `stackfor.render`'s, with an `mcp:` block for the picked servers, whose tools are then granted
to the agent with `mcpgrant` (the `mcp grant` edit, which refuses undeclared tools); `agent.lock`
is written last. Picked versions are not copied: they must resolve over the roots the new project
resolves (the built-ins and `AGENTBREEDER_REGISTRY_PATH`), and the lock records where.

User text reaches the files only as: the validated name; the prompt text, as the body of
`prompt.md`; the cases, as JSON lines; refs and tool names that matched their patterns, JSON-
quoted. The rendered `agent.yaml` must load back to exactly what the spec asked for
(`TEMPLATE_INVALID` otherwise).

The spec file must be a regular file (not a link) you own that no other user can read or write,
at most 256 KiB of UTF-8 JSON without duplicate keys or non-finite numbers. Its content is never
echoed in errors.

`verified` in the summary is true only for a combination that has been run end to end
(`VERIFIED`, with at most `VERIFIED_TOOLS` tools); the CLI writes unverified ones too, and the
console enables only verified ones (its form offers one knowledge version for a provider with an
embedding model, #1069).
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, replace
from datetime import date, timedelta
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

from agentbreeder import evals, newagent, registry, runtime_profile, stackfor
from agentbreeder.agentfile import (
    MAX_SUBAGENTS,
    MAX_TURN_MODEL_CALLS,
    SUBAGENT_NAME,
)
from agentbreeder.newagent import (
    EVALS_SOURCE,
    PROMPT_SOURCE,
    PROVIDERS,
    REVIEW_DAYS,
    RUNTIME,
    VERSION,
    NewAgentError,
)

# Spec versions this CLI reads. 2 (#1058 SP, shared with #1085's `runtime`) adds `runtime`,
# `subagents`, `skills`, `limits`, case `tags` and the sub-agent and skill checks; a version 1
# spec with any of them is SPEC_INVALID, and a version 1 spec writes what it always wrote.
SPEC_VERSIONS = (1, 2)
SPEC_VERSION = 2  # the newest
MAX_SPEC_BYTES_V1 = 256 * 1024
MAX_SPEC_BYTES_V2 = 512 * 1024  # six prompts of 32 KiB is 192 KiB before the cases
V2_FIELDS = ("runtime", "subagents", "skills", "limits")
V2_CHECKS = (
    "subagents_include",
    "subagents_exclude",
    "subagent_tools_include",
    "skills_loaded",
    "skills_not_loaded",
)
MAX_SUBAGENT_TOOLS = 20  # agentfile.SubAgent.tools
MAX_SKILLS = 10  # agentfile.AgentFile.skills
MAX_DESCRIPTION = 500  # agentfile.SubAgent.description, in characters
MAX_PROMPT = 32 * 1024
MAX_TEXT = 4000  # a turn or a reference
MAX_CASES = 100  # the builder's limit; the eval loader allows more
CASE_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
MCP_TOOL = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"  # services.schema.MCP_TOOL
# Combinations run end to end with this command (services up, release, invoke), keyed by the
# runtime's name (#1083 F1, console spec §4.3), as the sorted names of the parts the spec uses
# beyond the model, prompt and evals every spec has. A provider must also be verified
# (`PROVIDERS`). A runtime name is added only by a verification record; every run so far used
# the default version (`newagent.RUNTIME`), so another version of a listed runtime is unverified.
# One knowledge version was run end to end with gemini alone and with one path tool, and one path
# tool alone (#833), and one knowledge version with openai alone (#1020,
# `openai/text-embedding-3-small`); MCP was not, so any combination with MCP stays unverified.
# Every run had one tool, so a spec with more tools is unverified too (`VERIFIED_TOOLS`).
VERIFIED: dict[str, frozenset[tuple[str, ...]]] = {
    "adk": frozenset(
        {
            (),
            ("knowledge",),
            ("tools",),
            ("knowledge", "tools"),
            # #1058 and #1059 AC: docs/superpowers/verification/1058-subagents.md and
            # 1059-skills-a.md, on `PART_RUNTIMES`' version only.
            ("subagents",),
            ("subagents", "tools"),
            ("skills",),
        }
    ),
}
VERIFIED_TOOLS = 1  # the most tools a verified spec has, its sub-agents' included
# Parts the starter's runtime (`newagent.RUNTIME`) does not run, with the one version each was
# run end to end on. A spec with such a part is verified only when it pins that version; every
# other combination still needs the starter's.
PART_RUNTIMES = {"subagents": "runtime/adk@1.5.0", "skills": "runtime/adk@1.5.0"}
# The providers each part ran with (#1058, #1059 AC: gemini only), as `TOOL_PROVIDERS`; every
# sub-agent's provider counts too. The runs had two sub-agents and two skills, so a spec with
# more is unverified.
SUBAGENT_PROVIDERS = frozenset({"gemini"})
SKILL_PROVIDERS = frozenset({"gemini"})
VERIFIED_SUBAGENTS = 2
VERIFIED_SKILLS = 2
# Runtime versions that were run end to end, each with its run (owner decision 2026-11-17).
# This is what `registry runtimes` labels (`runtime_verified`). It says nothing about a
# `new agent` combination: those all ran on `newagent.RUNTIME`, so `verified` does not read it.
RUNTIMES_RUN = frozenset(
    {
        "runtime/adk@1.3.0",  # Northwind 61/61 (#1095)
        "runtime/adk@1.4.0",  # the starter and the 3.0.0a3 public-registry runs (#836)
    }
)
# The providers a combination with a tool was run with (#833); openrouter ran model + prompt +
# evals alone and openai that plus one knowledge version (#1020), so a tool on any other provider
# stays unverified.
TOOL_PROVIDERS = frozenset({"gemini"})
STACK_SERVICES = frozenset({"postgres", "litellm"})  # services the generated stack has
# A provider's embedding model in the pinned LiteLLM 1.106.2 model map and its largest output
# (`output_vector_size`). anthropic and openrouter have none there.
EMBEDDINGS = {
    "gemini": ("gemini/gemini-embedding-2", 3072),
    "openai": ("openai/text-embedding-3-small", 1536),
}
KNOWLEDGE_POSTGRES = "service/postgres-pgvector@1.2.0"

AGENT_HEAD = """\
# The {name} agent, written by `agentbreeder new agent --from-spec` on {today}.
#
# Published versions are immutable. To change the prompt (or, the same way, the evals in
# src/evals/), edit src/prompt/, bump `version:` in its manifest.yaml, run
# `agentbreeder registry publish src/prompt` and change the ref below; then build and release.
# `agentbreeder next` shows where the project is and what to run.
kind: agent
schema_version: 1
name: {name}
runtime: {runtime}
model: {{alias: chat, reasoning: {reasoning}, max_model_calls: {calls}}}
prompt: {{ref: prompt/{name}-prompt@{version}}}
evals: {{ref: eval/{name}-evals@{version}, threshold: {threshold}, retries: {retries}}}
"""
AGENT_TAIL = """\
traces: none        # this stack has no trace collector
agent_events: none  # and no event log for the agent
"""

EVALS_MANIFEST = """\
kind: eval
name: {name}-evals
version: {version}
description: Cases for the {name} agent, from the agent builder.
judge: {{alias: judge}}
cases: golden.jsonl
"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _text(value: str, limit: int, where: str) -> str:
    """Text without control, bidi or invisible format characters (tab, newline and the
    zero-width joiners are allowed)."""
    if registry.clean(value)[1]:
        raise ValueError(f"{where} has control, bidi or invisible characters")
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"{where} is over {limit} bytes")
    if not value.strip():
        raise ValueError(f"{where} is empty")
    return value


_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")


def _nested(items, inside: bool) -> bool:
    """True when a repeat (other than `?`) contains another repeat or an alternation, or the
    pattern has a backreference: the shapes that backtrack exponentially in `re.search`."""
    for op, value in items:
        name = str(op)
        if name in ("GROUPREF", "GROUPREF_EXISTS"):
            return True
        if name in _REPEATS:
            low, high, body = value
            repeats = high != 1
            if repeats and inside:
                return True
            if _nested(body, inside or repeats):
                return True
        elif name == "BRANCH":
            if inside:
                return True
            if any(_nested(branch, inside) for branch in value[1]):
                return True
        elif name in ("SUBPATTERN", "ATOMIC_GROUP"):
            body = value[-1] if name == "SUBPATTERN" else value
            if _nested(body, inside):
                return True
        elif name in ("ASSERT", "ASSERT_NOT"):
            if _nested(value[1], inside):
                return True
    return False


MAX_REPEATS = 2  # variable repeats (`?`, `*`, `+`, `{m,n}`) and alternations in one pattern


def _repeats(items) -> int:
    """How many variable repeats the parsed pattern has, at any depth."""
    count = 0
    for op, value in items:
        name = str(op)
        if name in _REPEATS:
            low, high, body = value
            count += low != high
            # A body repeated a fixed number of times counts that many times: `(a?){18}` is
            # eighteen optionals. (An unbounded repeat around a repeat is refused by `_nested`.)
            count += _repeats(body) * max(1, min(high, 1000))
        elif name == "BRANCH":
            # An alternation backtracks like a repeat: `(?:a|aa)` 25 times is exponential.
            count += 1 + sum(_repeats(branch) for branch in value[1])
        elif name == "SUBPATTERN":
            count += _repeats(value[-1])
        elif name == "ATOMIC_GROUP":
            count += _repeats(value)
        elif name in ("ASSERT", "ASSERT_NOT"):
            count += _repeats(value[1])
    return count


def _pattern(pattern: str, where: str) -> None:
    """A `must_match` or `must_not_match` pattern runs later against model replies in
    `release`: refuse one that is not a regular expression, whose shape can backtrack
    exponentially (nested repeats, a repeated alternation, a backreference), or that has more
    than `MAX_REPEATS` variable repeats and alternations (`a?a?a?…aaa` and `(?:a|aa)…` are
    exponential, `a*a*a*b` a high-degree polynomial). Two adjacent repeats can still cost
    O(n^3), seconds on a 1,000-character reply: not refused here, but `evals` stops a case's
    patterns at `evals.PATTERN_TIMEOUT` and fails the case (S-34 #10)."""
    import re._parser as parser  # private, present on 3.11-3.13; the compile below is the API

    try:
        re.compile(pattern)
        parsed = parser.parse(pattern)
    except (re.error, RecursionError, OverflowError):
        raise ValueError(f"{where}: not a regular expression") from None
    if _nested(list(parsed), False):
        raise ValueError(
            f"{where}: nested repeats, repeated alternatives and backreferences are not allowed"
        )
    if _repeats(list(parsed)) > MAX_REPEATS:
        raise ValueError(
            f"{where}: at most {MAX_REPEATS} repeats (?, *, +, {{m,n}}) and alternatives (|) "
            "in one pattern"
        )


TOOL_NAME = r"[A-Za-z_][A-Za-z0-9_.-]{0,127}"


def _distinct(values: list[str], pattern, where: str) -> None:
    if any(not pattern.fullmatch(value) for value in values):
        raise ValueError(f"{where}: not a name")
    if len(set(values)) != len(values):
        raise ValueError(f"{where}: duplicate name")


class Expect(_Strict):
    must_match: list[str] = Field(default_factory=list, max_length=20)
    must_not_match: list[str] = Field(default_factory=list, max_length=20)
    tools_include: list[str] = Field(default_factory=list, max_length=20)
    tools_exclude: list[str] = Field(default_factory=list, max_length=20)
    # Spec v2 (evals.py's rules for sub-agents and skills, #1058 EV).
    subagents_include: list[str] = Field(default_factory=list, max_length=MAX_SUBAGENTS)
    subagents_exclude: list[str] = Field(default_factory=list, max_length=MAX_SUBAGENTS)
    subagent_tools_include: dict[str, list[str]] = Field(
        default_factory=dict, max_length=MAX_SUBAGENTS
    )
    skills_loaded: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)
    skills_not_loaded: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)

    @model_validator(mode="after")
    def _checked(self):
        for field_name in ("must_match", "must_not_match"):
            for pattern in getattr(self, field_name):
                _text(pattern, 200, f"expect.{field_name}")
                _pattern(pattern, f"expect.{field_name}")
        for field_name in ("tools_include", "tools_exclude"):
            for tool in getattr(self, field_name):
                if not re.fullmatch(TOOL_NAME, tool):
                    raise ValueError(f"expect.{field_name}: not a tool name")
        for field_name in ("subagents_include", "subagents_exclude"):
            _distinct(getattr(self, field_name), evals.SUBAGENT, f"expect.{field_name}")
        for field_name in ("skills_loaded", "skills_not_loaded"):
            _distinct(getattr(self, field_name), evals.SKILL, f"expect.{field_name}")
        for name, tools in self.subagent_tools_include.items():
            if not evals.SUBAGENT.fullmatch(name):
                raise ValueError("expect.subagent_tools_include: not a sub-agent name")
            if not 0 < len(tools) <= 20 or not all(re.fullmatch(TOOL_NAME, t) for t in tools):
                raise ValueError("expect.subagent_tools_include: 1 to 20 tool names each")
        return self

    def subagents(self) -> set[str]:
        """The sub-agent names the checks name."""
        return {
            *self.subagents_include,
            *self.subagents_exclude,
            *self.subagent_tools_include,
        }

    def skills(self) -> set[str]:
        return {*self.skills_loaded, *self.skills_not_loaded}


class Case(_Strict):
    id: str = Field(pattern=CASE_ID)
    turns: list[str] = Field(min_length=1, max_length=10)
    expect: Expect = Field(default_factory=Expect)
    reference: str
    tags: list[str] = Field(default_factory=list, max_length=evals.MAX_TAGS)  # spec v2

    @field_validator("tags")
    @classmethod
    def _tags(cls, tags: list[str]) -> list[str]:
        _distinct(tags, evals.TAG, "tags")
        return tags

    @field_validator("turns")
    @classmethod
    def _turns(cls, turns: list[str]) -> list[str]:
        return [_text(turn, MAX_TEXT, "a turn") for turn in turns]

    @field_validator("reference")
    @classmethod
    def _reference(cls, reference: str) -> str:
        return _text(reference, MAX_TEXT, "reference")


class Evals(_Strict):
    cases: list[Case] = Field(min_length=1, max_length=MAX_CASES)
    threshold: float = Field(default=1.0, ge=0, le=1)
    retries: int = Field(default=1, ge=0, le=3)

    @field_validator("threshold", mode="before")
    @classmethod
    def _number(cls, value):
        # JSON `1` is an int; strict mode would refuse it as a float.
        return float(value) if isinstance(value, int) and not isinstance(value, bool) else value

    @model_validator(mode="after")
    def _unique(self):
        ids = [case.id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("evals.cases: duplicate id")
        return self


class Prompt(_Strict):
    text: str

    @field_validator("text")
    @classmethod
    def _checked(cls, text: str) -> str:
        return _text(text, MAX_PROMPT, "prompt.text")


class Mcp(_Strict):
    ref: str
    tools: list[str] = Field(min_length=1, max_length=100)

    @field_validator("tools")
    @classmethod
    def _tools(cls, tools: list[str]) -> list[str]:
        if any(not re.fullmatch(MCP_TOOL, tool) for tool in tools):
            raise ValueError("not a tool name")
        if len(set(tools)) != len(tools):
            raise ValueError("duplicate tool")
        return tools


class Knowledge(_Strict):
    """`agent.yaml`'s `knowledge` entry (agentfile.Knowledge), from a pinned registry ref."""

    ref: str = Field(max_length=200)
    top_k: int = Field(default=5, ge=1, le=20)
    extra_searches: int = Field(default=1, ge=0, le=5)


class SubAgent(_Strict):
    """A sub-agent (spec v2; `agentfile.SubAgent`): its own prompt, model and tools, called by
    the agent as `ask_<name>`. The prompt becomes `prompt/NAME-SUB-prompt@0.1.0`."""

    name: str = Field(pattern=SUBAGENT_NAME)
    description: str
    model: str
    model_id: str | None = None
    max_model_calls: int | None = Field(default=None, ge=1, le=20)  # None: the agent's
    max_calls_per_turn: int = Field(default=2, ge=1, le=3)
    prompt: Prompt
    tools: list[str] = Field(default_factory=list, max_length=MAX_SUBAGENT_TOOLS)
    mcp: list[Mcp] = Field(default_factory=list, max_length=10)

    @field_validator("description")
    @classmethod
    def _description(cls, text: str) -> str:
        if len(text) > MAX_DESCRIPTION:
            raise ValueError(f"description is over {MAX_DESCRIPTION} characters")
        return _text(text, 4 * MAX_DESCRIPTION, "description")


class Limits(_Strict):
    turn_model_calls: int = Field(ge=1, le=MAX_TURN_MODEL_CALLS)


class Spec(_Strict):
    schema_version: Literal[1, 2]
    name: str
    model: str
    model_id: str | None = None
    max_model_calls: int = Field(default=2, ge=1, le=20)
    prompt: Prompt
    evals: Evals
    tools: list[str] = Field(default_factory=list, max_length=50)
    # The builder writes a stack that serves one embedding alias, so a spec picks at most one
    # knowledge set whatever the runtime; the runtime's own `limits.knowledge` and `knowledge`
    # capability are checked as build checks them (`_runtime`).
    knowledge: list[Knowledge] = Field(default_factory=list, max_length=1)
    mcp: list[Mcp] = Field(default_factory=list, max_length=10)
    # Spec v2 only (`_v1_only` refuses them under 1).
    runtime: str | None = Field(default=None, max_length=200)
    subagents: list[SubAgent] = Field(default_factory=list, max_length=MAX_SUBAGENTS)
    skills: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)
    limits: Limits | None = None

    @model_validator(mode="after")
    def _composition(self):
        names = [sub.name for sub in self.subagents]
        if len(set(names)) != len(names):
            raise ValueError("subagents: duplicate name")
        if self.limits is not None and not self.subagents:
            raise ValueError("limits: valid only with subagents")
        for index, case in enumerate(self.evals.cases):
            if case.expect.subagents() - set(names):
                raise ValueError(
                    f"evals.cases[{index}].expect: names a sub-agent the spec does not have"
                )
        return self


# -- reading the spec ----------------------------------------------------------------------------


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise NewAgentError("SPEC_INVALID", "the spec has a duplicate key")
        result[key] = value
    return result


def _constant(name: str):
    raise NewAgentError("SPEC_INVALID", "the spec has a non-finite number")


def read(path: Path) -> dict:
    """The spec file's JSON object. Refuses a link, a non-regular file, another user's file, a
    file others can read or write, and anything over `MAX_SPEC_BYTES_V2`, or over
    `MAX_SPEC_BYTES_V1` unless it says `schema_version` 2."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise NewAgentError("SPEC_MISSING", "the spec file does not exist") from None
    except OSError:
        raise NewAgentError(
            "SPEC_UNSAFE", "the spec file must be a regular file you can read, not a link"
        ) from None
    with os.fdopen(descriptor, "rb") as reader:
        info = os.fstat(reader.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise NewAgentError("SPEC_UNSAFE", "the spec file must be a regular file")
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise NewAgentError(
                "SPEC_UNSAFE",
                "the spec file must be yours and private (chmod 600): it can hold a prompt",
            )
        raw = reader.read(MAX_SPEC_BYTES_V2 + 1)
    if len(raw) > MAX_SPEC_BYTES_V2:
        raise NewAgentError("SPEC_TOO_LARGE", f"the spec is over {MAX_SPEC_BYTES_V2 // 1024} KiB")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        if isinstance(error, NewAgentError):
            raise
        raise NewAgentError("SPEC_INVALID", "the spec is not UTF-8 JSON") from None
    if not isinstance(data, dict):
        raise NewAgentError("SPEC_INVALID", "the spec must be a JSON object")
    if data.get("schema_version") != 2 and len(raw) > MAX_SPEC_BYTES_V1:
        raise NewAgentError(
            "SPEC_TOO_LARGE",
            f"the spec is over {MAX_SPEC_BYTES_V1 // 1024} KiB (a schema_version 2 spec may be "
            f"{MAX_SPEC_BYTES_V2 // 1024} KiB)",
        )
    return data


def _problems(error: ValidationError) -> str:
    """Where the spec is wrong, never the value (the spec can hold a prompt)."""
    items = []
    for problem in error.errors()[:5]:
        where = ".".join(str(part) for part in problem["loc"]) or "spec"
        if len(where) > 80:  # an unknown key is the sender's text
            where = where[:77] + "..."
        message = problem["msg"].removeprefix("Value error, ")
        items.append(f"{where}: {message}")
    return "; ".join(registry.clean(item, name=True)[0] for item in items)


def _v1_only(data: dict) -> None:
    """A version 1 spec carries no version 2 field: refused by location, never by value."""
    for key in V2_FIELDS:
        if key in data:
            raise NewAgentError("SPEC_INVALID", f"{key}: needs schema_version 2")
    cases = data.get("evals", {}).get("cases") if isinstance(data.get("evals"), dict) else None
    for index, case in enumerate(cases if isinstance(cases, list) else []):
        if not isinstance(case, dict):
            continue
        if "tags" in case:
            raise NewAgentError(
                "SPEC_INVALID", f"evals.cases[{index}].tags: needs schema_version 2"
            )
        expect = case.get("expect")
        for key in V2_CHECKS:
            if isinstance(expect, dict) and key in expect:
                raise NewAgentError(
                    "SPEC_INVALID",
                    f"evals.cases[{index}].expect.{key}: needs schema_version 2",
                )


def parse(data: dict) -> Spec:
    version = data.get("schema_version")
    if version not in SPEC_VERSIONS or isinstance(version, bool):
        if version is not None:
            raise NewAgentError(
                "SPEC_VERSION_UNSUPPORTED",
                "this AgentBreeder reads spec schema_version "
                + " and ".join(map(str, SPEC_VERSIONS)),
            )
    elif version == 1:
        _v1_only(data)
    try:
        return Spec.model_validate(data)
    except ValidationError as error:
        raise NewAgentError("SPEC_INVALID", _problems(error)) from None


# -- refs ----------------------------------------------------------------------------------------


def _resolve(text: str, kind: str, where: str, directory: Path) -> registry.Resolved:
    try:
        ref = registry.Ref.parse(text)
    except registry.RegistryError:
        raise NewAgentError(
            "SPEC_INVALID", f"{where}: not a pinned {kind}/name@X.Y.Z ref"
        ) from None
    if ref.kind != kind:
        raise NewAgentError("SPEC_INVALID", f"{where}: must be a {kind}/name@X.Y.Z ref")
    try:
        return registry.resolve(ref, registry.roots(directory))
    except registry.RegistryError as error:
        raise NewAgentError(error.code, newagent._bare(error)) from None


def _ref_entry(hit: registry.Resolved, directory: Path) -> dict:
    return {
        "ref": str(hit.ref),
        "kind": hit.ref.kind,
        "root_kind": registry.root_kind(hit.root, directory),
        "runs_code": registry.runs_code(hit.ref.kind, hit.path),
        "sha256": _version_digest(hit),
    }


def _version_digest(hit: registry.Resolved) -> str:
    try:
        return registry.copy_and_hash(hit.path)
    except registry.RegistryError as error:
        raise NewAgentError(error.code, newagent._bare(error)) from None


def _servers(spec: Spec, directory: Path) -> tuple[dict[str, str], list[dict], list[str]]:
    """Server name -> ref, the ref entries and the keys the servers import."""
    from agentbreeder import mcp as mcp_kind
    from agentbreeder.services.schema import RESERVED, SERVICE_NAME, ServiceError

    servers: dict[str, str] = {}
    entries, keys = [], []
    for index, entry in enumerate(spec.mcp):
        hit = _resolve(entry.ref, "mcp", f"mcp[{index}].ref", directory)
        name = hit.ref.name
        if not re.fullmatch(SERVICE_NAME, name) or name in RESERVED or name in STACK_SERVICES:
            raise NewAgentError(
                "SPEC_UNSUPPORTED", f"mcp[{index}]: {name} cannot name a stack's MCP server"
            )
        if name in servers:
            raise NewAgentError("SPEC_INVALID", f"mcp[{index}]: {name} is picked twice")
        try:
            declared = mcp_kind.load_mcp(hit.path)
        except ServiceError as error:
            raise NewAgentError(error.code, newagent._bare(error)) from None
        undeclared = [tool for tool in entry.tools if tool not in declared.tools]
        if undeclared:
            raise NewAgentError(
                "MCP_TOOL_UNDECLARED",
                f"{hit.ref} doesn't declare {len(undeclared)} tool(s): {', '.join(undeclared)}",
            )
        servers[name] = str(hit.ref)
        entries.append(_ref_entry(hit, directory))
        for source in declared.secrets.values():
            if source.import_ and source.import_ not in keys:
                keys.append(source.import_)
    return servers, entries, keys


def _knowledge(spec: Spec, directory: Path) -> tuple[list[dict], list[dict], tuple | None]:
    """The ref entries, the summary's `knowledge` rows and the (alias, model, dimensions) the
    stack serves for the picked knowledge version (None without one)."""
    from agentbreeder import knowledge as kb

    if not spec.knowledge:
        return [], [], None
    if spec.model not in EMBEDDINGS:
        raise NewAgentError(
            "SPEC_UNSUPPORTED",
            f"knowledge needs an embedding model, and {spec.model} has none in the pinned "
            f"LiteLLM model map; use {' or '.join(sorted(EMBEDDINGS))}",
        )
    model, largest = EMBEDDINGS[spec.model]
    entry = spec.knowledge[0]
    hit = _resolve(entry.ref, "knowledge", "knowledge[0].ref", directory)
    # The registry's safe copy-and-hash first: it refuses links, hard links and non-regular
    # files (a FIFO manifest would block a plain open) before anything is read here.
    ref_entry = _ref_entry(hit, directory)
    try:
        manifest = registry.read_manifest(hit.path, hit.ref)
        want = kb.expected(hit)
    except (kb.KnowledgeError, registry.RegistryError) as error:
        raise NewAgentError(error.code, newagent._bare(error)) from None
    # What was read must be the version that was hashed (and is pinned in agent.lock).
    if _version_digest(hit) != ref_entry["sha256"]:
        raise NewAgentError("DIGEST_MISMATCH", f"{hit.ref} changed while it was read; try again")
    # The runtime always chunks by paragraph into pgvector; refuse a version that says otherwise.
    for key, value in (("store", "pgvector"), ("chunking", "paragraph")):
        if manifest.get(key) not in (None, value):
            raise NewAgentError("KNOWLEDGE_INVALID", f"{hit.ref}: {key} must be {value}")
    if not stackfor.ALIAS.fullmatch(want.alias) or want.alias in ("chat", "judge"):
        raise NewAgentError(
            "ALIAS_UNSUPPORTED",
            f"{hit.ref}: embedding.alias must be letters, digits, '.', '_' and '-' (at most 64), "
            "and not chat or judge",
        )
    if want.dimensions > largest:
        raise NewAgentError(
            "SPEC_UNSUPPORTED",
            f"{hit.ref}: embedding.dimensions is {want.dimensions}; {model} makes at most "
            f"{largest}",
        )
    row = {
        "ref": str(hit.ref),
        "alias": want.alias,
        "dimensions": want.dimensions,
        "embedding_model": model,
        "top_k": entry.top_k,
        "extra_searches": entry.extra_searches,
        "documents": want.documents,
        "chunks": len(want.chunks),
    }
    return [ref_entry], [row], (want.alias, model, want.dimensions)


# -- spec v2: runtime, sub-agents and skills -----------------------------------------------------


@dataclass(frozen=True)
class Sub:
    """A sub-agent as written: its gateway alias, LiteLLM model and prompt version."""

    spec: SubAgent
    alias: str
    model_id: str
    prompt_ref: str
    source: str  # the prompt's source directory in the project


@dataclass(frozen=True)
class Composition:
    runtime: str  # the ref agent.yaml pins
    entries: list[dict]  # the pinned runtime's ref entry (none for the default)
    subs: list[Sub]
    served: tuple[tuple[str, str, str], ...]  # (alias, provider, model) of sub-agents' aliases
    keys: list[str]  # provider keys the sub-agents add
    tools: dict[str, registry.Resolved]  # every picked tool ref (agent's, then sub-agents')
    skills: list[dict]  # the skills' ref entries
    profile: runtime_profile.Profile  # the runtime version's resolved view


def _runtime(spec: Spec, directory: Path) -> tuple[str, list[dict], runtime_profile.Profile]:
    """The runtime ref, its ref entry when the spec pins one, and its profile
    (`runtime_profile`: capabilities, limits and reserved names, read from the version)."""
    if spec.runtime is None:
        try:
            hit = registry.resolve(registry.Ref.parse(RUNTIME), [registry.BUILTIN_ROOT])
        except registry.RegistryError as error:  # a packaging bug
            raise NewAgentError(error.code, newagent._bare(error)) from None
        entries = []
    else:
        hit = _resolve(spec.runtime, "runtime", "runtime", directory)
        entries = [_ref_entry(hit, directory)]  # hashed before its manifest is read
    try:
        profile = runtime_profile.load(hit.path)
    except runtime_profile.ProfileError as error:
        if error.code == "PROTOCOL_UNSUPPORTED":
            raise NewAgentError(error.code, f"{hit.ref}: {error}") from None
        raise NewAgentError("ARTIFACT_INVALID", f"{hit.ref} manifest.yaml: {error}") from None
    try:
        runtime_profile.load_modules(hit.path, profile)  # build would refuse a missing file
    except runtime_profile.ProfileError as error:
        raise NewAgentError(error.code, f"{hit.ref}: {error}") from None
    if entries and _version_digest(hit) != entries[0]["sha256"]:
        raise NewAgentError("DIGEST_MISMATCH", f"{hit.ref} changed while it was read; try again")
    # build's refusals (`build.check_runtime`), so a spec never writes a project that cannot
    # build
    for code, used, needed, what in (
        ("RUNTIME_NO_SUBAGENTS", spec.subagents, "subagents", "does not run sub-agents"),
        ("RUNTIME_NO_SKILLS", spec.skills, "skills", "does not load skills"),
        ("MCP_RUNTIME_UNSUPPORTED", spec.mcp, "mcp", "has no MCP client"),
        ("RUNTIME_NO_KNOWLEDGE", spec.knowledge, "knowledge", "does not search knowledge"),
    ):
        if used and not profile.has(needed):
            raise NewAgentError(code, f"{hit.ref} {what}; pick a runtime version that does")
    for part, field, used in (
        ("knowledge", "knowledge", len(spec.knowledge)),
        ("tools", "tools", len(spec.tools)),
        ("mcp_servers", "mcp", len(spec.mcp)),
    ):
        allowed = profile.limit(part)
        if used > allowed:  # worded as build's refusal
            raise NewAgentError(
                "RUNTIME_LIMIT",
                f"{hit.ref} takes at most {allowed} {field}: "
                f"{'entry' if allowed == 1 else 'entries'} (limits.{part}); the spec has {used}",
            )
    return str(hit.ref), entries, profile


def _subagents(spec: Spec, model_id: str) -> tuple[list[Sub], tuple, list[str]]:
    """Each sub-agent's alias (CC2: the agent's `chat` for the same provider and model, else
    `chat-NAME`), model and prompt version; the aliases to serve and the keys they add."""
    subs, served, keys = [], [], []
    agent_keys = set(PROVIDERS[spec.model].keys)
    for index, sub in enumerate(spec.subagents):
        where = f"subagents[{index}]"
        if sub.model not in PROVIDERS:
            raise NewAgentError(
                "MODEL_UNSUPPORTED",
                f"{where}.model must be one of: {', '.join(sorted(PROVIDERS))}",
            )
        if (sub.model == "ollama") != (spec.model == "ollama"):
            # Ollama's gateway version reaches the host; the others' does not (#1025 O1).
            raise NewAgentError(
                "SPEC_UNSUPPORTED",
                f"{where}.model: ollama and the hosted providers need different gateway "
                "versions; use ollama for the agent and every sub-agent, or for none",
            )
        try:
            resolved = newagent.model_id_for(sub.model, sub.model_id)
        except NewAgentError as error:
            raise NewAgentError(error.code, f"{where}: {newagent._bare(error)}") from None
        same = (sub.model, resolved) == (spec.model, model_id)
        alias = "chat" if same else f"chat-{sub.name}"
        prompt_name = f"{spec.name}-{sub.name.replace('_', '-')}-prompt"
        if not registry.NAME.fullmatch(prompt_name):
            raise NewAgentError(
                "SPEC_INVALID",
                f"{where}.name: with the agent's name, the prompt version name would be over "
                "63 characters; use a shorter name",
            )
        if not same:
            served.append((alias, sub.model, resolved))
            for key in PROVIDERS[sub.model].keys:
                if key not in agent_keys and key not in keys:
                    keys.append(key)
        subs.append(
            Sub(
                sub,
                alias,
                resolved,
                f"prompt/{prompt_name}@{VERSION}",
                f"src/subagents/{sub.name}",
            )
        )
    return subs, tuple(served), keys


def _tools(spec: Spec, directory: Path) -> dict[str, registry.Resolved]:
    """Every picked tool, the agent's first: one version per tool across the agent and its
    sub-agents (the same ref in two of them is placed once)."""
    tools: dict[str, registry.Resolved] = {}
    versions: dict[str, str] = {}
    picks = [(f"tools[{i}]", ref) for i, ref in enumerate(spec.tools)]
    for index, sub in enumerate(spec.subagents):
        if len(set(sub.tools)) != len(sub.tools):
            raise NewAgentError(
                "SPEC_INVALID", f"subagents[{index}].tools: a tool is picked twice"
            )
        picks += [(f"subagents[{index}].tools[{j}]", ref) for j, ref in enumerate(sub.tools)]
    for where, text in picks:
        if text in tools and where.startswith("subagents"):
            continue
        hit = _resolve(text, "tool", where, directory)
        if versions.setdefault(hit.ref.name, str(hit.ref)) != str(hit.ref) or text in tools:
            raise NewAgentError("SPEC_INVALID", "tools: one version per tool")
        tools[text] = hit
    return tools


def _skills(spec: Spec, directory: Path, capabilities: frozenset[str] | None) -> list[dict]:
    entries, names = [], set()
    for index, text in enumerate(spec.skills):
        hit = _resolve(text, "skill", f"skills[{index}]", directory)
        if hit.ref.name in names:
            raise NewAgentError("SPEC_INVALID", f"skills[{index}]: one version per skill")
        names.add(hit.ref.name)
        entry = _ref_entry(hit, directory)
        if entry["runs_code"] and "skill_scripts" not in (capabilities or ()):
            raise NewAgentError(
                "RUNTIME_NO_SKILL_SCRIPTS",
                f"skills[{index}]: {hit.ref} has scripts, and the runtime cannot run them",
            )
        entries.append(entry)
    for index, case in enumerate(spec.evals.cases):
        if case.expect.skills() - names:
            raise NewAgentError(
                "SPEC_INVALID",
                f"evals.cases[{index}].expect: names a skill the spec does not pick",
            )
    return entries


def _composition(spec: Spec, model_id: str, directory: Path) -> Composition:
    runtime, entries, profile = _runtime(spec, directory)
    subs, served, keys = _subagents(spec, model_id)
    granted = {entry.ref: set(entry.tools) for entry in spec.mcp}
    for index, sub in enumerate(spec.subagents):
        for j, entry in enumerate(sub.mcp):
            if entry.ref not in granted or not set(entry.tools) <= granted[entry.ref]:
                raise NewAgentError(
                    "SUBAGENT_MCP_UNGRANTED",
                    f"subagents[{index}].mcp[{j}]: every server and tool must also be in the "
                    "agent's own mcp: entry for that server",
                )
    tools = _tools(spec, directory)
    skills = _skills(spec, directory, profile.capabilities)
    return Composition(runtime, entries, subs, served, keys, tools, skills, profile)


# -- the plan ------------------------------------------------------------------------------------


def _subagents_yaml(spec: Spec, subs: list[Sub]) -> str:
    text = ""
    if spec.skills:
        text += "skills:\n" + "".join(f"  - {{ref: {json.dumps(ref)}}}\n" for ref in spec.skills)
    if subs:
        text += "subagents:\n"
    for sub in subs:
        calls = sub.spec.max_model_calls or spec.max_model_calls
        text += (
            f"  - name: {sub.spec.name}\n"
            f"    description: {json.dumps(sub.spec.description, ensure_ascii=False)}\n"
            f"    model: {{alias: {json.dumps(sub.alias)}, "
            f"reasoning: {PROVIDERS[sub.spec.model].reasoning}, max_model_calls: {calls}}}\n"
            f"    prompt: {{ref: {sub.prompt_ref}}}\n"
            f"    max_calls_per_turn: {sub.spec.max_calls_per_turn}\n"
        )
        if sub.spec.tools:
            text += "    tools:\n" + "".join(
                f"      - {{ref: {json.dumps(ref)}}}\n" for ref in sub.spec.tools
            )
        if sub.spec.mcp:
            text += "    mcp:\n" + "".join(
                f"      - ref: {json.dumps(entry.ref)}\n"
                f"        tools: [{', '.join(json.dumps(tool) for tool in entry.tools)}]\n"
                for entry in sub.spec.mcp
            )
    if spec.limits is not None:
        text += f"limits: {{turn_model_calls: {spec.limits.turn_model_calls}}}\n"
    return text


def _agent_yaml(spec: Spec, today: date, runtime: str = RUNTIME, subs: list[Sub] = ()) -> str:
    text = AGENT_HEAD.format(
        name=spec.name,
        today=today.isoformat(),
        runtime=runtime,
        version=VERSION,
        calls=spec.max_model_calls,
        reasoning=PROVIDERS[spec.model].reasoning,
        threshold=json.dumps(spec.evals.threshold),
        retries=spec.evals.retries,
    )
    if spec.knowledge:
        text += "knowledge:\n" + "".join(
            f"  - {{ref: {json.dumps(entry.ref)}, top_k: {entry.top_k}, "
            f"extra_searches: {entry.extra_searches}}}\n"
            for entry in spec.knowledge
        )
    if spec.tools:
        text += "tools:\n" + "".join(f"  - {{ref: {json.dumps(ref)}}}\n" for ref in spec.tools)
    if spec.mcp:
        text += "mcp:\n" + "".join(
            f"  - ref: {json.dumps(entry.ref)}\n"
            f"    tools: [{', '.join(json.dumps(tool) for tool in entry.tools)}]\n"
            for entry in spec.mcp
        )
    return text + _subagents_yaml(spec, list(subs)) + AGENT_TAIL


def _coded(error: ValidationError) -> str:
    """The agent file's own code for a refusal that has one (`agentfile.CodedError`, e.g.
    SUBAGENT_NOTES), else SPEC_INVALID."""
    from agentbreeder.agentfile import CodedError

    for problem in error.errors():
        cause = (problem.get("ctx") or {}).get("error")
        if isinstance(cause, CodedError):
            return cause.code
    return "SPEC_INVALID"


def _check_agent(text: str, spec: Spec, runtime: str = RUNTIME, subs: list[Sub] = ()):
    """The rendered agent.yaml must load as an agent and say exactly what the spec asked.
    Returns the loaded agent."""
    from agentbreeder.agentfile import AgentFile
    from agentbreeder.strict_yaml import StrictYamlError, load_text

    try:
        agent = AgentFile.model_validate(load_text(text))
    except ValidationError as error:
        raise NewAgentError(_coded(error), f"the agent: {_problems(error)}") from None
    except StrictYamlError:  # a template bug: every value is quoted or checked
        raise NewAgentError(
            "TEMPLATE_INVALID", "the generated agent.yaml does not parse"
        ) from None
    expected = (
        spec.name,
        runtime,
        [(entry.ref, entry.top_k, entry.extra_searches) for entry in spec.knowledge],
        list(spec.tools),
        [(entry.ref, list(entry.tools)) for entry in spec.mcp],
        spec.evals.threshold,
        spec.evals.retries,
        spec.max_model_calls,
        list(spec.skills),
        [
            (
                sub.spec.name,
                sub.spec.description,
                sub.alias,
                sub.spec.max_model_calls or spec.max_model_calls,
                sub.prompt_ref,
                list(sub.spec.tools),
                [(entry.ref, list(entry.tools)) for entry in sub.spec.mcp],
                sub.spec.max_calls_per_turn,
            )
            for sub in subs
        ],
        spec.limits.turn_model_calls if spec.limits else None,
    )
    got = (
        agent.name,
        agent.runtime,
        [(entry.ref, entry.top_k, entry.extra_searches) for entry in agent.knowledge],
        [entry.ref for entry in agent.tools],
        [(entry.ref, list(entry.tools)) for entry in agent.mcp],
        agent.evals.threshold if agent.evals else None,
        agent.evals.retries if agent.evals else None,
        agent.model.max_model_calls,
        [entry.ref for entry in agent.skills],
        [
            (
                sub.name,
                sub.description,
                sub.model.alias,
                sub.model.max_model_calls,
                sub.prompt.ref,
                [entry.ref for entry in sub.tools],
                [(entry.ref, list(entry.tools)) for entry in sub.mcp],
                sub.max_calls_per_turn,
            )
            for sub in agent.subagents
        ],
        agent.limits.turn_model_calls if agent.limits else None,
    )
    if got != expected:
        raise NewAgentError("TEMPLATE_INVALID", "the generated agent.yaml differs from the spec")
    return agent


def _check_names(
    agent, tools: dict[str, registry.Resolved], profile: runtime_profile.Profile
) -> None:
    """build's checks of the written agent against its runtime (`check_runtime`: a missing
    capability or a count over a limit) and its sub-agent name checks (SUBAGENT_NAME_CLASH:
    `ask_<name>` against the agent's own functions, MCP and the runtime's reserved tool names,
    and clashes inside one sub-agent), over the picked versions as they resolve, so the preview
    shows the refusal build would make."""
    from agentbreeder.build import BuildError, check_runtime, check_subagent_names
    from agentbreeder.strict_yaml import StrictYamlError

    try:
        check_runtime(agent, profile)
        if not agent.subagents:
            return
        check_subagent_names(agent, {ref: hit.path for ref, hit in tools.items()}, profile)
    except BuildError as error:
        raise NewAgentError(error.code, newagent._bare(error)) from None
    except (StrictYamlError, OSError):
        raise NewAgentError("TOOL_INVALID", "a picked tool's tool.yaml cannot be read") from None


def combination(spec: Spec) -> tuple[str, ...]:
    tools = spec.tools or any(sub.tools for sub in spec.subagents)
    parts = (
        ("knowledge", spec.knowledge),
        ("tools", tools),
        ("mcp", spec.mcp or any(sub.mcp for sub in spec.subagents)),
        ("subagents", spec.subagents),
        ("skills", spec.skills),
    )
    return tuple(sorted(part for part, used in parts if used))


def runtime_verified(ref: str) -> bool:
    """Whether this runtime version was run end to end (`RUNTIMES_RUN`): the label
    `registry runtimes` shows. Not the builder's check; a spec on such a version can still be
    an unverified combination (`verified`)."""
    return ref in RUNTIMES_RUN


def verified(spec: Spec) -> bool:
    """Whether the spec's combination was run end to end: its runtime is the default version
    (`newagent.RUNTIME`) of a name in `VERIFIED`, its parts are in `VERIFIED` for that name, its
    provider is verified, and for a spec with tools there are at most `VERIFIED_TOOLS` of them
    on a `TOOL_PROVIDERS` provider. Every combination ran on the default runtime version, so a
    spec that pins another is not verified, even one `registry runtimes` shows as run end to
    end (`runtime_verified`, e.g. `runtime/adk@1.3.0`).

    Sub-agents and skills need a newer version than the default: a spec with either is
    verified only on the version in `PART_RUNTIMES`, with the agent and every sub-agent on a
    `SUBAGENT_PROVIDERS` (or `SKILL_PROVIDERS`) provider, at most `VERIFIED_SUBAGENTS`
    sub-agents and `VERIFIED_SKILLS` skills, each provider's default model (no `model_id`),
    and `VERIFIED_TOOLS` counting every agent's tools, each on a `TOOL_PROVIDERS` provider. A
    sub-agent's `mcp:` counts as MCP. The caps (`limits`, `max_model_calls`,
    `max_calls_per_turn`) are not part of the claim."""
    parts = combination(spec)
    wanted = {PART_RUNTIMES[part] for part in parts if part in PART_RUNTIMES}
    if len(wanted) > 1:
        return False
    runtime = spec.runtime or RUNTIME
    name = runtime.removeprefix("runtime/").partition("@")[0]
    if runtime != (wanted.pop() if wanted else RUNTIME) or name not in VERIFIED:
        return False
    if parts not in VERIFIED[name] or not PROVIDERS[spec.model].verified:
        return False
    models = [spec.model, *(sub.model for sub in spec.subagents)]
    # the runs used each provider's default model; another model id was not run with either part
    if (spec.subagents or spec.skills) and any(
        model_id is not None for model_id in (spec.model_id, *(s.model_id for s in spec.subagents))
    ):
        return False
    if spec.subagents and (
        len(spec.subagents) > VERIFIED_SUBAGENTS
        or any(model not in SUBAGENT_PROVIDERS for model in models)
    ):
        return False
    if spec.skills and (len(spec.skills) > VERIFIED_SKILLS or spec.model not in SKILL_PROVIDERS):
        return False
    tooled = [(spec.model, spec.tools), *((sub.model, sub.tools) for sub in spec.subagents)]
    if sum(len(tools) for _, tools in tooled) > VERIFIED_TOOLS:
        return False
    return all(model in TOOL_PROVIDERS for model, tools in tooled if tools)


SUBAGENT_PROMPT_MANIFEST = """\
kind: prompt
name: {name}
version: {version}
description: The {agent} agent's {sub} sub-agent's prompt.
"""


def plan(
    name: str,
    spec_path: Path,
    directory: Path | None = None,
    today: date | None = None,
    expect_digest: str | None = None,
) -> tuple[newagent.Plan, dict]:
    """Read and check the spec and fill the templates; writes nothing. Returns the plan and the
    extra summary fields. `expect_digest`: refuse unless the plan is the one a dry run showed
    (its `plan_sha256`)."""
    if expect_digest is not None and not registry.DIGEST.fullmatch(expect_digest):
        raise NewAgentError("DIGEST_INVALID", "--expect-digest must be sha256:<64 hex digits>")
    spec = parse(read(spec_path))
    newagent.check_inputs(spec.name, spec.model)
    if spec.name != name:
        raise NewAgentError("NAME_MISMATCH", "NAME must be the spec's `name`")
    resolved = newagent.model_id_for(spec.model, spec.model_id)
    today = today or date.today()
    base = newagent.plan(name, spec.model, directory, today=today, model_id=resolved)
    target = base.directory
    knowledge_refs, knowledge_rows, embedding = _knowledge(spec, target)
    parts = _composition(spec, resolved, target)
    if embedding and embedding[0] in {alias for alias, _, _ in parts.served}:
        raise NewAgentError(
            "ALIAS_UNSUPPORTED", "knowledge[0]: its embedding alias is a sub-agent's alias"
        )
    refs = [_ref_entry(hit, target) for hit in parts.tools.values()]
    servers, mcp_refs, keys = _servers(spec, target)
    refs = parts.entries + knowledge_refs + refs + mcp_refs + parts.skills
    # the sub-agents' model keys, then the servers' keys not already among them (a version 1
    # spec has no sub-agent keys, so its list is what it was)
    keys = [*parts.keys, *(key for key in keys if key not in parts.keys)]
    agent_text = _agent_yaml(spec, today, parts.runtime, parts.subs)
    agent = _check_agent(agent_text, spec, parts.runtime, parts.subs)
    _check_names(agent, parts.tools, parts.profile)
    review_by = (today + timedelta(days=REVIEW_DAYS)).isoformat()
    stack = stackfor.render(
        name,
        spec.model,
        resolved,
        ["chat", "judge"],
        budget=5.0,
        rpm=60,
        today=today,
        review_by=review_by,
        written_by="--from-spec",
        mcp=servers,
        keys=tuple(keys),
        embedding=embedding,
        postgres=KNOWLEDGE_POSTGRES if embedding else stackfor.POSTGRES,
        served=parts.served,
    )
    cases = "".join(
        json.dumps(case.model_dump(exclude_defaults=True), ensure_ascii=False) + "\n"
        for case in spec.evals.cases
    )
    if servers:
        stack["stack.yaml"] = _granted(stack["stack.yaml"], name, servers, spec)
    served = embedding[:2] if embedding else None
    sub_models = tuple((alias, model) for alias, _, model in parts.served)
    _preflight(stack, name, resolved, served, sub_models)
    values = {"name": name, "version": VERSION}
    files = {
        "agent.yaml": agent_text,
        **stack,
        f"{PROMPT_SOURCE}/manifest.yaml": newagent.PROMPT_MANIFEST.format(**values),
        f"{PROMPT_SOURCE}/prompt.md": _prompt_text(spec.prompt.text),
        f"{EVALS_SOURCE}/manifest.yaml": EVALS_MANIFEST.format(**values),
        f"{EVALS_SOURCE}/golden.jsonl": cases,
    }
    publishes = list(base.publishes)
    for sub in parts.subs:
        prompt_name = registry.Ref.parse(sub.prompt_ref).name
        files[f"{sub.source}/manifest.yaml"] = SUBAGENT_PROMPT_MANIFEST.format(
            name=prompt_name, version=VERSION, agent=name, sub=sub.spec.name
        )
        files[f"{sub.source}/prompt.md"] = _prompt_text(sub.spec.prompt.text)
        publishes.append((sub.source, sub.prompt_ref))
    grants = [
        {"server": server, "tools": list(entry.tools)}
        for server, entry in zip(servers, spec.mcp, strict=True)
    ]
    built = replace(
        base,
        files=files,
        publishes=tuple(publishes),
        cases=tuple(case.id for case in spec.evals.cases),
        lock_after_move=any(entry["root_kind"] == "path" for entry in refs),
        pinned={entry["ref"]: entry["sha256"] for entry in refs},
        embedding=served,
        runtime=parts.runtime,
        runtime_root_kind=parts.entries[0]["root_kind"] if parts.entries else "builtin",
        served=sub_models,
    )
    extra = {
        "spec_version": spec.schema_version,
        "combination": {
            "parts": ["model", "prompt", "evals", *combination(spec)],
            "verified": verified(spec),
        },
        "refs": refs,
        "runs_code": any(e["runs_code"] and e["root_kind"] != "builtin" for e in refs),
        "grants": grants,
        "knowledge": knowledge_rows,
        "keys": [*PROVIDERS[spec.model].keys, *keys],
        "plan_sha256": plan_digest(built, refs),
    }
    if expect_digest is not None and extra["plan_sha256"] != expect_digest:
        raise NewAgentError(
            "DIGEST_MISMATCH",
            "the project would differ from the preview (the spec, a picked version or the date "
            "changed); preview it again",
        )
    return built, extra


def _prompt_text(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _granted(text: str, name: str, servers: dict[str, str], spec: Spec) -> str:
    """`text` (a rendered stack.yaml) with each picked server's tools granted to the agent:
    `mcp grant`'s edit (`mcpgrant.change`), made on a private copy, so the preview is the file
    that is written."""
    import tempfile

    from agentbreeder import mcpgrant

    with tempfile.TemporaryDirectory(prefix="agentbreeder-spec-") as temporary:
        path = Path(temporary) / stackfor.STACK_FILE
        path.write_text(text, encoding="utf-8")
        for server, entry in zip(servers, spec.mcp, strict=True):
            try:
                mcpgrant.change(Path(temporary), "grant", name, server, list(entry.tools))
            except mcpgrant.GrantError as error:
                raise NewAgentError(error.code, error.message) from None
        return path.read_text(encoding="utf-8")


def plan_digest(plan: newagent.Plan, refs: list[dict]) -> str:
    """`plan_sha256`: every file's path and text, the picked refs and where each resolves.
    `--expect-digest` refuses a write whose plan is not the one previewed."""
    import hashlib

    body = json.dumps(
        {"files": plan.files, "refs": refs, "directory": str(plan.directory)},
        sort_keys=True,
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _preflight(
    files: dict[str, str],
    name: str,
    model_id: str,
    embedding: tuple[str, str] | None = None,
    served: tuple[tuple[str, str], ...] = (),
) -> None:
    """The rendered stack must load and its gateway config pass the review `services up`
    applies, so a preview that succeeds is one the write accepts."""
    import tempfile

    from agentbreeder.services.schema import ServiceError, load_stack

    with tempfile.TemporaryDirectory(prefix="agentbreeder-spec-") as temporary:
        root = Path(temporary)
        for relative in (stackfor.STACK_FILE, stackfor.LITELLM_CONFIG):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(files[relative], encoding="utf-8")
        try:
            stack = load_stack(root / stackfor.STACK_FILE)
        except ServiceError as error:
            raise NewAgentError(
                "TEMPLATE_INVALID", f"the generated stack does not validate: {error}"
            ) from None
        if name not in stack.environment.agents:  # a template bug
            raise NewAgentError("TEMPLATE_INVALID", "the generated stack names another agent")
        newagent.check_gateway(
            root / stackfor.LITELLM_CONFIG,
            {"chat", "judge"},
            model_id,
            embedding,
            subagents=served,
        )


def summary(plan: newagent.Plan, extra: dict, dry_run: bool) -> dict:
    """The `--json` shape: `new agent`'s, with each file's text (the console's preview shows
    exactly what is written) and the builder's fields."""
    result = plan.summary(dry_run=dry_run)
    # As before `--runtime` existed: the provider's own verification. Whether this runtime and
    # these parts were run is `combination.verified`.
    result["verified"] = PROVIDERS[plan.model].verified
    for entry in result["files"]:
        entry["content"] = plan.files[entry["path"]]
    result.update(extra)
    return result
