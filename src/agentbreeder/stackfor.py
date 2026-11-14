"""`agentbreeder new agent NAME --stack-for DIR/agent.yaml --model gemini`: write a `stack.yaml`
(and `stack/litellm/config.yaml`) beside an existing `agent.yaml`, so a project that has only an
agent file can be brought up and released (#823, console spec §4.5 "Onboard").

The stack is the starter's (`newagent`): Postgres with `app` and `litellm` databases and the
LiteLLM gateway, with one gateway alias per model the agent uses (its `model.alias` and, with
`evals:`, the eval set's `judge.alias`). Only shapes that starter covers are written: an agent
with `knowledge`, `tools` or `mcp` needs services this stack does not have (an embedding model, a
tool's backing service, an MCP relay), so it is refused with `STACK_FOR_UNSUPPORTED` rather than
given a stack that cannot run it. `traces: otlp` is allowed with a warning: there is no trace
collector in this stack, so no traces are exported.

Nothing but `stack.yaml` and `stack/litellm/config.yaml` is written, and neither may exist. The
agent file and its directory get the console's registration check (spec §10 S-01, the same
`center.files` functions): the directory and `agent.yaml` must be yours and not writable by
others, and so must every ancestor (owned by you or root; a sticky directory such as `/tmp` is
allowed). Files are staged in a hidden directory inside the project, validated as a stack, then
hard-linked into place (a link never replaces an existing file); on failure what was created is
removed. User text reaches the YAML only as validated names and JSON-quoted aliases.

Identity (#909, later): the generated `environment.agents.NAME` entry is the single place where
an identity principal created at onboarding would be added; see `_agent_entry`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from agentbreeder import newagent, registry
from agentbreeder.newagent import PROVIDERS, REVIEW_DAYS, SUMMARY_VERSION, NewAgentError

AGENT_FILE = "agent.yaml"
STACK_FILE = "stack.yaml"
LITELLM_CONFIG = "stack/litellm/config.yaml"
# Gateway aliases that go into the YAML; JSON-quoted as well. Narrower than `Model.alias` (no
# `/`), so an alias is also a plain LiteLLM `model_name`.
ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

STACK_YAML = """\
# Local backing services for the {name} agent: Postgres and the LiteLLM model gateway.
# Written by `agentbreeder new agent {name} --stack-for agent.yaml` on {today}.
# `agentbreeder services up --environment dev --env-file FILE` starts them; FILE holds
# {key}=... (mode 0600). See docs/services.md.
kind: stack
schema_version: 1
name: {name}
services:
  postgres:
    ref: service/postgres-pgvector@1.1.0
    with: {{databases: [app, litellm]}}
  litellm:
    ref: service/litellm@1.0.0
    with: {{config: stack/litellm/config.yaml}}
    wire: {{database-url: postgres.databases.litellm}}
environment:
  gateway: litellm
  models: [{models}]
  ceilings: {{budget_usd: {budget}, rpm: {rpm}}}
  bindings:
    DATABASE_URL: {{ref: postgres.databases.app}}
  eval_database: {{ref: postgres.databases.app}}
  agents:
{agent_entry}
"""

LITELLM_HEAD = "model_list:\n"
LITELLM_MODEL = """\
  - model_name: {alias}
    litellm_params: {{model: {model}, api_key: os.environ/{key}}}
"""
LITELLM_TAIL = """\
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
"""


def _agent_entry(name: str, review_by: str) -> str:
    """`environment.agents.NAME`. #909 seam: the identity principal created at onboarding
    (and its RBAC/ABAC attributes) is added here, and nowhere else in this module."""
    return (
        f"    {name}:\n"
        "      sponsor: you@example.com   # who answers for this agent\n"
        f'      review_by: "{review_by}"     # the gate refuses deploys after this date\n'
        "      candidate: true            # release tests a candidate before production"
    )


@dataclass(frozen=True)
class StackForPlan:
    name: str
    model: str
    agent_file: Path
    directory: Path
    review_by: str
    aliases: tuple[str, ...]
    files: dict[str, str]  # relative path -> content, in write order
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self, dry_run: bool) -> dict:
        """The `--json` shape (docs/commands.md)."""
        return {
            "schema_version": SUMMARY_VERSION,
            "dry_run": dry_run,
            "name": self.name,
            "directory": str(self.directory),
            "agent_file": str(self.agent_file),
            "model": self.model,
            "key": PROVIDERS[self.model].key,
            "aliases": list(self.aliases),
            "review_by": self.review_by,
            "files": [
                {
                    "path": relative,
                    "mode": "0644",
                    "sha256": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
                }
                for relative, content in self.files.items()
            ],
            "warnings": list(self.warnings),
        }


def _check_owned(directory: Path, agent_file: Path) -> None:
    """S-01, as registration applies it (`center.files`): refuse a project another OS user
    could change. `directory` is resolved (no links)."""
    from agentbreeder.center.files import BadRequest, check_ancestors

    uid = os.getuid()
    for item, kind, what in (
        (directory, stat.S_ISDIR, "a directory"),
        (agent_file, stat.S_ISREG, "a regular file"),
    ):
        try:
            info = os.lstat(item)
        except FileNotFoundError as error:
            raise NewAgentError("AGENT_FILE_MISSING", f"{item} does not exist") from error
        except OSError as error:
            raise NewAgentError("PROJECT_UNSAFE", f"{item} cannot be read") from error
        if not kind(info.st_mode):
            raise NewAgentError("PROJECT_UNSAFE", f"{item} must be {what} (not a link)")
        if info.st_uid != uid:
            raise NewAgentError(
                "PROJECT_UNSAFE",
                f"{item} is owned by another user; only projects you alone control are onboarded",
            )
        if info.st_mode & 0o022:
            raise NewAgentError(
                "PROJECT_UNSAFE",
                f"{item} is writable by other users (`chmod go-w {item}` fixes this); only "
                "projects you alone control are onboarded",
            )
    try:
        check_ancestors(directory.parent)
    except BadRequest as error:
        message = str(error).replace(
            "the console only runs projects you alone control",
            "only projects you alone control are onboarded",
        )
        raise NewAgentError(error.code, message) from error


def _judge_alias(ref: str, directory: Path) -> str:
    from agentbreeder.evals import EvalError, load_eval_set

    try:
        resolved = registry.resolve(registry.Ref.parse(ref), registry.roots(directory))
        return load_eval_set(resolved.path).judge_alias
    except registry.RegistryError as error:
        raise NewAgentError(error.code, newagent._bare(error)) from error
    except EvalError as error:
        raise NewAgentError(error.code, newagent._bare(error)) from error


def plan(name: str, model: str, agent_path: Path, today: date | None = None) -> StackForPlan:
    """Check the agent file and fill the templates; writes nothing."""
    from agentbreeder.agentfile import AgentFileError, load_agent_file

    newagent.check_inputs(name, model)  # the same name and model checks as the starter
    given = Path(os.path.abspath(agent_path))
    if registry.clean(str(given), name=True)[0] != str(given):
        raise NewAgentError(
            "DESTINATION_UNSAFE", "the agent file path has control or bidi characters"
        )
    if given.name != AGENT_FILE:
        raise NewAgentError("AGENT_FILE_NAME", f"--stack-for takes a path to {AGENT_FILE}")
    try:
        directory = given.parent.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise NewAgentError("AGENT_FILE_MISSING", f"{given.parent} does not exist") from error
    agent_file = directory / AGENT_FILE
    _check_owned(directory, agent_file)
    try:
        agent = load_agent_file(agent_file)
    except AgentFileError as error:
        raise NewAgentError(error.code, newagent._bare(error)) from error
    if agent.name != name:
        raise NewAgentError(
            "NAME_MISMATCH",
            f"NAME must be the agent's name in {AGENT_FILE} ({agent.name}); the stack is named "
            "after it",
        )
    unsupported = [
        key
        for key, present in (
            ("knowledge", agent.knowledge),
            ("tools", agent.tools),
            ("mcp", agent.mcp),
        )
        if present
    ]
    if unsupported:
        raise NewAgentError(
            "STACK_FOR_UNSUPPORTED",
            f"{AGENT_FILE} uses {', '.join(unsupported)}, which need services the generated "
            "stack does not have; write stack.yaml by hand (docs/services.md)",
        )
    aliases = [agent.model.alias]
    if agent.evals is not None:
        judge = _judge_alias(agent.evals.ref, directory)
        if judge not in aliases:
            aliases.append(judge)
    for alias in aliases:
        if not ALIAS.fullmatch(alias):
            raise NewAgentError(
                "ALIAS_UNSUPPORTED",
                "model aliases must be letters, digits, '.', '_' and '-' (at most 64 "
                "characters) for a generated stack; write stack.yaml by hand (docs/services.md)",
            )
    for relative in (STACK_FILE, LITELLM_CONFIG):
        if os.path.lexists(directory / relative):
            raise NewAgentError("STACK_EXISTS", f"{directory / relative} already exists")
    warnings = []
    if agent.traces == "otlp":
        warnings.append(
            "traces: otlp, but the generated stack has no trace collector, so no traces are "
            "exported; set `traces: none` or add a collector (docs/services.md)"
        )
    today = today or date.today()
    review_by = (today + timedelta(days=REVIEW_DAYS)).isoformat()
    provider = PROVIDERS[model]
    quoted = [json.dumps(alias) for alias in aliases]
    # The gate refuses a deploy whose model budget or rpm exceeds the environment's ceilings, so
    # they are at least the agent's own (the starter's 5 USD and 60 rpm otherwise).
    budget = max(5.0, agent.model.budget_usd or 0)
    rpm = max(60, agent.model.rpm or 0)
    stack = STACK_YAML.format(
        budget=json.dumps(int(budget) if budget.is_integer() else budget),
        rpm=rpm,
        name=name,
        today=today.isoformat(),
        key=provider.key,
        models=", ".join(quoted),
        agent_entry=_agent_entry(name, review_by),
    )
    config = (
        LITELLM_HEAD
        + "".join(
            LITELLM_MODEL.format(alias=alias, model=provider.model, key=provider.key)
            for alias in quoted
        )
        + LITELLM_TAIL
    )
    return StackForPlan(
        name,
        model,
        agent_file,
        directory,
        review_by,
        tuple(aliases),
        {STACK_FILE: stack, LITELLM_CONFIG: config},
        tuple(warnings),
    )


def check(plan: StackForPlan) -> None:
    """The `--dry-run` checks, again at write time: ownership and no existing files."""
    _check_owned(plan.directory, plan.agent_file)
    for relative in plan.files:
        if os.path.lexists(plan.directory / relative):
            raise NewAgentError("STACK_EXISTS", f"{plan.directory / relative} already exists")


def _directory(path: Path, created: list[Path]) -> None:
    """A real directory you own at `path` (made 0755 if missing, recorded in `created`)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        path.mkdir(mode=0o755)
        os.chmod(path, 0o755)
        created.append(path)
        return
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise NewAgentError(
            "PROJECT_UNSAFE", f"{path} must be a directory you own that others cannot write to"
        )


def create(plan: StackForPlan) -> StackForPlan:
    """Write the files beside the agent file; nothing else in the project changes."""
    from agentbreeder.services.schema import ServiceError, load_stack

    check(plan)
    created: list[Path] = []
    staging: Path | None = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=".agentbreeder-stack-", dir=plan.directory))
        for relative, content in plan.files.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "x", encoding="utf-8") as writer:
                writer.write(content)
            os.chmod(target, 0o644)
        try:
            stack = load_stack(staging / STACK_FILE)
        except ServiceError as error:  # a template bug: every input is checked first
            raise NewAgentError(
                "TEMPLATE_INVALID", f"the generated stack does not validate: {error}"
            ) from error
        if stack.name != plan.name or plan.name not in stack.environment.agents:
            raise NewAgentError("TEMPLATE_INVALID", "the generated stack names another agent")
        for relative in plan.files:
            target = plan.directory / relative
            for parent in reversed(Path(relative).parents[:-1]):
                _directory(plan.directory / parent, created)
            # A hard link fails with EEXIST rather than replacing a file that appeared.
            try:
                os.link(staging / relative, target, follow_symlinks=False)
            except FileExistsError as error:
                raise NewAgentError("STACK_EXISTS", f"{target} appeared while writing") from error
            created.append(target)
    except BaseException as error:
        for path in reversed(created):
            try:
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()
            except OSError:
                pass
        if isinstance(error, OSError):
            raise NewAgentError(
                "WRITE_FAILED", f"cannot write {plan.directory}: {error.strerror or error}"
            ) from error
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return plan
