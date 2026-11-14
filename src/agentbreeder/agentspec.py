"""`agentbreeder new agent NAME --from-spec FILE`: write an agent project from a JSON spec (M17
#833, the CLI half of the console's agent builder; console spec §4.5, guided §14.7).

The console's builder form never renders YAML: it writes the form as JSON to a private temp file
and runs `new agent NAME --from-spec FILE --dry-run --json` for the preview (the full text of
every file is in `files[].content`), then the same without `--dry-run` to write it.

The spec (`SPEC_VERSION` 1, docs/commands/new.md) names the agent, the model provider and an
optional model id (#905), the prompt text, the eval cases, picked `tool/` versions and `mcp/`
servers with the tools to grant. `knowledge` is refused for now (`SPEC_UNSUPPORTED`): it needs an
embedding alias and an index the generated stack does not have.

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
(`VERIFIED`); the CLI writes unverified ones too, and the console enables only verified ones.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agentbreeder import newagent, registry, stackfor
from agentbreeder.newagent import (
    EVALS_SOURCE,
    PROMPT_SOURCE,
    PROVIDERS,
    REVIEW_DAYS,
    RUNTIME,
    VERSION,
    NewAgentError,
)

SPEC_VERSION = 1
MAX_SPEC_BYTES = 256 * 1024
MAX_PROMPT = 32 * 1024
MAX_TEXT = 4000  # a turn or a reference
MAX_CASES = 100  # the builder's limit; the eval loader allows more
CASE_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
MCP_TOOL = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"  # services.schema.MCP_TOOL
# Combinations run end to end with this command (services up, release, invoke), as the sorted
# names of the parts the spec uses beyond the model, prompt and evals every spec has. A provider
# must also be verified (`PROVIDERS`).
VERIFIED: frozenset[tuple[str, ...]] = frozenset({()})
STACK_SERVICES = frozenset({"postgres", "litellm"})  # services the generated stack has

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
model: {{alias: chat, reasoning: low, max_model_calls: {calls}}}
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
    """Text without control or bidi characters (tab and newline are allowed)."""
    if registry.clean(value)[1]:
        raise ValueError(f"{where} has control or bidi characters")
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


class Expect(_Strict):
    must_match: list[str] = Field(default_factory=list, max_length=20)
    must_not_match: list[str] = Field(default_factory=list, max_length=20)
    tools_include: list[str] = Field(default_factory=list, max_length=20)
    tools_exclude: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _checked(self):
        for field_name in ("must_match", "must_not_match"):
            for pattern in getattr(self, field_name):
                _text(pattern, 200, f"expect.{field_name}")
                _pattern(pattern, f"expect.{field_name}")
        for field_name in ("tools_include", "tools_exclude"):
            for tool in getattr(self, field_name):
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", tool):
                    raise ValueError(f"expect.{field_name}: not a tool name")
        return self


class Case(_Strict):
    id: str = Field(pattern=CASE_ID)
    turns: list[str] = Field(min_length=1, max_length=10)
    expect: Expect = Field(default_factory=Expect)
    reference: str

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


class Spec(_Strict):
    schema_version: Literal[1]
    name: str
    model: str
    model_id: str | None = None
    max_model_calls: int = Field(default=2, ge=1, le=20)
    prompt: Prompt
    evals: Evals
    tools: list[str] = Field(default_factory=list, max_length=50)
    knowledge: list[Any] = Field(default_factory=list)  # refused unless empty (for now)
    mcp: list[Mcp] = Field(default_factory=list, max_length=10)


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
    file others can read or write, and anything over `MAX_SPEC_BYTES`."""
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
        raw = reader.read(MAX_SPEC_BYTES + 1)
    if len(raw) > MAX_SPEC_BYTES:
        raise NewAgentError("SPEC_TOO_LARGE", f"the spec is over {MAX_SPEC_BYTES // 1024} KiB")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, ValueError) as error:
        if isinstance(error, NewAgentError):
            raise
        raise NewAgentError("SPEC_INVALID", "the spec is not UTF-8 JSON") from None
    if not isinstance(data, dict):
        raise NewAgentError("SPEC_INVALID", "the spec must be a JSON object")
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


def parse(data: dict) -> Spec:
    try:
        return Spec.model_validate(data)
    except ValidationError as error:
        version = data.get("schema_version")
        if version != SPEC_VERSION and version is not None:
            raise NewAgentError(
                "SPEC_VERSION_UNSUPPORTED",
                f"this AgentBreeder reads spec schema_version {SPEC_VERSION}",
            ) from None
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
        "runs_code": registry.runs_code(hit.ref.kind),
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


# -- the plan ------------------------------------------------------------------------------------


def _agent_yaml(spec: Spec, today: date) -> str:
    text = AGENT_HEAD.format(
        name=spec.name,
        today=today.isoformat(),
        runtime=RUNTIME,
        version=VERSION,
        calls=spec.max_model_calls,
        threshold=json.dumps(spec.evals.threshold),
        retries=spec.evals.retries,
    )
    if spec.tools:
        text += "tools:\n" + "".join(f"  - {{ref: {json.dumps(ref)}}}\n" for ref in spec.tools)
    if spec.mcp:
        text += "mcp:\n" + "".join(
            f"  - ref: {json.dumps(entry.ref)}\n"
            f"    tools: [{', '.join(json.dumps(tool) for tool in entry.tools)}]\n"
            for entry in spec.mcp
        )
    return text + AGENT_TAIL


def _check_agent(text: str, spec: Spec) -> None:
    """The rendered agent.yaml must load as an agent and say exactly what the spec asked."""
    from agentbreeder.agentfile import AgentFile
    from agentbreeder.strict_yaml import StrictYamlError, load_text

    try:
        agent = AgentFile.model_validate(load_text(text))
    except ValidationError as error:
        raise NewAgentError("SPEC_INVALID", f"the agent: {_problems(error)}") from None
    except StrictYamlError:  # a template bug: every value is quoted or checked
        raise NewAgentError(
            "TEMPLATE_INVALID", "the generated agent.yaml does not parse"
        ) from None
    expected = (
        spec.name,
        list(spec.tools),
        [(entry.ref, list(entry.tools)) for entry in spec.mcp],
        spec.evals.threshold,
        spec.evals.retries,
        spec.max_model_calls,
    )
    got = (
        agent.name,
        [entry.ref for entry in agent.tools],
        [(entry.ref, list(entry.tools)) for entry in agent.mcp],
        agent.evals.threshold if agent.evals else None,
        agent.evals.retries if agent.evals else None,
        agent.model.max_model_calls,
    )
    if got != expected:
        raise NewAgentError("TEMPLATE_INVALID", "the generated agent.yaml differs from the spec")


def combination(spec: Spec) -> tuple[str, ...]:
    return tuple(sorted(part for part, used in (("tools", spec.tools), ("mcp", spec.mcp)) if used))


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
    if spec.knowledge:
        raise NewAgentError(
            "SPEC_UNSUPPORTED",
            "knowledge is not supported by the agent builder yet: the generated stack has no "
            "embedding model or index",
        )
    resolved = newagent.model_id_for(spec.model, spec.model_id)
    today = today or date.today()
    base = newagent.plan(name, spec.model, directory, today=today, model_id=resolved)
    target = base.directory
    refs = []
    for index, text in enumerate(spec.tools):
        refs.append(_ref_entry(_resolve(text, "tool", f"tools[{index}]", target), target))
    if len({entry["ref"].split("@")[0] for entry in refs}) != len(refs):
        raise NewAgentError("SPEC_INVALID", "tools: one version per tool")
    servers, mcp_refs, keys = _servers(spec, target)
    refs += mcp_refs
    agent_text = _agent_yaml(spec, today)
    _check_agent(agent_text, spec)
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
    )
    cases = "".join(
        json.dumps(case.model_dump(exclude_defaults=True), ensure_ascii=False) + "\n"
        for case in spec.evals.cases
    )
    prompt = spec.prompt.text if spec.prompt.text.endswith("\n") else spec.prompt.text + "\n"
    if servers:
        stack["stack.yaml"] = _granted(stack["stack.yaml"], name, servers, spec)
    _preflight(stack, name, resolved)
    values = {"name": name, "version": VERSION}
    files = {
        "agent.yaml": agent_text,
        **stack,
        f"{PROMPT_SOURCE}/manifest.yaml": newagent.PROMPT_MANIFEST.format(**values),
        f"{PROMPT_SOURCE}/prompt.md": prompt,
        f"{EVALS_SOURCE}/manifest.yaml": EVALS_MANIFEST.format(**values),
        f"{EVALS_SOURCE}/golden.jsonl": cases,
    }
    grants = [
        {"server": server, "tools": list(entry.tools)}
        for server, entry in zip(servers, spec.mcp, strict=True)
    ]
    built = replace(
        base,
        files=files,
        cases=tuple(case.id for case in spec.evals.cases),
        lock_after_move=any(entry["root_kind"] == "path" for entry in refs),
        pinned={entry["ref"]: entry["sha256"] for entry in refs},
    )
    parts = combination(spec)
    extra = {
        "spec_version": SPEC_VERSION,
        "combination": {
            "parts": ["model", "prompt", "evals", *parts],
            "verified": parts in VERIFIED and PROVIDERS[spec.model].verified,
        },
        "refs": refs,
        "runs_code": any(e["runs_code"] and e["root_kind"] != "builtin" for e in refs),
        "grants": grants,
        "keys": [PROVIDERS[spec.model].key, *keys],
        "plan_sha256": plan_digest(built, refs),
    }
    if expect_digest is not None and extra["plan_sha256"] != expect_digest:
        raise NewAgentError(
            "DIGEST_MISMATCH",
            "the project would differ from the preview (the spec, a picked version or the date "
            "changed); preview it again",
        )
    return built, extra


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


def _preflight(files: dict[str, str], name: str, model_id: str) -> None:
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
        newagent.check_gateway(root / stackfor.LITELLM_CONFIG, {"chat", "judge"}, model_id)


def summary(plan: newagent.Plan, extra: dict, dry_run: bool) -> dict:
    """The `--json` shape: `new agent`'s, with each file's text (the console's preview shows
    exactly what is written) and the builder's fields."""
    result = plan.summary(dry_run=dry_run)
    for entry in result["files"]:
        entry["content"] = plan.files[entry["path"]]
    result.update(extra)
    return result
